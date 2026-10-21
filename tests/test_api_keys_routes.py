"""API-keys routes — characterization tests locking the /auth/api-keys surface.

The 4 endpoints moved from the api/main.py monolith to
api/routes/api_keys.py with identical paths/methods/behavior. These tests
fail if a route is dropped, renamed, or re-gated in the move.

NOTE: api.api_keys.api_key_manager hardcodes its DuckDB path
("data/curated/warehouse.duckdb"), so the functional tests redirect
db_path at a tmp file — never the real warehouse.
"""

from __future__ import annotations

EXPECTED_API_KEY_ROUTES = {
    ("POST", "/auth/api-keys"),
    ("GET", "/auth/api-keys"),
    ("DELETE", "/auth/api-keys/{key_id}"),
    ("POST", "/auth/api-keys/{key_id}/rotate"),
}


def test_all_api_key_routes_registered_with_methods():
    from api.main import app
    from tests.route_helpers import route_methods

    found = route_methods(app, "/auth/api-keys")
    missing = EXPECTED_API_KEY_ROUTES - found
    assert not missing, f"api-key routes lost in the move: {sorted(missing)}"


def _real_user_id(username: str = "test_auth_user") -> str:
    from tests.conftest import ensure_user

    ensure_user(username, "Str0ng!Passw0rd")
    from api.auth import user_manager

    info = user_manager.get_user_info(username)
    assert info, f"test user {username} missing"
    return info["user_id"]


def _isolated_store(monkeypatch, tmp_path):
    """Point the singleton manager at a tmp DuckDB file (never the real warehouse)."""
    import duckdb

    from api.api_keys import DuckDBAPIKeyStore, api_key_manager

    db_path = str(tmp_path / "keys.duckdb")
    with duckdb.connect(db_path) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    monkeypatch.setattr(api_key_manager, "store", DuckDBAPIKeyStore(db_path))


def test_api_keys_free_user_is_gated(billing_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    _isolated_store(monkeypatch, tmp_path)
    _real_user_id()
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    assert client.get("/auth/api-keys", headers=auth_headers()).status_code == 403
    assert (
        client.post("/auth/api-keys", json={"name": "x"}, headers=auth_headers()).status_code
        == 403
    )


def test_api_keys_crud_roundtrip(billing_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    _isolated_store(monkeypatch, tmp_path)
    user_id = _real_user_id()
    ensure_billing_row(user_id, "lifetime", never_expires=True)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()

    assert (
        client.post("/auth/api-keys", json={}, headers=headers).status_code == 422
    )

    created = client.post(
        "/auth/api-keys", json={"name": "routemove-key"}, headers=headers
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["success"] is True
    assert body["api_key"]  # raw key returned once on create
    key_id = body["key_id"]

    listed = client.get("/auth/api-keys", headers=headers)
    assert listed.status_code == 200
    assert any(k.get("id") == key_id for k in listed.json()["keys"])

    rotated = client.post(f"/auth/api-keys/{key_id}/rotate", headers=headers)
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["api_key"]

    revoked = client.delete(f"/auth/api-keys/{key_id}", headers=headers)
    assert revoked.json() == {"success": True, "message": "API key revoked"}

    # Revoke is idempotent (re-revoke succeeds); unknown ids 404.
    assert (
        client.delete(f"/auth/api-keys/{key_id}", headers=headers).status_code == 200
    )
    assert (
        client.delete("/auth/api-keys/does-not-exist", headers=headers).status_code
        == 404
    )
    assert any(
        k.get("id") == rotated.json()["key_id"]
        for k in client.get("/auth/api-keys", headers=headers).json()["keys"]
    )
