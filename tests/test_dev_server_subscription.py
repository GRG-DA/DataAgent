"""Phase 5 — dev-server mock covers the subscription checkout walkthrough.

The dev mock must exercise the REAL /create-subscription + /verify-subscription
code paths (only Razorpay's server side is faked), so the local walkthrough
covers buy monthly → verify → subscription success page, exactly like the
one-time order mock does for Lifetime.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest


def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


class TestDevServerSubscriptions:
    def test_plan_id_env_default_present(self):
        import billing_server.dev_server  # noqa: F401  (sets env defaults)
        assert os.environ.get("RAZORPAY_PLAN_ID_PRO")

    def test_fake_create_subscription(self):
        import billing_server.dev_server as dv
        sub = dv._fake_razorpay_create_subscription("plan_dev", {"email": "a@b.c"})
        assert sub["id"].startswith("sub_dev_")
        assert sub["status"] == "created"
        assert sub["plan_id"] == "plan_dev"
        assert sub["notes"] == {"email": "a@b.c"}

    def test_fake_fetch_subscription_is_active_with_period(self):
        import billing_server.dev_server as dv
        live = dv._fake_razorpay_fetch_subscription("sub_dev_abc")
        assert live["status"] == "active"
        assert int(live["current_end"]) > time.time()

    def test_dev_sub_pay_signature_verifies(self):
        import billing_server.dev_server as dv
        from billing_server import razorpay_client as rc
        out = run_async(dv.dev_sub_pay({"subscription_id": "sub_123"}))
        assert out["payment_id"].startswith("pay_dev_")
        assert rc.verify_subscription_checkout_signature(
            "sub_123", out["payment_id"], out["signature"])
        with pytest.raises(Exception):
            run_async(dv.dev_sub_pay({"subscription_id": "bad"}))

    def test_create_subscription_dev_adds_checkout_url(self, monkeypatch):
        import billing_server.dev_server as dv

        async def _fake(parsed):
            return {"subscription_id": "sub_1", "amount": 49900}

        monkeypatch.setattr(dv, "_real_create_subscription", _fake)
        out = run_async(dv._create_subscription_dev({"email": "a@b.com"}))
        assert "/dev/sub-checkout" in out["checkout_url"]
        assert "sub_1" in out["checkout_url"]

    def test_dev_sub_checkout_page(self):
        import billing_server.dev_server as dv
        html = run_async(dv.dev_sub_checkout(
            subscription_id="sub_9", amount=49900, email="a@b.c"))
        body = html.body.decode() if isinstance(html.body, (bytes, bytearray)) else html.body
        assert "DEV MOCK" in body
        assert "sub_9" in body
        assert "\u20b9499" in body
        assert "/dev/sub-pay" in body
        assert "/verify-subscription" in body
        assert "/success?subscription_id=" in body
        with pytest.raises(Exception):
            run_async(dv.dev_sub_checkout(subscription_id="bad"))
