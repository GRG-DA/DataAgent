"""Web-facing security posture for the hosted SaaS (second hardening pass).

Covers:

* the double-submit CSRF guard for cookie-authenticated requests,
* session / CSRF cookie hardening (``Secure``, ``HttpOnly``, ``SameSite``),
* the configurable CORS credentials flag and the production origin check,
* the ``TRUSTED_HOSTS`` startup check,
* the ``datetime.utcnow()`` sweep.

None of these tests touch the DuckDB warehouse: they are pure request and
response plumbing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── helpers ──────────────────────────────────────────────────────────────


@dataclass
class _User:
    """Minimal stand-in for ``api.middleware.auth_middleware.User``.

    The real dataclass resolves the billing plan on construction, which
    touches the warehouse — this keeps the tests hermetic.
    """

    user_id: str = "user-1"
    username: str = "tester"
    role: str = "user"
    auth_method: str = "session"
    session_id: Optional[str] = "sess-abc"


def _make_request(headers: Optional[dict] = None, scheme: str = "http") -> Request:
    """A real Starlette request with the given headers and scheme."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "root_path": "",
        "scheme": scheme,
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _build_app(user: Optional[_User] = None) -> FastAPI:
    """A tiny app with a stub auth middleware and the real CSRF guard."""
    from api.middleware.csrf import CSRFProtectionMiddleware

    app = FastAPI()

    @app.get("/thing")
    async def _get_thing():
        return {"ok": True}

    @app.post("/thing")
    async def _post_thing():
        return {"ok": True}

    @app.post("/auth/login")
    async def _login():
        return {"ok": True}

    class _StubAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if user is not None:
                request.state.user = user
            return await call_next(request)

    # Starlette inserts at position 0, so the LAST add_middleware() is the
    # OUTERMOST. The stub auth must wrap the CSRF guard (the real app
    # registers AuthMiddleware after CSRFProtectionMiddleware for the
    # same reason: the guard needs request.state.user).
    app.add_middleware(CSRFProtectionMiddleware)
    app.add_middleware(_StubAuth)
    return app


def _set_cookie_headers(token: str) -> dict:
    return {"Cookie": f"csrf_token={token}", "X-CSRF-Token": token}


# ── 1. CSRF guard ────────────────────────────────────────────────────────


def test_csrf_blocks_session_post_without_token():
    client = TestClient(_build_app(_User()))
    res = client.post("/thing")
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_csrf_allows_session_post_with_matching_token():
    from api.middleware.csrf import derive_csrf_token

    token = derive_csrf_token("sess-abc")
    client = TestClient(_build_app(_User()))
    res = client.post("/thing", headers=_set_cookie_headers(token))
    assert res.status_code == 200


def test_csrf_rejects_mismatched_header_and_cookie():
    client = TestClient(_build_app(_User()))
    res = client.post("/thing", headers=_set_cookie_headers("not-the-token"))
    assert res.status_code == 403


def test_csrf_rejects_injected_cookie_value():
    """A cookie value the attacker chose (not derived from this session).

    Plain double-submit without a signature accepts this; the guard must not.
    """
    client = TestClient(_build_app(_User()))
    res = client.post("/thing", headers=_set_cookie_headers("attacker-chosen"))
    assert res.status_code == 403


def test_csrf_rejects_token_from_another_session():
    from api.middleware.csrf import derive_csrf_token

    other = derive_csrf_token("sess-other")
    client = TestClient(_build_app(_User()))
    res = client.post("/thing", headers=_set_cookie_headers(other))
    assert res.status_code == 403


def test_csrf_allows_bearer_authenticated_post():
    user = _User(auth_method="jwt", session_id=None)
    client = TestClient(_build_app(user))
    res = client.post("/thing", headers={"Authorization": "Bearer a-jwt"})
    assert res.status_code == 200


def test_csrf_allows_api_key_post():
    user = _User(auth_method="api_key", session_id=None)
    client = TestClient(_build_app(user))
    res = client.post("/thing", headers={"X-API-Key": "da_key"})
    assert res.status_code == 200


def test_csrf_allows_session_user_that_also_presents_a_bearer_token():
    """The SPA sends the JWT on every request; the session cookie must not
    turn those calls into CSRF-protected ones."""
    client = TestClient(_build_app(_User()))
    res = client.post("/thing", headers={"Authorization": "Bearer a-jwt"})
    assert res.status_code == 200


def test_csrf_ignores_safe_methods():
    client = TestClient(_build_app(_User()))
    assert client.get("/thing").status_code == 200
    assert client.request("OPTIONS", "/thing").status_code in (200, 405)


def test_csrf_ignores_unauthenticated_requests():
    """No user → the auth middleware answers 401; the guard must not
    pre-empt it (that would break /auth/login and /auth/register)."""
    client = TestClient(_build_app(None))
    res = client.post("/thing")
    assert res.status_code == 200


def test_csrf_exempts_login_endpoint():
    client = TestClient(_build_app(_User()))
    assert client.post("/auth/login").status_code == 200


def test_csrf_403_body_never_leaks_the_expected_token():
    from api.middleware.csrf import derive_csrf_token

    token = derive_csrf_token("sess-abc")
    client = TestClient(_build_app(_User()))
    res = client.post("/thing")
    assert token not in res.text


def test_csrf_never_logs_the_token(caplog):
    from api.middleware.csrf import derive_csrf_token

    token = derive_csrf_token("sess-abc")
    client = TestClient(_build_app(_User()))
    with caplog.at_level(logging.DEBUG):
        client.post("/thing", headers=_set_cookie_headers("wrong-token"))
    assert token not in caplog.text
    assert "wrong-token" not in caplog.text


def test_csrf_cookie_is_issued_and_readable_by_js():
    client = TestClient(_build_app(_User()))
    res = client.get("/thing")
    raw = res.headers.get("set-cookie", "")
    assert "csrf_token=" in raw
    # Deliberately NOT HttpOnly: the SPA has to read it to echo it back.
    assert "HttpOnly" not in raw
    assert "SameSite=lax" in raw


def test_csrf_cookie_value_matches_the_session_derived_token():
    from api.middleware.csrf import derive_csrf_token

    client = TestClient(_build_app(_User()))
    res = client.get("/thing")
    raw = res.headers.get("set-cookie", "")
    value = raw.split("csrf_token=", 1)[1].split(";", 1)[0]
    assert value == derive_csrf_token("sess-abc")


def test_csrf_cookie_is_reissued_when_stale():
    """A cookie from an older session is replaced, not trusted."""
    client = TestClient(_build_app(_User()))
    res = client.get("/thing", headers={"Cookie": "csrf_token=stale-value"})
    raw = res.headers.get("set-cookie", "")
    assert "csrf_token=" in raw
    assert "stale-value" not in raw


def test_csrf_guard_does_not_500_on_garbage_header():
    client = TestClient(_build_app(_User()))
    res = client.post(
        "/thing",
        headers=[(b"Cookie", b"csrf_token=x"), (b"X-CSRF-Token", b"\xe9")],
    )
    assert res.status_code == 403


# ── 2. Session / CSRF cookie hardening ───────────────────────────────────


def test_real_auth_middleware_and_csrf_guard_work_together(monkeypatch):
    """The real AuthMiddleware resolves the session cookie to
    ``auth_method='session'``; the guard must then demand the token."""
    import api.middleware.auth_middleware as am
    from api.middleware.auth_middleware import AuthMiddleware
    from api.middleware.csrf import CSRFProtectionMiddleware, derive_csrf_token

    app = FastAPI()

    @app.post("/thing")
    async def _post_thing():
        return {"ok": True}

    class _Session:
        user_id = "user-1"
        tenant_id = "default"

    class _Sessions:
        def get_session(self, _sid):
            return _Session()

        def refresh_session(self, _sid):
            return None

    class _Keys:
        def validate_key(self, _raw):
            return None

    monkeypatch.setattr(
        am.user_manager,
        "get_user_info_by_id",
        lambda uid: {
            "username": "u", "role": "user", "user_id": uid, "is_active": True,
        },
    )

    app.add_middleware(CSRFProtectionMiddleware)
    app.add_middleware(
        AuthMiddleware, session_manager=_Sessions(), api_key_manager=_Keys()
    )

    client = TestClient(app)
    sid = "sess-real"
    # The session cookie alone authenticates — and is not enough for a write.
    res = client.post("/thing", headers={"Cookie": f"session_id={sid}"})
    assert res.status_code == 403

    token = derive_csrf_token(sid)
    res = client.post(
        "/thing",
        headers={
            "Cookie": f"session_id={sid}; csrf_token={token}",
            "X-CSRF-Token": token,
        },
    )
    assert res.status_code == 200


def test_session_cookie_is_httponly_and_lax_over_plain_http():
    from api.middleware.auth_middleware import apply_session_cookie

    resp = Response()
    apply_session_cookie(resp, "sess-1", _make_request())
    raw = resp.headers.get("set-cookie", "")
    assert "session_id=sess-1" in raw
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw
    assert "Secure" not in raw


def test_session_cookie_is_secure_behind_a_tls_proxy():
    from api.middleware.auth_middleware import apply_session_cookie

    resp = Response()
    request = _make_request({"X-Forwarded-Proto": "https"})
    apply_session_cookie(resp, "sess-1", request)
    assert "Secure" in resp.headers.get("set-cookie", "")


def test_session_cookie_is_secure_when_the_deployment_forces_it(monkeypatch):
    from config import settings
    from api.middleware.auth_middleware import apply_session_cookie

    monkeypatch.setattr(settings.session, "force_secure_cookie", True)
    resp = Response()
    apply_session_cookie(resp, "sess-1", _make_request())
    assert "Secure" in resp.headers.get("set-cookie", "")


def test_session_helper_also_issues_the_readable_csrf_cookie():
    from api.middleware.auth_middleware import apply_session_cookie

    resp = Response()
    apply_session_cookie(resp, "sess-1", _make_request())
    cookies = resp.headers.getlist("set-cookie")
    csrf = [c for c in cookies if c.startswith("csrf_token=")]
    assert csrf, "the CSRF companion cookie must be issued with the session"
    assert "HttpOnly" not in csrf[0]


# ── 3. CORS ──────────────────────────────────────────────────────────────


def test_cors_credentials_default_to_false():
    from config import settings

    assert settings.api.cors_credentials is False


def test_cors_check_warns_about_development_origins(caplog):
    from config import check_cors_origins

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        check_cors_origins(
            ["http://localhost:3000", "http://127.0.0.1:3000"],
            app_env="development",
        )
    assert any("localhost" in r.getMessage() for r in caplog.records)


def test_cors_check_is_quiet_for_an_explicit_production_allow_list(caplog):
    from config import check_cors_origins

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        origins = check_cors_origins(
            ["https://analytics.example.com"], app_env="production"
        )
    assert origins == ["https://analytics.example.com"]
    assert not [r for r in caplog.records if "localhost" in r.getMessage()]


def test_cors_check_refuses_to_start_in_production_with_the_shipped_defaults():
    from config import check_cors_origins

    with pytest.raises(RuntimeError) as exc:
        check_cors_origins(
            ["http://localhost:3000", "http://127.0.0.1:3000"],
            app_env="production",
        )
    assert "API_CORS_ORIGINS" in str(exc.value)


def test_cors_check_refuses_a_wildcard_in_production():
    from config import check_cors_origins

    with pytest.raises(RuntimeError):
        check_cors_origins(["*"], app_env="production", explicit=True)


# ── 4. TRUSTED_HOSTS ─────────────────────────────────────────────────────


def test_trusted_hosts_parses_the_allow_list():
    from api.middleware.host_guard import trusted_hosts

    hosts = trusted_hosts({"TRUSTED_HOSTS": "a.example.com, b.example.com"})
    assert hosts == ["a.example.com", "b.example.com"]


def test_trusted_hosts_warns_when_unset(caplog):
    from api.middleware.host_guard import trusted_hosts

    with caplog.at_level(logging.WARNING):
        hosts = trusted_hosts({})
    assert hosts == ["*"]
    assert any("TRUSTED_HOSTS" in r.getMessage() for r in caplog.records)


def test_trusted_hosts_refuses_to_start_in_production_without_an_allow_list():
    from api.middleware.host_guard import trusted_hosts

    with pytest.raises(RuntimeError) as exc:
        trusted_hosts({}, app_env="production")
    assert "TRUSTED_HOSTS" in str(exc.value)


def test_trusted_hosts_accepts_an_explicit_production_allow_list():
    from api.middleware.host_guard import trusted_hosts

    hosts = trusted_hosts(
        {"TRUSTED_HOSTS": "analytics.example.com"}, app_env="production"
    )
    assert hosts == ["analytics.example.com"]


# ── 5. datetime.utcnow() sweep ───────────────────────────────────────────


OWNED_MODULES = (
    "api/api_keys.py",
    "api/audit.py",
    "api/session.py",
)


@pytest.mark.parametrize("relative_path", OWNED_MODULES)
def test_owned_modules_no_longer_use_datetime_utcnow(relative_path):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "datetime.utcnow(" not in source, (
        f"{relative_path} still calls the deprecated datetime.utcnow()"
    )


def test_session_timestamps_are_naive_utc():
    from datetime import datetime, timezone

    from api.session import Session

    session = Session(id="s", user_id="u")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert session.created_at.tzinfo is None
    assert session.last_activity.tzinfo is None
    assert abs((now - session.created_at).total_seconds()) < 5


def test_api_key_created_at_is_naive_utc():
    from datetime import datetime, timezone

    from api.api_keys import APIKey

    key = APIKey(
        id="k",
        user_id="u",
        name="n",
        key_hash="h",
        key_prefix="p",
        scopes=[],
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert key.created_at.tzinfo is None
    assert abs((now - key.created_at).total_seconds()) < 5


def test_audit_event_timestamp_is_naive_utc():
    from datetime import datetime, timezone

    from api.audit import AuditEvent

    event = AuditEvent()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert event.timestamp.tzinfo is None
    assert abs((now - event.timestamp).total_seconds()) < 5
