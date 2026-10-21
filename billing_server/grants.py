"""Entitlement grants — fold paid orders + subscriptions into one grant.

Pro is an auto-renewing monthly subscription (Razorpay Subscriptions):
a row grants Pro until its paid-through date (``current_end``). Lifetime is
a one-time order with no expiry. Legacy one-time pro orders keep their
original 31-day window (``grant_days``, backfilled on the orders table).

The engine is pure: rows in, one Grant out. Callers (GET /v1/entitlements,
feature ticketing) read the store themselves, so exactly one rule set
decides entitlement everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

#: Legacy one-time pro orders without a stored grant window keep the
#: original 31 days from purchase (matches the retired key lifetime).
LEGACY_PRO_GRANT_DAYS = 31

#: Subscription statuses that will attempt another charge (display only —
#: past-end rows never grant, whatever the status says).
_RENEWING_STATUSES = ("active", "authenticated", "pending")


@dataclass(frozen=True)
class Grant:
    """One current entitlement: plan + expiry + renewal display state."""

    plan: str
    expires_at: Optional[str]  # ISO UTC; None = never (lifetime)
    source: str  # "subscription" | "order"
    renews: bool = False
    cancel_at_period_end: bool = False
    subscription_id: str = ""


def _as_now(now: Optional[datetime]) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_iso(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _order_grant(order: dict, now: datetime) -> Optional[Grant]:
    if str(order.get("status", "")) != "paid":
        return None
    plan = str(order.get("plan", "")).lower()
    if plan == "lifetime":
        return Grant(plan="lifetime", expires_at=None, source="order")
    if plan != "pro":
        return None
    paid_at = _parse_iso(order.get("updated_at") or order.get("created_at"))
    if paid_at is None:
        return None
    try:
        days = int(order["grant_days"]) if order.get("grant_days") is not None \
            else LEGACY_PRO_GRANT_DAYS
    except (ValueError, TypeError):
        days = LEGACY_PRO_GRANT_DAYS
    expires = paid_at + timedelta(days=days)
    if expires <= now:
        return None
    return Grant(plan="pro", expires_at=expires.isoformat(), source="order")


def _subscription_grant(sub: dict, now: datetime) -> Optional[Grant]:
    end = _parse_iso(sub.get("current_end"))
    if end is None or end <= now:
        return None
    cancelling = bool(sub.get("cancel_at_period_end"))
    status = str(sub.get("status", "")).lower()
    return Grant(
        plan=str(sub.get("plan", "pro")).lower() or "pro",
        expires_at=end.isoformat(),
        source="subscription",
        renews=status in _RENEWING_STATUSES and not cancelling,
        cancel_at_period_end=cancelling,
        subscription_id=str(sub.get("subscription_id", "")),
    )


def order_grant(order: dict, now: Optional[datetime] = None) -> Optional[Grant]:
    """Live grant for a single order row (feature ticketing uses this)."""
    return _order_grant(order, _as_now(now))


def subscription_grant(sub: dict, now: Optional[datetime] = None) -> Optional[Grant]:
    """Live grant for a single subscription row (cancel endpoint uses this)."""
    return _subscription_grant(sub, _as_now(now))


def current_grant(orders: list[dict], subscriptions: list[dict],
                  now: Optional[datetime] = None) -> Optional[Grant]:
    """Best live grant for one email: lifetime first, else longest expiry.

    Expired and refunded rows contribute nothing; malformed rows are skipped
    rather than fatal. On a tie the renewing subscription wins.
    """
    now = _as_now(now)

    grants = [g for g in (_order_grant(o, now) for o in orders or []) if g]
    grants += [g for g in (_subscription_grant(s, now) for s in subscriptions or []) if g]
    if not grants:
        return None
    for grant in grants:
        if grant.plan == "lifetime":
            return grant
    return max(
        grants,
        key=lambda g: (_parse_iso(g.expires_at), g.source == "subscription"),
    )
