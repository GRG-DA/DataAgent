"""RED tests: renewal snapshot store/read + staleness phone-home (Phase 4).

ADR-004: cloud_sync persists the last-known renewal state per email
(is_subscription / auto_renew / cancel_at_period_end / renews_at / synced_at)
so the subscription page renders offline. A stale snapshot (older than
SNAPSHOT_TTL_SECONDS, or a renewal within RENEWAL_SOON_SECONDS) triggers a
best-effort billing-server refresh on page view — fail-secure, never
downgrades.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

httpx = pytest.importorskip("httpx")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat()


@pytest.fixture()
def snap_env(tmp_path, monkeypatch):
    """Configure cloud sync against a fake billing host + expose the module."""
    monkeypatch.setenv("BILLING_SYNC_URL", "https://billing.example.test")
    monkeypatch.setenv("BILLING_SYNC_SECRET", "unit_secret")
    import api.billing.renewal_snapshot as rs
    return rs


def _fake_get(payload=None, status=200, calls=None):
    def _get(url, params=None, timeout=None):
        if calls is not None:
            calls.append(url)
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


def _write_raw_snapshot(email: str, snap: dict) -> None:
    """Insert a snapshot with an arbitrary (e.g. old) synced_at."""
    from api.billing.schema import SCHEMA, get_connection

    with get_connection() as con:
        con.execute(
            f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
                key VARCHAR PRIMARY KEY, value VARCHAR)"""
        )
        key = f"renewal_snapshot:{email.strip().lower()}"
        con.execute(f"DELETE FROM {SCHEMA}.premium_settings WHERE key = ?", [key])
        con.execute(
            f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
            [key, json.dumps(snap)],
        )


# ── store / read ─────────────────────────────────────────────────────


def test_store_read_snapshot_subscription(snap_env, billing_db):
    renews = _iso(_now() + timedelta(days=20))
    snap_env.store_snapshot("sub@example.com", {
        "plan": "pro", "status": "active", "expires_at": renews,
        "auto_renew": True, "cancel_at_period_end": False,
    })
    snap = snap_env.read_snapshot("sub@example.com")
    assert snap is not None
    assert snap["is_subscription"] is True
    assert snap["auto_renew"] is True
    assert snap["cancel_at_period_end"] is False
    assert snap["renews_at"] == renews
    assert snap["synced_at"]


def test_store_snapshot_non_subscription(snap_env, billing_db):
    snap_env.store_snapshot("life@example.com", {
        "plan": "lifetime", "status": "active", "expires_at": None,
    })
    snap = snap_env.read_snapshot("life@example.com")
    assert snap is not None
    assert snap["is_subscription"] is False
    assert snap["auto_renew"] is None
    assert snap["cancel_at_period_end"] is None


def test_read_snapshot_missing_returns_none(snap_env, billing_db):
    assert snap_env.read_snapshot("nobody@example.com") is None


# ── staleness ────────────────────────────────────────────────────────


def test_is_stale_true_for_missing_snapshot(snap_env):
    assert snap_env.is_stale(None) is True


def test_is_stale_false_for_fresh_snapshot(snap_env):
    snap = {
        "is_subscription": True,
        "renews_at": _iso(_now() + timedelta(days=20)),
        "synced_at": _iso(_now()),
    }
    assert snap_env.is_stale(snap) is False


def test_is_stale_true_when_synced_at_old(snap_env):
    snap = {
        "is_subscription": True,
        "renews_at": _iso(_now() + timedelta(days=20)),
        "synced_at": _iso(_now() - timedelta(hours=25)),
    }
    assert snap_env.is_stale(snap) is True


def test_is_stale_true_when_renewal_imminent(snap_env):
    snap = {
        "is_subscription": True,
        "renews_at": _iso(_now() + timedelta(hours=12)),
        "synced_at": _iso(_now()),
    }
    assert snap_env.is_stale(snap) is True


# ── refresh_if_stale (phone-home) ────────────────────────────────────


def test_refresh_if_stale_fresh_snapshot_no_phone_home(snap_env, billing_db, monkeypatch):
    snap_env.store_snapshot("fresh@example.com", {
        "plan": "pro", "status": "active",
        "expires_at": _iso(_now() + timedelta(days=20)),
        "auto_renew": True, "cancel_at_period_end": False,
    })
    import api.billing.cloud_sync as cs

    def boom(*a, **k):
        raise AssertionError("phone home must not run for a fresh snapshot")

    monkeypatch.setattr(cs.httpx, "get", boom)
    snap = snap_env.refresh_if_stale("user-fresh", "fresh@example.com")
    assert snap is not None and snap["auto_renew"] is True


def test_refresh_if_stale_phones_home_and_updates(snap_env, billing_db, monkeypatch):
    _write_raw_snapshot("stale@example.com", {
        "is_subscription": True, "auto_renew": True, "cancel_at_period_end": False,
        "renews_at": _iso(_now() + timedelta(hours=6)),
        "synced_at": _iso(_now() - timedelta(hours=25)),
    })
    import api.billing.cloud_sync as cs

    calls = []
    monkeypatch.setattr(cs.httpx, "get", _fake_get({
        "plan": "pro", "status": "active",
        "expires_at": _iso(_now() + timedelta(days=31)),
        "auto_renew": False, "cancel_at_period_end": True,
    }, calls=calls))
    snap = snap_env.refresh_if_stale("user-stale", "stale@example.com")
    assert calls, "a stale renewal snapshot must trigger a billing-server fetch"
    assert snap is not None
    assert snap["auto_renew"] is False
    assert snap["cancel_at_period_end"] is True


def test_refresh_if_stale_network_error_keeps_snapshot(snap_env, billing_db, monkeypatch):
    _write_raw_snapshot("flaky@example.com", {
        "is_subscription": True, "auto_renew": True, "cancel_at_period_end": False,
        "renews_at": _iso(_now() + timedelta(hours=6)),
        "synced_at": _iso(_now() - timedelta(hours=25)),
    })
    import api.billing.cloud_sync as cs

    def boom(url, params=None, timeout=None):
        raise cs.httpx.ConnectError("no network")

    monkeypatch.setattr(cs.httpx, "get", boom)
    snap = snap_env.refresh_if_stale("user-flaky", "flaky@example.com")
    assert snap is not None and snap["auto_renew"] is True  # fail-secure


def test_refresh_if_stale_unconfigured_no_call(snap_env, billing_db, monkeypatch):
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    import api.billing.cloud_sync as cs

    monkeypatch.setattr(cs, "billing_sync_base_url", lambda: "")
    monkeypatch.setattr(cs, "billing_sync_secret", lambda: "")

    def boom(*a, **k):
        raise AssertionError("no phone home when the billing server is unconfigured")

    monkeypatch.setattr(cs.httpx, "get", boom)
    _write_raw_snapshot("local@example.com", {
        "is_subscription": True, "auto_renew": True, "cancel_at_period_end": False,
        "renews_at": _iso(_now() + timedelta(hours=6)),
        "synced_at": _iso(_now() - timedelta(hours=25)),
    })
    snap = snap_env.refresh_if_stale("user-local", "local@example.com")
    assert snap is not None and snap["auto_renew"] is True


# ── subscription_renewal_for_user (user_id → email → snapshot) ───────


def test_subscription_renewal_for_user_resolves_email(snap_env, billing_db):
    from api.auth import user_manager

    user_id = user_manager.add_user(
        "renew_ui_user", "Str0ng!Passw0rd", role="viewer", email="ui@example.com")
    renews = _iso(_now() + timedelta(days=17))
    snap_env.store_snapshot("ui@example.com", {
        "plan": "pro", "status": "active", "expires_at": renews,
        "auto_renew": True, "cancel_at_period_end": False,
    })
    renewal = snap_env.subscription_renewal_for_user(user_id)
    assert renewal is not None
    assert renewal["renews_at"] == renews
    assert renewal["auto_renew"] is True


def test_subscription_renewal_for_user_none_when_not_subscriber(snap_env, billing_db):
    from api.auth import user_manager

    user_id = user_manager.add_user(
        "renew_ui_free", "Str0ng!Passw0rd", role="viewer", email="norenew@example.com")
    snap_env.store_snapshot("norenew@example.com", {"plan": "free", "status": "none"})
    assert snap_env.subscription_renewal_for_user(user_id) is None


# ── /config entitlements carry the renewal block ─────────────────────


def test_get_entitlements_includes_renewal_for_subscriber(snap_env, billing_db):
    from api.auth import user_manager
    from api.billing.entitlements import get_entitlements

    user_id = user_manager.add_user(
        "renew_ent_sub", "Str0ng!Passw0rd", role="viewer", email="ent@example.com")
    snap_env.store_snapshot("ent@example.com", {
        "plan": "pro", "status": "active",
        "expires_at": _iso(_now() + timedelta(days=17)),
        "auto_renew": True, "cancel_at_period_end": False,
    })
    ent = get_entitlements(user_id)
    assert ent.get("renewal"), "subscription entitlements must expose renewal state"
    assert ent["renewal"]["auto_renew"] is True


def test_get_entitlements_renewal_none_without_snapshot(snap_env, billing_db):
    from api.auth import user_manager
    from api.billing.entitlements import get_entitlements

    user_id = user_manager.add_user(
        "renew_ent_free", "Str0ng!Passw0rd", role="viewer", email="entfree@example.com")
    ent = get_entitlements(user_id)
    assert ent.get("renewal") is None
