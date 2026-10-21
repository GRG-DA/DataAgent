"""P5 abuse detection + enforcement — sharing becomes visible and blockable.

Covers: sharing signals (key across accounts, email across IPs), enforce
creates blocks, blocked vectors get 402 on tickets + relay, unblock
restores, admin auth required.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def srv_client(billing_env, monkeypatch):
    import billing_server.main as main_mod

    monkeypatch.setattr(main_mod, "_PROXIES_CACHE",
                        {"ts": 0.0, "proxies": []})
    return TestClient(main_mod.app, raise_server_exceptions=False)


def _wh_event(order_id, payment_id, email="buyer@example.com",
              plan="pro", amount=49900):
    return json.dumps({
        "event": "payment_link.paid",
        "payload": {
            "order": {"entity": {"id": order_id, "amount": amount,
                                 "receipt": "r1", "notes": {"plan": plan, "email": email}}},
            "payment": {"entity": {"id": payment_id, "order_id": order_id,
                                   "amount": amount, "status": "captured", "email": email}},
        },
    }).encode()


def _whsig(body: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _paid_key(srv_client, order_id, payment_id, email, plan="pro"):
    """Paid order + support-path key minting (purchases are keyless now)."""
    body = _wh_event(order_id, payment_id, email=email, plan=plan)
    r = srv_client.post("/razorpay/webhook", content=body,
                        headers={"X-Razorpay-Signature": _whsig(body)})
    assert r.status_code == 200, r.text
    from billing_server import keymint, store
    key = keymint.mint_key(plan, email=email)
    store.attach_key(order_id, key, keymint.key_hash(key))
    return key


def _key_claim(key, email, user_id, secret="sync_secret", exp=None):
    from billing_server import keymint
    exp = exp or int(time.time())
    kh = keymint.key_hash(key)
    msg = f"ticket|{kh}|{email.lower()}|{user_id}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"mode": "key", "email": email, "feature": "ip_rotation",
            "key": key, "key_hash": kh, "user_id": user_id,
            "exp": str(exp), "sig": sig}


# ── signals ─────────────────────────────────────────────────────────


def test_key_sharing_signal(srv_client):
    """One key_hash driving 3 accounts surfaces a signal."""
    from billing_server import store

    for uid in ("u-a1", "u-a2", "u-a3"):
        store.log_feature_use("shared@example.com", "ip_rotation", "pro",
                              "KHASH1", uid, "9.9.9.9")
    signals = store.abuse_signals(hours=24, min_user_ids=3, min_ips=10)
    assert any(s["key_hash"] == "KHASH1" and
               any("3 accounts" in r for r in s["reasons"]) for s in signals)


def test_ip_spread_signal(srv_client):
    from billing_server import store

    for i in range(11):
        store.log_feature_use("roam@example.com", "ip_rotation", "pro",
                              "", "u-roam", f"10.0.0.{i}")
    signals = store.abuse_signals(hours=24, min_user_ids=3, min_ips=10)
    assert any(s["email"] == "roam@example.com" and
               any("source IPs" in r for r in s["reasons"]) for s in signals)


def test_no_signal_below_threshold(srv_client):
    from billing_server import store

    for uid in ("u-b1", "u-b2"):
        store.log_feature_use("ok@example.com", "ip_rotation", "pro",
                              "KHASH2", uid, "9.9.9.9")
    assert store.abuse_signals(hours=24, min_user_ids=3, min_ips=10) == []


# ── enforce + block effects ─────────────────────────────────────────


def test_enforce_blocks_and_ticket_402(srv_client):
    from billing_server import store

    for uid in ("u-c1", "u-c2", "u-c3"):
        store.log_feature_use("bad@example.com", "ip_rotation", "pro",
                              "KHASH3", uid, "9.9.9.9")
    r = srv_client.post("/admin/abuse/enforce", params={"token": "admin_test"})
    assert r.status_code == 200, r.text
    assert any(b["value"] == "bad@example.com" for b in r.json()["blocked"])
    assert store.is_blocked("email", "bad@example.com")

    # Even a freshly-minted token cannot ticket while blocked.
    from billing_server import keymint
    token = keymint.mint_entitlement_token("bad@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "bad@example.com",
                              "feature": "ip_rotation", "token": token})
    assert r.status_code == 402


def test_blocked_email_relay_402(srv_client):
    from billing_server import keymint, store

    ticket = keymint.mint_feature_ticket("blocked@example.com", "ip_rotation", "pro")
    store.block_grant("email", "blocked@example.com", "manual review")
    r = srv_client.get("/v1/proxies", headers={"X-Feature-Ticket": ticket})
    assert r.status_code == 402


def test_key_hash_block_stops_key_mode(srv_client):
    key = _paid_key(srv_client, "order_ab1", "pay_ab1", "khb@example.com")
    from billing_server import keymint, store
    store.block_grant("key_hash", keymint.key_hash(key), "leaked key")
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "khb@example.com", "u-kh"))
    assert r.status_code == 402


def test_unblock_restores_access(srv_client):
    from billing_server import keymint, store

    key = _paid_key(srv_client, "order_ab2", "pay_ab2", "ub@example.com")
    store.block_grant("email", "ub@example.com", "oops")
    assert srv_client.post(
        "/v1/feature-ticket", json=_key_claim(key, "ub@example.com", "u-ub")
    ).status_code == 402
    r = srv_client.post("/admin/abuse/unblock",
                        params={"token": "admin_test", "kind": "email",
                                "value": "ub@example.com"})
    assert r.status_code == 200 and r.json()["removed"] is True
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "ub@example.com", "u-ub"))
    assert r.status_code == 200, r.text


def test_abuse_admin_requires_token(srv_client):
    assert srv_client.get("/admin/abuse").status_code == 401
    assert srv_client.post("/admin/abuse/enforce").status_code == 401
    r = srv_client.get("/admin/abuse", params={"token": "admin_test"})
    assert r.status_code == 200
    assert set(r.json()) >= {"signals", "blocks", "thresholds"}
