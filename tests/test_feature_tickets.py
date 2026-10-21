"""P3 feature tickets + proxy relay — value gated at the server.

Server: ticket issuance (token/key modes, refund/unknown/forgery verdicts),
proxy relay (ticket required, upstream cached, audit-logged).
App: ticket verify/cache, access-decision matrix, endpoint enforcement.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path as _P

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


# ── server fixtures ───────────────────────────────────────────────


@pytest.fixture()
def srv_client(billing_env, monkeypatch):
    import billing_server.main as main_mod

    monkeypatch.setattr(main_mod, "_PROXIES_CACHE",
                        {"ts": 0.0, "proxies": []})

    def fake_create_order(amount, currency, receipt, notes):
        return {"id": f"order_{receipt[:12]}", "amount": amount,
                "currency": currency, "status": "created", "notes": notes}

    monkeypatch.setattr(main_mod.razorpay_client, "create_order", fake_create_order)
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


def _pub_pem(billing_env) -> str:
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(billing_env["public_pem"].encode())
    return pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _key_claim(key, email, user_id, secret="sync_secret", exp=None):
    from billing_server import keymint
    exp = exp or int(time.time())
    kh = keymint.key_hash(key)
    msg = f"ticket|{kh}|{email.lower()}|{user_id}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"mode": "key", "email": email, "feature": "ip_rotation",
            "key": key, "key_hash": kh, "user_id": user_id,
            "exp": str(exp), "sig": sig}


# ── ticket issuance ───────────────────────────────────────────────


def test_ticket_via_entitlement_token(srv_client, billing_env, monkeypatch):
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", _pub_pem(billing_env))
    _paid_key(srv_client, "order_ft1", "pay_ft1", "tok@example.com")
    from billing_server import keymint
    token = keymint.mint_entitlement_token("tok@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "tok@example.com",
                              "feature": "ip_rotation", "token": token})
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    # App-side verification accepts it.
    from api.billing.feature_ticket import verify_ticket
    payload = verify_ticket(ticket, "tok@example.com", "ip_rotation")
    assert payload["plan"] == "pro"
    # Issuance is audit-logged (P5 foundation).
    from billing_server import store
    assert store.feature_use_counts("tok@example.com", "ip_rotation")["total"] >= 1


def test_ticket_via_key_claim(srv_client, billing_env):
    key = _paid_key(srv_client, "order_ft2", "pay_ft2", "keyclaim@example.com")
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "keyclaim@example.com", "u-1"))
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "pro"


def test_ticket_unknown_feature(srv_client, billing_env):
    from billing_server import keymint
    token = keymint.mint_entitlement_token("a@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "a@example.com",
                              "feature": "teleport", "token": token})
    assert r.status_code == 400
    assert "teleport" not in r.text  # no reflected input in errors


def test_ticket_redeemed_by_other_account_rejected(srv_client):
    """One-time use extends to tickets: another account cannot mint."""
    key = _paid_key(srv_client, "order_ft5", "pay_ft5", "shared@example.com")
    from billing_server import keymint, store
    assert store.redeem_key(keymint.key_hash(key),
                            "shared@example.com", "user-owner")["status"] == "redeemed"
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "shared@example.com", "user-intruder"))
    assert r.status_code == 409


def test_ticket_stale_pro_order_denied(srv_client, billing_env):
    """A pro purchase past the 31-day grant window mints no tickets (H1)."""
    import sqlite3
    from datetime import datetime, timedelta

    key = _paid_key(srv_client, "order_ft6", "pay_ft6", "stale@example.com")
    old = (datetime.now() - timedelta(days=60)).isoformat()
    con = sqlite3.connect(str(billing_env["cfg"].DB_PATH))
    try:
        con.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?",
                    [old, "order_ft6"])
        con.commit()
    finally:
        con.close()
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "stale@example.com", "u-stale"))
    assert r.status_code == 402


def test_ticket_forged_token_rejected(srv_client):
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "a@example.com",
                              "feature": "ip_rotation", "token": "AAA.BBB"})
    assert r.status_code == 401


def test_ticket_refunded_key_denied(srv_client):
    key = _paid_key(srv_client, "order_ft3", "pay_ft3", "ref@example.com")
    refund = json.dumps({
        "event": "refund.processed",
        "payload": {"payment": {"entity": {"id": "pay_ft3"}},
                    "refund": {"entity": {"amount": 49900}}}}).encode()
    srv_client.post("/razorpay/webhook", content=refund,
                    headers={"X-Razorpay-Signature": _whsig(refund)})
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "ref@example.com", "u-9"))
    assert r.status_code == 402


def test_ticket_unknown_key_404(srv_client, billing_env):
    key = billing_env["keymint"].mint_key("pro", email="ghost2@example.com")
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "ghost2@example.com", "u-x"))
    assert r.status_code == 404


def test_ticket_wrong_account_rejected(srv_client):
    key = _paid_key(srv_client, "order_ft4", "pay_ft4", "owner@example.com")
    r = srv_client.post("/v1/feature-ticket",
                        json=_key_claim(key, "intruder@example.com", "u-z"))
    assert r.status_code in (401, 404)


# ── proxy relay ───────────────────────────────────────────────────


def _ticket_for(srv_client, billing_env, email, plan="pro"):
    from billing_server import keymint
    key = _paid_key(srv_client, f"order_r_{email[:6]}", f"pay_r_{email[:6]}",
                    email, plan=plan)
    assert key
    return keymint.mint_entitlement_token(email, plan)


def test_relay_requires_ticket(srv_client):
    assert srv_client.get("/v1/proxies", params={"ticket": "junk"}).status_code == 402
    assert srv_client.get("/v1/proxies").status_code == 402


def test_relay_serves_and_caches(srv_client, billing_env, monkeypatch):
    import billing_server.main as main_mod
    from billing_server import keymint

    _paid_key(srv_client, "order_rl1", "pay_rl1", "relay@example.com")
    token = keymint.mint_entitlement_token("relay@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "relay@example.com",
                              "feature": "ip_rotation", "token": token})
    ticket = r.json()["ticket"]
    calls = []

    async def fake_upstream(url: str) -> str:
        calls.append(url)
        return "http://1.1.1.1:8080\nhttps://2.2.2.2:443\nhttp://1.1.1.1:8080\n"

    monkeypatch.setattr(main_mod, "_fetch_upstream", fake_upstream)
    r1 = srv_client.get("/v1/proxies", params={"ticket": ticket, "limit": 10})
    assert r1.status_code == 200, r1.text
    assert r1.json()["proxies"] == ["http://1.1.1.1:8080", "https://2.2.2.2:443"]
    r2 = srv_client.get("/v1/proxies", params={"ticket": ticket})
    assert r2.status_code == 200
    assert len(calls) == 2  # one fetch per upstream URL, then cached


def test_relay_accepts_header_ticket(srv_client, billing_env, monkeypatch):
    """Tickets travel in a header, never the URL (L1)."""
    import billing_server.main as main_mod
    from billing_server import keymint

    _paid_key(srv_client, "order_rl3", "pay_rl3", "hdr@example.com")
    token = keymint.mint_entitlement_token("hdr@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "hdr@example.com",
                              "feature": "ip_rotation", "token": token})
    ticket = r.json()["ticket"]

    async def fake_upstream(url: str) -> str:
        return "http://3.3.3.3:8080\n"

    monkeypatch.setattr(main_mod, "_fetch_upstream", fake_upstream)
    r = srv_client.get("/v1/proxies", headers={"X-Feature-Ticket": ticket})
    assert r.status_code == 200, r.text
    assert r.json()["proxies"] == ["http://3.3.3.3:8080"]


def test_relay_upstream_outage_502(srv_client, billing_env, monkeypatch):
    import billing_server.main as main_mod
    from billing_server import keymint

    _paid_key(srv_client, "order_rl2", "pay_rl2", "outage@example.com")
    token = keymint.mint_entitlement_token("outage@example.com", "pro")
    r = srv_client.post("/v1/feature-ticket",
                        json={"mode": "token", "email": "outage@example.com",
                              "feature": "ip_rotation", "token": token})
    ticket = r.json()["ticket"]

    async def boom(url: str) -> str:
        raise RuntimeError("cdn down")

    monkeypatch.setattr(main_mod, "_fetch_upstream", boom)
    r = srv_client.get("/v1/proxies", params={"ticket": ticket})
    assert r.status_code == 502


# ── app: ticket verify + decision matrix ──────────────────────────


@pytest.fixture()
def app_env(billing_db, billing_env, monkeypatch):
    from config import settings
    import keygen_app.storage as kg_storage

    monkeypatch.setenv("LICENSE_PUBLIC_KEY", _pub_pem(billing_env))
    monkeypatch.setattr(
        kg_storage, "default_db", lambda: _P(settings.duckdb.database_path)
    )
    monkeypatch.delenv("BILLING_SYNC_URL", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    return billing_env


def _new_user(username, email=""):
    from api.auth import user_manager
    uid = user_manager.add_user(username, "Str0ng!Passw0rd", role="viewer", email=email)
    assert uid
    return uid


def _activate(uid, key):
    from keygen_app.config import load_public_key
    from keygen_app.keygen import bound_email, key_hash, validate_key
    from keygen_app.storage import activate
    from api.billing import redemption

    payload = validate_key(key, load_public_key())
    redemption.record_local_claim(key_hash(key), uid, bound_email(payload))
    activate(user_id=uid, plan=payload["plan"],
             license_key=key, expires_at=payload.get("expiresAt"))


def _server_on(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_URL", "https://billing.example.test")
    monkeypatch.setenv("BILLING_SYNC_SECRET", "unit_secret")


def _boom_post(*a, **k):
    raise httpx.ConnectError("offline")


def test_app_verify_ticket_roundtrip(app_env):
    from api.billing.feature_ticket import verify_ticket
    from billing_server import keymint

    t = keymint.mint_feature_ticket("app@example.com", "ip_rotation", "pro")
    assert verify_ticket(t, "app@example.com", "ip_rotation")["plan"] == "pro"
    with pytest.raises(ValueError):
        verify_ticket(t, "other@example.com", "ip_rotation")
    with pytest.raises(ValueError):
        verify_ticket(t + "x", "app@example.com", "ip_rotation")


def test_decision_free_denies(app_env, monkeypatch):
    from api.billing.feature_ticket import access_decision

    _server_on(monkeypatch)
    uid = _new_user("ft_free", email="ftfree@example.com")
    assert access_decision(uid, "ip_rotation")[0] == "deny"


def test_decision_unconfigured_direct(app_env, monkeypatch):
    from api.billing.feature_ticket import access_decision

    uid = _new_user("ft_off", email="ftoff@example.com")
    _activate(uid, app_env["keymint"].mint_key("pro", email="ftoff@example.com"))
    assert access_decision(uid, "ip_rotation") == ("direct", None)


def test_decision_cached_ticket_relay(app_env, monkeypatch):
    from api.billing.feature_ticket import access_decision, store_ticket
    from billing_server import keymint

    _server_on(monkeypatch)
    uid = _new_user("ft_cached", email="ftcached@example.com")
    _activate(uid, app_env["keymint"].mint_key("pro", email="ftcached@example.com"))
    store_ticket("ftcached@example.com", "ip_rotation",
                 keymint.mint_feature_ticket("ftcached@example.com", "ip_rotation", "pro"))

    def _must_not_call(*a, **k):
        raise AssertionError("network must not be touched with a cached ticket")

    monkeypatch.setattr(httpx, "post", _must_not_call)
    decision, ticket = access_decision(uid, "ip_rotation")
    assert decision == "relay" and ticket


def test_decision_offline_direct_with_proof(app_env, monkeypatch):
    from api.billing.feature_ticket import access_decision

    _server_on(monkeypatch)
    uid = _new_user("ft_dconn", email="ftdconn@example.com")
    _activate(uid, app_env["keymint"].mint_key("pro", email="ftdconn@example.com"))
    monkeypatch.setattr(httpx, "post", _boom_post)
    assert access_decision(uid, "ip_rotation") == ("direct", None)


def test_decision_verdict_deny(app_env, monkeypatch):
    from api.billing.feature_ticket import access_decision

    _server_on(monkeypatch)
    uid = _new_user("ft_deny", email="ftdeny@example.com")
    _activate(uid, app_env["keymint"].mint_key("lifetime", email="ftdeny@example.com"))

    class R:
        status_code = 402

        @staticmethod
        def json():
            return {"detail": "Key was refunded"}

        text = "Key was refunded"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    decision, reason = access_decision(uid, "ip_rotation")
    assert decision == "deny"


def test_decision_unknown_with_proof_direct(app_env, monkeypatch):
    """Manual key the server never registered: crypto holds → direct."""
    from api.billing.feature_ticket import access_decision

    _server_on(monkeypatch)
    uid = _new_user("ft_unk", email="ftunk@example.com")
    _activate(uid, app_env["keymint"].mint_key("pro", email="ftunk@example.com"))

    class _RedeemOK:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "status": "already_owner"}

    class _Ticket404:
        status_code = 404

        @staticmethod
        def json():
            return {"detail": "unknown"}

        text = "unknown"

    def _route(url, json=None, timeout=None):
        if "/v1/feature-ticket" in str(url):
            return _Ticket404()
        return _RedeemOK()

    monkeypatch.setattr(httpx, "post", _route)
    assert access_decision(uid, "ip_rotation") == ("direct", None)


def test_decision_unknown_without_proof_deny(app_env, monkeypatch):
    """Garbage key + unknown server state → deny (nothing to trust)."""
    from api.billing.feature_ticket import access_decision
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.subs import start_trial

    _server_on(monkeypatch)
    uid = _new_user("ft_unk2", email="ftunk2@example.com")
    start_trial(uid, "ftunk2@example.com")
    with get_connection() as con:
        con.execute(
            f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
                key VARCHAR PRIMARY KEY, value VARCHAR)"""
        )
        con.execute(
            f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
            ["license_key", "DATAAGENT-GARBAGE"],
        )
    from api.billing.cache import clear_request_cache

    clear_request_cache()

    class R:
        status_code = 404

        @staticmethod
        def json():
            return {"detail": "unknown"}

        text = "unknown"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    # Garbage key + unknown server state → deny (nothing trustworthy).
    assert access_decision(uid, "ip_rotation")[0] == "deny"


def test_decision_trial_direct_without_network(app_env, monkeypatch):
    """Trial grants keep working with no server round trip at all."""
    from api.billing.feature_ticket import access_decision
    from api.billing.subs import start_trial

    _server_on(monkeypatch)
    uid = _new_user("ft_trial", email="fttrial@example.com")
    start_trial(uid, "fttrial@example.com")

    def _must_not_call(*a, **k):
        raise AssertionError("trial path must not touch the network")

    monkeypatch.setattr(httpx, "post", _must_not_call)
    assert access_decision(uid, "ip_rotation") == ("direct", None)


# ── endpoint enforcement ──────────────────────────────────────────


def test_fetch_endpoint_relay_path(app_env, billing_db, monkeypatch):
    """Ticket decision=relay → pool applied from the relay list."""
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers
    from api.auth import user_manager

    user_manager.add_user("ft_ep1", "Str0ng!Passw0rd", role="viewer",
                          email="fepy1@example.com")
    info = user_manager.get_user_info("ft_ep1")
    _activate(info["user_id"], app_env["keymint"].mint_key("pro", email="fepy1@example.com"))

    import api.billing.feature_ticket as ft_mod
    import agent.proxifly_pool as pool_mod

    monkeypatch.setattr(ft_mod, "access_decision", lambda uid, feat: ("relay", "TICKET123"))

    seen = {}

    async def fake_relay(ticket, protocols=None, limit=300, timeout=30.0):
        seen["ticket"] = ticket
        return ["http://9.9.9.9:8080"]

    async def fake_refresh(protocols=None, limit=300, force=False, probe=True, proxies=None):
        seen["applied"] = list(proxies or [])
        return {"refreshed": True, "count": len(proxies or []),
                "proxies": list(proxies or [])}

    monkeypatch.setattr(pool_mod, "fetch_via_relay", fake_relay)
    monkeypatch.setattr(pool_mod, "refresh", fake_refresh)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = client.post("/llm/providers/opencode/proxies/fetch", json={},
                    headers=auth_headers("ft_ep1"))
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert seen["ticket"] == "TICKET123"
    assert seen["applied"] == ["http://9.9.9.9:8080"]


def test_fetch_endpoint_deny_path(app_env, billing_db, monkeypatch):
    import api.billing.feature_ticket as ft_mod
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers
    from api.auth import user_manager

    user_manager.add_user("ft_ep2", "Str0ng!Passw0rd", role="viewer",
                          email="fepy2@example.com")
    info = user_manager.get_user_info("ft_ep2")
    _activate(info["user_id"], app_env["keymint"].mint_key("pro", email="fepy2@example.com"))
    monkeypatch.setattr(ft_mod, "access_decision",
                        lambda uid, feat: ("deny", "Key was refunded"))
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = client.post("/llm/providers/opencode/proxies/fetch", json={},
                    headers=auth_headers("ft_ep2"))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False and body.get("upgrade_required") is True
