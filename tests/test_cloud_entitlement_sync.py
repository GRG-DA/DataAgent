"""App-side cloud entitlement sync + product-key activation tests.

Covers:
- cloud_sync fetch (HTTP mocked), fail-secure behavior (no config / no network
  → None, never an exception)
- apply_remote_plan: upgrade applies, free-from-server never downgrades a
  locally-activated key, idempotent no-op
- end-to-end: a key minted by the billing server keypair is activated via
  POST /premium/activate-key and flips entitlements to pro.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

httpx = pytest.importorskip("httpx")

from tests.conftest import auth_headers  # noqa: E402


@pytest.fixture()
def sync_env(tmp_path, monkeypatch):
    """Configure cloud sync against a fake billing host."""
    monkeypatch.setenv("BILLING_SYNC_URL", "https://billing.example.test")
    monkeypatch.setenv("BILLING_SYNC_SECRET", "unit_secret")
    import api.billing.cloud_sync as cs
    return cs


def _fake_httpx_get(payload=None, status=200):
    def _get(url, params=None, timeout=None):
        import httpx as _hx

        class R:
            status_code = status

            @staticmethod
            def json():
                return payload or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise _hx.HTTPStatusError("err", request=None, response=None)

        return R()

    return _get


# ── fetch_remote_plan ─────────────────────────────────────────────────


def test_fetch_returns_none_when_unconfigured(monkeypatch):
    import api.billing.cloud_sync as cs
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    monkeypatch.setattr(cs, "billing_sync_base_url", lambda: "")
    assert cs.fetch_remote_plan("a@b.com") is None
    assert cs.sync_user("u1", "a@b.com") is None  # never raises


def test_fetch_parses_plan(sync_env, monkeypatch):
    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "pro", "status": "active", "expires_at": None}))
    out = sync_env.fetch_remote_plan("buyer@example.com")
    assert out == {"plan": "pro", "status": "active", "expires_at": None,
                   "source": sync_env.DEFAULT_SOURCE, "token": None,
                   "auto_renew": None, "cancel_at_period_end": None}


def test_fetch_passes_through_renewal_flags(sync_env, monkeypatch):
    """Subscription responses carry auto_renew / cancel_at_period_end (Phase 4)."""
    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "pro", "status": "active",
                                         "expires_at": "2030-01-01T00:00:00",
                                         "auto_renew": True,
                                         "cancel_at_period_end": False}))
    out = sync_env.fetch_remote_plan("buyer@example.com")
    assert out is not None
    assert out["auto_renew"] is True
    assert out["cancel_at_period_end"] is False


def test_fetch_accepts_signed_token(sync_env, billing_env, monkeypatch):
    """Paid responses carrying a server-signed token verify and pass through."""
    from cryptography.hazmat.primitives import serialization
    from billing_server import keymint as server_keymint

    # The app verifies with LICENSE_PUBLIC_KEY; point it at the fixture pair
    # (billing_env) that the server mints with.
    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())
    token = server_keymint.mint_entitlement_token("buyer@example.com", "pro")
    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "pro", "status": "active",
                                         "expires_at": None, "token": token}))
    out = sync_env.fetch_remote_plan("buyer@example.com")
    assert out and out["plan"] == "pro" and out["token"] == token


def test_fetch_rejects_forged_token(sync_env, billing_env, monkeypatch):
    """A token signed by another key fails closed (no grant applied)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from billing_server import keymint as server_keymint
    import base64
    import json

    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())
    # Forge with an unrelated keypair.
    rogue = ed25519.Ed25519PrivateKey.generate()
    payload = {"product": "dataagent-ent", "ver": 1, "email": "buyer@example.com",
               "plan": "pro", "iat": "2026-01-01T00:00:00+00:00",
               "exp": "2030-01-01T00:00:00+00:00", "grant_expires_at": None}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    forged = (base64.urlsafe_b64encode(raw).decode().rstrip("=") + "."
              + base64.urlsafe_b64encode(rogue.sign(raw)).decode().rstrip("="))
    assert forged != server_keymint.mint_entitlement_token("buyer@example.com", "pro")
    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "pro", "status": "active",
                                         "expires_at": None, "token": forged}))
    assert sync_env.fetch_remote_plan("buyer@example.com") is None


def test_fetch_fail_secure_on_network_error(sync_env, monkeypatch):
    import httpx as _hx

    def boom(url, params=None, timeout=None):
        raise _hx.ConnectError("no network")

    monkeypatch.setattr(sync_env.httpx, "get", boom)
    assert sync_env.fetch_remote_plan("buyer@example.com") is None


def test_fetch_rejects_unknown_plan(sync_env, monkeypatch):
    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "ultra", "status": "active"}))
    assert sync_env.fetch_remote_plan("buyer@example.com") is None


# ── apply_remote_plan ─────────────────────────────────────────────────


def test_apply_stores_renewal_snapshot(billing_db, sync_env):
    """A synced subscription persists the renewal snapshot (Phase 4)."""
    from api.billing import cloud_sync as cs
    from api.billing import renewal_snapshot as rs

    applied = cs.apply_remote_plan("user-renew-store", "store@example.com", {
        "plan": "pro", "status": "active", "expires_at": "2030-01-01T00:00:00",
        "auto_renew": True, "cancel_at_period_end": False,
    })
    assert applied == "pro"
    snap = rs.read_snapshot("store@example.com")
    assert snap is not None
    assert snap["is_subscription"] is True
    assert snap["auto_renew"] is True
    assert snap["renews_at"] == "2030-01-01T00:00:00"


def test_apply_free_stores_last_checked_snapshot(billing_db, sync_env):
    """Even a free/no-grant response records a last-checked (non-sub) snapshot."""
    from api.billing import cloud_sync as cs
    from api.billing import renewal_snapshot as rs

    cs.apply_remote_plan("user-renew-free", "free@example.com",
                         {"plan": "free", "status": "none", "expires_at": None})
    snap = rs.read_snapshot("free@example.com")
    assert snap is not None
    assert snap["is_subscription"] is False


def test_apply_upgrades_free_to_pro(billing_db, monkeypatch):
    import api.billing.cloud_sync as cs
    from api.billing.entitlements import get_plan_for_user

    user = "user-cloud-1"
    assert get_plan_for_user(user) == "free"
    applied = cs.apply_remote_plan(user, "buyer@example.com",
                                   {"plan": "pro", "status": "active", "expires_at": None})
    assert applied == "pro"
    assert get_plan_for_user(user) == "pro"


def test_apply_never_downgrades_local_key(sync_env, billing_db):
    """Server says free — a locally activated pro key must survive."""
    from api.billing.cloud_sync import apply_remote_plan
    from api.billing.entitlements import get_plan_for_user
    from api.subscriptions import set_plan

    user = "user-cloud-2"
    set_plan(user, "lifetime")
    applied = apply_remote_plan(user, "buyer@example.com",
                                {"plan": "free", "status": "none", "expires_at": None})
    assert applied is None
    assert get_plan_for_user(user) == "lifetime"


def test_apply_is_idempotent(sync_env, billing_db):
    from api.billing.cloud_sync import apply_remote_plan

    user = "user-cloud-3"
    first = apply_remote_plan(user, "b@e.com", {"plan": "pro", "status": "active", "expires_at": None})
    second = apply_remote_plan(user, "b@e.com", {"plan": "pro", "status": "active", "expires_at": None})
    assert first == "pro" and second is None


# ── end-to-end: mint → activate in-app → entitlement flips ───────────


def test_activated_key_unlocks_pro_features(billing_db, billing_env, tmp_path, monkeypatch):
    """Full loop: billing-server keymint → app /premium/activate-key → pro."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cryptography.hazmat.primitives import serialization

    import api.premium as premium
    from api.dependencies import get_current_user
    from api.middleware.auth_middleware import User

    # Point the app's public-key loader at the fixture's keypair, and the
    # keygen storage writer at the test warehouse (never the real one).
    from config import settings
    from pathlib import Path as _P
    import keygen_app.storage as kg_storage

    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())
    monkeypatch.setattr(
        kg_storage, "default_db", lambda: _P(settings.duckdb.database_path)
    )

    from api.auth import user_manager
    user_id = user_manager.add_user(
        "keyuser_bound", "Str0ng!Passw0rd", role="viewer", email="keyuser@example.com"
    )

    user = User(user_id=user_id, username="keyuser_bound", role="viewer")
    app = FastAPI()
    app.include_router(premium.router)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    key = billing_env["keymint"].mint_key("pro", email="keyuser@example.com")
    r = client.post("/premium/activate-key", json={"key": key})
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "pro"

    # Bad key rejected.
    r = client.post("/premium/activate-key", json={"key": "DATAAGENT-BADKEY"})
    assert r.status_code == 400

    # Entitlements flipped for the activating user.
    from api.billing.entitlements import get_plan_for_user, has_feature
    assert get_plan_for_user(user_id) == "pro"
    assert has_feature(user_id, "dashboard_export") is True


def test_cloud_sync_via_sync_user_applies_plan(sync_env, billing_db, monkeypatch):
    """sync_user end-to-end with a mocked HTTP layer."""
    from api.billing.entitlements import get_plan_for_user

    monkeypatch.setattr(sync_env.httpx, "get",
                        _fake_httpx_get({"plan": "lifetime", "status": "active", "expires_at": None}))
    applied = sync_env.sync_user("user-cloud-4", "life@example.com")
    assert applied == "lifetime"
    assert get_plan_for_user("user-cloud-4") == "lifetime"


# ── P2: server-signed tokens gate cloud_sync rows ───────────────────


def _point_app_at_fixture_keypair(billing_env, monkeypatch):
    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())


def test_apply_with_token_writes_cloud_sync_row(billing_db, billing_env, monkeypatch):
    """Token-bearing sync writes a cloud_sync row enforced by signature."""
    from api.auth import user_manager
    from api.billing import cloud_sync as cs
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import SCHEMA, get_connection
    from billing_server import keymint as server_keymint

    _point_app_at_fixture_keypair(billing_env, monkeypatch)
    user_id = user_manager.add_user(
        "cloudtok_user", "Str0ng!Passw0rd", role="viewer", email="cloudtok@example.com")
    token = server_keymint.mint_entitlement_token("cloudtok@example.com", "pro")
    applied = cs.apply_remote_plan(
        user_id, "cloudtok@example.com",
        {"plan": "pro", "status": "active", "expires_at": None,
         "source": "cloud_sync", "token": token})
    assert applied == "pro"
    assert get_plan_for_user(user_id) == "pro"
    with get_connection() as con:
        row = con.execute(
            f"""SELECT payment_provider FROM {SCHEMA}.subscriptions
                WHERE user_id = ? AND status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1""",
            [user_id]).fetchone()
    assert row and row[0] == "cloud_sync"


def test_apply_with_forged_token_rejected(billing_db, billing_env, monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from api.auth import user_manager
    from api.billing import cloud_sync as cs
    from api.billing.entitlements import get_plan_for_user

    _point_app_at_fixture_keypair(billing_env, monkeypatch)
    user_id = user_manager.add_user(
        "cloudtok_forge", "Str0ng!Passw0rd", role="viewer", email="forge@example.com")
    rogue = ed25519.Ed25519PrivateKey.generate()
    payload = {"product": "dataagent-ent", "ver": 1, "email": "forge@example.com",
               "plan": "pro", "iat": "2026-01-01T00:00:00+00:00",
               "exp": "2030-01-01T00:00:00+00:00", "grant_expires_at": None}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    forged = (base64.urlsafe_b64encode(raw).decode().rstrip("=") + "."
              + base64.urlsafe_b64encode(rogue.sign(raw)).decode().rstrip("="))
    applied = cs.apply_remote_plan(
        user_id, "forge@example.com",
        {"plan": "pro", "status": "active", "expires_at": None,
         "source": "cloud_sync", "token": forged})
    assert applied is None
    assert get_plan_for_user(user_id) == "free"
