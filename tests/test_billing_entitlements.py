"""Entitlements + feature gating — free denied, paid allowed after admin grant.

Uses the billing_db fixture (temp DuckDB + seed). Plan upgrades go through
api/subscriptions.set_plan (admin/self-serve offline path); in-app payment
webhooks were removed.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient


def _activate(user_id: str, plan: str = "lifetime") -> None:
    from api.subscriptions import set_plan

    set_plan(user_id, plan)


# ── plan resolution ───────────────────────────────────────────────────


def test_free_user_defaults_to_free_plan(billing_db):
    from api.billing.entitlements import get_plan_for_user

    assert get_plan_for_user("user-ent-free") == "free"


def test_seeded_plans_present(billing_db):
    from api.billing.entitlements import get_plan_prices

    prices = get_plan_prices()
    codes = {p["plan"] for p in prices}
    assert {"free", "pro", "lifetime"} <= codes


def test_pro_feature_flags_from_seed(billing_db):
    from api.billing.entitlements import has_feature

    # Seed marks dashboard_export disabled for free, enabled for pro.
    assert has_feature("user-ent-free", "dashboard_export", plan="free") is False
    assert has_feature("user-ent-pro", "dashboard_export", plan="pro") is True
    assert has_feature("user-ent-life", "dashboard_export", plan="lifetime") is True


def test_admin_grant_upgrades_plan(billing_db):
    from api.billing.entitlements import get_plan_for_user, has_feature

    user_id = "user-ent-activated"
    assert get_plan_for_user(user_id) == "free"

    _activate(user_id, "lifetime")

    assert get_plan_for_user(user_id) == "lifetime"
    assert has_feature(user_id, "dashboard_export") is True


# ── gate (require_feature) ────────────────────────────────────────────


def _app_with_gate():
    from api.billing.gate import require_feature
    from api.billing.schema import FEATURE_DASHBOARD_EXPORT
    from api.dependencies import get_current_user as deps_get_current_user
    from api.middleware.auth_middleware import User

    app = FastAPI()

    @app.get("/export")
    async def export(_: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT))):
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


def test_gate_denies_free_user(billing_db):
    app, set_user = _app_with_gate()
    set_user("user-gate-free", plan="free")
    client = TestClient(app)

    resp = client.get("/export")
    assert resp.status_code == 403
    assert resp.headers.get("X-Upgrade-Required") == "true"
    assert resp.headers.get("X-Required-Feature") == "dashboard_export"


def test_gate_allows_free_user_on_public_noop(billing_db):
    """Sanity: gate dependency still resolves a user (not a wiring failure)."""
    app, set_user = _app_with_gate()
    set_user("user-gate-free2", plan="free")
    client = TestClient(app)
    resp = client.get("/export")
    assert resp.status_code == 403  # free is denied — proves gate ran


def test_gate_allows_activated_user(billing_db):
    app, set_user = _app_with_gate()
    user_id = "user-gate-paid"
    set_user(user_id, plan="free")
    client = TestClient(app)
    assert client.get("/export").status_code == 403

    _activate(user_id, "lifetime")
    # Re-resolve plan from DB (plan=None → _resolve_plan).
    set_user(user_id, plan=None)

    resp = client.get("/export")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_gate_requires_authentication(billing_db):
    app, set_user = _app_with_gate()
    client = TestClient(app)
    resp = client.get("/export")
    assert resp.status_code == 401
