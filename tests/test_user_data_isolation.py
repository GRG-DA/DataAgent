"""Per-user data isolation — RED tests.

A second user must never see another user's chats, messages, or query
history. Previously list endpoints returned `user_id = ? OR user_id = ''`
(anonymous rows visible to everyone) and the SPA kept browser history in
unscoped localStorage keys shared across logins on one machine.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "js"


def _ensure_user(username: str, role: str = "viewer") -> dict:
    from api.auth import user_manager

    if not user_manager.get_user_info(username):
        assert user_manager.add_user(username, "Str0ng!Passw0rd!x", role)
    return user_manager.get_user_info(username) or {}


def _headers(username: str) -> dict:
    from api.auth import create_jwt_token

    _ensure_user(username)
    return {"Authorization": f"Bearer {create_jwt_token(username, role='viewer')}"}


def test_conversations_are_private_to_owner():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    ha, hb = _headers("iso_owner_a"), _headers("iso_owner_b")

    conv = client.post("/user/conversations", json={"title": "iso A private"},
                       headers=ha).json()
    conv_id = conv["id"]
    client.post(f"/user/conversations/{conv_id}/messages",
                json={"role": "user", "content": "hello a"}, headers=ha)
    client.post("/user/query-history",
                json={"query": "iso A q", "sql": "SELECT 1"}, headers=ha)
    try:
        blist = client.get("/user/conversations", headers=hb).json()["conversations"]
        assert all(c["id"] != conv_id for c in blist)
        bhist = client.get("/user/query-history", headers=hb).json()["history"]
        assert all(h["query"] != "iso A q" for h in bhist)

        assert client.get(f"/user/conversations/{conv_id}", headers=hb).status_code == 403
        assert client.post(f"/user/conversations/{conv_id}/messages",
                           json={"role": "user", "content": "hi-jack"},
                           headers=hb).status_code == 403
        assert client.put(f"/user/conversations/{conv_id}",
                          json={"title": "hijacked"},
                          headers=hb).status_code == 403
        assert client.delete(f"/user/conversations/{conv_id}",
                             headers=hb).status_code == 403
        # Owner still sees everything.
        assert client.get(f"/user/conversations/{conv_id}", headers=ha).status_code == 200
    finally:
        client.delete(f"/user/conversations/{conv_id}", headers=ha)


def test_clear_history_only_deletes_own_rows():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    ha, hb = _headers("iso_owner_a"), _headers("iso_owner_b")
    client.post("/user/query-history",
                json={"query": "iso A keep me", "sql": ""}, headers=ha)
    client.delete("/user/query-history", headers=hb)
    mine = [h["query"] for h in
            client.get("/user/query-history", headers=ha).json()["history"]]
    assert "iso A keep me" in mine


def test_list_endpoints_do_not_fall_back_to_anonymous_rows():
    """Guard the exact regression: no `OR user_id = ''` in list queries."""
    src = (ROOT / "api" / "user_data.py").read_text(encoding="utf-8")
    assert "OR user_id = ''" not in src


def test_browser_history_keys_are_scoped_per_user():
    """SPA browser caches must be namespaced so switching users on one
    machine cannot surface the previous user's chats/queries."""
    rbac = (WEB / "components" / "rbac.js").read_text(encoding="utf-8")
    assert "storeKey" in rbac.split("window.RBAC =")[-1]
    for rel in ("pages/chat.js", "pages/sql.js", "pages/dashboard.js",
                "pages/blankdash.js", "pages/dashboardStudio.js"):
        src = (WEB / rel).read_text(encoding="utf-8")
        assert "storeKey" in src, f"{rel} must namespace its browser keys"
