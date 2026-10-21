"""
Entitlements — resolve what a user is allowed to do based on their plan.

Single source of truth for plan gating. The auth middleware attaches a
`plan` to the User context; this module resolves the full entitlement set
(ads_visible, ip_rotation, ai_unlimited, quotas) from the billing tables,
falling back to the legacy api.subscriptions PLANS dict when the billing
tables aren't seeded yet.
"""
from __future__ import annotations

import logging
from typing import Optional

from api.billing.schema import get_connection, SCHEMA
from api.billing.cache import request_cache
from api.billing.subs import find_effective_subscription

logger = logging.getLogger(__name__)

# Fallback entitlements (mirror api.subscriptions.PLANS) used when the
# billing tables are unavailable. Kept in sync with seed.py.
_FALLBACK = {
    "free": {
        "ads_visible": True,
        "ip_rotation": False,
        "ai_unlimited": False,
        "recreate_from_image": False,
        "dashboard_export": False,
        "alerts_thresholds": False,
        "scheduled_reports": False,
        "smart_monitoring": False,
        "live_monitor": False,
        "api_connection": False,
        "enterprise": False,
        "governance": False,
        "external_sources": False,
        "storytelling": False,
        "reports": False,
        "export_page": False,
        "automation": False,
        "connect_page": False,
        "marketplace": False,
        "organization": False,
        "max_dashboards": 3,
        "max_ai_requests": 25,
        "max_chat_history_days": 7,
    },
    "pro": {
        "ads_visible": False,
        "ip_rotation": True,
        "ai_unlimited": True,
        "recreate_from_image": True,
        "dashboard_export": True,
        "alerts_thresholds": True,
        "scheduled_reports": True,
        "smart_monitoring": True,
        "live_monitor": True,
        "api_connection": True,
        "enterprise": True,
        "governance": True,
        "external_sources": True,
        "storytelling": True,
        "reports": True,
        "export_page": True,
        "automation": True,
        "connect_page": True,
        "marketplace": True,
        "organization": True,
        "max_dashboards": 50,
        "max_ai_requests": 50,
        "max_chat_history_days": 365,
    },
    "lifetime": {
        "ads_visible": False,
        "ip_rotation": True,
        "ai_unlimited": True,
        "recreate_from_image": True,
        "dashboard_export": True,
        "alerts_thresholds": True,
        "scheduled_reports": True,
        "smart_monitoring": True,
        "live_monitor": True,
        "api_connection": True,
        "enterprise": True,
        "governance": True,
        "external_sources": True,
        "storytelling": True,
        "reports": True,
        "export_page": True,
        "automation": True,
        "connect_page": True,
        "marketplace": True,
        "organization": True,
        "max_dashboards": 1000,
        "max_ai_requests": 100,
        "max_chat_history_days": 3650,
    },
}


def _normalize_plan(plan: Optional[str]) -> str:
    """Map legacy plan codes to canonical ones."""
    if not plan:
        return "free"
    plan = plan.lower().strip()
    if plan == "premium":
        return "pro"
    if plan not in _FALLBACK:
        return "free"
    return plan


def get_plan_for_user(user_id: str) -> str:
    """Return the canonical plan code for a user.

    Resolves through the canonical subscription layer (lazy expiry, identity
    fallback), then falls back to the legacy analytics.user_subscriptions
    table.

    Anti-tamper: keygen-sourced paid plans are re-verified against the
    stored Ed25519 product key on every call (signature + expiry + email
    binding); cloud_sync-sourced plans require a valid server-signed token.
    Tampered DB rows fail closed to free — see api.billing.license_guard.
    """
    try:
        sub = find_effective_subscription(user_id)
        if sub:
            return _verify_effective(user_id, sub)
    except Exception as e:  # noqa: BLE001
        logger.debug("billing subscriptions lookup failed: %s", e)

    # Fall back to the Google identity so a paid subscription follows the
    # Google account even if the local user_id changed (e.g. account recreated).
    try:
        from api.auth import user_manager
        info = user_manager.get_user_info_by_id(user_id)
        email = (info or {}).get("email", "")
        sso_subject = (info or {}).get("sso_subject", "")
        if email or sso_subject:
            sub = find_effective_subscription(user_id, email, sso_subject)
            if sub:
                return _verify_effective(user_id, sub)
    except Exception as e:  # noqa: BLE001
        logger.debug("google identity plan lookup failed: %s", e)

    try:
        from api.subscriptions import get_plan as legacy_get_plan
        from api.billing.license_guard import verify_legacy_fallback

        legacy = _normalize_plan(legacy_get_plan(user_id))
        try:
            return verify_legacy_fallback(user_id, legacy)
        except Exception:  # noqa: BLE001 — fail closed
            logger.debug("legacy license guard failed closed for %s", user_id)
            return "free"
    except Exception as e:  # noqa: BLE001
        logger.debug("legacy plan lookup failed: %s", e)
        return "free"


def _verify_effective(user_id: str, sub: dict) -> str:
    """Verify an effective subscription row by its grant source.

    - ``keygen``    → Ed25519 product-key path (license_guard).
    - ``cloud_sync``→ server-signed entitlement-token path (license_guard).
    - anything else (trial / razorpay / admin) → the row must carry a valid
      local grant signature over its own contents. Without one the row is
      treated as tampered and the plan is refused.

    Previously every non-keygen/non-cloud_sync provider was returned
    verbatim, so editing ``warehouse.duckdb`` to
    ``payment_provider='razorpay'`` + ``plan_id='plan_lifetime'`` granted a
    full paid entitlement with no key and no signature. Guard errors fail
    closed to free, never to the unverified plan.
    """
    plan = _normalize_plan((sub.get("plan_id") or "").replace("plan_", "", 1))
    provider = str(sub.get("payment_provider") or "").lower()
    try:
        if provider == "keygen":
            from api.billing.license_guard import verified_keygen_plan

            return verified_keygen_plan(user_id, plan)
        if provider == "cloud_sync":
            from api.billing.license_guard import verified_cloud_sync_plan

            return verified_cloud_sync_plan(user_id, plan)
        if plan == "free":
            return "free"
        # Local grant (trial / admin / manual): require a valid signature.
        from api.billing.grant_signature import verify_grant

        if not verify_grant(sub):
            logger.info(
                "local grant rejected for %s: missing or invalid signature (provider=%s)",
                user_id, provider or "none",
            )
            return "free"
        return plan
    except Exception:  # noqa: BLE001 — fail closed, never grant on error
        logger.debug("license guard failed closed for %s", user_id)
        return "free"


def get_entitlements(user_id: str, plan: Optional[str] = None) -> dict:
    """Resolve the full entitlement set for a user (cached per request).

    Returns a dict with feature flags and quotas:
        {
            "plan": "pro",
            "ads_visible": False,
            "ip_rotation": True,
            "ai_unlimited": True,
            "max_dashboards": 50,
            "max_chat_history_days": 365,
        }
    """
    cache = request_cache()
    if cache is not None:
        key = f"ent:{user_id}:{plan or ''}"
        if key in cache:
            return cache[key]
        result = _compute_entitlements(user_id, plan)
        cache[key] = result
        return result
    return _compute_entitlements(user_id, plan)


def _compute_entitlements(user_id: str, plan: Optional[str] = None) -> dict:
    """Compute the full entitlement set for a user (no caching)."""
    if plan is None:
        plan = _normalize_plan(get_plan_for_user(user_id))
    else:
        # Explicit plan is a what-if (flag matrix/tests) — but a caller
        # passing a paid plan for a user with tampered rows must not launder
        # it. Re-verify signed grants; no signed rows → unchanged (preserves
        # what-if semantics for trial/admin paths).
        plan = _normalize_plan(plan)
        if plan in ("pro", "lifetime"):
            try:
                from api.billing.license_guard import (
                    verified_cloud_sync_plan,
                    verified_keygen_plan,
                )

                plan = verified_keygen_plan(user_id, plan)
                plan = verified_cloud_sync_plan(user_id, plan)
            except Exception:  # noqa: BLE001 — fail closed
                plan = "free"
    base = dict(_FALLBACK.get(plan, _FALLBACK["free"]))
    base["plan"] = plan

    # Overlay feature flags from the billing tables when available.
    try:
        with get_connection() as con:
            plan_id = f"plan_{plan}"
            rows = con.execute(
                f"""SELECT feature_key, enabled FROM {SCHEMA}.plan_features
                    WHERE plan_id = ?""",
                [plan_id],
            ).fetchall()
            for key, enabled in rows:
                base[key] = bool(enabled)

            limit_rows = con.execute(
                f"""SELECT metric, limit_value FROM {SCHEMA}.plan_limits
                    WHERE plan_id = ?""",
                [plan_id],
            ).fetchall()
            for metric, limit_value in limit_rows:
                if metric == "dashboards":
                    base["max_dashboards"] = limit_value
                elif metric == "chat_history_days":
                    base["max_chat_history_days"] = limit_value
                elif metric == "ai_requests":
                    base["max_ai_requests"] = limit_value
    except Exception as e:  # noqa: BLE001
        logger.debug("billing feature overlay failed: %s", e)

    # Renewal state (subscription grants only) — drives the subscription page
    # and any /config consumer; None for key / lifetime / admin / free grants.
    try:
        from api.billing.renewal_snapshot import subscription_renewal_for_user

        base["renewal"] = subscription_renewal_for_user(user_id)
    except Exception:  # noqa: BLE001
        base["renewal"] = None

    return base


def get_plan_prices() -> list[dict]:
    """Return active prices for all visible plans (for the pricing UI)."""
    prices = []
    try:
        with get_connection() as con:
            rows = con.execute(
                f"""SELECT p.code, p.name, p.description, p.is_recommended,
                           pr.billing_cycle, pr.amount, pr.currency, pr.amount_provider
                    FROM {SCHEMA}.plans p
                    JOIN {SCHEMA}.plan_prices pr ON pr.plan_id = p.id
                    WHERE p.is_visible = TRUE AND p.is_active = TRUE AND pr.is_active = TRUE
                    ORDER BY p.display_order, pr.billing_cycle"""
            ).fetchall()
            for code, name, desc, recommended, cycle, amount, currency, provider_id in rows:
                prices.append({
                    "plan": code,
                    "name": name,
                    "description": desc,
                    "recommended": bool(recommended),
                    "billing_cycle": cycle,
                    "amount": amount,
                    "currency": currency,
                    "amount_provider": provider_id,
                })
    except Exception as e:  # noqa: BLE001
        logger.debug("plan prices lookup failed: %s", e)

    if not prices:
        # Fallback to the legacy PLANS dict.
        from api.subscriptions import PLANS
        for code, p in PLANS.items():
            prices.append({
                "plan": code,
                "name": p["label"],
                "description": "",
                "recommended": code == "pro",
                "billing_cycle": "MONTHLY",
                "amount": int(p["price"] * 100),
                "currency": "INR",
                "amount_provider": None,
            })
    return prices


def get_plan_features(plan: str) -> list[str]:
    """Return the human-readable feature list for a plan."""
    from api.subscriptions import PLANS
    plan = _normalize_plan(plan)
    return PLANS.get(plan, PLANS["free"]).get("features", [])


def has_feature(user_id: str, feature_key: str, plan: Optional[str] = None) -> bool:
    """Check a single feature flag for a user."""
    ent = get_entitlements(user_id, plan)
    return bool(ent.get(feature_key, False))