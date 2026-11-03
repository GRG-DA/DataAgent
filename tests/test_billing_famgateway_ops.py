"""FamGateway rail — operations & recovery (ADR-016 follow-ups).

Covers the post-review hardening backlog:
 1. Missed-webhook sweep   → POST /admin/reconcile (verify_order → same grant path)
 2. Admin refunds          → POST /admin/orders/refund (fam rail has no refund webhook)
 3. /create-order rate limit (per-IP, in-process)
 4. Grant + expiry-nudge emails (Resend, best-effort)
 5. FAMGATEWAY_STRICT_VERDICT: tighten the order-id-less verdict leniency
 6. Observability: checkout_url drift log, /admin/webhook-failures, provider column

Reuses the offline fixtures from test_billing_famgateway.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from test_billing_famgateway import (  # noqa: E402,F401 fixtures + helpers
    FAM_KEY,
    _famsig,
    _famevent,
    _order_client,
    client,
    fam_env,
)

WH = "/famgateway/webhook"
ADMIN = "admin_test"


def _deliver(client, body: bytes):
    return client.post(WH, content=body, headers={"X-FamGateway-Signature": _famsig(body)})


# ── 1) missed-webhook reconciliation sweep ────────────────────────────


def test_admin_reconcile_grants_a_missed_webhook_order(client):
    """A payment whose webhook was never delivered still gets its grant."""
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("lifetime", client)
    assert store.get_order(order_id)["status"] == "created"

    r = client.post("/admin/reconcile", params={"token": ADMIN, "older_than_minutes": 0})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scanned"] == 1
    assert data["granted"] == 1
    assert data["results"][0]["state"] == "granted"
    assert store.get_order(order_id)["status"] == "paid"
    assert store.get_order(order_id)["payment_id"]  # UTR/transaction recorded

    # Re-running the sweep must not double-grant.
    r2 = client.post("/admin/reconcile", params={"token": ADMIN, "older_than_minutes": 0})
    assert r2.json()["granted"] == 0


def test_admin_reconcile_leaves_unconfirmed_verdicts_unpaid(client, monkeypatch):
    """A verdict of anything but success/paid must NOT grant."""
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("lifetime", client)
    monkeypatch.setattr(famgw, "verify_order", lambda oid: {
        "order_id": oid, "status": "pending", "utr": "",
        "transaction_id": "", "amount": 10000.0,
    })
    r = client.post("/admin/reconcile", params={"token": ADMIN, "older_than_minutes": 0})
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["state"] != "granted", r.text
    assert store.get_order(order_id)["status"] != "paid"


def test_admin_reconcile_never_trusts_a_foreign_verdict(client, monkeypatch):
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("lifetime", client)
    monkeypatch.setattr(famgw, "verify_order", lambda oid: {
        "order_id": "famo_someone_else", "status": "success", "utr": "999",
        "transaction_id": "famt_x", "amount": 10000.0,
    })
    r = client.post("/admin/reconcile", params={"token": ADMIN, "older_than_minutes": 0})
    assert r.json()["results"][0]["state"] != "granted"
    assert store.get_order(order_id)["status"] != "paid"


def test_admin_reconcile_is_token_protected(client):
    r = client.post("/admin/reconcile", params={"token": "wrong"})
    assert r.status_code == 401, r.text


def test_admin_header_token_authenticates(client):
    r = client.get("/admin/abuse", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200, r.text


def test_webhook_after_sweep_grant_is_a_plain_duplicate(client, monkeypatch):
    """The sweep writes the same idempotency markers: a webhook redelivery of
    the payment it already granted (same UTR) is a plain duplicate."""
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("pro", client)
    monkeypatch.setattr(famgw, "verify_order", lambda oid: {
        "order_id": oid, "status": "success", "utr": "700111222",
        "transaction_id": "famt_dup", "amount": 499.0,
    })
    r = client.post("/admin/reconcile", params={"token": ADMIN, "older_than_minutes": 0})
    assert r.json()["granted"] == 1, r.text
    assert store.get_order(order_id)["status"] == "paid"
    assert store.get_order(order_id)["payment_id"] == "700111222"

    body = _famevent(order_id, utr="700111222")
    r2 = _deliver(client, body)
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True, r2.text


# ── 2) admin refunds (fam rail: refunds are manual, dashboard-side) ───


def test_admin_refund_revokes_a_fam_grant(client):
    import billing_server.grants as grants_mod
    from billing_server import store

    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="777000")
    assert _deliver(client, body).status_code == 200
    grant = grants_mod.current_grant(store.paid_orders_for_email("buyer@example.com"), [])
    assert grant is not None

    r = client.post("/admin/orders/refund", params={"token": ADMIN, "order_id": order_id})
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] is True
    assert store.get_order(order_id)["status"] == "refunded"
    assert grants_mod.current_grant(store.paid_orders_for_email("buyer@example.com"), []) is None


def test_admin_refund_is_idempotent_and_validated(client):
    order_id = _order_client("lifetime", client)
    assert client.post("/admin/orders/refund",
                       params={"token": ADMIN, "order_id": order_id}).status_code == 200
    r = client.post("/admin/orders/refund", params={"token": ADMIN, "order_id": order_id})
    assert r.status_code == 200
    assert r.json().get("already_refunded") is True
    assert client.post("/admin/orders/refund",
                       params={"token": ADMIN, "order_id": "famo_nope"}).status_code == 404
    assert client.post("/admin/orders/refund",
                       params={"token": "wrong", "order_id": order_id}).status_code == 401


def test_admin_refund_fails_closed_without_admin_token(client, monkeypatch):
    import billing_server.config as cfg

    monkeypatch.setattr(cfg, "ADMIN_TOKEN", "")
    r = client.post("/admin/orders/refund",
                    params={"token": "anything", "order_id": "famo_x"})
    assert r.status_code == 503, r.text


def test_admin_registry_lists_the_provider(client):
    order_id = _order_client("pro", client)
    r = client.get("/admin/registry", params={"token": ADMIN})
    assert r.status_code == 200
    assert "famgateway" in r.text, r.text
    assert order_id in r.text


# ── 3) /create-order rate limit ───────────────────────────────────────


def test_create_order_is_rate_limited(client, monkeypatch):
    import billing_server.accounts as accounts_mod

    monkeypatch.setitem(accounts_mod._LIMITS, "create-order", (2, 60.0))
    accounts_mod.reset_rate_limits()
    for _ in range(2):
        r = client.post("/create-order", json={"plan": "lifetime",
                                              "email": "buyer@example.com"})
        assert r.status_code == 200, r.text
    r3 = client.post("/create-order", json={"plan": "lifetime",
                                            "email": "buyer@example.com"})
    assert r3.status_code == 429, r3.text
    accounts_mod.reset_rate_limits()


# ── 4) grant + expiry-nudge emails ────────────────────────────────────


def test_grant_email_sent_once_per_grant(client, monkeypatch):
    from billing_server import emailer

    captured: list = []
    monkeypatch.setattr(emailer, "send_grant_email",
                        lambda email, plan, expires_at="": captured.append(
                            {"email": email, "plan": plan, "expires_at": expires_at}) or True)

    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="810100")
    assert _deliver(client, body).status_code == 200
    assert len(captured) == 1, captured
    call = captured[0]
    assert call["plan"] == "pro"
    assert call["email"] == "buyer@example.com"
    expected = {(datetime.now(timezone.utc).date() + timedelta(days=d)).isoformat()
                for d in (30, 31, 32)}
    assert call["expires_at"] in expected, call

    # A redelivery (duplicate) must not re-email.
    assert _deliver(client, body).json().get("duplicate") is True
    assert len(captured) == 1


def test_grant_email_lifetime_has_no_expiry(client, monkeypatch):
    from billing_server import emailer

    captured: list = []
    monkeypatch.setattr(emailer, "send_grant_email",
                        lambda email, plan, expires_at="": captured.append(expires_at) or True)
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, amount_rupees=10000.0, utr="999100")
    assert _deliver(client, body).json().get("plan") == "lifetime"
    assert captured == [""], captured


def test_expiry_nudge_sends_once_per_window(client, monkeypatch):
    """A live 31-day window expiring within the window gets exactly one nudge."""
    from billing_server import emailer

    captured: list = []
    monkeypatch.setattr(emailer, "send_expiry_nudge_email",
                        lambda email, expires_on: captured.append(
                            {"email": email, "expires_on": expires_on}) or True)

    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="781000")
    assert _deliver(client, body).status_code == 200

    import billing_server.config as cfg
    past = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    con = sqlite3.connect(cfg.DB_PATH)
    with con:
        con.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?",
                    [past, order_id])
    con.close()

    r = client.post("/admin/expiry-nudge", params={"token": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["nudged"] == 1, data
    assert captured[0]["email"] == "buyer@example.com"

    r2 = client.post("/admin/expiry-nudge", params={"token": ADMIN})
    assert r2.json()["nudged"] == 0, r2.text
    assert len(captured) == 1

    # A fresh window (31 days out) must NOT be nudged.
    r3 = client.post("/admin/expiry-nudge",
                     params={"token": ADMIN, "window_days": 31})
    assert r3.json()["nudged"] == 0, r3.text


def test_expiry_nudge_without_email_provider_nudges_nothing(client, monkeypatch):
    import billing_server.config as cfg

    order_id = _order_client("pro", client)
    body = _famevent(order_id, utr="781100")
    assert _deliver(client, body).status_code == 200
    past = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    con = sqlite3.connect(cfg.DB_PATH)
    with con:
        con.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?",
                    [past, order_id])
    con.close()
    monkeypatch.setattr(cfg, "RESEND_API_KEY", "")  # billing_env already has it
    r = client.post("/admin/expiry-nudge", params={"token": ADMIN})
    assert r.json()["nudged"] == 0, r.text
    # No marker was left: the nudge retries once email is configured.
    con = sqlite3.connect(cfg.DB_PATH)
    events = con.execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'fam.expiry_nudge'").fetchone()[0]
    con.close()
    assert events == 0, "a failed send must not swallow future nudges"


# ── 5) strict verify verdicts (env-gated) ─────────────────────────────


def test_strict_verdict_flag_requires_order_id(client, monkeypatch):
    import billing_server.config as cfg
    import billing_server.famgateway_client as famgw
    from billing_server import store

    order_id = _order_client("lifetime", client)
    monkeypatch.setattr(famgw, "verify_order", lambda oid: {
        "status": "success", "utr": "41414", "amount": 10000.0})
    body = _famevent(order_id, amount_rupees=10000.0, utr="41414")

    monkeypatch.setattr(cfg, "FAMGATEWAY_STRICT_VERDICT", True)
    r = _deliver(client, body)
    assert r.json().get("ignored") == "verify missing order_id", r.text
    assert store.get_order(order_id)["status"] != "paid"

    monkeypatch.setattr(cfg, "FAMGATEWAY_STRICT_VERDICT", False)
    # Same event re-delivered (UTR matches the verdict): lenient mode grants.
    body2 = _famevent(order_id, amount_rupees=10000.0, utr="41414")
    r2 = _deliver(client, body2)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("plan") == "lifetime", r2.text
    assert store.get_order(order_id)["status"] == "paid"


# ── 6) observability ──────────────────────────────────────────────────


def test_create_order_logs_when_provider_omits_checkout_url(client, monkeypatch, caplog):
    import logging

    import billing_server.famgateway_client as famgw

    def no_url(amount_rupees, customer_email, webhook_url=None, redirect_url=None):
        return {"order_id": "famo_nourl", "qr_url": "https://fam.test/qr.png"}

    monkeypatch.setattr(famgw, "create_order", no_url)
    with caplog.at_level(logging.WARNING, logger="billing.main"):
        r = client.post("/create-order", json={"plan": "lifetime",
                                               "email": "buyer@example.com"})
    assert r.status_code == 200, r.text
    assert any("checkout_url" in rec.message for rec in caplog.records), caplog.text


def test_admin_webhook_failures_reports_ignored_events(client):
    order_id = _order_client("lifetime", client)
    body = _famevent(order_id, amount_rupees=1.0, utr="6060")  # underpaid → rollback row
    assert _deliver(client, body).status_code == 200

    r = client.get("/admin/webhook-failures", params={"token": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kinds"].get("fam.payment.success", 0) >= 1, data
    assert len(data["entries"]) >= 1
    assert any("fam.payment.success" in e["kind"] for e in data["entries"])

    assert client.get("/admin/webhook-failures",
                      params={"token": "wrong"}).status_code == 401
