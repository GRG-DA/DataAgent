"""Product keys are account-bound.

A key minted for a buyer must activate only on the account that owns the
purchase email — sharing the key with other accounts must fail. Legacy
(email-less) keys must not be activatable at all.

Covers:
- keygen payload carries the buyer's (lowercased) email; unbound keys have none
- POST /premium/activate-key: owner succeeds, other account / no-email account
  / unbound key are rejected
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

httpx = pytest.importorskip("httpx")


def _new_user(username: str, email: str = "") -> str:
    """Create a real user record (isolated store) and return its user_id."""
    from api.auth import user_manager

    uid = user_manager.add_user(username, "Str0ng!Passw0rd", role="viewer", email=email)
    assert uid, f"could not create test user {username}"
    return uid


@pytest.fixture()
def app_client(billing_db, billing_env, monkeypatch):
    """Tiny FastAPI app with the premium router + the fixture keypair."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cryptography.hazmat.primitives import serialization

    import api.premium as premium
    from api.dependencies import get_current_user
    from config import settings
    from pathlib import Path as _P
    import keygen_app.storage as kg_storage

    # The app verifies keys with the fixture keypair, and keygen storage
    # writes to the temp warehouse (never the real one).
    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())
    monkeypatch.setattr(
        kg_storage, "default_db", lambda: _P(settings.duckdb.database_path)
    )
    # Deterministic offline default; remote-registry tests set these explicitly.
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)

    app = FastAPI()
    app.include_router(premium.router)
    client = TestClient(app)

    def login_as(user_id: str, username: str):
        from api.middleware.auth_middleware import User

        app.dependency_overrides[get_current_user] = (
            lambda: User(user_id=user_id, username=username, role="viewer")
        )
        return client

    return SimpleNamespace(client=client, login_as=login_as, billing_env=billing_env)


def _payload(billing_env, key):
    from cryptography.hazmat.primitives import serialization
    from keygen_app.keygen import validate_key

    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    return validate_key(key, pub)


# ── keygen: buyer email in the signed payload ─────────────────────────


def test_generate_key_embeds_lowercased_buyer_email(billing_env):
    from keygen_app.keygen import bound_email

    key = billing_env["keymint"].mint_key("pro", email="Buyer@Example.COM")
    payload = _payload(billing_env, key)
    assert payload["email"] == "buyer@example.com"
    assert bound_email(payload) == "buyer@example.com"


def test_generate_key_without_email_is_unbound(billing_env):
    from keygen_app.keygen import bound_email, generate_key

    key = generate_key(billing_env["private"], "pro")  # legacy tooling key
    payload = _payload(billing_env, key)
    assert "email" not in payload
    assert bound_email(payload) == ""


def test_mint_key_refuses_missing_buyer_email(billing_env):
    with pytest.raises(ValueError):
        billing_env["keymint"].mint_key("pro")


# ── activation: owner succeeds ────────────────────────────────────────


def test_key_activates_on_owner_account(app_client):
    uid = _new_user("bind_owner_1", email="owner1@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="owner1@example.com")

    r = app_client.login_as(uid, "bind_owner_1").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "pro"

    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(uid) == "pro"


def test_owner_can_reactivate_same_key(app_client):
    uid = _new_user("bind_owner_re", email="owner-re@example.com")
    key = app_client.billing_env["keymint"].mint_key("lifetime", email="owner-re@example.com")
    client = app_client.login_as(uid, "bind_owner_re")

    assert client.post("/premium/activate-key", json={"key": key}).status_code == 200
    r = client.post("/premium/activate-key", json={"key": key})
    assert r.status_code == 200, r.text


def test_email_match_is_case_insensitive(app_client):
    uid = _new_user("bind_owner_case", email="CaseOwner@Example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="CASEOWNER@EXAMPLE.COM")

    r = app_client.login_as(uid, "bind_owner_case").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 200, r.text


# ── activation: sharing is blocked ────────────────────────────────────


def test_key_rejected_on_second_account(app_client):
    owner_uid = _new_user("bind_owner_2", email="owner2@example.com")
    other_uid = _new_user("bind_other_2", email="other2@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="owner2@example.com")

    r = app_client.login_as(other_uid, "bind_other_2").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 403, r.text

    # The second account must not gain the plan.
    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(other_uid) == "free"
    assert get_plan_for_user(owner_uid) == "free"


def test_key_rejected_for_account_without_email(app_client):
    uid = _new_user("bind_local_1")  # local account, no email identity
    key = app_client.billing_env["keymint"].mint_key("pro", email="somebuyer@example.com")

    r = app_client.login_as(uid, "bind_local_1").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 403, r.text

    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(uid) == "free"


def test_unbound_legacy_key_rejected(app_client):
    from keygen_app.keygen import generate_key

    uid = _new_user("bind_owner_3", email="owner3@example.com")
    key = generate_key(app_client.billing_env["private"], "pro")  # no buyer binding

    r = app_client.login_as(uid, "bind_owner_3").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 400, r.text

    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(uid) == "free"


# ── one-time use: first account claims the key ────────────────────────


def test_second_account_blocked_on_same_device(app_client):
    """One-time use: even with the buyer's own email, a second account on the
    same install cannot activate the key."""
    uid_a = _new_user("claim_first", email="claim@example.com")
    uid_b = _new_user("claim_second", email="claim@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="claim@example.com")

    r = app_client.login_as(uid_a, "claim_first").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 200, r.text

    r = app_client.login_as(uid_b, "claim_second").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 403, r.text

    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(uid_a) == "pro"
    assert get_plan_for_user(uid_b) == "free"


def test_local_ledger_records_key_owner(app_client):
    from keygen_app.keygen import key_hash

    uid = _new_user("claim_ledger", email="ledger@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="ledger@example.com")
    r = app_client.login_as(uid, "claim_ledger").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 200, r.text

    from api.billing import redemption

    claim = redemption.local_claim(key_hash(key))
    assert claim is not None and claim["user_id"] == uid


# ── one-time use: billing-server registry (when configured) ──────────


def _configure_sync(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_URL", "https://billing.example.test")
    monkeypatch.setenv("BILLING_SYNC_SECRET", "unit_secret")


def _fake_post(status_code, payload):
    def _post(url, json=None, timeout=None):
        return SimpleNamespace(status_code=status_code, json=lambda: payload)

    return _post


def test_remote_conflict_rejects_activation(app_client, monkeypatch):
    _configure_sync(monkeypatch)
    from api.billing import redemption

    monkeypatch.setattr(
        redemption.httpx, "post",
        _fake_post(409, {"detail": {"status": "conflict"}}),
    )
    uid = _new_user("remote_conflict", email="rc@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="rc@example.com")

    r = app_client.login_as(uid, "remote_conflict").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 403, r.text

    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(uid) == "free"


def test_remote_unreachable_rejects_activation(app_client, monkeypatch):
    """Single use cannot be verified offline — fail closed when configured."""
    _configure_sync(monkeypatch)
    from api.billing import redemption
    import httpx as _hx

    def _boom(url, json=None, timeout=None):
        raise _hx.ConnectError("offline")

    monkeypatch.setattr(redemption.httpx, "post", _boom)
    uid = _new_user("remote_offline", email="ro@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="ro@example.com")

    r = app_client.login_as(uid, "remote_offline").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 503, r.text


def test_remote_redeemed_activates_and_records(app_client, monkeypatch):
    _configure_sync(monkeypatch)
    from api.billing import redemption

    monkeypatch.setattr(
        redemption.httpx, "post",
        _fake_post(200, {"ok": True, "status": "redeemed"}),
    )
    uid = _new_user("remote_ok", email="rok@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="rok@example.com")

    r = app_client.login_as(uid, "remote_ok").post(
        "/premium/activate-key", json={"key": key}
    )
    assert r.status_code == 200, r.text

    from keygen_app.keygen import key_hash

    claim = redemption.local_claim(key_hash(key))
    assert claim is not None and claim["user_id"] == uid


def test_remote_already_owner_allows_reactivation(app_client, monkeypatch):
    _configure_sync(monkeypatch)
    from api.billing import redemption

    monkeypatch.setattr(
        redemption.httpx, "post",
        _fake_post(200, {"ok": True, "status": "already_owner"}),
    )
    uid = _new_user("remote_owner", email="rowner@example.com")
    key = app_client.billing_env["keymint"].mint_key("pro", email="rowner@example.com")
    client = app_client.login_as(uid, "remote_owner")

    assert client.post("/premium/activate-key", json={"key": key}).status_code == 200
    assert client.post("/premium/activate-key", json={"key": key}).status_code == 200
