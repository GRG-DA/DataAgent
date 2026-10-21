"""
Self-Service Query Builder
Converts visual query specifications to DuckDB SQL with JOIN auto-detection,
validation, and preview. Enables non-technical users to build queries visually.
"""

import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field, asdict, fields
import json

import duckdb
import pandas as pd

from config import settings
from api.tenancy import apply_tenant_search_path, tenant_database_path

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

MAX_RESULT_ROWS = 1_000_000

_TABLE_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _coerce_row_count(value: Any, field_name: str) -> int:
    """Coerce a JSON-provided ``limit``/``offset`` into a safe integer.

    Rejects booleans, fractional floats, negative values, and — critically —
    strings that are not plain digits, so payloads like
    ``"5; DROP TABLE x; --"`` can never reach the SQL string.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(
            f"Invalid {field_name}: expected a non-negative integer, got {value!r}"
        )
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(
                f"Invalid {field_name}: expected an integer, got {value!r}"
            )
        result = int(value)
    elif isinstance(value, str):
        if not re.fullmatch(r"\d{1,9}", value.strip()):
            raise ValueError(
                f"Invalid {field_name}: expected a non-negative integer, got {value!r}"
            )
        result = int(value.strip())
    else:
        raise ValueError(
            f"Invalid {field_name}: expected a non-negative integer, "
            f"got {type(value).__name__}"
        )
    if result < 0:
        raise ValueError(f"Invalid {field_name}: must be non-negative")
    if result > MAX_RESULT_ROWS:
        raise ValueError(f"Invalid {field_name}: must be <= {MAX_RESULT_ROWS}")
    return result


def quote_table_ref(table: str) -> str:
    """Validate and quote a (possibly schema-qualified) table reference.

    Raises ValueError when any dot-separated part is not a plain SQL
    identifier, otherwise returns ``"part"`` or ``"schema"."table"``.
    """
    parts = [p.strip() for p in str(table).strip().split(".")]
    if not parts or any(not _TABLE_PART_RE.fullmatch(p) for p in parts):
        raise ValueError(f"Invalid table name: {table!r}")
    return ".".join(f'"{p}"' for p in parts)


class FilterOperator(str, Enum):
    """Supported filter operators."""
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    LIKE = "like"
    NOT_LIKE = "not_like"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    CONTAINS = "contains"  # case-insensitive substring


class AggregationType(str, Enum):
    """Supported aggregation functions."""
    SUM = "sum"
    AVG = "avg"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    MIN = "min"
    MAX = "max"
    MEDIAN = "median"
    STDDEV = "stddev"


class JoinType(str, Enum):
    """Supported JOIN types."""
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class SortDirection(str, Enum):
    """Sort direction."""
    ASC = "asc"
    DESC = "desc"


@dataclass
class SelectField:
    """A field in the SELECT clause."""
    table: str = ""
    column: str = ""
    alias: str = ""
    aggregation: Optional[AggregationType] = None
    distinct: bool = False


@dataclass
class FilterCondition:
    """A WHERE clause filter."""
    table: str = ""
    column: str = ""
    operator: FilterOperator = FilterOperator.EQ
    value: Any = None
    value2: Any = None  # for BETWEEN
    logical: str = "AND"  # AND, OR


@dataclass
class SortSpec:
    """ORDER BY specification."""
    table: str = ""
    column: str = ""
    direction: SortDirection = SortDirection.ASC

    def __post_init__(self):
        # JSON callers can smuggle non-strings (lists/dicts/ints) in via
        # `order_by`/`sorts`; reject them before they reach the SQL builder.
        if not isinstance(self.column, str):
            raise ValueError(
                f"Invalid sort column: expected a string, got {self.column!r}"
            )


@dataclass
class JoinSpec:
    """JOIN specification."""
    source_table: str = ""
    target_table: str = ""
    source_column: str = ""
    target_column: str = ""
    join_type: JoinType = JoinType.INNER


@dataclass
class GroupByField:
    """GROUP BY field."""
    table: str = ""
    column: str = ""


@dataclass
class QuerySpec:
    """Complete visual query specification."""
    from_table: str = ""
    select_fields: List[SelectField] = field(default_factory=list)
    filters: List[FilterCondition] = field(default_factory=list)
    sorts: List[SortSpec] = field(default_factory=list)
    joins: List[JoinSpec] = field(default_factory=list)
    group_by: List[GroupByField] = field(default_factory=list)
    having: Optional[FilterCondition] = None
    limit: int = 1000
    offset: int = 0
    table_alias: str = ""

    def __post_init__(self):
        # Coerce on construction so an injected string can never be
        # interpolated into the LIMIT/OFFSET clauses later.
        self.limit = _coerce_row_count(self.limit, "limit")
        self.offset = _coerce_row_count(self.offset, "offset")


# ── JSON → QuerySpec parsing ─────────────────────────────────────────────

def _enum_or(value, enum_cls, default=None):
    """Coerce a raw JSON value (str or enum) into ``enum_cls``."""
    if value is None:
        return default
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).lower())
    except ValueError:
        return default


def _coerce(cls, data):
    """Build a nested dataclass from a plain dict, ignoring unknown keys."""
    if isinstance(data, cls):
        return data
    if not isinstance(data, dict):
        return cls()
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def parse_query_spec(data: Dict[str, Any]) -> QuerySpec:
    """Parse a JSON payload into a fully-typed :class:`QuerySpec`.

    The API accepts plain dicts (FastAPI hands request bodies through as
    dicts), so every nested structure must be coerced into its dataclass —
    passing raw dicts into ``QuerySpec(**data)`` only fails later when the
    SQL builder accepts attributes.

    Supports two formats:
    1. Full spec: {"from_table": "...", "select_fields": [...], ...}
    2. Simple format: {"table": "...", "columns": [...], "aggregates": [...], ...}
    """
    # Normalize input: map simple format to full spec format
    normalized = dict(data)
    if "from_table" not in normalized and "table" in normalized:
        normalized["from_table"] = normalized.pop("table")

    # Build select_fields from columns + aggregates if not provided
    if "select_fields" not in normalized:
        columns = normalized.pop("columns", [])
        aggregates = normalized.pop("aggregates", [])
        select_fields_raw = []

        # Add plain columns
        for col in columns:
            if col == "*":
                continue  # Will be handled by empty select_fields
            select_fields_raw.append({"column": col})

        # Add aggregated columns
        for agg in aggregates:
            if isinstance(agg, dict):
                col = agg.get("column", "")
                func = agg.get("function", "count").lower()
                alias = agg.get("alias", "")
                if col:
                    select_fields_raw.append({
                        "column": col,
                        "aggregation": func,
                        "alias": alias or f"{func}_{col}",
                    })

        normalized["select_fields"] = select_fields_raw

    # Map simple sort format to full sorts
    if "sorts" not in normalized:
        order_by = normalized.pop("order_by", None)
        order_direction = normalized.pop("order_direction", "asc")
        if order_by:
            normalized["sorts"] = [{"column": order_by, "direction": order_direction}]

    select_fields = [
        _coerce(SelectField, {
            **f,
            "aggregation": _enum_or(f.get("aggregation"), AggregationType),
        }) if isinstance(f, dict) else f
        for f in normalized.get("select_fields", [])
    ]
    filters = [
        _coerce(FilterCondition, {
            **f,
            "operator": _enum_or(f.get("operator"), FilterOperator, FilterOperator.EQ),
        }) if isinstance(f, dict) else f
        for f in normalized.get("filters", [])
    ]
    sorts = [
        _coerce(SortSpec, {
            **f,
            "direction": _enum_or(f.get("direction"), SortDirection, SortDirection.ASC),
        }) if isinstance(f, dict) else f
        for f in normalized.get("sorts", [])
    ]
    joins = [
        _coerce(JoinSpec, {
            **j,
            "join_type": _enum_or(j.get("join_type"), JoinType, JoinType.INNER),
        }) if isinstance(j, dict) else j
        for j in normalized.get("joins", [])
    ]
    group_by = [
        _coerce(GroupByField, g) if isinstance(g, dict)
        else GroupByField(column=str(g)) if isinstance(g, str)
        else g
        for g in normalized.get("group_by", [])
    ]
    having_raw = normalized.get("having")
    having = _coerce(FilterCondition, {
        **having_raw,
        "operator": _enum_or(having_raw.get("operator"), FilterOperator, FilterOperator.EQ),
    }) if isinstance(having_raw, dict) else having_raw

    known_top = {f.name for f in fields(QuerySpec)}
    kwargs = {k: v for k, v in normalized.items() if k in known_top}
    kwargs.update(select_fields=select_fields, filters=filters, sorts=sorts,
                  joins=joins, group_by=group_by, having=having)
    return QuerySpec(**kwargs)


# ── SQL Builder ───────────────────────────────────────────────────────────

class SQLBuilder:
    """Converts QuerySpec to executable DuckDB SQL."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema

    def _tenant_db_path(self) -> str:
        """The DuckDB file that holds the caller's tenant's tables.

        With the per-tenant database mode on this is the tenant's own
        file; otherwise it is the shared warehouse.
        """
        return tenant_database_path(shared_path=self.db_path)

    def _quote(self, name: str) -> str:
        """Quote an identifier for safe SQL."""
        return f'"{name.replace(chr(34), chr(34) + chr(34))}"'

    def _qualify(self, table: str, column: str) -> str:
        """Return qualified column name (table.column or alias.column)."""
        t = table or self._current_alias
        return f"{self._quote(t)}.{self._quote(column)}"

    def build(self, spec: QuerySpec) -> Tuple[str, Optional[str]]:
        """
        Convert a QuerySpec to a SQL string.
        Returns (sql, error_message). error_message is None on success.
        """
        try:
            sql = self._build_query(spec)
            return sql, None
        except Exception as e:
            logger.error(f"SQL build failed: {e}")
            return "", str(e)

    def _build_query(self, spec: QuerySpec) -> str:
        """Build the full SQL query from spec."""
        parts = []

        # FROM clause
        from_table = self._quote(spec.from_table)
        alias = self._quote(spec.table_alias) if spec.table_alias else ""
        from_clause = f"FROM {from_table}"
        if alias:
            from_clause += f" AS {alias}"
        self._current_alias = spec.table_alias or spec.from_table

        # SELECT clause
        if not spec.select_fields:
            select_clause = f"SELECT *"
        else:
            select_items = []
            for field in spec.select_fields:
                col = self._qualify(field.table or spec.from_table, field.column)
                if field.distinct:
                    col = f"DISTINCT {col}"
                if field.aggregation:
                    agg_map = {
                        AggregationType.COUNT_DISTINCT: f"COUNT(DISTINCT {col})",
                        AggregationType.MEDIAN: f"MEDIAN({col})",
                        AggregationType.STDDEV: f"STDDEV({col})",
                    }
                    agg_sql = agg_map.get(field.aggregation,
                                           f"{field.aggregation.value.upper()}({col})")
                    col = agg_sql
                if field.alias:
                    col += f" AS {self._quote(field.alias)}"
                select_items.append(col)
            select_clause = "SELECT " + ", ".join(select_items)

        parts.append(select_clause)
        parts.append(from_clause)

        # JOINs
        for join in spec.joins:
            src = self._qualify(join.source_table, join.source_column)
            tgt = self._qualify(join.target_table, join.target_column)
            join_sql = f"{join.join_type.value.upper()} JOIN {self._quote(join.target_table)}"
            if join.target_column:
                join_sql += f" ON {src} = {tgt}"
            parts.append(join_sql)

        # WHERE filters
        if spec.filters:
            where_parts = []
            for f in spec.filters:
                col = self._qualify(f.table or spec.from_table, f.column)
                condition = self._build_filter_condition(col, f)
                where_parts.append(condition)
            if where_parts:
                # Combine with AND/OR — use the logical operator from the first filter
                logic = spec.filters[0].logical if spec.filters else "AND"
                where_clause = f"WHERE {f' {logic} '.join(where_parts)}"
                parts.append(where_clause)

        # GROUP BY — the caller's own GROUP BY wins; otherwise it is derived
        # from the non-aggregated select fields.
        group_by_fields = list(spec.group_by) or self._derive_group_by(spec)
        if group_by_fields:
            group_cols = [self._qualify(g.table or spec.from_table, g.column)
                          for g in group_by_fields]
            parts.append(f"GROUP BY {', '.join(group_cols)}")

        # HAVING
        if spec.having:
            col = self._qualify(spec.having.table or spec.from_table, spec.having.column)
            parts.append(f"HAVING {self._build_filter_condition(col, spec.having)}")

        # ORDER BY
        if spec.sorts:
            # Build a map of column -> alias for aggregated fields
            agg_alias_map = {}
            for field in spec.select_fields:
                if field.aggregation and field.alias:
                    agg_alias_map[field.column.lower()] = self._quote(field.alias)
                    # Callers sort by the alias they see in the result set.
                    agg_alias_map[field.alias.lower()] = self._quote(field.alias)
                elif field.aggregation and not field.alias:
                    # Auto-generated alias: aggregation_column
                    auto_alias = f"{field.aggregation.value}_{field.column}"
                    agg_alias_map[field.column.lower()] = self._quote(auto_alias)
                    agg_alias_map[auto_alias.lower()] = self._quote(auto_alias)

            order_cols = []
            for s in spec.sorts:
                # Belt-and-braces on top of SortSpec.__post_init__ (mutation).
                if not isinstance(s.column, str):
                    raise ValueError(
                        f"Invalid sort column: expected a string, got {s.column!r}"
                    )
                # Use alias if the sort column is an aggregated field
                col_key = s.column.lower()
                if col_key in agg_alias_map:
                    col = agg_alias_map[col_key]
                else:
                    col = self._qualify(s.table or spec.from_table, s.column)
                order_cols.append(f"{col} {s.direction.value.upper()}")
            parts.append(f"ORDER BY {', '.join(order_cols)}")

        # LIMIT / OFFSET — int() is belt-and-braces on top of __post_init__
        parts.append(f"LIMIT {int(spec.limit)}")
        if int(spec.offset) > 0:
            parts.append(f"OFFSET {int(spec.offset)}")

        return " ".join(parts)

    @staticmethod
    def _derive_group_by(spec: QuerySpec) -> List[GroupByField]:
        """Derive the GROUP BY from the non-aggregated select fields.

        A visual spec that mixes a plain column with an aggregate needs a
        GROUP BY over the plain columns, otherwise the generated SQL is
        invalid ("column must appear in the GROUP BY clause").  A GROUP BY
        the caller supplied always wins — this is only the fallback.
        """
        if not any(f.aggregation for f in spec.select_fields):
            return []
        derived: List[GroupByField] = []
        seen = set()
        for field in spec.select_fields:
            if field.aggregation or not field.column:
                continue
            key = (field.table or spec.from_table, field.column)
            if key in seen:
                continue
            seen.add(key)
            derived.append(GroupByField(table=field.table, column=field.column))
        return derived

    def _build_filter_condition(self, column: str, f: FilterCondition) -> str:
        """Build a single WHERE condition from a FilterCondition."""
        op = f.operator

        if op == FilterOperator.IS_NULL:
            return f"{column} IS NULL"
        if op == FilterOperator.IS_NOT_NULL:
            return f"{column} IS NOT NULL"

        if op == FilterOperator.IN:
            if isinstance(f.value, list):
                vals = ", ".join(self._format_value(v) for v in f.value)
                return f"{column} IN ({vals})"
            return f"{column} IN ({self._format_value(f.value)})"

        if op == FilterOperator.NOT_IN:
            if isinstance(f.value, list):
                vals = ", ".join(self._format_value(v) for v in f.value)
                return f"{column} NOT IN ({vals})"
            return f"{column} NOT IN ({self._format_value(f.value)})"

        if op == FilterOperator.BETWEEN:
            return f"{column} BETWEEN {self._format_value(f.value)} AND {self._format_value(f.value2 or f.value)}"

        if op == FilterOperator.LIKE:
            return f"{column} LIKE {self._format_value(f.value)}"

        if op == FilterOperator.NOT_LIKE:
            return f"{column} NOT LIKE {self._format_value(f.value)}"

        if op == FilterOperator.CONTAINS:
            return f"LOWER({column}) LIKE LOWER({self._format_value(f'%{f.value}%')})"

        # Standard operators
        op_map = {
            FilterOperator.EQ: "=",
            FilterOperator.NEQ: "!=",
            FilterOperator.GT: ">",
            FilterOperator.GTE: ">=",
            FilterOperator.LT: "<",
            FilterOperator.LTE: "<=",
        }
        sql_op = op_map.get(op, "=")
        return f"{column} {sql_op} {self._format_value(f.value)}"

    def _format_value(self, value: Any) -> str:
        """Format a value for SQL inclusion."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        # String — escape single quotes
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def validate(self, spec: QuerySpec) -> Dict[str, Any]:
        """
        Validate a query spec without executing.
        Returns dict with is_valid, errors list, and estimated_row_count.
        """
        errors = []

        # Must have a from table
        if not spec.from_table:
            errors.append("No table selected")

        # Validate tables exist
        try:
            conn = duckdb.connect(self._tenant_db_path())
            apply_tenant_search_path(conn)
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{self.schema}'"
            ).fetchall()
            table_names = {t[0].lower() for t in tables}
            conn.close()

            if spec.from_table.lower() not in table_names:
                errors.append(f"Table '{spec.from_table}' not found in warehouse")

            for join in spec.joins:
                if join.source_table.lower() not in table_names:
                    errors.append(f"Table '{join.source_table}' not found (in JOIN)")
                if join.target_table.lower() not in table_names:
                    errors.append(f"Table '{join.target_table}' not found (in JOIN)")
        except Exception as e:
            errors.append(f"Cannot validate tables: {e}")

        # Aggregation + non-aggregation mixing: every plain column must be
        # grouped.  The builder derives the GROUP BY when the caller did
        # not supply one, so this is only an error when the *supplied*
        # GROUP BY leaves a plain column out.
        has_agg = any(f.aggregation for f in spec.select_fields)
        has_non_agg = any(not f.aggregation for f in spec.select_fields)
        if has_agg and has_non_agg:
            effective = {
                g.column.lower() for g in (spec.group_by or self._derive_group_by(spec))
            }
            missing = sorted({
                f.column for f in spec.select_fields
                if not f.aggregation and f.column.lower() not in effective
            })
            if missing:
                errors.append(
                    "Non-aggregated columns must be in GROUP BY when using "
                    "aggregations: " + ", ".join(missing)
                )

        # Estimate row count
        estimated_rows = 0
        if not errors:
            try:
                sql, _ = self.build(spec)
                count_sql = sql.split("LIMIT")[0].replace(
                    "SELECT", "SELECT COUNT(*) AS cnt", 1
                ).split("ORDER BY")[0] if "SELECT" in sql else ""
                if count_sql:
                    conn = duckdb.connect(self._tenant_db_path())
                    apply_tenant_search_path(conn)
                    result = conn.execute(count_sql).fetchone()
                    conn.close()
                    estimated_rows = result[0] if result else 0
            except Exception:
                estimated_rows = -1  # Unknown

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "estimated_row_count": estimated_rows,
            "warning": "Large result set" if estimated_rows > 100000 else "",
        }

    def execute(self, spec: QuerySpec) -> Dict[str, Any]:
        """Build and execute a query, returning results."""
        sql, error = self.build(spec)
        if error:
            return {"success": False, "error": error}

        try:
            from etl.readonly import readonly_transaction

            conn = duckdb.connect(self._tenant_db_path())
            try:
                apply_tenant_search_path(conn)
                with readonly_transaction(conn):
                    df = conn.execute(sql).df()
            finally:
                conn.close()

            total_rows = len(df)
            paginated = df.head(spec.limit)

            return {
                "success": True,
                "sql": sql,
                "row_count": total_rows,
                "columns": list(df.columns),
                "data": paginated.to_dict("records"),
                "truncated": total_rows > spec.limit,
            }
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            from api.tenancy import sanitize_error_message

            return {"success": False, "error": sanitize_error_message(str(e)), "sql": sql}


# ── JOIN Auto-Detection ───────────────────────────────────────────────────

class JoinDetector:
    """Auto-detect potential JOIN relationships between tables."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema

    def _tenant_db_path(self) -> str:
        """The DuckDB file that holds the caller's tenant's tables."""
        return tenant_database_path(shared_path=self.db_path)

    def get_sample_data(self, table: str, limit: int = 50) -> pd.DataFrame:
        """Load sample data from a table. Returns an empty frame when the
        table does not exist or the name is unsafe so callers (e.g.
        JoinDetector.detect_joins) can degrade gracefully instead of raising
        CatalogException."""
        try:
            ref = quote_table_ref(table)
            safe_limit = max(1, min(int(limit or 50), 1000))
            conn = duckdb.connect(self._tenant_db_path())
            try:
                apply_tenant_search_path(conn)
                return conn.execute(f"SELECT * FROM {ref} LIMIT {safe_limit}").df()
            finally:
                conn.close()
        except Exception:
            return pd.DataFrame()

    def detect_joins(self, table1: str, table2: str) -> List[JoinSpec]:
        """
        Detect potential JOIN columns between two tables.
        Looks for matching column names and value overlaps.
        """
        df1 = self.get_sample_data(table1)
        df2 = self.get_sample_data(table2)

        if df1.empty or df2.empty:
            return []

        candidates = []
        for c1 in df1.columns:
            c1_low = c1.lower()
            for c2 in df2.columns:
                c2_low = c2.lower()

                # Name-based match patterns
                match = False
                if c1_low == c2_low and ("id" in c1_low or c1_low.endswith("_id")):
                    match = True
                elif c2_low == "id" and c1_low == f"{table2.lower()}_id":
                    match = True
                elif c1_low == "id" and c2_low == f"{table1.lower()}_id":
                    match = True

                if match:
                    # Value overlap check
                    vals1 = set(df1[c1].dropna().astype(str).unique()[:20])
                    vals2 = set(df2[c2].dropna().astype(str).unique()[:20])
                    overlap = len(vals1 & vals2)
                    total = len(vals1 | vals2)
                    overlap_pct = round(overlap / total * 100, 1) if total > 0 else 0

                    if overlap > 0:
                        candidates.append({
                            "source_table": table1,
                            "target_table": table2,
                            "source_column": c1,
                            "target_column": c2,
                            "overlap_pct": overlap_pct,
                            "sample_match": f"{overlap}/{total} values match",
                            "confidence": "high" if overlap_pct > 50 else "medium" if overlap_pct > 20 else "low",
                        })

        candidates.sort(key=lambda x: x["overlap_pct"], reverse=True)
        return candidates

    def suggest_metrics(self, table: str) -> List[Dict[str, Any]]:
        """Suggest useful metrics and dimensions for a table."""
        df = self.get_sample_data(table)
        if df.empty:
            return []

        suggestions = []
        for col in df.columns:
            dtype = df[col].dtype
            if pd.api.types.is_numeric_dtype(dtype):
                suggestions.append({
                    "table": table,
                    "column": col,
                    "type": "metric",
                    "default_aggregation": "sum",
                    "suggested_chart": "bar" if df[col].nunique() < 10 else "line",
                })
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                suggestions.append({
                    "table": table,
                    "column": col,
                    "type": "dimension",
                    "default_aggregation": "year_month",
                    "suggested_chart": "line",
                })
            else:
                unique_ratio = df[col].nunique() / len(df) if len(df) > 0 else 0
                if unique_ratio < 0.5:  # Low cardinality = good dimension
                    suggestions.append({
                        "table": table,
                        "column": col,
                        "type": "dimension",
                        "default_aggregation": None,
                        "suggested_chart": "bar" if df[col].nunique() < 20 else "table",
                    })

        return suggestions


# ── Convenience ───────────────────────────────────────────────────────────

def build_and_execute(spec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Build and execute a query from a dict spec (for API endpoint)."""
    spec = QuerySpec(**spec_dict)
    builder = SQLBuilder()
    return builder.execute(spec)


def validate_spec(spec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a query spec from a dict."""
    try:
        spec = QuerySpec(**spec_dict)
        builder = SQLBuilder()
        return builder.validate(spec)
    except Exception as e:
        return {"is_valid": False, "errors": [str(e)], "estimated_row_count": 0}
