"""Razorpay Subscriptions client — create/fetch/cancel + checkout signature.

Written before the subscription methods existed (TDD). Pro now bills as an
auto-renewing monthly Razorpay subscription; the client stays SDK-free
(signed Basic-auth requests over httpx, monkeypatched here — no network).
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

httpx = pytest.importorskip("httpx")


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=None)


def _capture(monkeypatch, method, resp):
    calls = []

    def fake(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(httpx, method, fake)
    return calls


# ── create ───────────────────────────────────────────────────────────


def test_create_subscription_posts_plan_and_notes(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp
    import billing_server.config as cfg

    calls = _capture(monkeypatch, "post", _Resp({"id": "sub_abc", "status": "created"}))
    sub = rzp.create_subscription(
        "plan_123", notes={"email": "buyer@example.com", "plan": "pro"}
    )
    assert sub["id"] == "sub_abc"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"].endswith("/subscriptions")
    body = call["json"]
    assert body["plan_id"] == "plan_123"
    assert body["total_count"] == cfg.SUBSCRIPTION_TOTAL_COUNT
    assert body["customer_notify"] == 1
    assert body["quantity"] == 1
    assert body["notes"]["email"] == "buyer@example.com"
    assert call["headers"]["Authorization"].startswith("Basic ")


def test_create_subscription_wraps_errors(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    _capture(monkeypatch, "post", RuntimeError("boom"))
    with pytest.raises(rzp.RazorpayError, match="Subscription creation failed"):
        rzp.create_subscription("plan_123", notes={})


# ── fetch ────────────────────────────────────────────────────────────


def test_fetch_subscription_returns_payload(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    calls = _capture(monkeypatch, "get", _Resp({"id": "sub_abc", "current_end": 1790000000}))
    sub = rzp.fetch_subscription("sub_abc")
    assert sub["current_end"] == 1790000000
    assert calls[0]["url"].endswith("/subscriptions/sub_abc")


def test_fetch_subscription_failure_returns_none(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    _capture(monkeypatch, "get", RuntimeError("down"))
    assert rzp.fetch_subscription("sub_abc") is None


# ── cancel ───────────────────────────────────────────────────────────


def test_cancel_subscription_defaults_to_cycle_end(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    calls = _capture(monkeypatch, "post", _Resp({"id": "sub_abc", "status": "active"}))
    sub = rzp.cancel_subscription("sub_abc")
    assert sub["id"] == "sub_abc"
    assert calls[0]["url"].endswith("/subscriptions/sub_abc/cancel")
    assert calls[0]["json"] == {"cancel_at_cycle_end": 1}


def test_cancel_subscription_immediate_flag(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    calls = _capture(monkeypatch, "post", _Resp({"id": "sub_abc"}))
    rzp.cancel_subscription("sub_abc", at_cycle_end=False)
    assert calls[0]["json"] == {"cancel_at_cycle_end": 0}


def test_cancel_subscription_failure_returns_none(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    _capture(monkeypatch, "post", RuntimeError("down"))
    assert rzp.cancel_subscription("sub_abc") is None


# ── plan creation (one-time setup, scripts/razorpay_create_plan.py) ─


def test_create_plan_posts_monthly_item(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    calls = _capture(monkeypatch, "post", _Resp({"id": "plan_abc"}))
    plan = rzp.create_plan("DataAgent Pro", 49900)
    assert plan["id"] == "plan_abc"
    call = calls[0]
    assert call["url"].endswith("/plans")
    body = call["json"]
    assert body["period"] == "monthly"
    assert body["interval"] == 1
    assert body["item"] == {"name": "DataAgent Pro", "amount": 49900, "currency": "INR"}


def test_create_plan_wraps_errors(billing_env, monkeypatch):
    from billing_server import razorpay_client as rzp

    _capture(monkeypatch, "post", RuntimeError("boom"))
    with pytest.raises(rzp.RazorpayError, match="Plan creation failed"):
        rzp.create_plan("DataAgent Pro", 49900)


# ── checkout signature (payment_id|subscription_id) ─────────────────


def test_verify_subscription_checkout_signature(billing_env):
    from billing_server import razorpay_client as rzp

    sig = hmac.new(b"test_secret", b"pay_1|sub_1", hashlib.sha256).hexdigest()
    assert rzp.verify_subscription_checkout_signature("sub_1", "pay_1", sig) is True
    assert rzp.verify_subscription_checkout_signature("sub_1", "pay_1", "00" * 32) is False
    assert rzp.verify_subscription_checkout_signature("sub_1", "pay_1", "") is False
