"""One-time product-key redemption.

A purchased key may be activated by exactly one account — first redemption
wins:

- **Locally**: ``analytics.key_redemptions`` records the first ``user_id``
  that activated a key hash, so a second account on the same install is
  refused even when it shares the buyer email (or works offline).
- **Globally**: when the billing server is configured, ``POST
  /v1/keys/redeem`` claims the key for the first account and rejects every
  other account. Unknown and refunded keys are refused too. If the server
  cannot be reached the activation fails — single use cannot be verified
  offline. (Entitlements still follow the Google email via cloud sync, so a
  legitimate second machine gets its plan from ``/v1/entitlements`` instead
  of re-activating the key.)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from api.billing import cloud_sync
from api.billing.schema import SCHEMA, get_connection

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0

_TABLE = f"{SCHEMA}.key_redemptions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(con) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            key_hash     VARCHAR,
            user_id      VARCHAR,
            email        VARCHAR,
            activated_at TIMESTAMP
        )
        """
    )


def local_claim(key_hash_val: str) -> Optional[Dict[str, Any]]:
    """Return the local redemption row for a key hash, if one exists."""
    try:
        with get_connection() as con:
            _ensure_table(con)
            row = con.execute(
                f"""SELECT key_hash, user_id, email, activated_at
                    FROM {_TABLE} WHERE key_hash = ? LIMIT 1""",
                [key_hash_val],
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("local redemption lookup failed: %s", exc)
        return None
    if not row:
        return None
    return {
        "key_hash": row[0],
        "user_id": row[1],
        "email": row[2] or "",
        "activated_at": str(row[3]) if row[3] is not None else "",
    }


def record_local_claim(key_hash_val: str, user_id: str, email: str) -> None:
    """Record the owning account for a key hash (idempotent, first write wins)."""
    with get_connection() as con:
        _ensure_table(con)
        row = con.execute(
            f"SELECT user_id FROM {_TABLE} WHERE key_hash = ? LIMIT 1",
            [key_hash_val],
        ).fetchone()
        if row:
            return
        con.execute(
            f"""INSERT INTO {_TABLE} (key_hash, user_id, email, activated_at)
                VALUES (?, ?, ?, ?)""",
            [key_hash_val, user_id, (email or "").lower(), _now_iso()],
        )


def is_remote_configured() -> bool:
    """True when the billing server URL + sync secret are configured."""
    return cloud_sync.is_configured()


def _sign(message: str, secret: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def redeem_remote(key: str, key_hash_val: str, email: str, user_id: str,
                  timeout: float = REQUEST_TIMEOUT) -> Dict[str, str]:
    """Ask the billing server to claim this key for ``user_id``.

    Returns ``{"status": ...}`` with one of: ``redeemed`` / ``already_owner`` /
    ``conflict`` / ``refunded`` / ``unknown`` / ``unreachable``. Never raises.
    """
    base = cloud_sync.billing_sync_base_url()
    secret = cloud_sync.billing_sync_secret()
    if not base or not secret:
        return {"status": "unreachable"}
    email = (email or "").strip().lower()
    exp = int(time.time())
    message = f"{key_hash_val}|{email}|{user_id}|{exp}"
    body = {
        "key": key,
        "key_hash": key_hash_val,
        "email": email,
        "user_id": user_id,
        "exp": str(exp),
        "sig": _sign(message, secret),
    }
    try:
        resp = httpx.post(f"{base}/v1/keys/redeem", json=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.debug("key redemption unreachable: %s", exc)
        return {"status": "unreachable"}

    if resp.status_code == 200:
        try:
            data = resp.json() or {}
        except Exception:  # noqa: BLE001 — non-JSON 200 is not a grant
            return {"status": "unreachable"}
        status = str(data.get("status", ""))
        # Fail closed on unexpected status: only explicit redeemed/owner
        # counts as a grant; anything else is treated as unreachable so the
        # caller fails closed (activation) or fails open (periodic check).
        if status in ("redeemed", "already_owner"):
            return {"status": status}
        logger.warning("key redemption unexpected status %r", status)
        return {"status": "unreachable"}
    if resp.status_code == 409:
        try:
            detail = (resp.json() or {}).get("detail")
        except Exception:  # noqa: BLE001
            detail = None
        status = str(detail.get("status", "")) if isinstance(detail, dict) else ""
        if status == "refunded":
            return {"status": "refunded"}
        return {"status": "conflict"}
    if resp.status_code == 404:
        return {"status": "unknown"}
    logger.warning("key redemption failed with HTTP %s", resp.status_code)
    return {"status": "unreachable"}
