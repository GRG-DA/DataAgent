"""FamGateway HTTP client — thin httpx wrapper (ADR-016).

FamGateway (famgateway.in) is a third-party UPI automation service that is
100% free for merchants: hosted checkout page, QR + UPI intent, HMAC-SHA256
webhooks keyed with the merchant API key, and a server-to-server order-verify
endpoint. One-time payments only — no recurring billing, which is why Pro is
sold as a pre-paid 31-day window in famgateway mode.

It is NOT a licensed PSP and not affiliated with FamPay/FamApp/Trio. Treat it
strictly as a switchable rail (`PAYMENTS_PROVIDER`), never as a trust root:
the webhook HMAC is the enforcement boundary, the verify endpoint is
defense-in-depth.
"""
from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Optional

import httpx

import billing_server.config as cfg

API_BASE = "https://famgateway.in"
TIMEOUT = 20.0


class FamGatewayError(RuntimeError):
    pass


def _base() -> str:
    return (cfg.FAMGATEWAY_API_BASE or API_BASE).rstrip("/")


def _api_key() -> str:
    return cfg.FAMGATEWAY_API_KEY


def create_order(amount_rupees: int, customer_email: str, *,
                 webhook_url: Optional[str] = None,
                 redirect_url: Optional[str] = None) -> dict:
    """Create a FamGateway order; returns at least order_id + checkout_url."""
    body = {
        "amount": int(amount_rupees),
        "currency": "INR",
        "customer_email": customer_email,
    }
    if webhook_url:
        body["webhook_url"] = webhook_url
    if redirect_url:
        body["redirect_url"] = redirect_url
    resp = None
    try:
        resp = httpx.post(
            f"{_base()}/api/create-order",
            headers={"X-Api-Key": _api_key(), "Content-Type": "application/json"},
            json=body,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            detail = str(resp.json().get("message", ""))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        raise FamGatewayError(
            f"Order creation failed: {exc} {detail}".strip()) from exc


def verify_order(order_id: str) -> dict:
    """Server-to-server authoritative check (UTR, transaction_id, status).

    Raises FamGatewayError on any transport/API failure — callers decide the
    fallback policy (the ADR-016 rule: a signed webhook body may be trusted
    when this endpoint is unreachable; a *mismatching* verdict never is).
    """
    resp = None
    try:
        resp = httpx.get(
            f"{_base()}/api/verify-order.php",
            headers={"X-Api-Key": _api_key()},
            params={"order_id": order_id},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise FamGatewayError(f"Order verification failed: {exc}") from exc


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256(raw_body, FAMGATEWAY_API_KEY) == X-FamGateway-Signature."""
    expected = hmac.new(cfg.FAMGATEWAY_API_KEY.encode(), raw_body, sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip())
