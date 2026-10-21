"""TDD (F6): the duckdb MCP server guard only checked the first keyword,
so `SELECT * FROM read_text('...')` read local files and stacked statements
were not rejected (they only failed because the connection is read-only).

The guard lives in `mcp_servers/sql_guard.py` (pure stdlib, importable from
the main venv — the `mcp` package is not installed there).
"""

import pytest

from mcp_servers.sql_guard import assert_read_only


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_text('C:/Windows/win.ini')",
        "SELECT * FROM read_blob('x')",
        "SELECT * FROM read_csv_auto('x.csv')",
        "SELECT * FROM read_parquet('x.parquet')",
        "SELECT * FROM read_json_auto('x.json')",
        "SELECT * FROM glob('C:/Users/*')",
        "SELECT * FROM sniff_csv('x.csv')",
        "SELECT getenv('TOKEN')",
        "SELECT * FROM query_table('api_keys')",
        'SELECT "read_text"(\'x\')',
        "SELECT main.read_text('x')",
        "SELECT * FROM 'x.csv'",
        "SELECT * FROM sales JOIN 'x.parquet' ON 1=1",
        "SELECT 1; DROP TABLE x",
        "SELECT 1; INSTALL httpfs",
        "DROP TABLE x",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "CREATE TABLE t AS SELECT 1",
        "ATTACH 'x.db'",
        "COPY t TO 'x.csv'",
        "",
        "   ",
    ],
)
def test_unsafe_sql_rejected(sql):
    with pytest.raises(ValueError):
        assert_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sales",
        "SELECT * FROM analytics.sales LIMIT 10",
        "WITH t AS (SELECT 1 AS a) SELECT * FROM t",
        "VALUES (1), (2)",
        "DESCRIBE analytics.sales",
        "SHOW TABLES",
        "EXPLAIN SELECT 1",
        "SELECT ';' AS semi",
        "SELECT * FROM sales WHERE note = 'read_text(x)'",
        "SELECT * FROM sales;",
        "SELECT * FROM sales -- trailing comment",
        "FROM sales SELECT product",
    ],
)
def test_safe_sql_allowed(sql):
    assert assert_read_only(sql) is None
