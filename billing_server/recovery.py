"""FamGateway payment recovery & lifecycle ops (ADR-016).

Owns the shared, identical grant path for:
  - ``POST /famgateway/webhook``  (the live surface; thin handler in main.py)
  - ``POST /admin/reconcile``     (missed-webhook sweep out of verify_order)
  - ``POST /admin/expiry-nudge``  (D-n email reminder before a window ends)

Why the sweep exists: FamGateway documents NO webhook retry guarantees. A
single lost delivery would otherwise leave a real payment ungranted forever —
the sweep re-asks the provider's authoritative ``verify-order`` endpoint for
every fam order still ``created`` and runs it through the SAME guards as the
webhook (event/status allowlist, idempotency markers, underpay check, UTR
ownership, verdict cross-check). Nothing here trusts more than the webhook
does; every fallback rolls the idempotency marker back like the webhook does.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

import billing_server.config as cfg
from billing_server import emailer, famgateway_client, store

logger = logging.getLogger("billing.recovery")

_FAM_EVENTS = frozenset({"payment.success", "payment.verified"})
_FAM_SUCCESS_STATUSES = frozenset({"success", "completed"})


# ── shared event decision (webhook body or sweep verdict) ────────────


def process_fam_event(event: dict, confirmed: Optional[dict] = None) -> dict:
    """One signed event → at most one grant, or an ``ignored`` reason.

    ``confirmed`` lets the reconcile sweep pass the verify verdict it already
    fetched (avoids a second API call); the webhook omits it and the
    defense-in-depth lookup happens here.

    Raises HTTPException(500) on internal failure — after rolling the
    idempotency marker back, exactly like the webhook contract.
    """
    event_kind = str(event.get("event") or "").lower()
    status = str(event.get("status") or "").lower()
    if event_kind not in _FAM_EVENTS:
        return {"ok": True, "ignored": event_kind or "unknown"}
    if status not in _FAM_SUCCESS_STATUSES:
        return {"ok": True, "ignored": f"status:{status or 'missing'}"}

    order_id = str(event.get("order_id") or "").strip()
    if not order_id:
        return {"ok": True, "ignored": "no order id"}

    utr = str(event.get("utr") or "").strip()
    transaction_id = str(event.get("transaction_id") or "").strip()
    if not utr and not transaction_id:
        # A success event with no bank reference is unverifiable: nobody can
        # prove which payment it belongs to, so it must never grant. Checked
        # BEFORE the idempotency marker so a later UTR-bearing redelivery
        # still gets processed.
        return {"ok": True, "ignored": "no payment reference"}
    payment_id = utr or transaction_id
    ref = f"{order_id}:{payment_id}"
    if store.seen_event("fam.payment.success", ref):
        return {"ok": True, "duplicate": True}

    def _ignore_and_rollback(reason: str) -> dict:
        # Roll the idempotency marker back so a later corrected event is not
        # swallowed as a duplicate for something we refused.
        store.record_event_failure("fam.payment.success", ref, reason)
        return {"ok": True, "ignored": reason}

    try:
        order = store.get_order(order_id)
        if not order or str(order.get("provider") or "") != "famgateway":
            return _ignore_and_rollback("unmatched order")

        # Underpaid: FamGateway amounts arrive in RUPEES; our rows store paise.
        try:
            paid_paise = round(float(event.get("amount") or 0) * 100)
        except (TypeError, ValueError, OverflowError):
            # 1e400 amounts parse to inf and explode round() — never let a
            # weird body 500 the endpoint; it just compares to nothing.
            paid_paise = 0
        try:
            recorded = int(order.get("amount") or 0)
        except (TypeError, ValueError):
            recorded = 0
        if recorded and paid_paise and paid_paise < recorded:
            logger.warning("underpaid famgateway order ignored: %s < %s (%s)",
                           paid_paise, recorded, order_id)
            return _ignore_and_rollback("underpaid")

        # UTR replay: another order already paid this exact bank reference.
        # (Terminal: the marker stays, so the replay keeps being swallowed.)
        owner = store.resolve_utr_owner(payment_id)
        if owner and owner != order_id:
            logger.warning("UTR replay blocked: %s already paid %s", payment_id, owner)
            return {"ok": True, "duplicate": True}

        # Defense-in-depth confirm (never the trust boundary — the HMAC is):
        # a mismatching verdict aborts; an unreachable verify endpoint falls
        # back to the signed body so an outage cannot swallow real payments.
        if confirmed is None:
            try:
                confirmed = famgateway_client.verify_order(order_id)
            except famgateway_client.FamGatewayError as exc:
                logger.warning("famgateway verify endpoint unreachable for %s: %s",
                               order_id, exc)
                confirmed = None
        if confirmed is not None:
            confirmed_status = str(confirmed.get("status") or "").lower()
            confirmed_utr = str(confirmed.get("utr") or "").strip()
            # The verdict must be for THIS order, not a neighbouring one.
            # FAMGATEWAY_STRICT_VERDICT=1 additionally refuses verdicts that
            # carry no order_id at all (ADR-016: the pre-sweep leniency; flip
            # it once the live verify-order responses are confirmed to carry
            # order_id — the sweep then still recovers orders, so a strict
            # refusal can no longer lose a payment).
            confirmed_order = str(confirmed.get("order_id") or "").strip()
            if not confirmed_order:
                if cfg.FAMGATEWAY_STRICT_VERDICT:
                    logger.info("famgateway verify verdict for %s omitted order_id "
                                "(strict mode)", order_id)
                    return _ignore_and_rollback("verify missing order_id")
                # Legacy FamGateway schemas omit it and verify_order() was
                # already keyed to order_id — documented ADR-016 leniency.
            elif confirmed_order != order_id:
                logger.warning("famgateway verify returned order %s for %s",
                               confirmed_order, order_id)
                return _ignore_and_rollback("verify mismatch")
            if confirmed_status and confirmed_status not in _FAM_SUCCESS_STATUSES:
                logger.warning("famgateway verify contradicts webhook for %s "
                               "(%s != payment.success)", order_id, confirmed_status)
                return _ignore_and_rollback("verify mismatch")
            # The verdict is authoritative on AMOUNT too: it carries rupees,
            # our rows store paise. A verdict below the order's price is a
            # partial payment, whatever the signed event claimed.
            confirmed_amount = confirmed.get("amount")
            if confirmed_amount is not None:
                try:
                    verdict_paise = round(float(confirmed_amount) * 100)
                except (TypeError, ValueError, OverflowError):
                    verdict_paise = 0
                if verdict_paise and verdict_paise < int(order.get("amount") or 0):
                    logger.warning("famgateway verify underpaid %s: %s < %s",
                                   order_id, verdict_paise, int(order.get("amount") or 0))
                    return _ignore_and_rollback("underpaid")
            if confirmed_utr and confirmed_utr != utr and utr:
                return _ignore_and_rollback("verify mismatch")
            if confirmed_utr and not utr:
                utr = confirmed_utr
                payment_id = utr

        if order["status"] != "paid":
            updated = store.mark_paid(order_id, payment_id,
                                      grant_days=None)  # pre-recorded at create-order
            _send_grant_email(updated or store.get_order(order_id) or order)
        return {"ok": True, "plan": order["plan"]}
    except Exception as exc:  # noqa: BLE001
        # Roll the idempotency marker back so FamGateway's retry reprocesses.
        store.record_event_failure("fam.payment.success", ref, str(exc))
        logger.exception("famgateway webhook processing failed for %s", ref)
        raise HTTPException(status_code=500, detail="Processing failed") from exc


def _send_grant_email(order: Optional[dict]) -> None:
    """Best-effort purchase confirmation with the window's end date.

    Never raises and never blocks the grant: the success page + the
    entitlement API are the source of truth if the email is lost. Only
    called on the newly-paid path, so redeliveries do not re-email.
    """
    if not order:
        return
    plan = str(order.get("plan") or "")
    if plan not in ("pro", "lifetime"):
        return
    expires_at = ""
    try:
        days = int(order.get("grant_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if plan == "pro" and days > 0:
        granted_at = str(order.get("updated_at") or "")
        try:
            end = (datetime.fromisoformat(granted_at) + timedelta(days=days))
            if end.tzinfo is not None:
                end = end.astimezone(timezone.utc)
            expires_at = end.date().isoformat()
        except (ValueError, TypeError):
            expires_at = ""
    try:
        emailer.send_grant_email(str(order.get("email") or ""), plan, expires_at)
    except Exception as exc:  # noqa: BLE001 — email is never part of the grant
        logger.warning("famgateway grant email to %s failed: %s",
                       order.get("email"), exc)


# ── missed-webhook reconcile sweep ────────────────────────────────────


def settle_fam_order(order: dict) -> dict:
    """Reconcile one created fam order against the provider's verdict.

    The verdict becomes a synthetic `payment.verified` event and goes through
    ``process_fam_event`` — every webhook guard applies unchanged, which also
    means the sweep writes the SAME idempotency markers, so a later webhook
    redelivery of a sweep-granted payment is a plain duplicate.
    """
    order_id = str(order.get("order_id") or "")
    try:
        verdict = famgateway_client.verify_order(order_id)
    except famgateway_client.FamGatewayError as exc:
        logger.info("reconcile: verify unavailable for %s: %s", order_id, exc)
        return {"order_id": order_id, "state": "verify_unreachable"}
    status = str(verdict.get("status") or "").lower()
    if status not in _FAM_SUCCESS_STATUSES:
        return {"order_id": order_id,
                "state": f"unconfirmed:{status or 'unknown'}"}
    event = {
        "event": "payment.verified", "status": "success",
        "order_id": order_id,
        "amount": verdict.get("amount"),
        "utr": str(verdict.get("utr") or "").strip(),
        "transaction_id": str(verdict.get("transaction_id") or "").strip(),
    }
    payload = process_fam_event(event, confirmed=verdict)
    if payload.get("plan"):
        return {"order_id": order_id, "state": "granted",
                "plan": payload["plan"]}
    if payload.get("duplicate"):
        return {"order_id": order_id, "state": "duplicate"}
    return {"order_id": order_id,
            "state": f"ignored:{payload.get('ignored') or 'unknown'}"}


def run_reconcile(limit: int = 50, older_than_minutes: int = 5) -> dict:
    """Sweep the pending fam orders (operator/cron entry point)."""
    rows = store.list_unsettled_fam_orders(
        older_than_minutes=older_than_minutes, limit=limit)
    results = [settle_fam_order(r) for r in rows]
    granted = sum(1 for r in results if r["state"] == "granted")
    logger.info("famgateway reconcile: %d scanned, %d granted", len(rows), granted)
    return {"ok": True, "scanned": len(rows), "granted": granted, "results": results}


# ── expiry nudges (ADR-016 "email nudge" follow-up) ───────────────────


def run_expiry_nudges(window_days: int = 3) -> dict:
    """Email reminders for Pro windows ending within ``window_days``.

    FamGateway has no recurring billing, so renewal is pay-again — the nudge
    is the only reminder that is not the expiry itself. Each (email, window
    end date) pair is nudged at most once: the marker is written only after
    a send actually succeeded, so an unconfigured/broken mailer retries on
    the next run instead of going silent forever.
    """
    window_days = max(0, min(int(window_days or 3), 30))
    now = datetime.now(timezone.utc)
    out = []
    for row in store.fam_grant_expiries():
        expires_at = row["expires_at"]
        days_left = (expires_at - now).days
        if not (0 <= days_left <= window_days):
            continue
        email = row["email"]
        expires_on = expires_at.date().isoformat()
        info = {"email": email, "expires_on": expires_on, "days": days_left}
        ref = f"{email}:{expires_on}"
        if store.event_seen("fam.expiry_nudge", ref):
            out.append({**info, "state": "skipped"})
            continue
        try:
            sent = emailer.send_expiry_nudge_email(email, expires_on)
        except Exception as exc:  # noqa: BLE001
            logger.warning("expiry nudge to %s failed: %s", email, exc)
            sent = False
        if sent:
            store.mark_event("fam.expiry_nudge", ref)
            out.append({**info, "state": "sent"})
        else:
            out.append({**info, "state": "email_unavailable"})
    sent_count = sum(1 for r in out if r["state"] == "sent")
    return {"ok": True, "window_days": window_days, "nudged": sent_count,
            "results": out}
