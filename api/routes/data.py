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
from api.lineage_service import identity_column_map, record_persisted_transform
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


class CleanApplyRequest(BaseModel):
    """Request to clean a warehouse table and persist the result.

    The source table is never modified: the cleaned rows are written to a
    new table (``<table>_clean`` by default, suffixed on collision).
    """

    table_name: str
    output_table: Optional[str] = None
    dedup: bool = True
    null_strategy: str = "drop"
    drop_columns: List[str] = []
    trim_whitespace: bool = True
    empty_to_null: bool = True


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


_MAX_CLEAN_APPLY_ROWS = 1_000_000


def _clean_dataframe_ops(
    df: pd.DataFrame,
    *,
    dedup: bool,
    null_strategy: str,
    drop_columns: List[str],
    trim_whitespace: bool,
    empty_to_null: bool,
) -> tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Apply deterministic cleaning steps to a copy of *df*.

    Returns the cleaned frame plus a human-readable change log. The input
    frame is never mutated, so callers can compare before/after safely.
    """
    df_after = df.copy()
    changes: List[Dict[str, Any]] = []

    if trim_whitespace:
        for col in df_after.columns:
            if df_after[col].dtype == object:
                df_after[col] = df_after[col].map(
                    lambda v: v.strip() if isinstance(v, str) else v
                )
        changes.append(
            {"step": "Trim whitespace", "detail": "Trimmed text columns", "rows_affected": 0}
        )

    if empty_to_null:
        for col in df_after.columns:
            if df_after[col].dtype == object:
                df_after[col] = df_after[col].map(
                    lambda v: None if isinstance(v, str) and v.strip() == "" else v
                )
        changes.append(
            {"step": "Empty strings to null", "detail": "Normalized empty text cells", "rows_affected": 0}
        )

    if dedup:
        dup_count = int(df_after.duplicated().sum())
        if dup_count > 0:
            df_after = df_after.drop_duplicates()
            changes.append(
                {"step": "Deduplication", "detail": f"Removed {dup_count} duplicate rows", "rows_affected": dup_count}
            )

    for col in df_after.columns:
        null_count = int(df_after[col].isnull().sum())
        if null_count == 0:
            continue
        if null_strategy == "drop":
            before_len = len(df_after)
            df_after = df_after.dropna(subset=[col])
            dropped = before_len - len(df_after)
            if dropped > 0:
                changes.append(
                    {"step": f"Drop nulls in '{col}'", "detail": f"Dropped {dropped} rows with null values", "rows_affected": dropped}
                )
        elif null_strategy.startswith("fill_"):
            numeric = pd.api.types.is_numeric_dtype(df_after[col])
            if null_strategy == "fill_mean" and numeric:
                fill_val = df_after[col].mean()
            elif null_strategy == "fill_median" and numeric:
                fill_val = df_after[col].median()
            elif null_strategy == "fill_mode":
                modes = df_after[col].mode()
                fill_val = modes.iloc[0] if len(modes) > 0 else 0
            else:
                # Non-numeric mean/median falls back to the mode.
                modes = df_after[col].mode()
                fill_val = modes.iloc[0] if len(modes) > 0 else 0
            filled = null_count
            df_after[col] = df_after[col].fillna(fill_val)
            if isinstance(fill_val, float):
                shown = f"{fill_val:.2f}"
            else:
                shown = str(fill_val)
            changes.append(
                {"step": f"Fill nulls in '{col}'", "detail": f"Filled {filled} nulls with {null_strategy.replace('fill_', '')} ({shown})", "rows_affected": filled}
            )

    for col in drop_columns:
        if col in df_after.columns:
            df_after = df_after.drop(columns=[col])
            changes.append(
                {"step": f"Drop column '{col}'", "detail": f"Removed column (was {df[col].dtype})", "rows_affected": 0}
            )

    return df_after, changes


def _cleaning_code_snippet(
    request: "CleanApplyRequest", output_table: str
) -> str:
    """Return an auditable pandas snippet mirroring the applied steps."""
    lines = [
        "# Generated by DataAgent - deterministic cleaning (pandas)",
        "df = df.copy()",
    ]
    if request.trim_whitespace:
        lines.append(
            "for _c in df.select_dtypes(include=['object']).columns:\n"
            "    df[_c] = df[_c].map(lambda v: v.strip() if isinstance(v, str) else v)"
        )
    if request.empty_to_null:
        lines.append(
            "for _c in df.select_dtypes(include=['object']).columns:\n"
            "    df[_c] = df[_c].map(lambda v: None if isinstance(v, str) and v.strip() == '' else v)"
        )
    if request.dedup:
        lines.append("df = df.drop_duplicates()")
    if request.null_strategy == "drop":
        lines.append("df = df.dropna()")
    elif request.null_strategy.startswith("fill_"):
        method = request.null_strategy.replace("fill_", "")
        lines.append(f"df = df.fillna(df.{method}())")
    if request.drop_columns:
        lines.append(f"df = df.drop(columns={request.drop_columns!r})")
    lines.append(f"# persisted as {output_table!r} (source table untouched)")
    return "\n".join(lines)


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

        df_after, changes = _clean_dataframe_ops(
            df_before,
            dedup=request.dedup,
            null_strategy=request.null_strategy,
            drop_columns=request.drop_columns,
            trim_whitespace=False,
            empty_to_null=False,
        )

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


def _free_output_table(
    table_name: str, tenant_id: Optional[str] = None
) -> str:
    """Pick ``<table>_clean`` (``_clean2``, ``_clean3``, ... on collision)."""
    from api.security_paths import sanitize_table_name

    loader = _loader()
    base = sanitize_table_name(f"{table_name}_clean")
    output_table = base
    suffix = 2
    while loader.table_exists(output_table, tenant_id=tenant_id):
        output_table = f"{base}{suffix}"
        suffix += 1
        if suffix > 50:
            raise HTTPException(
                status_code=409,
                detail="Could not find a free output table name",
            )
    return output_table


@router.post("/clean/apply")
async def clean_apply(request: CleanApplyRequest, http_request: Request):
    """Clean a warehouse table and persist the result to a NEW table.

    The source table is never modified. The output defaults to
    ``<table>_clean`` (``<table>_clean2``, ``_clean3``, ... on collision) so a
    repeated task can never destroy earlier results.
    """
    from api.middleware.rate_limit import check_rate_limit
    from api.security_paths import validate_table_name

    check_rate_limit(http_request, "load")

    table_name = validate_table_name(request.table_name)
    loader = _loader()
    if not loader.table_exists(table_name):
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    if request.output_table:
        output_table = validate_table_name(request.output_table)
        if output_table == table_name:
            raise HTTPException(
                status_code=400, detail="Refusing to overwrite the source table"
            )
    else:
        output_table = _free_output_table(table_name)

    safe = '"' + table_name.replace('"', '""') + '"'
    try:
        with loader.get_connection() as conn:
            df_before = conn.execute(f"SELECT * FROM {safe}").df()

        if len(df_before) > _MAX_CLEAN_APPLY_ROWS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Table is too large to clean here "
                    f"(max {_MAX_CLEAN_APPLY_ROWS} rows)"
                ),
            )

        df_after, changes = _clean_dataframe_ops(
            df_before,
            dedup=request.dedup,
            null_strategy=request.null_strategy,
            drop_columns=request.drop_columns,
            trim_whitespace=request.trim_whitespace,
            empty_to_null=request.empty_to_null,
        )

        if df_after.empty:
            raise HTTPException(
                status_code=400,
                detail="No rows left after cleaning; source table left unchanged",
            )

        rows = loader.load_dataframe(df_after, output_table, if_exists="fail")
        logger.info(
            "Clean apply: %s -> %s (%s rows)", table_name, output_table, rows
        )

        from api.tenancy import current_tenant_id

        record_persisted_transform(
            operation="clean",
            inputs=[table_name],
            outputs=[output_table],
            table=output_table,
            description=f"Cleaned {table_name} into {output_table}",
            metadata={
                "rows_before": len(df_before),
                "rows_after": len(df_after),
                "changes": changes,
                "dedup": request.dedup,
                "null_strategy": request.null_strategy,
                "drop_columns": list(request.drop_columns),
            },
            column_map=identity_column_map(
                table_name,
                [col for col in df_after.columns if col in df_before.columns],
            ),
            tenant_id=current_tenant_id(),
        )

        return {
            "success": True,
            "source_table": table_name,
            "output_table": output_table,
            "rows_before": len(df_before),
            "rows_after": len(df_after),
            "cols_before": len(df_before.columns),
            "cols_after": len(df_after.columns),
            "rows_loaded": rows,
            "changes": changes,
            "cleaning_code": _cleaning_code_snippet(request, output_table),
            "message": f"Cleaned {table_name} -> {output_table} ({len(df_after)} rows)",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Clean apply failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Clean apply failed")


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

        from api.tenancy import current_tenant_id

        record_persisted_transform(
            operation="load",
            inputs=[request.filename],
            outputs=[table_name],
            table=table_name,
            description=f"Loaded {request.filename} into {table_name}",
            metadata={
                "rows": rows,
                "filename": request.filename,
                "sheet": sheet_name,
                "if_exists": request.if_exists,
            },
            column_map=identity_column_map(request.filename, cleaned_df.columns),
            tenant_id=current_tenant_id(),
        )

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
