"""Billing-server central accounts — TDD guard.

Covers the ADR-002 decision: the billing server stores ONLY username/password
(email + bcrypt hash) and purchase records. Endpoints:

  POST /v1/accounts/register  -> create account (409 on duplicate, 400 weak pw)
  POST /v1/accounts/login     -> session token + HttpOnly website cookie
  GET  /v1/accounts/me        -> profile for Bearer token or website cookie
  GET  /v1/accounts/summary   -> plan + safe purchase history for the session
  POST /v1/accounts/logout    -> revoke the website cookie session

Session tokens are Ed25519-signed by the same licensing key as entitlement
tokens (single trust anchor). The website cookie is an opaque, hashed DB
session so it also works on keyless deployments. Login is rate-limited per IP.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

STRONG = "Corr3ct!HorseBattery9"


@pytest.fixture()
def client(billing_env, monkeypatch):
    import billing_server.accounts as accounts_mod
    import billing_server.config as cfg
    import billing_server.main as main_mod

    # TestClient speaks plain HTTP and does not share the production cookie
    # domain; production keeps Secure + .data-agent.me (asserted separately).
    monkeypatch.setattr(cfg, "SESSION_COOKIE_DOMAIN", "", raising=False)
    monkeypatch.setattr(cfg, "SESSION_COOKIE_SECURE", False, raising=False)
    accounts_mod.reset_rate_limits()
    return TestClient(main_mod.app, raise_server_exceptions=False)


def _session_cookie(response) -> str:
    return response.headers.get("set-cookie", "")


def _me(client, **kwargs):
    return client.get("/v1/accounts/me", **kwargs)


def _summary(client, **kwargs):
    return client.get("/v1/accounts/summary", **kwargs)


def _register(client, email="User@Example.com", password=STRONG):
    return client.post("/v1/accounts/register", json={"email": email, "password": password})


def _login(client, email="user@example.com", password=STRONG):
    return client.post("/v1/accounts/login", json={"email": email, "password": password})


# ── register ──────────────────────────────────────────────────────────


def test_register_creates_account(client, billing_env):
    from billing_server import store

    r = _register(client)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "user@example.com"  # normalized
    assert body["user_id"]

    acc = store.get_account_by_email("USER@example.com")
    assert acc is not None
    assert acc["email"] == "user@example.com"


def test_register_hashes_password(client, billing_env):
    from billing_server import store

    _register(client)
    import billing_server.accounts as accounts_mod

    acc = store.get_account_by_email("user@example.com")
    assert acc["password_hash"] != STRONG
    assert acc["password_hash"].startswith("$2")  # bcrypt
    assert accounts_mod.verify_password(STRONG, acc["password_hash"]) is True
    assert accounts_mod.verify_password("wrong-password", acc["password_hash"]) is False


def test_register_duplicate_email_conflict(client):
    from billing_server import store

    body = _register(client).json()
    assert store.set_email_verified(body["user_id"]) is True
    r2 = _register(client, email="USER@EXAMPLE.COM")
    assert r2.status_code == 409


def test_register_rejects_invalid_email(client):
    r = _register(client, email="not-an-email")
    assert r.status_code == 422


def test_register_rejects_weak_password(client):
    r = _register(client, password="short")
    assert r.status_code == 400


# ── login ─────────────────────────────────────────────────────────────


def test_login_returns_verifiable_session_token(client):
    _register(client)
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "user@example.com"
    assert body["token"]

    import billing_server.accounts as accounts_mod

    payload = accounts_mod.verify_session_token(body["token"])
    assert payload["email"] == "user@example.com"
    assert payload["user_id"] == body["user_id"]


def test_login_is_case_insensitive(client):
    _register(client, email="Mixed@Example.com")
    r = _login(client, email="mIxEd@eXaMpLe.CoM")
    assert r.status_code == 200


def test_login_sets_http_only_website_cookie_and_me_accepts_it(client):
    _register(client)
    r = _login(client)

    cookie = _session_cookie(r)
    assert "da_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert client.cookies.get("da_session")

    me = _me(client)
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


def test_register_also_starts_a_website_session(client):
    r = _register(client)

    assert "da_session=" in _session_cookie(r)
    assert _me(client).status_code == 200


def test_website_cookie_is_host_scoped_and_secure_in_production(client, monkeypatch):
    import billing_server.config as cfg

    monkeypatch.setattr(cfg, "SESSION_COOKIE_DOMAIN", ".data-agent.me", raising=False)
    monkeypatch.setattr(cfg, "SESSION_COOKIE_SECURE", True, raising=False)
    _register(client)

    cookie = _session_cookie(_login(client))
    assert "Domain=.data-agent.me" in cookie
    assert "Secure" in cookie


def test_session_cookie_token_is_hashed_in_store(client):
    import billing_server.accounts as accounts_mod
    from billing_server import store

    _register(client)
    _login(client)
    raw = client.cookies.get("da_session")
    assert raw

    assert store.get_account_session(raw) is None
    assert store.get_account_session(accounts_mod.hash_session_token(raw)) is not None


def test_logout_revokes_cookie_session_and_clears_cookie(client):
    _register(client)
    assert _me(client).status_code == 200

    r = client.post("/v1/accounts/logout")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "Max-Age=0" in _session_cookie(r)
    assert _me(client).status_code == 401


def test_login_wrong_password_and_unknown_email_same_error(client):
    _register(client)
    r1 = _login(client, password="Wr0ng!Password123")
    r2 = _login(client, email="ghost@example.com")
    assert r1.status_code == 401
    assert r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]  # no user enumeration


def test_login_rate_limited(client):
    _register(client)
    last = None
    for _ in range(12):
        last = _login(client, password="Wr0ng!Password123")
    assert last.status_code == 429


# ── me ────────────────────────────────────────────────────────────────


def test_keyless_login_returns_no_session_token(client, billing_env, tmp_path, monkeypatch):
    cfg = billing_env["cfg"]
    monkeypatch.setattr(cfg, "KEYLESS", True, raising=False)
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY", "")
    _register(client)

    r = _login(client)

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "user@example.com"
    assert "token" not in body
    assert "da_session=" in _session_cookie(r)
    assert _me(client).status_code == 200


def test_missing_signing_key_still_503_when_not_keyless(client, billing_env, tmp_path, monkeypatch):
    cfg = billing_env["cfg"]
    monkeypatch.setattr(cfg, "KEYLESS", False, raising=False)
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY", "")
    _register(client)

    r = _login(client)

    assert r.status_code == 503


def test_me_returns_profile_with_valid_token(client):
    _register(client)
    token = _login(client).json()["token"]
    r = client.get("/v1/accounts/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "user@example.com"
    assert body["user_id"]


def test_me_rejects_missing_and_bad_tokens(client):
    assert client.get("/v1/accounts/me").status_code == 401
    r = client.get("/v1/accounts/me", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_me_rejects_expired_token(client, billing_env):
    import billing_server.accounts as accounts_mod

    token = accounts_mod.mint_session_token("user@example.com", "u1", days=-1)
    r = client.get("/v1/accounts/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ── hardening (security-review follow-ups) ────────────────────────────


# ── account summary ──────────────────────────────────────────────────


def test_summary_requires_a_session(client):
    assert _summary(client).status_code == 401


def test_summary_returns_free_account_and_no_purchases(client):
    from billing_server import store

    body = _register(client).json()
    assert store.set_email_verified(body["user_id"]) is True

    r = _summary(client)

    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "user@example.com"
    assert body["plan"] == "free"
    assert body["status"] == "free"
    assert body["purchases"] == []
    assert body["subscription"] is None


def test_summary_returns_paid_plan_and_safe_purchase_history(client, billing_env):
    from billing_server import store

    body = _register(client).json()
    assert store.set_email_verified(body["user_id"]) is True
    store.record_order(
        "ord_summary_lifetime", "user@example.com", "lifetime", 1000000, "INR",
        notes={"private": "do-not-return"}, provider="famgateway",
    )
    store.mark_paid("ord_summary_lifetime", "pay_secret_123", grant_days=None)
    store.attach_key("ord_summary_lifetime", "LICENSE-SECRET", "key-hash-secret")

    r = _summary(client)

    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "lifetime"
    assert body["status"] == "active"
    assert body["expires_at"] is None
    assert len(body["purchases"]) == 1
    purchase = body["purchases"][0]
    assert purchase == {
        "order_id": "ord_summary_lifetime",
        "plan": "lifetime",
        "amount": 1000000,
        "currency": "INR",
        "status": "paid",
        "provider": "famgateway",
        "created_at": purchase["created_at"],
        "paid_at": purchase["paid_at"],
    }
    serialized = r.text
    for secret in ("pay_secret_123", "LICENSE-SECRET", "key-hash-secret",
                   "do-not-return", "license_key", "payment_id", "notes"):
        assert secret not in serialized


def test_summary_does_not_leak_another_accounts_purchases(client):
    from billing_server import store

    body = _register(client).json()
    assert store.set_email_verified(body["user_id"]) is True
    store.record_order(
        "ord_other_buyer", "other@example.com", "lifetime", 1000000, "INR",
        provider="famgateway",
    )
    store.mark_paid("ord_other_buyer", "pay_other_123", grant_days=None)

    r = _summary(client)

    assert r.json()["plan"] == "free"
    assert r.json()["purchases"] == []
    assert "ord_other_buyer" not in r.text


def test_register_rejects_password_over_72_bytes(client):
    # bcrypt only considers the first 72 bytes — reject cleanly, not 500.
    assert _register(client, password="a" * 73).status_code == 400
    assert _register(client, email="multi@example.com", password="é" * 40).status_code == 400


def test_login_overlong_password_returns_401_not_500(client):
    _register(client)
    assert _login(client, password="a" * 100).status_code == 401


def test_login_unknown_email_still_runs_bcrypt(client, monkeypatch):
    import billing_server.accounts as accounts_mod

    calls = []
    real = accounts_mod.verify_password

    def spy(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(accounts_mod, "verify_password", spy)
    r = _login(client, email="ghost@example.com")
    assert r.status_code == 401
    assert calls, "verify_password must run for unknown emails too (anti-timing)"


def test_account_logs_do_not_leak_email(client, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        _register(client)
        _login(client)
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "user@example.com" not in joined
