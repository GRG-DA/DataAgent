"""
Subscriptions — per-account plans (free / pro / lifetime).

Stored in DuckDB table `analytics.user_subscriptions`, keyed by user_id.
In-app payment checkout was removed; paid plans are granted by an admin
(POST /subscriptions/{user_id}) or imported from the external billing site.

**One price list.**  The amounts below are the *same* amounts the external
checkout charges (``billing_server/config.py`` -> ``PLANS``): Indian rupees,
paise in the smallest unit.  There is no second "display" price any more —
the app used to show Pro at $6/mo while the checkout billed ₹499/mo, which
is two price lists for one plan and a refund waiting to happen (C2).  The
checkout is the authority for what a customer is charged; this dict and
``api/billing/seed.py`` -> ``PRICES`` mirror it, and
``tests/test_launch_billing_legal.py`` fails if they drift apart.

**Lifetime is the headline tier** (LAUNCH-READINESS §8): it is the tier
whose price can absorb a metered AI allowance, and it is the flow the
keygen / offline-verification path already supports.  Pro stays as a
capped, secondary tier.

**No plan has unlimited AI.**  ``ai_requests_daily`` is a hard daily cap
in requests; the arithmetic behind the numbers is documented in
``api/billing/seed.py`` (C3).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.auth_middleware import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "user_subscriptions"
SCHEMA = "analytics"

#: The currency every price in this file is expressed in.  The checkout
#: bills in INR (Razorpay), so the app shows INR too.
CURRENCY = "INR"

PLANS = {
    "free": {
        "label": "Free",
        "price": 0,
        "currency": CURRENCY,
        "cycle": "MONTHLY",
        "recommended": False,
        "max_dashboards": 3,
        "max_chat_history_days": 7,
        "ai_requests_daily": 25,
        "features": [
            "Basic chat",
            "25 AI requests a day",
            "3 saved dashboards",
            "7-day history",
            "Ads shown — upgrade to remove",
            "AI without IP rotation",
        ],
    },
    "pro": {
        "label": "Pro",
        "price": 499,
        "currency": CURRENCY,
        "cycle": "MONTHLY",
        "recommended": False,
        "max_dashboards": 50,
        "max_chat_history_days": 365,
        "ai_requests_daily": 50,
        "features": [
            "50 AI requests a day",
            "50 saved dashboards",
            "1-year history",
            "Priority support",
            "Ad-free experience",
            "AI with IP rotation",
        ],
    },
    "lifetime": {
        "label": "Lifetime",
        "price": 10000,
        "currency": CURRENCY,
        "cycle": "ONETIME",
        "recommended": True,
        "max_dashboards": 1000,
        "max_chat_history_days": 3650,
        "ai_requests_daily": 100,
        "features": [
            "100 AI requests a day",
            "Everything in Pro",
            "1000 saved dashboards",
            "Lifetime access",
            "Early features",
            "Ad-free experience",
            "AI with IP rotation",
        ],
    },
}


def _get_con():
    import api.state as _state

    _loader = _state.loader
    if _loader is None:
        from config import settings
        from etl.load import WarehouseLoader
        _state.loader = WarehouseLoader(
            db_path=settings.duckdb.database_path,
            schema=settings.duckdb.schema,
            config=settings.duckdb.config,
        )
        return _state.loader.get_connection()
    return _loader.get_connection()


def _ensure_table() -> None:
    with _get_con() as con:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE} (
                user_id VARCHAR,
                plan VARCHAR NOT NULL DEFAULT 'free',
                status VARCHAR NOT NULL DEFAULT 'active',
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _row_to_sub(row: tuple) -> dict:
    return {
        "user_id": row[0],
        "plan": row[1],
        "status": row[2],
        "started_at": row[3],
        "expires_at": row[4],
        "updated_at": row[5],
    }


def ensure_subscription(user_id: str, default_plan: str = "free") -> dict:
    """Create a subscription row if missing; return the current subscription."""
    _ensure_table()
    plan = default_plan if default_plan in PLANS else "free"
    with _get_con() as con:
        row = con.execute(
            f"SELECT user_id, plan, status, started_at, expires_at, updated_at FROM {SCHEMA}.{TABLE} WHERE user_id = ?",
            [user_id],
        ).fetchone()
        if row is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            con.execute(
                f"INSERT INTO {SCHEMA}.{TABLE} (user_id, plan, status, started_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                [user_id, plan, now, now],
            )
            row = con.execute(
                f"SELECT user_id, plan, status, started_at, expires_at, updated_at FROM {SCHEMA}.{TABLE} WHERE user_id = ?",
                [user_id],
            ).fetchone()
    return _row_to_sub(row)


def get_plan(user_id: str) -> str:
    """Return the effective plan name for a user (defaults to 'free')."""
    _ensure_table()
    with _get_con() as con:
        row = con.execute(
            f"SELECT plan, status, expires_at FROM {SCHEMA}.{TABLE} WHERE user_id = ?",
            [user_id],
        ).fetchone()
    if not row:
        return "free"
    plan, status, expires_at = row
    if status != "active":
        return "free"
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) < datetime.now(timezone.utc).replace(tzinfo=None):
                return "free"
        except (ValueError, TypeError):
            pass
    return plan if plan in PLANS else "free"


def set_plan(user_id: str, plan: str, duration_days: Optional[int] = None,
             provider: str = "razorpay") -> dict:
    """Set a user's plan. duration_days sets an expiry (None = never expires).

    Writes through to the canonical analytics.subscriptions table (best-effort)
    so entitlements resolve from a single source of truth. ``provider`` marks
    the grant source (``razorpay`` for admin/manual, ``cloud_sync`` for
    server-token grants so the guard can enforce the signature).
    """
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")
    _ensure_table()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = None
    if duration_days:
        expires_at = (now + timedelta(days=duration_days)).isoformat()
    with _get_con() as con:
        row = con.execute(
            f"SELECT user_id FROM {SCHEMA}.{TABLE} WHERE user_id = ?",
            [user_id],
        ).fetchone()
        if row is None:
            con.execute(
                f"INSERT INTO {SCHEMA}.{TABLE} (user_id, plan, status, started_at, expires_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?)",
                [user_id, plan, now.isoformat(), expires_at, now.isoformat()],
            )
        else:
            con.execute(
                f"UPDATE {SCHEMA}.{TABLE} SET plan = ?, expires_at = ?, updated_at = ? WHERE user_id = ?",
                [plan, expires_at, now.isoformat(), user_id],
            )
        # Write-through to the canonical billing table (best-effort).
        try:
            from api.billing.subs import ensure_billing_row
            ensure_billing_row(
                user_id,
                plan,
                billing_cycle="ONETIME" if duration_days is None else "MONTHLY",
                status="ACTIVE",
                period_end=expires_at,
                never_expires=duration_days is None,
                con=con,
                provider=provider,
            )
        except Exception:  # noqa: BLE001
            pass
        row = con.execute(
            f"SELECT user_id, plan, status, started_at, expires_at, updated_at FROM {SCHEMA}.{TABLE} WHERE user_id = ?",
            [user_id],
        ).fetchone()
    return _row_to_sub(row)


@router.get("/subscriptions/plans")
async def list_plans():
    """List available plans and their entitlements."""
    return {"plans": PLANS}


def _trial_identity(user: User) -> tuple[str, str]:
    """Server-side identity for trial dedup (never trusts client input)."""
    email = (user.email or "").strip().lower()
    sso_subject = ""
    try:
        from api.auth import user_manager

        info = user_manager.get_user_info_by_id(user.user_id) or {}
        email = str(info.get("email", email or "")).strip().lower()
        sso_subject = str(info.get("sso_subject", "") or "")
    except Exception:  # noqa: BLE001
        pass
    return email, sso_subject


@router.post("/subscriptions/trial/start")
async def start_pro_trial(user: User = Depends(get_current_user)):
    """Start a one-time Pro trial (default 14 days, TRIALING status).

    Single use per account (user_id + Google identity). Paid users and
    prior trial users get 409. Disabled via BILLING_TRIAL_ENABLED=0 (403).
    """
    from api.billing.subs import (
        TrialAlreadyUsedError,
        TrialDisabledError,
        start_trial,
        trial_days,
    )

    email, sso_subject = _trial_identity(user)
    try:
        sub = start_trial(user.user_id, email, sso_subject)
    except TrialDisabledError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except TrialAlreadyUsedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "plan": "pro",
        "status": sub["status"],
        "trial_ends_at": sub["trial_ends_at"],
        "current_period_end": sub["current_period_end"],
        "days": trial_days(),
        "message": f"Pro trial active for {trial_days()} days — paid features unlocked.",
    }


@router.get("/subscriptions/trial/status")
async def trial_status(user: User = Depends(get_current_user)):
    """Trial eligibility + active-trial state (drives the subscription UI)."""
    from api.billing.subs import get_trial_status

    email, sso_subject = _trial_identity(user)
    return {**get_trial_status(user.user_id, email, sso_subject), "plan": "pro"}


@router.get("/subscriptions/me")
async def my_subscription(user: User = Depends(get_current_user)):
    """Return the current user's subscription (with renewal state)."""
    email, sso_subject = _trial_identity(user)
    snap = None
    try:
        from api.billing.renewal_snapshot import read_snapshot, refresh_if_stale

        refresh_if_stale(user.user_id, email, sso_subject)
        snap = read_snapshot(email)
    except Exception:  # noqa: BLE001 — renewal info is best-effort
        snap = None
    sub = ensure_subscription(user.user_id)
    sub["plan_details"] = PLANS.get(sub["plan"], PLANS["free"])
    if snap and snap.get("is_subscription"):
        sub["renewal"] = snap
    elif snap and snap.get("expires_at"):
        # One-time grant window (ADR-016: FamGateway 31-day Pro). Not a
        # subscription — expose the expiry and a non-renewing flag so the
        # UI says "expires …, renew by paying again" instead of nothing.
        sub["renewal"] = {
            "is_subscription": False,
            "renews_at": None,
            "auto_renew": False,
            "cancel_at_period_end": False,
            "expires_at": snap.get("expires_at"),
            "synced_at": snap.get("synced_at"),
        }
    else:
        sub["renewal"] = None
    return sub


@router.post("/subscriptions/sync")
async def sync_subscription(user: User = Depends(get_current_user)):
    """Force an entitlement sync against the billing server.

    ``GET /subscriptions/me`` only phones home when the renewal snapshot is
    older than 24 hours, so a customer who bought a licence a minute ago
    sees no change. This route calls the sync **unconditionally**.

    The identity comes from the session, never from the request body. The
    local plan is never downgraded: if the billing server is unreachable the
    current plan is returned unchanged.
    """
    from datetime import datetime, timezone

    email, sso_subject = _trial_identity(user)
    synced = False
    detail = ""

    if not email:
        # The keyless entitlement path resolves the plan BY EMAIL. An account
        # without one cannot be matched to a purchase.
        detail = (
            "This account has no email address, so the billing server cannot "
            "match it to a purchase. Add your email address first."
        )
    else:
        try:
            from api.billing.cloud_sync import is_configured, sync_user

            if not is_configured():
                # The shipped default: no billing server is configured. That
                # is not a failure - the app is a self-hosted free plan.
                detail = (
                    "No billing server is configured on this install, so there "
                    "is nothing to sync. The plan shown is your local plan."
                )
            else:
                plan = sync_user(user.user_id, email, sso_subject)
                if plan:
                    synced = True
                else:
                    # sync_user returns None both when the plan is unchanged
                    # and when there is no purchase. Neither is a failure.
                    detail = (
                        "The billing server has no newer plan for this "
                        "account. The plan shown is your current plan."
                    )
        except Exception as e:  # noqa: BLE001 — never downgrade the local plan
            logger.warning("entitlement sync failed: %s", e)
            detail = "The billing server could not be reached."

    sub = ensure_subscription(user.user_id)
    sub["plan_details"] = PLANS.get(sub["plan"], PLANS["free"])
    sub["synced"] = synced
    sub["synced_at"] = datetime.now(timezone.utc).isoformat()
    if detail:
        sub["detail"] = detail
    return sub


@router.post("/subscriptions/me")
async def upgrade_subscription(body: Dict[str, Any], user: User = Depends(get_current_user)):
    """Upgrade the current user's plan.

    In-app payment checkout was removed (payments live on a separate site).
    Self-serve may only stay on / downgrade to Free; paid plans are granted
    by an admin via POST /subscriptions/{user_id}.
    """
    plan = str(body.get("plan", "")).lower()
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")
    if plan != "free":
        raise HTTPException(
            status_code=403,
            detail="Paid plans are managed externally. Contact an admin to upgrade.",
        )
    duration_days = body.get("duration_days")
    sub = set_plan(user.user_id, plan, duration_days)
    sub["plan_details"] = PLANS.get(sub["plan"], PLANS["free"])
    return sub


@router.post("/subscriptions/{user_id}")
async def admin_set_subscription(user_id: str, body: Dict[str, Any], user: User = Depends(get_current_user)):
    """Admin-only: set another user's plan."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    plan = str(body.get("plan", "")).lower()
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")
    duration_days = body.get("duration_days")
    sub = set_plan(user_id, plan, duration_days)
    sub["plan_details"] = PLANS.get(sub["plan"], PLANS["free"])
    return sub


@router.get("/billing/tenant-usage")
async def billing_tenant_usage(
    tenant_id: str, user: User = Depends(get_current_user)
):
    """Admin-only: a tenant's aggregate AI usage for today.

    A per-user cap alone lets one tenant's many users burn the shared LLM
    budget, so the tenant's aggregate usage is metered and capped too
    (ADR-009 D5 / BILLING_TENANT_AI_DAILY_CAP).
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    from api.billing.metering import get_tenant_usage

    return {"success": True, "usage": get_tenant_usage(tenant_id)}