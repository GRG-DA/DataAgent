"""DuckDB MCP server for DataAgent.

Runs inside the isolated `.venv-mcp` (never the main app venv — see
`requirements.txt`). Opens the warehouse in READ-ONLY mode to avoid
DuckDB "file in use" conflicts on Windows (single-writer rule).

Env:
  DATAAGENT_DB_PATH  path to the DuckDB file to serve (default: warehouse).
"""

import json
import os
from pathlib import Path

import duckdb

try:  # package context (repo root on sys.path, e.g. tests)
    from mcp_servers.sql_guard import assert_read_only as _assert_read_only
except ImportError:  # run as a plain script (sys.path[0] = mcp_servers/)
    from sql_guard import assert_read_only as _assert_read_only

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "curated" / "warehouse.duckdb"
MAX_ROWS = 500


def _db_path() -> Path:
    """Resolve the warehouse path.

    A relative DATAAGENT_DB_PATH is resolved against the project root, not
    the server's working directory, so the MCP server works no matter where
    the client launched it from. Windows drive-relative paths such as
    ``C:data\\warehouse.duckdb`` are rejected: DuckDB parses them as
    drive-relative and resolves them against the wrong directory.
    """
    raw = os.environ.get("DATAAGENT_DB_PATH")
    if not raw:
        return DEFAULT_DB
    path = Path(raw)
    if path.is_absolute():
        return path
    if len(raw) > 1 and raw[1] == ":":
        raise ValueError(
            f"DATAAGENT_DB_PATH must be absolute or project-relative, got drive-relative {raw!r}"
        )
    return PROJECT_ROOT / path


def _connect():
    path = _db_path()
    if not path.exists():
        raise FileNotFoundError(f"Warehouse not found: {path}")
    return duckdb.connect(str(path), read_only=True)


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("duckdb")


@mcp.tool()
def query(sql: str, limit: int = MAX_ROWS) -> str:
    """Run a read-only SQL query against the warehouse and return rows as JSON.

    Only SELECT/WITH/VALUES/DESCRIBE/SHOW/EXPLAIN statements are allowed.
    Rows are capped at `limit` (default 500).
    """
    _assert_read_only(sql)
    limit = min(max(int(limit), 1), MAX_ROWS)
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
    truncated = len(rows) > limit
    return json.dumps(
        {"columns": cols, "rows": rows[:limit], "truncated": truncated, "count": min(len(rows), limit)},
        default=str,
        ensure_ascii=False,
    )


@mcp.tool()
def list_tables() -> str:
    """List all tables and views in the warehouse with their schema names."""
    with _connect() as conn:
        res = conn.execute(
            """SELECT table_schema, table_name, table_type
               FROM information_schema.tables
               ORDER BY table_schema, table_name"""
        ).fetchall()
    return json.dumps({"tables": [list(r) for r in res]}, default=str)


@mcp.tool()
def describe_table(table: str) -> str:
    """Describe the columns, types, and nullability of a table or view."""
    with _connect() as conn:
        quoted = '"' + table.replace('"', '""') + '"'
        res = conn.execute(f"DESCRIBE {quoted}").fetchall()
    return json.dumps({"columns": [list(r) for r in res]}, default=str)


if __name__ == "__main__":
    mcp.run(transport="stdio")