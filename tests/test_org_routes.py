"""Org routes — characterization tests locking the /org/* surface.

The 20 org endpoints moved from the api/main.py monolith to
api/routes/org.py with identical paths/methods/behavior. These tests
fail if a route is dropped, renamed, or re-gated in the move.
"""

from __future__ import annotations

EXPECTED_ORG_ROUTES = {
    ("GET", "/org/tree"),
    ("GET", "/org/departments"),
    ("POST", "/org/departments"),
    ("GET", "/org/departments/{dept_id}"),
    ("PUT", "/org/departments/{dept_id}"),
    ("DELETE", "/org/departments/{dept_id}"),
    ("GET", "/org/teams"),
    ("POST", "/org/teams"),
    ("GET", "/org/teams/{team_id}"),
    ("PUT", "/org/teams/{team_id}"),
    ("DELETE", "/org/teams/{team_id}"),
    ("GET", "/org/groups"),
    ("POST", "/org/groups"),
    ("GET", "/org/groups/{group_id}"),
    ("PUT", "/org/groups/{group_id}"),
    ("DELETE", "/org/groups/{group_id}"),
    ("POST", "/org/groups/{group_id}/members"),
    ("DELETE", "/org/groups/{group_id}/members/{user_id}"),
    ("GET", "/org/groups/{group_id}/members"),
    ("GET", "/org/users/{user_id}/groups"),
}


def test_all_org_routes_registered_with_methods():
    from api.main import app
    from tests.route_helpers import route_methods

    found = route_methods(app, "/org/")
    missing = EXPECTED_ORG_ROUTES - found
    assert not missing, f"org routes lost in the move: {sorted(missing)}"


def _real_user_id(username: str = "test_auth_user") -> str:
    from tests.conftest import ensure_user

    ensure_user(username, "Str0ng!Passw0rd")
    from api.auth import user_manager

    info = user_manager.get_user_info(username)
    assert info, f"test user {username} missing"
    return info["user_id"]


def test_org_free_user_is_gated(billing_db):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    _real_user_id()
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = client.get("/org/tree", headers=auth_headers())
    assert resp.status_code == 403


def test_org_department_crud_roundtrip(billing_db):
    from fastapi.testclient import TestClient

    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    user_id = _real_user_id()
    ensure_billing_row(user_id, "lifetime", never_expires=True)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()

    created = client.post(
        "/org/departments", json={"name": "RouteMove QA"}, headers=headers
    )
    assert created.status_code == 200, created.text
    dept_id = created.json()["department"]["id"]

    listed = client.get("/org/departments", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == dept_id for d in listed.json()["departments"])

    fetched = client.get(f"/org/departments/{dept_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["department"]["id"] == dept_id

    updated = client.put(
        f"/org/departments/{dept_id}",
        json={"description": "moved-router-check"},
        headers=headers,
    )
    assert updated.json()["success"] is True

    tree = client.get("/org/tree", headers=headers)
    assert tree.status_code == 200
    assert tree.json()["success"] is True

    deleted = client.delete(f"/org/departments/{dept_id}", headers=headers)
    assert deleted.json()["success"] is True
    assert client.get(f"/org/departments/{dept_id}", headers=headers).json()["success"] is False


def test_org_teams_groups_smoke(billing_db):
    from fastapi.testclient import TestClient

    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    user_id = _real_user_id()
    ensure_billing_row(user_id, "lifetime", never_expires=True)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()

    assert client.get("/org/teams", headers=headers).json()["success"] is True
    group = client.post("/org/groups", json={"name": "RouteMove Group"}, headers=headers)
    assert group.status_code == 200, group.text
    group_id = group.json()["group"]["id"]
    members = client.get(f"/org/groups/{group_id}/members", headers=headers)
    assert members.json() == {"success": True, "members": []}
    assert client.delete(f"/org/groups/{group_id}", headers=headers).json()["success"] is True
