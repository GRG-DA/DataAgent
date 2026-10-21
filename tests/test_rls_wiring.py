"""TDD (F5): RLS exists but was never wired into user-facing SQL paths.

`RLSEnabledSQLTool` was dead code and `SQLTool.execute` only applied
`rewrite_query` when a caller passed both `rls_manager` and `user` — no API
endpoint did. These tests pin the new `apply_rls` helper and its use by
`POST /sql` (the /bi/query, /execute, /tasks/run paths use the same helper).
"""

import duckdb
import pytest

from api.middleware.auth_middleware import User
from api.rls import RLSConfig, RLSManager, apply_rls


def _viewer(tenant_id=None):
    return User(user_id="u1", username="u1", role="viewer", tenant_id=tenant_id)


def _admin():
    return User(user_id="a1", username="a1", role="admin", tenant_id="t1")


@pytest.fixture()
def rls_enabled(monkeypatch):
    from api import rls as rls_mod

    manager = RLSManager(RLSConfig(enabled=True, default_tenant="default"))
    monkeypatch.setattr(rls_mod, "get_rls_manager", lambda: manager)
    return manager


# ── Unit: apply_rls ───────────────────────────────────────────────────────

def test_viewer_query_gets_tenant_filter(rls_enabled):
    out = apply_rls("SELECT product FROM sales", _viewer("t1"))
    assert "tenant_id" in out
    assert "t1" in out


def test_default_tenant_used_when_missing(rls_enabled):
    out = apply_rls("SELECT product FROM sales", _viewer(None))
    assert "'default'" in out


def test_existing_where_is_preserved(rls_enabled):
    out = apply_rls("SELECT * FROM sales WHERE amount > 10", _viewer("t1"))
    assert "amount > 10" in out
    assert "tenant_id" in out


def test_admin_bypasses_rls(rls_enabled):
    query = "SELECT product FROM sales"
    assert apply_rls(query, _admin()) == query


def test_disabled_rls_leaves_query_untouched(monkeypatch):
    from api import rls as rls_mod

    manager = RLSManager(RLSConfig(enabled=False))
    monkeypatch.setattr(rls_mod, "get_rls_manager", lambda: manager)
    query = "SELECT product FROM sales"
    assert apply_rls(query, _viewer("t1")) == query


def test_no_user_leaves_query_untouched(rls_enabled):
    query = "SELECT product FROM sales"
    assert apply_rls(query, None) == query


def test_tenant_id_with_quotes_is_escaped(rls_enabled):
    import sqlglot
    from sqlglot import exp

    tenant = "x' OR '1'='1"
    out = apply_rls("SELECT product FROM sales", _viewer(tenant))
    parsed = sqlglot.parse_one(out, dialect="duckdb")
    literals = [lit.this for lit in parsed.find_all(exp.Literal)]
    assert tenant in literals, out
    # The injected OR must live inside a string literal, not as SQL.
    assert "OR '1'='1'" not in out.replace(tenant, "")


# ── Integration: POST /sql applies RLS ────────────────────────────────────

def test_sql_endpoint_applies_rls(billing_db, rls_enabled, monkeypatch):
    from fastapi.testclient import TestClient

    main_mod = __import__("api.main", fromlist=["app"])
    monkeypatch.setattr(main_mod, "loader", billing_db)

    con = duckdb.connect(str(billing_db.db_path))
    con.execute("CREATE TABLE analytics.rls_sales (amount DOUBLE)")
    con.execute("INSERT INTO analytics.rls_sales VALUES (1.0)")
    con.close()

    client = TestClient(main_mod.app)
    from tests.conftest import auth_headers

    headers = auth_headers(
        username="rls_viewer", password="Str0ng!Passw0rd", role="user"
    )
    r = client.post(
        "/sql",
        json={"query": "SELECT amount FROM analytics.rls_sales"},
        headers=headers,
    )
    body = r.json()
    # The rewritten query references tenant_id, which rls_sales does not have:
    # execution must fail on the RLS filter, proving the rewrite was applied.
    assert body.get("success") is False, body
    assert "tenant_id" in (body.get("error") or "")


def test_sql_endpoint_without_rls_returns_rows(billing_db, monkeypatch):
    from fastapi.testclient import TestClient

    main_mod = __import__("api.main", fromlist=["app"])
    monkeypatch.setattr(main_mod, "loader", billing_db)

    con = duckdb.connect(str(billing_db.db_path))
    con.execute("CREATE TABLE analytics.rls_sales (amount DOUBLE)")
    con.execute("INSERT INTO analytics.rls_sales VALUES (1.0)")
    con.close()

    client = TestClient(main_mod.app)
    from tests.conftest import auth_headers

    headers = auth_headers(
        username="rls_plain", password="Str0ng!Passw0rd", role="admin"
    )
    r = client.post(
        "/sql",
        json={"query": "SELECT amount FROM analytics.rls_sales"},
        headers=headers,
    )
    body = r.json()
    assert body.get("success") is True, body
    assert body.get("row_count") == 1
