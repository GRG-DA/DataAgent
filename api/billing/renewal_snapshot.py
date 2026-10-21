"""Renewal snapshot — last-known subscription renewal state per email.

Written by cloud_sync on every successful billing-server fetch (keyless
subscriptions, ADR-004); read by GET /subscriptions/me and the /config
entitlements so the subscription page can render renewal state even when the
billing site is unreachable.

Also drives the staleness phone-home: a snapshot older than
SNAPSHOT_TTL_SECONDS, or with a renewal within RENEWAL_SOON_SECONDS, is
refreshed from the billing server on page view. Refreshes are best-effort and
fail-secure — a failed fetch never downgrades or clears the snapshot.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Refresh the snapshot at most once a day on page view.
SNAPSHOT_TTL_SECONDS = 24 * 3600
# Also phone home when a renewal is close (period may have advanced server-side).
RENEWAL_SOON_SECONDS = 24 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(iso: Any) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace(" ", "T"))
    except (ValueError, TypeError):
        return None


def _snapshot_key(email: str) -> str:
    return f"renewal_snapshot:{(email or '').strip().lower()}"


def store_snapshot(email: str, remote: Optional[Dict[str, Any]]) -> None:
    """Persist the last server-reported renewal state for an email (best-effort).

    ``remote`` is a fetch_remote_plan() dict. Presence of auto_renew /
    cancel_at_period_end (not None) marks the response as a live subscription;
    anything else records a non-subscription "last checked" snapshot.
    """
    email = (email or "").strip().lower()
    if not email:
        return
    remote = remote if isinstance(remote, dict) else {}
    is_sub = (remote.get("auto_renew") is not None
              or remote.get("cancel_at_period_end") is not None)
    snap = {
        "is_subscription": bool(is_sub),
        "auto_renew": remote.get("auto_renew") if is_sub else None,
        "cancel_at_period_end": remote.get("cancel_at_period_end") if is_sub else None,
        "renews_at": remote.get("expires_at") if is_sub else None,
        "synced_at": _now().isoformat(),
    }
    try:
        from api.billing.schema import SCHEMA, get_connection

        value = json.dumps(snap)
        with get_connection() as con:
            con.execute(
                f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
                    key VARCHAR PRIMARY KEY, value VARCHAR)"""
            )
            row = con.execute(
                f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_snapshot_key(email)],
            ).fetchone()
            if row:
                con.execute(
                    f"UPDATE {SCHEMA}.premium_settings SET value = ? WHERE key = ?",
                    [value, _snapshot_key(email)],
                )
            else:
                con.execute(
                    f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
                    [_snapshot_key(email), value],
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("renewal snapshot store failed: %s", exc)


def read_snapshot(email: str) -> Optional[Dict[str, Any]]:
    """Return the stored renewal snapshot for an email, or None."""
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_snapshot_key(email)],
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("renewal snapshot read failed: %s", exc)
        return None
    if not row or not row[0]:
        return None
    try:
        snap = json.loads(str(row[0]))
    except (ValueError, TypeError):
        return None
    return snap if isinstance(snap, dict) else None


def is_stale(snapshot: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """True when the snapshot should be refreshed from the billing server."""
    if not snapshot:
        return True
    now = now or _now()
    synced = _parse(snapshot.get("synced_at"))
    if synced is None or now - synced > timedelta(seconds=SNAPSHOT_TTL_SECONDS):
        return True
    renews = _parse(snapshot.get("renews_at"))
    if renews is not None and renews <= now + timedelta(seconds=RENEWAL_SOON_SECONDS):
        return True
    return False


def refresh_if_stale(user_id: str, email: str, sso_subject: str = "") -> Optional[Dict[str, Any]]:
    """Phone home when the snapshot is stale; return the (possibly updated) one.

    Fail-secure: unconfigured billing server, network errors, or refused
    downgrades leave the previous snapshot untouched.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        from api.billing import cloud_sync

        if not cloud_sync.is_configured():
            return read_snapshot(email)
        snap = read_snapshot(email)
        if not is_stale(snap):
            return snap
        cloud_sync.sync_user(user_id, email, sso_subject)
    except Exception as exc:  # noqa: BLE001
        logger.debug("renewal phone-home failed: %s", exc)
    return read_snapshot(email)


def subscription_renewal_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a user_id to its email and return the subscription snapshot.

    Returns the snapshot only when the server last reported a live
    subscription; None for key / lifetime / admin / free accounts or when no
    snapshot exists.
    """
    try:
        from api.auth import user_manager

        info = user_manager.get_user_info_by_id(user_id) or {}
        email = str(info.get("email") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return None
    if not email:
        return None
    snap = read_snapshot(email)
    if snap and snap.get("is_subscription"):
        return snap
    return None
