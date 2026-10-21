"""Razorpay HTTP client — thin stdlib-httpx wrapper over the REST API.

Orders cover one-time purchases (lifetime); Subscriptions cover the
auto-renewing monthly Pro plan. No Razorpay SDK — signed Basic-auth
requests to api.razorpay.com are enough.
"""
from __future__ import annotations

import base64
import hmac
from hashlib import sha256
from typing import Any, Optional

import httpx

import billing_server.config as cfg

API_BASE = "https://api.razorpay.com/v1"
TIMEOUT = 20.0


class RazorpayError(RuntimeError):
    pass


def _auth_header() -> str:
    token = base64.b64encode(
        f"{cfg.RAZORPAY_KEY_ID}:{cfg.RAZORPAY_KEY_SECRET}".encode()
    ).decode()
    return f"Basic {token}"


def create_order(amount: int, currency: str, receipt: str, notes: dict) -> dict:
    """Create a Razorpay order; returns the order dict (id, amount, ...)."""
    resp = None
    try:
        resp = httpx.post(
            f"{API_BASE}/orders",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            json={
                "amount": amount,
                "currency": currency,
                "receipt": receipt[:40],
                "notes": notes,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            detail = str(resp.json().get("error", {}).get("description", ""))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        raise RazorpayError(f"Order creation failed: {exc} {detail}".strip()) from exc


def fetch_payment(payment_id: str) -> Optional[dict]:
    """Fetch a payment (used to double-check amount/status server-side)."""
    try:
        resp = httpx.get(
            f"{API_BASE}/payments/{payment_id}",
            headers={"Authorization": _auth_header()},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256(order_id|payment_id, KEY_SECRET) == signature (constant-time)."""
    expected = hmac.new(
        cfg.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


# ── Subscriptions (auto-renewing monthly Pro) ────────────────────────


def create_subscription(plan_id: str, notes: dict,
                        total_count: Optional[int] = None) -> dict:
    """Create a Razorpay subscription; returns the subscription dict (id, …)."""
    resp = None
    try:
        resp = httpx.post(
            f"{API_BASE}/subscriptions",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            json={
                "plan_id": plan_id,
                "total_count": int(total_count or cfg.SUBSCRIPTION_TOTAL_COUNT),
                "customer_notify": 1,
                "quantity": 1,
                "notes": notes,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            detail = str(resp.json().get("error", {}).get("description", ""))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        raise RazorpayError(f"Subscription creation failed: {exc} {detail}".strip()) from exc


def create_plan(name: str, amount: int, currency: str = "INR",
                period: str = "monthly", interval: int = 1) -> dict:
    """Create a subscription plan (one-time setup — scripts/razorpay_create_plan.py)."""
    resp = None
    try:
        resp = httpx.post(
            f"{API_BASE}/plans",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            json={
                "period": period,
                "interval": interval,
                "item": {"name": name, "amount": amount, "currency": currency},
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            detail = str(resp.json().get("error", {}).get("description", ""))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        raise RazorpayError(f"Plan creation failed: {exc} {detail}".strip()) from exc


def fetch_subscription(subscription_id: str) -> Optional[dict]:
    """Fetch a subscription (status + current_end), best-effort like payments."""
    try:
        resp = httpx.get(
            f"{API_BASE}/subscriptions/{subscription_id}",
            headers={"Authorization": _auth_header()},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def cancel_subscription(subscription_id: str, at_cycle_end: bool = True) -> Optional[dict]:
    """Cancel a subscription; at_cycle_end keeps Pro until the period ends."""
    try:
        resp = httpx.post(
            f"{API_BASE}/subscriptions/{subscription_id}/cancel",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            json={"cancel_at_cycle_end": 1 if at_cycle_end else 0},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def verify_subscription_checkout_signature(subscription_id: str, payment_id: str,
                                           signature: str) -> bool:
    """HMAC-SHA256(payment_id|subscription_id, KEY_SECRET) == signature."""
    expected = hmac.new(
        cfg.RAZORPAY_KEY_SECRET.encode(),
        f"{payment_id}|{subscription_id}".encode(),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256(raw_body, WEBHOOK_SECRET) == X-Razorpay-Signature (constant-time)."""
    expected = hmac.new(cfg.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
