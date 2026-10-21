"""
Premium license management — ported from FreeLLMAPI server/src/routes/premium.ts.

Validates license keys against an external license server (configurable via
PREMIUM_LICENSE_SERVER_URL). Keys and status are stored in a DuckDB settings
table so they survive restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import duckdb
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import settings
from api.dependencies import get_current_user
from api.middleware.auth_middleware import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/premium", tags=["premium"])

# ── Settings table helpers ────────────────────────────────────────────

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.premium_settings (
    key   VARCHAR,
    value VARCHAR
)
"""

_KEY = "license_key"
_STATUS = "license_status"


def _conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(settings.duckdb.database_path),
        read_only=False,
    )


def _ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_TABLE_SQL.format(schema=settings.duckdb.schema))


def _get_setting(con: duckdb.DuckDBPyConnection, key: str) -> Optional[str]:
    try:
        row = con.execute(
            f"SELECT value FROM {settings.duckdb.schema}.premium_settings WHERE key = ?",
            [key],
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _set_setting(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    # NOTE: INSERT OR REPLACE fails on this table: boot-time migration
    # (api.db.strip_constraint_indexes) removes the PK from analytics tables,
    # so ON CONFLICT has no index to bind to (BinderException on this DuckDB
    # build). DELETE+INSERT keeps one row per key without upsert semantics.
    schema = settings.duckdb.schema
    con.execute(
        f"DELETE FROM {schema}.premium_settings WHERE key = ?",
        [key],
    )
    con.execute(
        f"INSERT INTO {schema}.premium_settings (key, value) VALUES (?, ?)",
        [key, value],
    )


def _delete_setting(con: duckdb.DuckDBPyConnection, key: str) -> None:
    try:
        con.execute(
            f"DELETE FROM {settings.duckdb.schema}.premium_settings WHERE key = ?",
            [key],
        )
    except Exception:
        pass


# ── License server helpers ────────────────────────────────────────────

def _license_server_url() -> str:
    url = getattr(settings, "premium", None)
    if url and hasattr(url, "license_server_url"):
        return url.license_server_url.rstrip("/")
    return ""


def _site_url() -> str:
    cfg = getattr(settings, "premium", None)
    if cfg and hasattr(cfg, "site_url"):
        return cfg.site_url
    return ""


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "…" + key[-4:]


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _account_email(user: User) -> str:
    """Email identity for the signed-in account (user store is authoritative)."""
    from api.auth import user_manager

    info = user_manager.get_user_info_by_id(user.user_id) or {}
    return str(info.get("email") or user.email or "").strip().lower()


# ── Pydantic models ──────────────────────────────────────────────────

class KeyActivateRequest(BaseModel):
    key: str


class PortalRequest(BaseModel):
    email: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────

@router.get("")
async def get_premium_status():
    """Return current premium/license status for the UI."""
    con = _conn()
    try:
        _ensure_table(con)
        raw_key = _get_setting(con, _KEY)
        raw_status = _get_setting(con, _STATUS)
    finally:
        con.close()

    license_obj = None
    if raw_status:
        try:
            license_obj = json.loads(raw_status)
        except Exception:
            pass

    has_key = raw_key is not None and len(raw_key) > 0
    masked = _mask_key(raw_key) if has_key else None

    return {
        "hasKey": has_key,
        "maskedKey": masked,
        "license": license_obj,
        "siteUrl": _site_url(),
    }


@router.post("/key")
async def activate_license_key(req: KeyActivateRequest):
    """Validate a license key against the external license server and store it."""
    server_url = _license_server_url()
    if not server_url:
        raise HTTPException(
            status_code=503,
            detail="License server URL not configured (PREMIUM_LICENSE_SERVER_URL)",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server_url}/v1/license/activate",
                json={"key": req.key, "product": "dataagent"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"License server error: {exc.response.text}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cannot reach license server: {exc}")

    if not data.get("valid", False):
        raise HTTPException(status_code=400, detail="Invalid license key")

    # Store key + status
    license_obj = {
        "valid": True,
        "plan": data.get("plan"),
        "status": data.get("status", "active"),
        "expiresAt": data.get("expiresAt"),
        "cancelAtPeriodEnd": data.get("cancelAtPeriodEnd", False),
        "keyHash": _hash_key(req.key),
    }
    con = _conn()
    try:
        _ensure_table(con)
        _set_setting(con, _KEY, req.key)
        _set_setting(con, _STATUS, json.dumps(license_obj))
    finally:
        con.close()

    return {"ok": True, "license": license_obj}


@router.post("/activate-key")
async def activate_product_key(req: KeyActivateRequest, user: User = Depends(get_current_user)):
    """Activate a product key generated by the Key Manager (Ed25519, offline).

    Keys are account-bound: the signed payload carries the buyer's email, and
    the key only activates on that account. This stops one paid key from being
    shared across accounts. Legacy keys without a buyer binding are refused.

    Verifies the signature locally using the baked-in public key, then writes
    the plan to DuckDB so the subscription page picks it up immediately.
    """
    from keygen_app.keygen import bound_email, validate_key
    from keygen_app.config import load_public_key
    from keygen_app.storage import activate as kg_activate

    try:
        public_key = load_public_key()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"Public key not loaded: {exc}")

    try:
        payload = validate_key(req.key, public_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid product key: {exc}")

    buyer_email = bound_email(payload)
    if not buyer_email:
        raise HTTPException(
            status_code=400,
            detail=(
                "This key is not linked to a buyer account, so it cannot be "
                "activated. Use the key issued with your purchase, or contact "
                "support."
            ),
        )
    account_email = _account_email(user)
    if not account_email:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This key is registered to {buyer_email}. Sign in with that "
                "email account (Continue with Google) to activate it."
            ),
        )
    if account_email != buyer_email:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This key is registered to {buyer_email}, but you are signed "
                f"in as {account_email}. Product keys only work on the account "
                "that purchased them."
            ),
        )

    # One-time use: the first account to claim the key owns it. The local
    # ledger blocks a second account on this device; the billing server
    # registry (when configured) blocks every other account globally and
    # refuses unknown/refunded keys. Never verified → never activated.
    from api.billing import redemption

    key_hash_val = _hash_key(req.key)
    claim = redemption.local_claim(key_hash_val)
    if claim and str(claim.get("user_id")) != str(user.user_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "This key was already activated on another account on this "
                "device. Each product key works on one account only."
            ),
        )

    if redemption.is_remote_configured():
        remote = redemption.redeem_remote(
            req.key, key_hash_val, account_email, user.user_id
        )
        status = remote.get("status")
        if status == "conflict":
            raise HTTPException(
                status_code=403,
                detail=(
                    "This key was already activated on another account. Each "
                    "product key works on one account only."
                ),
            )
        if status == "refunded":
            raise HTTPException(
                status_code=403,
                detail="This key was refunded and can no longer be activated.",
            )
        if status == "unknown":
            raise HTTPException(
                status_code=404,
                detail=(
                    "This key is not registered with the billing server. "
                    "Contact support."
                ),
            )
        if status not in ("redeemed", "already_owner"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not verify this key online. Connect to the internet "
                    "and try again — activation is one-time and must be verified."
                ),
            )

    try:
        redemption.record_local_claim(key_hash_val, user.user_id, account_email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record key redemption locally: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Activation failed: could not record the key on this device.",
        )

    plan = payload.get("plan", "pro")
    expires_at = payload.get("expiresAt")

    try:
        # Pass the warehouse the app actually uses. Without this the Key
        # Manager falls back to a path derived from its own module location,
        # which in the frozen desktop build points into the (unwritable)
        # PyInstaller bundle instead of the runtime warehouse.
        result = kg_activate(
            user_id=user.user_id,
            plan=plan,
            license_key=req.key,
            expires_at=expires_at,
            db_path=Path(settings.duckdb.database_path),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Activation failed: {exc}")

    return {
        "ok": True,
        "plan": plan,
        "expiresAt": expires_at,
        "license": result.get("license", {}),
    }


@router.delete("/key")
async def remove_license_key():
    """Remove the stored license key."""
    con = _conn()
    try:
        _ensure_table(con)
        _delete_setting(con, _KEY)
        _delete_setting(con, _STATUS)
    finally:
        con.close()
    return {"ok": True}


@router.post("/sync")
async def sync_license():
    """Re-validate the stored license key against the external server."""
    con = _conn()
    try:
        _ensure_table(con)
        raw_key = _get_setting(con, _KEY)
    finally:
        con.close()

    if not raw_key:
        raise HTTPException(status_code=400, detail="No license key stored")

    server_url = _license_server_url()
    if not server_url:
        raise HTTPException(status_code=503, detail="License server not configured")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server_url}/v1/license/activate",
                json={"key": raw_key, "product": "dataagent"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}")

    license_obj = {
        "valid": data.get("valid", False),
        "plan": data.get("plan"),
        "status": data.get("status", "active"),
        "expiresAt": data.get("expiresAt"),
        "cancelAtPeriodEnd": data.get("cancelAtPeriodEnd", False),
        "keyHash": _hash_key(raw_key),
    }

    con = _conn()
    try:
        _ensure_table(con)
        _set_setting(con, _STATUS, json.dumps(license_obj))
    finally:
        con.close()

    return {"ok": True, "license": license_obj}


@router.post("/portal")
async def open_billing_portal(req: PortalRequest):
    """Open the billing portal via the external license server."""
    con = _conn()
    try:
        _ensure_table(con)
        raw_key = _get_setting(con, _KEY)
    finally:
        con.close()

    if not raw_key:
        raise HTTPException(status_code=400, detail="No license key stored")

    server_url = _license_server_url()
    if not server_url:
        raise HTTPException(status_code=503, detail="License server not configured")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server_url}/v1/portal",
                json={"key": raw_key, "email": req.email, "product": "dataagent"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Portal request failed: {exc}")

    return {"url": data.get("url", "")}
