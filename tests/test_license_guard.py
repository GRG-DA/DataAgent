"""Anti-crack license guard — tampered DB rows fail closed to free.

Covers:
- frozen builds ignore LICENSE_PUBLIC_KEY env override
- default admin passcode never unlocks
- hand-edited keygen pro rows without a valid stored key -> free
- valid activation survives re-verification
- legacy user_subscriptions pro without a key -> free
- email mismatch / expired / corrupt key -> free
- offline grace: valid keys work offline inside grace, require online after
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path as _P

import pytest


def _new_user(username: str, email: str = "") -> str:
    from api.auth import user_manager

    uid = user_manager.add_user(username, "Str0ng!Passw0rd", role="viewer", email=email)
    assert uid, f"could not create test user {username}"
    return uid


@pytest.fixture()
def guard_env(billing_db, billing_env, monkeypatch):
    """Wire fixture keypair + temp warehouse for license_guard tests."""
    from cryptography.hazmat.primitives import serialization
    from config import settings
    import keygen_app.storage as kg_storage

    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    pem = pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem.decode())
    # The storage layer resolves the warehouse lazily (so a frozen build
    # targets the writable runtime dir rather than the PyInstaller bundle).
    # Patch the resolver, not a snapshot constant.
    monkeypatch.setattr(
        kg_storage, "default_db", lambda: _P(settings.duckdb.database_path)
    )
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    return billing_env


def _activate_key(user_id: str, key: str) -> None:
    """Write activation rows the same way the app does (keygen path)."""
    from keygen_app.keygen import validate_key, bound_email
    from cryptography.hazmat.primitives import serialization
    from keygen_app.config import load_public_key
    from keygen_app.storage import activate
    from api.billing import redemption
    from keygen_app.keygen import key_hash
    import os

    payload = validate_key(key, load_public_key())
    # record owner like api/premium.py does
    redemption.record_local_claim(key_hash(key), user_id, bound_email(payload))
    activate(user_id=user_id, plan=payload["plan"],
             license_key=key, expires_at=payload.get("expiresAt"))


# ── config hardening ────────────────────────────────────────────────


def test_frozen_ignores_env_override(monkeypatch):
    import sys
    import keygen_app.config as cfg

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")
    monkeypatch.setenv("LICENSE_PUBLIC_FINGERPRINT", "0" * 16)
    # Frozen must load the baked key, not the env attacker key.
    key = cfg.load_public_key()
    assert cfg.public_fingerprint(key) == "deb8b1e5d88e42e4"
    # Fingerprint check passes for the genuine baked key.
    assert cfg.check_public_key_fingerprint() == "deb8b1e5d88e42e4"


def test_default_admin_passcode_fails_closed():
    import keygen_app.config as cfg

    if cfg.ADMIN_PASSCODE_HASH == hashlib.sha256(b"change-me").hexdigest():
        assert cfg.check_admin_passcode("change-me") is False
        assert cfg.check_admin_passcode("anything") is False


# ── re-verification ─────────────────────────────────────────────────


def test_valid_activation_survives_reverification(guard_env):
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_owner_1", email="guard1@example.com")
    key = guard_env["keymint"].mint_key("pro", email="guard1@example.com")
    _activate_key(uid, key)
    assert get_plan_for_user(uid) == "pro"
    # Second resolution re-verifies signature — still pro.
    assert get_plan_for_user(uid) == "pro"


def test_hand_edited_pro_without_key_fails_closed(guard_env):
    """Attacker inserts a keygen billing row but owns no key."""
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import SCHEMA, get_connection
    import uuid
    from datetime import datetime, timezone

    uid = _new_user("guard_tamper_1", email="tamper1@example.com")
    assert get_plan_for_user(uid) == "free"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as con:
        con.execute(
            f"""INSERT INTO {SCHEMA}.subscriptions
                (id, user_id, plan_id, status, billing_cycle, start_date,
                 current_period_start, current_period_end, payment_provider,
                 created_at, updated_at)
                VALUES (?, ?, 'plan_pro', 'ACTIVE', 'ONETIME', ?, ?, NULL,
                        'keygen', ?, ?)""",
            [str(uuid.uuid4()), uid, now, now, now, now],
        )
    # No stored license_key exists → guard lapses the row → free.
    assert get_plan_for_user(uid) == "free"


def test_corrupt_stored_key_downgrades(guard_env):
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import SCHEMA, get_connection

    uid = _new_user("guard_owner_2", email="guard2@example.com")
    key = guard_env["keymint"].mint_key("pro", email="guard2@example.com")
    _activate_key(uid, key)
    assert get_plan_for_user(uid) == "pro"
    # Corrupt the stored key (attacker swaps/deletes it).
    with get_connection() as con:
        con.execute(
            f"UPDATE {SCHEMA}.premium_settings SET value = ? "
            "WHERE key = 'license_key'",
            ["DATAAGENT-CORRUPT"],
        )
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(uid) == "free"


def test_email_mismatch_downgrades(guard_env):
    """Key bound to buyer A does not entitle account B, even with a DB row."""
    from api.billing.entitlements import get_plan_for_user
    from keygen_app.storage import activate
    from api.billing import redemption
    from keygen_app.keygen import key_hash

    owner = _new_user("guard_owner_3", email="owner3@example.com")
    other = _new_user("guard_other_3", email="other3@example.com")
    key = guard_env["keymint"].mint_key("pro", email="owner3@example.com")
    _activate_key(owner, key)
    assert get_plan_for_user(owner) == "pro"
    # Attacker copies the same key + a billing row onto another account.
    redemption.record_local_claim(key_hash(key) + "x", other, "owner3@example.com")
    activate(user_id=other, plan="pro", license_key=key, expires_at=None)
    # Stored key is bound to owner3, not other3 → free.
    assert get_plan_for_user(other) == "free"


def test_legacy_pro_without_key_fails_closed(guard_env):
    """Hand-edited analytics.user_subscriptions plan=pro with no key → free."""
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import SCHEMA, get_connection
    from datetime import datetime, timezone

    uid = _new_user("guard_legacy_1", email="legacy1@example.com")
    assert get_plan_for_user(uid) == "free"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as con:
        con.execute(
            f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.user_subscriptions (
                user_id VARCHAR PRIMARY KEY, plan VARCHAR NOT NULL DEFAULT 'free',
                status VARCHAR NOT NULL DEFAULT 'active',
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
        )
        row = con.execute(
            f"SELECT user_id FROM {SCHEMA}.user_subscriptions WHERE user_id = ?",
            [uid],
        ).fetchone()
        if row:
            con.execute(
                f"UPDATE {SCHEMA}.user_subscriptions SET plan = 'pro', updated_at = ? WHERE user_id = ?",
                [now, uid],
            )
        else:
            con.execute(
                f"""INSERT INTO {SCHEMA}.user_subscriptions
                    (user_id, plan, status, started_at, updated_at)
                    VALUES (?, 'pro', 'active', ?, ?)""",
                [uid, now, now],
            )
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(uid) == "free"


# ── offline grace (P1) ────────────────────────────────────────────


def _configure_remote(monkeypatch, redeem_fn=None):
    """Pretend the billing server is configured, with a stubbed redeem call."""
    from api.billing import redemption

    monkeypatch.setattr(redemption, "is_remote_configured", lambda: True)
    if redeem_fn is not None:
        monkeypatch.setattr(redemption, "redeem_remote", redeem_fn)
    return redemption


def _set_remote_check(iso_or_none):
    from api.billing.schema import SCHEMA, get_connection

    with get_connection() as con:
        try:
            row = con.execute(
                f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?",
                ["license_last_remote_check"],
            ).fetchone()
        except Exception:  # noqa: BLE001 — table missing
            return
        if iso_or_none is None:
            con.execute(
                f"DELETE FROM {SCHEMA}.premium_settings WHERE key = ?",
                ["license_last_remote_check"],
            )
        elif row:
            con.execute(
                f"UPDATE {SCHEMA}.premium_settings SET value = ? WHERE key = ?",
                [iso_or_none, "license_last_remote_check"],
            )
        else:
            con.execute(
                f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
                ["license_last_remote_check", iso_or_none],
            )
    from api.billing.cache import clear_request_cache

    clear_request_cache()


def _set_keygen_created_at(user_id: str, iso: str):
    from api.billing.schema import SCHEMA, get_connection

    with get_connection() as con:
        con.execute(
            f"""UPDATE {SCHEMA}.subscriptions SET created_at = ?
                WHERE user_id = ? AND payment_provider = 'keygen'""",
            [iso, user_id],
        )
    from api.billing.cache import clear_request_cache

    clear_request_cache()


def _unreachable(*args, **kwargs):
    import httpx as _hx

    raise _hx.ConnectError("offline")


def test_offline_within_grace_stays_pro(guard_env, monkeypatch):
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_grace_ok", email="grace-ok@example.com")
    key = guard_env["keymint"].mint_key("lifetime", email="grace-ok@example.com")
    _activate_key(uid, key)
    assert get_plan_for_user(uid) == "lifetime"
    # Server configured but last check was recent → no network needed.
    _configure_remote(monkeypatch, _unreachable)
    _set_remote_check((datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert get_plan_for_user(uid) == "lifetime"


def test_offline_beyond_grace_drops_to_free(guard_env, monkeypatch):
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_grace_old", email="grace-old@example.com")
    key = guard_env["keymint"].mint_key("lifetime", email="grace-old@example.com")
    _activate_key(uid, key)
    assert get_plan_for_user(uid) == "lifetime"
    _configure_remote(monkeypatch, _unreachable)
    _set_remote_check((datetime.now(timezone.utc) - timedelta(days=20)).isoformat())
    assert get_plan_for_user(uid) == "free"


def test_no_check_row_uses_grant_age(guard_env, monkeypatch):
    """Deleting the check row cannot reset the clock — grant age applies."""
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_grace_age", email="grace-age@example.com")
    key = guard_env["keymint"].mint_key("lifetime", email="grace-age@example.com")
    _activate_key(uid, key)
    _configure_remote(monkeypatch, _unreachable)
    _set_remote_check(None)
    _set_keygen_created_at(
        uid, (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    )
    assert get_plan_for_user(uid) == "free"


def test_online_valid_refreshes_grace(guard_env, monkeypatch):
    from api.billing import license_guard
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_grace_re", email="grace-re@example.com")
    key = guard_env["keymint"].mint_key("lifetime", email="grace-re@example.com")
    _activate_key(uid, key)
    _configure_remote(
        monkeypatch, lambda *a, **k: {"status": "already_owner"}
    )
    _set_remote_check((datetime.now(timezone.utc) - timedelta(days=20)).isoformat())
    assert get_plan_for_user(uid) == "lifetime"
    # Successful check stamps now — next offline check is inside grace.
    assert license_guard._last_remote_check() is not None
    assert (
        datetime.now(timezone.utc) - license_guard._last_remote_check()
    ) < timedelta(hours=1)


def test_remote_revoked_after_activation_drops_to_free(guard_env, monkeypatch):
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_grace_rev", email="grace-rev@example.com")
    key = guard_env["keymint"].mint_key("lifetime", email="grace-rev@example.com")
    _activate_key(uid, key)
    assert get_plan_for_user(uid) == "lifetime"
    _configure_remote(
        monkeypatch, lambda *a, **k: {"status": "refunded"}
    )
    _set_remote_check(None)
    assert get_plan_for_user(uid) == "free"


def test_grace_days_env_clamped(monkeypatch):
    from api.billing import license_guard

    monkeypatch.setenv("LICENSE_GRACE_DAYS", "0")
    assert license_guard.grace_days() == 1
    monkeypatch.setenv("LICENSE_GRACE_DAYS", "999")
    assert license_guard.grace_days() == 90
    monkeypatch.setenv("LICENSE_GRACE_DAYS", "banana")
    assert license_guard.grace_days() == 14


# ── P2: cloud_sync rows require a server-signed token ───────────────


def _insert_cloud_sync_row(user_id: str, plan: str = "pro"):
    import uuid
    from api.billing.schema import SCHEMA, get_connection

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as con:
        con.execute(
            f"""INSERT INTO {SCHEMA}.subscriptions
                (id, user_id, plan_id, status, billing_cycle, start_date,
                 current_period_start, current_period_end, payment_provider,
                 created_at, updated_at)
                VALUES (?, ?, ?, 'ACTIVE', 'MONTHLY', ?, ?, NULL,
                        'cloud_sync', ?, ?)""",
            [str(uuid.uuid4()), user_id, f"plan_{plan}", now, now, now, now],
        )
    from api.billing.cache import clear_request_cache

    clear_request_cache()


def test_cloud_sync_row_without_token_fails_closed(guard_env):
    """Hand-inserted cloud_sync row with no server token grants nothing."""
    from api.billing.entitlements import get_plan_for_user

    uid = _new_user("guard_cloud_1", email="cloud1@example.com")
    _insert_cloud_sync_row(uid, "pro")
    assert get_plan_for_user(uid) == "free"


def test_cloud_sync_row_with_valid_token_grants(guard_env):
    from api.billing.entitlement_token import store_token
    from api.billing.entitlements import get_plan_for_user
    from billing_server import keymint as server_keymint

    uid = _new_user("guard_cloud_2", email="cloud2@example.com")
    _insert_cloud_sync_row(uid, "pro")
    store_token("cloud2@example.com",
                server_keymint.mint_entitlement_token("cloud2@example.com", "pro"))
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(uid) == "pro"


def test_cloud_sync_row_with_expired_token_fails_closed(guard_env):
    from api.billing.entitlement_token import store_token
    from api.billing.entitlements import get_plan_for_user
    from billing_server import keymint as server_keymint

    uid = _new_user("guard_cloud_3", email="cloud3@example.com")
    _insert_cloud_sync_row(uid, "pro")
    # Token minted then backdated past expiry is rejected on verify.
    token = server_keymint.mint_entitlement_token("cloud3@example.com", "pro", days=1)
    import base64
    import json

    raw_b64, sig_b64 = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(raw_b64 + "=" * ((4 - len(raw_b64) % 4) % 4)))
    raw["exp"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    new_raw = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    # Re-signing would need the server key — instead store the tampered
    # payload with the original signature so verification must fail.
    tampered = (base64.urlsafe_b64encode(new_raw).decode().rstrip("=") + "." + sig_b64)
    store_token("cloud3@example.com", tampered)
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(uid) == "free"


def test_cloud_sync_row_with_wrong_email_token_fails_closed(guard_env):
    from api.billing.entitlement_token import store_token
    from api.billing.entitlements import get_plan_for_user
    from billing_server import keymint as server_keymint

    uid = _new_user("guard_cloud_4", email="cloud4@example.com")
    _insert_cloud_sync_row(uid, "pro")
    store_token("cloud4@example.com",
                server_keymint.mint_entitlement_token("someone-else@example.com", "pro"))
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(uid) == "free"
