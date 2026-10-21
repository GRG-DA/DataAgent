"""Pricing unification — ONE price list for the app and the checkout.

Source of truth: `billing_server/config.py` (what the customer is charged,
INR paise) + `api/subscriptions.py` (what the app shows) +
`api/billing/seed.py` (the seeded rows).  All three must agree.

The app used to show Pro at $6/mo while the checkout billed ₹499/mo — two
price lists for one plan (C2).  The display-only yearly Pro row is gone as
well: the checkout does not sell it, so the app must not advertise it.
"""

from __future__ import annotations


def test_seed_prices_match_checkout():
    from api.billing.seed import PRICES

    assert ("free", "MONTHLY", 0, None) in PRICES
    assert ("pro", "MONTHLY", 49900, None) in PRICES
    assert ("lifetime", "ONETIME", 1000000, None) in PRICES
    # No display-only yearly row: the checkout sells monthly Pro + one-time
    # lifetime, and nothing else.
    assert not any(cycle == "YEARLY" for _code, cycle, _a, _p in PRICES)


def test_legacy_plans_agree_with_seed():
    from api.subscriptions import PLANS

    assert PLANS["free"]["price"] == 0
    assert PLANS["pro"]["price"] == 499
    assert PLANS["lifetime"]["price"] == 10000
    assert PLANS["pro"]["currency"] == "INR"
    assert PLANS["lifetime"]["currency"] == "INR"


def test_seeded_price_rows_match_checkout(billing_db):
    from api.billing.seed import plan_price

    assert plan_price("pro", "MONTHLY")["amount"] == 49900
    assert plan_price("pro", "MONTHLY")["currency"] == "INR"
    assert plan_price("lifetime", "ONETIME")["amount"] == 1000000
    assert plan_price("lifetime", "ONETIME")["currency"] == "INR"


def test_billing_server_is_checkout_truth():
    import billing_server.config as cfg

    assert cfg.PLANS["pro"]["amount"] == 49900  # INR 499/mo
    assert cfg.PLANS["lifetime"]["amount"] == 1000000  # INR 10,000
