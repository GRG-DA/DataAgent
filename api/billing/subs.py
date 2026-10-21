"""
Canonical subscription layer — single source of truth for "what plan does
this user have".

All plan resolution (entitlements, auth middleware, admin grants, webhook
activation) flows through this module. It owns the lifecycle transitions on
analytics.subscriptions (lazy expiry, renewal period advance, cancel-at-period-
end, past-due, revoke) and keeps the legacy analytics.user_subscriptions table
in sync as a write-through compatibility layer.

Lifecycle model:
    created        -> ACTIVE (period = billing_cycle length)
    renewal        -> advance current_period_start/end (MONTHLY/YEARLY only)
    cancel         -> cancel_at_period_end = TRUE (access until period end)
    failed renewal -> PAST_DUE (access until period end, then EXPIRED)
    full refund    -> CANCELLED
    lazy expiry    -> EXPIRED when current_period_end / expires_at /
                      trial_ends_at passes (checked on read, no sweeper)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from api.billing.schema import get_connection, SCHEMA
from api.billing.grant_signature import new_grant_nonce

logger = logging.getLogger(__name__)

# Period lengths per billing cycle (days).
PERIOD_DAYS = {"MONTHLY": 31, "YEARLY": 365, "ONETIME": 3650}

# Statuses that still grant entitlements (checked for lazy expiry).
_EFFECTIVE_STATUSES = ("ACTIVE", "TRIALING", "PAST_DUE")

_COL_NAMES = (
    "id", "user_id", "plan_id", "status", "billing_cycle",
    "current_period_start", "current_period_end", "cancel_at_period_end",
    "cancelled_at", "expires_at", "trial_ends_at", "payment_provider",
    "email", "sso_subject", "grant_nonce", "grant_signature",
    "created_at", "updated_at",
)
_COLS = ", ".join(_COL_NAMES)


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _period_end(billing_cycle: str, start: Optional[datetime] = None) -> str:
    start = start or datetime.now(timezone.utc).replace(tzinfo=None)
    days = PERIOD_DAYS.get((billing_cycle or "MONTHLY").upper(), 31)
    return (start + timedelta(days=days)).isoformat()


def _plan_code(plan_id: str) -> str:
    return (plan_id or "").replace("plan_", "", 1)


def _sign_row(con, sub_id: str, user_id: str, plan_id: str, provider: str,
              period_end: Optional[str], expires_at: Optional[str],
              nonce: str, trial_ends_at: Optional[str] = None) -> None:
    """Stamp (or refresh) the grant signature for a subscription row.

    Called after every write that changes what the row entitles the user to
    (create, plan change, period change, status change). The signature
    covers the grant's canonical value, so any later hand-edit to those
    fields invalidates it and the entitlement is refused.
    """
    try:
        from api.billing.grant_signature import sign_grant

        signature = sign_grant(
            user_id, plan_id, provider, nonce, period_end, expires_at,
            trial_ends_at,
        )
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET grant_signature = ? WHERE id = ?",
            [signature, sub_id],
        )
    except Exception as exc:  # noqa: BLE001 — never break a write on signing
        logger.debug("grant signing failed for %s: %s", sub_id, exc)


def _log_event(con, subscription_id: str, event_type: str,
               from_status: Optional[str] = None,
               to_status: Optional[str] = None,
               metadata: Optional[dict] = None) -> None:
    con.execute(
        f"""INSERT INTO {SCHEMA}.subscription_events
            (id, subscription_id, type, from_status, to_status, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), subscription_id, event_type, from_status, to_status,
         json.dumps(metadata) if metadata else None, _now()],
    )


def _is_expired(sub: dict) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for key in ("current_period_end", "expires_at", "trial_ends_at"):
        val = sub.get(key)
        if not val:
            continue
        try:
            if datetime.fromisoformat(str(val)) < now:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _lapse(con, sub: dict) -> None:
    """Transition an expired subscription to EXPIRED (lazy, on read)."""
    con.execute(
        f"""UPDATE {SCHEMA}.subscriptions SET status = 'EXPIRED', updated_at = ?
            WHERE id = ? AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')""",
        [_now(), sub["id"]],
    )
    _log_event(con, sub["id"], "expired", from_status=sub["status"],
               to_status="EXPIRED", metadata={"reason": "period_end"})


def _sync_legacy(con, user_id: str, plan: str) -> None:
    """Best-effort write-through to the legacy user_subscriptions table."""
    try:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.user_subscriptions (
                user_id VARCHAR PRIMARY KEY,
                plan VARCHAR NOT NULL DEFAULT 'free',
                status VARCHAR NOT NULL DEFAULT 'active',
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = con.execute(
            f"SELECT plan FROM {SCHEMA}.user_subscriptions WHERE user_id = ?",
            [user_id],
        ).fetchone()
        if row:
            if row[0] != plan:
                con.execute(
                    f"UPDATE {SCHEMA}.user_subscriptions SET plan = ?, updated_at = ? WHERE user_id = ?",
                    [plan, _now(), user_id],
                )
        elif plan != "free":
            now = _now()
            con.execute(
                f"INSERT INTO {SCHEMA}.user_subscriptions (user_id, plan, status, started_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                [user_id, plan, now, now],
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("legacy sync failed: %s", e)


def sync_legacy(user_id: str, plan: str) -> None:
    """Public write-through to the legacy table (opens its own connection)."""
    try:
        with get_connection() as con:
            _sync_legacy(con, user_id, plan)
    except Exception as e:  # noqa: BLE001
        logger.debug("legacy sync failed: %s", e)


def _first_effective(con, where: str, params: list) -> Optional[dict]:
    rows = con.execute(
        f"""SELECT {_COLS} FROM {SCHEMA}.subscriptions
            WHERE {where} AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')
            ORDER BY created_at DESC LIMIT 10""",
        params,
    ).fetchall()
    for row in rows:
        sub = dict(zip(_COL_NAMES, row))
        if _is_expired(sub):
            _lapse(con, sub)
            continue
        return sub
    return None


def find_effective_subscription(
    user_id: str,
    email: str = "",
    sso_subject: str = "",
    con=None,
) -> Optional[dict]:
    """Return the user's effective subscription row (dict) or None.

    Applies lazy expiry: ACTIVE/TRIALING/PAST_DUE rows whose period has
    passed are transitioned to EXPIRED (with an audit event) and skipped.
    Falls back to the Google identity (email / sso_subject) when the local
    user_id has no row, so a paid subscription follows the Google account.
    """
    if con is None:
        with get_connection() as con:
            return _find_effective_subscription(con, user_id, email, sso_subject)
    return _find_effective_subscription(con, user_id, email, sso_subject)


def _find_effective_subscription(con, user_id, email, sso_subject):
    sub = _first_effective(con, "user_id = ?", [user_id])
    if sub:
        _sync_legacy(con, user_id, _plan_code(sub["plan_id"]))
        return sub
    if email or sso_subject:
        sub = _first_effective(con, "(email = ? OR sso_subject = ?)", [email, sso_subject])
        if sub:
            _sync_legacy(con, user_id, _plan_code(sub["plan_id"]))
            return sub
    return None


def ensure_billing_row(
    user_id: str,
    plan: str,
    billing_cycle: str = "MONTHLY",
    status: str = "ACTIVE",
    period_end: Optional[str] = None,
    never_expires: bool = False,
    email: Optional[str] = None,
    sso_subject: Optional[str] = None,
    provider: str = "razorpay",
    con=None,
) -> str:
    """Upsert a canonical subscription row; returns its id.

    period_end=None keeps the existing value (or defaults to one period for
    new rows). never_expires=True clears the period end (admin grants).
    """
    if con is None:
        with get_connection() as con:
            return _ensure_billing_row(con, user_id, plan, billing_cycle, status,
                                       period_end, never_expires, email, sso_subject, provider)
    return _ensure_billing_row(con, user_id, plan, billing_cycle, status,
                               period_end, never_expires, email, sso_subject, provider)


def _ensure_billing_row(con, user_id, plan, billing_cycle, status, period_end,
                        never_expires, email, sso_subject, provider="razorpay"):
    plan_id = f"plan_{plan}"
    now = _now()
    row = con.execute(
        f"""SELECT id FROM {SCHEMA}.subscriptions
            WHERE user_id = ? AND plan_id = ? AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE', 'CANCEL_AT_PERIOD_END')
            ORDER BY created_at DESC LIMIT 1""",
        [user_id, plan_id],
    ).fetchone()
    if row:
        sub_id = row[0]
        sets = ["status = ?", "billing_cycle = ?", "updated_at = ?"]
        params = [status, billing_cycle, now]
        if never_expires:
            sets.append("current_period_end = NULL")
        elif period_end is not None:
            sets.append("current_period_end = ?")
            params.append(period_end)
        if email:
            sets.append("email = COALESCE(email, ?)")
            params.append(email)
        if sso_subject:
            sets.append("sso_subject = COALESCE(sso_subject, ?)")
            params.append(sso_subject)
        params.append(sub_id)
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        # Re-sign: the grant's value may have changed (period, status, plan).
        _resign(con, sub_id)
        _log_event(con, sub_id, "updated", from_status=None, to_status=status,
                   metadata={"plan": plan, "billing_cycle": billing_cycle})
        return sub_id

    sub_id = str(uuid.uuid4())
    end = None if never_expires else (period_end or _period_end(billing_cycle))
    nonce = new_grant_nonce()
    con.execute(
        f"""INSERT INTO {SCHEMA}.subscriptions
            (id, user_id, plan_id, status, billing_cycle, start_date,
             current_period_start, current_period_end, cancel_at_period_end,
             payment_provider, email, sso_subject, grant_nonce, grant_signature,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?, ?, ?, ?)""",
        [sub_id, user_id, plan_id, status, billing_cycle, now, now, end,
         provider, email, sso_subject, nonce, "", now, now],
    )
    _sign_row(con, sub_id, user_id, plan_id, provider, end, None, nonce)
    _log_event(con, sub_id, "created", from_status=None, to_status=status,
               metadata={"plan": plan, "billing_cycle": billing_cycle})
    return sub_id


def _resign(con, sub_id: str) -> None:
    """Refresh the grant signature for an existing row from its current values."""
    try:
        row = con.execute(
            f"""SELECT user_id, plan_id, payment_provider, grant_nonce,
                       current_period_end, expires_at, trial_ends_at
                FROM {SCHEMA}.subscriptions WHERE id = ?""",
            [sub_id],
        ).fetchone()
        if not row:
            return
        nonce = row[3]
        if not nonce:
            nonce = new_grant_nonce()
            con.execute(
                f"UPDATE {SCHEMA}.subscriptions SET grant_nonce = ? WHERE id = ?",
                [nonce, sub_id],
            )
        _sign_row(con, sub_id, row[0], row[1], row[2] or "", row[4], row[5], nonce, row[6])
    except Exception as exc:  # noqa: BLE001
        logger.debug("grant re-signing failed for %s: %s", sub_id, exc)


def advance_period(sub_id: str, billing_cycle: str, con=None) -> None:
    """Advance a recurring subscription's period (renewal)."""
    if con is None:
        with get_connection() as con:
            _advance_period(con, sub_id, billing_cycle)
    else:
        _advance_period(con, sub_id, billing_cycle)


def _advance_period(con, sub_id, billing_cycle):
    if billing_cycle not in ("MONTHLY", "YEARLY"):
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = _period_end(billing_cycle, now)
    con.execute(
        f"""UPDATE {SCHEMA}.subscriptions SET
            current_period_start = ?, current_period_end = ?,
            status = 'ACTIVE', cancel_at_period_end = FALSE, updated_at = ?
            WHERE id = ?""",
        [now.isoformat(), end, _now(), sub_id],
    )
    # The period moved, so the grant's canonical value changed — re-sign it.
    _resign(con, sub_id)
    _log_event(con, sub_id, "renewed", from_status="ACTIVE", to_status="ACTIVE",
               metadata={"billing_cycle": billing_cycle})


def cancel_recurring(user_id: str, con=None, reactivate: bool = False) -> list:
    """Mark all ACTIVE recurring (MONTHLY/YEARLY) subs cancel_at_period_end.

    When ``reactivate`` is True, clears the flag instead (undo a cancellation).
    """
    if con is None:
        with get_connection() as con:
            return _cancel_recurring(con, user_id, reactivate=reactivate)
    return _cancel_recurring(con, user_id, reactivate=reactivate)


def _cancel_recurring(con, user_id, reactivate: bool = False):
    rows = con.execute(
        f"""SELECT id FROM {SCHEMA}.subscriptions
            WHERE user_id = ? AND status = 'ACTIVE' AND billing_cycle IN ('MONTHLY', 'YEARLY')
            ORDER BY created_at DESC""",
        [user_id],
    ).fetchall()
    ids = []
    for (sub_id,) in rows:
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET cancel_at_period_end = ?, updated_at = ? WHERE id = ?",
            [not reactivate, _now(), sub_id],
        )
        _log_event(con, sub_id, "reactivate" if reactivate else "cancel_scheduled",
                   from_status="ACTIVE", to_status="ACTIVE",
                   metadata={"cancel_at_period_end": not reactivate})
        ids.append(sub_id)
    return ids


def mark_past_due(user_id: str, con=None) -> None:
    """Mark the user's newest ACTIVE subscription PAST_DUE (failed renewal)."""
    if con is None:
        with get_connection() as con:
            _mark_past_due(con, user_id)
    else:
        _mark_past_due(con, user_id)


def _mark_past_due(con, user_id):
    rows = con.execute(
        f"""SELECT id, status FROM {SCHEMA}.subscriptions
            WHERE user_id = ? AND status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1""",
        [user_id],
    ).fetchall()
    for sub_id, status in rows:
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET status = 'PAST_DUE', updated_at = ? WHERE id = ?",
            [_now(), sub_id],
        )
        _log_event(con, sub_id, "past_due", from_status=status, to_status="PAST_DUE")


def revoke(sub_id: str, reason: str = "refund", con=None) -> None:
    """Cancel a subscription immediately (e.g. full refund) and sync legacy."""
    if con is None:
        with get_connection() as con:
            _revoke(con, sub_id, reason)
    else:
        _revoke(con, sub_id, reason)


def _revoke(con, sub_id, reason):
    row = con.execute(
        f"SELECT user_id, status FROM {SCHEMA}.subscriptions WHERE id = ?",
        [sub_id],
    ).fetchone()
    if not row:
        return
    user_id, status = row
    con.execute(
        f"UPDATE {SCHEMA}.subscriptions SET status = 'CANCELLED', cancelled_at = ?, updated_at = ? WHERE id = ?",
        [_now(), _now(), sub_id],
    )
    _log_event(con, sub_id, "cancelled", from_status=status, to_status="CANCELLED",
               metadata={"reason": reason})
    plan = _effective_plan(con, user_id)
    _sync_legacy(con, user_id, plan or "free")


def _effective_plan(con, user_id) -> Optional[str]:
    rows = con.execute(
        f"""SELECT plan_id, current_period_end, expires_at, trial_ends_at
            FROM {SCHEMA}.subscriptions
            WHERE user_id = ? AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE')
            ORDER BY created_at DESC LIMIT 10""",
        [user_id],
    ).fetchall()
    for plan_id, end, expires, trial in rows:
        if _is_expired({"current_period_end": end, "expires_at": expires, "trial_ends_at": trial}):
            continue
        return _plan_code(plan_id)
    return None


# ── Pro trial (single use per account) ───────────────────────────────

TRIAL_PLAN = "pro"
TRIAL_BILLING_CYCLE = "MONTHLY"


class TrialAlreadyUsedError(Exception):
    """Raised when a user who already had paid/trial access asks for a trial."""


class TrialDisabledError(Exception):
    """Raised when trials are disabled via BILLING_TRIAL_ENABLED=0."""


def trial_enabled() -> bool:
    """Trials are on by default; set BILLING_TRIAL_ENABLED=0 to disable."""
    import os

    return os.environ.get("BILLING_TRIAL_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def trial_days() -> int:
    """Trial length in days (env BILLING_TRIAL_DAYS, default 14, clamped 1–90)."""
    import os

    try:
        days = int(os.environ.get("BILLING_TRIAL_DAYS", "14"))
    except ValueError:
        return 14
    return max(1, min(90, days))


def _trial_identity_filter(user_id: str, email: str = "", sso_subject: str = "") -> tuple[str, list]:
    """Match prior paid/trial rows by user_id or Google identity (non-empty only)."""
    clauses = ["user_id = ?"]
    params: list = [user_id]
    if email:
        clauses.append("(email = ? AND email <> '')")
        params.append(email)
    if sso_subject:
        clauses.append("(sso_subject = ? AND sso_subject <> '')")
        params.append(sso_subject)
    return f"({' OR '.join(clauses)})", params


def _had_paid_or_trial(con, user_id: str, email: str = "", sso_subject: str = "") -> bool:
    """True when any prior pro/lifetime row exists (any status — trials leave EXPIRED rows)."""
    where, params = _trial_identity_filter(user_id, email, sso_subject)
    row = con.execute(
        f"""SELECT 1 FROM {SCHEMA}.subscriptions
            WHERE {where} AND plan_id IN ('plan_pro', 'plan_lifetime')
            LIMIT 1""",
        params,
    ).fetchone()
    return row is not None


def _sync_legacy_with_expiry(con, user_id: str, plan: str, expires_at_iso: str) -> None:
    """Mirror a time-boxed grant into the legacy table (with expiry, so it lapses)."""
    try:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.user_subscriptions (
                user_id VARCHAR PRIMARY KEY,
                plan VARCHAR NOT NULL DEFAULT 'free',
                status VARCHAR NOT NULL DEFAULT 'active',
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = con.execute(
            f"SELECT user_id FROM {SCHEMA}.user_subscriptions WHERE user_id = ?",
            [user_id],
        ).fetchone()
        now = _now()
        if row:
            con.execute(
                f"""UPDATE {SCHEMA}.user_subscriptions
                    SET plan = ?, status = 'active', expires_at = ?, updated_at = ?
                    WHERE user_id = ?""",
                [plan, expires_at_iso, now, user_id],
            )
        else:
            con.execute(
                f"""INSERT INTO {SCHEMA}.user_subscriptions
                    (user_id, plan, status, started_at, expires_at, updated_at)
                    VALUES (?, ?, 'active', ?, ?, ?)""",
                [user_id, plan, now, expires_at_iso, now],
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("trial legacy sync failed: %s", e)


def _start_trial(con, user_id: str, email: str = "", sso_subject: str = "") -> dict:
    if not trial_enabled():
        raise TrialDisabledError("Trials are disabled")
    if _had_paid_or_trial(con, user_id, email, sso_subject):
        raise TrialAlreadyUsedError("Trial already used (or paid plan present)")
    days = trial_days()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_iso = (now + timedelta(days=days)).isoformat()
    now_iso = now.isoformat()
    sub_id = str(uuid.uuid4())
    nonce = new_grant_nonce()
    con.execute(
        f"""INSERT INTO {SCHEMA}.subscriptions
            (id, user_id, plan_id, status, billing_cycle, start_date,
             current_period_start, current_period_end, cancel_at_period_end,
             payment_provider, email, sso_subject, trial_ends_at,
             grant_nonce, grant_signature, created_at, updated_at)
            VALUES (?, ?, ?, 'TRIALING', ?, ?, ?, ?, FALSE, 'trial', ?, ?, ?, ?, ?, ?, ?)""",
        [sub_id, user_id, f"plan_{TRIAL_PLAN}", TRIAL_BILLING_CYCLE,
         now_iso, now_iso, end_iso,
         (email or None), (sso_subject or None), end_iso, nonce, "", now_iso, now_iso],
    )
    _sign_row(con, sub_id, user_id, f"plan_{TRIAL_PLAN}", "trial",
              end_iso, None, nonce, end_iso)
    _log_event(con, sub_id, "trial_started", from_status=None, to_status="TRIALING",
               metadata={"plan": TRIAL_PLAN, "days": days})
    _sync_legacy_with_expiry(con, user_id, TRIAL_PLAN, end_iso)
    # Post-insert single-use guard: if a concurrent start (or a paid grant)
    # landed first, this insert is the duplicate — roll it back and refuse.
    # (DuckDB is single-writer; the second committer always sees both rows.)
    where, params = _trial_identity_filter(user_id, email, sso_subject)
    dupes = con.execute(
        f"""SELECT COUNT(*) FROM {SCHEMA}.subscriptions
            WHERE {where} AND plan_id IN ('plan_pro', 'plan_lifetime')""",
        params,
    ).fetchone()[0]
    if dupes > 1:
        con.execute(f"DELETE FROM {SCHEMA}.subscriptions WHERE id = ?", [sub_id])
        _log_event(con, sub_id, "trial_duplicate_refused", from_status="TRIALING",
                   to_status=None, metadata={"plan": TRIAL_PLAN})
        _sync_legacy_with_expiry(
            con, user_id, "free",
            (now - timedelta(days=1)).isoformat(),
        )
        raise TrialAlreadyUsedError("Trial already used (or paid plan present)")
    try:
        from api.billing.cache import clear_request_cache

        clear_request_cache()
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": sub_id,
        "user_id": user_id,
        "plan_id": f"plan_{TRIAL_PLAN}",
        "status": "TRIALING",
        "billing_cycle": TRIAL_BILLING_CYCLE,
        "current_period_end": end_iso,
        "trial_ends_at": end_iso,
    }


def start_trial(user_id: str, email: str = "", sso_subject: str = "", con=None) -> dict:
    """Grant one TRIALING pro subscription (default 14 days). Single use.

    Raises TrialDisabledError when BILLING_TRIAL_ENABLED=0 and
    TrialAlreadyUsedError when any prior pro/lifetime/trial row exists for the
    user_id or Google identity.
    """
    if con is None:
        with get_connection() as con:
            return _start_trial(con, user_id, email, sso_subject)
    return _start_trial(con, user_id, email, sso_subject)


def get_trial_status(user_id: str, email: str = "", sso_subject: str = "", con=None) -> dict:
    """Eligibility + active-trial state for the subscriptions UI."""
    if con is None:
        with get_connection() as con:
            return _get_trial_status(con, user_id, email, sso_subject)
    return _get_trial_status(con, user_id, email, sso_subject)


def _get_trial_status(con, user_id: str, email: str = "", sso_subject: str = "") -> dict:
    where, params = _trial_identity_filter(user_id, email, sso_subject)
    rows = con.execute(
        f"""SELECT status, trial_ends_at, current_period_end FROM {SCHEMA}.subscriptions
            WHERE {where} AND plan_id IN ('plan_pro', 'plan_lifetime')
            ORDER BY created_at DESC LIMIT 10""",
        params,
    ).fetchall()
    active_ends_at: Optional[str] = None
    for status, trial_end, period_end in rows:
        if status == "TRIALING" and not _is_expired({
            "current_period_end": period_end,
            "expires_at": None,
            "trial_ends_at": trial_end,
        }):
            # DuckDB returns TIMESTAMP with a space separator; normalize to
            # ISO-8601 so it matches start_trial()'s trial_ends_at format.
            active_ends_at = str(trial_end or period_end or "").replace(" ", "T")
            break
    return {
        "eligible": not _had_paid_or_trial(con, user_id, email, sso_subject),
        "active": active_ends_at is not None,
        "ends_at": active_ends_at,
        "days": trial_days(),
        "enabled": trial_enabled(),
    }