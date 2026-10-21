"""P0: execution-surface hardening — sandbox escapes must fail closed.

Covers: SQL validator denies platform tables + file/URL functions,
/execute python blocks imports/builtins/file methods, context-table
validation rejects system tables for non-admins, legit analysis still works.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_limiter():
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


# ── SQL validator ───────────────────────────────────────────────────


def _tool() -> "SQLTool":
    from agent.tools_sql import SQLTool

    tool = SQLTool.__new__(SQLTool)
    tool.forbidden_keywords = SQLTool.FORBIDDEN_KEYWORDS
    tool.FORBIDDEN_FUNCTIONS = SQLTool.FORBIDDEN_FUNCTIONS
    return tool


def test_validator_denies_platform_tables():
    tool = _tool()
    for q in (
        "SELECT key_hash FROM analytics.api_keys",
        'SELECT * FROM "analytics"."api_keys"',
        "SELECT a FROM sales UNION SELECT key_hash FROM api_keys",
        "WITH x AS (SELECT 1 AS a) SELECT * FROM analytics.subscriptions",
    ):
        ok, err = tool.validate_query(q)
        assert ok is False, q
        assert "platform table" in (err or "").lower(), q


def test_validator_denies_file_and_url_functions():
    tool = _tool()
    for q in (
        "SELECT * FROM read_csv_auto('http://169.254.169.254/latest')",
        "SELECT * FROM read_parquet('s3://bucket/keys.parquet')",
        "SELECT * FROM parquet_scan('/etc/passwd')",
        "SELECT current_setting('memory_limit')",
    ):
        ok, _ = tool.validate_query(q)
        assert ok is False, q


def test_validator_denies_stacked_statement():
    tool = _tool()
    ok, _ = tool.validate_query("SELECT 1; DROP TABLE sales")
    assert ok is False
    # Pure stacked selects (no forbidden keyword) must fail on the ";" rule.
    ok, err = tool.validate_query("SELECT 1; SELECT 2")
    assert ok is False
    assert "multiple" in (err or "").lower()
    ok, _ = tool.validate_query("SELECT 1;")
    assert ok is True


def test_validator_allows_legit_queries():
    tool = _tool()
    for q in (
        "SELECT region, SUM(amount) FROM sales GROUP BY region",
        "WITH t AS (SELECT 1 AS a) SELECT * FROM t",
        "select * from analytics.sales_data limit 10",
    ):
        ok, err = tool.validate_query(q)
        assert ok is True, (q, err)


# ── /execute python sandbox ─────────────────────────────────────────


def _post_code(client, headers, code, language="python", context=None):
    body = {"code": code, "language": language}
    if context is not None:
        body["context"] = context
    return client.post("/execute", json=body, headers=headers)


def test_execute_blocks_imports_and_builtins():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()
    for code in (
        "import os\nprint('hi')",
        "from os import system\nprint('hi')",
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "x = getattr(__builtins__, 'eval')",
        "print((1).__class__.__base__.__subclasses__())",
        "import subprocess\nsubprocess.Popen('id', shell=True)",
        "import pandas as pd\npd.read_csv('http://169.254.169.254/')",
        "import numpy as np\nnp.load('/tmp/x.npy')",
        "df.to_csv('/tmp/exfil.csv')",
        "exec('print(1)')",
        "eval('1+1')",
        "compile('1', '', 'eval')",
        "import numpy as np\nnp.savetxt('/tmp/x', [1])",
        "x = np.memmap('/tmp/x', dtype='float64')",
        "np.array([1]).tofile('/tmp/x')",
        "df.to_html('/tmp/x.html')",
        "df.to_latex('/tmp/x.tex')",
        "os.system('id')",
        "x = __builtins__",
        "y = list(range(2000000))",
    ):
        r = _post_code(client, headers, code)
        assert r.status_code == 200, code
        body = r.json()
        # Blocked either statically ("Security: …") or by the restricted
        # builtins (NameError) — both mean no execution happened.
        assert body["success"] is False, (code, body)


def test_execute_rejects_oversize_code():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    big = "a = 1\n" * 5000  # >20k chars
    r = _post_code(client, auth_headers(), big)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "too large" in (body.get("error") or "").lower()


def test_execute_blocks_operator_bombs_but_allows_math():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()
    for code in (
        "x = 10**10\nprint(x)",
        "x = [0]*20000\nprint(len(x))",
        "x = list(range(200000))",
    ):
        r = _post_code(client, headers, code)
        assert r.status_code == 200, code
        assert r.json()["success"] is False, (code, r.json())
    # Legit column-style math (Name * Constant) must still run.
    r = _post_code(client, headers, "a = 6\nb = a * 7\nprint(b)")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True, body
    assert "42" in (body.get("output") or "")


def test_execute_legit_analysis_works():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = _post_code(client, auth_headers(),
                   "vals = [3, 1, 2]\nprint(sorted(vals))\nprint(sum(vals))")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True, body
    assert "[1, 2, 3]" in (body.get("output") or "")
    assert "6" in (body.get("output") or "")


def test_execute_context_system_table_denied_for_viewer():
    """A viewer must not read platform tables through the notebook sandbox.

    Two independent layers protect this:
    1. RBAC — the `viewer` role has no `query:execute` permission, so
       /execute is refused with 403 before the sandbox even runs.
    2. The sandbox's own system-table check — for roles that *do* have
       query:execute, the context table is still validated.
    """
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    viewer = auth_headers("sandbox_viewer", role="viewer")
    for table in ("api_keys", "analytics.api_keys"):
        r = _post_code(client, viewer, "print(df.shape)",
                       context={"table": table})
        # Layer 1: RBAC refuses the request outright.
        assert r.status_code in (200, 403), (table, r.status_code)
        if r.status_code == 403:
            continue
        body = r.json()
        assert body["success"] is False, (table, body)

    # Layer 2: a role that may execute still cannot read a platform table.
    analyst = auth_headers("sandbox_analyst", role="user")
    for table in ("api_keys", "analytics.api_keys"):
        r = _post_code(client, analyst, "print(df.shape)",
                       context={"table": table})
        assert r.status_code == 200, (table, r.status_code)
        body = r.json()
        assert body["success"] is False, (table, body)


def test_execute_context_injection_rejected():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = _post_code(client, auth_headers(), "print(1)",
                   context={"table": 'sales"; DROP TABLE sales; --'})
    assert r.status_code == 200
    assert r.json()["success"] is False
