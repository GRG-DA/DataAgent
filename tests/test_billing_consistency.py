"""Billing plan consistency — one price change must land everywhere.

Guards the 4 plan authorities against silent drift:
  api/subscriptions.py PLANS (legacy display + quotas)
  api/billing/seed.py PLANS/PRICES/FEATURES/LIMITS (canonical tables)
  api/billing/entitlements.py _FALLBACK (offline entitlement matrix)
  billing_server/config.py PLANS (real checkout amounts, paise)
  keygen_app/keygen.py PLANS (mintable key plans)

ONE price list (C2): Pro INR 499/mo (49900 paise), Lifetime INR 10,000
(1000000 paise) — the app and the checkout say the same thing.
"""
from __future__ import annotations


def test_plan_codes_agree():
    from api.subscriptions import PLANS as legacy
    from api.billing.seed import PLANS as seed
    from api.billing.entitlements import _FALLBACK
    import billing_server.config as cfg
    from keygen_app.keygen import PLANS as keygen_plans

    assert set(legacy) == {"free", "pro", "lifetime"}
    assert {p["code"] for p in seed} == {"free", "pro", "lifetime"}
    assert set(_FALLBACK) == {"free", "pro", "lifetime"}
    assert set(cfg.PLANS) == {"pro", "lifetime"}
    assert {"pro", "lifetime"} <= set(keygen_plans)


def test_quotas_agree():
    from api.subscriptions import PLANS as legacy
    from api.billing.seed import LIMITS

    limits = {(code, metric): value for code, metric, value in LIMITS}
    for code in ("free", "pro", "lifetime"):
        assert legacy[code]["max_dashboards"] == limits[(code, "dashboards")], code
        assert legacy[code]["max_chat_history_days"] == limits[(code, "chat_history_days")], code


def test_feature_flags_agree():
    from api.billing.seed import FEATURES
    from api.billing.entitlements import _FALLBACK

    seed_flags = {(code, key): enabled for code, key, enabled in FEATURES}
    for code, flags in _FALLBACK.items():
        for key, enabled in flags.items():
            if key in ("plan", "max_dashboards", "max_chat_history_days", "max_ai_requests"):
                continue
            assert seed_flags.get((code, key)) == bool(enabled), (code, key)


def test_checkout_amounts_match_the_documented_price_list():
    """The seed rows and the checkout carry the same amounts (one list)."""
    from api.billing.seed import PRICES
    import billing_server.config as cfg

    seed_prices = {(code, cycle): amount for code, cycle, amount, _ in PRICES}
    assert seed_prices[("pro", "MONTHLY")] == 49900  # INR 499
    assert seed_prices[("lifetime", "ONETIME")] == 1000000  # INR 10,000
    assert cfg.PLANS["pro"]["amount"] == 49900
    assert cfg.PLANS["pro"]["currency"] == "INR"
    assert cfg.PLANS["lifetime"]["amount"] == 1000000
    # Pro sells monthly, lifetime one-time — nothing else at checkout.
    assert cfg.PLANS["pro"]["cycle"] == "MONTHLY"
    assert cfg.PLANS["lifetime"]["cycle"] == "ONETIME"
