"""
PBI Studio Router â€” model-first Power BI studio endpoints.

Endpoints:
- Get Data: import-paste, import-file, import-warehouse, schema
- Model: save, measure, relationship, date-table, suggest
- Report: save, load, list, visual-data
- Export: pbit, csv, xlsx
"""
import base64
import hashlib
import io
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, Query
from pydantic import BaseModel, Field

from api.billing.gate import require_feature
from api.billing.schema import FEATURE_DASHBOARD_EXPORT, FEATURE_RECREATE_FROM_IMAGE
from api.security_paths import validate_table_name
from etl.pandas_compat import register_dataframe

from api import state as _app_state
from api import pbix_spec as _pbix_spec
from api.dashboard_store import (
    create_dashboard,
    get_dashboard,
    list_dashboards,
    update_dashboard,
    delete_dashboard,
)
from api.tenancy import tenant_search_path, tenant_search_path_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashstudio", tags=["dashstudio"])

# Free-tier vision model used by image analysis. The default opencode provider
# model is text-only, so it must be passed explicitly on every vision call.
_CODE_VISION_MODEL = "mimo-v2.6-flash-free"


def _get_con():
    """Short-lived connection to the curated warehouse.

    DuckDB's in-process instance cache means every ``duckdb.connect`` on the
    same file attaches to one shared database instance, so handlers can
    safely open/close their own connection (isolated statement sequence)
    without clashing with the app-wide handle held by ``api.db``.
    """
    import duckdb

    from config import settings

    return duckdb.connect(str(settings.duckdb.database_path))


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Schemas
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ColumnSchema(BaseModel):
    name: str
    type: str  # VARCHAR, DOUBLE, DATE, TIMESTAMP, BOOLEAN, INTEGER, BIGINT
    role: str  # dimension, measure, date, key


class TableSchema(BaseModel):
    name: str
    source: dict  # {"type": "csv|file|warehouse", "table": "..."}
    columns: list[ColumnSchema]
    measures: list[dict] = []  # {"name": "...", "dax": "..."}


class RelationshipSchema(BaseModel):
    fromTable: str
    fromColumn: str
    toTable: str
    toColumn: str


class DateTableSchema(BaseModel):
    source: str  # "Table.column"


class ModelSchema(BaseModel):
    tables: list[TableSchema]
    relationships: list[RelationshipSchema] = []
    dateTable: Optional[DateTableSchema] = None


class VisualSpec(BaseModel):
    type: str  # kpi, bar, line, pie, table, scatter, area, matrix
    table: str
    measure: Optional[str] = None
    dimension: Optional[str] = None
    time: Optional[str] = None  # day, week, month, quarter, year
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 3


class SlicerSpec(BaseModel):
    table: str
    field: str
    kind: str  # select, timeline


class PageSchema(BaseModel):
    name: str
    visuals: list[VisualSpec] = []
    slicers: list[SlicerSpec] = []


class ReportSchema(BaseModel):
    pages: list[PageSchema]


class DashboardObject(BaseModel):
    id: Optional[str] = None
    name: str
    title: str = ""
    subtitle: str = ""
    model: ModelSchema
    report: ReportSchema


class ImportPasteRequest(BaseModel):
    table_name: str
    csv_text: str


class ImportWarehouseRequest(BaseModel):
    table: str  # analytics.table_name


class MeasureRequest(BaseModel):
    table: str
    name: str
    dax: str


class RelationshipRequest(BaseModel):
    fromTable: str
    fromColumn: str
    toTable: str
    toColumn: str


class DateTableRequest(BaseModel):
    source: str


class VisualDataRequest(BaseModel):
    page: int = 0
    visual: int = 0
    filters: dict = {}  # {"table.field": ["value1", "value2"]}
    #: Saved dashboard to resolve the visual from. Mutually usable with
    #: ``dashboard`` (an inline dashboard object).
    dash_id: Optional[str] = None
    #: Inline dashboard object (``{"model": ..., "report": ...}``).
    dashboard: Optional[Dict[str, Any]] = None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _json_safe_rows(df: pd.DataFrame) -> list:
    """Convert a DataFrame to JSON-safe records, replacing NaN/NaT with None
    so the response body serializes cleanly (NaN is not JSON compliant)."""
    import numpy as np
    out = []
    for rec in df.to_dict("records"):
        clean = {}
        for k, v in rec.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                clean[k] = None
            elif isinstance(v, (pd.Timestamp, type(pd.NaT))) or pd.isna(v):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out


def _infer_type(series: pd.Series) -> str:
    """Infer DuckDB type from pandas series."""
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        return "DOUBLE"
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    # Try parsing as date
    try:
        pd.to_datetime(series.dropna().iloc[0] if len(series.dropna()) > 0 else None)
        return "DATE"
    except Exception:
        pass
    return "VARCHAR"


def _infer_role(name: str, dtype: str) -> str:
    """Infer semantic role from column name and type."""
    name_lower = name.lower()
    if dtype in ("DATE", "TIMESTAMP"):
        return "date"
    if name_lower.endswith("_id") or name_lower in ("id", "key", "pk"):
        return "key"
    if dtype in ("BIGINT", "DOUBLE", "INTEGER"):
        return "measure"
    return "dimension"


def _stage_to_warehouse(table_name: str, df: pd.DataFrame) -> int:
    """Stage a dataframe to analytics.<table_name> in DuckDB."""
    # Guarded: this identifier reaches CREATE OR REPLACE TABLE / COUNT(*).
    table_name = validate_table_name(table_name)
    con = _get_con()
    try:
        register_dataframe(con, f"staging_{table_name}", df)
        con.execute(f"CREATE OR REPLACE TABLE analytics.{table_name} AS SELECT * FROM staging_{table_name}")
        con.unregister(f"staging_{table_name}")
        count = con.execute(f"SELECT COUNT(*) FROM analytics.{table_name}").fetchone()[0]
        return count
    finally:
        con.close()


def _get_table_schema(table_name: str) -> list[dict]:
    """Get column schema for a warehouse table."""
    con = _get_con()
    try:
        cols = con.execute(f"DESCRIBE analytics.{table_name}").fetchall()
        return [
            {
                "name": c[0],
                "type": c[1],
                "nullable": c[2],
                "key": c[3],
                "default": c[4],
            }
            for c in cols
        ]
    finally:
        con.close()


def _sample_rows(table_name: str, n: int = 5) -> list[dict]:
    """Get sample rows from a table."""
    con = _get_con()
    try:
        rows = con.execute(f"SELECT * FROM analytics.{table_name} LIMIT {n}").fetchall()
        cols = [d[0] for d in con.execute(f"SELECT * FROM analytics.{table_name} LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def _validate_table_exists(table_name: str) -> bool:
    """Check if a table exists in analytics schema."""
    con = _get_con()
    try:
        result = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'analytics' AND table_name = ?",
            [table_name],
        ).fetchone()
        return result is not None
    finally:
        con.close()


def _get_table_columns(table_name: str) -> list[str]:
    """Get column names for a table."""
    con = _get_con()
    try:
        rows = con.execute(f"DESCRIBE analytics.{table_name}").fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Get Data Endpoints
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/import-paste")
async def import_paste(req: ImportPasteRequest):
    """Paste CSV text â†’ parse â†’ infer schema â†’ stage to warehouse."""
    try:
        df = pd.read_csv(io.StringIO(req.csv_text))
    except Exception as e:
        raise HTTPException(400, f"CSV parse error: {e}")

    # Infer schema
    columns = []
    for col in df.columns:
        dtype = _infer_type(df[col])
        role = _infer_role(col, dtype)
        columns.append({"name": col, "type": dtype, "role": role})

    # Stage to warehouse
    table_name = req.table_name
    row_count = _stage_to_warehouse(table_name, df)

    return {
        "success": True,
        "table": table_name,
        "columns": columns,
        "sample_rows": _json_safe_rows(df.head(5)),
        "row_count": row_count,
    }


@router.post("/import-file")
async def import_file(file: UploadFile = File(...), table_name: str = Form(...)):
    """Upload CSV/XLSX/Parquet â†’ parse â†’ infer schema â†’ stage to warehouse."""
    # Enforce the same size cap as /upload. This endpoint previously had no
    # cap at all, so an unbounded body was buffered into memory and then
    # handed to a parquet/zip parser (a decompression-bomb surface).
    from config import settings

    max_bytes = int(getattr(settings.data, "max_upload_size_mb", 100)) * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            413,
            f"File too large (limit {getattr(settings.data, 'max_upload_size_mb', 100)} MB)",
        )
    fname = file.filename or ""

    try:
        if fname.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        elif fname.endswith(".parquet"):
            df = pd.read_parquet(io.BytesIO(content))
        else:
            raise HTTPException(400, "Unsupported file type. Use CSV, XLSX, or Parquet.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"File parse error: {e}")

    # Infer schema
    columns = []
    for col in df.columns:
        dtype = _infer_type(df[col])
        role = _infer_role(col, dtype)
        columns.append({"name": col, "type": dtype, "role": role})

    # Stage to warehouse
    row_count = _stage_to_warehouse(table_name, df)

    return {
        "success": True,
        "table": table_name,
        "columns": columns,
        "sample_rows": _json_safe_rows(df.head(5)),
        "row_count": row_count,
    }


@router.post("/import-warehouse")
async def import_warehouse(req: ImportWarehouseRequest):
    """Pick existing warehouse table â†’ describe â†’ preview."""
    # Guarded: the identifier reaches SELECT/DESCRIBE against the warehouse.
    table = validate_table_name(req.table)
    # Validate table exists
    con = _get_con()
    try:
        con.execute(f"SELECT 1 FROM analytics.{table} LIMIT 1")
    except Exception:
        raise HTTPException(404, f"Table analytics.{table} not found")
    finally:
        con.close()

    cols = _get_table_schema(table)
    columns = []
    for c in cols:
        dtype = c["type"].upper()
        role = _infer_role(c["name"], dtype)
        columns.append({"name": c["name"], "type": dtype, "role": role})

    sample = _sample_rows(table, 5)
    row_count = 0
    con = _get_con()
    try:
        row_count = con.execute(f"SELECT COUNT(*) FROM analytics.{table}").fetchone()[0]
    finally:
        con.close()

    return {
        "success": True,
        "table": table,
        "columns": columns,
        "sample_rows": sample,
        "row_count": row_count,
    }


@router.get("/schema")
async def get_schema(table: str = Query(...)):
    """Full schema + role inference for a warehouse table."""
    return await import_warehouse(ImportWarehouseRequest(table=table))


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Model Endpoints
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/model")
async def save_model(dash: DashboardObject):
    """Save entire dashboard model."""
    dash_id = dash.id or str(uuid.uuid4())
    dash_dict = dash.model_dump()
    dash_dict["id"] = dash_id
    result = create_dashboard(dash.name, dash_dict)
    return {"success": True, "id": result["id"]}


@router.post("/model/measure")
async def add_measure(req: MeasureRequest):
    """Add/update a DAX measure (validated against DuckDB)."""
    # Validate DAX by running it as SQL against the table
    con = _get_con()
    try:
        # Simple validation: try to run the DAX as a SELECT
        # In real impl, this would use a DAX parser; for now just check it runs
        test_sql = f"SELECT {req.dax} AS val FROM analytics.{req.table} LIMIT 1"
        con.execute(test_sql)
    except Exception as e:
        raise HTTPException(400, f"DAX validation failed: {e}")
    finally:
        con.close()

    return {"success": True, "measure": {"name": req.name, "dax": req.dax, "table": req.table}}


@router.post("/model/relationship")
async def add_relationship(req: RelationshipRequest):
    """Add a relationship between tables."""
    # Validate both tables and columns exist
    con = _get_con()
    try:
        for t, c in [(req.fromTable, req.fromColumn), (req.toTable, req.toColumn)]:
            cols = [r[0] for r in con.execute(f"DESCRIBE analytics.{t}").fetchall()]
            if c not in cols:
                raise HTTPException(400, f"Column {c} not found in {t}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Validation error: {e}")
    finally:
        con.close()

    return {"success": True, "relationship": req.model_dump()}


@router.post("/model/date-table")
async def create_date_table(req: DateTableRequest):
    """Create auto date table for a date column."""
    # Parse source: "Table.column"
    try:
        table, column = req.source.split(".", 1)
    except ValueError:
        raise HTTPException(400, "Source must be 'Table.column'")

    table = validate_table_name(table)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", column or ""):
        raise HTTPException(400, "Invalid column name")

    con = _get_con()
    try:
        # Get min/max date
        row = con.execute(
            f'SELECT MIN("{column}"), MAX("{column}") FROM analytics.{table}'
        ).fetchone()
        if not row or row[0] is None:
            raise HTTPException(400, "No date data found")
        min_date, max_date = row
    finally:
        con.close()

    # Generate date table name
    dt_name = f"DateTable_{table}_{column}".replace(".", "_")

    # Build date table using DuckDB's generate_series. The series must be
    # aliased with a column name (``d(dt)``) — referencing the bare table
    # alias inside EXTRACT binds a STRUCT, not the timestamp.
    con = _get_con()
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE analytics.{dt_name} AS
            SELECT
                d.dt::DATE as Date,
                EXTRACT(DAY FROM d.dt) as Day,
                EXTRACT(MONTH FROM d.dt) as Month,
                EXTRACT(QUARTER FROM d.dt) as Quarter,
                EXTRACT(YEAR FROM d.dt) as Year,
                strftime(d.dt, '%b') as MonthName,
                strftime(d.dt, '%Y') as YearName,
                strftime(d.dt, '%b %Y') as MonthYear,
                CASE WHEN EXTRACT(DOW FROM d.dt) IN (0,6) THEN 'Weekend' ELSE 'Weekday' END as DayType
            FROM generate_series(CAST('{min_date}' AS DATE), CAST('{max_date}' AS DATE), INTERVAL 1 DAY) as d(dt)
        """)
    finally:
        con.close()

    return {
        "success": True,
        "dateTable": {"name": dt_name, "source": req.source, "columns": [
            "Date", "Day", "Month", "Quarter", "Year", "MonthName", "YearName", "MonthYear", "DayType"
        ]}
    }


@router.get("/model/suggest")
async def suggest_relationships():
    """Auto-suggest star-schema relationships by matching column names/values."""
    con = _get_con()
    try:
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'analytics'"
        ).fetchall()
        table_names = [t[0] for t in tables]
    finally:
        con.close()

    def _q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    suggestions = []
    for i, t1 in enumerate(table_names):
        for t2 in table_names[i+1:]:
            con = _get_con()
            try:
                c1 = set(r[0] for r in con.execute(f"DESCRIBE analytics.{_q(t1)}").fetchall())
                c2 = set(r[0] for r in con.execute(f"DESCRIBE analytics.{_q(t2)}").fetchall())
                common = c1 & c2
                for col in common:
                    # Check if it looks like a key (id, key, pk) or has high uniqueness
                    if col.lower().endswith(("_id", "id", "key", "pk")):
                        suggestions.append({
                            "fromTable": t1,
                            "fromColumn": col,
                            "toTable": t2,
                            "toColumn": col,
                            "confidence": "high",
                        })
                    elif len(common) == 1:
                        suggestions.append({
                            "fromTable": t1,
                            "fromColumn": col,
                            "toTable": t2,
                            "toColumn": col,
                            "confidence": "medium",
                        })
            finally:
                con.close()

    return {"success": True, "suggestions": suggestions}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Report / Dashboard Endpoints
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/save")
async def save_dashboard(dash: DashboardObject, request: Request):
    """Save a full dashboard (model + report).

    The owner comes from the session, never from the request body.
    """
    owner_id, _is_admin = _dashstudio_owner(request)
    dash_id = dash.id or str(uuid.uuid4())
    dash_dict = dash.model_dump()
    dash_dict["id"] = dash_id
    result = create_dashboard(dash.name, dash_dict, owner_id=owner_id or "")
    return {"success": True, "id": result["id"]}


@router.get("/load/{dash_id}")
async def load_dashboard(dash_id: str, request: Request):
    """Load a dashboard by ID (owner-scoped)."""
    owner_id, is_admin = _dashstudio_owner(request)
    result = get_dashboard(dash_id)
    if not result:
        raise HTTPException(404, "Dashboard not found")
    row_owner = result.get("owner_id") or ""
    if row_owner and row_owner != owner_id and not is_admin:
        raise HTTPException(404, "Dashboard not found")
    if not row_owner and not is_admin:
        # Legacy dashboard with no owner — admin-only (ADR-009 D1).
        raise HTTPException(404, "Dashboard not found")
    return {"success": True, "dashboard": result}


def _dashstudio_owner(request: Request) -> tuple:
    """Return ``(owner_id, is_admin)`` for the caller.

    Ownership always comes from the session, never from the request body.
    DashStudio's list/load/delete used to be unscoped, which handed every
    authenticated caller every dashboard in the warehouse (ADR-009 D1).
    """
    user = getattr(request.state, "user", None)
    owner_id = str(getattr(user, "user_id", "") or "") or None
    is_admin = bool(user is not None and getattr(user, "is_admin", False))
    return owner_id, is_admin


@router.get("/list")
async def list_dashboards_endpoint(request: Request):
    """List the caller's dashboards (admins see every dashboard)."""
    owner_id, is_admin = _dashstudio_owner(request)
    return {
        "success": True,
        "dashboards": list_dashboards(owner_id, include_legacy=is_admin),
    }


@router.delete("/{dash_id}")
async def delete_dashboard_endpoint(dash_id: str, request: Request):
    """Delete a dashboard (owner-scoped)."""
    owner_id, is_admin = _dashstudio_owner(request)
    if not delete_dashboard(dash_id, owner_id, include_legacy=is_admin):
        raise HTTPException(404, "Dashboard not found")
    return {"success": True}


@router.post("/visual-data")
async def get_visual_data(req: VisualDataRequest, request: Request = None):
    """Get aggregated data for a visual with slicer filters applied.

    The visual is resolved from the dashboard object (either a saved
    dashboard by ``dash_id`` or one supplied inline as ``dashboard``), its
    SQL is built from the visual spec, and the slicer selections are
    applied as WHERE predicates.

    The generated SQL goes through the same read-only policy as ``/sql``:
    it is validated by :class:`agent.tools_sql.SQLTool`, rewritten for
    row-level security, and executed inside a rolled-back transaction on
    the process-wide warehouse connection.
    """
    from agent.tools_sql import SQLTool
    from api.db import get_connection, _bi_query_lock
    from api.rls import apply_rls
    from api.security_paths import validate_column_name, validate_table_name
    from config import settings
    from etl.readonly import readonly_transaction

    dashboard = req.dashboard
    if dashboard is None and req.dash_id:
        loaded = get_dashboard(req.dash_id)
        if not loaded:
            return {"success": False, "error": f"Dashboard '{req.dash_id}' not found"}
        dashboard = loaded

    if dashboard is None:
        return {
            "success": False,
            "error": "A dashboard is required — pass 'dash_id' or 'dashboard'.",
        }

    pages = ((dashboard.get("report") or {}).get("pages")) or []
    if not pages:
        return {"success": False, "error": "Dashboard has no pages"}
    if req.page < 0 or req.page >= len(pages):
        return {"success": False, "error": f"Page {req.page} out of range"}
    page = pages[req.page]

    visuals = page.get("visuals") or []
    if req.visual < 0 or req.visual >= len(visuals):
        return {"success": False, "error": f"Visual {req.visual} out of range"}
    visual = visuals[req.visual]

    try:
        table = validate_table_name(visual.get("table", ""))
    except HTTPException as exc:
        return {"success": False, "error": exc.detail}

    measure = visual.get("measure") or ""
    dimension = visual.get("dimension") or ""
    time_col = visual.get("time") or ""
    grain = visual.get("grain") or ""

    if measure and dimension:
        select = f'"{dimension}" AS dimension, {measure} AS value'
        group = f'GROUP BY "{dimension}"'
        order = 'ORDER BY value DESC'
    elif measure:
        select = f"{measure} AS value"
        group = ""
        order = ""
    elif dimension:
        select = f'"{dimension}" AS dimension'
        group = ""
        order = ""
    else:
        return {"success": False, "error": "Visual has neither a measure nor a dimension"}

    # Slicer selections: {"table.field": ["value1", "value2"]}
    where_parts = []
    for key, values in (req.filters or {}).items():
        if not isinstance(values, (list, tuple)) or not values:
            continue
        field = key.split(".")[-1]
        try:
            field = validate_column_name(field)
        except HTTPException as exc:
            return {"success": False, "error": exc.detail}
        literals = ", ".join(
            "NULL" if v is None else
            (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
             else "'" + str(v).replace("'", "''") + "'")
            for v in values
        )
        where_parts.append(f'"{field}" IN ({literals})')

    # Clause order matters: SELECT -> FROM -> WHERE -> GROUP BY -> ORDER BY.
    sql = f"SELECT {select} FROM {table}"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if group:
        sql += f" {group}"
    if order:
        sql += f" {order}"
    sql += " LIMIT 1000"

    # Same read-only policy as /sql and /bi/query.
    tool = SQLTool(db_path=settings.duckdb.database_path, schema=settings.duckdb.schema)
    is_valid, error = tool.validate_query(sql)
    if not is_valid:
        return {"success": False, "error": error,
                "hint": "Only SELECT and WITH queries are allowed."}

    user = getattr(getattr(request, "state", None), "user", None)
    if user:
        sql = apply_rls(sql, user)

    try:
        conn = get_connection()
        with _bi_query_lock:
            # The shared connection is process-wide: set the tenant search
            # path for this query AND restore it afterwards. Leaving the
            # caller's schema on it would let the *next* request (any
            # tenant) resolve that tenant's tables — a cross-tenant read
            # (docs/LAUNCH-READINESS.md §6 defect #6).
            with tenant_search_path_scope(conn):
                with readonly_transaction(conn):
                    df = conn.execute(sql).df()
        return {
            "success": True,
            "sql": sql,
            "columns": list(df.columns),
            "data": _json_safe_rows(df),
            "row_count": int(len(df)),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"Failed to load visual data: {exc}"}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Export Endpoints
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/export/pbit/{dash_id}")
async def export_pbit(
    dash_id: str,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Export dashboard as a .pbix report (delegates to the rebuilt exporter).

    .pbix (not .pbit) so Power BI Desktop opens it with data — .pbit templates
    open in "no data" mode and render charts empty.
    """
    import re

    import duckdb
    from fastapi.responses import StreamingResponse

    from api.dashboards import get_dashboard_manager
    from config import settings
    from export import pbix_export

    dash = get_dashboard_manager().get(dash_id)
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    con = duckdb.connect(str(settings.duckdb.database_path))
    con.execute(f"SET search_path = '{tenant_search_path()}'")

    def run_query(sql):
        return con.execute(sql).df()

    def load_table(table, limit):
        return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()

    try:
        data = pbix_export.build_powerbi_pbit(dash, run_query, load_table)
    finally:
        con.close()
    fname = re.sub(r'[\\/:*?"<>|]+', "_", str(dash.get("name") or "dashboard")).strip() or "dashboard"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}.pbix"'},
    )


@router.get("/export/csv/{table}")
async def export_csv(
    table: str,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Export a model table as CSV."""
    table = validate_table_name(table)
    con = _get_con()
    try:
        df = con.execute(f"SELECT * FROM analytics.{table}").df()
    finally:
        con.close()
    return {"success": True, "csv": df.to_csv(index=False)}


@router.get("/export/xlsx/{table}")
async def export_xlsx(
    table: str,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Export a model table as base64-encoded XLSX (capped at EXPORT_MAX_ROWS)."""
    table = validate_table_name(table)
    from export.bi_export import _max_export_rows

    con = _get_con()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM analytics.{table}").fetchone()[0]
        df = con.execute(
            f"SELECT * FROM analytics.{table} LIMIT {int(_max_export_rows())}"
        ).df()
    finally:
        con.close()
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "success": True,
        "xlsx_base64": encoded,
        "rows": len(df),
        "total_rows": total,
        "truncated": total > len(df),
    }


# ─────────────────────────────────────────────────────────────
# Image → Dashboard (recreate a dashboard from a screenshot)
# ─────────────────────────────────────────────────────────────

_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Chart families that render from the same (dimension, measure) SQL, so a zone
# can be swapped between them without re-validating its query.
_SWAP_SAFE_CHARTS = {
    "line", "area", "bar", "column", "donut", "pie",
    "horizontalbar", "stackedbar",
}


def _detect_image_mime(raw: bytes) -> Optional[str]:
    """Validate the upload is a real PNG/JPEG/WebP by magic bytes."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_or(v, default):
    """Return v when it is a usable #hex color, else default."""
    return v if (isinstance(v, str) and _HEX_RE.match(v)) else default


def _coerce_spec(data: dict) -> Optional[dict]:
    """Validate/normalize a parsed spec dict (pages + widgets present)."""
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        # Coerce a flat widget list into a single page.
        if isinstance(data.get("widgets"), list) and data["widgets"]:
            data["pages"] = [{"name": "Page 1", "widgets": data["widgets"]}]
        else:
            return None
    for p in data["pages"]:
        for w in p.get("widgets") or []:
            if isinstance(w, dict):
                _normalize_widget_box(w)
    # Normalize the header object: keep it only when explicitly present,
    # and give it a clamped box + usable colors.
    hdr = data.get("header")
    if isinstance(hdr, dict) and hdr.get("present"):
        hdr["present"] = True
        _normalize_widget_box(hdr)
        hdr["background"] = _hex_or(hdr.get("background"), "")
        hdr["textColor"] = _hex_or(hdr.get("textColor"), "")
        hdr["title"] = str(hdr.get("title") or "").strip()
        data["header"] = hdr
    else:
        data["header"] = {"present": False}
    # Surface colors: fall back to theme-consistent defaults.
    dark = str(data.get("theme") or "light").lower() == "dark"
    data["background"] = _hex_or(data.get("background"), "#0f172a" if dark else "#f8fafc")
    data["cardBg"] = _hex_or(data.get("cardBg"), "#252540" if dark else "#ffffff")
    data["textColor"] = _hex_or(data.get("textColor"), "#e0e0e0" if dark else "#0f172a")
    data["accent"] = _hex_or(data.get("accent"), "#818cf8" if dark else "#2563eb")
    if isinstance(data.get("palette"), list):
        data["palette"] = [c for c in data["palette"] if isinstance(c, str) and _HEX_RE.match(c)][:10]
    else:
        data["palette"] = []
    return data


# Fallback box anchors when the vision model omits x/y/w/h: region → top-left
# corner, size → footprint. Only used so old specs still lay out sensibly.
_REGION_ANCHOR = {
    "top-left": (2, 6), "top-center": (34, 6), "top-right": (66, 6),
    "mid-left": (2, 34), "mid-center": (34, 34), "mid-right": (66, 34),
    "bottom-left": (2, 62), "bottom-center": (34, 62), "bottom-right": (66, 62),
    "header": (2, 2), "footer": (2, 90), "full": (2, 2),
}
_SIZE_FOOTPRINT = {"small": (30, 24), "medium": (30, 30), "large": (60, 42)}


def _normalize_widget_box(w: dict) -> None:
    """Clamp a widget's x/y/w/h to 0-100 percent; derive from region/size when
    the vision model left them out."""
    def num(v, lo, hi, default):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, f))

    x = num(w.get("x"), 0, 100, None)
    y = num(w.get("y"), 0, 100, None)
    ww = num(w.get("w"), 0, 100, None)
    hh = num(w.get("h"), 0, 100, None)
    if ww is None or hh is None:
        fw, fh = _SIZE_FOOTPRINT.get(str(w.get("size") or "").lower(), (30, 30))
        if ww is None:
            ww = fw
        if hh is None:
            hh = fh
    if x is None or y is None:
        ax, ay = _REGION_ANCHOR.get(str(w.get("region") or "").lower(), (2, 6))
        if x is None:
            x = ax
        if y is None:
            y = ay
    # Keep the box inside the canvas.
    w["x"] = round(min(x, 100 - ww), 2)
    w["y"] = round(min(y, 100 - hh), 2)
    w["w"] = round(ww, 2)
    w["h"] = round(hh, 2)


def _repair_json_object(text: str) -> Optional[dict]:
    """Salvage a dict from a reply the vision model truncated mid-stream.

    Free vision models routinely cut the reply while writing the final
    ``buildPrompt`` member (no closing quote, no closing brace). Strategy:
    drop the malformed trailing member at the last top-level separator, then
    close the still-open object/array scopes and try ``json.loads``. Also
    tolerates trailing commas before a closing bracket.
    """
    import re as _re

    text = text.strip()
    if not text.startswith("{"):
        return None

    def _ends_open_string(t: str) -> bool:
        in_str, esc = False, False
        for ch in t:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
        return in_str

    def _closers(t: str) -> str:
        stack = []
        in_str, esc = False, False
        for ch in t:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        return "".join(reversed(stack))

    candidates = [text]
    if _ends_open_string(text):
        commas = [i for i in range(len(text)) if text[i] == ","]
        for i in reversed(commas[-12:]):
            candidates.append(text[:i])
    for cand in candidates:
        cand = _re.sub(r",\s*([}\]])", r"\1", cand).rstrip()
        # A reply cut mid-value leaves an unterminated string; close it so the
        # partial member survives (e.g. '{"a": "xyz' -> {"a": "xyz"}).
        if _ends_open_string(cand):
            cand += '"'
        try:
            obj = json.loads(cand + _closers(cand))
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_spec_json(content: str) -> Optional[dict]:
    """Fence-strip + validate the vision model's spec JSON.

    Tolerates ``` fences, prose around the JSON, and trailing design-brief text
    that may itself contain braces (balanced-brace scan). Returns None when the
    reply is not usable JSON so the caller can degrade cleanly.
    """
    if not content:
        return None
    import re as _re

    text = _re.sub(r"```(?:json)?\s*", "", content).strip()

    # Fast path: the whole reply is the JSON object.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _coerce_spec(data)
    except Exception:
        pass

    # Repair path: recover a spec from a reply truncated mid-stream.
    repaired = _repair_json_object(text)
    if repaired is not None:
        return _coerce_spec(repaired)

    # Slow path: scan for a balanced JSON object anywhere in the reply.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:i + 1])
                        if isinstance(data, dict):
                            return _coerce_spec(data)
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def _count_widgets(spec: dict) -> int:
    n = 0
    for p in spec.get("pages") or []:
        n += len(p.get("widgets") or [])
    return n


def _extract_brief(content: str, spec: dict) -> str:
    """Pull the natural-language design brief out of the raw vision reply."""
    import re as _re

    text = _re.sub(r"```(?:json)?\s*", "", content or "").strip()
    m = _re.search(r"\{[\s\S]*\}", text)
    if m:
        text = (text[: m.start()] + text[m.end():]).strip()
    text = text.strip().strip("`").strip()
    if not text:
        fams = []
        for p in spec.get("pages") or []:
            for w in p.get("widgets") or []:
                fams.append(str(w.get("family") or "chart"))
        text = ("Rebuild this dashboard: " + ", ".join(fams[:8])
                + (f" using a {spec.get('theme')} theme with accent {spec.get('accent')}."))
    return text[:2000]


def _extract_build_prompt(content: str, spec: dict) -> str:
    """Pull the React-reimplementation brief from the vision reply.

    Prefers the `buildPrompt` field the vision model emits inside the spec;
    falls back to the natural-language design brief trailing the JSON.
    """
    build = spec.get("buildPrompt")
    if isinstance(build, str) and build.strip():
        return build.strip()[:2000]
    return _extract_brief(content, spec)


async def _analyze_image(raw: bytes, mime: str, model: Optional[str] = None) -> dict:
    """Run the vision model over the image and return {spec, prompt, buildPrompt, ...}.

    `model` overrides the configured default provider model (used to force a
    cheaper/free vision model). Free-tier vision
    models are flaky and sometimes stream back prose with no usable JSON, so a
    non-JSON reply is retried once; transport/quota errors propagate so the
    caller can report them distinctly. Raises ValueError when the model reply is
    still not usable JSON after the retry.
    """
    import os as _os
    import tempfile as _tempfile
    from agent.llm_client import Message, opencode_provider_from_settings

    system = (
        "You are a dashboard design analyst. Read the dashboard screenshot and describe it "
        "EXACTLY so it can be rebuilt pixel-for-pixel. Return ONLY valid JSON with this shape:\n"
        '{"theme":"light|dark","background":"#hex","cardBg":"#hex","textColor":"#hex",'
        '"accent":"#hex","palette":["#hex",...],'
        '"header":{"present":true,"title":"...","x":0-100,"y":0-100,"w":0-100,"h":0-100,'
        '"background":"#hex","textColor":"#hex"},'
        '"pages":[{"name":"...","widgets":[{"index":1,"family":"kpi|bar|line|pie|donut|area|'
        'table|gauge|treemap|funnel|scatter|heatmap|map|slicer|text","title":"...",'
        '"content":"for text widgets: the literal text shown",'
        '"x":0-100,"y":0-100,"w":0-100,"h":0-100}]}],'
        '"buildPrompt":"one paragraph describing the exact visual design so an engineer '
        'can reimplement it in React: arrangement of the header, cards and charts, colors, '
        'typography feel, and any standout visual details"}\n'
        "Rules: list every visible widget and its exact title text. For EVERY widget give its "
        "bounding box as percentages of the canvas: x,y = top-left corner, w,h = width/height "
        "(e.g. a KPI card in the top-left quarter is x:2,y:8,w:24,h:14). Be precise about "
        "positions and sizes — the layout must be rebuilt EXACTLY as shown, including how "
        "many chart columns/rows there are. If the image has a title/header band at the top, "
        "fill the header object with its box and its title text; set present:false when there "
        "is no header band. cardBg is the card/panel surface color (white cards on a grey page "
        '→ cardBg "#ffffff"); textColor is the main text color on cards. Extract the dominant '
        "accent color and the full chart palette as hex. Note the background color and "
        "whether the theme is light or dark. Do not invent widgets that are not visible."
    )
    user = (
        "Here is a dashboard screenshot. Describe it exactly as a JSON spec per the schema. "
        "After the JSON, on a new line, write a one-paragraph natural-language design brief "
        "describing the layout, colors, and widget mix."
    )
    call_kwargs = {"stream": False, "options": {"max_tokens": 8192, "temperature": 0.1}}
    if model:
        call_kwargs["model"] = model
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user, images=[_image_data_uri(raw, mime)]),
    ]

    content = None
    spec = None
    for _attempt in range(2):
        provider = opencode_provider_from_settings()
        try:
            resp = await provider.chat(messages, **call_kwargs)
        finally:
            await provider.close()
        content = getattr(getattr(resp, "message", None), "content", None) or str(resp)
        spec = _parse_spec_json(content)
        if spec is not None:
            break
    if not spec:
        try:
            _raw_dump = _os.path.join(_tempfile.gettempdir(), "vision_raw.txt")
            with open(_raw_dump, "w", encoding="utf-8") as _f:
                _f.write(str(content)[:20000])
        except Exception:
            pass
        raise ValueError("analysis_failed")
    return {
        "spec": spec,
        "prompt": _extract_brief(content, spec),
        "buildPrompt": _extract_build_prompt(content, spec),
        "looksLikeDashboard": _count_widgets(spec) >= 2,
    }


async def _select_plan_for_image(plan: dict, spec: dict, title: str) -> dict:
    """big-pickle chooses which validated zones/KPIs to keep so the rebuilt
    dashboard matches the screenshot's shape.

    Only a subset of already-SQL-validated widgets is ever returned, and chart
    swaps are restricted to families that render from the same query — so the
    result is always guaranteed to render.
    """
    from agent.llm_client import Message, opencode_provider_from_settings
    from api.main import _parse_llm_json, _safe_llm_titles

    zones = plan.get("zones") or []
    kpis = plan.get("kpis") or []
    zone_index = [
        {"idx": i, "chartType": z.get("chartType"), "zone": z.get("zone"),
         "metric": z.get("metric"), "x": z.get("x"), "y": z.get("y"),
         "title": z.get("title")}
        for i, z in enumerate(zones)
    ]
    kpi_index = [
        {"idx": i, "label": k.get("label"), "variant": k.get("variant"),
         "column": k.get("column")}
        for i, k in enumerate(kpis)
    ]
    system = (
        "You are a dashboard designer matching a reference screenshot to a validated widget "
        "plan. The plan's widgets are all pre-validated against real data; you may only "
        "SELECT a subset and rename titles. Return ONLY JSON:\n"
        '{"keep_zones":[int...],"keep_kpis":[int...],"titles":{"zoneIdx":"new title"},'
        '"chartSwaps":{"zoneIdx":"bar|line|area|donut|pie|column"},"narrative":"1-2 sentences"}'
    )
    user = json.dumps({
        "image_spec": spec,
        "validated_zones": zone_index,
        "validated_kpis": kpi_index,
        "user_title": title,
    })
    provider = opencode_provider_from_settings()
    try:
        resp = await provider.chat(
            [Message(role="system", content=system), Message(role="user", content=user)],
            stream=False,
            options={"max_tokens": 4096, "temperature": 0.1},
        )
    finally:
        await provider.close()
    content = getattr(getattr(resp, "message", None), "content", None) or str(resp)
    refined = _parse_llm_json(content)
    if not refined:
        return plan

    # Titles first (they reference the ORIGINAL zone indices), then filter.
    titles = refined.get("titles")
    if isinstance(titles, dict):
        plan["zones"] = _safe_llm_titles(plan["zones"], titles)

    keep_zones = refined.get("keep_zones")
    if isinstance(keep_zones, list) and keep_zones:
        keep = set()
        for i in keep_zones:
            try:
                keep.add(int(i))
            except Exception:
                pass
        if keep:
            plan["zones"] = [z for i, z in enumerate(zones) if i in keep]

    keep_kpis = refined.get("keep_kpis")
    if isinstance(keep_kpis, list) and keep_kpis:
        keep = set()
        for i in keep_kpis:
            try:
                keep.add(int(i))
            except Exception:
                pass
        if keep:
            plan["kpis"] = [k for i, k in enumerate(kpis) if i in keep]

    swaps = refined.get("chartSwaps")
    if isinstance(swaps, dict):
        for i, ct in swaps.items():
            try:
                idx = int(i)
            except Exception:
                continue
            if 0 <= idx < len(plan["zones"]) and str(ct).lower() in _SWAP_SAFE_CHARTS:
                plan["zones"][idx]["chartType"] = str(ct).lower()
    return plan


# ── Image-spec-driven plan builder ──────────────────────────────────────────
# The screenshot's widget list is the source of truth: each vision widget is
# mapped to a real column (fuzzy title match, plus derived ratios like CTR/CPM/
# CPC) and its SQL is validated against the table before inclusion. The image
# shapes the dashboard — titles, counts and layout — but only data-backed
# widgets survive. This is what makes the rebuilt dashboard actually *look like*
# the reference instead of a generic auto-build.

_DIM_SYNONYMS = {
    "source": ["channel", "source", "medium", "platform", "traffic"],
    "ad type": ["ad_type", "adtype", "ad", "creative", "ad_format"],
    "campaign type": ["campaign_type", "type", "campaign_type_name"],
    "campaign name": ["campaign_name", "campaign"],
    "status": ["status", "state"],
    "region": ["region", "country", "city", "state", "area"],
}

_DERIVED_RATIOS = [
    ("ctr", ["click"], ["impression"], 100.0),
    ("cpm", ["spend", "cost", "amount"], ["impression"], 1000.0),
    ("cpc", ["spend", "cost", "amount"], ["click"], 1.0),
    ("conversion rate", ["conversion"], ["click"], 100.0),
    ("roi", ["revenue", "income"], ["spend", "cost", "amount"], 100.0),
    ("margin", ["profit", "revenue"], ["revenue"], 100.0),
]

_REGION_RANK = {
    "header": 0, "top": 1, "top-left": 2, "top-right": 3, "left": 4,
    "center": 5, "middle": 5, "right": 6, "bottom-left": 7, "bottom": 8,
    "bottom-right": 9, "footer": 10, "full": 11,
}


def _region_rank(region):
    return _REGION_RANK.get(str(region or "").lower(), 5)


def _size_from_spec(size):
    s = str(size or "").lower()
    if any(k in s for k in ("xlarge", "full", "wide")):
        return (12, 4)
    if "large" in s:
        return (6, 4)
    if "small" in s:
        return (3, 3)
    return (4, 4)


def _spec_widgets(spec):
    """Flatten the vision spec's widgets (pages → widgets).

    A flat ``widgets`` list is normalized into a single page by
    ``_coerce_spec`` before any builder runs, so a spec with only a
    top-level ``widgets`` key is not a valid spec and yields nothing.
    """
    if not isinstance(spec, dict):
        return []
    widgets = []
    pages = spec.get("pages")
    if isinstance(pages, list):
        for p in pages:
            ws = p.get("widgets") if isinstance(p, dict) else None
            if isinstance(ws, list):
                widgets.extend(ws)
    return widgets


def _match_col(title, cols):
    """Fuzzy-match a widget title to a column name (best-effort)."""
    if not cols:
        return None
    tl = str(title or "").lower()
    tokens = [t for t in re.findall(r"[a-z0-9]+", tl) if len(t) >= 3]
    best, best_score = None, 0
    for c in cols:
        cl = str(c).lower()
        score = 0
        if cl and cl in tl:
            score = 100 + len(cl)
        else:
            for t in tokens:
                if t in cl:
                    score = max(score, 30 + len(t))
                elif cl in t:
                    score = max(score, 20 + len(cl))
        if score > best_score:
            best_score, best = score, c
    return best if best_score >= 20 else None


def _match_dim(title, cat):
    """Match a widget title to a categorical column. The synonym map is
    authoritative: when a known label (Source, Ad type, Campaign name, ...) is
    present, only columns matching its terms qualify — so "Campaign type" never
    fuzzy-guesses its way onto campaign_name. Falls back to fuzzy matching only
    for labels the synonym map doesn't know."""
    tl = str(title or "").lower()
    for key, terms in _DIM_SYNONYMS.items():
        if key in tl:
            best, best_score = None, 0
            for c in cat:
                cl = str(c).lower()
                for t in terms:
                    if t in cl:
                        score = len(t)
                        if score > best_score:
                            best_score, best = score, c
            return best
    return _match_col(title, cat)


def _find_by_term(term, cols):
    for c in cols:
        if term in str(c).lower():
            return c
    return None


def _derived_kpi(title, measures, table):
    """Derive a common ratio metric (CTR/CPM/CPC/...) from two measures."""
    from api.main import _qcol, _validate_sql_single

    tl = str(title or "").lower()
    for pattern, num_terms, den_terms, factor in _DERIVED_RATIOS:
        if pattern not in tl:
            continue
        num = den = None
        for t in num_terms:
            num = _find_by_term(t, measures)
            if num:
                break
        for t in den_terms:
            den = _find_by_term(t, measures)
            if den:
                break
        if not num or not den or num == den:
            continue
        expr = f"SUM({_qcol(num)})/NULLIF(SUM({_qcol(den)}),0)*{factor}"
        sql = f"SELECT {expr} AS value FROM {_qcol(table)}"
        if not _validate_sql_single(sql):
            continue
        return {
            "label": str(title).strip(),
            "variant": "kpi",
            "agg": "SUM",
            "column": f"{num}/{den}",
            "expression": expr,
            "spark": False,
            "sql": sql,
        }
    return None


_RATE_COL_RE = re.compile(
    r"margin|profit.?margin|pct|percent|%|rate|ratio|score|utiliz|discount|"
    r"conversion|ctr|cpm|cpc|cpa|roas|roi|arpu|aov|win.?rate|bounce"
)


def _rate_kpi_agg(col_name) -> str:
    """Rate/ratio columns aggregate by AVG; absolute measures by SUM."""
    return "AVG" if _RATE_COL_RE.search(str(col_name or "").lower()) else "SUM"


def _pick_pbix_column(widget, cols, role="dimension"):
    """Bind a PBIX widget to a real table column.

    Priority: (1) a projected field whose parsed column exists in the table
    (exact, case-insensitive) — for measures prefer a field with an aggregation,
    for dimensions prefer role Category/Y-axis-groupers; (2) fuzzy title match
    via _match_col / _match_dim. Returns the column name or None.
    """
    fields = (widget or {}).get("fields") or []
    want_agg = role == "measure"
    best_field, best_field_score = None, 0
    for f in fields:
        col, agg = _pbix_spec._field_parts(f.get("ref"))
        if not col:
            continue
        cl = col.lower()
        match = next((c for c in cols if str(c).lower() == cl), None)
        if not match:
            continue
        score = 1
        if want_agg and agg:
            score += 2
        if not want_agg and f.get("role") in ("Category", "Rows", "Axis", "Series", "Legend", "Values"):
            score += 2
        if score > best_field_score:
            best_field, best_field_score = match, score
    if best_field:
        return best_field

    title = str((widget or {}).get("title") or "")
    if role == "dimension":
        return _match_dim(title, cols)
    return _pbix_measure_from_title(title, cols) or _match_col(title, cols)


# Measure synonyms for title fallback: PBIX titles say "Revenue" where the
# warehouse column is "amount" / "sales", etc. Applied only to the measure
# side of an "X by Y" title.
_MEASURE_SYNONYMS = {
    "revenue": ["amount", "sales", "total", "price"],
    "sales": ["amount", "revenue", "total"],
    "profit": ["margin", "income", "amount"],
    "cost": ["spend", "expense", "amount"],
    "spend": ["cost", "expense", "amount"],
    "quantity": ["qty", "units", "count"],
    "orders": ["count", "order_id"],
}


def _pbix_measure_from_title(title, cols):
    """Measure from a PBIX title using the "X by Y" convention.

    In "Amount by region", X is the measure and Y the dimension — so fuzzy
    matching the FULL title ties dimension and measure. Match the head (plus
    known synonyms) first; None when nothing matches.
    """
    tl = str(title or "").lower().strip()
    head = re.split(r"\s+by\s+|\s+per\s+|\s+vs\.?\s+", tl, maxsplit=1)[0].strip()
    if not head:
        return None
    candidates = [head] + _MEASURE_SYNONYMS.get(head, [])
    for cand in candidates:
        match = next((c for c in cols if str(c).lower() == cand), None)
        if match:
            return match
    # Substring match on the head only (e.g. "total revenue" → "total_revenue").
    tokens = [t for t in re.findall(r"[a-z0-9]+", head) if len(t) >= 3]
    best, best_score = None, 0
    for c in cols:
        cl = str(c).lower()
        for t in tokens:
            if t in cl and len(t) > best_score:
                best, best_score = c, len(t)
    return best


def _pbix_field_agg(widget, cols):
    """Aggregation declared by the widget's projected measure field, if any."""
    for f in (widget or {}).get("fields") or []:
        col, agg = _pbix_spec._field_parts(f.get("ref"))
        if not col or not agg:
            continue
        if any(str(c).lower() == col.lower() for c in cols):
            return agg
    return None


def _box_from_spec(w, cw, ch):
    """Convert a widget's normalized 0-100 box into canvas pixels."""
    x = float(w.get("x") or 0) / 100.0
    y = float(w.get("y") or 0) / 100.0
    ww = float(w.get("w") or 30) / 100.0
    hh = float(w.get("h") or 30) / 100.0
    return {
        "x": int(round(x * cw)),
        "y": int(round(y * ch)),
        "w": int(round(ww * cw)),
        "h": int(round(hh * ch)),
    }


def _repair_layout(items, cw, ch, min_w=60, min_h=40):
    """Light repair pass over the vision model's exact boxes.

    The boxes are the source of truth — this only fixes small vision
    imprecision: clamp inside the canvas, enforce minimum sizes, align edges
    that are within 1.5% of each other, and nudge apart boxes that overlap
    more than half of the smaller one. No canonical template is imposed.
    """
    tol = 1.5  # percent of canvas
    boxes = [it["box"] for it in items if isinstance(it.get("box"), dict)]
    if not boxes:
        return

    # 1. Clamp + minimum size.
    for b in boxes:
        b["w"] = max(min_w, min(b.get("w", min_w), cw))
        b["h"] = max(min_h, min(b.get("h", min_h), ch))
        b["x"] = max(0, min(b.get("x", 0), cw - b["w"]))
        b["y"] = max(0, min(b.get("y", 0), ch - b["h"]))

    def _snap_group(vals, tol_px):
        """Group sorted values within tol_px and set each group to its mean."""
        out = vals[:]
        i = 0
        while i < len(out):
            j = i
            while j + 1 < len(out) and out[j + 1] - out[j] <= tol_px:
                j += 1
            if j > i:
                mean = sum(out[i:j + 1]) / (j - i + 1)
                for k in range(i, j + 1):
                    out[k] = mean
            i = j + 1
        return out

    # 2. Edge-snap: align lefts/tops that are close. Rights/bottoms are only
    # snapped for boxes whose start edge is NOT already aligned with another
    # box — shifting those would break the (visually dominant) column/row
    # alignment the start pass just established.
    for axis, size_key, span in (("x", "w", cw), ("y", "h", ch)):
        tol_px = span * tol / 100.0
        order = sorted(range(len(boxes)), key=lambda i: boxes[i][axis])
        starts = _snap_group([boxes[i][axis] for i in order], tol_px)
        for i, v in zip(order, starts):
            boxes[i][axis] = v
        # Which boxes share their (snapped) start edge with another box?
        start_vals = [boxes[i][axis] for i in range(len(boxes))]
        pinned = []
        for i in range(len(boxes)):
            pinned.append(any(j != i and abs(start_vals[i] - start_vals[j]) <= tol_px
                               for j in range(len(boxes))))
        # Right/bottom edges: snap x+w / y+h, then shift x/y to keep the size.
        order2 = [i for i in sorted(range(len(boxes)),
                                    key=lambda i: boxes[i][axis] + boxes[i][size_key])
                  if not pinned[i]]
        edges = _snap_group([boxes[i][axis] + boxes[i][size_key] for i in order2], tol_px)
        for i, v in zip(order2, edges):
            boxes[i][axis] = v - boxes[i][size_key]

    # 3. De-overlap: only when overlap covers >50% of the smaller box.
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, c = boxes[i], boxes[j]
            ox = min(a["x"] + a["w"], c["x"] + c["w"]) - max(a["x"], c["x"])
            oy = min(a["y"] + a["h"], c["y"] + c["h"]) - max(a["y"], c["y"])
            if ox <= 0 or oy <= 0:
                continue
            smaller = min(a["w"] * a["h"], c["w"] * c["h"])
            if ox * oy > 0.5 * smaller:
                # Nudge the later box to the right of (or below) the earlier one.
                if c["x"] + a["w"] + 12 <= cw and c["x"] + c["w"] + 12 <= cw + a["w"]:
                    c["x"] = a["x"] + a["w"] + 12
                elif c["y"] + c["h"] + 12 <= ch:
                    c["y"] = a["y"] + a["h"] + 12
                c["x"] = max(0, min(c["x"], cw - c["w"]))
                c["y"] = max(0, min(c["y"], ch - c["h"]))


def _build_plan_from_spec(df, table, profile, spec, title="", canvas=None):
    """Build a validated plan that mirrors the screenshot's widget list.

    Each vision widget maps to a real column (fuzzy title match, derived ratios)
    and its SQL is validated against the table before inclusion. Widgets carry
    the image's exact bounding boxes (canvas px) so the renderer can reproduce
    the screenshot's layout. Widgets that cannot be matched to data become
    sized placeholders so the layout still matches the image. Returns None only
    when the spec detected no widgets at all.
    """
    from api.main import (
        _breakdown_sql, _gauge_sql, _heat_sql, _plan_metric_name, _qcol,
        _trend_sql, _validate_sql_single,
    )

    cw = int((canvas or {}).get("w") or 1900)
    ch = int((canvas or {}).get("h") or 1080)

    cols = profile["columns"]
    measures = [c for c, i in cols.items() if i.get("is_measure")]
    dates = [c for c, i in cols.items() if i.get("is_date") and (i.get("range_days") or 0) >= 0]
    cat = [c for c, i in cols.items() if i.get("is_categorical")]
    geo = [c for c, i in cols.items() if i.get("is_geo")]
    # Slicers can filter on any non-measure, non-date, non-ID column — including
    # high-cardinality names (campaign_name) that make poor chart axes.
    dim_all = [c for c, i in cols.items()
               if not i.get("is_measure") and not i.get("is_date") and not i.get("is_id")]
    date_col = ""
    for c in sorted(dates, key=lambda x: -(cols[x].get("range_days") or 0)):
        if (cols[c].get("range_days") or 0) >= 1:
            date_col = c
            break
    if not date_col and dates:
        date_col = dates[0]

    widgets = _spec_widgets(spec)
    if not widgets:
        return None
    widgets = sorted(
        widgets,
        key=lambda w: (_region_rank(w.get("region") if isinstance(w, dict) else ""),
                       int(w.get("index") or 0) if isinstance(w, dict) else 0),
    )

    kpis, zones, filters, tables, texts, placeholders = [], [], [], [], [], []
    used_dims = set()

    def add_zone(zone, chart_type, wtitle, x, y, sql, size, box, widx=None):
        if not x or not sql:
            return False
        if not _validate_sql_single(sql):
            return False
        zones.append({
            "zone": zone, "chartType": chart_type,
            "title": str(wtitle).strip() or _plan_metric_name(y or x),
            "metric": _plan_metric_name(y or x),
            "x": x, "y": y or "", "sql": sql,
            "cols": size[0], "rows": size[1], "config": {},
            "box": box, "widx": widx,
        })
        return True

    def add_placeholder(family, wtitle, box, widx=None, note=""):
        placeholders.append({
            "family": str(family or "chart").lower(),
            "title": str(wtitle or "Widget").strip() or "Widget",
            "box": box, "widx": widx, "note": note or "",
        })

    for widx, w in enumerate(widgets):
        if not isinstance(w, dict):
            continue
        family = str(w.get("family") or "").lower()
        wtitle = str(w.get("title") or "").strip()
        size = _size_from_spec(w.get("size"))
        box = _box_from_spec(w, cw, ch)
        # PBIX widgets carry projected data fields (from singleVisual.projections).
        # When present they are the authoritative binding — fuzzy title matching
        # is only the fallback. Image-flow widgets have no fields and keep the
        # legacy path untouched.
        wfields = w.get("fields") if isinstance(w.get("fields"), list) else []
        pbix_measure = _pick_pbix_column(w, measures, role="measure") if wfields else None
        pbix_dim = _pick_pbix_column(w, cat, role="dimension") if wfields else None
        pbix_agg = _pbix_field_agg(w, measures) if wfields else None

        if family == "kpi":
            m = pbix_measure or _match_col(wtitle, measures)
            if m:
                agg = pbix_agg or _rate_kpi_agg(m)
                expr = (f"COUNT(DISTINCT {_qcol(m)})" if agg == "COUNT DISTINCT"
                        else f"{agg}({_qcol(m)})")
                sql = f"SELECT {expr} AS value FROM {_qcol(table)}"
                if _validate_sql_single(sql):
                    kpi = {
                        "label": wtitle or _plan_metric_name(m),
                        "variant": "kpi", "agg": agg, "column": m,
                        "expression": expr,
                        "spark": bool(date_col), "sql": sql,
                        "box": box, "widx": widx,
                    }
                    if date_col:
                        if agg == "AVG":
                            fmt = "%Y-%m"
                            kpi["sparkSql"] = (
                                f"SELECT strftime(CAST({_qcol(date_col)} AS TIMESTAMP), "
                                f"'{fmt}') AS label, AVG({_qcol(m)}) AS value "
                                f"FROM {_qcol(table)} GROUP BY 1 ORDER BY 1"
                            )
                        else:
                            kpi["sparkSql"] = _trend_sql(table, date_col, m, "month")
                    kpis.append(kpi)
                else:
                    add_placeholder(family, wtitle, box, widx)
            else:
                d = _derived_kpi(wtitle, measures, table)
                if d:
                    d["box"] = box
                    d["widx"] = widx
                    kpis.append(d)
                else:
                    add_placeholder(family, wtitle, box, widx)
        elif family in ("line", "area"):
            m = pbix_measure or _match_col(wtitle, measures)
            if not m:
                add_placeholder(family, wtitle, box, widx)
                continue
            if date_col:
                if not add_zone("trend", family, wtitle, date_col, m,
                                _trend_sql(table, date_col, m, "month"), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                d = _match_dim(wtitle, cat)
                if d:
                    if not add_zone("trend", family, wtitle, d, m,
                                    _breakdown_sql(table, d, m, 12), size, box, widx):
                        add_placeholder(family, wtitle, box, widx)
                else:
                    add_placeholder(family, wtitle, box, widx)
        elif family in ("bar", "column"):
            m = pbix_measure or _match_col(wtitle, measures)
            d = pbix_dim or _match_dim(wtitle, cat)
            if d and m:
                if not add_zone("comparison", family, wtitle, d, m,
                               _breakdown_sql(table, d, m, 12), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            elif m and date_col:
                if not add_zone("trend", family, wtitle, date_col, m,
                                _trend_sql(table, date_col, m, "month"), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            elif m:
                d = next((c for c in cat if c not in used_dims), None)
                if d:
                    used_dims.add(d)
                    if not add_zone("comparison", family, wtitle, d, m,
                                    _breakdown_sql(table, d, m, 12), size, box, widx):
                        add_placeholder(family, wtitle, box, widx)
                else:
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family in ("donut", "pie"):
            m = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            d = pbix_dim or _match_dim(wtitle, cat)
            if d and m:
                if not add_zone("breakdown", family, wtitle, d, m,
                               _breakdown_sql(table, d, m, 8), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "gauge":
            m = pbix_measure or _match_col(wtitle, measures)
            if m:
                if not add_zone("breakdown", "gauge", wtitle, m, m,
                                _gauge_sql(table, m), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "funnel":
            m = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            d = pbix_dim or _match_dim(wtitle, cat)
            if d and m:
                if not add_zone("special", "funnel", wtitle, d, m,
                                _breakdown_sql(table, d, m, 8), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "scatter":
            # Scatter needs two measures (x, y) — use the two best matches.
            m1 = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            m2 = next((c for c in measures if c != m1), None)
            if m1 and m2:
                sql = (f"SELECT {_qcol(m1)} AS x, {_qcol(m2)} AS y FROM {_qcol(table)} "
                       f"WHERE {_qcol(m1)} IS NOT NULL AND {_qcol(m2)} IS NOT NULL LIMIT 200")
                if not add_zone("correlation", "scatter", wtitle, m1, m2, sql, size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "radar":
            # Radar/spider charts render natively in the frontend — no new
            # zone contract needed: aiChartBuild._planWidgetType maps
            # chartType "radar" to a Chart.js radar widget (charts.js radar(),
            # dashboardRenderer "radar" path, blankdash default radar SQL),
            # and the blankdash data loader pivots every extra result column
            # into its own dataset — so label + N measures draws a
            # multi-axis spider. Zone "special" matches the funnel-style
            # advanced visuals in the frontend priority order. Axes are
            # capped (8 categories like donut/funnel, 6 measures) to keep
            # the spider readable.
            d = pbix_dim or _match_dim(wtitle, cat)
            m1 = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            if d and m1:
                mcols = [m1] + [c for c in measures if c != m1][:5]
                selects = ", ".join(f"SUM({_qcol(m)}) AS {_qcol(m)}" for m in mcols)
                sql = (f"SELECT {_qcol(d)} AS label, {selects} FROM {_qcol(table)} "
                       f"GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
                if not add_zone("special", "radar", wtitle, d, m1, sql, size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "map":
            g = geo[0] if geo else None
            m = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            if g and m:
                sql = (f"SELECT {_qcol(g)} AS label, SUM({_qcol(m)}) AS value "
                       f"FROM {_qcol(table)} GROUP BY label ORDER BY value DESC LIMIT 12")
                if not add_zone("geo", "countrymap", wtitle, g, m, sql, size, box, widx):
                    add_placeholder(family, wtitle, box, widx, "no geo column")
            else:
                add_placeholder(family, wtitle, box, widx, "no geo column")
        elif family == "table":
            sql = f"SELECT * FROM {_qcol(table)} LIMIT 20"
            if _validate_sql_single(sql):
                tables.append({
                    "title": wtitle or "Data Table", "sql": sql,
                    "box": box, "widx": widx,
                })
            else:
                add_placeholder(family, wtitle, box, widx)
        elif family == "text":
            texts.append({
                "title": wtitle or "Text",
                "content": str(w.get("content") or wtitle or "").strip(),
                "box": box, "widx": widx,
            })
        elif family == "slicer":
            d = ((_pick_pbix_column(w, dim_all, role="dimension") if wfields else None)
                 or _match_dim(wtitle, dim_all))
            if d and d not in used_dims:
                filters.append({
                    "field": d, "type": "multi", "label": wtitle or _plan_metric_name(d),
                    "sql": f"SELECT DISTINCT {_qcol(d)} AS label FROM {_qcol(table)} ORDER BY label",
                    "box": box, "widx": widx,
                })
                used_dims.add(d)
            elif date_col and "date" in wtitle.lower():
                filters.append({
                    "field": date_col, "type": "daterange", "label": wtitle or _plan_metric_name(date_col),
                    "sql": f"SELECT MIN({_qcol(date_col)}) AS min_val, MAX({_qcol(date_col)}) AS max_val FROM {_qcol(table)}",
                    "box": box, "widx": widx,
                })
            else:
                add_placeholder(family, wtitle, box, widx, "no matching column")
        elif family == "heatmap":
            # Heatmap needs two categorical dims + a measure (label, category,
            # value) — the same contract blankdash.js renders. Falls back to a
            # sized placeholder when the table has fewer than two dimensions.
            m = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            d1 = pbix_dim or _match_dim(wtitle, cat)
            d2 = next((c for c in cat if c != d1), None)
            if d1 and d2:
                if not add_zone("special", "heatmap", wtitle, d1, d2,
                                _heat_sql(table, d1, d2, m or ""), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx, "needs two dimensions")
        elif family == "treemap":
            # Treemap is a hierarchical breakdown (label/value) — same SQL as
            # donut/bar breakdowns, rendered by the frontend treemap path.
            m = pbix_measure or _match_col(wtitle, measures) or (measures[0] if measures else None)
            d = pbix_dim or _match_dim(wtitle, cat)
            if d and m:
                if not add_zone("breakdown", "treemap", wtitle, d, m,
                                _breakdown_sql(table, d, m, 12), size, box, widx):
                    add_placeholder(family, wtitle, box, widx)
            else:
                add_placeholder(family, wtitle, box, widx)
        else:
            # Unknown family: keep a sized placeholder.
            add_placeholder(family, wtitle, box, widx)

    if not (kpis or zones or filters or tables or texts or placeholders):
        return None

    # Light repair over the exact boxes (clamp, edge-snap, de-overlap) — the
    # vision model's layout is preserved, only small imprecision is fixed.
    _repair_layout(kpis + zones + filters + tables + texts + placeholders, cw, ch)

    metric = measures[0] if measures else ""
    return {
        "table": table,
        "row_count": profile.get("row_count", len(df)),
        "canvas": {"w": cw, "h": ch},
        "kpis": kpis,
        "filters": filters,
        "zones": zones,
        "tables": tables,
        "texts": texts,
        "placeholders": placeholders,
        "date_col": date_col,
        "metric": metric,
        "profile": {c: {k: info.get(k) for k in ("is_date", "is_numeric", "is_categorical",
                     "is_measure", "is_geo", "is_id", "nunique", "cardinality",
                     "range_days", "zero_pct") if info.get(k) is not None}
                    for c, info in cols.items()},
    }


@router.post("/analyze-image")
async def analyze_image(
    image: UploadFile = File(...),
    _: None = Depends(require_feature(FEATURE_RECREATE_FROM_IMAGE)),
):
    """Analyze a dashboard screenshot and return a structured design spec.

    Uses the free-tier vision model (mimo-v2.6-flash-free). Returns the spec JSON, a
    natural-language brief, the image SHA-256 (for staleness checks) and a
    ``looksLikeDashboard`` hint.
    """
    raw = await image.read()
    if len(raw) > _MAX_IMAGE_BYTES:
        return {"success": False, "error_code": "image_too_large",
                "message": "Image exceeds 10MB — upload a smaller screenshot."}
    mime = _detect_image_mime(raw)
    if not mime:
        return {"success": False, "error_code": "invalid_image",
                "message": "Upload a PNG, JPEG or WebP image."}
    try:
        result = await _analyze_image(raw, mime, model=_CODE_VISION_MODEL)
    except ValueError:
        return {"success": False, "error_code": "analysis_failed",
                "message": "Could not read a dashboard from this image. Try a clearer screenshot."}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"analyze-image vision call failed: {e}")
        return {"success": False, "error_code": "vision_unavailable",
                "message": "The vision model could not be reached right now. Try again or use AI Auto-Build."}
    return {
        "success": True,
        "spec": result["spec"],
        "prompt": result["prompt"],
        "sha": hashlib.sha256(raw).hexdigest(),
        "looksLikeDashboard": result["looksLikeDashboard"],
    }


@router.post("/build-from-image")
async def build_from_image(
    image: UploadFile = File(...),
    table_name: str = Form(...),
    title: str = Form(""),
    spec_sha: str = Form(""),
    spec_json: str = Form(""),
    _: None = Depends(require_feature(FEATURE_RECREATE_FROM_IMAGE)),
):
    """Build a validated dashboard plan that matches a reference screenshot.

    The screenshot's widget list drives the plan: each vision widget is mapped
    to a real column (fuzzy title match, derived ratios like CTR/CPM/CPC) and
    its SQL is validated against the table, so the rebuilt dashboard mirrors
    the image's titles, counts and layout. When the image-driven plan is too
    thin, it falls back to the deterministic auto-build plan plus a big-pickle
    selection pass. Returns the same plan contract as /dashboard/auto-build-plan
    plus the extracted theme for styling.
    """
    raw = await image.read()
    mime = _detect_image_mime(raw)
    if not mime:
        return {"success": False, "error_code": "invalid_image",
                "message": "Upload a PNG, JPEG or WebP image."}
    sha = hashlib.sha256(raw).hexdigest()
    if spec_sha and spec_sha != sha:
        return {"success": False, "error_code": "image_changed",
                "message": "The image changed since it was analyzed. Please re-analyze it."}

    spec = None
    if spec_json:
        try:
            spec = json.loads(spec_json)
        except Exception:
            spec = None
    if not spec:
        try:
            result = await _analyze_image(raw, mime, model=_CODE_VISION_MODEL)
            spec = result["spec"]
        except ValueError:
            return {"success": False, "error_code": "analysis_failed",
                    "message": "Could not read a dashboard from this image. Try a clearer screenshot."}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"build-from-image vision call failed: {e}")
            return {"success": False, "error_code": "vision_unavailable",
                    "message": "The vision model could not be reached right now. Try again or use AI Auto-Build."}

    from api.main import _build_auto_plan, _build_column_profile, _qcol
    import duckdb

    from config import settings

    if not _app_state.loader or not _app_state.loader.table_exists(table_name):
        return {"success": False, "error": f"Table '{table_name}' not found"}
    try:
        with duckdb.connect(str(settings.duckdb.database_path)) as conn:
            conn.execute(f"SET search_path = '{tenant_search_path()}'")
            df = conn.execute(f"SELECT * FROM {_qcol(table_name)}").df()
    except Exception as e:  # noqa: BLE001
        logger.error(f"build-from-image: failed to load {table_name}: {e}")
        return {"success": False, "error": f"Failed to load table: {e}"}
    if df.empty:
        return {"success": False, "error": "Empty table — no data to analyze"}

    profile = _build_column_profile(df)

    # Canvas mirrors the uploaded image's aspect ratio so widget boxes map 1:1.
    # Small screenshots are scaled up to a standard ~1900px-wide canvas (aspect
    # preserved) so the rebuilt dashboard renders at a normal working size.
    canvas = None
    try:
        from PIL import Image as _PILImage
        import io as _io
        with _PILImage.open(_io.BytesIO(raw)) as im:
            iw, ih = im.size
        if iw and ih:
            scale = max(1.0, 1900.0 / iw)
            canvas = {"w": int(round(iw * scale)), "h": int(round(ih * scale))}
    except Exception:  # noqa: BLE001
        canvas = None

    # Primary path: build the plan FROM the screenshot's widget list. Every
    # widget's SQL is validated against the real data, so the result mirrors the
    # image's titles/counts/layout and is guaranteed to render.
    plan = _build_plan_from_spec(df, table_name, profile, spec, title or "", canvas)

    # Fallback: deterministic auto-build + big-pickle selection pass.
    if plan is None:
        plan = _build_auto_plan(df, table_name, profile, [], title or "")
        try:
            plan = await _select_plan_for_image(plan, spec, title or "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"build-from-image selection pass skipped: {e}")

    # Honor explicit user chart swaps from the edited spec (swap-safe only).
    # Swap keys are SPEC widget indexes — translate to zone indexes via widx
    # so a swap lands on the widget the user saw in the confirm modal.
    swaps = spec.get("chartSwaps") if isinstance(spec, dict) else None
    if isinstance(swaps, dict):
        zones = plan.get("zones") or []
        by_widx = {z.get("widx"): i for i, z in enumerate(zones) if z.get("widx") is not None}
        for i, ct in swaps.items():
            try:
                widx = int(i)
            except Exception:
                continue
            if str(ct).lower() not in _SWAP_SAFE_CHARTS:
                continue
            zidx = by_widx.get(widx, widx if 0 <= widx < len(zones) else None)
            if zidx is not None and 0 <= zidx < len(zones):
                zones[zidx]["chartType"] = str(ct).lower()

    plan["used"] = "ai-image"
    plan["spec"] = {
        "theme": spec.get("theme", "light"),
        "background": spec.get("background", ""),
        "cardBg": spec.get("cardBg", ""),
        "textColor": spec.get("textColor", ""),
        "accent": spec.get("accent", ""),
        "palette": spec.get("palette", []),
        "header": spec.get("header", {"present": False}),
    }
    return {"success": True, "plan": plan}


# ── PBIX import → blankdash replica pipeline ────────────────────────────────
# A .pbix/.pbit is a zip whose Report/Layout carries the report's exact pages,
# visual types, titles and absolute positions. api.pbix_spec parses that
# deterministically into the same spec shape the screenshot flow uses, so the
# proven "Recreate from Image" plan builder can rebuild the report on the
# user's own warehouse data — but with EXACT geometry instead of vision-guessed
# boxes, and without needing an LLM at all.

_PBIX_MIN_CANVAS_W = 1900
_PBIX_MAX_CANVAS_W = 4800


def _pbix_canvas(pw, ph):
    """Map a PBIX page size to a blankdash canvas size in px.

    Mirrors the image flow's canvas rule (scale small pages up so widgets land
    at a normal working size, keep larger pages 1:1) with a sanity band: the
    canvas never drops below ~1900px wide nor balloons past 4800px.
    """
    try:
        w = float(pw)
        h = float(ph)
    except (TypeError, ValueError):
        w = h = 0.0
    if not w or not h or w < 32 or h < 18 or w > 20000 or h > 20000:
        return (1900, 1080)
    scale = max(1.0, _PBIX_MIN_CANVAS_W / w)
    cw = int(round(w * scale))
    ch = int(round(h * scale))
    if cw > _PBIX_MAX_CANVAS_W:
        down = _PBIX_MAX_CANVAS_W / cw
        cw = int(round(cw * down))
        ch = int(round(ch * down))
    return (cw, ch)


def _validate_pbix_spec(spec):
    """Validate/normalize the pbix spec before planning.

    Same surface-color coercion as _coerce_spec (usable hex, theme-consistent
    defaults) plus: at least one page with at least one widget, and a sane
    canvas size. Raises ValueError with a user-facing message when unusable.
    """
    if not isinstance(spec, dict):
        raise ValueError("This .pbix did not produce a readable dashboard spec.")
    pages = spec.get("pages")
    if not isinstance(pages, list):
        pages = []
    pages = [p for p in pages if isinstance(p, dict) and (p.get("widgets") or [])]
    if not pages:
        raise ValueError("No dashboard visuals were found in this .pbix file.")
    spec["pages"] = pages

    dark = str(spec.get("theme") or "light").lower() == "dark"
    spec["background"] = _hex_or(spec.get("background"), "#0f172a" if dark else "#f8fafc")
    spec["cardBg"] = _hex_or(spec.get("cardBg"), "#252540" if dark else "#ffffff")
    spec["textColor"] = _hex_or(spec.get("textColor"), "#e0e0e0" if dark else "#0f172a")
    spec["accent"] = _hex_or(spec.get("accent"), "#818cf8" if dark else "#2563eb")
    pal = spec.get("palette")
    spec["palette"] = [c for c in pal if isinstance(c, str) and _HEX_RE.match(c)][:10] if isinstance(pal, list) else []

    hdr = spec.get("header")
    spec["header"] = hdr if (isinstance(hdr, dict) and hdr.get("present")) else {"present": False}
    if spec["header"].get("present") and not str(spec["header"].get("title") or "").strip():
        spec["header"]["title"] = str(pages[0].get("name") or "Dashboard")[:200]

    cw, ch = _pbix_canvas((spec.get("canvas") or {}).get("w"), (spec.get("canvas") or {}).get("h"))
    spec["canvas"] = {"w": cw, "h": ch}
    return spec


def _page_header(header, page):
    """Per-page header: PBIX pages carry their own display name, which becomes
    that page's header band title (falling back to the spec-level title)."""
    hdr = dict(header) if (isinstance(header, dict) and header.get("present")) else {"present": False}
    if hdr.get("present"):
        hdr["title"] = str(page.get("name") or hdr.get("title") or "Page 1")[:200]
    return hdr


def _build_plans_from_pbix_spec(spec, table_name, df, profile):
    """Build one validated widget plan per PBIX page.

    Each page plans against its OWN canvas so its boxes map 1:1 to the original
    page geometry (no cross-page aspect stretching). Returns a list of plan
    dicts shaped like _build_plan_from_spec output, plus ``name`` (page name),
    ``used`` and ``table``. Pages whose plan comes back empty are dropped.
    """
    header = spec.get("header") or {"present": False}
    plans = []
    for page in spec.get("pages") or []:
        if not (page.get("widgets") or []):
            continue
        cw, ch = _pbix_canvas(page.get("width"), page.get("height"))
        page_spec = {
            "theme": spec.get("theme", "light"),
            "background": spec.get("background", ""),
            "cardBg": spec.get("cardBg", ""),
            "textColor": spec.get("textColor", ""),
            "accent": spec.get("accent", ""),
            "palette": spec.get("palette", []),
            "header": _page_header(header, page),
            "pages": [{"name": page.get("name") or "Page 1", "widgets": page.get("widgets") or []}],
        }
        try:
            plan = _build_plan_from_spec(df, table_name, profile, page_spec, title="", canvas={"w": cw, "h": ch})
        except Exception as e:  # noqa: BLE001 — one bad page must not kill the import
            logger.warning(f"pbix plan failed for page '{page.get('name')}': {e}")
            plan = None
        if not plan:
            continue
        has_content = any(
            isinstance(plan.get(k), list) and plan.get(k)
            for k in ("kpis", "zones", "filters", "tables", "texts", "placeholders")
        )
        if not has_content:
            continue
        plan["name"] = page.get("name") or f"Page {len(plans) + 1}"
        plan["used"] = "pbix"
        plan["table"] = table_name
        plan["canvas"] = {"w": cw, "h": ch}
        plan["spec"] = page_spec
        plans.append(plan)
    return plans


# ── LLM refinement of PBIX plans ─────────────────────────────────────────────
# The deterministic plan binds widgets from their projected fields + titles.
# This pass asks the LLM to re-map any widget whose binding is weak (missing
# dimension/measure) or to confirm the strong ones, then applies ONLY changes
# that survive SQL validation — so a hallucinating model cannot break the
# replica. The LLM call and the SQL validator are injectable for tests.

_REFINE_SYSTEM = (
    "You map Power BI report visuals to a warehouse table's columns so the report "
    "can be rebuilt on that data. You receive each visual's title, its projected "
    "fields (ref/role) and the table's column profile. Return ONLY JSON:\n"
    '{"mappings":[{"page":int,"widx":int,"dimension":"col|null","measure":"col|null",'
    '"agg":"SUM|AVG|COUNT|COUNT DISTINCT|MIN|MAX|null","chartType":"bar|column|line|area|pie|donut|gauge|funnel|scatter|table|null",'
    '"title":"improved title|null"}]}\n'
    "Rules: use ONLY columns from the profile. Choose the dimension from categorical/date "
    "columns and the measure from numeric columns, guided by the visual's fields and "
    "title. For KPI cards pick the measure that matches the label. Keep chartType null "
    "unless the current one is clearly wrong for the chosen data. Never invent columns."
)


def _pbix_widget_index(plan, widx):
    """Find a plan widget by its spec widx across kpis/zones/filters/tables."""
    for key in ("kpis", "zones", "filters", "tables", "texts"):
        for it in plan.get(key) or []:
            if isinstance(it, dict) and it.get("widx") == widx:
                return key, it
    return None, None


def _pbix_spec_widget_fields(plan, widx):
    """Projected fields of the original spec widget with this widx."""
    for page in (plan.get("spec") or {}).get("pages") or []:
        for w in page.get("widgets") or []:
            if isinstance(w, dict) and w.get("index") == widx:
                return w.get("fields") or []
    return []


def _apply_pbix_mapping(plan, mapping, profile, validate_sql):
    """Apply one LLM mapping to a plan widget after validation. Returns True
    when the widget was changed. Unknown columns and chart types are ignored;
    zones whose proposed SQL does not validate keep their current binding."""
    _, widget = _pbix_widget_index(plan, mapping.get("widx"))
    if not widget:
        return False

    profile_cols = (profile or {}).get("columns") or {}
    dims = [c for c, i in profile_cols.items() if i.get("is_categorical")]
    measures = [c for c, i in profile_cols.items() if i.get("is_measure")]
    dates = [c for c, i in profile_cols.items() if i.get("is_date")]
    table = plan.get("table") or ""
    from api.main import _breakdown_sql, _qcol, _trend_sql

    date_col = next(
        (c for c in sorted(dates, key=lambda x: -((profile_cols.get(x) or {}).get("range_days") or 0))
         if (profile_cols.get(c) or {}).get("range_days", 0) >= 1),
        "",
    )

    def _ok(col, pool):
        return isinstance(col, str) and col in pool

    dim = mapping.get("dimension")
    meas = mapping.get("measure")
    agg = str(mapping.get("agg") or "").upper() or None
    ct = str(mapping.get("chartType") or "").lower() or None
    title = str(mapping.get("title") or "").strip() or None

    changed = False
    is_zone = widget in (plan.get("zones") or [])
    is_kpi = widget in (plan.get("kpis") or [])

    if title:
        widget["title"] = title[:120]
        changed = True

    # Chart-type swaps only on zones and only to render-safe families.
    if ct and ct in _SWAP_SAFE_CHARTS and is_zone:
        widget["chartType"] = ct
        changed = True

    if is_kpi:
        if _ok(meas, measures):
            a = agg if agg in ("SUM", "AVG", "COUNT", "COUNT DISTINCT", "MIN", "MAX") \
                else _rate_kpi_agg(meas)
            expr = f"COUNT(DISTINCT {_qcol(meas)})" if a == "COUNT DISTINCT" else f"{a}({_qcol(meas)})"
            sql = f"SELECT {expr} AS value FROM {_qcol(table)}"
            if validate_sql(sql):
                widget["agg"], widget["expression"], widget["sql"] = a, expr, sql
                widget["column"] = meas
                changed = True
        return changed

    if not is_zone:
        return changed

    # Zones: compute the new binding BEFORE mutating, and keep the current one
    # when the proposed SQL does not validate.
    dim_ok = _ok(dim, dims) or _ok(dim, dates)
    meas_ok = _ok(meas, measures)
    if not dim_ok and not meas_ok:
        return changed
    d = dim if dim_ok else widget.get("x")
    m = meas if meas_ok else (widget.get("y") or "")
    if date_col and d == date_col:
        sql = _trend_sql(table, d, m, "month")
    elif d:
        sql = _breakdown_sql(table, d, m, 12)
    else:
        sql = ""
    if sql and not validate_sql(sql):
        return changed
    if dim_ok:
        widget["x"] = dim
        changed = True
    if meas_ok:
        widget["y"] = meas
        changed = True
    if sql:
        widget["sql"] = sql
    return changed


async def _refine_pbix_plans_with_llm(plans, profile, llm_call=None, validate_sql=None):
    """Refine validated PBIX plans with one LLM mapping pass.

    llm_call(system, user) -> str (defaults to the opencode provider);
    validate_sql(sql) -> bool (defaults to _validate_sql_single). Every
    proposed change is validated before being applied; LLM/garbage failures
    leave the plans untouched. Returns the same list for chaining.
    """
    from agent.llm_client import Message, opencode_provider_from_settings
    from api.main import _parse_llm_json, _validate_sql_single

    validate = validate_sql or _validate_sql_single

    index = []
    for pi, plan in enumerate(plans or []):
        for key in ("kpis", "zones", "filters", "tables", "texts"):
            for it in plan.get(key) or []:
                if isinstance(it, dict) and it.get("widx") is not None:
                    index.append({
                        "page": pi, "widx": it["widx"], "kind": key.rstrip("s"),
                        "title": it.get("title") or it.get("label") or "",
                        "x": it.get("x"), "y": it.get("y"),
                        "chartType": it.get("chartType"),
                        "fields": _pbix_spec_widget_fields(plan, it["widx"]),
                    })
    if not index:
        return plans

    profile_cols = (profile or {}).get("columns") or {}
    compact = {
        "columns": [
            {"name": c, "kind": ("date" if i.get("is_date") else
                                 "measure" if i.get("is_measure") else
                                 "categorical" if i.get("is_categorical") else "other")}
            for c, i in profile_cols.items()
        ],
        "widgets": index,
    }
    user = json.dumps(compact)

    async def _default_llm(system, user_text):
        provider = opencode_provider_from_settings()
        try:
            resp = await provider.chat(
                [Message(role="system", content=system), Message(role="user", content=user_text)],
                stream=False, options={"max_tokens": 4096, "temperature": 0.1},
            )
        finally:
            await provider.close()
        return getattr(getattr(resp, "message", None), "content", None) or str(resp)

    call = llm_call or _default_llm
    try:
        result = call(_REFINE_SYSTEM, user)
        if hasattr(result, "__await__"):
            result = await result
        content = result
    except Exception as e:  # noqa: BLE001 — provider down: keep deterministic plan
        logger.warning(f"pbix refine LLM call failed: {e}")
        return plans

    refined = _parse_llm_json(content)
    mappings = refined.get("mappings") if isinstance(refined, dict) else None
    if not isinstance(mappings, list):
        return plans

    for m in mappings:
        if not isinstance(m, dict):
            continue
        try:
            pi = int(m.get("page", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= pi < len(plans):
            try:
                _apply_pbix_mapping(plans[pi], m, profile, validate)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"pbix refine mapping skipped: {e}")

    return plans


def _pbix_theme_payload(spec):
    """Theme contract returned to the frontend (same shape as build-from-image)."""
    return {
        "theme": spec.get("theme", "light"),
        "background": spec.get("background", ""),
        "cardBg": spec.get("cardBg", ""),
        "textColor": spec.get("textColor", ""),
        "accent": spec.get("accent", ""),
        "palette": spec.get("palette", []),
        "header": spec.get("header", {"present": False}),
        "canvas": spec.get("canvas") or {},
        "pages": [
            {"name": p.get("name") or "Page 1",
             "width": p.get("width"), "height": p.get("height"),
             "widgetCount": len(p.get("widgets") or [])}
            for p in (spec.get("pages") or [])
        ],
    }


@router.post("/import-pbix")
async def import_pbix(file: UploadFile = File(...)):
    """Parse an uploaded .pbix/.pbit into a DashboardStudio spec.

    Deterministic (zip + JSON only, no LLM): returns the flat theme contract,
    per-page widget lists with 0-100% boxes, and metadata. The frontend shows a
    confirm modal; the actual build happens in /build-from-pbix.
    """
    fname = file.filename or ""
    if not fname.lower().endswith((".pbix", ".pbit")):
        raise HTTPException(400, "Upload a Power BI (.pbix or .pbit) file.")
    raw = await file.read()
    try:
        from api.pbix_spec import pbix_to_spec
        spec = pbix_to_spec(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"import-pbix parse failed: {e}")
        raise HTTPException(400, "This Power BI file could not be read.")
    try:
        _validate_pbix_spec(spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "success": True,
        "fileName": fname,
        "sha": hashlib.sha256(raw).hexdigest(),
        "spec": spec,
    }


@router.post("/build-from-pbix")
async def build_from_pbix(
    table_name: str = Form(...),
    title: str = Form(""),
    spec_json: str = Form(""),
    ai: bool = Form(True),
    _: None = Depends(require_feature(FEATURE_RECREATE_FROM_IMAGE)),
):
    """Build a validated dashboard plan per page from a parsed PBIX spec.

    Loads the selected warehouse table, then runs the same SQL-validated widget
    mapping used by the screenshot flow over each PBIX page — exact boxes,
    real columns, guaranteed-renderable widgets. Placeholders keep unmatched
    visuals so the replica keeps the original layout.
    """
    try:
        spec = json.loads(spec_json or "")
    except Exception:  # noqa: BLE001
        spec = None
    try:
        spec = _validate_pbix_spec(spec if isinstance(spec, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e))

    from api.main import _build_column_profile, _qcol
    import duckdb

    from config import settings

    if not _app_state.loader or not _app_state.loader.table_exists(table_name):
        raise HTTPException(404, f"Table '{table_name}' not found")
    try:
        with duckdb.connect(str(settings.duckdb.database_path)) as conn:
            conn.execute(f"SET search_path = '{tenant_search_path()}'")
            df = conn.execute(f"SELECT * FROM {_qcol(table_name)}").df()
    except Exception as e:  # noqa: BLE001
        logger.error(f"build-from-pbix: failed to load {table_name}: {e}")
        raise HTTPException(400, "Failed to load the selected table.")
    if df.empty:
        raise HTTPException(400, "Empty table — no data to analyze")

    profile = _build_column_profile(df)
    plans = _build_plans_from_pbix_spec(spec, table_name, df, profile)
    if ai and plans:
        try:
            plans = await _refine_pbix_plans_with_llm(plans, profile)
        except Exception as e:  # noqa: BLE001 — refinement is best-effort
            logger.warning(f"build-from-pbix: AI refinement skipped: {e}")
    if not plans:
        raise HTTPException(400, "No widgets in this report could be matched to the selected table's data. Try a richer table.")
    return {
        "success": True,
        "title": title or (spec.get("header") or {}).get("title") or "Imported PBIX",
        "theme": _pbix_theme_payload(spec),
        "plans": plans,
    }