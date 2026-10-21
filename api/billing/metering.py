"""
Usage metering for the billing system.

Tracks per-user, per-metric, per-period usage against the plan limits
(usage_records) with an idempotent event log (usage_events). Mirrors the
TypeScript reference's UsageService, backed by DuckDB.

Two budgets are enforced:

* the **per-user** plan limit (``max_ai_requests`` and friends), and
* the **per-tenant** budget (``BILLING_TENANT_AI_DAILY_CAP``), which caps
  the aggregate AI usage of every account of a tenant.  A cap of 0 means
  unlimited.

Concurrency: a module-level lock serializes read-modify-write cycles, and
the whole cycle runs on the process-wide shared warehouse connection under
the app's query lock, so an unlocked multi-connection read-modify-write
cannot race (N+1 connections + TOCTOU).

Reads that must not persist run inside a rolled-back transaction; the
writes that follow run outside it, so a metered increment is never
silently discarded.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from api.billing.schema import (
    SCHEMA,
    get_connection,
    readonly_connection,
    readonly_phase,
)
from api.billing.entitlements import get_entitlements

logger = logging.getLogger(__name__)

_METER_LOCK = threading.Lock()

# Metrics metered per DAY instead of per calendar month.
_DAILY_METRICS = {"ai_requests"}


def current_period() -> str:
    """Monthly period key (YYYY-MM) — the default for non-daily metrics."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")


def _period_for(metric: str) -> str:
    """Period key for a metric: daily (YYYY-MM-DD) for _DAILY_METRICS,
    monthly (YYYY-MM) for everything else."""
    if metric in _DAILY_METRICS:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    return current_period()


def tenant_ai_daily_cap() -> int:
    """The per-tenant daily AI request budget (0 = unlimited).

    Read from ``BILLING_TENANT_AI_DAILY_CAP`` first (so a change takes
    effect without a restart), then from the configuration.
    """
    raw = (os.environ.get("BILLING_TENANT_AI_DAILY_CAP") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning(
                "BILLING_TENANT_AI_DAILY_CAP=%r is not a number — treating the "
                "tenant budget as unlimited", raw,
            )
            return 0
    try:
        from config import settings

        return max(0, int(settings.billing.tenant_ai_daily_cap or 0))
    except Exception:  # noqa: BLE001 — the config is optional
        return 0


def resolve_tenant_id(user_id: str, explicit: Optional[str] = None) -> str:
    """The tenant of *user_id* (the identity store is the source of truth).

    An explicit tenant wins; otherwise the account's own ``tenant_id`` is
    used.  An account without a tenant has no tenant budget.
    """
    if explicit is not None:
        return str(explicit).strip()
    try:
        from api.user_store import get_user_store

        record = get_user_store().get_user_by_id(user_id) or {}
        return str(record.get("tenant_id") or "").strip()
    except Exception:  # noqa: BLE001 — no tenant, no tenant budget
        return ""


def _plan_limit(user_id: str, metric: str, con=None) -> Tuple[int, Optional[str]]:
    """(limit, subscription_id) for the user's current plan.

    limit == -1 means unlimited. Falls back to the Free plan when the user
    has no active subscription. When `con` is provided, the plan and limit
    are resolved on that same connection (no extra connections).
    """
    key = {
        "dashboards": "max_dashboards",
        "chat_history_days": "max_chat_history_days",
        "ai_requests": "max_ai_requests",
    }.get(metric)
    if key is None:
        # Unknown metric → deny by default (limit 0).
        return 0, None

    if con is not None:
        try:
            from api.billing.subs import find_effective_subscription, _plan_code
            sub = find_effective_subscription(user_id, con=con)
            plan = _plan_code(sub["plan_id"]) if sub else "free"
            row = con.execute(
                f"SELECT limit_value FROM {SCHEMA}.plan_limits WHERE plan_id = ? AND metric = ?",
                [f"plan_{plan}", metric],
            ).fetchone()
            if row:
                return int(row[0]), (sub["id"] if sub else None)
        except Exception:  # noqa: BLE001
            pass

    ent = get_entitlements(user_id)
    limit = ent.get(key)
    if limit is None:
        # Unknown metric → deny by default (limit 0).
        limit = 0
    return int(limit), ent.get("subscription_id")


def _dashboard_count(con, user_id: str) -> int:
    """The real number of dashboards the user owns (-1 when unknown)."""
    try:
        row = con.execute(
            "SELECT count(*) FROM analytics.dashboards WHERE owner_id = ?",
            [user_id],
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001 - never block create on reconcile
        return -1


def _backfill_dashboards(con, user_id: str, period: str, count: int) -> None:
    """Seed the dashboards usage counter from the actual dashboard count.

    Runs once per (user, period) when no usage record exists yet, so users
    with dashboards created before metering are counted accurately.
    """
    try:
        con.execute(
            f"""INSERT INTO {SCHEMA}.usage_records
                (id, user_id, subscription_id, metric, period, used, limit_snapshot, tenant_id)
                VALUES (?, ?, NULL, 'dashboards', ?, ?, NULL, ?)""",
            [str(uuid.uuid4()), user_id, period, int(count), resolve_tenant_id(user_id)],
        )
    except Exception:  # noqa: BLE001
        pass


def _reconcile_dashboards(con, user_id: str, period: str, row, actual: int) -> int:
    """Make the dashboards counter match reality. Returns the true count.

    Self-heal drift for the dashboards metric: the counter can end a
    period higher than reality (deletes that predated release_usage,
    manual row removal, restored backups), permanently blocking plan
    quota. The true count is cheap and authoritative.
    """
    if actual < 0:
        return int(row[0]) if row else 0
    if row is None:
        _backfill_dashboards(con, user_id, period, actual)
        return actual
    used = int(row[0])
    if used == actual:
        return used
    con.execute(
        f"UPDATE {SCHEMA}.usage_records SET used = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND metric = ? AND period = ?",
        [actual, user_id, "dashboards", period],
    )
    return actual


def _tenant_used(con, tenant_id: str, metric: str, period: str) -> int:
    """The metered usage of every account of *tenant_id* in *period*."""
    if not tenant_id:
        return 0
    row = con.execute(
        f"SELECT COALESCE(SUM(used), 0) FROM {SCHEMA}.usage_records "
        "WHERE tenant_id = ? AND metric = ? AND period = ?",
        [tenant_id, metric, period],
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _is_duplicate(con, idempotency_key: str) -> bool:
    """True when this idempotency key was already recorded."""
    row = con.execute(
        f"SELECT id FROM {SCHEMA}.usage_events WHERE idempotency_key = ?",
        [idempotency_key],
    ).fetchone()
    return row is not None


def check_usage(user_id: str, metric: str) -> Dict[str, Any]:
    """Whether the user may consume more of `metric` this period."""
    period = _period_for(metric)
    with _METER_LOCK:
        with get_connection() as con:
            # The plan lookup may write (lazy expiry, legacy sync), so it
            # runs outside the rolled-back read phase.
            limit, _sub_id = _plan_limit(user_id, metric, con=con)
            with readonly_phase(con):
                row = con.execute(
                    f"SELECT used FROM {SCHEMA}.usage_records WHERE user_id = ? AND metric = ? AND period = ?",
                    [user_id, metric, period],
                ).fetchone()
                actual = _dashboard_count(con, user_id) if metric == "dashboards" else -1
            if metric == "dashboards":
                used = _reconcile_dashboards(con, user_id, period, row, actual)
            else:
                used = int(row[0]) if row else 0
    if limit == -1:
        return {"allowed": True, "used": used, "limit": -1, "remaining": -1}
    return {
        "allowed": used < limit,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
    }


@dataclass(frozen=True)
class _UsageState:
    """One metered usage record plus the budgets that apply to it."""

    user_id: str
    metric: str
    period: str
    record_id: Optional[str]
    used: int
    limit: int
    subscription_id: Optional[str]
    tenant_id: str
    tenant_used: int


def _read_usage_state(con, user_id: str, metric: str, period: str,
                      tenant: str) -> _UsageState:
    """Read the usage record and the tenant total for the period.

    The plan limit is filled in by the caller (it is resolved on the same
    connection, outside the rolled-back read phase).
    """
    rec = con.execute(
        f"SELECT id, used FROM {SCHEMA}.usage_records "
        "WHERE user_id = ? AND metric = ? AND period = ?",
        [user_id, metric, period],
    ).fetchone()
    return _UsageState(
        user_id=user_id,
        metric=metric,
        period=period,
        record_id=rec[0] if rec else None,
        used=int(rec[1]) if rec else 0,
        limit=-1,
        subscription_id=None,
        tenant_id=tenant,
        tenant_used=_tenant_used(con, tenant, metric, period),
    )


def _check_limits(state: _UsageState, amount: int) -> None:
    """Raise UsageLimitError when the increment would exceed a budget."""
    if state.limit != -1 and state.used + amount > state.limit:
        raise UsageLimitError(state.metric, state.used, state.limit)
    cap = tenant_ai_daily_cap() if state.metric in _DAILY_METRICS else 0
    if cap > 0 and state.tenant_id and state.tenant_used + amount > cap:
        raise UsageLimitError(f"tenant:{state.metric}", state.tenant_used, cap)


def _write_usage(con, state: _UsageState, new_used: int) -> None:
    """Persist the metered increment.

    This runs *outside* the rolled-back read phase: a rolled-back
    transaction would silently discard the usage and break the cap.
    """
    if state.record_id:
        con.execute(
            f"UPDATE {SCHEMA}.usage_records SET used = ?, limit_snapshot = ?, "
            "subscription_id = ?, tenant_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [new_used, state.limit, state.subscription_id, state.tenant_id, state.record_id],
        )
        return
    con.execute(
        f"INSERT INTO {SCHEMA}.usage_records (id, user_id, subscription_id, metric, "
        "period, used, limit_snapshot, tenant_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), state.user_id, state.subscription_id, state.metric,
         state.period, new_used, state.limit, state.tenant_id],
    )


def _write_usage_event(con, user_id: str, metric: str, amount: int,
                        idempotency_key: str) -> None:
    """Append the idempotent usage event."""
    con.execute(
        f"INSERT INTO {SCHEMA}.usage_events (id, user_id, metric, amount, idempotency_key) "
        "VALUES (?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), user_id, metric, amount, idempotency_key],
    )


def record_usage(
    user_id: str,
    metric: str,
    amount: int = 1,
    idempotency_key: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically increment usage for `metric` this period.

    Raises UsageLimitError when the increment would exceed the plan limit
    or the tenant's daily budget. Returns {"used", "limit", "remaining"}
    on success, or {"status": "duplicate_ignored"} when idempotency_key was
    already seen.
    """
    period = _period_for(metric)
    tenant = resolve_tenant_id(user_id, tenant_id)
    with _METER_LOCK:
        with get_connection() as con:
            if idempotency_key and _is_duplicate(con, idempotency_key):
                return {"status": "duplicate_ignored"}
            # The plan lookup may write (lazy expiry, legacy sync), so it
            # runs outside the rolled-back read phase.
            limit, subscription_id = _plan_limit(user_id, metric, con=con)
            with readonly_phase(con):
                state = _read_usage_state(con, user_id, metric, period, tenant)
            state = replace(state, limit=limit, subscription_id=subscription_id)
            _check_limits(state, amount)
            new_used = state.used + amount
            _write_usage(con, state, new_used)
            if idempotency_key:
                _write_usage_event(con, user_id, metric, amount, idempotency_key)
    return {
        "used": new_used,
        "limit": state.limit,
        "remaining": -1 if state.limit == -1 else max(0, state.limit - new_used),
    }


def release_usage(user_id: str, metric: str, amount: int = 1) -> Dict[str, Any]:
    """Decrement usage for `metric` this period (floor 0).

    Called when a metered resource is deleted (e.g. a dashboard) so the
    counter reflects reality. Returns the updated usage summary.
    """
    period = _period_for(metric)
    with _METER_LOCK:
        with get_connection() as con:
            with readonly_phase(con):
                rec = con.execute(
                    f"SELECT id, used FROM {SCHEMA}.usage_records WHERE user_id = ? AND metric = ? AND period = ?",
                    [user_id, metric, period],
                ).fetchone()
                if rec is None:
                    return {"used": 0, "limit": None, "remaining": None}
                rec_id, used = rec[0], int(rec[1])
                new_used = max(0, used - amount)
            con.execute(
                f"UPDATE {SCHEMA}.usage_records SET used = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [new_used, rec_id],
            )
            limit = _plan_limit(user_id, metric, con=con)[0]
    return {
        "used": new_used,
        "limit": limit,
        "remaining": -1 if limit == -1 else max(0, limit - new_used),
    }


def get_usage_summary(user_id: str) -> Dict[str, Any]:
    """Per-metric usage summary for the current period(s).

    Daily-metered metrics report today's usage; monthly metrics report the
    current month.
    """
    result: Dict[str, Any] = {"period": _period_for("dashboards")}
    with readonly_connection() as con:
        for metric in ("dashboards", "chat_history_days", "ai_requests"):
            period = _period_for(metric)
            row = con.execute(
                f"""SELECT used, limit_snapshot FROM {SCHEMA}.usage_records
                    WHERE user_id = ? AND metric = ? AND period = ?""",
                [user_id, metric, period],
            ).fetchone()
            if not row:
                continue
            limit = int(row[1])
            result[metric] = {
                "used": int(row[0]),
                "limit": limit,
                "remaining": -1 if limit == -1 else max(0, limit - int(row[0])),
            }
    return result


def get_tenant_usage(tenant_id: str) -> Dict[str, Any]:
    """The aggregate AI usage of one tenant for today's daily period.

    ``limit`` is the tenant's daily budget (``-1`` when it is unlimited),
    ``users`` breaks the aggregate down per account.  Read-only: it never
    writes.
    """
    wanted = str(tenant_id or "").strip()
    period = _period_for("ai_requests")
    cap = tenant_ai_daily_cap()
    limit = -1 if cap <= 0 else cap
    if not wanted:
        return {
            "tenant_id": wanted,
            "period": period,
            "metric": "ai_requests",
            "used": 0,
            "limit": limit,
            "remaining": -1 if limit == -1 else limit,
            "users": [],
        }
    with readonly_connection() as con:
        total = _tenant_used(con, wanted, "ai_requests", period)
        rows = con.execute(
            f"SELECT user_id, used FROM {SCHEMA}.usage_records "
            "WHERE tenant_id = ? AND metric = ? AND period = ? ORDER BY user_id",
            [wanted, "ai_requests", period],
        ).fetchall()
    users: List[Dict[str, Any]] = [
        {"user_id": str(row[0]), "used": int(row[1] or 0)} for row in rows
    ]
    return {
        "tenant_id": wanted,
        "period": period,
        "metric": "ai_requests",
        "used": total,
        "limit": limit,
        "remaining": -1 if limit == -1 else max(0, limit - total),
        "users": users,
    }


class UsageLimitError(Exception):
    """Raised when a usage increment would exceed the plan limit."""

    def __init__(self, metric: str, used: int, limit: int):
        self.metric = metric
        self.used = used
        self.limit = limit
        super().__init__(
            f"Usage limit reached for '{metric}': {used}/{limit}. "
            "Upgrade your plan to continue."
        )
