"""TDD (F2/F3): SQLTool.validate_query must block file-read functions,
comment-obfuscated system tables, literal-table reads and statement forms
beyond a single SELECT.

Audit PoCs that must stay blocked:
- ``SELECT * FROM read_text('data/.jwt_secret')`` (local file read)
- ``SELECT * FROM/**/api_keys`` (comment bypassed the FROM/JOIN regex)
- stacked statements / INSTALL / LOAD / CALL were not keyword-covered.
"""

import duckdb
import pytest

from agent.tools_sql import SQLTool


@pytest.fixture()
def tool(tmp_path):
    return SQLTool(db_path=tmp_path / "validator.duckdb", schema="analytics")


# ── F2: file / network / env functions ────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM read_text('C:/Windows/win.ini')",
        "SELECT content FROM read_text('data/.jwt_secret') AS content",
        "SELECT * FROM read_blob('C:/Users/secret.png')",
        "SELECT * FROM glob('C:/Users/*')",
        "SELECT * FROM read_json_auto('x.json')",
        "SELECT * FROM read_ndjson('x.ndjson')",
        "SELECT * FROM sniff_csv('x.csv')",
        "SELECT * FROM parquet_metadata('x.parquet')",
        "SELECT * FROM parquet_schema('x.parquet')",
        "SELECT getenv('OPENAI_API_KEY')",
        "SELECT * FROM query_table('api_keys')",
        'SELECT "read_text"(\'x\')',
        "SELECT main.read_text('x')",
        "SELECT * FROM read_csv_auto('http://169.254.169.254/latest/meta-data/')",
    ],
)
def test_file_and_network_functions_rejected(tool, query):
    ok, err = tool.validate_query(query)
    assert not ok, query
    assert err


# ── F3: system tables (incl. comment / paren obfuscation) ─────────────────

@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM api_keys",
        "SELECT * FROM/**/api_keys",
        "SELECT * FROM /*x*/ api_keys",
        "SELECT a.* FROM /*x*/ api_keys a",
        "SELECT * FROM (api_keys)",
        'SELECT * FROM "api_keys"',
        "SELECT * FROM analytics.api_keys",
        "SELECT * FROM ANALYTICS.API_KEYS",
        "WITH k AS (SELECT * FROM api_keys) SELECT * FROM k",
    ],
)
def test_system_tables_rejected(tool, query):
    ok, err = tool.validate_query(query)
    assert not ok, query


def test_system_tables_allowed_when_explicit(tool):
    ok, err = tool.validate_query("SELECT * FROM api_keys", allow_system_tables=True)
    assert ok, err


# ── Literal-table file reads ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM 'x.csv'",
        "SELECT * FROM 'C:/Windows/win.ini'",
        "SELECT * FROM sales JOIN 'x.parquet' ON 1=1",
    ],
)
def test_literal_table_reads_rejected(tool, query):
    ok, err = tool.validate_query(query)
    assert not ok, query


# ── Non-select statement forms ────────────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE analytics.sales",
        "INSERT INTO analytics.sales VALUES (1)",
        "UPDATE analytics.sales SET amount = 0",
        "DELETE FROM analytics.sales",
        "CREATE TABLE analytics.x AS SELECT 1",
        "ALTER TABLE analytics.sales ADD COLUMN x INT",
        "INSTALL httpfs",
        "LOAD httpfs",
        "CALL pragma_show_tables()",
        "PRAGMA show_tables",
        "SET threads = 1",
        "ATTACH 'other.duckdb'",
        "EXPLAIN SELECT 1",
        "SUMMARIZE analytics.sales",
        "SELECT 1; DROP TABLE analytics.sales",
        "SELECT 1; INSTALL httpfs",
        "SELECT 1; SELECT 2",
        "SELECT * INTO analytics.copy FROM analytics.sales",
    ],
)
def test_unsafe_statements_rejected(tool, query):
    ok, err = tool.validate_query(query)
    assert not ok, query


# ── Legitimate analytics SQL keeps working ────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "SELECT product, SUM(amount) FROM sales GROUP BY product ORDER BY 2 DESC LIMIT 10",
        "WITH t AS (SELECT * FROM sales) SELECT COUNT(*) AS n FROM t",
        "SELECT a FROM sales WHERE note = '--not a comment' AND flag = '/*'",
        "SELECT * FROM analytics.sales QUALIFY row_number() OVER (PARTITION BY product) = 1",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT * FROM sales PIVOT (SUM(amount) FOR product IN ('a'))",
        "SELECT * FROM sales;",
        "SELECT * FROM UNNEST([1, 2, 3]) AS t(x)",
        "SELECT * FROM generate_series(1, 3)",
        "SELECT s.product FROM sales s JOIN sales r ON s.product = r.product",
        "SELECT CASE WHEN amount > 1 THEN 'big' ELSE 'small' END AS size FROM sales",
        "SELECT CAST(amount AS DOUBLE) FROM sales",
    ],
)
def test_legit_queries_allowed(tool, query):
    ok, err = tool.validate_query(query)
    assert ok, f"{query} -> {err}"


# ── Execution smoke test (table + happy path) ─────────────────────────────

def test_execute_still_works_after_hardening(tmp_path):
    db_path = tmp_path / "exec.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA analytics")
    con.execute("CREATE TABLE analytics.sales (product VARCHAR, amount INTEGER)")
    con.execute("INSERT INTO analytics.sales VALUES ('a', 1), ('b', 2)")
    con.close()

    tool = SQLTool(db_path=db_path, schema="analytics")
    result = tool.execute("SELECT product, amount FROM sales ORDER BY amount")
    assert result.success, result.error
    assert result.row_count == 2


def test_execute_rejects_file_read(tmp_path):
    tool = SQLTool(db_path=tmp_path / "exec2.duckdb", schema="analytics")
    result = tool.execute("SELECT * FROM read_text('C:/Windows/win.ini')")
    assert not result.success
    assert result.error
