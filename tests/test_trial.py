"""Pro trial — one 14-day Pro grant per user, server-side enforced.

Covers (TDD red first):
- free user can start trial once -> TRIALING pro, entitlements flip
- second start -> 409
- paid user cannot start trial -> 409
- expired trial -> free again (lazy expiry)
- endpoint requires auth -> 401
"""

from __future__ import annotations


def test_start_trial_grants_pro_entitlements(billing_db):
    from api.billing.subs import start_trial
    from api.billing.entitlements import get_plan_for_user, has_feature

    user_id = "user-trial-fresh"
    assert get_plan_for_user(user_id) == "free"

    sub = start_trial(user_id)
    assert sub["status"] == "TRIALING"
    assert sub["plan_id"] == "plan_pro"
    assert sub["trial_ends_at"]

    assert get_plan_for_user(user_id) == "pro"
    assert has_feature(user_id, "dashboard_export") is True
    assert has_feature(user_id, "automation") is True


def test_trial_only_once_per_user(billing_db):
    from api.billing.subs import start_trial, TrialAlreadyUsedError

    user_id = "user-trial-once"
    start_trial(user_id)
    try:
        start_trial(user_id)
    except TrialAlreadyUsedError:
        pass
    else:
        raise AssertionError("second trial should raise TrialAlreadyUsedError")


def test_paid_user_cannot_start_trial(billing_db):
    from api.billing.subs import ensure_billing_row, start_trial, TrialAlreadyUsedError

    user_id = "user-trial-paid"
    ensure_billing_row(user_id, "lifetime", never_expires=True)
    try:
        start_trial(user_id)
    except TrialAlreadyUsedError:
        pass
    else:
        raise AssertionError("paid user trial should raise TrialAlreadyUsedError")


def test_expired_trial_returns_to_free(billing_db):
    from datetime import datetime, timedelta, timezone

    from api.billing.subs import start_trial
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import get_connection, SCHEMA

    user_id = "user-trial-expired"
    sub = start_trial(user_id)
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).isoformat()
    with get_connection() as con:
        con.execute(
            f"""UPDATE {SCHEMA}.subscriptions
                SET trial_ends_at = ?, current_period_end = ?, expires_at = ?
                WHERE id = ?""",
            [past, past, past, sub["id"]],
        )
        # Keep the legacy mirror consistent so the fallback cannot resurrect pro.
        con.execute(
            f"""UPDATE {SCHEMA}.user_subscriptions
                SET expires_at = ? WHERE user_id = ?""",
            [past, user_id],
        )
    from api.billing.cache import clear_request_cache

    clear_request_cache()
    assert get_plan_for_user(user_id) == "free"


def test_trial_endpoint_requires_auth(billing_db):
    from fastapi.testclient import TestClient

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = client.post("/subscriptions/trial/start")
    assert resp.status_code == 401


def test_trial_endpoint_happy_path(billing_db):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers, ensure_user
    from api.auth import user_manager

    ensure_user("trial_api_user", "Str0ng!Passw0rd", role="viewer")
    info = user_manager.get_user_info("trial_api_user")
    user_id = info["user_id"]
    # Force free (no prior subscription rows for this fresh user).
    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user(user_id) == "free"

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = client.post("/subscriptions/trial/start", headers=auth_headers("trial_api_user", "Str0ng!Passw0rd", role="viewer"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "pro"
    assert body["status"] == "TRIALING"

    # Second call -> 409, single use enforced server-side.
    resp2 = client.post("/subscriptions/trial/start", headers=auth_headers("trial_api_user", "Str0ng!Passw0rd", role="viewer"))
    assert resp2.status_code == 409


def test_trial_disabled_returns_403(billing_db, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers, ensure_user

    monkeypatch.setenv("BILLING_TRIAL_ENABLED", "0")
    ensure_user("trial_disabled_user", "Str0ng!Passw0rd", role="viewer")
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = client.post(
        "/subscriptions/trial/start",
        headers=auth_headers("trial_disabled_user", "Str0ng!Passw0rd", role="viewer"),
    )
    assert resp.status_code == 403


def test_trial_dedups_by_google_email(billing_db):
    from api.billing.subs import start_trial, TrialAlreadyUsedError

    start_trial("user-trial-mail-a", email="same@example.com")
    try:
        start_trial("user-trial-mail-b", email="same@example.com")
    except TrialAlreadyUsedError:
        pass
    else:
        raise AssertionError("same Google email on a new id should be refused")


def test_trial_status_shape(billing_db):
    from api.billing.subs import get_trial_status, start_trial

    fresh = get_trial_status("user-trial-status-fresh")
    assert fresh == {
        "eligible": True,
        "active": False,
        "ends_at": None,
        "days": 14,
        "enabled": True,
    }
    sub = start_trial("user-trial-status-fresh")
    active = get_trial_status("user-trial-status-fresh")
    assert active["eligible"] is False
    assert active["active"] is True
    assert active["ends_at"] == sub["trial_ends_at"]
