"""Server subscription flow — checkout, webhooks, entitlements, cancel.

Written before the endpoints existed (TDD). Pro bills as an auto-renewing
monthly Razorpay subscription: no product keys are minted on any new
purchase; entitlement is decided by ``billing_server.grants`` from paid
orders + subscription rows. Razorpay HTTP is monkeypatched — fully offline.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(billing_env, monkeypatch):
    import billing_server.main as main_mod

    monkeypatch.setattr(main_mod.cfg, "RAZORPAY_PLAN_ID_PRO", "plan_test_monthly",
                        raising=False)
    return TestClient(main_mod.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from billing_server import accounts

    accounts.reset_rate_limits()
    yield


def _store():
    from billing_server import store

    return store


def _whsig(body: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _checkout_sig(first_id: str, payment_id: str, *, sub: bool = False) -> str:
    msg = f"{payment_id}|{first_id}" if sub else f"{first_id}|{payment_id}"
    return hmac.new(b"test_secret", msg.encode(), hashlib.sha256).hexdigest()


def _sync_params(email: str) -> dict:
    exp = str(int(time.time()))
    sig = hmac.new(b"sync_secret", f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()
    return {"email": email, "exp": exp, "sig": sig}


def _future_epoch(days: int = 30) -> int:
    return int(time.time()) + days * 86400


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _seed_sub(sub_id="sub_x", email="buyer@example.com", status="active",
              current_end_epoch=None, cancel=False):
    row = _store().upsert_subscription(
        sub_id, email, "pro", status=status,
        current_end=_iso(current_end_epoch) if current_end_epoch else None,
    )
    if cancel:
        row = _store().set_cancel_at_period_end(sub_id, True)
    return row


def _sub_event(kind: str, sub_id: str, *, status: str = "active",
               current_end: int = 0, email: str = "buyer@example.com",
               payment_id: str = "", cancel: bool = False,
               created_at: int = 1700000000, notes_email: bool = True) -> bytes:
    entity: dict = {"id": sub_id, "status": status,
                    "cancel_at_period_end": bool(cancel), "notes": {}}
    if current_end:
        entity["current_end"] = current_end
    if notes_email:
        entity["notes"] = {"email": email, "plan": "pro"}
    payload: dict = {"subscription": {"entity": entity}}
    if payment_id:
        payload["payment"] = {"entity": {"id": payment_id}}
    return json.dumps({"event": kind, "created_at": created_at,
                       "payload": payload}).encode()


# ── create-subscription ──────────────────────────────────────────────


def test_create_subscription_returns_checkout_params(client, monkeypatch):
    import billing_server.main as main_mod

    calls = []

    def fake_create(plan_id, notes, total_count=None):
        calls.append({"plan_id": plan_id, "notes": notes, "total_count": total_count})
        return {"id": "sub_new1", "status": "created", "notes": notes}

    monkeypatch.setattr(main_mod.razorpay_client, "create_subscription", fake_create)
    r = client.post("/create-subscription", json={"email": "Buyer@Example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["subscription_id"] == "sub_new1"
    assert body["key_id"] == "rzp_test_123"
    assert body["amount"] == 49900
    assert body["currency"] == "INR"
    assert calls[0]["plan_id"] == "plan_test_monthly"
    assert calls[0]["notes"] == {"plan": "pro", "email": "buyer@example.com"}
    row = _store().get_subscription("sub_new1")
    assert row["email"] == "buyer@example.com"
    assert row["status"] == "created"


def test_create_subscription_requires_plan_id(client, monkeypatch):
    import billing_server.main as main_mod

    monkeypatch.setattr(main_mod.cfg, "RAZORPAY_PLAN_ID_PRO", "", raising=False)
    r = client.post("/create-subscription", json={"email": "a@example.com"})
    assert r.status_code == 503


def test_create_subscription_provider_failure_is_502(client, monkeypatch):
    import billing_server.main as main_mod

    def boom(plan_id, notes, total_count=None):
        raise main_mod.razorpay_client.RazorpayError("down")

    monkeypatch.setattr(main_mod.razorpay_client, "create_subscription", boom)
    r = client.post("/create-subscription", json={"email": "a@example.com"})
    assert r.status_code == 502


def test_create_order_rejects_subscription_plan(client):
    r = client.post("/create-order", json={"plan": "pro", "email": "a@example.com"})
    assert r.status_code == 400


def test_create_order_still_works_for_lifetime(client, monkeypatch):
    import billing_server.main as main_mod

    def fake_create_order(amount, currency, receipt, notes):
        return {"id": "order_lt1", "amount": amount, "currency": currency,
                "status": "created", "notes": notes}

    monkeypatch.setattr(main_mod.razorpay_client, "create_order", fake_create_order)
    r = client.post("/create-order", json={"plan": "lifetime", "email": "a@example.com"})
    assert r.status_code == 200
    assert r.json()["order_id"] == "order_lt1"


# ── verify-subscription ──────────────────────────────────────────────


def test_verify_subscription_activates_and_stores_period(client, monkeypatch):
    import billing_server.main as main_mod

    end = _future_epoch(30)
    _seed_sub(sub_id="sub_v1", status="created")
    monkeypatch.setattr(
        main_mod.razorpay_client, "fetch_subscription",
        lambda sid: {"id": sid, "status": "active", "current_end": end,
                     "notes": {"email": "buyer@example.com"}},
    )
    sent = []
    monkeypatch.setattr(main_mod.emailer, "send_subscription_email",
                        lambda *a, **k: sent.append((a, k)) or True)

    r = client.post("/verify-subscription", json={
        "subscription_id": "sub_v1", "payment_id": "pay_v1",
        "signature": _checkout_sig("sub_v1", "pay_v1", sub=True),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "pro"
    assert body["status"] == "active"
    assert body["renews_at"] == _iso(end)
    row = _store().get_subscription("sub_v1")
    assert row["current_end"] == _iso(end)
    assert row["payment_id"] == "pay_v1"
    assert sent and sent[0][0][0] == "buyer@example.com"


def test_verify_subscription_bad_signature_400(client):
    r = client.post("/verify-subscription", json={
        "subscription_id": "sub_v9", "payment_id": "pay_v9",
        "signature": "00" * 32,
    })
    assert r.status_code == 400


def test_verify_subscription_unknown_404(client, monkeypatch):
    import billing_server.main as main_mod

    monkeypatch.setattr(main_mod.razorpay_client, "fetch_subscription",
                        lambda sid: None)
    r = client.post("/verify-subscription", json={
        "subscription_id": "sub_ghost", "payment_id": "pay_ghost",
        "signature": _checkout_sig("sub_ghost", "pay_ghost", sub=True),
    })
    assert r.status_code == 404


def test_verify_subscription_email_from_live_notes(client, monkeypatch):
    import billing_server.main as main_mod

    end = _future_epoch(30)
    monkeypatch.setattr(
        main_mod.razorpay_client, "fetch_subscription",
        lambda sid: {"id": sid, "status": "active", "current_end": end,
                     "notes": {"email": "Late@Example.com"}},
    )
    monkeypatch.setattr(main_mod.emailer, "send_subscription_email",
                        lambda *a, **k: True)
    r = client.post("/verify-subscription", json={
        "subscription_id": "sub_late", "payment_id": "pay_late",
        "signature": _checkout_sig("sub_late", "pay_late", sub=True),
    })
    assert r.status_code == 200
    assert _store().get_subscription("sub_late")["email"] == "late@example.com"


# ── subscription webhooks ────────────────────────────────────────────


def test_webhook_subscription_charged_sets_period(client):
    end = _future_epoch(30)
    body = _sub_event("subscription.charged", "sub_w1", current_end=end,
                      payment_id="pay_w1")
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    row = _store().get_subscription("sub_w1")
    assert row["status"] == "active"
    assert row["current_end"] == _iso(end)
    assert row["payment_id"] == "pay_w1"
    assert row["email"] == "buyer@example.com"

    # Same delivery resent → duplicate, state unchanged.
    r2 = client.post("/razorpay/webhook", content=body,
                     headers={"X-Razorpay-Signature": _whsig(body)})
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    assert _store().get_subscription("sub_w1")["current_end"] == _iso(end)


def test_webhook_subscription_second_cycle_advances(client):
    end1 = _future_epoch(30)
    end2 = _future_epoch(60)
    b1 = _sub_event("subscription.charged", "sub_w2", current_end=end1,
                    payment_id="pay_c1", created_at=1700000001)
    b2 = _sub_event("subscription.charged", "sub_w2", current_end=end2,
                    payment_id="pay_c2", created_at=1700000002)
    for b in (b1, b2):
        r = client.post("/razorpay/webhook", content=b,
                        headers={"X-Razorpay-Signature": _whsig(b)})
        assert r.status_code == 200
        assert r.json().get("duplicate") is not True
    assert _store().get_subscription("sub_w2")["current_end"] == _iso(end2)


def test_webhook_subscription_cancelled_sets_flag(client):
    end = _future_epoch(20)
    _seed_sub(sub_id="sub_w3", current_end_epoch=end)
    body = _sub_event("subscription.cancelled", "sub_w3", status="cancelled",
                      current_end=end, cancel=True)
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    row = _store().get_subscription("sub_w3")
    assert row["status"] == "cancelled"
    assert row["cancel_at_period_end"] == 1


def test_webhook_subscription_halted_records_status(client):
    body = _sub_event("subscription.halted", "sub_w4", status="halted",
                      current_end=_future_epoch(3))
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    assert _store().get_subscription("sub_w4")["status"] == "halted"


def test_webhook_subscription_unknown_ignored(client):
    body = _sub_event("subscription.activated", "sub_ghost2", notes_email=False)
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    assert r.json().get("ignored")
    assert _store().get_subscription("sub_ghost2") is None


def test_webhook_subscription_bad_signature_rejected(client):
    body = _sub_event("subscription.charged", "sub_w9", current_end=_future_epoch(5))
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": "deadbeef" * 8})
    assert r.status_code == 400


# ── entitlements (grant fold) ────────────────────────────────────────


def test_entitlements_active_subscription_renewal(client):
    end = _future_epoch(25)
    _seed_sub(sub_id="sub_e1", email="sub@example.com", current_end_epoch=end)
    r = client.get("/v1/entitlements", params=_sync_params("sub@example.com"))
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "pro"
    assert body["status"] == "active"
    assert body["expires_at"] == _iso(end)
    assert body["auto_renew"] is True
    assert body["cancel_at_period_end"] is False
    assert body["token"]


def test_entitlements_cancelling_subscription_keeps_access_no_renewal(client):
    end = _future_epoch(15)
    _seed_sub(sub_id="sub_e2", email="cancel@example.com",
              current_end_epoch=end, cancel=True)
    r = client.get("/v1/entitlements", params=_sync_params("cancel@example.com"))
    body = r.json()
    assert body["status"] == "active"
    assert body["auto_renew"] is False
    assert body["cancel_at_period_end"] is True


def test_entitlements_expired_subscription(client):
    _seed_sub(sub_id="sub_e3", email="gone@example.com",
              current_end_epoch=int(time.time()) - 86400)
    r = client.get("/v1/entitlements", params=_sync_params("gone@example.com"))
    body = r.json()
    assert body["plan"] == "pro"
    assert body["status"] == "expired"
    assert not body["token"]


def test_entitlements_legacy_pro_order_window(client):
    s = _store()
    s.record_order(order_id="order_leg1", email="legacy@example.com", plan="pro",
                   amount=49900, currency="INR", notes={})
    s.mark_paid("order_leg1", "pay_leg1")
    r = client.get("/v1/entitlements", params=_sync_params("legacy@example.com"))
    body = r.json()
    assert body["plan"] == "pro"
    assert body["status"] == "active"
    end = datetime.fromisoformat(body["expires_at"])
    delta = end - datetime.now(timezone.utc)
    assert timedelta(days=30, hours=23) < delta < timedelta(days=31, hours=1)


def test_entitlements_expired_legacy_pro_order(client):
    s = _store()
    s.record_order(order_id="order_old1", email="old@example.com", plan="pro",
                   amount=49900, currency="INR", notes={})
    s.mark_paid("order_old1", "pay_old1", grant_days=0)
    r = client.get("/v1/entitlements", params=_sync_params("old@example.com"))
    body = r.json()
    assert body["plan"] == "pro"
    assert body["status"] == "expired"


def test_entitlements_lifetime_order(client):
    s = _store()
    s.record_order(order_id="order_lt9", email="lt@example.com", plan="lifetime",
                   amount=1000000, currency="INR", notes={})
    s.mark_paid("order_lt9", "pay_lt9")
    r = client.get("/v1/entitlements", params=_sync_params("lt@example.com"))
    body = r.json()
    assert body["plan"] == "lifetime"
    assert body["status"] == "active"
    assert body["expires_at"] is None


def test_entitlements_no_purchase_free(client):
    r = client.get("/v1/entitlements", params=_sync_params("nobody@example.com"))
    body = r.json()
    assert body["plan"] == "free"
    assert body["status"] == "none"


# ── cancel endpoint (session-token auth) ─────────────────────────────


def _session_token(email: str, user_id: str = "u-1") -> str:
    from billing_server.accounts import mint_session_token

    return mint_session_token(email, user_id)


def test_cancel_endpoint_requires_session(client):
    r = client.post("/v1/subscriptions/cancel")
    assert r.status_code == 401


def test_cancel_endpoint_bad_token_401(client):
    r = client.post("/v1/subscriptions/cancel",
                    headers={"Authorization": "Bearer not.a-token"})
    assert r.status_code == 401


def test_cancel_endpoint_cancels_latest_active(client, monkeypatch):
    import billing_server.main as main_mod

    _seed_sub(sub_id="sub_c1", email="c@example.com",
              current_end_epoch=_future_epoch(20))
    calls = []

    def fake_cancel(sid, at_cycle_end=True):
        calls.append({"id": sid, "end": at_cycle_end})
        return {"id": sid, "status": "active"}

    monkeypatch.setattr(main_mod.razorpay_client, "cancel_subscription", fake_cancel)
    token = _session_token("c@example.com")
    r = client.post("/v1/subscriptions/cancel",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["cancel_at_period_end"] is True
    assert calls == [{"id": "sub_c1", "end": True}]
    assert _store().get_subscription("sub_c1")["cancel_at_period_end"] == 1


def test_cancel_endpoint_no_active_subscription_404(client):
    token = _session_token("nothing@example.com")
    r = client.post("/v1/subscriptions/cancel",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_cancel_endpoint_already_scheduled(client, monkeypatch):
    import billing_server.main as main_mod

    _seed_sub(sub_id="sub_c2", email="done@example.com",
              current_end_epoch=_future_epoch(10), cancel=True)
    calls = []
    monkeypatch.setattr(main_mod.razorpay_client, "cancel_subscription",
                        lambda *a, **k: calls.append(1) or {})
    token = _session_token("done@example.com")
    r = client.post("/v1/subscriptions/cancel",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["already_scheduled"] is True
    assert calls == []


def test_cancel_endpoint_provider_failure_502(client, monkeypatch):
    import billing_server.main as main_mod

    _seed_sub(sub_id="sub_c3", email="fail@example.com",
              current_end_epoch=_future_epoch(10))
    monkeypatch.setattr(main_mod.razorpay_client, "cancel_subscription",
                        lambda *a, **k: None)
    token = _session_token("fail@example.com")
    r = client.post("/v1/subscriptions/cancel",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 502


# ── one-time flow no longer mints keys ───────────────────────────────


def test_verify_lifetime_order_no_key(client):
    s = _store()
    s.record_order(order_id="order_nk1", email="lt2@example.com", plan="lifetime",
                   amount=1000000, currency="INR", notes={})
    r = client.post("/verify", json={
        "order_id": "order_nk1", "payment_id": "pay_nk1",
        "signature": _checkout_sig("order_nk1", "pay_nk1"),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["plan"] == "lifetime"
    assert "license_key" not in body
    row = s.get_order("order_nk1")
    assert row["status"] == "paid"
    assert not row["license_key"]


def test_webhook_payment_captured_no_key(client):
    s = _store()
    s.record_order(order_id="order_nk2", email="lt3@example.com", plan="lifetime",
                   amount=1000000, currency="INR", notes={})
    body = json.dumps({
        "event": "payment.captured",
        "payload": {
            "order": {"entity": {"id": "order_nk2", "amount": 1000000,
                                 "notes": {"plan": "lifetime",
                                           "email": "lt3@example.com"}}},
            "payment": {"entity": {"id": "pay_nk2", "order_id": "order_nk2",
                                   "amount": 1000000, "status": "captured",
                                   "email": "lt3@example.com"}},
        },
    }).encode()
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    row = s.get_order("order_nk2")
    assert row["status"] == "paid"
    assert not row["license_key"]


# ── success pages ────────────────────────────────────────────────────


def test_success_page_subscription_shows_renewal(client):
    end = _future_epoch(20)
    _seed_sub(sub_id="sub_s1", email="succ@example.com", current_end_epoch=end)
    r = client.get("/success", params={"subscription_id": "sub_s1"})
    assert r.status_code == 200
    text = r.text.lower()
    assert "auto-renews" in text
    assert "succ@example.com" in r.text
    assert "product key" not in text


def test_success_page_subscription_pending_202(client):
    _seed_sub(sub_id="sub_s2", email="pend@example.com", status="created")
    r = client.get("/success", params={"subscription_id": "sub_s2"})
    assert r.status_code == 202


def test_success_page_lifetime_no_key(client):
    s = _store()
    s.record_order(order_id="order_sp1", email="sp@example.com", plan="lifetime",
                   amount=1000000, currency="INR", notes={})
    s.mark_paid("order_sp1", "pay_sp1")
    r = client.get("/success", params={"order_id": "order_sp1"})
    assert r.status_code == 200
    text = r.text.lower()
    assert "sign in" in text
    assert "product key" not in text


# ── feature tickets accept the subscription grant ────────────────────


def test_feature_ticket_token_mode_accepts_subscription(client, billing_env):
    end = _future_epoch(30)
    _seed_sub(sub_id="sub_t1", email="ticket@example.com", current_end_epoch=end)
    token = billing_env["keymint"].mint_entitlement_token(
        "ticket@example.com", "pro", grant_expires_at=_iso(end))
    r = client.post("/v1/feature-ticket", json={
        "mode": "token", "email": "ticket@example.com",
        "feature": "ip_rotation", "token": token,
    })
    assert r.status_code == 200
    assert r.json()["plan"] == "pro"


def test_feature_ticket_token_mode_lapsed_subscription_402(client, billing_env):
    end = _future_epoch(30)
    _seed_sub(sub_id="sub_t2", email="reft@example.com", current_end_epoch=end)
    token = billing_env["keymint"].mint_entitlement_token(
        "reft@example.com", "pro", grant_expires_at=_iso(end))
    # The subscription lapses after the token was minted.
    _seed_sub(sub_id="sub_t2", email="reft@example.com", status="cancelled",
              current_end_epoch=int(time.time()) - 60)
    r = client.post("/v1/feature-ticket", json={
        "mode": "token", "email": "reft@example.com",
        "feature": "ip_rotation", "token": token,
    })
    assert r.status_code == 402


# ── subscription email (no key in the message) ───────────────────────


def test_send_subscription_email_posts_and_reports(billing_env, monkeypatch):
    import billing_server.emailer as emailer

    monkeypatch.setattr(emailer.cfg, "RESEND_API_KEY", "re_test_1")
    calls = []

    class _R:
        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(emailer.httpx, "post",
                        lambda url, **kw: calls.append({"url": url, **kw}) or _R())
    ok = emailer.send_subscription_email("sub@example.com", "pro",
                                         renews_at="2026-11-16T00:00:00+00:00")
    assert ok is True
    body = calls[0]["json"]
    assert body["to"] == ["sub@example.com"]
    assert "pro" in body["subject"].lower()
    assert "2026-11-16" in body["html"]
    assert "product key" not in body["html"].lower()


def test_send_subscription_email_skips_without_key(billing_env):
    import billing_server.emailer as emailer

    assert emailer.send_subscription_email("sub@example.com", "pro") is False
