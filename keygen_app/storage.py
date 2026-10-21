"""DuckDB write layer for the Key Manager GUI.

Writes an activation to all three places the app reads entitlements from:

1. analytics.premium_settings  -> global license (license_key + license_status)
2. analytics.user_subscriptions -> legacy per-user plan
3. analytics.subscriptions      -> canonical billing row (write-through, so
   api/billing/entitlements resolves the plan) + subscription_events audit

DuckDB is single-writer: the running web app holds the warehouse open, so all
writes retry a few times and surface a clear "close the running app" message
if the lock cannot be acquired.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

SCHEMA = "analytics"
PERIOD_DAYS = {"MONTHLY": 31, "YEARLY": 365, "ONETIME": 3650}

# The runtime data directory is resolved LAZILY (at call time), not at import
# time, so that a caller which sets $DATAAGENT_DATA_DIR after this module was
# imported still gets the right answer.
#
# Why this is not simply ``Path(__file__).parent.parent / "data" / ...``:
# in the frozen Windows desktop build ``__file__`` points into the PyInstaller
# bundle (``<install>\\ _internal\\keygen_app\\storage.py``), so the warehouse
# would resolve to a copy inside Program Files — unwritable by a normal user,
# and NOT the warehouse the app actually uses. Activating a paid licence
# would then fail with a lock error against the wrong file.
try:  # pragma: no cover - the fallback is for a standalone keygen checkout
    from api.runtime_paths import runtime_file
except Exception:  # noqa: BLE001

    def runtime_file(*parts: str) -> Path:
        """Standalone fallback: resolve relative to the project root."""
        return Path(__file__).parent.parent.joinpath(*parts)


def default_db() -> Path:
    """The warehouse the Key Manager writes activations into."""
    return runtime_file("data", "curated", "warehouse.duckdb")


def local_state_db() -> Path:
    """The Key Manager's own local state (pending licence sync)."""
    return runtime_file("local_state.db")


WRITE_RETRIES = 3
RETRY_DELAY_S = 1.0


class WarehouseLockedError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_end(billing_cycle: str) -> str:
    days = PERIOD_DAYS.get((billing_cycle or "MONTHLY").upper(), 31)
    return (datetime.now(timezone.utc) + __import__("datetime").timedelta(days=days)).isoformat()


def _hash_key(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def _with_retry(db_path: Path, fn):
    """Run fn(con) with retries; raise WarehouseLockedError on persistent lock."""
    last_err: Optional[Exception] = None
    for attempt in range(1, WRITE_RETRIES + 1):
        try:
            con = _connect(db_path)
            try:
                return fn(con)
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < WRITE_RETRIES:
                time.sleep(RETRY_DELAY_S * attempt)
    raise WarehouseLockedError(
        "Could not write to the warehouse — the running DataAgent app holds the "
        "database open. Close the app (or stop the server) and try again. "
        f"Last error: {last_err}"
    ) from last_err


def _upsert_setting(con, key: str, value: str) -> None:
    """Write a premium_settings row without relying on a PK/UNIQUE constraint.

    The DataAgent app strips PRIMARY KEY / UNIQUE constraints from analytics
    tables on boot (api/db.strip_constraint_indexes, indexed-DELETE safety), so
    ON CONFLICT / INSERT OR REPLACE would either fail or insert duplicates.
    SELECT-then-INSERT/UPDATE is constraint-agnostic and matches the app's own
    premium.py _set_setting pattern.
    """
    row = con.execute(
        f"SELECT 1 FROM {SCHEMA}.premium_settings WHERE key = ?", [key]
    ).fetchone()
    if row:
        con.execute(
            f"UPDATE {SCHEMA}.premium_settings SET value = ? WHERE key = ?",
            [value, key],
        )
    else:
        con.execute(
            f"INSERT INTO {SCHEMA}.premium_settings (key, value) VALUES (?, ?)",
            [key, value],
        )


def _ensure_tables(con) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.premium_settings (
            key VARCHAR PRIMARY KEY,
            value VARCHAR
        )
    """)
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
            grant_nonce VARCHAR,
            grant_signature VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Idempotent column additions for tables created before grant signing.
    for _col in ("grant_nonce", "grant_signature"):
        try:
            con.execute(
                f"ALTER TABLE {SCHEMA}.subscriptions ADD COLUMN IF NOT EXISTS {_col} VARCHAR"
            )
        except Exception:  # noqa: BLE001
            pass
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


def _log_event(con, subscription_id: str, event_type: str, to_status: str,
               metadata: Optional[dict] = None) -> None:
    con.execute(
        f"""INSERT INTO {SCHEMA}.subscription_events
            (id, subscription_id, type, from_status, to_status, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), subscription_id, event_type, None, to_status,
         json.dumps(metadata) if metadata else None, _now_iso()],
    )


def _ensure_billing_row(con, user_id: str, plan: str, expires_at: Optional[str]) -> str:
    """Mirror api/billing/subs.py ensure_billing_row for ONETIME grants."""
    from api.billing.grant_signature import new_grant_nonce

    plan_id = f"plan_{plan}"
    now = _now_iso()
    row = con.execute(
        f"""SELECT id FROM {SCHEMA}.subscriptions
            WHERE user_id = ? AND plan_id = ? AND status IN ('ACTIVE', 'TRIALING', 'PAST_DUE', 'CANCEL_AT_PERIOD_END')
            ORDER BY created_at DESC LIMIT 1""",
        [user_id, plan_id],
    ).fetchone()
    if row:
        sub_id = row[0]
        sets = ["status = ?", "billing_cycle = ?", "updated_at = ?"]
        params = ["ACTIVE", "ONETIME", now]
        if expires_at:
            sets.append("current_period_end = ?")
            params.append(expires_at)
        else:
            sets.append("current_period_end = NULL")
        params.append(sub_id)
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        _resign(con, sub_id)
        _log_event(con, sub_id, "updated", "ACTIVE",
                   metadata={"plan": plan, "billing_cycle": "ONETIME", "source": "keygen"})
        return sub_id

    sub_id = str(uuid.uuid4())
    end = expires_at or None  # None => never expires (lifetime)
    nonce = new_grant_nonce()
    con.execute(
        f"""INSERT INTO {SCHEMA}.subscriptions
            (id, user_id, plan_id, status, billing_cycle, start_date,
             current_period_start, current_period_end, cancel_at_period_end,
             payment_provider, grant_nonce, grant_signature, created_at, updated_at)
            VALUES (?, ?, ?, 'ACTIVE', 'ONETIME', ?, ?, ?, FALSE, 'keygen', ?, ?, ?, ?)""",
        [sub_id, user_id, plan_id, now, now, end, nonce, "", now, now],
    )
    _sign_row(con, sub_id, user_id, plan_id, "keygen", end, None, nonce)
    _log_event(con, sub_id, "created", "ACTIVE",
               metadata={"plan": plan, "billing_cycle": "ONETIME", "source": "keygen"})
    return sub_id


def _sign_row(con, sub_id: str, user_id: str, plan_id: str, provider: str,
              period_end: Optional[str], expires_at: Optional[str],
              nonce: str) -> None:
    """Stamp the grant signature for a keygen-issued subscription row."""
    try:
        from api.billing.grant_signature import sign_grant

        signature = sign_grant(
            user_id, plan_id, provider, nonce, period_end, expires_at,
        )
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET grant_signature = ? WHERE id = ?",
            [signature, sub_id],
        )
    except Exception:  # noqa: BLE001
        pass


def _resign(con, sub_id: str) -> None:
    """Refresh the grant signature for an existing row from its current values."""
    try:
        from api.billing.grant_signature import new_grant_nonce, sign_grant

        row = con.execute(
            f"""SELECT user_id, plan_id, payment_provider, grant_nonce,
                       current_period_end, expires_at
                FROM {SCHEMA}.subscriptions WHERE id = ?""",
            [sub_id],
        ).fetchone()
        if not row:
            return
        nonce = row[3] or new_grant_nonce()
        if not row[3]:
            con.execute(
                f"UPDATE {SCHEMA}.subscriptions SET grant_nonce = ? WHERE id = ?",
                [nonce, sub_id],
            )
        signature = sign_grant(row[0], row[1], row[2] or "", nonce, row[4], row[5])
        con.execute(
            f"UPDATE {SCHEMA}.subscriptions SET grant_signature = ? WHERE id = ?",
            [signature, sub_id],
        )
    except Exception:  # noqa: BLE001
        pass


def _sync_legacy(con, user_id: str, plan: str, expires_at: Optional[str]) -> None:
    now = _now_iso()
    row = con.execute(
        f"SELECT plan FROM {SCHEMA}.user_subscriptions WHERE user_id = ?",
        [user_id],
    ).fetchone()
    if row:
        con.execute(
            f"""UPDATE {SCHEMA}.user_subscriptions
                SET plan = ?, status = 'active', expires_at = ?, updated_at = ?
                WHERE user_id = ?""",
            [plan, expires_at, now, user_id],
        )
    else:
        con.execute(
            f"""INSERT INTO {SCHEMA}.user_subscriptions
                (user_id, plan, status, started_at, expires_at, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?)""",
            [user_id, plan, now, expires_at, now],
        )


def activate(
    user_id: str,
    plan: str,
    license_key: str,
    expires_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Write a full activation: premium_settings + user_subscriptions + billing.

    expires_at None => lifetime (real NULL in the DB, "Never expires" in UI).
    """
    db_path = db_path or default_db()
    license_obj = {
        "valid": True,
        "plan": plan,
        "status": "active",
        "expiresAt": expires_at,
        "cancelAtPeriodEnd": False,
        "keyHash": _hash_key(license_key),
    }

    def _do(con):
        _ensure_tables(con)
        _upsert_setting(con, "license_key", license_key)
        _upsert_setting(con, "license_status", json.dumps(license_obj))
        _sync_legacy(con, user_id, plan, expires_at)
        sub_id = _ensure_billing_row(con, user_id, plan, expires_at)
        return {"sub_id": sub_id, "license": license_obj}

    return _with_retry(db_path, _do)


# ── Local pending-sync state (SQLite, lives with the GUI) ──────────────


def _local_connect() -> sqlite3.Connection:
    state_db = local_state_db()
    state_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(state_db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS pending_sync (
            key_hash     VARCHAR PRIMARY KEY,
            license_key  VARCHAR NOT NULL,
            user_id      VARCHAR NOT NULL,
            plan         VARCHAR NOT NULL,
            expires_at   VARCHAR,
            redeemed_at  VARCHAR NOT NULL,
            synced       BOOLEAN NOT NULL DEFAULT FALSE,
            sync_attempts INT NOT NULL DEFAULT 0,
            last_error   VARCHAR
        )
        """
    )
    return con


def queue_pending_sync(user_id: str, plan: str, license_key: str,
                       expires_at: Optional[str]) -> None:
    import hashlib
    key_hash_val = hashlib.sha256(license_key.encode()).hexdigest()[:16]
    con = _local_connect()
    try:
        con.execute(
            """INSERT INTO pending_sync (key_hash, license_key, user_id, plan, expires_at, redeemed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (key_hash) DO UPDATE SET
                 license_key = excluded.license_key, user_id = excluded.user_id,
                 plan = excluded.plan, expires_at = excluded.expires_at,
                 synced = FALSE, sync_attempts = 0""",
            [key_hash_val, license_key, user_id, plan, expires_at, _now_iso()],
        )
        con.commit()
    finally:
        con.close()


def pending_sync_rows() -> list[dict]:
    con = _local_connect()
    try:
        rows = con.execute(
            "SELECT * FROM pending_sync WHERE synced = FALSE ORDER BY redeemed_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def mark_synced(key_hash_val: str) -> None:
    con = _local_connect()
    try:
        con.execute(
            "UPDATE pending_sync SET synced = TRUE, sync_attempts = sync_attempts + 1 WHERE key_hash = ?",
            [key_hash_val],
        )
        con.commit()
    finally:
        con.close()


def mark_sync_failed(key_hash_val: str, error: str) -> None:
    con = _local_connect()
    try:
        con.execute(
            "UPDATE pending_sync SET sync_attempts = sync_attempts + 1, last_error = ? WHERE key_hash = ?",
            [error, key_hash_val],
        )
        con.commit()
    finally:
        con.close()