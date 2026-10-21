"""Billing-server central accounts — TDD guard.

Covers the ADR-002 decision: the billing server stores ONLY username/password
(email + bcrypt hash) and purchase records. Endpoints:

  POST /v1/accounts/register  -> create account (409 on duplicate, 400 weak pw)
  POST /v1/accounts/login     -> session token (401 on bad credentials)
  GET  /v1/accounts/me        -> profile for Bearer session token

Session tokens are Ed25519-signed by the same licensing key as entitlement
tokens (single trust anchor). Login is rate-limited per IP.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

STRONG = "Corr3ct!HorseBattery9"


@pytest.fixture()
def client(billing_env):
    import billing_server.accounts as accounts_mod
    import billing_server.main as main_mod

    accounts_mod.reset_rate_limits()
    return TestClient(main_mod.app, raise_server_exceptions=False)


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
    assert _register(client).status_code == 200
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
