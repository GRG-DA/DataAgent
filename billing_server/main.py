"""DataAgent billing server — FastAPI app.

Owns the Razorpay and FamGateway keys plus the license signing key:
  POST /create-order          → FamGateway (UPI) or Razorpay order, one-time
  POST /verify                → verify Razorpay checkout HMAC (keyless)
  POST /create-subscription   → Razorpay subscription for Pro (auto-renewing)
  POST /verify-subscription   → verify subscription checkout HMAC, activate
  POST /v1/subscriptions/cancel → cancel-at-period-end for the session email
  POST /razorpay/webhook      → signature-verified Razorpay events
  POST /famgateway/webhook    → HMAC-verified FamGateway UPI payments (ADR-016)
  GET  /success               → success page (?order_id= / ?subscription_id=, keyless)
  GET  /v1/entitlements       → HMAC-signed plan lookup by email (for the app)
  POST /v1/keys/redeem        → one-time key redemption registry (legacy keys)
  GET  /admin/registry        → token-protected support view (orders, owner, refunds)
  GET  /healthz               → readiness probe

Run:  uvicorn billing_server.main:app --host 0.0.0.0 --port 8090
"""
from __future__ import annotations

import asyncio as _asyncio
import hashlib
import hmac
import html
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import billing_server.config as cfg
from billing_server import emailer, famgateway_client, grants, keymint, razorpay_client, recovery, store

logger = logging.getLogger("billing.main")

app = FastAPI(title="DataAgent Billing", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
    max_age=600,
)


def _ready_or_503(**kwargs) -> None:
    """Fail closed on a misconfigured rail with a 503 the buyer can read."""
    try:
        cfg.require_ready(**kwargs)
    except cfg.ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.middleware("http")
async def _billing_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    return response

_EMAIL_RE = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


# ── request models (validate at the boundary) ─────────────────────────


class CreateOrderRequest(BaseModel):
    plan: str = Field(pattern="^(pro|lifetime)$")
    email: str = Field(pattern=_EMAIL_RE)


class VerifyRequest(BaseModel):
    order_id: str = Field(min_length=6, max_length=64)
    payment_id: str = Field(min_length=6, max_length=64)
    signature: str = Field(min_length=32, max_length=128)


class CreateSubscriptionRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_RE, max_length=254)


class VerifySubscriptionRequest(BaseModel):
    subscription_id: str = Field(min_length=6, max_length=64)
    payment_id: str = Field(min_length=6, max_length=64)
    signature: str = Field(min_length=32, max_length=128)


# ── shared helpers ────────────────────────────────────────────────────


def _iso_from_epoch(value: Any) -> Optional[str]:
    """Unix epoch (int/str) → ISO-8601 UTC string; None when missing/invalid."""
    try:
        ts = int(value)
    except (ValueError, TypeError):
        return None
    if ts <= 0:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _plan_from_order(order_id: str) -> Optional[str]:
    order = store.get_order(order_id)
    return order["plan"] if order else None


# ── 1) create order ───────────────────────────────────────────────────


def _fam_redirect_url() -> Optional[str]:
    """Post-payment redirect for the FamGateway hosted checkout.

    The `{order_id}` placeholder in a configured template is substituted by
    FamGateway before redirecting; the built-in default lands on the generic
    success page (the grant itself is pushed by the webhook, not the hop).
    """
    template = (cfg.FAMGATEWAY_REDIRECT_URL
                or (cfg.BILLING_PUBLIC_URL.rstrip("/") + "/success"
                    if cfg.BILLING_PUBLIC_URL else "")).strip()
    return template or None


def _fam_create_order(req: CreateOrderRequest, plan: dict) -> dict:
    """FamGateway rail (ADR-016): hosted UPI checkout for BOTH plans.

    Pro is a pre-paid 31-day window (FamGateway cannot auto-renew); renewal
    is a NEW order row per payment — grants.py folds the longest live one.
    """
    _ready_or_503(order=True)
    amount_rupees = int(plan["amount"]) // 100  # config stores paise
    try:
        fg = famgateway_client.create_order(
            amount_rupees,
            req.email.lower(),
            webhook_url=cfg.FAMGATEWAY_WEBHOOK_URL or None,
            redirect_url=_fam_redirect_url(),
        )
    except famgateway_client.FamGatewayError as exc:
        logger.warning("famgateway create_order failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail="Payment provider unavailable") from exc
    order_id = str(fg.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=502, detail="Payment provider unavailable")
    if not (fg.get("checkout_url") or "").strip():
        # Provider-response drift: without the hosted URL the storefront
        # cannot start checkout (it surfaces "Checkout could not start").
        # Log server-side so an operator can tell schema drift from a
        # buyer-side error.
        logger.warning("famgateway create_order: no checkout_url returned for %s",
                       order_id)
    notes = {"plan": req.plan, "email": req.email.lower(), "provider": "famgateway"}
    store.record_order(
        order_id=order_id,
        email=req.email,
        plan=req.plan,
        amount=int(plan["amount"]),
        currency=plan["currency"],
        notes=notes,
        provider="famgateway",
        grant_days=31 if req.plan == "pro" else None,  # LEGACY_PRO_GRANT_DAYS window
    )
    return {
        "order_id": order_id,
        "plan": req.plan,
        "checkout_url": fg.get("checkout_url") or "",
        "qr_url": fg.get("qr_url") or "",
        "upi_intent": fg.get("upi_intent") or "",
        "amount": plan["amount"],
        "currency": plan["currency"],
        "name": "DataAgent",
        "description": plan["label"],
    }


@app.post("/create-order")
async def create_order(req: CreateOrderRequest, request: Request) -> dict:
    from billing_server import accounts as accounts_mod

    # Per-IP cap: every call opens a provider-side order (a 5-minute UPI
    # session + checkout page) — an unsupervised bucket is an easy way to
    # burn a provider's patience. In-process, like the other buckets.
    accounts_mod.check_rate_limit(request, "create-order")
    plan = cfg.PLANS[req.plan]
    if cfg.PAYMENTS_PROVIDER == "famgateway":
        return _fam_create_order(req, plan)
    if plan.get("subscription"):
        # Pro bills through Razorpay Subscriptions (auto-renewing) — a
        # one-time order would grant 31 days and silently expire.
        raise HTTPException(status_code=400,
                            detail="Pro is a subscription — use /create-subscription")
    _ready_or_503(order=True)
    notes = {"plan": req.plan, "email": req.email.lower()}
    try:
        order = razorpay_client.create_order(
            amount=plan["amount"],
            currency=plan["currency"],
            receipt=uuid.uuid4().hex,
            notes=notes,
        )
    except razorpay_client.RazorpayError as exc:
        logger.warning("create_order failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment provider unavailable") from exc
    store.record_order(
        order_id=order["id"],
        email=req.email,
        plan=req.plan,
        amount=plan["amount"],
        currency=plan["currency"],
        notes=notes,
    )
    return {
        "order_id": order["id"],
        "key_id": cfg.RAZORPAY_KEY_ID,
        "amount": plan["amount"],
        "currency": plan["currency"],
        "name": "DataAgent",
        "description": plan["label"],
    }


# ── 1b) public provider info (storefront copy + honest checkout flow) ─


@app.get("/v1/payments-info")
async def payments_info() -> dict:
    """Which rail is live ('famgateway' | 'razorpay' | 'off'). Not a secret."""
    return {"ok": True, "provider": cfg.PAYMENTS_PROVIDER}


# ── 2) verify checkout result (keyless — entitlement follows the email) ─


@app.post("/verify")
async def verify_payment(req: VerifyRequest) -> dict:
    if cfg.PAYMENTS_PROVIDER != "razorpay":
        # This HMAC is keyed with RAZORPAY_KEY_SECRET; on any other rail that
        # secret may be unset (empty-key HMAC is forgeable) — refuse, fail-closed.
        raise HTTPException(status_code=403,
                            detail="Card checkout is dormant on this deployment")
    _ready_or_503(order=True)
    if not razorpay_client.verify_checkout_signature(req.order_id, req.payment_id, req.signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    # One order row, one rail: a FamGateway order is settled by FamGateway's
    # own webhook and must never be paid through the Razorpay verifier.
    existing = store.get_order(req.order_id)
    if existing and str(existing.get("provider") or "") != "razorpay":
        raise HTTPException(status_code=403,
                            detail="Order belongs to a different payment rail")
    order = store.mark_paid(req.order_id, req.payment_id)
    if not order:
        raise HTTPException(status_code=404, detail="Unknown order")
    # No key minted: the paid order itself grants the plan by email
    # (resolved via GET /v1/entitlements). Nothing secret is returned here.
    return {"ok": True, "plan": order["plan"]}


# ── 2b) Pro subscription checkout (auto-renewing; keyless) ───────────


@app.post("/create-subscription")
async def create_subscription(req: CreateSubscriptionRequest) -> dict:
    """Start an auto-renewing Pro subscription (Razorpay Subscriptions)."""
    _ready_or_503(order=True)
    if not cfg.RAZORPAY_PLAN_ID_PRO:
        raise HTTPException(status_code=503,
                            detail="Subscription plan is not configured yet")
    plan = cfg.PLANS["pro"]
    notes = {"plan": "pro", "email": req.email.lower()}
    try:
        sub = razorpay_client.create_subscription(cfg.RAZORPAY_PLAN_ID_PRO, notes)
    except razorpay_client.RazorpayError as exc:
        logger.warning("create_subscription failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment provider unavailable") from exc
    store.upsert_subscription(
        sub["id"], req.email, "pro",
        status=str(sub.get("status") or "created"), notes=notes,
    )
    return {
        "subscription_id": sub["id"],
        "key_id": cfg.RAZORPAY_KEY_ID,
        "amount": plan["amount"],
        "currency": plan["currency"],
        "name": "DataAgent",
        "description": plan["label"],
    }


def _notes_email(entity: dict) -> str:
    """Lowercase buyer email from a Razorpay entity's notes, if present."""
    notes = entity.get("notes")
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except ValueError:
            notes = None
    if isinstance(notes, dict):
        return str(notes.get("email") or "").lower()
    return ""


@app.post("/verify-subscription")
async def verify_subscription(req: VerifySubscriptionRequest) -> dict:
    """Verify checkout HMAC, activate the subscription, return the period."""
    if cfg.PAYMENTS_PROVIDER != "razorpay":
        # Same fail-closed reason as /verify: empty-key HMAC is forgeable.
        raise HTTPException(status_code=403,
                            detail="Card checkout is dormant on this deployment")
    _ready_or_503(order=True)
    if not razorpay_client.verify_subscription_checkout_signature(
            req.subscription_id, req.payment_id, req.signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    row = store.get_subscription(req.subscription_id)
    live = razorpay_client.fetch_subscription(req.subscription_id)
    email = str((row or {}).get("email") or "").lower() or _notes_email(live or {})
    if not email:
        raise HTTPException(status_code=404, detail="Unknown subscription")
    status = str((live or {}).get("status") or (row or {}).get("status")
                 or "active").lower()
    current_end = (_iso_from_epoch((live or {}).get("current_end"))
                   or (row or {}).get("current_end"))
    updated = store.upsert_subscription(
        req.subscription_id, email, "pro", status=status,
        current_end=current_end, payment_id=req.payment_id,
        notes={"plan": "pro", "email": email},
    )
    renews_at = (updated or {}).get("current_end") or current_end
    emailer.send_subscription_email(email, "pro", renews_at or "")
    return {"ok": True, "plan": "pro", "status": status, "renews_at": renews_at}


# ── 3) webhooks (payment_link.paid / payment.captured / subscription.* / refund…) ─

#: Subscription lifecycle events folded into the local row (status/period).
_SUBSCRIPTION_EVENTS = frozenset({
    "subscription.authenticated", "subscription.activated", "subscription.charged",
    "subscription.pending", "subscription.halted", "subscription.cancelled",
    "subscription.completed", "subscription.paused", "subscription.resumed",
    "subscription.expired",
})


@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request) -> JSONResponse:
    _ready_or_503(webhook=True)
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not razorpay_client.verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = json.loads(raw.decode("utf-8"))
    kind = str(event.get("event", ""))

    if kind in ("payment_link.paid", "payment.captured"):
        return _handle_payment_captured(event, kind)
    if kind in _SUBSCRIPTION_EVENTS:
        return _handle_subscription_event(event, kind)
    if kind == "refund.processed":
        return _handle_refund(event)
    # payment_link.cancelled / .expired / anything else: acknowledged, ignored.
    return {"ok": True, "ignored": kind or "unknown"}


def _entity(event: dict, name: str) -> dict:
    return ((event.get("payload") or {}).get(name) or {}).get("entity") or {}


def _order_meta(event: dict) -> tuple[str, str, Optional[str]]:
    """Extract (order_id, payment_id, receipt) from any supported event shape."""
    order = _entity(event, "order")
    payment = _entity(event, "payment")
    order_id = order.get("id") or payment.get("order_id") or ""
    payment_id = payment.get("id") or ""
    receipt = order.get("receipt") or ""
    return order_id, payment_id, receipt


def _handle_payment_captured(event: dict, kind: str) -> JSONResponse:
    order_id, payment_id, _receipt = _order_meta(event)
    if not order_id:
        return JSONResponse({"ok": True, "ignored": "no order id"}, status_code=200)

    # Idempotency: one delivery per (event, payment).
    ref = f"{order_id}:{payment_id or 'x'}"
    if store.seen_event(kind, ref):
        return {"ok": True, "duplicate": True}

    def _expected_amount(plan: str) -> int:
        try:
            return int((cfg.PLANS.get(plan) or {}).get("amount") or 0)
        except (ValueError, TypeError):
            return 0

    try:
        order = store.get_order(order_id)
        payment = _entity(event, "payment")
        if not order:
            # Payment Link flow: no local order row — record from notes/email
            # in the webhook payload instead.
            plink = _entity(event, "payment_link")
            email = (payment.get("email") or plink.get("customer", {}).get("email") or "").lower()
            plan = _plan_from_notes(event, order)
            if not plan or not email:
                return JSONResponse({"ok": True, "ignored": "unmatched payment"}, status_code=200)
            try:
                amount = int(payment.get("amount") or order_amount(event) or 0)
            except (ValueError, TypeError):
                amount = 0
            expected = _expected_amount(plan)
            if expected and amount < expected:
                logger.warning("underpaid payment-link order ignored: %s < %s (%s)",
                               amount, expected, plan)
                return JSONResponse({"ok": True, "ignored": "underpaid"}, status_code=200)
            store.record_order(order_id, email, plan, amount or 1, "INR",
                               notes={"plan": plan, "email": email})
            store.mark_paid(order_id, payment_id or "plink")
            order = store.get_order(order_id)
        else:
            # Checkout flow: the paid amount must cover the recorded order.
            try:
                paid_amount = int(payment.get("amount") or order_amount(event) or 0)
            except (ValueError, TypeError):
                paid_amount = 0
            try:
                recorded = int(order.get("amount") or 0)
            except (ValueError, TypeError):
                recorded = 0
            if recorded and paid_amount and paid_amount < recorded:
                logger.warning("underpaid order ignored: %s < %s (%s)",
                               paid_amount, recorded, order_id)
                return JSONResponse({"ok": True, "ignored": "underpaid"}, status_code=200)
        if order["status"] != "paid":
            order = store.mark_paid(order_id, payment_id or order.get("payment_id") or "webhook") or order
        # Keyless: the paid order itself grants the plan by email.
        return JSONResponse({"ok": True, "plan": order["plan"]})
    except Exception as exc:  # noqa: BLE001
        # Roll the idempotency marker back so Razorpay's retry reprocesses.
        store.record_event_failure(kind, ref, str(exc))
        logger.exception("webhook processing failed for %s", ref)
        raise HTTPException(status_code=500, detail="Processing failed") from exc


def _handle_subscription_event(event: dict, kind: str) -> JSONResponse:
    """subscription.* lifecycle → keep the local row's status/period current.

    Entitlement truth stays "paid-through date in the future"; these events
    record state so /v1/entitlements reports renewal and expiry correctly.
    """
    sub = _entity(event, "subscription")
    sub_id = sub.get("id") or ""
    if not sub_id:
        return JSONResponse({"ok": True, "ignored": "no subscription id"}, status_code=200)

    payment_id = _entity(event, "payment").get("id") or ""
    # charged fires once per cycle: key idempotency on the payment (or the
    # event timestamp) so a retried delivery dedupes but cycle 2 does not.
    ref = f"{sub_id}:{payment_id or event.get('created_at') or 'x'}"
    if store.seen_event(kind, ref):
        return {"ok": True, "duplicate": True}

    try:
        row = store.get_subscription(sub_id)
        email = str((row or {}).get("email") or "").lower() or _notes_email(sub)
        if not email:
            return JSONResponse({"ok": True, "ignored": "unmatched subscription"},
                                status_code=200)
        status = str(sub.get("status") or "").lower() or "updated"
        store.upsert_subscription(
            sub_id, email, "pro", status=status,
            current_end=_iso_from_epoch(sub.get("current_end")),
            payment_id=payment_id, notes={"plan": "pro", "email": email},
        )
        if sub.get("cancel_at_period_end"):
            # upsert never clears the flag; set it when Razorpay says so.
            store.set_cancel_at_period_end(sub_id, True)
        return JSONResponse({"ok": True, "subscription_id": sub_id, "status": status})
    except Exception as exc:  # noqa: BLE001
        # Roll the idempotency marker back so Razorpay's retry reprocesses.
        store.record_event_failure(kind, ref, str(exc))
        logger.exception("subscription webhook failed for %s", ref)
        raise HTTPException(status_code=500, detail="Processing failed") from exc


# ── 3b) FamGateway UPI webhooks (ADR-016: fail-closed HMAC rail) ──────


@app.post("/famgateway/webhook")
async def famgateway_webhook(request: Request) -> JSONResponse:
    """One UTR = one paid order, one grant. Signature is the trust boundary.

    - HMAC-SHA256 over the RAW body, keyed with FAMGATEWAY_API_KEY — a body
      that cannot be authenticated is refused before anything is read.
    - Event/status allowlists; anything unrecognised is acknowledged (200)
      but ignored, so FamGateway never retries forever.
    - Idempotency: (order_id, utr) marker + the UTR owner check + a DB
      partial unique index as the race backstop.

    The decision path lives in billing_server.recovery.process_fam_event —
    the identical guards also settle missed webhooks via the /admin/reconcile
    sweep, so webhook and sweep can never diverge.
    """
    raw = await request.body()
    signature = request.headers.get("X-FamGateway-Signature", "")
    if not signature or not famgateway_client.verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except ValueError:
        return JSONResponse({"ok": True, "ignored": "malformed body"}, status_code=200)
    return JSONResponse(recovery.process_fam_event(event), status_code=200)



def order_amount(event: dict) -> int:
    order = _entity(event, "order")
    try:
        return int(order.get("amount") or 0)
    except (ValueError, TypeError):
        return 0


def _plan_from_notes(event: dict, order: Optional[dict]) -> Optional[str]:
    order_ent = _entity(event, "order")
    notes = (order_ent.get("notes") if isinstance(order_ent.get("notes"), dict) else None) \
        or (order or {}).get("notes") or {}
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except ValueError:
            notes = {}
    plan = str(notes.get("plan", "")).lower()
    return plan if plan in cfg.PLANS else None


def _handle_refund(event: dict) -> JSONResponse:
    payment = _entity(event, "payment")
    payment_id = payment.get("id") or ""
    refund_ent = _entity(event, "refund")
    refunded_paise = int(refund_ent.get("amount") or 0)
    order = store.refund_order(payment_id) if payment_id else None
    if order:
        # Full refund (amount == order amount) revokes; partial keeps the plan.
        if refunded_paise and refunded_paise < int(order.get("amount") or 0):
            return {"ok": True, "revoked": False, "reason": "partial refund"}
        return {"ok": True, "revoked": True, "order_id": order["order_id"]}
    return {"ok": True, "ignored": "unknown payment"}


# ── 4) success page (?order_id= lifetime / ?subscription_id= pro) ────


def _success_css() -> str:
    return (
        "body{font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e2e8f0;"
        "display:grid;place-items:center;min-height:100vh;margin:0}"
        ".card{background:#1e293b;padding:40px;border-radius:16px;max-width:560px;width:92%}"
    )


def _subscription_success_page(subscription_id: str) -> HTMLResponse:
    sub = store.get_subscription(subscription_id)
    if not sub:
        return HTMLResponse(
            "<h2>Subscription not found</h2><p>Check the link or contact support with your payment ID.</p>",
            status_code=404,
        )
    status = str(sub.get("status") or "created").lower()
    if status in ("created", "pending", "authenticated", "halted"):
        return HTMLResponse(
            "<h2>Almost there…</h2><p>Your subscription is confirming — refresh in a "
            "minute. Nothing else is needed from you.</p>",
            status_code=202,
        )
    renews_on = html.escape(str(sub.get("current_end") or "")[:10])
    email_html = html.escape(str(sub.get("email") or ""))
    return HTMLResponse(f"""\
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>DataAgent — subscription active</title>
<style>{_success_css()}</style></head><body><div class="card">
<h2 style="margin-top:0">🎉 Subscription active — {html.escape(cfg.PLANS["pro"]["label"])}</h2>
<p>Sign in to DataAgent with <b>{email_html}</b> — Pro is unlocked on all your devices.</p>
<p>Auto-renews on <b>{renews_on}</b>. Cancel anytime in Settings → Subscription —
Pro stays active until then.</p>
<p style="color:#64748b;font-size:13px">No codes to paste — your subscription is
tied to this email. Questions? Contact support with your payment ID.</p>
</div></body></html>""")


@app.get("/success", response_class=HTMLResponse)
async def success_page(order_id: str = "", subscription_id: str = "") -> HTMLResponse:
    """Post-checkout page (keyless): entitlement follows the buyer's email."""
    if subscription_id:
        return _subscription_success_page(subscription_id)
    order = store.get_order(order_id) if order_id else None
    if not order or order["status"] not in ("paid", "created"):
        return HTMLResponse(
            "<h2>Order not found</h2><p>Check the link or contact support with your payment ID.</p>",
            status_code=404,
        )
    if order["status"] != "paid":
        return HTMLResponse(
            "<h2>Almost there…</h2><p>Your payment is confirming — refresh in a "
            "minute. Nothing else is needed from you.</p>",
            status_code=202,
        )
    email_html = html.escape(str(order.get("email") or ""))
    plan_label = html.escape(str(cfg.PLANS.get(order["plan"], {}).get("label") or order["plan"]))
    return HTMLResponse(f"""\
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>DataAgent — payment successful</title>
<style>{_success_css()}</style></head><body><div class="card">
<h2 style="margin-top:0">🎉 Payment successful — {plan_label}</h2>
<p>Sign in to DataAgent with <b>{email_html}</b> — your plan is unlocked on all your devices.</p>
<p>In DataAgent: <b>Settings → Subscription → Sync</b> (sign in with the same account).
No codes to paste — your purchase is tied to this email.</p>
<p style="color:#fbbf24;font-size:13px">Use the same account email <b>{email_html}</b>
wherever you sign in. Questions? Contact support with your payment ID.</p>
</div></body></html>""")


# ── 5) entitlement read API (HMAC-signed; used by the desktop app) ───


class EntitlementsOut(BaseModel):
    email: str
    plan: str
    status: str
    expires_at: Optional[str] = None
    source: str
    token: Optional[str] = None
    auto_renew: Optional[bool] = None
    cancel_at_period_end: Optional[bool] = None


def _verify_signed(message: str, exp: str, sig: str) -> None:
    """Verify an HMAC-signed request body with a 5-minute freshness window."""
    cfg.require_ready(sync=True)
    if not message or not exp or not sig:
        raise HTTPException(status_code=400, detail="signed fields are required")
    try:
        exp_ts = int(exp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad exp") from exc
    skew = 300
    import time as _time
    if abs(_time.time() - exp_ts) > skew:
        raise HTTPException(status_code=401, detail="stale request")
    expected = hmac.new(
        cfg.ENTITLEMENTS_SYNC_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="bad signature")


def _verify_sync_sig(email: str, exp: str, sig: str) -> None:
    if not email:
        raise HTTPException(status_code=400, detail="email, exp and sig are required")
    _verify_signed(f"{email}|{exp}", exp, sig)


@app.get("/v1/entitlements", response_model=EntitlementsOut)
async def entitlements(email: str, exp: str, sig: str) -> EntitlementsOut:
    _verify_sync_sig(email.lower(), exp, sig)
    email = email.lower()
    orders = store.paid_orders_for_email(email)
    subs = store.subscriptions_for_email(email)
    grant = grants.current_grant(orders, subs)
    if grant is None:
        # No live grant: report the most recent purchase as expired (useful in
        # support), or free when there is no purchase history at all.
        if subs:
            return EntitlementsOut(email=email, plan="pro", status="expired",
                                   source="billing")
        if orders:
            return EntitlementsOut(email=email,
                                   plan=str(orders[0].get("plan") or "pro"),
                                   status="expired", source="billing")
        return EntitlementsOut(email=email, plan="free", status="none", source="billing")
    token: Optional[str] = None
    if grant.plan in ("pro", "lifetime"):
        # Short-lived signed assertion (P2): the app verifies this with the
        # baked public key, so cloud_sync grants cannot be forged via DB edit.
        # Fail-open to a tokenless response (old app behavior) when the
        # signing key is unavailable — never 500 the lookup.
        try:
            token = keymint.mint_entitlement_token(
                email, grant.plan, grant_expires_at=grant.expires_at)
        except Exception as exc:  # noqa: BLE001
            logger.warning("entitlement token mint failed: %s", exc)
            token = None
    is_sub = grant.source == "subscription"
    return EntitlementsOut(
        email=email,
        plan=grant.plan,
        status="active",
        expires_at=grant.expires_at,
        source="billing",
        token=token,
        auto_renew=bool(grant.renews) if is_sub else None,
        cancel_at_period_end=bool(grant.cancel_at_period_end) if is_sub else None,
    )


# ── 5b) central accounts (ADR-002): email/password + session tokens ─


SESSION_COOKIE = getattr(cfg, "SESSION_COOKIE_NAME", "da_session")


def _cookie_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "path": "/",
        "httponly": True,
        "secure": bool(getattr(cfg, "SESSION_COOKIE_SECURE", True)),
        "samesite": "lax",
    }
    domain = str(getattr(cfg, "SESSION_COOKIE_DOMAIN", "") or "").strip()
    if domain:
        kwargs["domain"] = domain
    return kwargs


def _set_session_cookie(response: Response, token: str) -> None:
    from billing_server import accounts as accounts_mod

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=accounts_mod.SESSION_TOKEN_DAYS * 24 * 60 * 60,
        **_cookie_kwargs(),
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs())


def _start_website_session(response: Response, row: dict) -> None:
    from billing_server import accounts as accounts_mod

    token = accounts_mod.new_session_token()
    store.create_account_session(
        row["user_id"],
        row["email"],
        accounts_mod.hash_session_token(token),
        accounts_mod.session_expires_at(),
    )
    _set_session_cookie(response, token)


def _send_account_verification(row: dict) -> bool:
    """Issue a fresh single-use link and send it. Never returns the token."""
    from billing_server import accounts as accounts_mod

    token = accounts_mod.new_email_verification_token()
    store.create_email_verification(
        row["user_id"],
        row["email"],
        accounts_mod.hash_email_verification_token(token),
        accounts_mod.email_verification_expires_at(),
    )
    return emailer.send_verification_email(
        row["email"], accounts_mod.email_verification_link(token)
    )


def _account_from_request(request: Request) -> dict:
    """Bearer token first (desktop app), then the website cookie."""
    from billing_server import accounts as accounts_mod

    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        try:
            payload = accounts_mod.verify_session_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        row = store.get_account_by_id(str(payload.get("user_id", "")))
        if not row or row.get("status") != "active":
            raise HTTPException(status_code=401, detail="account not found or inactive")
        return row

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        session = store.get_account_session(accounts_mod.hash_session_token(cookie))
        if session:
            row = store.get_account_by_id(str(session.get("user_id", "")))
            if row and row.get("status") == "active":
                return row
        raise HTTPException(status_code=401, detail="session expired or invalid")

    raise HTTPException(status_code=401, detail="missing session")


def _safe_purchase(order: dict) -> dict:
    """Only fields the buyer already knows; never keys or payment IDs."""
    return {
        "order_id": order.get("order_id"),
        "plan": order.get("plan"),
        "amount": order.get("amount"),
        "currency": order.get("currency"),
        "status": order.get("status"),
        "provider": order.get("provider"),
        "created_at": order.get("created_at"),
        "paid_at": order.get("updated_at"),
    }


def _safe_subscription(sub: Optional[dict]) -> Optional[dict]:
    if not sub:
        return None
    return {
        "plan": sub.get("plan"),
        "status": sub.get("status"),
        "current_end": sub.get("current_end"),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }


class AccountRegisterRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_RE, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AccountLoginRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_RE, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=4096)


@app.post("/v1/accounts/register")
async def accounts_register(req: AccountRegisterRequest, request: Request,
                            response: Response) -> dict:
    """Create an unverified account, or claim an unverified email.

    A buyer whose address was registered first by someone else can take the
    still-unverified account over; until an email is confirmed, the summary
    never reveals a plan or purchase history. A verified email is final.
    """
    from billing_server import accounts as accounts_mod

    accounts_mod.check_rate_limit(request, "register")
    email = req.email.strip().lower()
    try:
        accounts_mod.validate_password(req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    password_hash = accounts_mod.hash_password(req.password)
    row = store.get_account_by_email(email)
    if row is not None:
        if row.get("email_verified_at"):
            raise HTTPException(status_code=409, detail="email already registered")
        store.update_account_password(row["user_id"], password_hash)
        store.delete_account_sessions_for_user(row["user_id"])
        row = store.get_account_by_email(email) or row
    else:
        row = store.create_account(email, password_hash)
        if row is None:
            row = store.get_account_by_email(email)
            if row is None or row.get("email_verified_at"):
                raise HTTPException(status_code=409, detail="email already registered")
            store.update_account_password(row["user_id"], password_hash)
            store.delete_account_sessions_for_user(row["user_id"])
            row = store.get_account_by_email(email) or row

    verification_sent = _send_account_verification(row)
    _start_website_session(response, row)
    logger.info("account registered: %s", accounts_mod.mask_email(email))
    return {
        "ok": True,
        "user_id": row["user_id"],
        "email": row["email"],
        "email_verified": False,
        "verification_sent": bool(verification_sent),
    }


@app.post("/v1/accounts/login")
async def accounts_login(req: AccountLoginRequest, request: Request,
                         response: Response) -> dict:
    """Authenticate; set the website cookie and (when possible) return a token."""
    from billing_server import accounts as accounts_mod

    accounts_mod.check_rate_limit(request, "login")
    email = req.email.strip().lower()
    row = store.get_account_by_email(email)
    if not row or row.get("status") != "active":
        # Same error AND same work as a wrong password — no enumeration, no
        # timing signal (verify against a dummy hash so bcrypt always runs).
        accounts_mod.verify_password(req.password, accounts_mod.dummy_password_hash().decode())
        raise HTTPException(status_code=401, detail="invalid email or password")
    if not accounts_mod.verify_password(req.password, row.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="invalid email or password")
    store.touch_account(row["user_id"])
    try:
        token = accounts_mod.mint_session_token(email, row["user_id"])
    except FileNotFoundError as exc:
        if not cfg.KEYLESS:
            logger.error("session token mint failed: %s", exc)
            raise HTTPException(status_code=503, detail="signing key unavailable") from exc
        _start_website_session(response, row)
        logger.info("account login without a session token (keyless): %s",
                    accounts_mod.mask_email(email))
        return {"ok": True, "email": email, "user_id": row["user_id"],
                "email_verified": bool(row.get("email_verified_at"))}
    _start_website_session(response, row)
    logger.info("account login: %s", accounts_mod.mask_email(email))
    return {"ok": True, "token": token, "email": email, "user_id": row["user_id"],
            "email_verified": bool(row.get("email_verified_at"))}


@app.get("/v1/accounts/me")
async def accounts_me(request: Request) -> dict:
    """Profile for the app's Bearer token or the website cookie."""
    row = _account_from_request(request)
    return {"ok": True, "user_id": row["user_id"], "email": row["email"],
            "created_at": row["created_at"],
            "email_verified": bool(row.get("email_verified_at"))}


@app.get("/v1/accounts/summary")
async def accounts_summary(request: Request) -> dict:
    """Plan, renewal state, and safe purchase history for one account.

    Unverified accounts get no plan or purchase data: proving control of the
    email is the only thing that separates a buyer from someone who merely
    typed their address into the registration form.
    """
    row = _account_from_request(request)
    if not row.get("email_verified_at"):
        return {
            "ok": True,
            "user": {
                "user_id": row["user_id"],
                "email": row["email"],
                "created_at": row["created_at"],
            },
            "email_verified": False,
            "plan": None,
            "status": "unverified",
            "expires_at": None,
            "source": "none",
            "auto_renew": None,
            "cancel_at_period_end": None,
            "purchases": [],
            "subscription": None,
        }
    email = str(row["email"]).lower()
    orders = store.paid_orders_for_email(email)
    subscriptions = store.subscriptions_for_email(email)
    grant = grants.current_grant(orders, subscriptions)
    latest_subscription = subscriptions[0] if subscriptions else None

    if grant:
        plan = grant.plan
        status = "active"
        expires_at = grant.expires_at
        source = "billing"
        auto_renew = bool(grant.renews) if grant.source == "subscription" else None
        cancel_at_period_end = (
            bool(grant.cancel_at_period_end) if grant.source == "subscription" else None
        )
    else:
        plan = "free"
        status = "free"
        expires_at = None
        source = "none"
        auto_renew = None
        cancel_at_period_end = None

    return {
        "ok": True,
        "user": {
            "user_id": row["user_id"],
            "email": row["email"],
            "created_at": row["created_at"],
        },
        "email_verified": True,
        "plan": plan,
        "status": status,
        "expires_at": expires_at,
        "source": source,
        "auto_renew": auto_renew,
        "cancel_at_period_end": cancel_at_period_end,
        "purchases": [_safe_purchase(order) for order in orders[:100]],
        "subscription": _safe_subscription(latest_subscription),
    }


@app.post("/v1/accounts/verify-email")
async def accounts_verify_email(req: VerifyEmailRequest, request: Request) -> dict:
    """Consume a single-use link and unlock the account's plan/history."""
    from billing_server import accounts as accounts_mod

    accounts_mod.check_rate_limit(request, "verify-email")
    row = store.consume_email_verification(
        accounts_mod.hash_email_verification_token(req.token)
    )
    if row is None:
        raise HTTPException(status_code=400, detail="invalid or expired verification link")
    logger.info("account email verified: %s", accounts_mod.mask_email(row["email"]))
    return {"ok": True, "email_verified": True}


@app.post("/v1/accounts/resend-verification")
async def accounts_resend_verification(request: Request) -> dict:
    """Issue a fresh link for the signed-in account. Old links stop working."""
    from billing_server import accounts as accounts_mod

    row = _account_from_request(request)
    if row.get("email_verified_at"):
        return {"ok": True, "email_verified": True, "already_verified": True}
    accounts_mod.check_rate_limit(request, "resend-verification")
    sent = _send_account_verification(row)
    logger.info("verification email resent: %s", accounts_mod.mask_email(row["email"]))
    return {"ok": True, "email_verified": False, "verification_sent": bool(sent)}


@app.post("/v1/accounts/logout")
async def accounts_logout(request: Request, response: Response) -> dict:
    """Revoke the website session and always clear the cookie."""
    from billing_server import accounts as accounts_mod

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        store.delete_account_session(accounts_mod.hash_session_token(cookie))
    _clear_session_cookie(response)
    return {"ok": True}


@app.post("/v1/subscriptions/cancel")
async def cancel_subscription(request: Request) -> dict:
    """Schedule cancel-at-period-end for the session email's live subscription.

    Pro stays active until the paid-through date; Razorpay simply stops the
    next charge. Auth is the same Bearer session token as /v1/accounts/me.
    """
    from billing_server import accounts as accounts_mod

    accounts_mod.check_rate_limit(request, "cancel")
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = accounts_mod.verify_session_token(auth.split(" ", 1)[1].strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    email = str(payload.get("email") or "").lower()

    live = [
        (sub, grant)
        for sub, grant in (
            (s, grants.subscription_grant(s))
            for s in store.subscriptions_for_email(email)
        )
        if grant is not None
    ]
    if not live:
        raise HTTPException(status_code=404, detail="No active subscription")
    _sub, grant = max(live, key=lambda pair: str(pair[1].expires_at or ""))
    if grant.cancel_at_period_end:
        return {"ok": True, "already_scheduled": True, "cancel_at_period_end": True,
                "access_until": grant.expires_at}
    if razorpay_client.cancel_subscription(grant.subscription_id, at_cycle_end=True) is None:
        raise HTTPException(status_code=502, detail="Payment provider unavailable")
    store.set_cancel_at_period_end(grant.subscription_id, True)
    logger.info("subscription cancel scheduled: %s", accounts_mod.mask_email(email))
    return {"ok": True, "cancel_at_period_end": True, "access_until": grant.expires_at}


# ── 6) one-time key redemption registry (used by the app on activation) ─


class RedeemRequest(BaseModel):
    key: str = Field(min_length=16, max_length=4096)
    key_hash: str = Field(min_length=8, max_length=64)
    email: str = Field(pattern=_EMAIL_RE)
    user_id: str = Field(min_length=1, max_length=128)
    exp: str = Field(min_length=1, max_length=20)
    sig: str = Field(min_length=32, max_length=128)


@app.post("/v1/keys/redeem")
async def redeem_product_key(req: RedeemRequest) -> dict:
    """One-time use: the first account to redeem a key owns it.

    A different account — even with the buyer's email — gets 409; unknown and
    refunded keys are refused. Re-activation by the same account is idempotent.
    """
    email = req.email.lower()
    if not hmac.compare_digest(keymint.key_hash(req.key), req.key_hash):
        raise HTTPException(status_code=401, detail="key hash mismatch")
    _verify_signed(f"{req.key_hash}|{email}|{req.user_id}|{req.exp}", req.exp, req.sig)

    result = store.redeem_key(req.key_hash, email, req.user_id)
    status = result["status"]
    if status == "conflict":
        raise HTTPException(
            status_code=409,
            detail={"status": "conflict",
                    "message": "This key was already activated on another account"},
        )
    if status == "refunded":
        raise HTTPException(
            status_code=409,
            detail={"status": "refunded",
                    "message": "This key was refunded and can no longer be activated"},
        )
    if status == "unknown":
        raise HTTPException(
            status_code=404,
            detail={"status": "unknown",
                    "message": "This key was not issued by the billing server"},
        )
    return {"ok": True, "status": status}


# ── 6b) feature tickets + proxy relay (P3: value gated at the server) ─


class TicketRequest(BaseModel):
    mode: str = Field(pattern="^(token|key)$")
    email: str = Field(pattern=_EMAIL_RE)
    feature: str = Field(min_length=1, max_length=64)
    # token mode: a live entitlement token from GET /v1/entitlements.
    token: str = Field(default="", max_length=4096)
    # key mode: HMAC-signed product-key claim ("ticket|…" envelope — distinct
    # from the redeem envelope so captures cannot replay cross-endpoint).
    key: str = Field(default="", max_length=4096)
    key_hash: str = ""
    user_id: str = ""
    exp: str = ""
    sig: str = ""


def _ticket_grant_or_raise(req: TicketRequest) -> tuple[str, str]:
    """Validate proof of purchase for a feature. Returns (plan, key_hash).

    400 unknown feature / malformed · 401 bad auth/forgery ·
    402 known-bad or lapsed grant (refunded/conflict/expired) ·
    404 unknown (never issued here) · 403 plan does not cover the feature.
    """
    email = req.email.lower()
    feature = req.feature
    allowed = keymint.FEATURE_PLANS.get(feature)
    if not allowed:
        raise HTTPException(status_code=400, detail="Unknown feature")
    key_hash_val = ""
    if req.mode == "token":
        try:
            payload = keymint.verify_entitlement_token(req.token, email)
        except ValueError:
            logger.debug("feature ticket: bad entitlement token for %s", email)
            raise HTTPException(status_code=401, detail="Ticket rejected") from None
        plan = str(payload.get("plan", "")).lower()
        # Refund/cancel/expiry after token mint revokes ticketing too: the
        # grant fold must still find a live purchase.
        if grants.current_grant(store.paid_orders_for_email(email),
                                store.subscriptions_for_email(email)) is None:
            raise HTTPException(status_code=402, detail="Purchase refunded or expired")
    else:
        if not req.key or not req.key_hash or not req.user_id or not req.exp or not req.sig:
            raise HTTPException(status_code=400, detail="key, key_hash, user_id, exp and sig are required")
        if not hmac.compare_digest(keymint.key_hash(req.key), req.key_hash):
            raise HTTPException(status_code=401, detail="Ticket rejected")
        _verify_signed(f"ticket|{req.key_hash}|{email}|{req.user_id}|{req.exp}",
                       req.exp, req.sig)
        order = store.get_order_by_key_hash(req.key_hash)
        if order is None:
            raise HTTPException(status_code=404, detail="Key was not issued by the billing server")
        if order.get("status") == "refunded":
            raise HTTPException(status_code=402, detail="Key was refunded")
        if str(order.get("email", "")).lower() != email:
            raise HTTPException(status_code=401, detail="Ticket rejected")
        if str(order.get("plan", "")).lower() == "pro" and grants.order_grant(order) is None:
            raise HTTPException(status_code=402, detail="Purchase refunded or expired")
        # One-time use extends to ticketing: a key redeemed by another
        # account cannot mint tickets here (sharing stays visible + blocked).
        owner = store.get_redemption(req.key_hash)
        if owner and str(owner.get("user_id") or "") != str(req.user_id):
            raise HTTPException(status_code=409, detail="Key belongs to another account")
        plan = str(order.get("plan", "")).lower()
        key_hash_val = req.key_hash
    if plan not in allowed:
        raise HTTPException(status_code=403, detail="Plan does not cover this feature")
    # P5: blocked vectors stay blocked even with otherwise-valid proof.
    if store.is_blocked("email", email) or (
            key_hash_val and store.is_blocked("key_hash", key_hash_val)):
        raise HTTPException(status_code=402, detail="Grant blocked — contact support")
    return plan, key_hash_val


@app.post("/v1/feature-ticket")
async def feature_ticket(req: TicketRequest) -> dict:
    """Authorize one paid action: verify purchase proof, return a 24h ticket.

    The ticket (not the DB) is what value endpoints accept — a forged local
    database alone can never produce one. Every issuance is audit-logged.
    """
    plan, key_hash_val = _ticket_grant_or_raise(req)
    try:
        ticket = keymint.mint_feature_ticket(req.email.lower(), req.feature, plan)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Ticket rejected") from exc
    store.log_feature_use(req.email.lower(), req.feature, plan, key_hash_val,
                          req.user_id if req.mode == "key" else "")
    return {"ok": True, "ticket": ticket, "feature": req.feature, "plan": plan}


# Proxy relay: the list itself is public, but *this* curated, cached feed is
# a paid surface — only ticket holders get it (and every fetch is logged).
_PROXIES_CACHE: dict = {"ts": 0.0, "proxies": []}
_PROXIES_TTL = 3600.0
_PROXIFLY_URLS = (
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/https/data.txt",
)
#: Schemes the relay will ever serve (mitm-safe subset; SOCKS needs socksio).
_RELAY_SCHEMES = frozenset({"http", "https"})

_RELAY_REFRESH_LOCK = _asyncio.Lock()


async def _fetch_upstream(url: str) -> str:
    """Fetch one upstream proxy list (seam for tests to stub)."""
    import httpx as _hx

    async with _hx.AsyncClient(timeout=_hx.Timeout(20.0, connect=8.0)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


@app.get("/v1/proxies")
async def served_proxies(request: Request, ticket: str = "", protocols: str = "http,https",
                         limit: int = 300) -> dict:
    """Serve the curated proxy pool to ticket holders (P3 flagship)."""
    ticket = (request.headers.get("X-Feature-Ticket") or ticket or "")[:4096]
    try:
        payload = keymint.verify_feature_ticket(ticket, "ip_rotation")
    except ValueError:
        logger.debug("proxy relay refused a ticket")
        raise HTTPException(status_code=402, detail="Ticket rejected") from None
    if store.is_blocked("email", str(payload.get("email") or "")):
        raise HTTPException(status_code=402, detail="Grant blocked — contact support")
    limit = max(1, min(int(limit or 300), 1000))
    wanted = {p.strip().lower() for p in (protocols or "").split(",") if p.strip()}
    wanted &= set(_RELAY_SCHEMES)
    if not wanted:
        raise HTTPException(status_code=400, detail="No supported protocols requested")
    import time as _time

    async with _RELAY_REFRESH_LOCK:
        now = _time.time()
        if now - float(_PROXIES_CACHE.get("ts") or 0.0) > _PROXIES_TTL or not _PROXIES_CACHE.get("proxies"):
            seen: set = set()
            out: list = []
            try:
                for url in _PROXIFLY_URLS:
                    text = await _fetch_upstream(url)
                    for line in text.splitlines():
                        line = line.strip()[:256]
                        if not line or "://" not in line:
                            continue
                        if line.split("://", 1)[0].lower() not in _RELAY_SCHEMES:
                            continue
                        if line not in seen:
                            seen.add(line)
                            out.append(line)
            except Exception as exc:  # noqa: BLE001 — upstream outage
                if not _PROXIES_CACHE.get("proxies"):
                    raise HTTPException(status_code=502, detail="Upstream proxy lists unavailable") from exc
                out = list(_PROXIES_CACHE["proxies"])
            else:
                if not out:
                    raise HTTPException(status_code=502, detail="Upstream proxy lists empty")
                _PROXIES_CACHE["ts"] = now
                _PROXIES_CACHE["proxies"] = out
        full = list(_PROXIES_CACHE["proxies"])
    proxies = [p for p in full if p.split("://", 1)[0].lower() in wanted][:limit]
    try:
        ip = (request.client.host if request.client else "") or ""
    except Exception:  # noqa: BLE001
        ip = ""
    store.log_feature_use(str(payload.get("email") or ""), "ip_rotation",
                          str(payload.get("plan") or ""), ip=ip)
    return {"ok": True, "proxies": proxies, "count": len(proxies)}


# ── 7) support/admin registry view (token-protected) ─────────────────


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return "—"
    if len(key) <= 24:
        return "…"
    return f"{key[:14]}…{key[-6:]}"


def _require_admin(token: str, request: Optional[Request] = None) -> None:
    """Fail-closed: no token configured → disabled; wrong token → 401.

    Prefer the ``X-Admin-Token`` header so the token never lands in access
    logs; the query parameter remains for backward compatibility.
    """
    if not cfg.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin view is not configured")
    supplied = token or (
        request.headers.get("X-Admin-Token", "") if request is not None else ""
    )
    if not supplied or not hmac.compare_digest(supplied, cfg.ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#0f172a;border-radius:6px;'
        f'padding:2px 8px;font-size:12px;font-weight:700">{html.escape(text)}</span>'
    )


def _registry_row(order: dict) -> str:
    status = str(order.get("status") or "")
    if status == "refunded":
        status_html = _badge("refunded", "#f87171")
    elif status == "paid":
        status_html = _badge("paid", "#34d399")
    else:
        status_html = _badge(status or "created", "#fbbf24")

    owner = order.get("redeemed_by")
    if owner:
        redeem_html = (
            f'<span style="color:#93c5fd">{html.escape(str(owner))}</span><br>'
            f'<span class="muted">{html.escape(str(order.get("redeemed_at") or ""))}</span>'
        )
    elif status == "refunded":
        redeem_html = '<span class="muted">—</span>'
    else:
        redeem_html = '<span style="color:#fbbf24">unredeemed</span>'

    amount = f'{order.get("currency") or ""} {int(order.get("amount") or 0) / 100:,.2f}'
    return (
        "<tr>"
        f'<td><code>{html.escape(str(order.get("order_id") or ""))}</code>'
        f'<br><span class="muted">{html.escape(str(order.get("payment_id") or ""))}</span>'
        # Which rail issued it (ADR-016): reconciliations and manual refunds
        # target the right provider.
        f'<br><span class="muted">{html.escape(str(order.get("provider") or "razorpay"))}</span></td>'
        f'<td>{html.escape(str(order.get("email") or ""))}</td>'
        f'<td>{html.escape(str(order.get("plan") or ""))}</td>'
        f'<td>{html.escape(amount)}</td>'
        f"<td>{status_html}</td>"
        f'<td><code>{html.escape(_mask_key(order.get("license_key")))}</code>'
        f'<br><span class="muted">{html.escape(str(order.get("key_hash") or "—"))}</span></td>'
        f"<td>{redeem_html}</td>"
        f'<td class="muted">{html.escape(str(order.get("created_at") or ""))}</td>'
        "</tr>"
    )


@app.get("/admin/registry", response_class=HTMLResponse)
async def admin_registry(request: Request, token: str = "",
                         limit: int = 200) -> HTMLResponse:
    """Support view: orders, one-time-use key owner, refund status.

    Full keys are never rendered — masked key + key hash only; the key can be
    re-shown to the buyer via /success?order_id=.
    """
    _require_admin(token, request)
    rows = store.list_registry(limit)
    counts = store.registry_counts()
    rows_html = "".join(_registry_row(r) for r in rows) or (
        '<tr><td colspan="8" style="text-align:center;color:#64748b">No orders yet</td></tr>'
    )
    summary = (
        f'{counts["orders"]} orders · {counts["paid"]} paid · '
        f'{counts["refunded"]} refunded · {counts["redeemed"]} redeemed'
    )
    resp = HTMLResponse(f"""\
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DataAgent — key registry</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:32px}}
 h1{{margin:0 0 4px;font-size:22px}} .sub{{color:#94a3b8;font-size:13px;margin-bottom:20px}}
 .summary{{background:#1e293b;border-radius:10px;padding:10px 14px;font-size:13px;margin-bottom:16px;
          display:inline-block}}
 table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden;
        font-size:13px}}
 th{{text-align:left;padding:10px 12px;background:#243449;color:#cbd5e1;font-size:12px;
     text-transform:uppercase;letter-spacing:.4px}}
 td{{padding:10px 12px;border-top:1px solid #243449;vertical-align:top}}
 code{{font-size:12px;color:#a5b4fc;word-break:break-all}} .muted{{color:#64748b;font-size:11px}}
</style></head><body>
<h1>🔑 Key registry</h1>
<div class="sub">Orders left-joined with the one-time redemption registry — owner is the account id that first redeemed the key (support reference).</div>
<div class="summary">{summary}</div>
<table>
<thead><tr><th>Order / payment</th><th>Email</th><th>Plan</th><th>Amount</th><th>Status</th>
<th>Key (masked) / hash</th><th>Redeemed by</th><th>Created</th></tr></thead>
<tbody>{rows_html}</tbody></table>
</body></html>""")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 7b) fam-rail recovery + lifecycle admin (ADR-016 follow-ups) ───────


@app.post("/admin/reconcile")
async def admin_reconcile(request: Request, token: str = "", limit: int = 50,
                          older_than_minutes: int = 5) -> dict:
    """Grant fam orders the webhook never reached (verify-order sweep).

    FamGateway documents NO webhook retry guarantees — a lost delivery
    leaves a real payment ungranted forever. This sweep re-questions the
    provider's authoritative verify-order endpoint for every fam order
    still 'created' and runs the verdict through the SAME guards as
    /famgateway/webhook (see billing_server.recovery). Wire to a daily
    cron (curl POST) or run manually after an outage.
    """
    _require_admin(token, request)
    return recovery.run_reconcile(limit=limit,
                                  older_than_minutes=older_than_minutes)


@app.post("/admin/expiry-nudge")
async def admin_expiry_nudge(request: Request, token: str = "",
                             window_days: int = 3) -> dict:
    """Email one reminder per live Pro window ending within window_days.

    FamGateway cannot auto-renew, so renewal is pay-again (ADR-016): the
    nudge replaces the subscription-renewal email cycle. Markers make it
    idempotent per (email, window end date) — daily cron friendly.
    """
    _require_admin(token, request)
    return recovery.run_expiry_nudges(window_days=window_days)


@app.post("/admin/orders/refund")
async def admin_refund_order(request: Request, token: str = "",
                             order_id: str = "") -> dict:
    """Revoke one order by hand (FamGateway refunds have no webhook).

    A refunded order can never be granted again — entitlement folding and
    key redemption both skip status='refunded'. Idempotent.
    """
    _require_admin(token, request)
    order = store.get_order((order_id or "").strip())
    if not order:
        raise HTTPException(status_code=404, detail="Unknown order")
    if order["status"] == "refunded":
        return {"ok": True, "already_refunded": True, "order_id": order_id}
    updated = store.refund_order_by_id(order_id)
    logger.info("admin refund: order %s (%s)", order_id,
                (updated or {}).get("payment_id") or "?")
    return {"ok": True, "revoked": True, "order_id": order_id}


@app.get("/admin/webhook-failures")
async def admin_webhook_failures(request: Request, token: str = "",
                                 window_hours: int = 24,
                                 limit: int = 50) -> dict:
    """Webhook rollback visibility: underpaid / mismatch / processing rows.

    Every ignored grant is logged only — this is the glance-at endpoint for
    'why did this buyer not get their plan'.
    """
    _require_admin(token, request)
    return {
        "ok": True,
        "kinds": store.event_failure_counts(window_minutes=max(1, window_hours) * 60),
        "entries": store.list_recent_event_failures(limit=limit),
    }


# ── 8) abuse signals + enforcement (P5) ──────────────────────────


def _abuse_thresholds() -> tuple[int, int, int]:
    import os as _os

    def _int(name: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(_os.environ.get(name, str(default)))))
        except ValueError:
            return default

    return (
        _int("FEATURE_ABUSE_HOURS", 24, 1, 24 * 30),
        _int("FEATURE_ABUSE_USER_IDS", 3, 2, 1000),
        _int("FEATURE_ABUSE_IPS", 10, 2, 10000),
    )


@app.get("/admin/abuse")
async def admin_abuse(request: Request, token: str = "") -> dict:
    """Sharing/abuse signals + active blocks (support view, JSON)."""
    _require_admin(token, request)
    hours, min_users, min_ips = _abuse_thresholds()
    return {
        "ok": True,
        "thresholds": {"hours": hours, "min_user_ids": min_users, "min_ips": min_ips},
        "signals": store.abuse_signals(hours, min_users, min_ips),
        "blocks": store.list_blocks(),
    }


@app.post("/admin/abuse/enforce")
async def admin_abuse_enforce(request: Request, token: str = "") -> dict:
    """Block abusive vectors found by the signals scan.

    Wire to a scheduler (cron/Render cron) or run manually after reviewing
    GET /admin/abuse. Blocks affect ticket issuance + relay only — local
    plan resolution is untouched (fail-safe: a bad block never bricks a
    paying install offline, it just forces re-review).
    """
    _require_admin(token, request)
    hours, min_users, min_ips = _abuse_thresholds()
    signals = store.abuse_signals(hours, min_users, min_ips)
    blocked: list = []
    for sig in signals:
        email = str(sig.get("email") or "")
        if email:
            reason = f"abuse scan: {'; '.join(sig.get('reasons', []))}"
            store.block_grant("email", email, reason)
            blocked.append({"kind": "email", "value": email, "reason": reason})
    return {"ok": True, "blocked": blocked, "signals": signals}


@app.post("/admin/abuse/unblock")
async def admin_abuse_unblock(request: Request, token: str = "",
                              kind: str = "email", value: str = "") -> dict:
    """Remove a block after review (support tool)."""
    _require_admin(token, request)
    try:
        removed = store.unblock_grant(kind, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "removed": removed}


@app.get("/healthz")
async def healthz() -> dict:
    fam_ready = bool(cfg.FAMGATEWAY_API_KEY)
    ready = {"payments_provider": cfg.PAYMENTS_PROVIDER,
             "razorpay": bool(cfg.RAZORPAY_KEY_ID and cfg.RAZORPAY_KEY_SECRET),
             "razorpay_webhook": bool(cfg.RAZORPAY_WEBHOOK_SECRET),
             "famgateway": fam_ready,
             "license_key": True, "keyless": bool(cfg.KEYLESS),
             "sync": bool(cfg.ENTITLEMENTS_SYNC_SECRET),
             "email": bool(cfg.RESEND_API_KEY),
             "subscription_plan": bool(cfg.RAZORPAY_PLAN_ID_PRO)}
    try:
        keymint.load_private_key()
    except Exception:  # noqa: BLE001
        ready["license_key"] = False
    if cfg.PAYMENTS_PROVIDER == "famgateway":
        payments_ok = ready["famgateway"]
    else:
        payments_ok = ready["razorpay"] and ready["razorpay_webhook"]
    return {"ok": all([payments_ok, ready["license_key"] or ready["keyless"]]),
            "components": ready}
