"""
Data-ingest routes — extracted from api/main.py (monolith split, step 1).

Identical paths, methods, validation, and behavior; only the decorator target
changed (@app.* -> @router.*). Included via app.include_router(data_router).

State comes from api.state (set in lifespan). Tests patch api.state.* (and
legacy api.main.* globals) — see tests/test_load_endpoint_empty.py.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from api import state as _state
from api.security_paths import resolve_under, sanitize_upload_name
from api.security_uploads import SniffError, validate_content
from config import settings
from etl.clean import (
    CleaningPlan,
    CleaningStrategy,
    ColumnCleaningRule,
    DeduplicationStrategy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadResponse(BaseModel):
    """Response from file upload."""

    success: bool
    filename: str
    sheets: List[str] = []
    sheet_info: Dict[str, Any] = {}
    schema_info: Dict[str, Any] = Field(default_factory=dict, alias="schema")
    rows: int = 0
    columns: int = 0
    message: str = ""

    model_config = {"populate_by_name": True}

    @property
    def schema(self) -> Dict[str, Any]:
        """Back-compat: pre-alias readers used `.schema`."""
        return self.schema_info


class CleanRequest(BaseModel):
    """Request to clean data."""

    filename: str
    sheet: str = "default"
    plan: Optional[Dict[str, Any]] = None
    auto_infer: bool = True


class CleanResponse(BaseModel):
    """Response from data cleaning."""

    success: bool
    original_shape: List[int]
    cleaned_shape: List[int]
    report: Dict[str, Any] = {}
    cleaning_code: str = ""
    preview: Dict[str, Any] = {}


class CleanPreviewRequest(BaseModel):
    table_name: str
    dedup: bool = True
    null_strategy: str = "drop"
    drop_columns: List[str] = []


class LoadRequest(BaseModel):
    """Request to load data to warehouse."""

    filename: str = Field(..., description="Name of the uploaded file")
    sheet: str = Field("default", description="Sheet name (for Excel files)")
    table_name: Optional[str] = Field(None, description="Target table name")
    if_exists: str = Field("replace", description="replace, append, or fail", pattern="^(replace|append|fail)$")


def _extractor():
    ex = _state.extractor
    if ex is None:
        raise HTTPException(status_code=503, detail="Extractor not initialized")
    return ex


def _cleaner():
    cl = _state.cleaner
    if cl is None:
        raise HTTPException(status_code=503, detail="Cleaner not initialized")
    return cl


def _loader():
    ld = _state.loader
    if ld is None:
        raise HTTPException(status_code=503, detail="Loader not initialized")
    return ld


def _tenant_raw_dir() -> Path:
    """Return the caller's tenant directory under ``data/raw``.

    Each tenant gets its own directory, so two tenants uploading
    ``sales.csv`` cannot overwrite each other (ADR-009 D1).  The ``default``
    tenant keeps the flat ``data/raw`` layout so an existing install is
    unaffected.
    """
    from api.tenancy import DEFAULT_TENANT_ID, current_tenant_id, tenant_slug

    tenant_id = current_tenant_id()
    if not tenant_id or tenant_id == DEFAULT_TENANT_ID:
        return Path("data/raw")
    return Path("data/raw") / tenant_slug(tenant_id)


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    http_request: Request,
    file: UploadFile = File(...),
    sheet: Optional[str] = Form(None),
):
    """
    Upload a data file (CSV, Excel, Parquet, JSON, JSONL, TSV).

    The file is saved under the caller's tenant directory, extracted, and
    schema is inferred.
    """
    from api.middleware.rate_limit import check_rate_limit

    check_rate_limit(http_request, "upload")

    _ = sheet  # accepted for API compat; sheet selection happens at /load
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    safe_name = sanitize_upload_name(file.filename)

    ext = Path(safe_name).suffix.lower()
    if ext not in settings.data.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {settings.data.allowed_extensions}",
        )

    raw_dir = _tenant_raw_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_path = resolve_under(raw_dir, safe_name)

    content = await file.read()
    if len(content) > settings.data.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    # The extension is not proof of content: a "data.csv" can be a ZIP or an
    # Excel workbook, and the extractors will try to parse whatever arrives.
    # Verify the leading bytes match the declared type.
    try:
        validate_content(safe_name, content)
    except SniffError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    file_path.write_bytes(content)

    extractor = _extractor()
    try:
        data = extractor.extract(file_path)
        schema_info = extractor.infer_schema(file_path)

        if ext in [".xlsx", ".xls"]:
            sheets = list(data.keys())
        else:
            sheets = ["default"]

        first_sheet = sheets[0] if sheets else "default"
        df = data.get(first_sheet, pd.DataFrame())

        sheet_info = {}
        for sheet_name, sheet_df in data.items():
            sheet_info[sheet_name] = {
                "rows": sheet_df.shape[0],
                "columns": sheet_df.shape[1],
                "columns_list": list(sheet_df.columns),
            }

        return UploadResponse(
            success=True,
            filename=safe_name,
            sheets=sheets,
            sheet_info=sheet_info,
            schema=schema_info,
            rows=df.shape[0],
            columns=df.shape[1],
            message=f"File uploaded and parsed successfully. {len(sheets)} sheet(s) found.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process file")


@router.post("/clean", response_model=CleanResponse)
async def clean_data(request: CleanRequest, http_request: Request):
    """Clean uploaded data using auto-inferred or custom cleaning plan."""
    from api.middleware.rate_limit import check_rate_limit

    # Cleaning parses the whole file, so it is as expensive as the upload
    # and shares its budget (ADR-009 D5).
    check_rate_limit(http_request, "upload")

    # The uploaded file lives in the caller's *tenant* directory, so /clean
    # must read it from there too — the flat ``data/raw`` only holds the
    # files of the ``default`` tenant.
    raw_dir = _tenant_raw_dir()
    file_path = resolve_under(raw_dir, request.filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found. Upload first.")

    extractor = _extractor()
    cleaner = _cleaner()
    data = extractor.extract(file_path)

    sheet_name = request.sheet if request.sheet in data else list(data.keys())[0]
    df = data[sheet_name]

    if request.plan:
        raw = request.plan or {}
        plan = CleaningPlan()
        try:
            if "deduplication" in raw:
                try:
                    plan.deduplication = DeduplicationStrategy(str(raw["deduplication"]).lower())
                except ValueError:
                    pass
            subset = raw.get("deduplication_subset")
            if isinstance(subset, list):
                plan.deduplication_subset = [str(c) for c in subset]
            gns = raw.get("global_null_strategy")
            if gns is not None:
                try:
                    plan.global_null_strategy = CleaningStrategy(str(gns).lower())
                except ValueError:
                    pass
            drops = raw.get("drop_columns")
            if isinstance(drops, list):
                plan.drop_columns = [str(c) for c in drops]
            renames = raw.get("rename_columns")
            if isinstance(renames, dict):
                plan.rename_columns = {str(k): str(v) for k, v in renames.items()}
            rules = raw.get("column_rules")
            if isinstance(rules, dict):
                for col, rule in rules.items():
                    if not isinstance(rule, dict):
                        continue
                    try:
                        strategy = CleaningStrategy(str(rule.get("strategy", "drop")).lower())
                    except ValueError:
                        continue
                    plan.column_rules[str(col)] = ColumnCleaningRule(
                        column=str(col),
                        strategy=strategy,
                        fill_value=rule.get("fill_value"),
                        dtype=rule.get("dtype"),
                        date_format=rule.get("date_format"),
                        category_mapping=rule.get("category_mapping")
                        if isinstance(rule.get("category_mapping"), dict) else None,
                    )
        except Exception as e:
            logger.warning("Cleaning plan deserialization issue (using defaults for bad parts): %s", e)
    else:
        plan = None

    cleaned_df = cleaner.clean(df, plan)
    report = cleaner.get_report()
    cleaning_code = cleaner.generate_cleaning_code(plan or cleaner._infer_plan(df))

    return CleanResponse(
        success=True,
        original_shape=list(df.shape),
        cleaned_shape=list(cleaned_df.shape),
        report=report,
        cleaning_code=cleaning_code,
        preview={
            "columns": list(cleaned_df.columns),
            "dtypes": {c: str(t) for c, t in cleaned_df.dtypes.items()},
            "head": cleaned_df.head(10).to_dict("records"),
        },
    )


@router.post("/clean/preview")
async def clean_preview(request: CleanPreviewRequest):
    """Preview cleaning changes before applying  returns before/after stats + diff."""
    from api.security_paths import validate_table_name

    try:
        table_name = validate_table_name(request.table_name)
    except HTTPException:
        return {"success": False, "error": "Invalid table name"}
    try:
        loader = _loader()
        if not loader.table_exists(table_name):
            return {"success": False, "error": f"Table '{table_name}' not found"}

        # Same-process RW connection (same config as api.db/org) — DuckDB
        # forbids mixing read_only and RW configs to one file in a process.
        # Identifier is allowlisted by validate_table_name; quoted for safety.
        safe = '"' + table_name.replace('"', '""') + '"'
        schema = settings.duckdb.schema.replace('"', '""')
        with duckdb.connect(str(settings.duckdb.database_path)) as conn:
            conn.execute(f'SET search_path = "{schema}"')
            df_before = conn.execute(f'SELECT * FROM "{schema}".{safe}').df()

        df_after = df_before.copy()
        changes = []

        if request.dedup:
            dup_count = df_after.duplicated().sum()
            if dup_count > 0:
                df_after = df_after.drop_duplicates()
                changes.append({"step": "Deduplication", "detail": f"Removed {int(dup_count)} duplicate rows", "rows_affected": int(dup_count)})

        for col in df_after.columns:
            null_count = int(df_after[col].isnull().sum())
            if null_count > 0:
                if request.null_strategy == "drop":
                    before_len = len(df_after)
                    df_after = df_after.dropna(subset=[col])
                    dropped = before_len - len(df_after)
                    if dropped > 0:
                        changes.append({"step": f"Drop nulls in '{col}'", "detail": f"Dropped {dropped} rows with null values", "rows_affected": dropped})
                elif request.null_strategy.startswith("fill_"):
                    fill_val = {"fill_mean": df_after[col].mean(), "fill_median": df_after[col].median(), "fill_mode": df_after[col].mode().iloc[0] if len(df_after[col].mode()) > 0 else 0}.get(request.null_strategy, 0)
                    filled = int(df_after[col].isnull().sum())
                    df_after[col] = df_after[col].fillna(fill_val)
                    changes.append({"step": f"Fill nulls in '{col}'", "detail": f"Filled {filled} nulls with {request.null_strategy.replace('fill_', '')} ({fill_val:.2f})", "rows_affected": filled})

        for col in request.drop_columns:
            if col in df_after.columns:
                df_after = df_after.drop(columns=[col])
                changes.append({"step": f"Drop column '{col}'", "detail": f"Removed column (was {df_before[col].dtype})", "rows_affected": 0})

        cols_before = set(df_before.columns)
        cols_after = set(df_after.columns)
        added_cols = list(cols_after - cols_before)
        removed_cols = list(cols_before - cols_after)

        def _null_summary(df):
            return {c: {"count": int(df[c].isnull().sum()), "pct": round(float(df[c].isnull().sum() / len(df) * 100), 1) if len(df) > 0 else 0} for c in df.columns if df[c].isnull().sum() > 0}

        return {
            "success": True,
            "before": {"rows": len(df_before), "cols": len(df_before.columns), "nulls": _null_summary(df_before)},
            "after": {"rows": len(df_after), "cols": len(df_after.columns), "nulls": _null_summary(df_after)},
            "changes": changes,
            "columns_added": added_cols,
            "columns_removed": removed_cols,
            "rows_removed": len(df_before) - len(df_after),
            "rows_pct": round((len(df_before) - len(df_after)) / len(df_before) * 100, 1) if len(df_before) > 0 else 0,
        }
    except Exception as e:
        logger.error("Clean preview failed: %s", e, exc_info=True)
        return {"success": False, "error": "Clean preview failed"}


@router.post("/load")
async def load_to_warehouse(request: LoadRequest, http_request: Request):
    """Load cleaned data into the caller's tenant schema."""
    from api.middleware.rate_limit import check_rate_limit

    check_rate_limit(http_request, "load")

    # The uploaded file is stored under the tenant's own directory, so one
    # tenant's upload can never overwrite another's (ADR-009 D1).
    raw_dir = _tenant_raw_dir()
    file_path = resolve_under(raw_dir, request.filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    extractor = _extractor()
    cleaner = _cleaner()
    loader = _loader()
    try:
        data = extractor.extract(file_path)
        sheet_name = request.sheet if request.sheet in data else list(data.keys())[0]
        df = data[sheet_name]
        logger.info(f"Extracted data shape: {df.shape}")

        cleaned_df = cleaner.clean(df)
        logger.info(f"Cleaned data shape: {cleaned_df.shape}")

        if cleaned_df.empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No rows to load after cleaning. "
                    "The file may be empty or malformed."
                ),
            )

        from api.security_paths import sanitize_table_name, validate_table_name

        table_name = request.table_name
        if table_name:
            validate_table_name(table_name)
        else:
            table_name = Path(request.filename).stem.lower().replace(" ", "_")
            if len(data) > 1:
                table_name = f"{table_name}_{sheet_name}"
            # A name derived from a filename can start with a digit
            # ("2024-sales.csv" -> "2024_sales"), which is not a valid
            # identifier - and every read route would then reject it.
            table_name = sanitize_table_name(table_name)
            validate_table_name(table_name)

        try:
            rows = loader.load_dataframe(cleaned_df, table_name, if_exists=request.if_exists)
            logger.info(f"Loaded {rows} rows into {table_name}")
        except Exception as e:
            logger.error(f"Load to warehouse failed: {e}", exc_info=True)
            raise

        return {
            "success": True,
            "table_name": table_name,
            "rows_loaded": rows,
            "message": f"Loaded {rows} rows into {table_name}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Load failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Load failed")
