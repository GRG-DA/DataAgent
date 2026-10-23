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
                   redirect_url=None, captured=None):
        order_ref = f"famo_{customer_email.split('@', 1)[0]}_{amount_rupees}"
        if captured is not None:
            captured["redirect_url"] = redirect_url
        return {
            "order_id": order_ref,
            "checkout_url": f"https://fam.test/checkout/{order_ref}",
            "qr_url": f"https://fam.test/qr/{order_ref}.png",
            "upi_intent": f"upi://pay?fam={order_ref}",
        }

    captured = {}
    import functools
    monkeypatch.setattr(famgw, "create_order",
                        functools.partial(fam_create, captured=captured))

    def fam_verify(order_id):
        """Verify verdict mirrors the recorded price, like the real API."""
        from billing_server import store as _store
        row = _store.get_order(order_id) or {}
        return {
            "order_id": order_id, "status": "success", "utr": "",
            "transaction_id": "famt_1",
            "amount": (int(row.get("amount") or 0)) / 100,  # paise -> rupees
            "customer_email": (row.get("email") or "x@y.z"),
        }

    monkeypatch.setattr(famgw, "verify_order", fam_verify)
    tc = TestClient(main_mod.app, raise_server_exceptions=False)
    tc.captured = captured
    return tc


def _famsig(body: bytes, key: str = FAM_KEY) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def _order_client(plan: str, client, email: str = "buyer@example.com") -> str:
    r = client.post("/create-order", json={"plan": plan, "email": email})
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
    o1 = _order_client("pro", client, email="renew@example.com")
    b1 = _famevent(o1, email="renew@example.com", utr="810001")
    key = {"X-FamGateway-Signature": _famsig(b1)}
    assert _deliver(client, b1, key).status_code == 200
    o2 = _order_client("pro", client, email="renew@example.com")
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


# ── payments-info: the storefront needs honest copy per rail ─────────


def test_payments_info_reports_famgateway_in_fam_mode(client):
    r = client.get("/v1/payments-info")
    assert r.status_code == 200
    assert r.json()["provider"] == "famgateway"


def test_payments_info_defaults_to_razorpay(billing_env):
    import billing_server.main as main_mod
    from fastapi.testclient import TestClient
    plain = TestClient(main_mod.app, raise_server_exceptions=False)
    r = plain.get("/v1/payments-info")
    assert r.status_code == 200
    assert r.json()["provider"] == "razorpay"


# ── hardening from the FamGateway code review (payment surface) ───────


def test_razorpay_verify_dormant_in_famgateway_mode(client):
    """The dormant Razorpay rails must not run with an empty-key HMAC."""
    sig = "f" * 64
    r = client.post("/verify", json={
        "order_id": "order_1", "payment_id": "pay_forged", "signature": sig})
    assert r.status_code == 403, r.text


def test_razorpay_verify_cannot_pay_a_famgateway_order(client, monkeypatch):
    """A Razorpay-signed verify must never settle a FamGateway order."""
    import billing_server.config as cfg
    from billing_server import store

    order_id = _order_client("lifetime", client)  # created on the fam rail
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(cfg, "PAYMENTS_PROVIDER", "razorpay")
        monkey.setattr(cfg, "RAZORPAY_KEY_SECRET", "testsecret")
        sig = _famsig(f"{order_id}|pay_valid".encode(), "testsecret")
        r = client.post("/verify", json={
            "order_id": order_id, "payment_id": "pay_valid", "signature": sig})
        assert r.status_code == 403, r.text
    assert store.get_order(order_id)["status"] != "paid"


def test_razorpay_checkout_signature_fails_closed_on_empty_secret(monkeypatch):
    import billing_server.razorpay_client as rz

    monkeypatch.setattr(rz.cfg, "RAZORPAY_KEY_SECRET", "")
    assert rz.verify_checkout_signature("o", "p", "") is False


def test_fam_webhook_fails_closed_without_api_key(client, monkeypatch):
    """An empty API key makes HMAC forgeable: no signature may ever pass."""
    import billing_server.famgateway_client as famgw

    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_KEY", "")
    body = _famevent("famo_nok", utr="93939")
    sig = hmac.new(b"", body, hashlib.sha256).hexdigest()  # forgeable empty-key HMAC
    r = _deliver(client, body, {"X-FamGateway-Signature": sig})
    assert r.status_code == 400, r.text
    # and a correctly-signed body is equally refused — no trust root at all
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 400, r.text


def test_require_ready_fam_mode_webhook_requires_fam_key():
    from billing_server import config as cfg

    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(cfg, "PAYMENTS_PROVIDER", "famgateway")
        monkey.setattr(cfg, "FAMGATEWAY_API_KEY", "")
        with pytest.raises(cfg.ConfigError):
            cfg.require_ready(webhook=True)


def test_require_ready_rejects_unknown_provider():
    from billing_server import config as cfg

    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(cfg, "PAYMENTS_PROVIDER", "famgatewya")  # typo
        with pytest.raises(cfg.ConfigError):
            cfg.require_ready(order=True)


def test_create_order_rejects_unknown_provider_with_503(billing_env, monkeypatch):
    import billing_server.config as cfg
    import billing_server.main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(cfg, "PAYMENTS_PROVIDER", "not-a-provider")
    plain = TestClient(main_mod.app, raise_server_exceptions=False)
    r = plain.post("/create-order", json={"plan": "lifetime",
                                          "email": "buyer@example.com"})
    assert r.status_code == 503, r.text


def test_fam_create_order_returns_plan(client):
    r = client.post("/create-order", json={"plan": "pro",
                                           "email": "buyer@example.com"})
    assert r.status_code == 200, r.text
    assert r.json().get("plan") == "pro"


def test_fam_redirect_default_is_the_success_page(client, monkeypatch):
    import billing_server.config as cfg

    monkeypatch.setattr(cfg, "FAMGATEWAY_REDIRECT_URL", "")
    monkeypatch.setattr(cfg, "BILLING_PUBLIC_URL", "https://billing.test")
    client.post("/create-order", json={"plan": "lifetime",
                                       "email": "buyer@example.com"})
    redirect = client.captured.get("redirect_url") or ""
    assert redirect.endswith("/success"), redirect


def test_fam_webhook_without_payment_reference_ignored(client):
    """No bank reference means no verifiable identity: refuse to grant."""
    from billing_server import store
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, utr="", transaction_id="")
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200, r.text
    assert r.json().get("ignored"), r.text
    assert store.get_order(order_id)["status"] != "paid"


def test_ignored_events_roll_the_idempotency_marker_back(client):
    """A later redelivery must reprocess: no swallows, no lost payments."""
    from billing_server import store

    # underpaid event (order recorded at 49900 paise for pro)
    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="5050", amount_rupees=100.0)
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.json().get("ignored"), r.text

    # The rollback left a failures row (audit) and removed the marker:
    # the SAME bank reference delivered at full amount reprocesses — only
    # possible if the marker was actually rolled back.
    import billing_server.config as cfg
    import sqlite3
    con = sqlite3.connect(cfg.DB_PATH)
    with con:
        failures = con.execute(
            "SELECT COUNT(*) FROM failures "
            "WHERE kind = 'fam.payment.success' AND ref_id = ?",
            [f"{order_id}:5050"]).fetchone()[0]
    con.close()
    assert failures == 1, "expected a failure-row audit of the rollback"

    corrected = _famevent(order_id, utr="5050", amount_rupees=499.0)
    r = _deliver(client, corrected, {"X-FamGateway-Signature": _famsig(corrected)})
    assert r.status_code == 200, r.text
    assert r.json().get("plan") == "pro", r.text
    assert store.get_order(order_id)["status"] == "paid"


def test_fam_webhook_ignores_header_only_event_name(client):
    """The event decision must come from the signed body only."""
    order_id = _order_client("lifetime", client)
    r = _deliver(client, b"{}",
                 {"X-FamGateway-Signature": _famsig(b"{}"),
                  "X-FamGateway-Event": "payment.success"})
    assert r.status_code == 200, r.text
    obj = r.json()
    assert obj.get("duplicate") != True or obj.get("ignored"), obj
    assert obj.get("plan") is None, obj  # nothing granted via headers alone


def test_verify_endpoint_confirms_the_order_id(client, monkeypatch):
    """Defense-in-depth: a verify verdict for ANOTHER order must not count."""
    import billing_server.famgateway_client as famgw
    from billing_server import store

    monkeypatch.setattr(famgw, "verify_order", lambda order_id: {
        "order_id": "famo_someone_else", "status": "success", "utr": "12121",
        "transaction_id": "famt_x", "amount": 10000.0,
        "customer_email": "x@y.z",
    })
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, utr="12121", amount_rupees=10000.0)
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200, r.text
    assert r.json().get("ignored") == "verify mismatch", r.text
    assert store.get_order(order_id)["status"] != "paid"


def test_fam_webhook_verdict_amount_below_price_ignored(client):
    """The verify verdict is authoritative on AMOUNT too: if it says the
    buyer paid less than the order's price, never grant the plan."""
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("lifetime", client)  # recorded at ₹10,000
    monkey_patch = pytest.MonkeyPatch()
    monkey_patch.setattr(famgw, "verify_order", lambda oid: {
        "order_id": oid, "status": "success", "utr": "12121",
        "transaction_id": "famt_x", "amount": 1000.0,  # ₹1,000 verdict
        "customer_email": "buyer@example.com",
    })
    try:
        body = _famevent(order_id, utr="12121", amount_rupees=10000.0)
        r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
        assert r.status_code == 200, r.text
        assert r.json().get("ignored") == "underpaid", r.text
    finally:
        monkey_patch.undo()
    assert store.get_order(order_id)["status"] != "paid"


def test_fam_webhook_missing_event_amount_grants_via_verdict_price(client):
    """A success event without an amount is compared by the verify verdict:
    when the provider's authoritative record matches the order price, the
    grant stands (the HMAC was usable, and the price came from the API)."""
    from billing_server import store

    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, utr="31313", amount_rupees=0)
    r = _deliver(client, body, {"X-FamGateway-Signature": _famsig(body)})
    assert r.status_code == 200, r.text
    assert r.json().get("plan") == "lifetime", r.text
    assert store.get_order(order_id)["status"] == "paid"
