"""Phase 1 auth hardening — RED tests.

Guarantees:
1. Local mode no longer auto-injects an admin; non-public routes require auth.
2. Public path allowlist no longer exposes data/query/admin surfaces.
3. GET /auth/users requires admin.
4. Upload/clean/load reject path-traversal filenames.
5. JWTs carry a jti and logout revokes them.
6. API keys are accepted only via header, never query string.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient


# ── helpers ───────────────────────────────────────────────────────────


def _auth_enforced_flags_off(monkeypatch) -> None:
    from config import settings

    monkeypatch.setattr(settings.sso, "enabled", False)
    monkeypatch.setattr(settings.security, "api_key_enabled", False)


def _auth_enforced_flags_on(monkeypatch) -> None:
    from config import settings

    monkeypatch.setattr(settings.sso, "enabled", False)
    monkeypatch.setattr(settings.security, "api_key_enabled", True)


def _middleware_app() -> FastAPI:
    """Minimal app that only depends on AuthMiddleware + get_current_user."""
    from api.middleware.auth_middleware import AuthMiddleware, get_current_user

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/protected")
    async def protected(user=Depends(get_current_user)):
        return {"username": user.username, "role": user.role}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def _ensure_user(username: str, password: str, role: str = "admin") -> dict:
    from api.auth import user_manager

    if not user_manager.get_user_info(username):
        user_manager.add_user(username, password, role)
    return user_manager.get_user_info(username) or {}


def _login_headers(username: str, password: str) -> dict:
    from api.auth import create_jwt_token

    _ensure_user(username, password, role="admin")
    token = create_jwt_token(username, role="admin")
    return {"Authorization": f"Bearer {token}"}


# ── 1. local mode: no auto-admin ─────────────────────────────────────


def test_local_mode_rejects_unauthenticated_protected_request(monkeypatch):
    """Require auth always: local mode must 401, not inject admin."""
    _auth_enforced_flags_off(monkeypatch)
    client = TestClient(_middleware_app())
    res = client.get("/protected")
    assert res.status_code == 401


def test_local_mode_allows_valid_jwt(monkeypatch):
    _auth_enforced_flags_off(monkeypatch)
    client = TestClient(_middleware_app())
    headers = _login_headers("hard_local_jwt", "Str0ng!Passw0rd")
    res = client.get("/protected", headers=headers)
    assert res.status_code == 200
    assert res.json()["role"] == "admin"


def test_local_mode_still_allows_public_health(monkeypatch):
    _auth_enforced_flags_off(monkeypatch)
    client = TestClient(_middleware_app())
    res = client.get("/health")
    assert res.status_code == 200


# ── 2. public path allowlist ─────────────────────────────────────────


def test_public_paths_exclude_data_and_admin_surfaces():
    from api.middleware.auth_middleware import AuthMiddleware

    mw = AuthMiddleware(app=None)
    for path in (
        "/sql",
        "/ask",
        "/ask-sql",
        "/tables",
        "/tables/sales",
        "/schema",
        "/export/",
        "/export/csv/sales",
        "/compliance/",
        "/audit/",
        "/upload",
        "/clean",
        "/load",
        "/auth/users",
        "/profile/",
        "/queries/",
    ):
        assert not mw._is_public_path(path), f"{path} must not be public"


def test_public_paths_still_allow_login_and_docs():
    from api.middleware.auth_middleware import AuthMiddleware

    mw = AuthMiddleware(app=None)
    for path in (
        "/",
        "/health",
        "/config",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/auth/login",
        "/auth/register",
        "/auth/verify",
        "/auth/accounts/login",
        "/auth/google/login",
        "/auth/google/callback",
        "/auth/sso/login",
        "/auth/accounts/register",
        "/js/app.js",
        "/css/styles.css",
    ):
        assert mw._is_public_path(path), f"{path} should remain public"


# ── 3. GET /auth/users requires admin ────────────────────────────────


def test_auth_users_requires_authentication(monkeypatch):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    client = TestClient(app)
    res = client.get("/auth/users")
    assert res.status_code == 401


def test_auth_users_forbids_non_admin(monkeypatch):
    from api.auth import create_jwt_token, user_manager
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    user_manager.add_user("hard_viewer", "Str0ng!Passw0rd", "viewer")
    token = create_jwt_token("hard_viewer", role="viewer")
    client = TestClient(app)
    res = client.get("/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_auth_users_allows_admin(monkeypatch):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    client = TestClient(app)
    headers = _login_headers("hard_admin_users", "Str0ng!Passw0rd")
    res = client.get("/auth/users", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body.get("success") is True
    assert "users" in body


# ── 4. path traversal on upload / clean / load ───────────────────────


def test_sanitize_upload_name_rejects_traversal():
    from api.security_paths import sanitize_upload_name

    for bad in (
        "../evil.csv",
        "..\\evil.csv",
        "../../etc/passwd",
        "foo/bar.csv",
        "foo\\bar.csv",
        "",
        ".",
        "..",
        "/etc/passwd",
    ):
        with pytest.raises((ValueError, HTTPException)):
            sanitize_upload_name(bad)

    assert sanitize_upload_name("sales.csv") == "sales.csv"
    assert sanitize_upload_name("My Report.xlsx") == "My Report.xlsx"


def test_upload_endpoint_rejects_traversal_filename(monkeypatch, tmp_path):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    headers = _login_headers("hard_upload", "Str0ng!Passw0rd")
    # Clear any leftover from a prior RED run so we only assert this request.
    for leftover in (
        Path("data") / "evil.csv",
        Path("data") / "raw" / ".." / "evil.csv",
        Path("evil.csv"),
    ):
        if leftover.exists():
            leftover.unlink()
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/upload",
        files={"file": ("../evil.csv", b"a,b\n1,2\n", "text/csv")},
        headers=headers,
    )
    assert res.status_code == 400
    for escaped in (
        Path("data") / "evil.csv",
        Path("data") / "raw" / ".." / "evil.csv",
        Path("evil.csv"),
    ):
        assert not escaped.exists(), f"wrote outside data/raw: {escaped}"


def test_clean_endpoint_rejects_traversal_filename(monkeypatch):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    headers = _login_headers("hard_clean", "Str0ng!Passw0rd")
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/clean",
        json={"filename": "../secrets.txt"},
        headers=headers,
    )
    assert res.status_code == 400


def test_load_endpoint_rejects_traversal_filename(monkeypatch):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    headers = _login_headers("hard_load", "Str0ng!Passw0rd")
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/load",
        json={"filename": "../secrets.txt"},
        headers=headers,
    )
    assert res.status_code == 400


# ── 5. JWT jti + logout revocation ───────────────────────────────────


def test_jwt_includes_jti():
    from api.auth import create_jwt_token, verify_jwt_token

    token = create_jwt_token("hard_jti_user", role="viewer")
    payload = verify_jwt_token(token)
    assert payload is not None
    assert payload.get("jti")


def test_logout_revokes_jwt(monkeypatch):
    from api.auth import create_jwt_token, revoke_jwt_token, verify_jwt_token
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    token = create_jwt_token("hard_logout", role="admin")
    assert verify_jwt_token(token) is not None

    revoke_jwt_token(token)
    assert verify_jwt_token(token) is None

    # Endpoint logout path: revoked bearer must not authenticate
    client = TestClient(app)
    res = client.get(
        "/auth/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 401


def test_logout_endpoint_revokes_presented_token(monkeypatch):
    from api.auth import create_jwt_token, verify_jwt_token
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    headers = _login_headers("hard_logout_ep", "Str0ng!Passw0rd")
    token = headers["Authorization"].removeprefix("Bearer ")
    assert verify_jwt_token(token) is not None

    client = TestClient(app)
    res = client.post("/auth/logout", headers=headers)
    assert res.status_code == 200
    assert verify_jwt_token(token) is None


# ── 6. API key: header only, no query string ─────────────────────────


def test_api_key_query_param_is_ignored(monkeypatch):
    from api.middleware.auth_middleware import AuthMiddleware

    _auth_enforced_flags_on(monkeypatch)

    class _Req:
        def __init__(self):
            self.headers = {}
            self.query_params = {"api_key": "da_deadbeef"}
            self.cookies = {}

    mw = AuthMiddleware(app=None)

    def _validate(_raw):
        raise AssertionError("query-string api_key must never be validated")

    monkeypatch.setattr(mw.api_key_manager, "validate_key", _validate)

    import anyio

    result = anyio.run(lambda: mw._authenticate_api_key(_Req()))
    assert result is None


def test_api_key_header_still_accepted(monkeypatch):
    from api.middleware.auth_middleware import AuthMiddleware, User

    _auth_enforced_flags_on(monkeypatch)
    mw = AuthMiddleware(app=None)

    class _Ctx:
        user_id = "user-hard-key"
        key_id = "key-1"
        scopes = {"read"}

    class _Req:
        def __init__(self):
            self.headers = {"X-API-Key": "da_valid_key_header"}
            self.query_params = {}
            self.cookies = {}

    def _validate(_raw):
        return _Ctx()

    from api.auth import user_manager

    monkeypatch.setattr(mw.api_key_manager, "validate_key", _validate)
    monkeypatch.setattr(
        user_manager,
        "get_user_info_by_id",
        lambda _uid: {
            "username": "hard_key_user",
            "role": "viewer",
            "user_id": "user-hard-key",
            "is_active": True,
        },
    )

    import anyio

    result = anyio.run(lambda: mw._authenticate_api_key(_Req()))
    assert result is not None
    assert isinstance(result, User)
    assert result.auth_method == "api_key"


def test_inactive_api_key_user_is_rejected(monkeypatch):
    """Session/API-key paths must honor is_active like the JWT path."""
    from api.middleware.auth_middleware import AuthMiddleware

    _auth_enforced_flags_on(monkeypatch)
    mw = AuthMiddleware(app=None)

    class _Ctx:
        user_id = "user-inactive-key"
        key_id = "key-inactive"
        scopes: set = set()

    class _Req:
        def __init__(self):
            self.headers = {"X-API-Key": "da_inactive_key"}
            self.query_params = {}
            self.cookies = {}

    from api.auth import user_manager

    monkeypatch.setattr(mw.api_key_manager, "validate_key", lambda _raw: _Ctx())
    monkeypatch.setattr(
        user_manager,
        "get_user_info_by_id",
        lambda _uid: {
            "username": "inactive_user",
            "role": "viewer",
            "user_id": "user-inactive-key",
            "is_active": False,
        },
    )

    import anyio

    result = anyio.run(lambda: mw._authenticate_api_key(_Req()))
    assert result is None


def test_inactive_session_user_is_rejected(monkeypatch):
    from api.middleware.auth_middleware import AuthMiddleware

    _auth_enforced_flags_on(monkeypatch)
    mw = AuthMiddleware(app=None)

    class _Sess:
        user_id = "user-inactive-sess"
        tenant_id = "t1"

    class _Req:
        def __init__(self):
            self.headers = {}
            self.query_params = {}
            self.cookies = {"session_id": "sess-inactive"}

    from api.auth import user_manager

    monkeypatch.setattr(
        mw.session_manager,
        "get_session",
        lambda _sid: _Sess(),
    )
    monkeypatch.setattr(
        mw.session_manager,
        "refresh_session",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        user_manager,
        "get_user_info_by_id",
        lambda _uid: {
            "username": "inactive_sess",
            "role": "viewer",
            "user_id": "user-inactive-sess",
            "is_active": False,
        },
    )

    import anyio

    result = anyio.run(lambda: mw._authenticate_session(_Req()))
    assert result is None


def test_auth_verify_ignores_query_token(monkeypatch):
    """/auth/verify must not accept ?token= (leaks into access logs)."""
    from api.auth import create_jwt_token, user_manager
    from config import settings

    _ensure_user("verify_user", "Str0ng!Passw0rd", role="viewer")
    token = create_jwt_token("verify_user", role="viewer")
    monkeypatch.setattr(settings.sso, "enabled", False)
    monkeypatch.setattr(settings.security, "api_key_enabled", True)

    from api.main import app

    with TestClient(app) as client:
        r = client.get(f"/auth/verify?token={token}")
        assert r.status_code == 200
        body = r.json()
        assert body.get("authenticated") is False
        assert body.get("success") is False

        r2 = client.get(
            "/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        assert r2.json().get("authenticated") is True


def test_auth_verify_rejects_inactive_user(monkeypatch):
    """Deactivated accounts must not report authenticated via /auth/verify."""
    from api.auth import create_jwt_token, user_manager
    from config import settings

    _ensure_user("inactive_verify", "Str0ng!Passw0rd", role="viewer")
    user_manager.update_user("inactive_verify", is_active=False)
    try:
        token = create_jwt_token("inactive_verify", role="viewer")
        monkeypatch.setattr(settings.sso, "enabled", False)
        monkeypatch.setattr(settings.security, "api_key_enabled", True)

        from api.main import app

        with TestClient(app) as client:
            r = client.get(
                "/auth/verify",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            body = r.json()
            assert body.get("authenticated") is False
            assert body.get("success") is False
    finally:
        user_manager.update_user("inactive_verify", is_active=True)


def test_drop_tables_requires_admin(monkeypatch):
    """DELETE /tables and /tables/{name} must be admin-only."""
    from api.auth import create_jwt_token, user_manager
    from api.main import app
    from config import settings

    _ensure_user("drop_viewer", "Str0ng!Passw0rd", role="viewer")
    token = create_jwt_token("drop_viewer", role="viewer")
    monkeypatch.setattr(settings.sso, "enabled", False)
    monkeypatch.setattr(settings.security, "api_key_enabled", True)

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        r_all = client.request("DELETE", "/tables", json={"confirm": True}, headers=headers)
        assert r_all.status_code == 403

        r_one = client.request("DELETE", "/tables/definitely_not_a_table", headers=headers)
        assert r_one.status_code == 403


def test_google_login_page_escapes_message():
    """Google login HTML must not reflect raw attacker-controlled message (XSS)."""
    import re

    from api.main import _google_login_page

    resp = _google_login_page(
        "st",
        success=False,
        message='<script>alert("xss")</script>',
    )
    body = resp.body.decode("utf-8", errors="replace")
    # HTML body sink must escape; JS context uses json.dumps (safe separately).
    p = re.search(r"<p>(.*?)</p>", body, re.S)
    assert p, "expected <p> message sink"
    assert "<script>" not in p.group(1)
    assert "&lt;script&gt;" in p.group(1)


def test_google_login_page_escapes_script_sink():
    """JS sink inside <script> must not allow </script> breakout (XSS)."""
    from api.main import _google_login_page

    evil = "</script><script>alert(1)</script>"
    resp = _google_login_page(evil, success=False, message=evil)
    body = resp.body.decode("utf-8", errors="replace")
    # HTML parser terminates a script element at the first </script regardless
    # of JS string context — json.dumps alone leaves that sequence intact.
    assert "</script><script>" not in body
    assert "\\u003c/script\\u003e" in body
    # HTML sink still uses entity escapes.
    assert "&lt;script&gt;" in body


def test_table_name_identifier_rejects_injection():
    """validate_table_name rejects multi-statement / non-identifier names."""
    from fastapi import HTTPException

    from api.security_paths import validate_table_name

    for bad in (
        "sales;DROP TABLE analytics.sales",
        "sales; DROP TABLE x",
        "foo bar",
        '"quoted"',
        "1starts_with_digit",
        "",
        None,
        "../etc",
        "a" * 64,
    ):
        try:
            validate_table_name(bad)  # type: ignore[arg-type]
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError(f"expected 400 for {bad!r}")

    assert validate_table_name("sales") == "sales"
    assert validate_table_name("_tmp_2024") == "_tmp_2024"


def test_get_table_info_rejects_injection(monkeypatch):
    """GET /tables/{name} must 400 on non-identifier names (SQL injection)."""
    from api.auth import create_jwt_token
    from api.main import app
    from config import settings

    _ensure_user("tbl_viewer", "Str0ng!Passw0rd", role="viewer")
    token = create_jwt_token("tbl_viewer", role="viewer")
    monkeypatch.setattr(settings.sso, "enabled", False)
    monkeypatch.setattr(settings.security, "api_key_enabled", True)

    headers = {"Authorization": f"Bearer {token}"}
    # Semicolon is legal in a URL path segment; FastAPI path params accept it.
    evil = "sales;DROP TABLE analytics.sales"
    with TestClient(app) as client:
        r = client.get(f"/tables/{evil}", headers=headers)
        assert r.status_code == 400
        assert "Invalid table name" in r.text


# ── 7. hardening headers on every response ───────────────────────────


def test_security_headers_present(monkeypatch):
    """CSP + frame/clickjacking headers must ride along on API + static."""
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    with TestClient(app) as client:
        for path in ("/health", "/config"):
            r = client.get(path)
            assert r.status_code == 200
            assert "Content-Security-Policy" in r.headers, path
            assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
            assert r.headers.get("X-Frame-Options") == "DENY"
            assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_billing_pages_carry_frame_headers():
    from fastapi.testclient import TestClient

    import billing_server.main as billing_main

    client = TestClient(billing_main.app, raise_server_exceptions=False)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ── 8. session cookie issuance + clearing ────────────────────────────


def test_login_sets_httponly_session_cookie(monkeypatch):
    """POST /auth/login issues an HttpOnly session cookie (XSS cannot read)."""
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    _ensure_user("cookie_login", "Str0ng!Passw0rd", role="viewer")
    with TestClient(app) as client:
        r = client.post("/auth/login",
                        json={"username": "cookie_login", "password": "Str0ng!Passw0rd"})
        assert r.status_code == 200
        assert r.json().get("success") is True
        assert "session_id" in r.cookies, "login must set session_id cookie"
        # The cookie alone authenticates (no Bearer needed).
        r2 = client.get("/auth/status")
        assert r2.status_code == 200
        assert r2.json().get("authenticated") is True


def test_logout_clears_session_cookie(monkeypatch):
    from api.main import app

    _auth_enforced_flags_on(monkeypatch)
    headers = _login_headers("hard_logout_cookie", "Str0ng!Passw0rd")
    with TestClient(app) as client:
        r = client.post("/auth/logout", headers=headers)
        assert r.status_code == 200
        cleared = r.headers.get("set-cookie", "")
        assert "session_id" in cleared
        assert "Max-Age=0" in cleared or "expires=" in cleared.lower()


# ── 9. revocation persists across restarts; aud/iss enforced ─────────


def test_revocation_survives_restart(tmp_path, monkeypatch):
    """A logout must stay logged-out after a process restart."""
    import api.auth as auth_mod

    monkeypatch.setenv("DATAAGENT_REVOKED_JTIS", str(tmp_path / "revoked.json"))
    token = auth_mod.create_jwt_token("hard_restart_user", role="viewer")
    assert auth_mod.verify_jwt_token(token) is not None
    assert auth_mod.revoke_jwt_token(token) is True
    assert (tmp_path / "revoked.json").exists()

    # Simulate a restart: drop in-memory state, reload from disk.
    auth_mod._REVOKED_JTIS.clear()
    auth_mod._REVOKED_EXP.clear()
    auth_mod._load_revoked()
    assert auth_mod.verify_jwt_token(token) is None


def test_jwt_requires_issuer_and_audience():
    """Tokens without iss/aud (pre-upgrade or foreign) are rejected."""
    import jwt as pyjwt

    import api.auth as auth_mod

    legacy = pyjwt.encode(
        {"sub": "hard_legacy", "role": "viewer", "jti": "x" * 32},
        auth_mod.JWT_SECRET, algorithm=auth_mod.JWT_ALGORITHM)
    assert auth_mod.verify_jwt_token(legacy) is None

    fresh = auth_mod.create_jwt_token("hard_fresh", role="viewer")
    payload = auth_mod.verify_jwt_token(fresh)
    assert payload is not None
    assert payload.get("iss") == auth_mod.JWT_ISSUER
    assert payload.get("aud") == auth_mod.JWT_AUDIENCE
