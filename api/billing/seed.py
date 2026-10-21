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

from api.billing.schema import get_connection, SCHEMA

logger = logging.getLogger(__name__)


def get_currency() -> str:
    """Billing display currency (prices are seeded in this unit)."""
    import os

    return os.environ.get("BILLING_CURRENCY", "USD") or "USD"


def get_free_ai_daily_cap() -> int:
    """Daily AI-request cap for the Free plan (-1 = unlimited)."""
    return _env_int("BILLING_FREE_AI_DAILY_CAP", 25)


def get_pro_ai_daily_cap() -> int:
    """Daily AI-request cap for the Pro plan (-1 = unlimited, the default)."""
    return _env_int("BILLING_PRO_AI_DAILY_CAP", -1)


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default

# Plan definitions: code, name, description, display order, recommended.
PLANS = [
    {
        "id": "plan_free",
        "code": "free",
        "name": "Free",
        "description": "Basic chat, 3 saved dashboards, 7-day history, daily AI request cap. Ads shown; AI without IP rotation.",
        "display_order": 0,
        "is_recommended": False,
    },
    {
        "id": "plan_pro",
        "code": "pro",
        "name": "Pro",
        "description": "Unlimited chat, 50 saved dashboards, 1-year history, ad-free, AI with IP rotation.",
        "display_order": 1,
        "is_recommended": True,
    },
    {
        "id": "plan_lifetime",
        "code": "lifetime",
        "name": "Lifetime",
        "description": "Everything in Pro, 1000 dashboards, lifetime access, ad-free, AI with IP rotation.",
        "display_order": 2,
        "is_recommended": False,
    },
]

# Prices: (plan_code, billing_cycle, amount, amount_provider).
# amount_provider is unused (no payment gateway in-app); amounts are in the
# smallest currency unit (cents for USD): Pro $6/mo, Pro $60/yr, Lifetime $120.
# Source of truth is the external checkout (billing_server/config.py +
# website/js/billing.js); these display rows must match it (see ADR 0008).
# FX reference (do not "correct" these back): USD $6/mo ~= INR 499/mo
# (49900 paise), USD $120 ~= INR 10,000 (1000000 paise). NOTE: checkout sells
# only MONTHLY Pro + ONETIME Lifetime — the YEARLY row below is display-only
# (10x monthly rule); adding a yearly SKU means billing_server + website too.
PRICES = [
    ("free", "MONTHLY", 0, None),
    ("pro", "MONTHLY", 600, None),
    ("pro", "YEARLY", 6000, None),
    ("lifetime", "ONETIME", 12000, None),
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
LIMITS = [
    ("free", "ai_requests", -1),
    ("free", "dashboards", 3),
    ("free", "chat_history_days", 7),
    ("pro", "ai_requests", -1),
    ("pro", "dashboards", 50),
    ("pro", "chat_history_days", 365),
    ("lifetime", "ai_requests", -1),
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

            free_ai_cap = get_free_ai_daily_cap()
            pro_ai_cap = get_pro_ai_daily_cap()
            for code, metric, limit_value in LIMITS:
                plan_id = f"plan_{code}"
                if code == "free" and metric == "ai_requests" and free_ai_cap != -1:
                    limit_value = free_ai_cap
                if code == "pro" and metric == "ai_requests" and pro_ai_cap != -1:
                    limit_value = pro_ai_cap
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
                elif code == "free" and metric == "ai_requests" and free_ai_cap != -1:
                    # Keep the free AI cap in sync with the configured env var.
                    con.execute(
                        f"""UPDATE {SCHEMA}.plan_limits
                            SET limit_value = ?
                            WHERE plan_id = ? AND metric = ?""",
                        [free_ai_cap, plan_id, metric],
                    )
                elif code == "pro" and metric == "ai_requests" and pro_ai_cap != -1:
                    con.execute(
                        f"""UPDATE {SCHEMA}.plan_limits
                            SET limit_value = ?
                            WHERE plan_id = ? AND metric = ?""",
                        [pro_ai_cap, plan_id, metric],
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