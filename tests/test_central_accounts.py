"""Central accounts app-side proxy — TDD guard (ADR-002).

The app proxies email/password auth to the billing server and then provisions
the SAME local session local accounts get (JWT + user record keyed by email).
Fail-open contract: when central accounts are unconfigured the SPA stays on
local accounts; when the billing server is unreachable the proxy returns 503
with no partial state.

Scope: the billing server only ever sees email + password hash + purchases.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

STRONG = "Corr3ct!HorseBattery9"

#: The bootstrap token of a fresh instance. The first account on an empty
#: directory may only become admin when it presents this token (ADR-009 D3).
BOOTSTRAP = "b00tstrap-central-accounts"


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


@pytest.fixture()
def client():
    from api.main import app
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    return TestClient(app, raise_server_exceptions=False)


def _configure(monkeypatch, url: str = "http://billing.test") -> None:
    from api.billing import cloud_sync

    monkeypatch.setattr(cloud_sync, "billing_sync_base_url", lambda: url)


# ── status ────────────────────────────────────────────────────────────


def test_status_reports_configuration(monkeypatch, client):
    from api.billing import cloud_sync

    monkeypatch.setattr(cloud_sync, "billing_sync_base_url", lambda: "")
    r = client.get("/auth/accounts/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "configured": False}

    _configure(monkeypatch)
    r2 = client.get("/auth/accounts/status")
    assert r2.status_code == 200
    assert r2.json() == {"enabled": True, "configured": True}


# ── register ──────────────────────────────────────────────────────────


def test_register_proxies_and_provisions_local_user(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setenv("DATAAGENT_BOOTSTRAP_TOKEN", BOOTSTRAP)
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return _Resp(200, {"ok": True, "user_id": "acct-1", "email": "reg1@example.com"})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post("/auth/accounts/register",
                    json={"email": "Reg1@Example.com", "password": STRONG,
                          "bootstrap_token": BOOTSTRAP})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["token"] and body["user"]

    assert calls["url"] == "http://billing.test/v1/accounts/register"
    assert calls["json"]["email"] == "reg1@example.com"  # normalized

    from api.auth import user_manager, verify_jwt_token

    info = user_manager.get_user_by_email("reg1@example.com")
    assert info is not None
    assert info["sso_provider"] == "billing_accounts"
    assert info["sso_subject"] == "acct-1"
    payload = verify_jwt_token(body["token"])
    assert payload and payload["sub"] == info["username"]


def test_register_duplicate_maps_409(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None: _Resp(409, {"detail": "email already registered"}))
    r = client.post("/auth/accounts/register",
                    json={"email": "dup1@example.com", "password": STRONG})
    assert r.status_code == 409


def test_register_weak_password_maps_400(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None: _Resp(400, {"detail": "password must be at least 8 characters"}))
    r = client.post("/auth/accounts/register",
                    json={"email": "weak1@example.com", "password": "short"})
    assert r.status_code == 400


# ── login ─────────────────────────────────────────────────────────────


def test_login_provisions_and_returns_jwt(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setenv("DATAAGENT_BOOTSTRAP_TOKEN", BOOTSTRAP)
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(200, {
            "ok": True, "token": "billing-session-token",
            "email": "log1@example.com", "user_id": "acct-2",
        }),
    )
    r = client.post("/auth/accounts/login",
                    json={"email": "log1@example.com", "password": STRONG,
                          "bootstrap_token": BOOTSTRAP})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["token"] and body["user"]

    from api.auth import user_manager, verify_jwt_token

    info = user_manager.get_user_by_email("log1@example.com")
    assert info is not None
    assert info["sso_provider"] == "billing_accounts"
    assert verify_jwt_token(body["token"])["sub"] == info["username"]

    # Idempotent provisioning: a second login reuses the same local user.
    r2 = client.post("/auth/accounts/login",
                     json={"email": "log1@example.com", "password": STRONG})
    assert r2.status_code == 200
    assert user_manager.get_user_by_email("log1@example.com")["username"] == info["username"]


def test_login_invalid_credentials_maps_401(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None: _Resp(401, {"detail": "invalid email or password"}))
    r = client.post("/auth/accounts/login",
                    json={"email": "bad1@example.com", "password": "Wr0ng!Password9"})
    assert r.status_code == 401


def test_login_unreachable_returns_503_without_provisioning(monkeypatch, client):
    _configure(monkeypatch)

    def _boom(url, json=None, timeout=None):
        raise httpx.ConnectError("billing down")

    monkeypatch.setattr(httpx, "post", _boom)
    r = client.post("/auth/accounts/login",
                    json={"email": "down1@example.com", "password": STRONG})
    assert r.status_code == 503

    from api.auth import user_manager

    assert user_manager.get_user_by_email("down1@example.com") is None


def test_login_unconfigured_returns_503_and_skips_network(monkeypatch, client):
    from api.billing import cloud_sync

    monkeypatch.setattr(cloud_sync, "billing_sync_base_url", lambda: "")

    def _no_network(url, json=None, timeout=None):
        raise AssertionError("no network call expected when unconfigured")

    monkeypatch.setattr(httpx, "post", _no_network)
    r = client.post("/auth/accounts/login",
                    json={"email": "no1@example.com", "password": STRONG})
    assert r.status_code == 503


# ── account-takeover guard (security-review follow-up) ────────────────


def _seed_existing_user(provider: str, email: str, username: str):
    from api.auth import user_manager

    user_manager.add_user(
        username=username, password="Loc4l!Passw0rd", role="admin",
        email=email, sso_subject="ext-1", sso_provider=provider,
    )
    return user_manager.get_user_by_email(email)


def test_login_refuses_to_link_existing_google_user(monkeypatch, client):
    """An unverified billing registration for a Google user's email must NOT
    inherit that local user (account-takeover guard)."""
    _configure(monkeypatch)
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(200, {
            "ok": True, "token": "billing-tok",
            "email": "victim@example.com", "user_id": "acct-evil",
        }),
    )
    from api.auth import user_manager

    _seed_existing_user("google", email="victim@example.com", username="victim")
    r = client.post("/auth/accounts/login",
                    json={"email": "victim@example.com", "password": STRONG})
    assert r.status_code == 409

    after = user_manager.get_user_by_email("victim@example.com")
    assert after["sso_provider"] == "google"
    assert after["sso_subject"] == "ext-1"
    assert after["role"] == "admin"


def test_register_refuses_to_link_existing_local_user(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(200, {
            "ok": True, "user_id": "acct-evil", "email": "victim2@example.com",
        }),
    )
    from api.auth import user_manager

    _seed_existing_user("", email="victim2@example.com", username="victim2")
    r = client.post("/auth/accounts/register",
                    json={"email": "victim2@example.com", "password": STRONG})
    assert r.status_code == 409
    assert user_manager.get_user_by_email("victim2@example.com")["sso_subject"] == "ext-1"


def test_register_invalid_email_maps_400(monkeypatch, client):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None: _Resp(422, {"detail": "invalid email"}))
    r = client.post("/auth/accounts/register",
                    json={"email": "not-an-email@", "password": STRONG})
    assert r.status_code == 400
