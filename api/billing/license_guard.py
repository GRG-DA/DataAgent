"""License guard — re-verify stored product keys on every entitlement check.

PyInstaller exes are trivially unpackable, so a one-time check at
activation is not enough: an attacker can edit ``warehouse.duckdb``
(premium_settings / subscriptions / user_subscriptions) to ``plan=pro``
without ever owning a key. This module closes that hole:

- ``verify_stored_license()`` re-runs Ed25519 signature + expiry checks on
  the stored ``premium_settings.license_key`` using the baked-in public key.
- ``verified_keygen_plan()`` is called by entitlement resolution: paid plans
  sourced from ``payment_provider='keygen'`` rows are only honoured while
  the stored key is valid **and** bound to the requesting account's email.
  Anything else fails closed to ``free`` and lapses the tampered rows.
- Non-keygen grants (trial / razorpay / admin) are untouched.
- Remote revocation (refund / conflict / unknown) is re-checked at most once
  per 24h when the billing server is configured.
- Offline grace (P1): locally-valid keys work offline for LICENSE_GRACE_DAYS
  (default 14) after the last successful server check; beyond that the app
  requires one online validation or drops to free. Unconfigured server means
  no grace enforcement (offline-only deployments keep working).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REMOTE_CHECK_KEY = "license_last_remote_check"
_REMOTE_CHECK_INTERVAL = timedelta(hours=24)

_DEFAULT_GRACE_DAYS = 14


def grace_days() -> int:
    """Offline grace in days (env LICENSE_GRACE_DAYS, default 14, 1-90)."""
    try:
        days = int(os.environ.get("LICENSE_GRACE_DAYS", str(_DEFAULT_GRACE_DAYS)))
    except ValueError:
        return _DEFAULT_GRACE_DAYS
    return max(1, min(90, days))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_stored_license_key() -> Optional[str]:
    """Return the stored product key from premium_settings, if any."""
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings "
                "WHERE key = 'license_key'"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — missing table/DB → no key
        logger.debug("stored license lookup failed: %s", exc)
        return None
    if not row or not row[0]:
        return None
    return str(row[0])


def verify_stored_license() -> tuple[Optional[dict[str, Any]], str]:
    """Re-validate the stored key signature + expiry.

    Returns (payload, reason). payload is None when no valid key exists;
    reason is a short machine-readable code for logs/tests.
    """
    key = get_stored_license_key()
    if not key:
        return None, "no_key"
    try:
        from keygen_app.config import load_public_key
        from keygen_app.keygen import validate_key
    except Exception as exc:  # noqa: BLE001
        logger.debug("license deps unavailable: %s", exc)
        return None, "deps_unavailable"
    try:
        public_key = load_public_key()
    except Exception as exc:  # noqa: BLE001 — tampered/missing pubkey
        logger.warning("license public key untrusted: %s", exc)
        return None, "untrusted_pubkey"
    try:
        payload = validate_key(key, public_key)
    except ValueError as exc:
        logger.debug("stored license invalid: %s", exc)
        return None, "invalid_signature"
    return payload, "ok"


def _account_email(user_id: str) -> str:
    try:
        from api.auth import user_manager

        info = user_manager.get_user_info_by_id(user_id) or {}
        return str(info.get("email") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _keygen_rows(user_id: str) -> list[dict[str, Any]]:
    """Active keygen-sourced subscriptions for a user (empty when none)."""
    try:
        from api.billing.schema import SCHEMA, get_connection
    except Exception:  # noqa: BLE001
        return []
    try:
        with get_connection() as con:
            rows = con.execute(
                f"""SELECT id, plan_id, status, current_period_end, expires_at,
                           trial_ends_at, created_at
                    FROM {SCHEMA}.subscriptions
                    WHERE user_id = ? AND payment_provider = 'keygen'
                      AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')
                    ORDER BY created_at DESC LIMIT 5""",
                [user_id],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — table missing etc.
        logger.debug("keygen row lookup failed: %s", exc)
        return []
    out = []
    for r in rows:
        out.append({
            "id": r[0], "plan_id": r[1], "status": r[2],
            "current_period_end": r[3], "expires_at": r[4],
            "trial_ends_at": r[5], "created_at": r[6],
        })
    return out


def _cloud_sync_rows(user_id: str) -> list[dict[str, Any]]:
    """Active cloud_sync-sourced subscriptions for a user (empty when none)."""
    try:
        from api.billing.schema import SCHEMA, get_connection
    except Exception:  # noqa: BLE001
        return []
    try:
        with get_connection() as con:
            rows = con.execute(
                f"""SELECT id, plan_id, status, current_period_end, expires_at,
                           trial_ends_at, created_at, email
                    FROM {SCHEMA}.subscriptions
                    WHERE user_id = ? AND payment_provider = 'cloud_sync'
                      AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')
                    ORDER BY created_at DESC LIMIT 5""",
                [user_id],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud_sync row lookup failed: %s", exc)
        return []
    return [
        {
            "id": r[0], "plan_id": r[1], "status": r[2],
            "current_period_end": r[3], "expires_at": r[4],
            "trial_ends_at": r[5], "created_at": r[6], "email": r[7],
        }
        for r in rows
    ]


def lapse_cloud_sync_rows(user_id: str, reason: str) -> None:
    """Expire cloud_sync rows (token invalid/expired/missing)."""
    try:
        from api.billing.schema import SCHEMA, get_connection
    except Exception:  # noqa: BLE001
        return
    try:
        with get_connection() as con:
            con.execute(
                f"""UPDATE {SCHEMA}.subscriptions SET status = 'EXPIRED',
                    updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND payment_provider = 'cloud_sync'
                      AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')""",
                [user_id],
            )
            try:
                con.execute(
                    f"""UPDATE {SCHEMA}.user_subscriptions SET plan = 'free',
                        updated_at = CURRENT_TIMESTAMP WHERE user_id = ?""",
                    [user_id],
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("lapse cloud_sync rows failed: %s", exc)
    try:
        from api.billing.cache import clear_request_cache

        clear_request_cache()
    except Exception:  # noqa: BLE001
        pass


def verified_cloud_sync_plan(user_id: str, candidate_plan: str) -> str:
    """Enforce server-signed tokens for cloud_sync-sourced paid plans.

    - candidate free → free.
    - no active cloud_sync rows → candidate unchanged (other paths).
    - rows present → require a valid unexpired token bound to this account's
      email with a matching plan. Otherwise lapse rows and return free.
    """
    from api.billing.entitlement_token import get_stored_token, verify_token

    candidate = (candidate_plan or "free").lower().strip()
    if candidate == "free":
        return "free"
    rows = _cloud_sync_rows(user_id)
    if not rows:
        return candidate
    email = _account_email(user_id)
    if not email:
        lapse_cloud_sync_rows(user_id, "no_email")
        return "free"
    token = get_stored_token(email)
    if not token:
        logger.info("cloud_sync plan rejected for %s: no_token", user_id)
        lapse_cloud_sync_rows(user_id, "no_token")
        return "free"
    try:
        payload = verify_token(token, email)
    except ValueError as exc:
        logger.info("cloud_sync plan rejected for %s: %s", user_id, exc)
        lapse_cloud_sync_rows(user_id, "bad_token")
        return "free"
    if str(payload.get("plan", "")).lower() != candidate:
        logger.info("cloud_sync plan mismatch for %s", user_id)
        lapse_cloud_sync_rows(user_id, "plan_mismatch")
        return "free"
    return candidate


def lapse_keygen_rows(user_id: str, reason: str) -> None:
    """Expire keygen rows + mark stored license invalid (tamper response)."""
    import json as _json

    try:
        from api.billing.schema import SCHEMA, get_connection
    except Exception:  # noqa: BLE001
        return
    try:
        with get_connection() as con:
            con.execute(
                f"""UPDATE {SCHEMA}.subscriptions SET status = 'EXPIRED',
                    updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND payment_provider = 'keygen'
                      AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')""",
                [user_id],
            )
            try:
                con.execute(
                    f"""UPDATE {SCHEMA}.user_subscriptions SET plan = 'free',
                        updated_at = CURRENT_TIMESTAMP WHERE user_id = ?""",
                    [user_id],
                )
            except Exception:  # noqa: BLE001 — legacy table may not exist
                pass
            try:
                con.execute(
                    f"""UPDATE {SCHEMA}.premium_settings SET value = ?
                        WHERE key = 'license_status'""",
                    [_json.dumps({"valid": False, "reason": reason})],
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("lapse keygen rows failed: %s", exc)
    try:
        from api.billing.cache import clear_request_cache

        clear_request_cache()
    except Exception:  # noqa: BLE001
        pass


def _remote_check_due() -> bool:
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings "
                "WHERE key = ?",
                [_REMOTE_CHECK_KEY],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return True
    if not row or not row[0]:
        return True
    try:
        last = datetime.fromisoformat(str(row[0]))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (_utcnow() - last) >= _REMOTE_CHECK_INTERVAL
    except (ValueError, TypeError):
        return True


def _stamp_remote_check() -> None:
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            try:
                row = con.execute(
                    f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?",
                    [_REMOTE_CHECK_KEY],
                ).fetchone()
            except Exception:  # noqa: BLE001 — table missing
                return
            val = _utcnow().isoformat()
            if row:
                con.execute(
                    f"UPDATE {SCHEMA}.premium_settings "
                    "SET value = ? WHERE key = ?",
                    [val, _REMOTE_CHECK_KEY],
                )
            else:
                con.execute(
                    f"INSERT INTO {SCHEMA}.premium_settings "
                    "(key, value) VALUES (?, ?)",
                    [_REMOTE_CHECK_KEY, val],
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("remote check stamp failed: %s", exc)


def _last_remote_check() -> Optional[datetime]:
    """Timestamp of the last successful server validation, if any."""
    try:
        from api.billing.schema import SCHEMA, get_connection

        with get_connection() as con:
            row = con.execute(
                f"SELECT value FROM {SCHEMA}.premium_settings WHERE key = ?",
                [_REMOTE_CHECK_KEY],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row[0]:
        return None
    try:
        last = datetime.fromisoformat(str(row[0]))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last
    except (ValueError, TypeError):
        return None


def _as_aware(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _grace_expired(rows: list[dict[str, Any]]) -> bool:
    """True when offline longer than grace without a server validation.

    Grace counts from the last successful remote check; when none exists
    (pre-grace installs), from the oldest keygen grant so deleting the
    check row cannot reset the clock. Unconfigured server → never expires.
    """
    try:
        from api.billing import redemption
    except Exception:  # noqa: BLE001
        return False
    if not redemption.is_remote_configured():
        return False
    last = _last_remote_check()
    if last is not None:
        return (_utcnow() - last) > timedelta(days=grace_days())
    created = [_as_aware(r.get("created_at")) for r in rows]
    created = [c for c in created if c is not None]
    if not created:
        return False  # fail open when age is unknowable
    return (_utcnow() - min(created)) > timedelta(days=grace_days())


def _remote_revoked(user_id: str, email: str, key: str, key_hash_val: str) -> Optional[bool]:
    """Ask the billing server if a locally-valid key was revoked.

    Returns True (revoked), False (still owner), None (unreachable/unconfigured
    or not due — fail open).
    """
    try:
        from api.billing import redemption
    except Exception:  # noqa: BLE001
        return None
    if not redemption.is_remote_configured():
        return None
    if not _remote_check_due():
        return None
    try:
        result = redemption.redeem_remote(key, key_hash_val, email, user_id)
    except Exception as exc:  # noqa: BLE001 — stub/network raise = unreachable
        logger.debug("remote revocation check unreachable: %s", exc)
        return None
    status = (result or {}).get("status")
    if status in ("redeemed", "already_owner"):
        _stamp_remote_check()
        return False
    if status in ("conflict", "refunded", "unknown"):
        _stamp_remote_check()
        return True
    return None  # unreachable → fail open


def verified_keygen_plan(user_id: str, candidate_plan: str) -> str:
    """Enforce stored-key validity for keygen-sourced paid plans.

    - candidate free → free (nothing to verify).
    - no active keygen rows → candidate unchanged (trial/razorpay/admin path).
    - keygen rows present → require valid signature + unexpired + email binding,
      remote non-revoked when due, and offline grace not expired. Otherwise
      lapse rows and return free.
    """
    from keygen_app.keygen import bound_email, key_hash

    candidate = (candidate_plan or "free").lower().strip()
    if candidate == "free":
        return "free"
    rows = _keygen_rows(user_id)
    if not rows:
        return candidate
    payload, reason = verify_stored_license()
    if payload is None:
        logger.info("keygen plan rejected for %s: %s", user_id, reason)
        lapse_keygen_rows(user_id, reason)
        return "free"
    # Plan must match the signed grant — a stored pro key cannot justify a
    # hand-edited lifetime row.
    signed_plan = str(payload.get("plan", "")).lower()
    if signed_plan != candidate:
        logger.info(
            "keygen plan mismatch for %s: signed=%s candidate=%s",
            user_id, signed_plan, candidate,
        )
        lapse_keygen_rows(user_id, "plan_mismatch")
        return "free"
    # Email binding: key only works on the purchasing account.
    buyer = bound_email(payload)
    if not buyer:
        lapse_keygen_rows(user_id, "unbound_key")
        return "free"
    email = _account_email(user_id)
    if not email or email != buyer:
        logger.info("keygen email mismatch for %s", user_id)
        lapse_keygen_rows(user_id, "email_mismatch")
        return "free"
    # Remote revocation (refund/share) — fail open inside grace.
    # Single read of the stored key: verify and revoke must see the same key.
    # Import failures mean no server to check; redeem errors count as
    # unreachable (grace still applies below).
    try:
        from api.billing import redemption as _redemption
    except Exception:  # noqa: BLE001
        _redemption = None  # type: ignore[assignment]
    revoked = None
    if _redemption is not None:
        try:
            stored_key = get_stored_license_key() or ""
            revoked = _remote_revoked(
                user_id, email, stored_key,
                key_hash(stored_key) if stored_key else "",
            )
        except Exception:  # noqa: BLE001 — treat errors as unreachable
            revoked = None
    if revoked:
        logger.info("keygen key revoked remotely for %s", user_id)
        lapse_keygen_rows(user_id, "remote_revoked")
        return "free"
    if revoked is None and _redemption is not None:
        try:
            remote_on = _redemption.is_remote_configured()
        except Exception:  # noqa: BLE001
            remote_on = False
        # Due but unreachable → grace decides. Not due means a valid check
        # happened within 24h, so allow.
        if remote_on and _remote_check_due() and _grace_expired(rows):
            logger.info("keygen offline grace expired for %s", user_id)
            lapse_keygen_rows(user_id, "grace_expired")
            return "free"
    return candidate


def verify_legacy_fallback(user_id: str, legacy_plan: str) -> str:
    """Legacy user_subscriptions paid rows require a valid bound key.

    The legacy table has no provider column, so a hand-edited
    ``plan=pro`` row there must still present a valid stored key bound to
    this account — otherwise fail closed to free. Remote revocation and
    offline grace apply here too (fail open inside grace).
    """
    from keygen_app.keygen import bound_email, key_hash

    legacy = (legacy_plan or "free").lower().strip()
    if legacy == "free":
        return "free"
    # A canonical billing row already verified upstream wins — legacy is only
    # consulted when no effective subscription exists.
    payload, _ = verify_stored_license()
    if payload is None:
        return "free"
    if str(payload.get("plan", "")).lower() != legacy:
        return "free"
    buyer = bound_email(payload)
    if not buyer:
        return "free"
    email = _account_email(user_id)
    if email != buyer:
        return "free"
    try:
        from api.billing import redemption as _legacy_redemption
    except Exception:  # noqa: BLE001
        return legacy
    revoked = None
    try:
        stored_key = get_stored_license_key() or ""
        revoked = _remote_revoked(
            user_id, email, stored_key,
            key_hash(stored_key) if stored_key else "",
        )
    except Exception:  # noqa: BLE001 — treat errors as unreachable
        revoked = None
    if revoked:
        return "free"
    if revoked is None:
        try:
            remote_on = _legacy_redemption.is_remote_configured()
        except Exception:  # noqa: BLE001
            remote_on = False
        if remote_on and _remote_check_due() and _grace_expired([]):
            return "free"
    return legacy
