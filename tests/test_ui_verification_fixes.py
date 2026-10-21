"""Regression tests for the four issues found during UI verification.

1. /pipeline/diagnose crashed with NameError: PIPELINE_DIAGNOSE_PROMPT.
2. Free-plan dashboard quota drifted (counter never reconciled with reality).
3. GET/PUT /notifications/preferences 403'd in local mode (enterprise gate).
4. DELETE /tables wiped all user tables with no confirmation.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# 1. Pipeline diagnose prompt exists and endpoint no longer 500s on it
# ---------------------------------------------------------------------------

def test_pipeline_diagnose_prompt_constant_defined():
    """The missing constant was the root cause of the 500."""
    from api.main import PIPELINE_DIAGNOSE_PROMPT  # noqa: F401

    # The .format() placeholders used by diagnose_pipeline must be present.
    assert "{error}" in PIPELINE_DIAGNOSE_PROMPT
    assert "{pipeline_json}" in PIPELINE_DIAGNOSE_PROMPT
    assert "{schema_ctx}" in PIPELINE_DIAGNOSE_PROMPT
    assert "{available_tables}" in PIPELINE_DIAGNOSE_PROMPT
    # Literal JSON braces must be escaped for .format()
    PIPELINE_DIAGNOSE_PROMPT.format(
        error="e", pipeline_json="{}", schema_ctx="s", available_tables="a"
    )


def test_pipeline_diagnose_returns_dict_not_500(billing_db):
    """With no LLM orchestrator the endpoint degrades gracefully (no 500)."""
    from api.main import app
    from tests.conftest import auth_headers

    client = TestClient(
        app,
        raise_server_exceptions=True,
        headers=auth_headers("ui_diag_test", "Str0ng!Passw0rd", role="admin"),
    )
    resp = client.post(
        "/pipeline/diagnose",
        json={
            "error": "Column 'amounts' not found",
            "pipeline": {"steps": [{"type": "aggregate", "params": {"table": "sales"}}]},
            "available_tables": ["sales"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Either the LLM answered or the endpoint returned the graceful fallback.
    assert body.get("success") in (True, False)
    if body.get("success") is False:
        assert "error" in body


# ---------------------------------------------------------------------------
# 2. Dashboard quota self-heals when the counter drifts from reality
# ---------------------------------------------------------------------------

@pytest.fixture
def quota_env(billing_db, monkeypatch):
    """Metering env: a 'local' user on the free plan with 3-dash cap."""
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    return billing_db


def test_check_usage_reconciles_drifted_counter(quota_env, monkeypatch):
    """A usage counter higher than the real dashboard count is corrected."""
    from api.billing import metering
    from api.billing.schema import SCHEMA, get_connection
    from warehouse.system_tables import is_system_table  # noqa: F401

    user = "drift_user"

    # Create 1 real dashboard for the user.
    con = quota_env.get_connection().__enter__() if hasattr(quota_env, "get_connection") else None
    loader = quota_env
    loader.load_dataframe.__self__ if False else None
    import duckdb
    con = duckdb.connect(loader.db_path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(
        "CREATE TABLE IF NOT EXISTS analytics.dashboards ("
        " id VARCHAR, name VARCHAR, description VARCHAR, owner VARCHAR,"
        " owner_id VARCHAR, layout VARCHAR, widgets VARCHAR, filters VARCHAR,"
        " is_shared BOOLEAN DEFAULT FALSE, created_at TIMESTAMP, updated_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO analytics.dashboards (id, name, owner_id, created_at, updated_at) "
        "VALUES ('d1', 'Only Dash', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", [user]
    )
    con.close()

    # Seed a drifted usage record: says 3 used, reality is 1. Free limit = 3,
    # so before the fix this reported NOT allowed; after reconcile it must.
    period = metering.current_period()
    with get_connection() as con:
        con.execute(
            f"INSERT INTO {SCHEMA}.usage_records "
            "(id, user_id, subscription_id, metric, period, used, limit_snapshot) "
            "VALUES (?, ?, NULL, 'dashboards', ?, 3, NULL)",
            [__import__("uuid").uuid4(), user, period],
        )

    # Patch plan limit to the free-plan 3.
    monkeypatch.setattr(
        metering, "_plan_limit", lambda *a, **k: (3, None), raising=False
    )

    usage = metering.check_usage(user, "dashboards")
    assert usage["allowed"] is True, "counter said 3/3 but only 1 dashboard exists"
    assert usage["used"] == 1

    # And the stored record was corrected too.
    with get_connection() as con:
        row = con.execute(
            f"SELECT used FROM {SCHEMA}.usage_records "
            "WHERE user_id = ? AND metric = 'dashboards' AND period = ?",
            [user, period],
        ).fetchone()
    assert row[0] == 1


def test_check_usage_does_not_raise_counter(quota_env, monkeypatch):
    """Reconcile never inflates the counter above what the user really owns."""
    from api.billing import metering
    from api.billing.schema import SCHEMA, get_connection

    user = "low_user"
    import duckdb, uuid

    con = duckdb.connect(quota_env.db_path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(
        "CREATE TABLE IF NOT EXISTS analytics.dashboards ("
        " id VARCHAR, name VARCHAR, description VARCHAR, owner VARCHAR,"
        " owner_id VARCHAR, layout VARCHAR, widgets VARCHAR, filters VARCHAR,"
        " is_shared BOOLEAN DEFAULT FALSE, created_at TIMESTAMP, updated_at TIMESTAMP)"
    )
    # Zero real dashboards, but a stale counter at 1.
    period = metering.current_period()
    with get_connection() as con2:
        con2.execute(
            f"INSERT INTO {SCHEMA}.usage_records "
            "(id, user_id, subscription_id, metric, period, used, limit_snapshot) "
            "VALUES (?, ?, NULL, 'dashboards', ?, 1, NULL)",
            [str(uuid.uuid4()), user, period],
        )
    con.close()

    monkeypatch.setattr(
        metering, "_plan_limit", lambda *a, **k: (3, None), raising=False
    )
    usage = metering.check_usage(user, "dashboards")
    assert usage["used"] == 0
    assert usage["allowed"] is True


# ---------------------------------------------------------------------------
# 3. Notification preferences: authenticated access, not enterprise-only
# ---------------------------------------------------------------------------

def _make_local_user_client(app, monkeypatch):
    """TestClient with the auth dependency overridden to a local user.

    Also present a Bearer JWT so AuthMiddleware accepts the request
    (local-mode auto-admin was removed in Phase 1 hardening)."""
    from api.dependencies import get_current_user
    from tests.conftest import auth_headers

    class _User:
        user_id = "local"
        username = "local"
        plan = "free"
        role = "admin"

    app.dependency_overrides[get_current_user] = lambda: _User()
    return TestClient(
        app,
        headers=auth_headers("ui_notif_test", "Str0ng!Passw0rd", role="admin"),
    )


def test_notification_preferences_not_enterprise_gated(billing_db, monkeypatch):
    """Local (non-enterprise) users can read and update their preferences."""
    from api.main import app

    monkeypatch.setenv("DATAGENT_LOCAL_MODE", "1")
    client = _make_local_user_client(app, monkeypatch)
    resp = client.get("/notifications/preferences")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("success") in (True, False)  # handler ran, no 403

    resp = client.put(
        "/notifications/preferences", json={"channel": "email", "digest": True}
    )
    assert resp.status_code == 200, resp.text
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. DELETE /tables requires explicit confirmation
# ---------------------------------------------------------------------------

def test_drop_all_tables_requires_confirmation(billing_db):
    from api.main import app
    from tests.conftest import auth_headers

    client = TestClient(
        app, headers=auth_headers("ui_drop_test", "Str0ng!Passw0rd", role="admin")
    )
    resp = client.request("DELETE", "/tables")
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"].lower()


def test_drop_all_tables_with_confirmation_runs(billing_db, monkeypatch):
    from api.main import app
    import api.main as apimod
    from tests.conftest import auth_headers

    class _StubLoader:
        def drop_all_tables(self):
            return 0

    monkeypatch.setattr(apimod, "loader", _StubLoader())
    client = TestClient(
        app, headers=auth_headers("ui_drop_test", "Str0ng!Passw0rd", role="admin")
    )
    resp = client.request("DELETE", "/tables", json={"confirm": True})
    # The endpoint must accept the confirmed call (loader may drop 0 tables in
    # a fresh test warehouse — that's fine; the guard is what we're testing).
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "dropped" in body


def test_drop_single_table_still_works_without_body(billing_db, monkeypatch):
    """DELETE /tables/{name} keeps its simple contract."""
    from api.main import app
    import api.main as apimod
    from tests.conftest import auth_headers

    class _StubLoader:
        def table_exists(self, name):
            return False

    monkeypatch.setattr(apimod, "loader", _StubLoader())
    client = TestClient(
        app, headers=auth_headers("ui_drop_test", "Str0ng!Passw0rd", role="admin")
    )
    # Nonexistent table → 404 (not 400), proving no confirmation was demanded.
    resp = client.request("DELETE", "/tables/definitely_not_a_table")
    assert resp.status_code == 404
