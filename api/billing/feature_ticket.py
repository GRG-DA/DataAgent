"""Feature tickets (P3, app side) — bind paid actions to server proof.

Local plan flags can be patched out of the binary, but only the billing
server can mint tickets: per-request authorization for the flagship
network feature (``ip_rotation`` proxy pool) plus a usage trail.

Decision matrix in :func:`access_decision` (fail-open/closed chosen per case):

- free plan → ``deny`` (existing 403 behavior).
- no server configured → ``direct`` (offline product promise preserved).
- fresh ticket now → ``relay`` (server-authorized + audited).
- transport error + currently-valid local proof → ``direct`` (offline grace;
  P1/P2 validity already enforced at plan resolution).
- server verdict denies (refunded/conflict) → ``deny`` (authoritative).
- server 404 unknown + locally-valid key signature → ``direct`` (manual /
  admin keys the server never registered; crypto still holds).
- anything else without proof → ``deny``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

TICKET_PRODUCT = "dataagent-ticket"
TICKET_VERSION = 1


class TicketDenied(Exception):
    """The server refused a ticket (revoked/refunded/plan mismatch)."""


class TicketUnknown(Exception):
    """The server never issued this grant (manual/admin key or typo)."""


def verify_ticket(ticket: str, email: str = "", feature: str = "") -> dict[str, Any]:
    """Verify a cached/issued feature ticket with the baked public key."""
    import base64
    import json
    from datetime import datetime, timezone

    from keygen_app.config import load_public_key

    if not ticket or "." not in ticket:
        raise ValueError("malformed feature ticket")
    raw_b64, sig_b64 = ticket.rsplit(".", 1)
    pad = lambda s: s + "=" * ((4 - len(s) % 4) % 4)  # noqa: E731
    try:
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception as exc:
        raise ValueError("malformed feature ticket encoding") from exc
    try:
        public_key = load_public_key()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"untrusted public key: {exc}") from exc
    from cryptography.exceptions import InvalidSignature

    try:
        public_key.verify(sig, raw)
    except InvalidSignature as exc:
        raise ValueError("invalid feature ticket signature") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed feature ticket payload") from exc
    if payload.get("product") != TICKET_PRODUCT:
        raise ValueError("not a feature ticket")
    if payload.get("ver") != TICKET_VERSION:
        raise ValueError("unsupported feature ticket version")
    if email and str(payload.get("email") or "").lower() != email.strip().lower():
        raise ValueError("feature ticket email mismatch")
    if feature and payload.get("feature") != feature:
        raise ValueError("feature ticket is for another feature")
    try:
        exp_dt = datetime.fromisoformat(str(payload.get("exp") or ""))
    except ValueError as exc:
        raise ValueError("malformed feature ticket expiry") from exc
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    from datetime import datetime as _dt
    if exp_dt <= _dt.now(timezone.utc):
        raise ValueError("feature ticket expired")
    return payload


# ── per-email+feature ticket cache (premium_settings) ─────────────


def _cache_key(email: str, feature: str) -> str:
    return f"feature_ticket:{email.strip().lower()}:{feature}"


def get_cached_ticket(email: str, feature: str) -> Optional[str]:
    """Newest cached ticket, or None (never raises)."""
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_cache_key(email, feature)],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return str(row[0]) if row and row[0] else None


def store_ticket(email: str, feature: str, ticket: str) -> None:
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            con.execute(
                f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
                    key VARCHAR PRIMARY KEY, value VARCHAR)"""
            )
            row = con.execute(
                f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_cache_key(email, feature)],
            ).fetchone()
            if row:
                con.execute(
                    f"UPDATE {SCHEMA}.premium_settings SET value = ? WHERE key = ?",
                    [ticket, _cache_key(email, feature)],
                )
            else:
                con.execute(
                    f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
                    [_cache_key(email, feature), ticket],
                )
    except Exception:  # noqa: BLE001
        pass


def _account_email(user_id: str) -> str:
    try:
        from api.auth import user_manager

        info = user_manager.get_user_info_by_id(user_id) or {}
        return str(info.get("email") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _local_proof_ok(user_id: str, email: str) -> bool:
    """Crypto-only check: valid key or token bound to this account right now.

    Read-only (never lapses rows) — plan resolution owns enforcement; this
    only answers "does fresh cryptographic proof exist".
    """
    try:
        from api.billing.entitlement_token import get_stored_token, verify_token as verify_ent
        from api.billing.license_guard import verify_stored_license
        from keygen_app.keygen import bound_email
    except Exception:  # noqa: BLE001
        return False
    token = get_stored_token(email)
    if token:
        try:
            verify_ent(token, email)
            return True
        except ValueError:
            pass
    try:
        payload, _reason = verify_stored_license()
    except Exception:  # noqa: BLE001
        return False
    if not payload or bound_email(payload) != email:
        return False
    return True


def has_local_proof(user_id: str, email: str = "") -> bool:
    """Public wrapper: fresh cryptographic proof for this account right now."""
    return _local_proof_ok(user_id, email or _account_email(user_id))


def request_ticket(email: str, feature: str, user_id: str, timeout: float = 10.0
                   ) -> tuple[str, Optional[str]]:
    """Ask the billing server for a ticket.

    Returns (outcome, ticket|reason) with outcome one of: ``ticket``,
    ``deny`` (server verdict — authoritative), ``offline`` (transport error),
    ``unknown`` (server never issued this grant), ``local`` (no server-
    provable grant: trial/admin/legacy — direct path stays as-is).
    """
    import time as _time

    import httpx

    from api.billing import cloud_sync
    from api.billing.entitlement_token import get_stored_token

    from keygen_app.keygen import key_hash

    base = cloud_sync.billing_sync_base_url()
    secret = cloud_sync.billing_sync_secret()
    if not base or not secret:
        return "local", None
    email = (email or "").strip().lower()
    body: dict[str, Any] = {"mode": "key", "email": email, "feature": feature}
    token = get_stored_token(email)
    if token:
        body = {"mode": "token", "email": email, "feature": feature, "token": token}
    else:
        try:
            from api.billing.license_guard import get_stored_license_key
        except Exception:  # noqa: BLE001
            return "local", None
        key = get_stored_license_key()
        if not key:
            return "local", None  # trial/admin/legacy grant — nothing to prove
        import hashlib as _hashlib
        import hmac as _hmac

        exp = str(int(_time.time()))
        body.update({
            "key": key,
            "key_hash": key_hash(key),
            "user_id": user_id,
            "exp": exp,
            "sig": _hmac.new(
                secret.encode(),
                f"ticket|{key_hash(key)}|{email}|{user_id}|{exp}".encode(),
                _hashlib.sha256,
            ).hexdigest(),
        })
    try:
        resp = httpx.post(f"{base}/v1/feature-ticket", json=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — offline
        logger.debug("feature ticket unreachable: %s", exc)
        return "offline", None
    if resp.status_code == 200:
        try:
            ticket = (resp.json() or {}).get("ticket", "")
        except Exception:  # noqa: BLE001
            return "deny", "bad ticket response"
        if not ticket:
            return "deny", "empty ticket"
        try:
            verify_ticket(ticket, email, feature)
        except ValueError as exc:
            logger.debug("feature ticket failed verify: %s", exc)
            return "deny", str(exc)
        store_ticket(email, feature, ticket)
        return "ticket", ticket
    if resp.status_code == 404:
        return "unknown", None
    if resp.status_code in (401, 402, 403):
        try:
            reason = str((resp.json() or {}).get("detail") or resp.text)[:200]
        except Exception:  # noqa: BLE001
            reason = f"HTTP {resp.status_code}"
        return "deny", reason
    logger.debug("feature ticket HTTP %s", resp.status_code)
    return "offline", None


def access_decision(user_id: str, feature: str) -> tuple[str, Optional[str]]:
    """Decide how a paid action may proceed: relay / direct / deny.

    Returns (``"relay"``, ticket) | (``"direct"``, None) | (``"deny"``, reason).
    Free plans always deny; unconfigured servers stay direct (offline product).
    """
    from api.billing import cloud_sync
    from api.billing.entitlements import get_plan_for_user

    try:
        plan = get_plan_for_user(user_id)
    except Exception:  # noqa: BLE001
        plan = "free"
    if plan == "free":
        return "deny", "Upgrade required: paid plan needed"
    email = _account_email(user_id)
    if not email:
        return "deny", "Paid features need a signed-in account with an email"
    if not cloud_sync.is_configured():
        return "direct", None
    # Cached ticket still valid → relay without a round trip.
    cached = get_cached_ticket(email, feature)
    if cached:
        try:
            verify_ticket(cached, email, feature)
            return "relay", cached
        except ValueError:
            pass
    outcome, ticket = request_ticket(email, feature, user_id)
    if outcome == "ticket" and ticket:
        return "relay", ticket
    if outcome == "deny":
        return "deny", ticket or "Server refused this action"
    if outcome == "unknown":
        # Manual/admin keys the server never registered: crypto still holds.
        if _local_proof_ok(user_id, email):
            return "direct", None
        return "deny", "Unknown purchase — contact support"
    if outcome == "local":
        # Trial/admin/legacy grants have no server-provable proof by design —
        # plan resolution already verified them. Unchanged from today.
        return "direct", None
    # offline: direct iff fresh cryptographic proof exists now.
    if _local_proof_ok(user_id, email):
        return "direct", None
    return "deny", "Could not verify purchase — connect to the internet and retry"
