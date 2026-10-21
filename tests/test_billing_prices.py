"""Pricing unification — display prices must match the external checkout.

Source of truth: billing_server/config.py (INR paise) + website/js/billing.js
(USD display): Pro $6/mo (INR 499), Lifetime $120 (INR 10,000). The in-app
seed rows and legacy PLANS dict are display-only (no in-app checkout) and
must agree — users previously saw $9/$199 in-app vs $6/$120 at checkout
(see ADR 0008).
"""

from __future__ import annotations


def test_seed_prices_match_checkout():
    from api.billing.seed import PRICES

    assert ("free", "MONTHLY", 0, None) in PRICES
    assert ("pro", "MONTHLY", 600, None) in PRICES
    assert ("pro", "YEARLY", 6000, None) in PRICES
    assert ("lifetime", "ONETIME", 12000, None) in PRICES


def test_legacy_plans_agree_with_seed():
    from api.subscriptions import PLANS

    assert PLANS["free"]["price"] == 0
    assert PLANS["pro"]["price"] == 6
    assert PLANS["lifetime"]["price"] == 120


def test_seeded_price_rows_match_checkout(billing_db):
    from api.billing.seed import plan_price

    assert plan_price("pro", "MONTHLY")["amount"] == 600
    assert plan_price("pro", "YEARLY")["amount"] == 6000
    assert plan_price("lifetime", "ONETIME")["amount"] == 12000


def test_billing_server_is_checkout_truth():
    import billing_server.config as cfg

    assert cfg.PLANS["pro"]["amount"] == 49900  # INR 499/mo
    assert cfg.PLANS["lifetime"]["amount"] == 1000000  # INR 10,000
