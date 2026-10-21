"""Enterprise routes — characterization tests locking the /sla/* + /cost/* surface.

The 16 endpoints moved from the api/main.py monolith to
api/routes/enterprise.py with identical paths/methods/behavior. These tests
fail if a route is dropped, renamed, or re-gated in the move.

Manager singletons are swapped for tmp-DB instances — never the real warehouse.
"""

from __future__ import annotations

EXPECTED_ENTERPRISE_ROUTES = {
    ("GET", "/sla/definitions"),
    ("POST", "/sla/definitions"),
    ("PUT", "/sla/definitions/{def_id}"),
    ("DELETE", "/sla/definitions/{def_id}"),
    ("GET", "/sla/status"),
    ("GET", "/sla/breaches"),
    ("POST", "/sla/check/{def_id}"),
    ("POST", "/sla/monitor/start"),
    ("POST", "/cost/record"),
    ("GET", "/cost/queries"),
    ("GET", "/cost/summary"),
    ("POST", "/cost/budgets"),
    ("GET", "/cost/budgets"),
    ("GET", "/cost/budgets/status"),
    ("PUT", "/cost/budgets/{budget_id}"),
    ("DELETE", "/cost/budgets/{budget_id}"),
}


def test_all_enterprise_routes_registered_with_methods():
    from api.main import app
    from tests.route_helpers import route_methods

    found = route_methods(app, "/sla/") | route_methods(app, "/cost/")
    missing = EXPECTED_ENTERPRISE_ROUTES - found
    assert not missing, f"enterprise routes lost in the move: {sorted(missing)}"


def _real_user_id(username: str = "test_auth_user") -> str:
    from tests.conftest import ensure_user

    ensure_user(username, "Str0ng!Passw0rd")
    from api.auth import user_manager

    info = user_manager.get_user_info(username)
    assert info, f"test user {username} missing"
    return info["user_id"]


def _isolated_managers(monkeypatch, tmp_path):
    """Swap SLA + cost singletons for tmp-DB instances (never the real warehouse)."""
    import duckdb

    from config import settings as _settings

    # Module singletons construct against the settings DB at import time;
    # ensure its schema exists first so the import cannot fail.
    with duckdb.connect(_settings.duckdb.database_path) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    import api.cost_usage as cost_mod
    import api.sla as sla_mod

    for name in ("sla.duckdb", "cost.duckdb"):
        with duckdb.connect(str(tmp_path / name)) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    monkeypatch.setattr(
        sla_mod, "sla_manager", sla_mod.SLAManager(db_path=str(tmp_path / "sla.duckdb"))
    )
    monkeypatch.setattr(
        cost_mod,
        "cost_usage_manager",
        cost_mod.CostUsageManager(db_path=str(tmp_path / "cost.duckdb")),
    )


def test_enterprise_free_user_is_gated(billing_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    _isolated_managers(monkeypatch, tmp_path)
    _real_user_id()
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    assert client.get("/sla/definitions", headers=auth_headers()).status_code == 403
    assert client.get("/cost/summary", headers=auth_headers()).status_code == 403
    assert (
        client.post("/cost/budgets", json={"name": "x"}, headers=auth_headers()).status_code
        == 403
    )


def test_sla_crud_roundtrip(billing_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    _isolated_managers(monkeypatch, tmp_path)
    user_id = _real_user_id()
    ensure_billing_row(user_id, "lifetime", never_expires=True)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()

    created = client.post(
        "/sla/definitions",
        json={"name": "RouteMove SLA", "table_name": "sales"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    def_id = created.json()["definition"]["id"]

    assert client.get("/sla/definitions", headers=headers).json()["success"] is True
    assert client.get("/sla/status", headers=headers).json()["success"] is True
    assert client.get("/sla/breaches", headers=headers).json()["success"] is True

    checked = client.post(f"/sla/check/{def_id}", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["success"] is True

    assert (
        client.put(f"/sla/definitions/{def_id}", json={"severity": "critical"}, headers=headers).json()["success"]
        is True
    )
    assert (
        client.delete(f"/sla/definitions/{def_id}", headers=headers).json()["success"]
        is True
    )


def test_cost_crud_roundtrip(billing_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    _isolated_managers(monkeypatch, tmp_path)
    user_id = _real_user_id()
    ensure_billing_row(user_id, "lifetime", never_expires=True)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()

    recorded = client.post(
        "/cost/record",
        json={"user_id": user_id, "query_text": "SELECT 1", "duration_ms": 12.5},
        headers=headers,
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["success"] is True

    queries = client.get("/cost/queries", headers=headers)
    assert queries.status_code == 200
    assert queries.json()["total"] >= 1

    assert client.get("/cost/summary", headers=headers).json()["success"] is True

    budget = client.post(
        "/cost/budgets", json={"name": "RouteMove Budget"}, headers=headers
    )
    assert budget.status_code == 200, budget.text
    budget_id = budget.json()["budget"]["id"]

    assert client.get("/cost/budgets", headers=headers).json()["success"] is True
    assert client.get("/cost/budgets/status", headers=headers).json()["success"] is True
    assert (
        client.put(f"/cost/budgets/{budget_id}", json={"limit_amount": 500.0}, headers=headers).json()["success"]
        is True
    )
    assert (
        client.delete(f"/cost/budgets/{budget_id}", headers=headers).json()["success"]
        is True
    )
