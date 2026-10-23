"""FamGateway rail — create-order, HMAC webhooks, idempotency, UTR grants.

ADR-016: FamGateway is the live UPI rail (one-time payments only); Razorpay
stays dormant behind `PAYMENTS_PROVIDER` (default `razorpay` — the shipped
tests in test_billing_server.py still exercise that path). Fully offline:
FamGateway HTTP calls are monkeypatched, webhook signatures computed here.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


FAM_KEY = "famkey_test_0123456789"


@pytest.fixture()
def fam_env(billing_env, monkeypatch):
    """billing_env + the FamGateway provider switched on."""
    import billing_server.config as cfg

    monkeypatch.setattr(cfg, "PAYMENTS_PROVIDER", "famgateway")
    monkeypatch.setattr(cfg, "FAMGATEWAY_API_KEY", FAM_KEY)
    monkeypatch.setattr(cfg, "FAMGATEWAY_API_BASE", "https://fam.test")
    monkeypatch.setattr(cfg, "FAMGATEWAY_WEBHOOK_URL",
                        "https://billing.test/famgateway/webhook")
    return billing_env


@pytest.fixture()
def client(fam_env, monkeypatch):
    """TestClient with Ved Razorpay AND FamGateway both offline."""
    import billing_server.main as main_mod
    import billing_server.famgateway_client as famgw

    def fake_create_order(amount, currency, receipt, notes):
        return {"id": f"order_{receipt[:12]}", "amount": amount,
                "currency": currency, "status": "created"}

    monkeypatch.setattr(main_mod.razorpay_client, "create_order", fake_create_order)

    def fam_create(amount_rupees, customer_email, webhook_url=None,
                   redirect_url=None):
        order_ref = f"famo_{customer_email.split('@', 1)[0]}_{len(amount_rupees)}"
        return {
            "order_id": order_ref,
            "checkout_url": f"https://fam.test/checkout/{order_ref}",
            "qr_url": f"https://fam.test/qr/{order_ref}.png",
            "upi_intent": f"upi://pay?fam={order_ref}",
        }

    monkeypatch.setattr(famgw, "create_order", fam_create)
    monkeypatch.setattr(famgw, "verify_order", lambda order_id: {
        "order_id": order_id, "status": "success", "utr": "111122223333",
        "transaction_id": "famt_1", "amount": 499.0, "customer_email": "x@y.z",
    })
    return TestClient(main_mod.app, raise_server_exceptions=False)


def _famsig(body: bytes, key: str = FAM_KEY) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def _order_client(plan: str, client) -> str:
    r = client.post("/create-order", json={"plan": plan,
                                           "email": "buyer@example.com"})
    assert r.status_code == 200, r.text
    return r.json()["order_id"]


def _famevent(order_id: str, *, amount_rupees=499.0, email="buyer@example.com",
              status="success", utr="12121", transaction_id="famt_x",
              event="payment.success") -> bytes:
    return json.dumps({
        "event": event, "status": status,
        "order_id": order_id, "amount": amount_rupees, "currency": "INR",
        "customer_email": email, "utr": utr, "transaction_id": transaction_id,
    }).encode()


WH = "/famgateway/webhook"


def _deliver(client, body: bytes, headers: dict | None = None):
    return client.post(WH, content=body, headers=headers or {})


# ── create-order: famgateway mode ─────────────────────────────────────


def test_create_order_serves_lifetime_with_checkout_url(client):
    r = client.post("/create-order", json={"plan": "lifetime",
                                           "email": "buyer@example.com"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["order_id"].startswith("famo_")
    assert data["checkout_url"].startswith("https://fam.test/checkout/")
    assert data["amount"] == 1000000
    from billing_server import store
    order = store.get_order(data["order_id"])
    assert order["provider"] == "famgateway"


def test_create_order_serves_pro_in_famgateway_mode(client):
    """Pro is a pre-paid 31-day window via one-time FamGateway payment."""
    r = client.post("/create-order", json={"plan": "pro",
                                           "email": "buyer@example.com"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["checkout_url"]
    from billing_server import store
    order = store.get_order(data["order_id"])
    assert order["provider"] == "famgateway"
    assert order["grant_days"] == 31


def test_create_subscription_still_dormant_in_famgateway_mode(client):
    r = client.post("/create-subscription", json={"email": "buyer@example.com"})
    assert r.status_code == 503, r.text


# ── webhook: fail-closed signature ────────────────────────────────────


def test_fam_webhook_rejects_missing_signature(client):
    r = _deliver(client, b"{}")
    assert r.status_code == 400, r.text


def test_fam_webhook_rejects_bad_signature(client):
    r = _deliver(client, b"{}", {"X-FamGateway-Signature": "d" * 64})
    assert r.status_code == 400, r.text


def test_fam_webhook_rejects_foreign_key_signature(client):
    order_id = _order_client("pro", client)
    body = _famevent(order_id)
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body, "wrongkey")})
    assert r.status_code == 400, r.text


# ── webhook: marks paid once, grants by email (keyless) ───────────────


def test_fam_webhook_marks_paid_once_and_is_idempotent(client, fam_env):
    import billing_server.main as main_mod
    order_id = _order_client("pro", client)
    body = _famevent(order_id, amount_rupees=499.0, utr="700111222")
    main_mod.famgateway_client.verify_order = lambda oid: {  # confirm endpoint
        "order_id": oid, "status": "success", "utr": "700111222",
    }
    r1 = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r1.status_code == 200, r1.text
    assert r1.json().get("plan") == "pro"
    # FamGateway retry (same signed body) must not reprocess.
    r2 = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    from billing_server import store
    order = store.get_order(order_id)
    assert order["status"] == "paid"
    assert order["payment_id"] == "700111222"


def test_fam_webhook_pro_grant_stores_31_day_window(client):
    """Paid pro window = 31 days recorded on the order row (ADR-016)."""
    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="777000")
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200
    from billing_server import store
    order = store.get_order(order_id)
    assert order["status"] == "paid"
    assert order["grant_days"] == 31


def test_fam_webhook_lifetime_grants_perpetual(client, fam_env):
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, amount_rupees=10000.0,
                     utr="999000", transaction_id="famt_life")
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200
    assert r.json().get("plan") == "lifetime"
    from billing_server import store
    order = store.get_order(order_id)
    assert order["status"] == "paid"
    assert order["grant_days"] is None  # perpetual window for one-time lifetime


# ── underpaid / replay protection ─────────────────────────────────────


def test_fam_webhook_underpaid_mints_nothing(client):
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, amount_rupees=10.0, utr="555000")
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200
    assert r.json().get("ignored") == "underpaid"
    from billing_server import store
    assert store.get_order(order_id)["status"] != "paid"


def test_fam_webhook_utr_replay_across_orders_no_double_grant(client):
    """A second order claiming the SAME UTR can never double-pay."""
    from billing_server import store
    o1 = _order_client("pro", client)
    o2 = _order_client("lifetime", client)
    key = {"X-FamGateway-Signature": _famsig(b"")}
    b1 = _famevent(o1, amount_rupees=499.0, utr="444444")
    key["X-FamGateway-Signature"] = _famsig(b1)
    assert _deliver(client, b1, key).status_code == 200
    b2 = _famevent(o2, amount_rupees=10000.0, utr="444444")
    key["X-FamGateway-Signature"] = _famsig(b2)
    r2 = _deliver(client, b2, key)
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True
    orders = {o["payment_id"]: o for o in
              (store.get_order(o1), store.get_order(o2))}
    paid = [pid for pid, o in orders.items() if o["status"] == "paid"]
    assert len(paid) == 1  # one UTR → exactly one paid order


def test_fam_webhook_pro_renewal_extends_window(client):
    """Two 31-day pro windows: the later expiry wins (folded grant)."""
    o1 = _order_client("pro", client)
    b1 = _famevent(o1, email="renew@example.com", utr="810001")
    key = {"X-FamGateway-Signature": _famsig(b1)}
    assert _deliver(client, b1, key).status_code == 200
    o2 = _order_client("pro", client)
    b2 = _famevent(o2, email="renew@example.com", utr="810002")
    key["X-FamGateway-Signature"] = _famsig(b2)
    assert _deliver(client, b2, key).status_code == 200
    from billing_server import store
    import billing_server.grants as grants
    grant = grants.current_grant(store.paid_orders_for_email("renew@example.com"),
                                 [])
    assert grant is not None and grant.plan == "pro"


# ── verify endpoint trust boundary ────────────────────────────────────


def test_fam_webhook_trusts_signed_body_when_verify_endpoint_down(client):
    """Unregulated provider outage must not swallow a legitimately signed web-
    hook: fall back to the HMAC body (the signature is the enforcement layer).
    """
    import billing_server.main as main_mod
    import billing_server.famgateway_client as famgw

    def verify_boom(order_id):
        raise famgw.FamGatewayError("verify endpoint unavailable")

    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="123000")
    famgateway_client = main_mod.famgateway_client
    previous = famgateway_client.verify_order
    famgateway_client.verify_order = verify_boom
    try:
        r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    finally:
        famgateway_client.verify_order = previous
    assert r.status_code == 200
    assert r.json().get("plan") == "pro"
    from billing_server import store
    assert store.get_order(order_id)["status"] == "paid"


def test_fam_webhook_unknown_order_ignored(client):
    body = _famevent("famo_nope_does_not_exist", utr="31337")
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200
    assert r.json().get("ignored")
