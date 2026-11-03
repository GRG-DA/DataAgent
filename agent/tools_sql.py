"""
SQL Tool Module
Execute SQL queries against DuckDB warehouse with safety checks
"""

import logging
import os
import re
import time
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import pandas as pd
import duckdb
from dataclasses import dataclass
from contextlib import contextmanager

from api.audit import AuditResult

try:
    import sqlglot
    from sqlglot import exp as sqlglot_exp
except ImportError:  # pragma: no cover - sqlglot ships with the app deps
    sqlglot = None
    sqlglot_exp = None

_EXP_SEMICOLON = getattr(sqlglot_exp, "Semicolon", None) if sqlglot_exp is not None else None
_ALLOWED_AST_ROOTS = (
    (sqlglot_exp.Select, sqlglot_exp.Union, sqlglot_exp.Intersect, sqlglot_exp.Except)
    if sqlglot_exp is not None
    else ()
)
_PLAIN_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Schemas a user-supplied query may not reach.  `information_schema` and
#: `pg_catalog` would disclose other tenants' table names; every
#: `analytics_t_*` schema belongs to another tenant (ADR-009 D1).
_BLOCKED_SCHEMAS = frozenset({
    "information_schema",
    "pg_catalog",
    "main",
    "temp",
})

#: The system catalogs.  These are refused for *every* caller, including
#: one that is allowed to read the platform tables: `information_schema`
#: lists every schema and table in the file, including the other tenants'
#: `analytics_t_*` schemas, so it is a cross-tenant disclosure channel.
#: Platform-internal queries do not go through this validator.
_CATALOG_SCHEMAS = frozenset({
    "information_schema",
    "pg_catalog",
})

logger = logging.getLogger(__name__)


def _blocked_schemas() -> frozenset:
    """Schemas a user-supplied query may not reach, for the *current* tenant.

    The platform schema (``analytics``) is added for every tenant except
    ``default`` — whose own schema IS ``analytics``.  Without this a stale
    hard-coded ``analytics.<table>`` reference would silently resolve to the
    operator's table instead of failing closed (LAUNCH-TECHNICAL-REVIEW P0-3).
    The tenant's own ``analytics_t_*`` schema stays unreachable by explicit
    qualification: unqualified names are resolved through the search path,
    which is set per request to the caller's tenant schema (ADR-009 D1).
    """
    try:
        from api.tenancy import DEFAULT_TENANT_ID, PLATFORM_SCHEMA, current_tenant_id

        if current_tenant_id() != DEFAULT_TENANT_ID:
            return _BLOCKED_SCHEMAS | {PLATFORM_SCHEMA}
    except Exception:  # noqa: BLE001 - never break a query
        pass
    return _BLOCKED_SCHEMAS


def _own_tenant_schema() -> str:
    """The schema that holds the CALLER's own tables ("" when unknown).

    A qualified reference to the caller's OWN schema is safe - it is their
    data, in their workspace - and the app itself used to hand such
    references to the model (the SQL schema context named the tenant
    schema). Every OTHER ``analytics_t_*`` schema stays blocked: that is
    somebody else's data, and the whole point of the guard is that a query
    may not reach it by naming it.
    """
    try:
        from api.tenancy import current_tenant_id, tenant_schema

        return (tenant_schema(current_tenant_id()) or "").strip().lower()
    except Exception:  # noqa: BLE001 - never break a query
        return ""


def _schema_is_denied(db: str, blocked: frozenset) -> bool:
    """True when a schema-qualified reference must be refused.

    The system catalogs are never readable. The platform schema is blocked
    for every tenant but the one that owns it. The caller's own tenant
    schema is allowed; every other tenant's schema is not.
    """
    db_lower = (db or "").strip().lower()
    if not db_lower:
        return False
    if db_lower in _CATALOG_SCHEMAS:
        return True
    if db_lower in blocked:
        return True
    if db_lower.startswith("analytics_t_"):
        return db_lower != _own_tenant_schema()
    return False


def _sanitize_error(message: str) -> str:
    """Strip cross-tenant details from a warehouse error.

    DuckDB's "did you mean" suggestion can name another tenant's schema and
    table; the agent would otherwise repeat it to the user.
    """
    try:
        from api.tenancy import sanitize_error_message

        return sanitize_error_message(message)
    except Exception:  # noqa: BLE001
        return message


def _is_app_warehouse(db_path) -> bool:
    """True when *db_path* is the warehouse file the app already holds open.

    The app keeps one read-write connection to ``warehouse.duckdb`` open for
    the whole process (see ``api/db.py``). DuckDB refuses a second
    connection to the same file with a different configuration, so a fresh
    handle to the app's own warehouse is a guaranteed "file in use" error.
    Any other file (a test fixture, an ad-hoc database) is safe to open
    directly because it does not collide with the app's handle.
    """
    from config import settings

    try:
        app_path = os.path.abspath(str(settings.duckdb.database_path))
        target = os.path.abspath(str(db_path))
    except Exception:  # noqa: BLE001 - never let path resolution break a query
        return False
    return target == app_path


def _strip_sql_string_literals(sql: str) -> str:
    """Blank out the contents of single-quoted literals (handles '' escapes).

    Keeps the surrounding quotes so structural checks like ``FROM '...'``
    still see the literal marker, while text inside a string can never
    trigger keyword/function false positives — or hide payloads.
    """
    out: List[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
                out.append("'")
        else:
            out.append(ch)
            if ch == "'":
                in_string = True
        i += 1
    return "".join(out)


@dataclass
class SQLResult:
    """Result of SQL execution."""
    success: bool
    data: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    row_count: int = 0
    columns: Optional[List[str]] = None
    query: str = ""
    execution_time_ms: float = 0


class SQLTool:
    """
    SQL execution tool with safety validation.

    Features:
    - Read-only query enforcement
    - Forbidden keyword detection
    - Row limits
    - Query timeout
    - Result formatting
    - Row-Level Security (RLS) integration
    - Audit logging
    """

    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER",
        "TRUNCATE", "REPLACE", "MERGE", "COPY", "EXPORT", "IMPORT",
        "ATTACH", "DETACH", "PRAGMA", "SET", "RESET",
        "INSTALL", "LOAD", "CALL",
    ]

    # DuckDB file/network table functions: local-file read + SSRF via URL
    # forms (read_csv_auto('http://169.254.169.254/…'), httpfs, …).
    FORBIDDEN_FUNCTIONS = [
        "READ_CSV", "READ_CSV_AUTO", "CSV_SCAN", "READ_PARQUET",
        "PARQUET_SCAN", "READ_JSON", "READ_JSON_AUTO", "READ_NDJSON",
        "READ_XLSX", "READ_EXCEL", "READ_TEXT", "READ_BLOB",
        "GLOB", "SNIFF_CSV", "GETENV", "QUERY_TABLE",
        "PARQUET_METADATA", "PARQUET_SCHEMA",
        "HTTPFS", "HTTP_", "S3_", "AZURE_", "GCS_",
        "CURRENT_SETTING", "DUCKDB_SETTINGS",
    ]

    FORBIDDEN_FUNCTION_PREFIXES = (
        "READ_", "HTTP_", "S3_", "AZURE_", "GCS_", "PARQUET_",
    )

    ALLOWED_OPERATIONS = [
        "SELECT", "WITH", "LIMIT", "ORDER BY", "GROUP BY",
        "HAVING", "WHERE", "JOIN", "UNION", "INTERSECT",
        "EXCEPT", "DISTINCT", "CASE", "CAST",
    ]

    def __init__(
        self,
        db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
        schema: str = "analytics",
        max_rows: int = 1000,
        timeout: int = 60,
        allowed_operations: Optional[List[str]] = None,
        forbidden_keywords: Optional[List[str]] = None,
        rls_manager=None,
        audit_logger=None,
    ):
        self.db_path = Path(db_path)
        self.schema = schema
        self.max_rows = max_rows
        self.timeout = timeout
        self.allowed_operations = allowed_operations or self.ALLOWED_OPERATIONS
        self.forbidden_keywords = forbidden_keywords or self.FORBIDDEN_KEYWORDS
        self.rls_manager = rls_manager
        self.audit_logger = audit_logger

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    @contextmanager
    def get_connection(self):
        """Get a DuckDB connection.

        The app's own warehouse is served by the process-wide connection in
        ``api.db`` — the same pattern ``analysis/alerts.py`` and
        ``api/rls.py`` use. Opening a *fresh* read-write handle to that
        file was the source of the intermittent ``IO Error: Cannot open
        file ... used by another process`` 500s: DuckDB refuses a second
        connection to the same file with a different configuration.

        The shared connection is **process-wide**, so its ``search_path`` is
        set to the caller's tenant schema for the duration of the block and
        restored afterwards (``tenant_search_path_scope``).  Leaving the
        tenant's schema on the shared connection would let the *next*
        request resolve the previous tenant's tables — a cross-tenant read.
        Any other file is opened directly, because it does not collide with
        the app's handle.

        With the opt-in per-tenant database mode on, the tenant's data
        lives in the tenant's *own* file, so that file is opened instead
        of the shared warehouse — a hostile DuckDB client that opens the
        shared file can then not reach the tenant's tables at all.
        """
        from api.tenancy import (
            apply_tenant_search_path,
            apply_tenant_search_path_safe,
            current_tenant_id,
            tenant_database_path,
            tenant_owns_own_file,
            tenant_search_path_scope,
        )

        tid = current_tenant_id()
        if tenant_owns_own_file(tid):
            path = tenant_database_path(tid, shared_path=str(self.db_path))
            conn = duckdb.connect(path)
            try:
                apply_tenant_search_path(conn, tid)
                conn.execute("PRAGMA enable_progress_bar=false")
                yield conn
            finally:
                conn.close()
            return

        if _is_app_warehouse(self.db_path):
            from api.db import get_connection, _bi_query_lock

            conn = get_connection()
            # The shared connection is not thread-safe: hold the lock for
            # the whole execute+fetch window so a concurrent request cannot
            # step this query's pending result — or change the search path
            # under it.
            with _bi_query_lock:
                with tenant_search_path_scope(conn, tid):
                    yield conn
            return

        conn = duckdb.connect(str(self.db_path))
        try:
            # The tenant's schema comes first, so an unqualified table name
            # resolves to the caller's own copy.  The schema is created when
            # it does not exist yet (a brand-new tenant, or a throwaway
            # warehouse used by a test).
            apply_tenant_search_path_safe(conn, tid)
            conn.execute("PRAGMA enable_progress_bar=false")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _system_tables() -> set:
        """The platform table namespace.

        The registry object is returned as-is: its ``in`` check also matches
        the derived-name conventions (``DateTable_*``, ``*__npk``) that a
        plain set of literals would lose.
        """
        try:
            from warehouse.system_tables import SYSTEM_TABLES
            return SYSTEM_TABLES
        except Exception:  # noqa: BLE001
            return {"api_keys"}

    def _is_forbidden_function(self, name: str) -> bool:
        """True when a parsed function name hits the deny list.

        Normalizes quoting and schema qualification:
        ``"read_text"`` -> ``READ_TEXT``, ``main.read_text`` -> ``READ_TEXT``.
        """
        normalized = (
            str(name).strip().strip('"').rsplit(".", 1)[-1].strip('"').upper()
        )
        if not normalized:
            return False
        if normalized in self.FORBIDDEN_FUNCTIONS:
            return True
        return normalized.startswith(self.FORBIDDEN_FUNCTION_PREFIXES)

    def _validate_ast(self, query: str, allow_system_tables: bool):
        """AST validation via sqlglot. Returns (ok, error) or None when the
        query cannot be parsed (caller then uses the text fallback)."""
        if sqlglot is None:
            return None
        try:
            parsed = sqlglot.parse(query, read="duckdb")
        except Exception:  # noqa: BLE001 - unparseable -> fallback path
            return None

        statements = []
        for stmt in parsed:
            if stmt is None:
                continue
            if _EXP_SEMICOLON is not None and isinstance(stmt, _EXP_SEMICOLON):
                continue  # trailing ";" artifact, not a real statement
            statements.append(stmt)

        if not statements:
            return False, "Empty query"
        if len(statements) > 1:
            return False, "Multiple statements not allowed"

        stmt = statements[0]
        if not isinstance(stmt, _ALLOWED_AST_ROOTS):
            kind = type(stmt).__name__.upper()
            return False, f"Query must be a single SELECT statement (got {kind})"
        if stmt.args.get("into") is not None:
            return False, "SELECT INTO is not allowed"

        # Scan every function expression, not just Anonymous: sqlglot models
        # several dangerous table functions as dedicated nodes (e.g.
        # read_parquet -> exp.ReadParquet), which a bare Anonymous scan misses.
        for fn in stmt.find_all(sqlglot_exp.Func):
            if isinstance(fn, sqlglot_exp.Anonymous):
                fn_name = fn.name or ""
            else:
                try:
                    fn_name = fn.sql_name()
                except Exception:  # noqa: BLE001 - unknown node -> class name
                    fn_name = type(fn).__name__
            if self._is_forbidden_function(fn_name):
                return False, f"Forbidden function detected: {fn_name.upper()}"

        system_tables = self._system_tables() if not allow_system_tables else set()
        for table in stmt.find_all(sqlglot_exp.Table):
            name = (table.name or "").strip()
            if not name:
                # e.g. FROM query_table('…') — the function check covers it
                continue
            if not _PLAIN_TABLE_RE.fullmatch(name):
                return False, f"Invalid or file-based table reference: {name}"
            # A schema-qualified reference must not reach another tenant's
            # schema or the system catalogs.  With one schema per tenant,
            # `analytics_t_other.sales` would otherwise be readable.
            db = (table.db or "").strip()
            if db:
                db_lower = db.lower()
                # The system catalogs are never readable through this
                # validator, whoever the caller is.
                if db_lower in _CATALOG_SCHEMAS:
                    return False, f"Access denied to schema: {db}"
                if not allow_system_tables:
                    blocked = _blocked_schemas()
                    if _schema_is_denied(db, blocked):
                        return False, f"Access denied to schema: {db}"
            if name.lower() in system_tables:
                return False, f"Access denied to platform table: {name}"

        return True, None

    def _validate_fallback(self, query: str, allow_system_tables: bool):
        """Hardened text scan for syntax sqlglot cannot parse.

        String literals are blanked first and comments are rejected outright
        so obfuscation attempts fail closed instead of slipping through.
        """
        stripped = _strip_sql_string_literals(query)
        query_upper = stripped.upper()

        if "--" in stripped or "/*" in stripped:
            return False, "SQL comments are not allowed"

        for keyword in self.forbidden_keywords:
            if re.search(r"\b" + re.escape(keyword) + r"\b", query_upper):
                return False, f"Forbidden keyword detected: {keyword}"

        if re.search(r"\bFROM\s+'", query_upper) or re.search(r"\bJOIN\s+'", query_upper):
            return False, "File-based table references are not allowed"

        for match in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*\(|\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', stripped):
            candidate = (match.group(1) or match.group(2) or "").upper()
            if self._is_forbidden_function(candidate):
                return False, f"Forbidden function detected: {candidate}"

        first_word = query_upper.split()[0] if query_upper.split() else ""
        if first_word not in ("SELECT", "WITH"):
            return False, f"Query must start with SELECT or WITH, got: {first_word}"

        # Schema-qualified references: the system catalogs are refused for
        # every caller, the other tenant schemas (text fallback for exotic
        # syntax) only for user-supplied SQL.
        for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
            stripped,
            re.IGNORECASE,
        ):
            db = match.group(1).lower()
            if db in _CATALOG_SCHEMAS:
                return False, f"Access denied to schema: {match.group(1)}"
            if not allow_system_tables:
                blocked = _blocked_schemas()
                if _schema_is_denied(match.group(1), blocked):
                    return False, f"Access denied to schema: {match.group(1)}"

        if not allow_system_tables:
            for table in _referenced_tables(query):
                if table.lower() in self._system_tables():
                    return False, f"Access denied to platform table: {table}"

        if ";" in stripped.rstrip(";").strip():
            return False, "Multiple statements not allowed"

        return True, None

    def validate_query(self, query: str, allow_system_tables: bool = False
                       ) -> tuple[bool, Optional[str]]:
        """
        Validate SQL query for safety.

        Uses sqlglot AST analysis when the query parses (authoritative),
        falling back to a hardened text scan for exotic-but-valid DuckDB
        syntax that sqlglot cannot parse.

        Returns:
            (is_valid, error_message)
        """
        ast_result = self._validate_ast(query, allow_system_tables)
        if ast_result is not None:
            return ast_result
        return self._validate_fallback(query, allow_system_tables)

    def execute(
        self,
        query: str,
        params: Optional[List] = None,
        limit: Optional[int] = None,
        user: Any = None,
    ) -> SQLResult:
        """
        Execute a SQL query safely.

        Args:
            query: SQL query string
            params: Query parameters
            limit: Override default row limit
            user: User object for RLS and audit logging

        Returns:
            SQLResult with data or error
        """
        start_time = time.perf_counter()
        original_query = query

        # Validate query
        is_valid, error = self.validate_query(query)
        if not is_valid:
            result = SQLResult(
                success=False,
                error=error,
                query=query,
            )
            # Audit failed validation
            if self.audit_logger and user:
                self.audit_logger.log_query(
                    user_id=user.user_id if hasattr(user, 'user_id') else "unknown",
                    query=query,
                    row_count=0,
                    execution_time_ms=0,
                    tenant_id=getattr(user, 'tenant_id', None),
                    result=AuditResult.FAILURE,
                    error=error,
                )
            return result

        # Apply RLS if enabled and user provided.
        # rewrite_query fails CLOSED (it raises rather than returning the
        # original query), so a tenant filter that cannot be applied refuses
        # the query instead of running it unfiltered.
        if self.rls_manager and user:
            query = self.rls_manager.rewrite_query(query, user)

        # Apply row limit
        effective_limit = limit or self.max_rows
        if "LIMIT" not in query.upper():
            query = f"{query.rstrip(';')} LIMIT {effective_limit}"

        try:
            from etl.readonly import readonly_transaction

            with self.get_connection() as conn:
                with readonly_transaction(conn):
                    if params:
                        result = conn.execute(query, params)
                    else:
                        result = conn.execute(query)

                    df = result.df()

            execution_time = (time.perf_counter() - start_time) * 1000

            sql_result = SQLResult(
                success=True,
                data=df,
                row_count=len(df),
                columns=list(df.columns) if not df.empty else [],
                query=query,
                execution_time_ms=execution_time,
            )

            # Audit successful query
            if self.audit_logger and user:
                self.audit_logger.log_query(
                    user_id=user.user_id if hasattr(user, 'user_id') else "unknown",
                    query=original_query,
                    row_count=len(df),
                    execution_time_ms=execution_time,
                    tenant_id=getattr(user, 'tenant_id', None),
                    result=AuditResult.SUCCESS,
                )

            return sql_result

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000
            logger.error(f"SQL execution failed: {e}")

            result = SQLResult(
                success=False,
                error=_sanitize_error(str(e)),
                query=query,
                execution_time_ms=execution_time,
            )

            # Audit failed execution
            if self.audit_logger and user:
                self.audit_logger.log_query(
                    user_id=user.user_id if hasattr(user, 'user_id') else "unknown",
                    query=original_query,
                    row_count=0,
                    execution_time_ms=execution_time,
                    tenant_id=getattr(user, 'tenant_id', None),
                    result=AuditResult.FAILURE,
                    error=str(e),
                )

            return result

    def execute_many(self, queries: List[str]) -> List[SQLResult]:
        """Execute multiple queries."""
        return [self.execute(q) for q in queries]

    def get_schema(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """Get database schema information (the caller's tenant schema)."""
        from api.tenancy import current_tenant_id, tenant_schema

        def _qi(v: object) -> str:
            return '"' + str(v).replace('"', '""') + '"'

        schema = tenant_schema(current_tenant_id())
        with self.get_connection() as conn:
            if table_name:
                # Single table. The name is resolved through the search path
                # (the tenant's schema comes first), so the caller's own copy
                # is the one that is described.
                cols = conn.execute(f"DESCRIBE {_qi(table_name)}").df()
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {_qi(table_name)}"
                ).fetchone()[0]
                return {
                    "table": table_name,
                    "columns": cols.to_dict("records"),
                    "row_count": count,
                }
            else:
                # All tables — only the caller's own tenant schema.
                tables = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = ?",
                    [schema],
                ).fetchall()

                schema_map = {}
                for (table,) in tables:
                    cols = conn.execute(f"DESCRIBE {_qi(table)}").df()
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM {_qi(table)}"
                    ).fetchone()[0]
                    schema_map[table] = {
                        "columns": cols.to_dict("records"),
                        "row_count": count,
                    }
                return schema_map

    def explain_query(self, query: str) -> str:
        """Get query execution plan."""
        with self.get_connection() as conn:
            result = conn.execute(f"EXPLAIN {query}").fetchall()
            return "\n".join(str(r[0]) for r in result)

    def sample_table(self, table_name: str, n: int = 10) -> pd.DataFrame:
        """Get sample rows from a table (resolved in the caller's schema)."""
        n = max(1, min(int(n or 10), 1000))
        from api.security_paths import validate_table_name
        try:
            validate_table_name(table_name)
        except Exception:  # noqa: BLE001
            return pd.DataFrame()
        safe = str(table_name).replace('"', '""')
        query = f'SELECT * FROM "{safe}" LIMIT {n}'
        result = self.execute(query)
        return result.data if result.success else pd.DataFrame()


def _referenced_tables(query: str) -> list[str]:
    """Best-effort table names after FROM/JOIN (quoted, schema-qualified ok).

    Conservative by design: unknown shapes are left alone here and handled
    by execution-time errors, but any name we CAN parse is checked.
    """
    tables: list[str] = []
    _ident = r'(?:"[^"]+"|\'[^\']+\'|[A-Za-z_][\w$]*)'
    for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+(" + _ident + r"(?:\s*\.\s*" + _ident + r")*)",
            query, re.IGNORECASE):
        dotted = match.group(1) or ""
        parts = [p.strip().strip('"').strip("'") for p in dotted.split(".")]
        name = parts[-1] if parts else ""
        if name:
            tables.append(name)
    return tables


class SQLToolOpenAI:
    """OpenAI function calling format for SQL tool."""

    @staticmethod
    def get_function_definition() -> Dict[str, Any]:
        """Get OpenAI function definition."""
        return {
            "type": "function",
            "function": {
                "name": "execute_sql",
                "description": "Execute a read-only SQL query against the DuckDB analytics warehouse. "
                               "Use for data retrieval, aggregations, and analysis. "
                               "Only SELECT and WITH statements are allowed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "SQL query to execute. Must be a SELECT or WITH statement.",
                        },
                        "params": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional query parameters",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum rows to return (default: 1000)",
                        },
                    },
                    "required": ["query"],
                },
            },
        }


# Convenience function
def execute_sql(
    query: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    max_rows: int = 1000,
    **kwargs,
) -> SQLResult:
    """Convenience function to execute SQL."""
    tool = SQLTool(db_path, schema, max_rows)
    return tool.execute(query, **kwargs)