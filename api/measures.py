"""
Measures Registry + Date Calendar — the persistent "calculation layer".

Mirrors the Power-BI ``Calculations`` table pattern:

* ``analytics.measures`` — named, validated, reusable measure definitions
  (name / source table / SQL aggregate expression / format string / optional
  date column). Widgets, AI auto-builds and templates can resolve their KPIs
  against registered measures instead of re-inventing per-widget SQL.
* ``analytics.calendar`` — a full date dimension (2000-01-01..2044-12-31)
  with year / quarter / month / week / weekday hierarchy + date-truncated
  period starts, enabling canned time intelligence (YTD / MTD / QTD / MoM)
  without touching the user's data.

Both tables are hidden from end-user data sources via warehouse.system_tables.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.db import get_connection
from warehouse.system_tables import is_system_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/measures", tags=["measures"])

CALENDAR_START = "2000-01-01"
CALENDAR_END = "2045-01-01"  # range() upper bound is exclusive — covers through 2044-12-31

# Allowed characters for measure expressions (aggregate fragments like
# SUM("Unit price") or COUNT(*) — no statement separators, no comments).
_EXPR_RE = re.compile(r"^[\w\s\.\(\)\*\+\-\/%<>=!,'\"\[\]]+$")
_BLOCKED = ("--", "/*", "*/", ";")

_TIME_MODE_LABELS = {
    "none": ("Value", ""),
    "ytd": ("YTD", "Prior YTD"),
    "mtd": ("MTD", "Prior MTD"),
    "qtd": ("QTD", "Prior QTD"),
    "mom": ("MTD", "Prev MTD"),
}


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(con, table: str) -> bool:
    # Shared connection is not thread-safe — same lock as /bi/query.
    from api.db import _bi_query_lock
    with _bi_query_lock:
        row = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'analytics'"
            " AND table_name = ?",
            [table],
        ).fetchone()
    return bool(row)


def _table_columns(con, table: str) -> List[str]:
    from api.db import _bi_query_lock
    with _bi_query_lock:
        cols = con.execute(f"DESCRIBE analytics.{_q(table)}").fetchall()
    return [r[0] for r in cols]


# ════════════════════════════════════════════════════════════════════════
# Calendar (date dimension)
# ════════════════════════════════════════════════════════════════════════

def ensure_calendar(con=None) -> bool:
    """Create the analytics.calendar date dimension if it does not exist yet.

    Idempotent by construction: `IF NOT EXISTS` on CREATE TABLE AS SELECT is
    atomic, so concurrent calls (e.g. two /measures requests on fresh
    connections) cannot race a check-then-create.
    """
    con = con or get_connection()
    if _table_exists(con, "calendar"):
        return False
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS analytics.calendar AS
        SELECT
            d.date                                          AS date,
            EXTRACT(YEAR FROM d.date)::INT                  AS year,
            EXTRACT(QUARTER FROM d.date)::INT               AS quarter,
            EXTRACT(MONTH FROM d.date)::INT                 AS month,
            strftime(d.date, '%B')                          AS month_name,
            EXTRACT(DAY FROM d.date)::INT                   AS day,
            EXTRACT(DOY FROM d.date)::INT                   AS day_of_year,
            strftime(d.date, '%Y-%m')                       AS year_month,
            strftime(d.date, '%Y') || '-Q'
                || EXTRACT(QUARTER FROM d.date)             AS year_quarter,
            strftime(d.date, '%Y-W%W')                      AS year_week,
            EXTRACT(DOW FROM d.date)::INT                   AS weekday,
            strftime(d.date, '%A')                          AS weekday_name,
            EXTRACT(DOW FROM d.date) IN (0, 6)              AS is_weekend,
            date_trunc('month', d.date)                     AS month_start,
            date_trunc('quarter', d.date)                   AS quarter_start,
            date_trunc('year', d.date)                      AS year_start
        FROM range(DATE '{CALENDAR_START}', DATE '{CALENDAR_END}', INTERVAL 1 DAY) AS d(date)
    """)
    logger.info("Created analytics.calendar (%s → %s)", CALENDAR_START, CALENDAR_END)
    return True


# ════════════════════════════════════════════════════════════════════════
# Measures registry table
# ════════════════════════════════════════════════════════════════════════

def ensure_measures_table(con=None) -> None:
    con = con or get_connection()
    con.execute("""
        CREATE TABLE IF NOT EXISTS analytics.measures (
            id          VARCHAR,
            name        VARCHAR NOT NULL,
            table_name  VARCHAR NOT NULL,
            expression  VARCHAR NOT NULL,
            format      VARCHAR DEFAULT 'auto',
            description VARCHAR DEFAULT '',
            date_column VARCHAR DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def init_bi_tables() -> None:
    """Idempotent startup hook: creates calendar + measures table."""
    try:
        con = get_connection()
        ensure_calendar(con)
        ensure_measures_table(con)
        logger.info("BI calculation layer ready (calendar + measures)")
    except Exception as e:  # never block app startup
        logger.warning(f"BI calendar/measures init skipped: {e}")


# ════════════════════════════════════════════════════════════════════════
# Calendar hierarchy — catalog-backed date dimension (P3.11)
# ════════════════════════════════════════════════════════════════════════

_DATE_TYPE_RE = re.compile(r"date|timestamp|time", re.IGNORECASE)

_CALENDAR_LEVELS = (
    # (level, calendar_column, cell label template)
    ("Year", "year", "{Y}"),
    ("Quarter", "year_quarter", "{YQ}"),
    ("Month", "year_month", "{YM}"),
    ("Day", "date", "{D}"),
)


def _con_columns(con, table: str, schema: str = "analytics"):
    """Column names of a warehouse table (order preserved)."""
    from api.db import _bi_query_lock
    with _bi_query_lock:
        rows = con.execute(f"DESCRIBE {_q(schema)}.{_q(table)}").fetchall()
    return [r[0] for r in rows]


def _catalog_has_asset(table: str) -> bool:
    """Catalog-backed check: is this table registered in the data catalog?"""
    try:
        from api.db import _bi_query_lock
        with _bi_query_lock:
            con = get_connection()
            row = con.execute(
                "SELECT COUNT(*) FROM analytics.catalog_assets "
                "WHERE lower(table_name) = lower(?)", [table]
            ).fetchone()
            return bool(row and row[0] > 0)
    except Exception:
        return False


def get_calendar_hierarchy(table_name: str = "") -> Dict[str, Any]:
    """Calendar date-hierarchy descriptor (Year > Quarter > Month > Day).

    "Catalog-backed": the levels are read from the live ``analytics.calendar``
    dimension schema and the dimension table is registered in the data catalog
    as a certified asset. For an optional fact ``table_name`` the matching
    DATE/TIMESTAMP columns and the join expression are also returned so charts
    and slicers can group by calendar levels.
    """
    con = get_connection()
    hierarchy: Dict[str, Any] = {
        "name": "Calendar.Date (Year > Quarter > Month > Day)",
        "dimension": {"schema": "analytics", "table": "calendar"},
        "catalog_asset": _catalog_has_asset("calendar"),
        "date_columns": [],
        "levels": [],
        "join": None,
    }

    # Verify the calendar table exists before advertising levels.
    if _table_exists(con, "calendar"):
        try:
            cal_cols = set(_con_columns(con, "calendar"))
        except Exception:
            cal_cols = set()
        for level, cal_col, label_tpl in _CALENDAR_LEVELS:
            if cal_col not in cal_cols:
                continue
            hierarchy["levels"].append({
                "level": level,
                "calendar_column": cal_col,
                "label_template": label_tpl,
                "group_expr": 'c."%s"' % cal_col.replace('"', '""'),
            })

    if table_name:
        try:
            cols = _con_columns(con, table_name)
            date_cols = [c for c in cols if _DATE_TYPE_RE.search(str(c))]
        except Exception:
            date_cols = []
        hierarchy["date_columns"] = date_cols
        if date_cols:
            dc = date_cols[0]
            hierarchy["join"] = (
                'analytics.calendar c ON c."date" = CAST(%s.%s AS DATE)'
                % (_q(table_name), _q(dc))
            )
            identifier = _q(table_name) + ".*, " + "c.*"
            hierarchy["identifier"] = identifier
    return hierarchy


def calendar_preview(table_name: str = "") -> Dict[str, Any]:
    """Small human/UI-friendly probe: level values + row coverage."""
    out: Dict[str, Any] = {"success": True}
    if not _table_exists(get_connection(), "calendar"):
        return {"error": "calendar dimension not available"}
    hier = get_calendar_hierarchy(table_name)
    out["hierarchy"] = hier
    return out


# ════════════════════════════════════════════════════════════════════════
# Time intelligence — canonical SQL builder
# ════════════════════════════════════════════════════════════════════════

def _anchor_cte(tq: str, dcq: str) -> str:
    """Single-row CTE `a` with the data-anchored reference date `ref`.

    ref = MIN(CURRENT_DATE, MAX(data date ≤ CURRENT_DATE)) — i.e. relative
    periods (YTD / MTD / QTD / MoM) are anchored to the freshest data point,
    not to the wall clock, so half-loaded or stale datasets still resolve
    against their latest completed period.
    """
    return (
        f"WITH a AS ("
        f"SELECT COALESCE(LEAST(CURRENT_DATE, MAX(TRY_CAST(t.{dcq} AS DATE))), CURRENT_DATE) AS ref "
        f"FROM analytics.{tq} t"
        f")"
    )


def _period_conditions(mode: str):
    """Return (cur, prev, prevfull) calendar WHERE fragments.

    All conditions reference ``a.ref`` — the data-anchored reference date
    (see ``_anchor_cte``).  ``prevfull`` is the complete previous period
    (full prior month / quarter / year) used as the comparison basis;
    ``prev`` is the same-day-aligned equivalent of the current period.
    """
    m = str(mode).lower()
    if m == "ytd":
        return (
            "c.year = YEAR(a.ref) AND c.date <= a.ref",
            "c.year = YEAR(a.ref) - 1 AND c.date <= a.ref - INTERVAL 1 YEAR",
            "c.year = YEAR(a.ref) - 1",
        )
    if m == "mtd":
        return (
            "c.year = YEAR(a.ref) AND c.month = MONTH(a.ref) AND c.date <= a.ref",
            "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date <= a.ref - INTERVAL 1 MONTH",
            "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date < DATE_TRUNC('month', a.ref)",
        )
    if m == "qtd":
        # Note: DuckDB INTERVAL 1 QUARTER is 3 months (earlier date same year),
        # so prior-year alignment must use INTERVAL 1 YEAR on the anchor.
        return (
            "c.date >= DATE_TRUNC('quarter', a.ref) AND c.date <= a.ref",
            "c.date >= DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR) AND c.date <= a.ref - INTERVAL 1 YEAR",
            "c.date >= DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR)"
            " AND c.date < DATE_TRUNC('quarter', a.ref - INTERVAL 1 YEAR) + INTERVAL 3 MONTH",
        )
    if m == "mom":
        return (
            "c.date >= DATE_TRUNC('month', a.ref) AND c.date <= a.ref",
            "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date <= a.ref - INTERVAL 1 MONTH",
            "c.date >= DATE_TRUNC('month', a.ref - INTERVAL 1 MONTH) AND c.date < DATE_TRUNC('month', a.ref)",
        )
    return ("", "", "")


def build_time_intel_sql(expression: str, table: str, date_column: str, mode: str = "none") -> Dict[str, Any]:
    """Build {sql, spark_sql, anchor_sql, mode, cur_label, prev_label}.

    The main sql returns three scalar columns:
      value      — current period
      previous   — equivalent to-date period in the previous period
      comparison — full previous period (delta basis)
    """
    mode = str(mode or "none").lower()
    if mode not in _TIME_MODE_LABELS:
        mode = "none"
    cur_label, prev_label = _TIME_MODE_LABELS[mode]
    tq = _q(table)
    dcq = _q(date_column)

    if not date_column or mode == "none":
        return {
            "sql": (
                f"SELECT {expression} AS value, NULL AS previous, NULL AS comparison, "
                f"(SELECT strftime(MAX(CAST(t2.{dcq} AS DATE)), '%Y-%m-%d') FROM analytics.{tq} t2) AS anchor"
                if date_column
                else f"SELECT {expression} AS value, NULL AS previous, NULL AS comparison FROM analytics.{tq}"
            ),
            "spark_sql": "",
            "anchor_sql": "",
            "mode": "none",
            "cur_label": cur_label,
            "prev_label": prev_label,
        }

    cur, prev, prevfull = _period_conditions(mode)
    cte = _anchor_cte(tq, dcq)
    base = f"FROM analytics.{tq} t JOIN analytics.calendar c ON c.date = CAST(t.{dcq} AS DATE) JOIN a ON TRUE"
    sql = (
        f"{cte} "
        f"SELECT "
        f"(SELECT {expression} {base} WHERE {cur}) AS value, "
        f"(SELECT {expression} {base} WHERE {prev}) AS previous, "
        f"(SELECT {expression} {base} WHERE {prevfull}) AS comparison, "
        f"(SELECT strftime(MAX(CAST(t2.{dcq} AS DATE)), '%Y-%m-%d') FROM analytics.{tq} t2) AS anchor"
    )
    spark_sql = (
        f"{cte} "
        f"SELECT strftime(c.date, '%Y-%m') AS label, {expression} AS value "
        f"{base} "
        f"WHERE c.date >= CAST(strftime(a.ref - INTERVAL 11 MONTH, '%Y-%m-01') AS DATE) "
        f"AND c.date <= a.ref "
        f"GROUP BY label ORDER BY label"
    )
    anchor_sql = f"{cte} SELECT {_q('ref')} FROM a"
    return {
        "sql": sql,
        "spark_sql": spark_sql,
        "anchor_sql": anchor_sql,
        "mode": mode,
        "cur_label": cur_label,
        "prev_label": prev_label,
    }


# ════════════════════════════════════════════════════════════════════════
# Validation
# ════════════════════════════════════════════════════════════════════════

def _validate_measure(con, name: str, table: str, expression: str, date_column: str) -> Optional[str]:
    """Return an error message, or None when valid."""
    name = (name or "").strip()
    table = (table or "").strip()
    expression = (expression or "").strip()
    if not name:
        return "Measure name is required"
    if not table:
        return "Source table is required"
    if not expression:
        return "Expression is required"
    if is_system_table(table):
        return f"'{table}' is a system table — pick a user data source"
    if not _table_exists(con, table):
        return f"Table '{table}' not found in the warehouse"
    if any(b in expression for b in _BLOCKED) or not _EXPR_RE.match(expression):
        return "Expression contains unsupported characters (blocked: ; -- /* */)"
    try:
        con.execute(f"SELECT {expression} AS value FROM analytics.{_q(table)} LIMIT 1")
    except Exception as e:
        return f"Expression failed to run: {e}"
    if date_column:
        cols = _table_columns(con, table)
        if date_column not in cols:
            return f"Date column '{date_column}' not found in {table}"
    return None


# ════════════════════════════════════════════════════════════════════════
# Pydantic models
# ════════════════════════════════════════════════════════════════════════

class MeasureIn(BaseModel):
    name: str = Field(..., max_length=120)
    table_name: str = Field(..., max_length=120)
    expression: str = Field(..., max_length=500)
    format: str = Field("auto", max_length=40)
    description: str = Field("", max_length=500)
    date_column: str = Field("", max_length=120)


class PreviewIn(BaseModel):
    mode: str = "none"


def _row_to_measure(row, columns) -> Dict[str, Any]:
    """Row -> dict, tolerant of whatever columns the live table actually has.

    The warehouse table may lag behind this module's schema (missing columns
    show up empty instead of raising IndexError and 500-ing the endpoint).
    """
    values = {name: row[i] for i, name in enumerate(columns) if i < len(row)}
    return {
        "id": values.get("id", ""),
        "name": values.get("name", values.get("id", "")),
        "table_name": values.get("table_name", ""),
        "expression": values.get("expression", ""),
        "format": values.get("format", "auto"),
        "description": values.get("description", ""),
        "date_column": values.get("date_column", ""),
        "created_at": str(values.get("created_at", "")),
        "updated_at": str(values.get("updated_at", "")),
    }


# ════════════════════════════════════════════════════════════════════════
# REST endpoints
# ════════════════════════════════════════════════════════════════════════

@router.get("")
def list_measures(table: Optional[str] = None):
    """List registered measures, optionally filtered by source table."""
    # Lazy self-heal: tables may not exist if the app started before this module.
    ensure_calendar()
    ensure_measures_table()
    con = get_connection()
    from api.db import _bi_query_lock
    with _bi_query_lock:
        if table:
            cur = con.execute(
                "SELECT * FROM analytics.measures WHERE table_name = ? ORDER BY name",
                [table],
            )
        else:
            cur = con.execute(
                "SELECT * FROM analytics.measures ORDER BY table_name, name"
            )
        rows = cur.fetchall()
        columns = [d[0] for d in (cur.description or [])]
    return {"success": True, "count": len(rows), "measures": [_row_to_measure(r, columns) for r in rows]}


@router.get("/health")
def measures_health():
    """Calendar + registry health (row counts / date span)."""
    ensure_calendar()
    ensure_measures_table()
    con = get_connection()
    cal = con.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM analytics.calendar"
    ).fetchone()
    n = con.execute("SELECT COUNT(*) FROM analytics.measures").fetchone()[0]
    return {
        "success": True,
        "calendar": {"rows": int(cal[0]), "min": str(cal[1]), "max": str(cal[2])},
        "measures": int(n),
    }


@router.get("/sql")
def _measure_sql(name: str, mode: str = "none"):
    """Resolve a named measure to its plain / time-intel SQL (read-only)."""
    ensure_measures_table()
    con = get_connection()
    cur = con.execute(
        "SELECT * FROM analytics.measures WHERE name = ? LIMIT 1", [name]
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Measure '{name}' not found")
    m = _row_to_measure(row, [d[0] for d in (cur.description or [])])
    built = build_time_intel_sql(m["expression"], m["table_name"], m["date_column"], mode)
    return {"success": True, "measure": m, **built}


@router.post("")
def create_measure(req: MeasureIn):
    """Register a new measure (validated against the warehouse)."""
    ensure_measures_table()
    con = get_connection()
    err = _validate_measure(con, req.name, req.table_name, req.expression, req.date_column)
    if err:
        raise HTTPException(400, err)
    mid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    con.execute(
        "INSERT INTO analytics.measures (id, name, table_name, expression, format, description, date_column, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [mid, req.name.strip(), req.table_name.strip(), req.expression.strip(),
         req.format or "auto", (req.description or "").strip(), req.date_column.strip(), now, now],
    )
    return {"success": True, "measure": {
        "id": mid, "name": req.name.strip(), "table_name": req.table_name.strip(),
        "expression": req.expression.strip(), "format": req.format or "auto",
        "description": (req.description or "").strip(), "date_column": req.date_column.strip(),
    }}


@router.put("/{measure_id}")
def update_measure(measure_id: str, req: MeasureIn):
    ensure_measures_table()
    con = get_connection()
    exists = con.execute(
        "SELECT 1 FROM analytics.measures WHERE id = ?", [measure_id]
    ).fetchone()
    if not exists:
        raise HTTPException(404, "Measure not found")
    err = _validate_measure(con, req.name, req.table_name, req.expression, req.date_column)
    if err:
        raise HTTPException(400, err)
    con.execute(
        "UPDATE analytics.measures SET name = ?, table_name = ?, expression = ?, format = ?, "
        "description = ?, date_column = ?, updated_at = ? WHERE id = ?",
        [req.name.strip(), req.table_name.strip(), req.expression.strip(), req.format or "auto",
         (req.description or "").strip(), req.date_column.strip(), _now_iso(), measure_id],
    )
    return {"success": True, "id": measure_id}


@router.delete("/{measure_id}")
def delete_measure(measure_id: str):
    ensure_measures_table()
    con = get_connection()
    con.execute("DELETE FROM analytics.measures WHERE id = ?", [measure_id])
    return {"success": True, "id": measure_id}


@router.post("/{measure_id}/preview")
def preview_measure(measure_id: str, body: PreviewIn):
    """Compute a measure's value + time-intel deltas + trend (read-only)."""
    ensure_measures_table()
    con = get_connection()
    cur = con.execute(
        "SELECT * FROM analytics.measures WHERE id = ?", [measure_id]
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Measure not found")
    m = _row_to_measure(row, [d[0] for d in (cur.description or [])])
    built = build_time_intel_sql(m["expression"], m["table_name"], m["date_column"], body.mode or "none")
    try:
        r = con.execute(built["sql"]).fetchone()
    except Exception as e:
        raise HTTPException(400, f"Measure evaluation failed: {e}")
    value = r[0] if r else None
    previous = r[1] if r else None
    comparison = r[2] if r else None
    delta = delta_pct = None
    try:
        v, p = float(value), float(previous)
        if p is not None:
            delta = round(v - p, 4)
            delta_pct = round((v - p) / abs(p) * 100, 2) if p else None
    except (TypeError, ValueError):
        pass
    anchor = None
    if built["anchor_sql"]:
        try:
            anchor = str(con.execute(built["anchor_sql"]).fetchone()[0])
        except Exception:
            pass
    trend = {"labels": [], "values": []}
    if built["spark_sql"]:
        try:
            for lr in con.execute(built["spark_sql"]).fetchall():
                trend["labels"].append(str(lr[0]))
                trend["values"].append(float(lr[1]) if lr[1] is not None else None)
        except Exception:
            pass
    return {
        "success": True,
        "measure": {
            "id": m["id"], "name": m["name"], "table_name": m["table_name"],
            "expression": m["expression"], "format": m["format"],
            "description": m["description"], "date_column": m["date_column"],
        },
        "mode": built["mode"],
        "cur_label": built["cur_label"],
        "prev_label": built["prev_label"],
        "anchor": anchor,
        "value": value,
        "previous": previous,
        "comparison": comparison,
        "delta": delta,
        "delta_pct": delta_pct,
        "trend": trend,
        "sql": built["sql"],
        "spark_sql": built["spark_sql"],
    }