"""AI request metering — daily cap for every plan, enforcement, bypasses.

Covers:
- seed: the free cap defaults to 25; the paid caps are finite too.
- metering: ai_requests uses a DAILY period key; other metrics stay monthly.
- /ask returns 429 with X-Upgrade-Required headers when the cap is hit.
- No plan has unlimited AI (C3).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── seed defaults ─────────────────────────────────────────────────────


def test_free_ai_cap_defaults_to_25(billing_db):
    from api.billing.entitlements import get_entitlements

    ent = get_entitlements("user-ai-free", plan="free")
    assert ent["max_ai_requests"] == 25


def test_pro_and_lifetime_ai_requests_are_capped(billing_db):
    """No plan has unlimited AI any more (C3): every cap is finite.

    Unlimited AI on a paid plan is a guaranteed negative margin — one
    request costs up to ~$0.04 (10k input + 2k output tokens), so 600
    requests a month is ~$30 of cost against $6 of revenue.
    """
    from api.billing.entitlements import get_entitlements

    pro = get_entitlements("user-ai-pro", plan="pro")["max_ai_requests"]
    lifetime = get_entitlements("user-ai-life", plan="lifetime")["max_ai_requests"]
    assert pro > 0
    assert lifetime > 0
    assert pro != -1
    assert lifetime != -1


def test_env_overrides_change_seeded_caps(billing_db, monkeypatch):
    import api.state  # noqa: F401  (ensure billing modules bound to this db)
    from api.billing.seed import seed_billing_data
    from api.billing.entitlements import get_entitlements
    from api.billing.cache import clear_request_cache

    monkeypatch.setenv("BILLING_FREE_AI_DAILY_CAP", "5")
    monkeypatch.setenv("BILLING_PRO_AI_DAILY_CAP", "500")
    clear_request_cache()
    seed_billing_data()

    assert get_entitlements("user-ai-free2", plan="free")["max_ai_requests"] == 5
    assert get_entitlements("user-ai-pro2", plan="pro")["max_ai_requests"] == 500


# ── daily vs monthly period keying ────────────────────────────────────


def test_ai_requests_use_daily_period(billing_db):
    from api.billing.metering import _period_for

    assert _period_for("ai_requests").count("-") == 2  # YYYY-MM-DD
    assert _period_for("dashboards").count("-") == 1   # YYYY-MM


def test_meter_blocks_after_daily_cap_and_resets_next_day(billing_db):
    from api.billing.metering import record_usage, UsageLimitError, _period_for

    user = "user-meter-cap"
    for _ in range(25):
        record_usage(user, "ai_requests")
    with pytest.raises(UsageLimitError):
        record_usage(user, "ai_requests")

    # Simulate the next day: a new period key resets the counter.
    period = _period_for("ai_requests")
    next_day = str(int(period[-2:]) + 1).zfill(2)
    monkey = next_day if int(next_day) <= 28 else "01"
    # monkeypatch the period function to hand back a next-day key.
    from api.billing import metering as m
    original = m._period_for

    def _next_day(metric):
        p = original(metric)
        return p[:-2] + monkey if metric == "ai_requests" else p

    m._period_for = _next_day
    try:
        summary = record_usage(user, "ai_requests")
        assert summary["used"] == 1  # fresh day, counter restarted
    finally:
        m._period_for = original


# ── /ask enforcement (429 + upgrade headers) ──────────────────────────


def _real_user_id(username: str = "test_auth_user") -> str:
    """JWT auth resolves User.user_id from the user store, not the username —
    metering keys on that id, so tests must too."""
    from tests.conftest import ensure_user

    ensure_user(username, "Str0ng!Passw0rd")
    from api.auth import user_manager

    info = user_manager.get_user_info(username)
    assert info, f"test user {username} missing"
    return info["user_id"]


def _post_ask(client: TestClient, headers: dict):
    return client.post(
        "/ask",
        json={"question": "show sales by region", "table": "sales"},
        headers=headers,
    )


def test_ask_returns_429_when_free_cap_exhausted(billing_db, monkeypatch):
    from api.billing.metering import record_usage
    from tests.conftest import auth_headers

    user_id = _real_user_id()
    # Fill the free cap directly (deterministic — no LLM involved).
    for _ in range(25):
        record_usage(user_id, "ai_requests")

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = _post_ask(client, auth_headers())

    assert resp.status_code == 429
    assert resp.headers.get("X-Upgrade-Required") == "true"
    assert resp.headers.get("X-Required-Feature") == "ai_unlimited"
    assert "Daily AI request limit" in resp.json()["detail"]


def test_ask_unlimited_for_paid_plan(billing_db):
    from api.billing.metering import record_usage
    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    user_id = _real_user_id()
    # Grant through the canonical billing layer (the same layer metering
    # resolves plans through). set_plan() writes via a module-level loader
    # that can point at a previous test's tmp DB across tests.
    ensure_billing_row(user_id, "lifetime", never_expires=True)
    for _ in range(30):  # beyond the free cap
        record_usage(user_id, "ai_requests")

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    resp = _post_ask(client, auth_headers())
    # Must NOT be metered-blocked; either success or any non-429 app error.
    assert resp.status_code != 429


# ── gate enforcement on previously leaking surfaces ───────────────────


def _feature_app():
    """Tiny app reproducing the gate wiring used on pipeline endpoints."""
    from fastapi import Depends, FastAPI
    from api.billing.gate import require_feature
    from api.billing.schema import FEATURE_AUTOMATION
    from api.dependencies import get_current_user as deps_get_current_user
    from api.middleware.auth_middleware import User

    app = FastAPI()

    @app.get("/pipeline/list")
    async def list_pipelines(_: None = Depends(require_feature(FEATURE_AUTOMATION))):
        return {"ok": True}

    state = {"user": None}

    async def _fake_user():
        u = state["user"]
        if u is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return u

    app.dependency_overrides[deps_get_current_user] = _fake_user

    def set_user(user_id: str, plan: str | None):
        state["user"] = User(
            user_id=user_id,
            username=user_id,
            role="viewer",
            email=f"{user_id}@example.com",
            plan=plan,
            auth_method="test",
        )

    return app, set_user


def test_pipeline_gate_denies_free_user(billing_db):
    from fastapi import HTTPException  # noqa: F401

    app, set_user = _feature_app()
    set_user("user-pipe-free", plan="free")
    client = TestClient(app)

    resp = client.get("/pipeline/list")
    assert resp.status_code == 403
    assert resp.headers.get("X-Required-Feature") == "automation"


def test_pipeline_gate_allows_pro_user(billing_db):
    from api.subscriptions import set_plan

    app, set_user = _feature_app()
    user_id = "user-pipe-pro"
    set_plan(user_id, "pro")
    # Server-side resolution (plan=None): the gate must honour the DB grant.
    set_user(user_id, plan=None)
    client = TestClient(app)

    resp = client.get("/pipeline/list")
    assert resp.status_code == 200
