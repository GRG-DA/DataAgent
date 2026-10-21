"""Server-signed entitlement tokens (P2, app side — verify only).

The billing server mints short-lived Ed25519 plan assertions
(``billing_server.keymint.mint_entitlement_token``) with the license signing
key. The app verifies them with the baked-in public key, so a cloud_sync
grant cannot be forged by editing ``warehouse.duckdb`` — the attacker would
need the server's private key.

Token format: ``base64url(canonical_payload_json).base64url(signature)``.
Payload: ``{product: "dataagent-ent", ver: 1, email, plan, iat, exp,
grant_expires_at}``. The private key never appears here.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional

PRODUCT = "dataagent-ent"
VERSION = 1
PLANS = ("pro", "lifetime")


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * ((4 - len(part) % 4) % 4))


def verify_token(token: str, expected_email: str = "") -> dict[str, Any]:
    """Verify signature + product/version/plan/expiry (+ email binding).

    Raises ValueError on any failure. Returns the decoded payload dict.
    """
    from cryptography.exceptions import InvalidSignature

    from keygen_app.config import load_public_key

    if not token or "." not in token:
        raise ValueError("malformed entitlement token")
    raw_b64, sig_b64 = token.rsplit(".", 1)
    try:
        raw = _b64url_decode(raw_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("malformed entitlement token encoding") from exc
    try:
        public_key = load_public_key()
    except Exception as exc:  # noqa: BLE001 — tampered/missing pubkey
        raise ValueError(f"untrusted public key: {exc}") from exc
    try:
        public_key.verify(sig, raw)
    except InvalidSignature as exc:
        raise ValueError("invalid entitlement token signature") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed entitlement token payload") from exc
    if payload.get("product") != PRODUCT:
        raise ValueError("not an entitlement token")
    if payload.get("ver") != VERSION:
        raise ValueError("unsupported entitlement token version")
    plan = str(payload.get("plan", "")).lower()
    if plan not in PLANS:
        raise ValueError("unknown plan in entitlement token")
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise ValueError("entitlement token has no email")
    if expected_email and email != expected_email.strip().lower():
        raise ValueError("entitlement token email mismatch")
    exp = payload.get("exp")
    if not exp:
        raise ValueError("entitlement token has no expiry")
    try:
        exp_dt = datetime.fromisoformat(str(exp))
    except ValueError as exc:
        raise ValueError("malformed entitlement token expiry") from exc
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if exp_dt <= datetime.now(timezone.utc):
        raise ValueError("entitlement token expired")
    return payload


# ── per-email token store (premium_settings) ──────────────────────


def _token_key(email: str) -> str:
    return f"entitlement_token:{(email or '').strip().lower()}"


def store_token(email: str, token: str) -> None:
    """Persist the latest server token for an email (best-effort)."""
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            con.execute(
                f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
                    key VARCHAR PRIMARY KEY, value VARCHAR)"""
            )
            row = con.execute(
                f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_token_key(email)],
            ).fetchone()
            if row:
                con.execute(
                    f"UPDATE {SCHEMA}.premium_settings SET value = ? WHERE key = ?",
                    [token, _token_key(email)],
                )
            else:
                con.execute(
                    f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
                    [_token_key(email), token],
                )
    except Exception:  # noqa: BLE001
        pass


def get_stored_token(email: str) -> Optional[str]:
    """Return the stored server token for an email, if any."""
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_token_key(email)],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row[0]:
        return None
    return str(row[0])


def clear_token(email: str) -> None:
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            con.execute(
                f"DELETE FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_token_key(email)],
            )
    except Exception:  # noqa: BLE001
        pass
