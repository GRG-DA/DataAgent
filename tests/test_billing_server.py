"""Billing server tests — signature fail-closed, idempotent minting, refunds.

The billing_server package imports keygen_app (cryptography + httpx) and
FastAPI. Runs fully offline: Razorpay HTTP calls are monkeypatched.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from keygen_app.keygen import generate_keypair  # noqa: E402  (generate_keypair re-exported for tests)


# billing_env fixture lives in tests/conftest.py (shared with the sync tests).


@pytest.fixture()
def client(billing_env, monkeypatch):
    import billing_server.main as main_mod

    # Offline Razorpay: stub create_order.
    def fake_create_order(amount, currency, receipt, notes):
        return {"id": f"order_{receipt[:12]}", "amount": amount,
                "currency": currency, "status": "created", "notes": notes}

    monkeypatch.setattr(main_mod.razorpay_client, "create_order", fake_create_order)
    return TestClient(main_mod.app, raise_server_exceptions=False)


def _whsig(body: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payment_event(order_id: str, payment_id: str, email: str = "buyer@example.com",
                   plan: str = "pro", amount: int = 49900) -> bytes:
    return json.dumps({
        "event": "payment_link.paid",
        "payload": {
            "order": {"entity": {"id": order_id, "amount": amount,
                                 "receipt": "r1", "notes": {"plan": plan, "email": email}}},
            "payment": {"entity": {"id": payment_id, "order_id": order_id,
                                   "amount": amount, "status": "captured", "email": email}},
        },
    }).encode()


# ── webhook signature: fail-closed ────────────────────────────────────


def test_healthz_ok_without_a_license_key_in_keyless_mode(client, billing_env, tmp_path, monkeypatch):
    cfg = billing_env["cfg"]
    monkeypatch.setattr(cfg, "PAYMENTS_PROVIDER", "razorpay")
    monkeypatch.setattr(cfg, "KEYLESS", True, raising=False)
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY", "")

    r = client.get("/healthz")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["components"]["license_key"] is False
    assert body["components"]["keyless"] is True


def test_cors_allows_the_storefront_origin(client):
    r = client.options(
        "/create-order",
        headers={
            "Origin": "https://data-agent.me",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://data-agent.me"
    assert r.headers["access-control-allow-credentials"] == "true"


def test_cors_origins_never_allow_a_wildcard_or_plain_http():
    from billing_server.config import _parse_cors_origins

    assert _parse_cors_origins("*") == []
    assert _parse_cors_origins("http://evil.example,https://ok.example") == [
        "https://ok.example"
    ]
    assert _parse_cors_origins("http://localhost:3000") == ["http://localhost:3000"]
    assert _parse_cors_origins("http://localhost.evil.example") == []
    assert _parse_cors_origins("http://127.0.0.1.evil.example") == []
    assert _parse_cors_origins("https://data-agent.me/path") == []


def test_cors_rejects_an_unknown_origin(client):
    r = client.options(
        "/create-order",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in r.headers


def test_webhook_rejects_bad_signature(client):
    body = _payment_event("order_abc123", "pay_abc123")
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": "deadbeef" * 8})
    assert r.status_code == 400


def test_webhook_rejects_missing_signature(client):
    r = client.post("/razorpay/webhook", content=b"{}", headers={})
    assert r.status_code == 400


# ── payment events: mark paid once (idempotent, keyless) ─────────────


def test_webhook_marks_paid_once(client, billing_env):
    body = _payment_event("order_ok1", "pay_ok1", email="a@example.com")
    sig = _whsig(body)
    r1 = client.post("/razorpay/webhook", content=body, headers={"X-Razorpay-Signature": sig})
    assert r1.status_code == 200
    from billing_server import store
    o1 = store.get_order("order_ok1")
    assert o1["status"] == "paid"
    assert not o1["license_key"]  # keyless: entitlement follows the buyer email

    # Razorpay retry (same event) must not reprocess.
    r2 = client.post("/razorpay/webhook", content=body, headers={"X-Razorpay-Signature": sig})
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True


def test_webhook_grants_entitlement_by_buyer_email(client):
    """The paid order grants the plan to the purchaser's email (no key)."""
    body = _payment_event("order_bind1", "pay_bind1", email="Bound@Example.com")
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    r = client.get("/v1/entitlements", params=_sync_params("bound@example.com"))
    assert r.status_code == 200
    assert r.json()["plan"] == "pro"
    assert r.json()["status"] == "active"


def test_webhook_unmatched_plink_records_from_notes(client):
    """Payment Link flow: no local order row — email+plan come from webhook notes."""
    body = _payment_event("order_plink1", "pay_pl1", email="plink@example.com",
                          plan="lifetime", amount=1000000)
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    from billing_server import store
    o = store.get_order("order_plink1")
    assert o and o["email"] == "plink@example.com" and o["plan"] == "lifetime"
    assert not o["license_key"]  # keyless


def test_webhook_underpaid_plink_mints_nothing(client):
    """A payment below the plan price must not mint a key."""
    body = _payment_event("order_under1", "pay_under1", email="cheap@example.com",
                          plan="lifetime", amount=100)
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200
    assert r.json().get("ignored") == "underpaid"
    from billing_server import store
    assert store.get_order("order_under1") is None


# ── refund revokes ────────────────────────────────────────────────────


def test_refund_marks_order_refunded(client):
    body = _payment_event("order_ref1", "pay_ref1")
    client.post("/razorpay/webhook", content=body,
                headers={"X-Razorpay-Signature": _whsig(body)})
    refund = json.dumps({
        "event": "refund.processed",
        "payload": {
            "payment": {"entity": {"id": "pay_ref1"}},
            "refund": {"entity": {"amount": 49900}},
        },
    }).encode()
    r = client.post("/razorpay/webhook", content=refund,
                    headers={"X-Razorpay-Signature": _whsig(refund)})
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    from billing_server import store
    assert store.get_order("order_ref1")["status"] == "refunded"


def test_partial_refund_keeps_plan(client):
    body = _payment_event("order_pr1", "pay_pr1")
    client.post("/razorpay/webhook", content=body,
                headers={"X-Razorpay-Signature": _whsig(body)})
    refund = json.dumps({
        "event": "refund.processed",
        "payload": {
            "payment": {"entity": {"id": "pay_pr1"}},
            "refund": {"entity": {"amount": 100}},
        },
    }).encode()
    r = client.post("/razorpay/webhook", content=refund,
                    headers={"X-Razorpay-Signature": _whsig(refund)})
    assert r.json()["revoked"] is False


# ── entitlements read API ─────────────────────────────────────────────


def _sync_params(email: str, secret: str = "sync_secret", exp: int | None = None):
    exp = exp or int(time.time())
    sig = hmac.new(secret.encode(), f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()
    return {"email": email, "exp": str(exp), "sig": sig}


def test_entitlements_free_when_no_purchase(client):
    r = client.get("/v1/entitlements", params=_sync_params("nobody@example.com"))
    assert r.status_code == 200
    assert r.json()["plan"] == "free"


def test_entitlements_pro_after_purchase(client):
    body = _payment_event("order_ent1", "pay_ent1", email="pro@example.com")
    client.post("/razorpay/webhook", content=body,
                headers={"X-Razorpay-Signature": _whsig(body)})
    r = client.get("/v1/entitlements", params=_sync_params("pro@example.com"))
    assert r.status_code == 200
    data = r.json()
    assert data["plan"] == "pro" and data["status"] == "active"


def test_entitlements_paid_response_carries_signed_token(client, billing_env):
    """P2: paid lookups include an Ed25519 token the app can verify offline."""
    body = _payment_event("order_ent_tok", "pay_ent_tok", email="tok@example.com")
    client.post("/razorpay/webhook", content=body,
                headers={"X-Razorpay-Signature": _whsig(body)})
    r = client.get("/v1/entitlements", params=_sync_params("tok@example.com"))
    assert r.status_code == 200
    token = r.json().get("token")
    assert token, "paid entitlements must carry a signed token"
    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    from billing_server.keymint import ENTITLEMENT_TOKEN_PRODUCT
    import base64
    import json as _json
    from cryptography.exceptions import InvalidSignature

    raw_b64, sig_b64 = token.rsplit(".", 1)
    raw = base64.urlsafe_b64decode(raw_b64 + "=" * ((4 - len(raw_b64) % 4) % 4))
    sig = base64.urlsafe_b64decode(sig_b64 + "=" * ((4 - len(sig_b64) % 4) % 4))
    pub.verify(sig, raw)  # raises InvalidSignature when forged
    payload = _json.loads(raw)
    assert payload["product"] == ENTITLEMENT_TOKEN_PRODUCT
    assert payload["email"] == "tok@example.com" and payload["plan"] == "pro"


def test_entitlements_free_carries_no_token(client):
    r = client.get("/v1/entitlements", params=_sync_params("nobody@example.com"))
    assert r.status_code == 200
    assert r.json()["plan"] == "free"
    assert not r.json().get("token")


def test_entitlements_rejects_bad_signature(client):
    r = client.get("/v1/entitlements",
                   params={"email": "x@example.com", "exp": str(int(time.time())), "sig": "00"})
    assert r.status_code == 401


def test_entitlements_rejects_stale_request(client):
    old = int(time.time()) - 4000
    r = client.get("/v1/entitlements", params=_sync_params("x@example.com", exp=old))
    assert r.status_code == 401


# ── checkout verify endpoint ──────────────────────────────────────────


def test_verify_rejects_tampered_signature(client):
    r = client.post("/create-order", json={"plan": "lifetime", "email": "v@example.com"})
    order = r.json()
    bad = hmac.new(b"wrong", f"{order['order_id']}|pay_x12345".encode(), hashlib.sha256).hexdigest()
    r = client.post("/verify", json={
        "order_id": order["order_id"], "payment_id": "pay_x12345", "signature": bad})
    assert r.status_code == 400


def test_verify_ok_marks_paid_keyless(client):
    r = client.post("/create-order", json={"plan": "lifetime", "email": "v2@example.com"})
    order = r.json()
    sig = hmac.new(b"test_secret", f"{order['order_id']}|pay_ok2".encode(),
                   hashlib.sha256).hexdigest()
    r = client.post("/verify", json={
        "order_id": order["order_id"], "payment_id": "pay_ok2", "signature": sig})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["plan"] == "lifetime"
    assert "license_key" not in body  # keyless — nothing secret is returned
    from billing_server import store
    assert store.get_order(order["order_id"])["status"] == "paid"
    # Success page confirms by email sign-in instead of showing a key.
    r = client.get("/success", params={"order_id": order["order_id"]})
    assert r.status_code == 200 and "sign in" in r.text.lower()


def test_success_page_pending_then_paid(client):
    r = client.post("/create-order", json={"plan": "lifetime", "email": "v3@example.com"})
    order_id = r.json()["order_id"]
    # Unpaid order → pending page (no key disclosed).
    r = client.get("/success", params={"order_id": order_id})
    assert r.status_code == 202
    assert "DATAAGENT-" not in r.text
    sig = hmac.new(b"test_secret", f"{order_id}|pay_ok3".encode(), hashlib.sha256).hexdigest()
    client.post("/verify", json={"order_id": order_id, "payment_id": "pay_ok3", "signature": sig})
    r = client.get("/success", params={"order_id": order_id})
    assert r.status_code == 200


# ── one-time redemption registry ──────────────────────────────────────


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _redeem_body(key: str, email: str, user_id: str, secret: str = "sync_secret",
                 exp: int | None = None) -> dict:
    exp = exp or int(time.time())
    kh = _hash_key(key)
    msg = f"{kh}|{email.lower()}|{user_id}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"key": key, "key_hash": kh, "email": email, "user_id": user_id,
            "exp": str(exp), "sig": sig}


def _mint_paid_key(client, order_id: str, payment_id: str, email: str) -> str:
    """Paid order + support-path key minting (legacy keys are no longer
    issued by purchase — the webhook is keyless; support mints manually)."""
    body = _payment_event(order_id, payment_id, email=email)
    r = client.post("/razorpay/webhook", content=body,
                    headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200, r.text
    from billing_server import keymint, store
    key = keymint.mint_key("pro", email=email)
    store.attach_key(order_id, key, keymint.key_hash(key))
    return key


def test_redeem_first_account_claims_key(client):
    key = _mint_paid_key(client, "order_red1", "pay_red1", "redeem@example.com")
    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "redeem@example.com", "user-1"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "redeemed"

    # Same account again → idempotent, still one owner.
    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "redeem@example.com", "user-1"))
    assert r.status_code == 200
    assert r.json()["status"] == "already_owner"


def test_redeem_second_account_rejected(client):
    key = _mint_paid_key(client, "order_red2", "pay_red2", "owner2@example.com")
    first = client.post("/v1/keys/redeem",
                        json=_redeem_body(key, "owner2@example.com", "user-a"))
    assert first.status_code == 200
    second = client.post("/v1/keys/redeem",
                         json=_redeem_body(key, "other2@example.com", "user-b"))
    assert second.status_code == 409


def test_redeem_second_user_same_email_rejected(client):
    """One-time use is per account: a second account is refused even with the
    buyer's own email (another device gets the plan via entitlement sync)."""
    key = _mint_paid_key(client, "order_red5", "pay_red5", "same@example.com")
    assert client.post("/v1/keys/redeem",
                       json=_redeem_body(key, "same@example.com", "user-1")).status_code == 200
    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "same@example.com", "user-2"))
    assert r.status_code == 409


def test_redeem_rejects_unknown_key(client, billing_env):
    key = billing_env["keymint"].mint_key("pro", email="ghost@example.com")  # never registered
    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "ghost@example.com", "user-c"))
    assert r.status_code == 404


def test_redeem_rejects_bad_signature(client):
    key = _mint_paid_key(client, "order_red3", "pay_red3", "sig@example.com")
    body = _redeem_body(key, "sig@example.com", "user-d")
    body["sig"] = "00" * 32
    r = client.post("/v1/keys/redeem", json=body)
    assert r.status_code == 401


def test_redeem_rejects_refunded_key(client):
    key = _mint_paid_key(client, "order_red4", "pay_red4", "refund2@example.com")
    refund = json.dumps({
        "event": "refund.processed",
        "payload": {
            "payment": {"entity": {"id": "pay_red4"}},
            "refund": {"entity": {"amount": 49900}},
        },
    }).encode()
    client.post("/razorpay/webhook", content=refund,
                headers={"X-Razorpay-Signature": _whsig(refund)})
    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "refund2@example.com", "user-e"))
    assert r.status_code == 409


# ── support/admin registry view ───────────────────────────────────────


def _registry(client, token: str = "admin_test", **params):
    return client.get("/admin/registry", params={"token": token, **params})


def test_admin_registry_requires_token(client):
    assert client.get("/admin/registry").status_code == 401
    assert _registry(client, token="wrong").status_code == 401
    r = _registry(client)
    assert r.status_code == 200
    assert "Key registry" in r.text


def test_admin_registry_disabled_without_token(client, billing_env, monkeypatch):
    monkeypatch.setattr(billing_env["cfg"], "ADMIN_TOKEN", "")
    assert client.get("/admin/registry", params={"token": "x"}).status_code == 503


def test_admin_registry_shows_order_owner_and_refund(client):
    key = _mint_paid_key(client, "order_reg1", "pay_reg1", "reg@example.com")
    page = _registry(client).text
    assert "order_reg1" in page and "reg@example.com" in page
    assert "unredeemed" in page.lower()

    r = client.post("/v1/keys/redeem",
                    json=_redeem_body(key, "reg@example.com", "user-reg"))
    assert r.status_code == 200
    page = _registry(client).text
    assert "user-reg" in page and "redeemed" in page.lower()

    refund = json.dumps({
        "event": "refund.processed",
        "payload": {
            "payment": {"entity": {"id": "pay_reg1"}},
            "refund": {"entity": {"amount": 49900}},
        },
    }).encode()
    client.post("/razorpay/webhook", content=refund,
                headers={"X-Razorpay-Signature": _whsig(refund)})
    page = _registry(client).text
    assert "refunded" in page.lower()


def test_admin_registry_masks_full_key(client):
    key = _mint_paid_key(client, "order_reg2", "pay_reg2", "mask@example.com")
    page = _registry(client).text
    assert _hash_key(key) in page   # hash stays visible for lookup
    assert key not in page          # full key must never be rendered
