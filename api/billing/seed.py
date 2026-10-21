"""
Seed data for billing tables — plans, prices, features, and limits.

Idempotent: existing rows are left untouched, missing rows are inserted.
The `premium` plan code is migrated to `pro` (rename) so legacy rows keep
their entitlements.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Dict

from api.billing.schema import get_connection, SCHEMA

logger = logging.getLogger(__name__)


def get_currency() -> str:
    """Billing display currency (prices are seeded in this unit).

    The checkout bills in INR (Razorpay), so the app shows INR too — the
    app used to show $6/mo while the checkout billed ₹499/mo, i.e. two
    price lists for one plan (C2).
    """
    import os

    return os.environ.get("BILLING_CURRENCY", "INR") or "INR"


#: Plan code -> daily AI-request cap.  Mirrors ``api/subscriptions.PLANS``.
AI_DAILY_CAPS = {
    "free": 25,
    "pro": 50,
    "lifetime": 100,
}


def get_free_ai_daily_cap() -> int:
    """Daily AI-request cap for the Free plan (finite; -1 = unlimited)."""
    return _env_int("BILLING_FREE_AI_DAILY_CAP", AI_DAILY_CAPS["free"])


def get_pro_ai_daily_cap() -> int:
    """Daily AI-request cap for the Pro plan (finite; -1 = unlimited)."""
    return _env_int("BILLING_PRO_AI_DAILY_CAP", AI_DAILY_CAPS["pro"])


def get_lifetime_ai_daily_cap() -> int:
    """Daily AI-request cap for the Lifetime plan (finite; -1 = unlimited)."""
    return _env_int("BILLING_LIFETIME_AI_DAILY_CAP", AI_DAILY_CAPS["lifetime"])


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# ── AI-request caps: why these numbers ──────────────────────────────────
#
# Two deployments, two cost models:
#
# * DESKTOP (what this release ships): the LLM runs on the user's own
#   machine — Ollama by default (``llm.provider: "ollama"``) or a provider
#   key the user configured themselves.  The platform's marginal cost per AI
#   request is $0, so a cap is a PRODUCT limit, not a cost control.
#
# * HOSTED (ADR-009): the platform pays a shared cloud model.  Worst case
#   per request: 10k input + 2k output tokens (the schema and the
#   relationships for up to 25 tables are injected into every prompt —
#   ``agent/sqlgen.py:402``) ≈ $0.04/request at Sonnet-class prices
#   ($3/M input, $15/M output).
#
# The rule applied to the caps below:
#
#     requests/day x 30 x $0.04  <=  the hosted cost the plan can carry
#
#   free      25/day    750/month   hosted worst case  $30/month
#   pro       50/day   1500/month   hosted worst case  $60/month
#   lifetime 100/day   3000/month   hosted worst case $120/month
#
# A ₹499/month seat cannot pay for hosted Sonnet-class inference at any
# useful volume ($6 of revenue against $0.04 a request), which is exactly
# why the lifetime plan (₹10,000, the headline tier) is the primary
# product and Pro is a capped secondary tier.  The HARD cost ceiling for
# the hosted path is the tenant budget (``BILLING_TENANT_AI_DAILY_CAP``,
# default 100 requests/day = $4/day = $120/month per tenant, whatever the
# account count) — see ``config/settings.yaml``.
#
# The free tier's $30/month hosted worst case is a deliberate loss leader
# on the hosted path; the tenant budget bounds it.  A hosted deployment
# that wants a self-financing free tier sets BILLING_FREE_AI_DAILY_CAP=5.

# Plan definitions: code, name, description, display order, recommended.
PLANS = [
    {
        "id": "plan_free",
        "code": "free",
        "name": "Free",
        "description": "Basic chat, 3 saved dashboards, 7-day history, 25 AI requests a day. Ads shown; AI without IP rotation.",
        "display_order": 0,
        "is_recommended": False,
    },
    {
        "id": "plan_pro",
        "code": "pro",
        "name": "Pro",
        "description": "50 AI requests a day, 50 saved dashboards, 1-year history, ad-free, AI with IP rotation.",
        "display_order": 1,
        "is_recommended": False,
    },
    {
        "id": "plan_lifetime",
        "code": "lifetime",
        "name": "Lifetime",
        "description": "Everything in Pro, 100 AI requests a day, 1000 dashboards, lifetime access, ad-free, AI with IP rotation.",
        "display_order": 2,
        "is_recommended": True,
    },
]

# Prices: (plan_code, billing_cycle, amount, amount_provider).
# amount_provider is unused (no payment gateway in-app); amounts are in the
# smallest currency unit (paise for INR).  These rows are the SAME amounts
# the external checkout charges (`billing_server/config.py` -> PLANS):
# Pro INR 499/mo (49900 paise), Lifetime INR 10,000 one-time (1000000
# paise).  There is no second, display-only price any more — a yearly Pro
# row that the checkout does not sell is exactly the kind of drift that
# made the app and the checkout disagree (C2).  Adding a yearly SKU means
# billing_server/config.py + website/js/billing.js as well.
PRICES = [
    ("free", "MONTHLY", 0, None),
    ("pro", "MONTHLY", 49900, None),
    ("lifetime", "ONETIME", 1000000, None),
]

# Features: (plan_code, feature_key, enabled).
# 17 premium features — disabled for free, enabled for pro and lifetime.
FEATURES = [
    ("free", "ads_visible", True),
    ("free", "ip_rotation", False),
    ("free", "ai_unlimited", False),
    ("free", "recreate_from_image", False),
    ("free", "dashboard_export", False),
    ("free", "alerts_thresholds", False),
    ("free", "scheduled_reports", False),
    ("free", "smart_monitoring", False),
    ("free", "live_monitor", False),
    ("free", "api_connection", False),
    ("free", "enterprise", False),
    ("free", "governance", False),
    ("free", "external_sources", False),
    ("free", "storytelling", False),
    ("free", "reports", False),
    ("free", "export_page", False),
    ("free", "automation", False),
    ("free", "connect_page", False),
    ("free", "marketplace", False),
    ("free", "organization", False),
    ("pro", "ads_visible", False),
    ("pro", "ip_rotation", True),
    ("pro", "ai_unlimited", True),
    ("pro", "recreate_from_image", True),
    ("pro", "dashboard_export", True),
    ("pro", "alerts_thresholds", True),
    ("pro", "scheduled_reports", True),
    ("pro", "smart_monitoring", True),
    ("pro", "live_monitor", True),
    ("pro", "api_connection", True),
    ("pro", "enterprise", True),
    ("pro", "governance", True),
    ("pro", "external_sources", True),
    ("pro", "storytelling", True),
    ("pro", "reports", True),
    ("pro", "export_page", True),
    ("pro", "automation", True),
    ("pro", "connect_page", True),
    ("pro", "marketplace", True),
    ("pro", "organization", True),
    ("lifetime", "ads_visible", False),
    ("lifetime", "ip_rotation", True),
    ("lifetime", "ai_unlimited", True),
    ("lifetime", "recreate_from_image", True),
    ("lifetime", "dashboard_export", True),
    ("lifetime", "alerts_thresholds", True),
    ("lifetime", "scheduled_reports", True),
    ("lifetime", "smart_monitoring", True),
    ("lifetime", "live_monitor", True),
    ("lifetime", "api_connection", True),
    ("lifetime", "enterprise", True),
    ("lifetime", "governance", True),
    ("lifetime", "external_sources", True),
    ("lifetime", "storytelling", True),
    ("lifetime", "reports", True),
    ("lifetime", "export_page", True),
    ("lifetime", "automation", True),
    ("lifetime", "connect_page", True),
    ("lifetime", "marketplace", True),
    ("lifetime", "organization", True),
]

# Limits: (plan_code, metric, limit_value). -1 = unlimited.
# Every plan has a FINITE `ai_requests` cap: an unlimited AI allowance on a
# paid plan is a guaranteed negative margin (the arithmetic is documented
# above).  The free tier keeps a usable allowance (25/day).
LIMITS = [
    ("free", "ai_requests", 25),
    ("free", "dashboards", 3),
    ("free", "chat_history_days", 7),
    ("pro", "ai_requests", 50),
    ("pro", "dashboards", 50),
    ("pro", "chat_history_days", 365),
    ("lifetime", "ai_requests", 100),
    ("lifetime", "dashboards", 1000),
    ("lifetime", "chat_history_days", 3650),
]


def _row_exists(con, table: str, where: str, params: list) -> bool:
    row = con.execute(
        f"SELECT 1 FROM {SCHEMA}.{table} WHERE {where} LIMIT 1", params
    ).fetchone()
    return row is not None


def seed_billing_data() -> None:
    """Insert seed plans/prices/features/limits if missing. Idempotent."""
    try:
        with get_connection() as con:
            # Migrate legacy plan code premium -> pro (only if the legacy
            # table exists — it's created by api/subscriptions.py).
            legacy_exists = con.execute(
                f"SELECT count(*) FROM information_schema.tables WHERE table_schema = '{SCHEMA}' AND table_name = 'user_subscriptions'"
            ).fetchone()[0]
            if legacy_exists:
                con.execute(
                    f"UPDATE {SCHEMA}.user_subscriptions SET plan = 'pro' WHERE plan = 'premium'"
                )

            for p in PLANS:
                if not _row_exists(con, "plans", "code = ?", [p["code"]]):
                    con.execute(
                        f"""INSERT INTO {SCHEMA}.plans
                            (id, code, name, description, is_active, is_visible,
                             is_recommended, display_order)
                            VALUES (?, ?, ?, ?, TRUE, TRUE, ?, ?)""",
                        [p["id"], p["code"], p["name"], p["description"],
                         p["is_recommended"], p["display_order"]],
                    )
                else:
                    # Keep the plan row in sync: the headline tier, the
                    # description and the order change when the price list
                    # changes, and an existing install must not keep
                    # recommending the old tier forever.
                    con.execute(
                        f"""UPDATE {SCHEMA}.plans
                            SET name = ?, description = ?, is_recommended = ?,
                                display_order = ?
                            WHERE code = ?""",
                        [p["name"], p["description"], p["is_recommended"],
                         p["display_order"], p["code"]],
                    )

            for code, cycle, amount, provider_id in PRICES:
                plan_id = f"plan_{code}"
                if not _row_exists(
                    con, "plan_prices", "plan_id = ? AND billing_cycle = ?",
                    [plan_id, cycle],
                ):
                    con.execute(
                        f"""INSERT INTO {SCHEMA}.plan_prices
                            (id, plan_id, billing_cycle, amount, currency,
                             amount_provider, is_active)
                            VALUES (?, ?, ?, ?, ?, ?, TRUE)""",
                        [f"price_{code}_{cycle.lower()}", plan_id, cycle, amount,
                         get_currency(), provider_id],
                    )
                else:
                    # Keep existing price rows in sync with the configured
                    # currency and amount (e.g. after correcting prices).
                    con.execute(
                        f"""UPDATE {SCHEMA}.plan_prices
                            SET currency = ?, amount = ?, amount_provider = ?
                            WHERE plan_id = ? AND billing_cycle = ?""",
                        [get_currency(), amount, provider_id, plan_id, cycle],
                    )

            # Drop the price rows for cycles that are no longer sold.  The
            # display-only yearly Pro row is gone (one price list, C2) and an
            # existing install must not keep advertising a SKU the checkout
            # does not sell.
            sold: Dict[str, set] = {}
            for code, cycle, _amount, _provider in PRICES:
                sold.setdefault(f"plan_{code}", set()).add(cycle)
            for plan_id, cycles in sold.items():
                rows = con.execute(
                    f"SELECT id, billing_cycle FROM {SCHEMA}.plan_prices WHERE plan_id = ?",
                    [plan_id],
                ).fetchall()
                for row_id, cycle in rows:
                    if cycle not in cycles:
                        con.execute(
                            f"DELETE FROM {SCHEMA}.plan_prices WHERE id = ?", [row_id]
                        )
                        logger.info(
                            "Removed the stale price row %s/%s — it is no longer sold",
                            plan_id, cycle,
                        )

            for code, feature_key, enabled in FEATURES:
                plan_id = f"plan_{code}"
                if not _row_exists(
                    con, "plan_features", "plan_id = ? AND feature_key = ?",
                    [plan_id, feature_key],
                ):
                    con.execute(
                        f"""INSERT INTO {SCHEMA}.plan_features
                            (id, plan_id, feature_key, enabled)
                            VALUES (?, ?, ?, ?)""",
                        [f"feat_{code}_{feature_key}", plan_id, feature_key, enabled],
                    )

            # The AI caps are always re-synced, not only when the env var is
            # set: an install that was seeded before the caps existed still
            # holds `ai_requests = -1` (unlimited) rows, and this seed run
            # is the migration that replaces them (C3).  Idempotent — the
            # same value is written again on every run.
            ai_caps = {
                "free": get_free_ai_daily_cap(),
                "pro": get_pro_ai_daily_cap(),
                "lifetime": get_lifetime_ai_daily_cap(),
            }
            for code, metric, limit_value in LIMITS:
                plan_id = f"plan_{code}"
                if metric == "ai_requests":
                    cap = ai_caps.get(code, limit_value)
                    if cap != -1:
                        limit_value = cap
                if not _row_exists(
                    con, "plan_limits", "plan_id = ? AND metric = ?",
                    [plan_id, metric],
                ):
                    con.execute(
                        f"""INSERT INTO {SCHEMA}.plan_limits
                            (id, plan_id, metric, limit_value)
                            VALUES (?, ?, ?, ?)""",
                        [f"limit_{code}_{metric}", plan_id, metric, limit_value],
                    )
                elif metric == "ai_requests":
                    # Keep the AI cap in sync with the configured value.
                    con.execute(
                        f"""UPDATE {SCHEMA}.plan_limits
                            SET limit_value = ?
                            WHERE plan_id = ? AND metric = ?""",
                        [limit_value, plan_id, metric],
                    )

            logger.info("Billing seed data ensured")
    except Exception as e:  # noqa: BLE001
        logger.warning("Billing seed skipped: %s", e)


def plan_price(plan_code: str, cycle: str = "MONTHLY") -> dict | None:
    """Look up a plan price row (amount in smallest currency unit)."""
    plan_id = f"plan_{plan_code}"
    with get_connection() as con:
        row = con.execute(
            f"""SELECT id, plan_id, billing_cycle, amount, currency, amount_provider, is_active
                FROM {SCHEMA}.plan_prices
                WHERE plan_id = ? AND billing_cycle = ? AND is_active = TRUE
                LIMIT 1""",
            [plan_id, cycle],
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "plan_id": row[1],
        "billing_cycle": row[2],
        "amount": row[3],
        "currency": row[4],
        "amount_provider": row[5],
        "is_active": row[6],
    }