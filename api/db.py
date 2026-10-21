"""Shared DuckDB connection for dashboard CRUD and .pbix export.

One read-write connection to ``warehouse.duckdb`` is kept open for the whole
process and reused by every dashboard request and the export pipeline. The old
code opened a NEW ``duckdb.connect`` on every request, which collided with the
app's own open handle (and with uvicorn ``--reload``'s parent process), causing
intermittent ``IO Error: Cannot open file ... used by another process`` 500s on
Windows.

In-process, DuckDB allows multiple connections to the same file, but opening
and closing a fresh connection per request multiplies the collision windows and
is wasteful. A single process-wide connection is both faster and avoids the
"file in use" class of bugs for dashboard CRUD.
"""

import logging
import threading

import duckdb

from config import settings

logger = logging.getLogger(__name__)

_conn: "duckdb.DuckDBPyConnection | None" = None
_conn_path: str = ""

# Serializes execute()+fetchdf() on the shared connection. The process-wide
# connection is NOT thread-safe: two concurrent requests running
# ``conn.execute(q1).fetchdf()`` and ``conn.execute(q2).fetchdf()`` can step
# each other's pending result, so one fetchdf() returns None ("'NoneType'
# object has no attribute 'columns'"). Every handler that reads the shared
# connection must hold this lock for the whole execute+fetch window.
#
# It is re-entrant because the billing layer opens the shared connection
# from inside another shared-connection block (a plan lookup that falls
# back to the entitlement resolver), and a plain Lock would deadlock there.
_bi_query_lock = threading.RLock()


def get_connection() -> "duckdb.DuckDBPyConnection":
    """Return the process-wide warehouse connection (opens it once).

    The connection follows ``settings.duckdb.database_path``: when that
    path changes (a test fixture points it at a throwaway file, a
    per-tenant deployment at another file) the stale handle is closed and
    the new one opened, so no caller ever reads a database the process is
    no longer configured for.
    """
    global _conn, _conn_path
    path = str(settings.duckdb.database_path)
    if _conn is not None and _conn_path != path:
        logger.info(
            "The configured warehouse changed from %s to %s — reopening the "
            "shared connection", _conn_path, path,
        )
        close_connection()
    if _conn is None:
        _conn = duckdb.connect(path)
        try:
            # The platform schema must exist before the search path can
            # point at it (a brand new warehouse has no `analytics`).
            _conn.execute(f"CREATE SCHEMA IF NOT EXISTS {settings.duckdb.schema}")
            _conn.execute(f"SET search_path = '{settings.duckdb.schema}'")
        except Exception as exc:  # noqa: BLE001 — never brick the app
            logger.warning("Could not prepare the shared connection: %s", exc)
        # No interactive progress bar in a server process.
        _conn.execute("PRAGMA enable_progress_bar=false")
        _conn_path = path
        logger.info("Opened shared DuckDB connection to %s", path)
    return _conn


def close_connection() -> None:
    """Close the shared connection (used on app shutdown / in tests)."""
    global _conn, _conn_path
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    _conn_path = ""


def _rebuild_table(
    conn: "duckdb.DuckDBPyConnection",
    schema: str,
    table: str,
    index_sql: list[str] | None = None,
) -> bool:
    """Copy/DROP/RENAME rebuild of one table so its heap + ART indexes are
    recreated fresh. Preserves column definitions exactly and re-applies any
    secondary index DDL (``index_sql``) afterwards.

    Returns True when the table was rebuilt, False when it could not be (e.g. it
    no longer exists or has no columns).
    """
    cols = conn.execute(
        f"""SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            ORDER BY ordinal_position"""
    ).fetchall()
    if not cols:
        return False
    defs = []
    for cname, ctype, nullable, default in cols:
        part = f'"{cname}" {ctype}'
        if default is not None:
            part += f" DEFAULT {default}"
        if nullable == "NO":
            part += " NOT NULL"
        defs.append(part)
    tmp = f"{table}__npk"
    conn.execute(f'DROP TABLE IF EXISTS {schema}.{tmp}')
    conn.execute(f'CREATE TABLE {schema}.{tmp} ({", ".join(defs)})')
    conn.execute(f'INSERT INTO {schema}.{tmp} SELECT * FROM {schema}."{table}"')
    conn.execute(f'DROP TABLE {schema}."{table}"')
    conn.execute(f'ALTER TABLE {schema}.{tmp} RENAME TO "{table}"')
    if index_sql:
        for stmt in index_sql:
            try:
                conn.execute(stmt)
            except Exception as e:  # noqa: BLE001
                logger.warning("Index recreation failed for %s: %s", table, e)
    return True


def strip_constraint_indexes(db_path: str | None = None, schema: str = "analytics") -> int:
    """Drop PRIMARY KEY / UNIQUE indexes so row DELETEs never take the ART-index
    path while other connections are open.

    A shared process-wide connection is always alive. DuckDB's indexed DELETE /
    INSERT OR REPLACE then go through the PK/UNIQUE ART index, and under ANY
    second open connection to the same file the delete fails with a FATAL
    ``Invalid Input Error: Failed to delete all rows from index. Only deleted 0
    out of N rows.`` which also invalidates the database file for every other
    connection. Rebuilding those tables without the constraints makes deletes
    heap-scan based and safe. Nothing in the codebase relies on upsert semantics
    (no INSERT OR REPLACE / ON CONFLICT), so the constraints are pure liability.

    Idempotent: tables with no PK/UNIQUE constraints are left untouched.
    Returns the number of tables rebuilt (0 when already clean).
    """
    path = db_path or str(settings.duckdb.database_path)
    rebuilt = 0
    conn = duckdb.connect(path)
    try:
        conn.execute(f"SET search_path = '{schema}'")
        affected = conn.execute(
            f"""SELECT table_name, constraint_type
                FROM duckdb_constraints()
                WHERE schema_name = '{schema}'
                  AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
                GROUP BY 1, 2
                ORDER BY 1"""
        ).fetchall()
        tables = sorted({r[0] for r in affected})
        for table in tables:
            if _rebuild_table(conn, schema, table):
                rebuilt += 1
                logger.info(
                    "Stripped PK/UNIQUE constraints from analytics.%s (indexed-DELETE safety)",
                    table,
                )
    except Exception as e:  # noqa: BLE001 — never let a migration break app boot
        logger.warning("Constraint-index migration failed for %s: %s", schema, e)
    finally:
        conn.close()
    return rebuilt


def heal_indexed_tables(db_path: str | None = None, schema: str = "analytics",
                        max_rows: int = 50000) -> int:
    """Rebuild tables that carry secondary (non-PK/UNIQUE) ART indexes.

    ``strip_constraint_indexes`` only looks at ``duckdb_constraints()`` and
    thereby misses tables whose PK/UNIQUE was already stripped but which still
    hold *secondary* ART indexes (e.g. ``analytics.catalog_assets`` keeps
    ``idx_catalog_assets_{table,domain,cert}``). A dangling/heap-inconsistent
    entry in one of those secondary indexes makes ANY DELETE/UPDATE that touches
    an indexed column FATAL with

        Invalid Input Error: Failed to delete all rows from index.
        Only deleted 0 out of N rows.

    — and DuckDB then invalidates the entire database for every connection until
    restart, so the error recurs on each boot (the ``calendar`` dimension UPDATE
    at startup was the trigger here).

    A full copy/DROP/RENAME rebuild writes a fresh heap + fresh ART indexes and
    permanently heals the file. Because the app recreates its secondary indexes
    with ``CREATE INDEX IF NOT EXISTS`` on boot, this is idempotent and
    self-healing. Large tables (>= ``max_rows``) are left alone to avoid an
    expensive rebuild on boot.

    Returns the number of tables rebuilt.
    """
    path = db_path or str(settings.duckdb.database_path)
    rebuilt = 0
    conn = duckdb.connect(path)
    try:
        conn.execute(f"SET search_path = '{schema}'")
        indexed = conn.execute(
            f"""SELECT DISTINCT table_name
                FROM duckdb_indexes()
                WHERE schema_name = '{schema}' AND NOT is_primary
                ORDER BY 1"""
        ).fetchall()
        for (table,) in indexed:
            try:
                nrows = conn.execute(f'SELECT COUNT(*) FROM {schema}."{table}"').fetchone()[0]
            except Exception:  # noqa: BLE001
                continue
            if nrows >= max_rows:
                logger.info("Skipping index heal for %s.%s (%d rows)", schema, table, nrows)
                continue
            idx_sql = [
                r[0]
                for r in conn.execute(
                    f"""SELECT sql FROM duckdb_indexes()
                        WHERE schema_name = '{schema}' AND table_name = '{table}'
                          AND NOT is_primary AND sql IS NOT NULL"""
                ).fetchall()
            ]
            if _rebuild_table(conn, schema, table, index_sql=idx_sql):
                rebuilt += 1
                logger.info("Rebuilt %s.%s secondary ART indexes (dangling-index heal)", schema, table)
    except Exception as e:  # noqa: BLE001 — never let a migration break app boot
        logger.warning("Index-heal migration failed for %s: %s", schema, e)
    finally:
        conn.close()
    return rebuilt
