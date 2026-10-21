"""Grant folding: paid orders + subscriptions → one current entitlement.

Written before ``billing_server.grants`` existed (TDD). Pro is an
auto-renewing monthly subscription (Razorpay Subscriptions); lifetime stays
a one-time order. Legacy one-time pro orders keep their original 31-day
window. The engine is pure: rows in, one Grant out — no store access.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from billing_server.grants import current_grant

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _order(plan="pro", paid_at=None, grant_days=31, status="paid"):
    when = paid_at or (NOW - timedelta(days=1))
    return {
        "order_id": "o1", "payment_id": "pay_1", "email": "buyer@example.com",
        "plan": plan, "amount": 49900, "currency": "INR", "status": status,
        "license_key": None, "key_hash": None, "notes": None,
        "grant_days": grant_days,
        "created_at": _iso(when), "updated_at": _iso(when),
    }


def _sub(sub_id="sub_1", status="active", current_end=None, cancel=False, plan="pro"):
    return {
        "subscription_id": sub_id, "email": "buyer@example.com", "plan": plan,
        "status": status,
        "current_end": _iso(current_end) if isinstance(current_end, datetime) else current_end,
        "cancel_at_period_end": 1 if cancel else 0,
        "payment_id": "pay_1", "notes": None,
        "created_at": _iso(NOW), "updated_at": _iso(NOW),
    }


# ── empty / no-purchase ──────────────────────────────────────────────


def test_no_purchases_returns_none():
    assert current_grant([], [], now=NOW) is None


# ── lifetime orders ──────────────────────────────────────────────────


def test_lifetime_order_grants_forever():
    grant = current_grant([_order(plan="lifetime", grant_days=None)], [], now=NOW)
    assert grant.plan == "lifetime"
    assert grant.expires_at is None
    assert grant.renews is False
    assert grant.source == "order"


def test_lifetime_beats_subscription():
    orders = [_order(plan="lifetime", grant_days=None)]
    subs = [_sub(current_end=NOW + timedelta(days=20))]
    grant = current_grant(orders, subs, now=NOW)
    assert grant.plan == "lifetime" and grant.expires_at is None


# ── subscriptions ────────────────────────────────────────────────────


def test_live_subscription_grants_until_current_end():
    end = NOW + timedelta(days=10)
    grant = current_grant([], [_sub(current_end=end)], now=NOW)
    assert grant.plan == "pro"
    assert grant.expires_at == _iso(end)
    assert grant.source == "subscription"
    assert grant.renews is True
    assert grant.subscription_id == "sub_1"


def test_subscription_past_end_grants_nothing():
    assert current_grant([], [_sub(current_end=NOW - timedelta(days=1))], now=NOW) is None


def test_cancelled_subscription_grants_until_end_without_renewing():
    grant = current_grant(
        [], [_sub(status="cancelled", current_end=NOW + timedelta(days=5))], now=NOW
    )
    assert grant.renews is False
    assert grant.expires_at == _iso(NOW + timedelta(days=5))


def test_halted_subscription_grants_until_end_without_renewing():
    grant = current_grant(
        [], [_sub(status="halted", current_end=NOW + timedelta(days=2))], now=NOW
    )
    assert grant is not None and grant.renews is False


def test_pending_retry_keeps_grant_and_renews():
    grant = current_grant(
        [], [_sub(status="pending", current_end=NOW + timedelta(days=3))], now=NOW
    )
    assert grant is not None and grant.renews is True


def test_cancel_at_period_end_flag_reported():
    grant = current_grant(
        [], [_sub(status="active", current_end=NOW + timedelta(days=9), cancel=True)],
        now=NOW,
    )
    assert grant.cancel_at_period_end is True
    assert grant.renews is False


def test_duplicate_subscriptions_use_max_current_end():
    subs = [
        _sub(sub_id="sub_old", current_end=NOW + timedelta(days=3)),
        _sub(sub_id="sub_new", current_end=NOW + timedelta(days=20)),
    ]
    grant = current_grant([], subs, now=NOW)
    assert grant.subscription_id == "sub_new"
    assert grant.expires_at == _iso(NOW + timedelta(days=20))


def test_malformed_current_end_row_is_ignored():
    subs = [_sub(current_end="not-a-date")]
    orders = [_order(paid_at=NOW - timedelta(days=2), grant_days=31)]
    grant = current_grant(orders, subs, now=NOW)
    assert grant.source == "order"


# ── legacy one-time pro orders ───────────────────────────────────────


def test_legacy_pro_order_window_uses_grant_days():
    paid = NOW - timedelta(days=10)
    grant = current_grant([_order(paid_at=paid, grant_days=31)], [], now=NOW)
    assert grant.plan == "pro"
    assert grant.source == "order"
    assert grant.renews is False
    assert grant.expires_at == _iso(paid + timedelta(days=31))


def test_legacy_pro_order_without_grant_days_defaults_to_31():
    paid = NOW - timedelta(days=10)
    grant = current_grant([_order(paid_at=paid, grant_days=None)], [], now=NOW)
    assert grant is not None
    assert grant.expires_at == _iso(paid + timedelta(days=31))


def test_expired_pro_order_grants_nothing():
    paid = NOW - timedelta(days=40)
    assert current_grant([_order(paid_at=paid, grant_days=31)], [], now=NOW) is None


def test_refunded_order_grants_nothing():
    paid = NOW - timedelta(days=1)
    assert current_grant([_order(paid_at=paid, status="refunded")], [], now=NOW) is None


# ── folding ──────────────────────────────────────────────────────────


def test_fold_picks_latest_expiry_across_sources():
    orders = [_order(paid_at=NOW - timedelta(days=25), grant_days=31)]  # ends +6d
    subs = [_sub(current_end=NOW + timedelta(days=20))]
    grant = current_grant(orders, subs, now=NOW)
    assert grant.source == "subscription"
    assert grant.expires_at == _iso(NOW + timedelta(days=20))


def test_tie_prefers_subscription():
    end = NOW + timedelta(days=12)
    orders = [_order(paid_at=end - timedelta(days=31), grant_days=31)]
    grant = current_grant(orders, [_sub(current_end=end)], now=NOW)
    assert grant.source == "subscription"
    assert grant.renews is True


# ── public single-row wrappers (tickets + cancel endpoint) ──────────


def test_order_grant_wrapper_returns_live_grant():
    from billing_server.grants import order_grant

    grant = order_grant(_order(plan="pro"), now=NOW)
    assert grant is not None
    assert grant.plan == "pro"


def test_order_grant_wrapper_skips_inactive():
    from billing_server.grants import order_grant

    assert order_grant(_order(status="refunded"), now=NOW) is None
    assert order_grant(_order(paid_at=NOW - timedelta(days=40)), now=NOW) is None


def test_subscription_grant_wrapper_returns_live_grant():
    from billing_server.grants import subscription_grant

    grant = subscription_grant(_sub(current_end=NOW + timedelta(days=10)), now=NOW)
    assert grant is not None
    assert grant.subscription_id == "sub_1"


def test_subscription_grant_wrapper_skips_lapsed():
    from billing_server.grants import subscription_grant

    assert subscription_grant(_sub(current_end=NOW - timedelta(days=1)), now=NOW) is None
