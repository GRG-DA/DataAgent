"""RED tests: GET /subscriptions/me exposes renewal state (Phase 4, ADR-004).

The subscription page reads API.mySubscription() → GET /subscriptions/me and
renders the renewal line from the payload's ``renewal`` block. A stale
snapshot triggers a best-effort phone-home before the payload is built.
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
    monkeypatch.setenv("BILLING_SYNC_URL", "https://billing.example.test")
    monkeypatch.setenv("BILLING_SYNC_SECRET", "unit_secret")
    import api.billing.renewal_snapshot as rs
    return rs


@pytest.fixture()
def unconfigured_sync(monkeypatch):
    """Force cloud sync unconfigured so no network call is ever attempted."""
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    import api.billing.cloud_sync as cs

    monkeypatch.setattr(cs, "billing_sync_base_url", lambda: "")
    monkeypatch.setattr(cs, "billing_sync_secret", lambda: "")


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


def _client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.subscriptions as subs

    app = FastAPI()
    app.include_router(subs.router)
    app.dependency_overrides[subs.get_current_user] = lambda: user
    return TestClient(app)


def test_me_exposes_renewal_for_subscriber(snap_env, billing_db):
    from api.auth import user_manager
    from api.middleware.auth_middleware import User

    user_id = user_manager.add_user(
        "renew_me_sub", "Str0ng!Passw0rd", role="viewer", email="renewme@example.com")
    renews = _iso(_now() + timedelta(days=17))
    snap_env.store_snapshot("renewme@example.com", {
        "plan": "pro", "status": "active", "expires_at": renews,
        "auto_renew": True, "cancel_at_period_end": False,
    })
    user = User(user_id=user_id, username="renew_me_sub", role="viewer")
    body = _client(user).get("/subscriptions/me").json()
    assert body["renewal"] is not None
    assert body["renewal"]["auto_renew"] is True
    assert body["renewal"]["cancel_at_period_end"] is False
    assert body["renewal"]["renews_at"] == renews


def test_me_renewal_none_for_non_subscriber(unconfigured_sync, billing_db):
    from api.auth import user_manager
    from api.middleware.auth_middleware import User

    user_id = user_manager.add_user(
        "renew_me_free", "Str0ng!Passw0rd", role="viewer", email="norenewme@example.com")
    user = User(user_id=user_id, username="renew_me_free", role="viewer")
    body = _client(user).get("/subscriptions/me").json()
    assert body["renewal"] is None


def test_me_phones_home_for_stale_snapshot(snap_env, billing_db, monkeypatch):
    from api.auth import user_manager
    from api.middleware.auth_middleware import User
    import api.billing.cloud_sync as cs

    user_id = user_manager.add_user(
        "renew_me_stale", "Str0ng!Passw0rd", role="viewer", email="staleui@example.com")
    _write_raw_snapshot("staleui@example.com", {
        "is_subscription": True, "auto_renew": True, "cancel_at_period_end": False,
        "renews_at": _iso(_now() + timedelta(hours=5)),
        "synced_at": _iso(_now() - timedelta(hours=25)),
    })
    calls = []
    monkeypatch.setattr(cs.httpx, "get", _fake_get({
        "plan": "pro", "status": "active",
        "expires_at": _iso(_now() + timedelta(days=31)),
        "auto_renew": False, "cancel_at_period_end": True,
    }, calls=calls))

    user = User(user_id=user_id, username="renew_me_stale", role="viewer")
    body = _client(user).get("/subscriptions/me").json()
    assert calls, "stale renewal snapshot must trigger a phone-home"
    assert body["renewal"]["auto_renew"] is False
    assert body["renewal"]["cancel_at_period_end"] is True
