"""
Billing schema — DuckDB DDL for plans, prices, features, subscriptions,
payments, payment events (webhook idempotency), invoices, and usage.

All tables live in the analytics schema alongside the rest of the
warehouse. DDL is idempotent (CREATE TABLE IF NOT EXISTS) and mirrors the
Prisma schema from the reference subscription-billing-system.

The warehouse is reached through the **process-wide shared connection**
(:func:`api.db.get_connection`), not through a fresh handle per call: a
second connection to the same DuckDB file is what produced "a different
configuration than existing connections", and opening one per call
multiplied the single-writer collision windows on every metered request.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SCHEMA = "analytics"

# Feature keys used by entitlements resolution.
FEATURE_ADS_VISIBLE = "ads_visible"
FEATURE_IP_ROTATION = "ip_rotation"
FEATURE_AI_UNLIMITED = "ai_unlimited"

# Premium feature keys — 17 gated features.
FEATURE_RECREATE_FROM_IMAGE = "recreate_from_image"
FEATURE_DASHBOARD_EXPORT = "dashboard_export"
FEATURE_ALERTS_THRESHOLDS = "alerts_thresholds"
FEATURE_SCHEDULED_REPORTS = "scheduled_reports"
FEATURE_SMART_MONITORING = "smart_monitoring"
FEATURE_LIVE_MONITOR = "live_monitor"
FEATURE_API_CONNECTION = "api_connection"
FEATURE_ENTERPRISE = "enterprise"
FEATURE_GOVERNANCE = "governance"
FEATURE_EXTERNAL_SOURCES = "external_sources"
FEATURE_STORYTELLING = "storytelling"
FEATURE_REPORTS = "reports"
FEATURE_EXPORT_PAGE = "export_page"
FEATURE_AUTOMATION = "automation"
FEATURE_CONNECT_PAGE = "connect_page"
FEATURE_MARKETPLACE = "marketplace"
FEATURE_ORGANIZATION = "organization"

# Usage metrics tracked by the metering layer.
METRIC_AI_REQUESTS = "ai_requests"
METRIC_DASHBOARDS = "dashboards"
METRIC_CHAT_HISTORY_DAYS = "chat_history_days"

# Subscription statuses (mirrors SubscriptionStatus enum).
SUB_STATUSES = (
    "TRIALING",
    "ACTIVE",
    "PAST_DUE",
    "PAUSED",
    "CANCEL_AT_PERIOD_END",
    "CANCELLED",
    "EXPIRED",
    "PAYMENT_FAILED",
)

# Payment statuses.
PAY_STATUSES = (
    "CREATED",
    "SUCCESS",
    "FAILED",
    "REFUNDED",
    "PARTIALLY_REFUNDED",
    "CHARGEBACK",
)

# Billing cycles.
BILLING_CYCLES = ("MONTHLY", "YEARLY", "ONETIME")


def _shared_connection():
    """The process-wide warehouse connection."""
    from api.db import get_connection as _shared

    return _shared()


#: How many read phases are open on this thread (0 = none).  A nested read
#: phase must not start a second transaction: DuckDB refuses to begin a
#: transaction inside one.
_tx_state = threading.local()


def _tx_depth() -> int:
    """The number of read phases open on this thread."""
    return int(getattr(_tx_state, "depth", 0))


class _SharedWarehouse:
    """A serialised view of the shared warehouse connection.

    The billing code opens the warehouse with ``with get_connection() as
    con:``.  The shared connection is process-wide and not thread-safe, so
    the whole block runs under the app's query lock.  The block itself does
    **not** open a transaction: the read phase wraps its own reads in a
    rolled-back transaction (see :func:`readonly_phase`) and the writes
    that follow must persist, so they run outside any rolled-back
    transaction.
    """

    def __enter__(self):
        from api.db import _bi_query_lock

        _bi_query_lock.acquire()
        try:
            conn = _shared_connection()
        except BaseException:
            _bi_query_lock.release()
            raise
        self._lock = _bi_query_lock
        self._conn = conn
        return conn

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


def get_connection():
    """The shared warehouse connection.

    Usage is unchanged (``with get_connection() as con:``); the returned
    object opens the **process-wide** connection, serialised by the app's
    query lock.
    """
    return _SharedWarehouse()


@contextmanager
def readonly_phase(conn) -> Iterator[Any]:
    """Run a read inside a transaction that is always rolled back.

    A rolled-back transaction cannot persist, so a stray write cannot
    either.  When a read phase is already open on this thread the read
    simply joins it (a second ``BEGIN`` would fail).
    """
    from etl.readonly import readonly_transaction

    depth = _tx_depth()
    if depth > 0:
        _tx_state.depth = depth + 1
        try:
            yield conn
        finally:
            _tx_state.depth = depth
        return
    with readonly_transaction(conn):
        _tx_state.depth = 1
        try:
            yield conn
        finally:
            _tx_state.depth = 0


@contextmanager
def readonly_connection() -> Iterator[Any]:
    """The shared warehouse connection for a read-only statement.

    The statement runs inside a transaction that is always rolled back and
    the whole window holds the app's query lock (the shared connection is
    not thread-safe).
    """
    from api.db import _bi_query_lock

    with _bi_query_lock:
        conn = _shared_connection()
        with readonly_phase(conn):
            yield conn


def _ensure_schema(con) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")


def init_billing_tables() -> None:
    """Create all billing tables if they don't exist. Idempotent."""
    try:
        with get_connection() as con:
            _ensure_schema(con)

            # ── Plans ────────────────────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.plans (
                    id VARCHAR PRIMARY KEY,
                    code VARCHAR UNIQUE NOT NULL,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    is_active BOOLEAN DEFAULT TRUE,
                    is_visible BOOLEAN DEFAULT TRUE,
                    is_recommended BOOLEAN DEFAULT FALSE,
                    display_order INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Plan prices (immutable history) ──────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.plan_prices (
                    id VARCHAR PRIMARY KEY,
                    plan_id VARCHAR NOT NULL,
                    billing_cycle VARCHAR NOT NULL,
                    amount INT NOT NULL,
                    currency VARCHAR DEFAULT 'USD',
                    amount_provider VARCHAR,
                    is_active BOOLEAN DEFAULT TRUE,
                    effective_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    effective_to TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Plan features (entitlement flags) ────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.plan_features (
                    id VARCHAR PRIMARY KEY,
                    plan_id VARCHAR NOT NULL,
                    feature_key VARCHAR NOT NULL,
                    enabled BOOLEAN DEFAULT FALSE,
                    UNIQUE(plan_id, feature_key)
                )
            """)

            # ── Plan limits (quotas; -1 = unlimited) ─────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.plan_limits (
                    id VARCHAR PRIMARY KEY,
                    plan_id VARCHAR NOT NULL,
                    metric VARCHAR NOT NULL,
                    limit_value INT NOT NULL,
                    UNIQUE(plan_id, metric)
                )
            """)

            # ── Subscriptions ────────────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.subscriptions (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    plan_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'ACTIVE',
                    billing_cycle VARCHAR,
                    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    current_period_start TIMESTAMP,
                    current_period_end TIMESTAMP,
                    cancel_at_period_end BOOLEAN DEFAULT FALSE,
                    cancelled_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    trial_ends_at TIMESTAMP,
                    payment_provider VARCHAR,
                    provider_customer_id VARCHAR,
                    provider_subscription_id VARCHAR,
                    email VARCHAR,
                    sso_subject VARCHAR,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Google identity columns — idempotent for pre-existing tables.
            for _col, _typ in (("email", "VARCHAR"), ("sso_subject", "VARCHAR"),
                               ("grant_nonce", "VARCHAR"), ("grant_signature", "VARCHAR")):
                try:
                    con.execute(
                        f"ALTER TABLE {SCHEMA}.subscriptions ADD COLUMN IF NOT EXISTS {_col} {_typ}"
                    )
                except Exception:  # noqa: BLE001
                    pass

            # Provider-agnostic column renames — idempotent for tables created
            # before the Razorpay migration (stripe_* -> provider_*).
            for _table, _old, _new in (
                ("plan_prices", "amount_stripe", "amount_provider"),
                ("subscriptions", "stripe_customer_id", "provider_customer_id"),
                ("subscriptions", "stripe_subscription_id", "provider_subscription_id"),
            ):
                try:
                    con.execute(
                        f"ALTER TABLE {SCHEMA}.{_table} RENAME COLUMN {_old} TO {_new}"
                    )
                except Exception:  # noqa: BLE001
                    pass

            # ── Subscription events (state machine audit) ────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.subscription_events (
                    id VARCHAR PRIMARY KEY,
                    subscription_id VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    from_status VARCHAR,
                    to_status VARCHAR,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Payments ─────────────────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.payments (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    subscription_id VARCHAR,
                    provider VARCHAR DEFAULT 'stripe',
                    provider_payment_id VARCHAR,
                    provider_order_id VARCHAR,
                    amount INT NOT NULL,
                    currency VARCHAR DEFAULT 'USD',
                    status VARCHAR NOT NULL DEFAULT 'CREATED',
                    coupon_id VARCHAR,
                    refunded_amount INT DEFAULT 0,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Payment events (webhook idempotency) ─────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.payment_events (
                    id VARCHAR PRIMARY KEY,
                    payment_id VARCHAR,
                    provider VARCHAR NOT NULL,
                    provider_event_id VARCHAR UNIQUE NOT NULL,
                    type VARCHAR NOT NULL,
                    raw_payload JSON,
                    processed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Invoices ─────────────────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.invoices (
                    id VARCHAR PRIMARY KEY,
                    invoice_number VARCHAR UNIQUE NOT NULL,
                    user_id VARCHAR NOT NULL,
                    payment_id VARCHAR,
                    subscription_id VARCHAR,
                    amount INT NOT NULL,
                    tax_amount INT DEFAULT 0,
                    currency VARCHAR DEFAULT 'USD',
                    status VARCHAR NOT NULL,
                    file_url VARCHAR,
                    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Usage records (per user/metric/period) ───────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.usage_records (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    subscription_id VARCHAR,
                    metric VARCHAR NOT NULL,
                    period VARCHAR NOT NULL,
                    used INT DEFAULT 0,
                    limit_snapshot INT,
                    tenant_id VARCHAR DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, metric, period)
                )
            """)

            # The tenant column was added after the first release: the
            # per-tenant AI budget aggregates the usage of every account of
            # the tenant.  Idempotent for pre-existing tables.
            try:
                con.execute(
                    f"ALTER TABLE {SCHEMA}.usage_records "
                    "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR DEFAULT ''"
                )
            except Exception:  # noqa: BLE001
                pass

            # ── Usage events (idempotent metering log) ───────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.usage_events (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    metric VARCHAR NOT NULL,
                    amount INT DEFAULT 1,
                    idempotency_key VARCHAR UNIQUE,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Checkout orders (legacy crypto / manual no-KYC payments) ─
            # Kept for backward-compatibility with existing databases; no
            # code writes to this table anymore (Stripe tracks its own
            # orders server-side).
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.checkout_orders (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    method VARCHAR NOT NULL,
                    plan VARCHAR NOT NULL,
                    billing_cycle VARCHAR NOT NULL,
                    email VARCHAR,
                    sso_subject VARCHAR,
                    network VARCHAR,
                    address VARCHAR,
                    amount_smallest INT NOT NULL,
                    currency VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'PENDING',
                    tx_id VARCHAR,
                    paid_amount INT,
                    expiry TIMESTAMP,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Coupons ──────────────────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.coupons (
                    id VARCHAR PRIMARY KEY,
                    code VARCHAR UNIQUE NOT NULL,
                    type VARCHAR NOT NULL,
                    amount_off INT,
                    percent_off INT,
                    trial_extra_days INT,
                    plan_id VARCHAR,
                    max_redemptions INT,
                    times_redeemed INT DEFAULT 0,
                    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_until TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Coupon redemptions ───────────────────────────────────
            con.execute(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.coupon_redemptions (
                    id VARCHAR PRIMARY KEY,
                    coupon_id VARCHAR NOT NULL,
                    user_id VARCHAR NOT NULL,
                    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(coupon_id, user_id)
                )
            """)

            # ── One-time migration: sign pre-existing local grants ───
            # Rows written before grant signing existed carry no
            # ``grant_signature`` and would be refused by the entitlement
            # guard. Sign them once, from their own current values, so real
            # customers are not silently dropped to the free plan.
            try:
                _migrate_unsigned_grants(con)
            except Exception as exc:  # noqa: BLE001
                logger.debug("grant migration skipped: %s", exc)

            logger.info("Billing tables initialized")
    except Exception as e:  # noqa: BLE001
        logger.warning("Billing schema init skipped: %s", e)


def _migrate_unsigned_grants(con) -> int:
    """Sign subscription rows that predate grant signing.

    Returns the number of rows signed.

    The "already migrated" marker lives in a **file outside the database**
    (``data/.grant_migrated``), not in a table. If the marker lived in the
    warehouse, an attacker could delete both a tampered row's signature and
    the marker, and the next start would re-sign the tampered row — turning
    the migration into a bypass. Keeping the marker next to the signing key
    means only the database can be edited by the attacker, and the migration
    can therefore never be re-triggered by them.
    """
    import os

    from api.billing.grant_signature import (
        grant_key_path,
        new_grant_nonce,
        sign_grant,
    )

    marker = os.path.join(os.path.dirname(grant_key_path()), ".grant_migrated")
    if os.path.exists(marker):
        return 0

    rows = con.execute(
        f"""SELECT id, user_id, plan_id, payment_provider, grant_nonce,
                   grant_signature, current_period_end, expires_at, trial_ends_at
            FROM {SCHEMA}.subscriptions
            WHERE grant_signature IS NULL OR grant_signature = ''"""
    ).fetchall()
    signed = 0
    for row in rows:
        (sub_id, user_id, plan_id, provider, nonce, signature,
         period_end, expires_at, trial_ends_at) = row
        if signature:
            continue
        if not nonce:
            nonce = new_grant_nonce()
            con.execute(
                f"UPDATE {SCHEMA}.subscriptions SET grant_nonce = ? WHERE id = ?",
                [nonce, sub_id],
            )
        new_sig = sign_grant(
            str(user_id or ""), str(plan_id or ""), str(provider or ""),
            nonce, period_end, expires_at, trial_ends_at,
        )
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET grant_signature = ? WHERE id = ?",
            [new_sig, sub_id],
        )
        signed += 1

    # Write the marker last, so a crash mid-migration re-runs it (harmless —
    # already-signed rows are skipped) rather than leaving rows unsigned.
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("1")
    except OSError as exc:
        logger.debug("grant migration marker not written: %s", exc)
    if signed:
        logger.info("Signed %d pre-existing subscription grant(s)", signed)
    return signed


def current_period() -> str:
    """Return the current usage period as YYYY-MM."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return f"{now.year}-{now.month:02d}"