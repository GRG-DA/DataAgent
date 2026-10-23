"""Durable store for orders/payments/keys — SQLite (WAL), plus Turso/libSQL.

The billing server never touches the DataAgent DuckDB warehouse (single-writer
rule). SQLite is more than enough at this volume and keeps deployment trivial.
Hosted deployments can point the same store at a remote Turso (libSQL)
database via TURSO_DATABASE_URL + TURSO_AUTH_TOKEN; the adapter in
``billing_server/libsql_adapter.py`` bridges the sqlite3/sqlite-compatible
surface this module relies on.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import billing_server.config as cfg

_lock = threading.Lock()
#: Targets (local DB path or remote URL) whose schema is already ensured in
#: this process. Remote targets must not pay the full DDL round trip on every
#: single store call; all callers hold _lock, so this is race-free.
_initialized_targets: set[str] = set()


def _target() -> str:
    return cfg.TURSO_DATABASE_URL or str(cfg.DB_PATH)


def _connect() -> Any:
    if cfg.TURSO_DATABASE_URL:
        from billing_server.libsql_adapter import connect as _libsql_connect
        return _libsql_connect(cfg.TURSO_DATABASE_URL, cfg.TURSO_AUTH_TOKEN)
    return _connect_local()


def _connect_local() -> sqlite3.Connection:
    Path(cfg.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(cfg.DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


#: Schema DDL, executed statement-by-statement (the remote adapter has no
#: executescript) on the first connection per target in this process.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS orders (
        order_id      TEXT PRIMARY KEY,
        payment_id    TEXT,
        email         TEXT NOT NULL,
        plan          TEXT NOT NULL,
        amount        INTEGER NOT NULL,
        currency      TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'created',
        license_key   TEXT,
        key_hash      TEXT,
        notes         TEXT,
        provider      TEXT NOT NULL DEFAULT 'razorpay',
        grant_days    INTEGER,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email)",
    "CREATE INDEX IF NOT EXISTS idx_orders_payment ON orders(payment_id)",
    """CREATE TABLE IF NOT EXISTS events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kind       TEXT NOT NULL,
        ref_id     TEXT NOT NULL,
        payload    TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(kind, ref_id)
    )""",
    """CREATE TABLE IF NOT EXISTS failures (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kind       TEXT NOT NULL,
        ref_id     TEXT NOT NULL,
        error      TEXT,
        created_at TEXT NOT NULL
    )""",
    # One-time redemption registry: the first account to redeem a key
    # owns it; every other account is refused.
    """CREATE TABLE IF NOT EXISTS redemptions (
        key_hash    TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        order_id    TEXT,
        redeemed_at TEXT NOT NULL
    )""",
    # Feature-ticket audit (P3/P5): every server-authorized paid action.
    # Same key/token from many emails/accounts becomes visible here.
    """CREATE TABLE IF NOT EXISTS feature_uses (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        email      TEXT NOT NULL,
        feature    TEXT NOT NULL,
        plan       TEXT NOT NULL,
        key_hash   TEXT,
        user_id    TEXT,
        ip         TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_feature_uses_email ON feature_uses(email)",
    "CREATE INDEX IF NOT EXISTS idx_feature_uses_key ON feature_uses(key_hash)",
    # Central accounts (ADR-002): email + bcrypt hash ONLY. Purchases
    # stay in orders; no user file data ever lands here.
    """CREATE TABLE IF NOT EXISTS accounts (
        user_id       TEXT PRIMARY KEY,
        email         TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'active',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email
        ON accounts(email COLLATE NOCASE)""",
    # Auto-renewing Pro subscriptions (Razorpay Subscriptions): one row per
    # sub_… id mirroring status + paid-through date. Folding rows into the
    # current entitlement happens in billing_server.grants — this table is
    # the durable source of truth (Turso-hosted in production).
    """CREATE TABLE IF NOT EXISTS subscriptions (
        subscription_id      TEXT PRIMARY KEY,
        email                TEXT NOT NULL,
        plan                 TEXT NOT NULL,
        status               TEXT NOT NULL DEFAULT 'created',
        current_end          TEXT,
        cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
        payment_id           TEXT,
        notes                TEXT,
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_email ON subscriptions(email)",
)


def _ensure_schema(con) -> None:
    target = _target()
    if target in _initialized_targets:
        return
    for statement in _SCHEMA_STATEMENTS:
        con.execute(statement)
    # Idempotent widening for pre-P3 databases (CREATE TABLE IF NOT EXISTS
    # never adds columns).
    for _col in ("user_id TEXT", "ip TEXT"):
        try:
            con.execute(f"ALTER TABLE feature_uses ADD COLUMN {_col}")
        except Exception:  # noqa: BLE001 — already exists
            pass
    # Per-order paid window (days). NULL = perpetual (lifetime) or not yet
    # classified; legacy pro orders are backfilled to their original 31 days.
    try:
        con.execute("ALTER TABLE orders ADD COLUMN grant_days INTEGER")
    except Exception:  # noqa: BLE001 — already exists
        pass
    # Payment rail that issued the order (ADR-016): legacy rows are Razorpay.
    try:
        con.execute("ALTER TABLE orders ADD COLUMN provider TEXT DEFAULT 'razorpay'")
    except Exception:  # noqa: BLE001 — already exists
        pass
    try:
        con.execute(
            "UPDATE orders SET provider = 'razorpay' WHERE provider IS NULL")
    except Exception:  # noqa: BLE001 — classification is best-effort
        pass
    # A FamGateway UTR pays exactly one order, ever: a second webhook claiming
    # the same UTR against a different order can never double-grant (the
    # handler resolves owners softly first; this index is the backstop).
    try:
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_utr_unique "
            "ON orders(payment_id) "
            "WHERE provider = 'famgateway' AND payment_id IS NOT NULL")
    except Exception:  # noqa: BLE001 — already exists / legacy schema mid-upgrade
        pass
    _initialized_targets.add(target)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_order(order_id: str, email: str, plan: str, amount: int,
                 currency: str, notes: Optional[dict] = None,
                 provider: Optional[str] = None,
                 grant_days: Optional[int] = None) -> None:
    """Create (or no-op on duplicate) an order row for one payment attempt.

    ``provider`` classifies the rail (ADR-016); ``grant_days`` pre-records the
    paid window (31 days for famgateway Pro; NULL = perpetual/unknown).
    """
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute(
            """INSERT INTO orders (order_id, email, plan, amount, currency, status, notes,
                                   provider, grant_days, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'created', ?, COALESCE(?, 'razorpay'), ?, ?, ?)
               ON CONFLICT(order_id) DO NOTHING""",
            [order_id, email.lower(), plan, amount, currency,
             json.dumps(notes) if notes else None,
             provider or "razorpay", grant_days, _now(), _now()],
        )


def mark_paid(order_id: str, payment_id: str,
              grant_days: Optional[int] = None) -> Optional[dict]:
    """Set status='paid' once; returns the row dict, or None if unknown order.

    ``grant_days`` records the paid window for one-time orders (legacy pro
    keeps its original 31 days). None never clears an existing value, so
    retried webhooks and lifetime orders stay intact.
    """
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute(
            """UPDATE orders SET payment_id = ?, status = 'paid',
                      grant_days = COALESCE(?, grant_days), updated_at = ?
               WHERE order_id = ?""",
            [payment_id, grant_days, _now(), order_id],
        )
        row = con.execute("SELECT * FROM orders WHERE order_id = ?", [order_id]).fetchone()
        return dict(row) if row else None


def attach_key(order_id: str, license_key: str, key_hash_val: str) -> None:
    """Store the minted key (idempotent: first mint wins)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute(
            """UPDATE orders SET license_key = COALESCE(license_key, ?),
               key_hash = ?, updated_at = ? WHERE order_id = ?""",
            [license_key, key_hash_val, _now(), order_id],
        )


def get_order(order_id: str) -> Optional[dict]:
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute("SELECT * FROM orders WHERE order_id = ?", [order_id]).fetchone()
        return dict(row) if row else None


def latest_order_for_email(email: str) -> Optional[dict]:
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            """SELECT * FROM orders WHERE email = ? AND status = 'paid'
               ORDER BY updated_at DESC LIMIT 1""",
            [email.lower()],
        ).fetchone()
        return dict(row) if row else None


def paid_orders_for_email(email: str) -> list[dict]:
    """All paid (not refunded) orders for an email, newest first."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT * FROM orders WHERE email = ? AND status = 'paid'
               ORDER BY updated_at DESC""",
            [email.lower()],
        ).fetchall()
        return [dict(r) for r in rows]


def refund_order(payment_id: str) -> Optional[dict]:
    """Mark the order refunded (drives entitlement revocation)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM orders WHERE payment_id = ? ORDER BY updated_at DESC LIMIT 1",
            [payment_id],
        ).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE orders SET status = 'refunded', updated_at = ? WHERE order_id = ?",
            [_now(), row["order_id"]],
        )
        return dict(row)


def get_redemption(key_hash_val: str) -> Optional[dict]:
    """Return the redemption row for a key hash, or None if never redeemed."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM redemptions WHERE key_hash = ?", [key_hash_val]
        ).fetchone()
        return dict(row) if row else None


def redeem_key(key_hash_val: str, email: str, user_id: str) -> dict:
    """Claim a key for an account (one-time use).

    Returns one of:
      {"status": "redeemed"}       first account claimed it
      {"status": "already_owner"}  the same account re-activating
      {"status": "conflict"}       another account already claimed it
      {"status": "unknown"}        key was not issued/paid here
      {"status": "refunded"}       the order was refunded
    """
    email = (email or "").lower()
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM redemptions WHERE key_hash = ?", [key_hash_val]
        ).fetchone()
        if row:
            if str(row["user_id"]) == str(user_id):
                return {"status": "already_owner", "order_id": row["order_id"]}
            return {"status": "conflict"}
        order = con.execute(
            """SELECT * FROM orders WHERE key_hash = ?
               ORDER BY updated_at DESC LIMIT 1""",
            [key_hash_val],
        ).fetchone()
        if not order or not order["license_key"] or order["status"] != "paid":
            if order and order["status"] == "refunded":
                return {"status": "refunded"}
            return {"status": "unknown"}
        con.execute(
            """INSERT INTO redemptions (key_hash, email, user_id, order_id, redeemed_at)
               VALUES (?, ?, ?, ?, ?)""",
            [key_hash_val, email, user_id, order["order_id"], _now()],
        )
        return {"status": "redeemed", "order_id": order["order_id"]}


def get_order_by_key_hash(key_hash_val: str) -> Optional[dict]:
    """Newest order carrying a key hash (for feature-ticket key mode)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            """SELECT * FROM orders WHERE key_hash = ?
               ORDER BY updated_at DESC LIMIT 1""",
            [key_hash_val],
        ).fetchone()
        return dict(row) if row else None


def list_registry(limit: int = 200) -> list[dict]:
    """Orders left-joined with their redemption — data for the support view."""
    limit = max(1, min(int(limit or 200), 1000))
    with _lock, _connect() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT o.order_id, o.payment_id, o.email, o.plan, o.amount, o.currency,
                      o.status, o.license_key, o.key_hash, o.created_at, o.updated_at,
                      r.user_id AS redeemed_by, r.redeemed_at
               FROM orders o
               LEFT JOIN redemptions r ON r.key_hash = o.key_hash
               ORDER BY o.updated_at DESC LIMIT ?""",
            [limit],
        ).fetchall()
        return [dict(r) for r in rows]


def registry_counts() -> dict:
    """Accurate totals for the support view summary line."""
    with _lock, _connect() as con:
        _ensure_schema(con)

        def one(sql: str) -> int:
            return con.execute(sql).fetchone()[0]

        return {
            "orders": one("SELECT COUNT(*) FROM orders"),
            "paid": one("SELECT COUNT(*) FROM orders WHERE status = 'paid'"),
            "refunded": one("SELECT COUNT(*) FROM orders WHERE status = 'refunded'"),
            "redeemed": one("SELECT COUNT(*) FROM redemptions"),
        }


def resolve_utr_owner(utr: str) -> Optional[str]:
    """Order id that already paid a FamGateway UTR (UTR-replay guard).

    None when unknown/unpaid; the assumption is that a UTR (bank reference)
    identifies exactly one real-world payment.
    """
    if not utr:
        return None
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            """SELECT order_id FROM orders
               WHERE payment_id = ? AND status = 'paid'
               ORDER BY updated_at DESC LIMIT 1""",
            [utr],
        ).fetchone()
        return dict(row)["order_id"] if row else None


def seen_event(kind: str, ref_id: str) -> bool:
    """True if this event was already processed (webhook idempotency)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT 1 FROM events WHERE kind = ? AND ref_id = ?", [kind, ref_id]
        ).fetchone()
        if row:
            return True
        con.execute(
            "INSERT INTO events (kind, ref_id, created_at) VALUES (?, ?, ?)",
            [kind, ref_id, _now()],
        )
        return False


def record_event_failure(kind: str, ref_id: str, error: str) -> None:
    """Remove the event marker so a retry can reprocess a failed delivery."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute("DELETE FROM events WHERE kind = ? AND ref_id = ?", [kind, ref_id])
        con.execute(
            "INSERT INTO failures (kind, ref_id, error, created_at) VALUES (?, ?, ?, ?)",
            [kind, ref_id, error[:500], _now()],
        )


#: Retain the feature-use audit trail this long (P5 abuse review window).
FEATURE_USE_RETENTION_DAYS = 90


def log_feature_use(email: str, feature: str, plan: str,
                     key_hash_val: str = "", user_id: str = "",
                     ip: str = "") -> None:
    """Audit one server-authorized paid action (best-effort, never raises)."""
    try:
        with _lock, _connect() as con:
            _ensure_schema(con)
            con.execute(
                """INSERT INTO feature_uses (email, feature, plan, key_hash,
                                            user_id, ip, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [email.lower(), feature, plan, key_hash_val or None,
                 user_id or None, ip or None, _now()],
            )
            try:
                con.execute(
                    """DELETE FROM feature_uses
                       WHERE created_at < datetime('now', ?)""",
                    [f"-{FEATURE_USE_RETENTION_DAYS} days"],
                )
            except Exception:  # noqa: BLE001 — prune is best-effort
                pass
    except Exception:  # noqa: BLE001
        pass


def feature_use_counts(email: str = "", feature: str = "") -> dict:
    """Counts for tests/support: total uses, distinct emails per key hash."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        clauses, params = [], []
        if email:
            clauses.append("email = ?")
            params.append(email.lower())
        if feature:
            clauses.append("feature = ?")
            params.append(feature)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = con.execute(f"SELECT COUNT(*) FROM feature_uses {where}", params).fetchone()[0]
        emails = con.execute(
            f"SELECT COUNT(DISTINCT email) FROM feature_uses {where}", params
        ).fetchone()[0]
        return {"total": total, "distinct_emails": emails}


# ── Central accounts (ADR-002) ───────────────────────────────────


def create_account(email: str, password_hash: str) -> Optional[dict]:
    """Insert a new account. Returns the row dict, or None if the email exists."""
    email = (email or "").strip().lower()
    with _lock, _connect() as con:
        _ensure_schema(con)
        existing = con.execute(
            "SELECT 1 FROM accounts WHERE email = ?", [email]
        ).fetchone()
        if existing:
            return None
        user_id = uuid.uuid4().hex
        now = _now()
        con.execute(
            """INSERT INTO accounts (user_id, email, password_hash, status, created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?)""",
            [user_id, email, password_hash, now, now],
        )
        return {"user_id": user_id, "email": email, "status": "active",
                "created_at": now, "updated_at": now}


def get_account_by_email(email: str) -> Optional[dict]:
    email = (email or "").strip().lower()
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM accounts WHERE email = ?", [email]
        ).fetchone()
        return dict(row) if row else None


def get_account_by_id(user_id: str) -> Optional[dict]:
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM accounts WHERE user_id = ?", [user_id]
        ).fetchone()
        return dict(row) if row else None


def touch_account(user_id: str) -> None:
    """Bump updated_at on a successful login (last-seen signal)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute(
            "UPDATE accounts SET updated_at = ? WHERE user_id = ?",
            [_now(), user_id],
        )


# ── Auto-renewing subscriptions (Razorpay Subscriptions) ─────────


def upsert_subscription(subscription_id: str, email: str, plan: str,
                        status: str = "created",
                        current_end: Optional[str] = None,
                        payment_id: str = "",
                        notes: Optional[dict] = None) -> dict:
    """Insert or refresh a subscription row from checkout/webhook data.

    Updates never clear a previously recorded paid-through date, payment id,
    or the cancel-at-period-end flag — a stale or partial webhook must not
    roll the subscription's state backwards.
    """
    with _lock, _connect() as con:
        _ensure_schema(con)
        now = _now()
        con.execute(
            """INSERT INTO subscriptions (subscription_id, email, plan, status,
                                          current_end, payment_id, notes,
                                          created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(subscription_id) DO UPDATE SET
                 status = excluded.status,
                 current_end = COALESCE(excluded.current_end, subscriptions.current_end),
                 payment_id = COALESCE(excluded.payment_id, subscriptions.payment_id),
                 notes = COALESCE(excluded.notes, subscriptions.notes),
                 updated_at = excluded.updated_at""",
            [subscription_id, email.lower(), plan, status, current_end,
             payment_id or None, json.dumps(notes) if notes else None, now, now],
        )
        row = con.execute(
            "SELECT * FROM subscriptions WHERE subscription_id = ?", [subscription_id]
        ).fetchone()
        return dict(row)


def get_subscription(subscription_id: str) -> Optional[dict]:
    with _lock, _connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM subscriptions WHERE subscription_id = ?", [subscription_id]
        ).fetchone()
        return dict(row) if row else None


def subscriptions_for_email(email: str) -> list[dict]:
    """All subscription rows for an email (any status), newest first."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT * FROM subscriptions WHERE email = ?
               ORDER BY updated_at DESC""",
            [email.lower()],
        ).fetchall()
        return [dict(r) for r in rows]


def set_cancel_at_period_end(subscription_id: str, flag: bool) -> Optional[dict]:
    """Record the user's cancel-at-period-end choice (None if unknown sub)."""
    with _lock, _connect() as con:
        _ensure_schema(con)
        con.execute(
            """UPDATE subscriptions SET cancel_at_period_end = ?, updated_at = ?
               WHERE subscription_id = ?""",
            [1 if flag else 0, _now(), subscription_id],
        )
        row = con.execute(
            "SELECT * FROM subscriptions WHERE subscription_id = ?", [subscription_id]
        ).fetchone()
        return dict(row) if row else None


# ── Abuse blocks + sharing signals (P5) ──────────────────────────


def _ensure_abuse_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS abuse_blocks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,
            value      TEXT NOT NULL,
            reason     TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(kind, value)
        )"""
    )


def block_grant(kind: str, value: str, reason: str = "") -> None:
    """Block an abusive grant vector (kind email|key_hash). Idempotent."""
    kind = (kind or "").lower()
    if kind not in ("email", "key_hash"):
        raise ValueError("kind must be email|key_hash")
    value = value.lower() if kind == "email" else value
    with _lock, _connect() as con:
        _ensure_schema(con)
        _ensure_abuse_tables(con)
        con.execute(
            """INSERT INTO abuse_blocks (kind, value, reason, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(kind, value) DO UPDATE SET reason = excluded.reason""",
            [kind, value, reason[:300], _now()],
        )


def unblock_grant(kind: str, value: str) -> bool:
    """Remove a block. Returns True when a row was deleted."""
    kind = (kind or "").lower()
    value = value.lower() if kind == "email" else value
    with _lock, _connect() as con:
        _ensure_schema(con)
        _ensure_abuse_tables(con)
        cur = con.execute(
            "DELETE FROM abuse_blocks WHERE kind = ? AND value = ?", [kind, value]
        )
        return (cur.rowcount or 0) > 0


def is_blocked(kind: str, value: str) -> bool:
    kind = (kind or "").lower()
    value = value.lower() if kind == "email" else value
    try:
        with _lock, _connect() as con:
            _ensure_schema(con)
            _ensure_abuse_tables(con)
            row = con.execute(
                "SELECT 1 FROM abuse_blocks WHERE kind = ? AND value = ?",
                [kind, value],
            ).fetchone()
            return row is not None
    except Exception:  # noqa: BLE001 — fail open on store errors
        return False


def list_blocks() -> list[dict]:
    with _lock, _connect() as con:
        _ensure_schema(con)
        _ensure_abuse_tables(con)
        rows = con.execute(
            "SELECT kind, value, reason, created_at FROM abuse_blocks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def abuse_signals(hours: int = 24, min_user_ids: int = 3,
                  min_ips: int = 10) -> list[dict]:
    """Sharing/abuse signals over a recent window.

    Two shapes: one key_hash driving many user_ids (key sharing), and one
    email from many IPs (credential sharing / rotation abuse). Tuned via
    FEATURE_ABUSE_* env on the enforce endpoint.
    """
    with _lock, _connect() as con:
        _ensure_schema(con)
        _ensure_abuse_tables(con)
        rows = con.execute(
            """SELECT key_hash, email,
                      COUNT(*) AS uses,
                      COUNT(DISTINCT user_id) AS user_ids,
                      COUNT(DISTINCT ip) AS ips,
                      COUNT(DISTINCT email) AS emails
               FROM feature_uses
               WHERE created_at >= datetime('now', ?)
               GROUP BY key_hash, email""",
            [f"-{max(1, int(hours or 24))} hours"],
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            reasons = []
            if (d.get("key_hash") and (d.get("user_ids") or 0) >= min_user_ids):
                reasons.append(f"key shared across {d['user_ids']} accounts")
            if (d.get("ips") or 0) >= min_ips:
                reasons.append(f"{d['ips']} source IPs in window")
            if reasons:
                d["reasons"] = reasons
                out.append(d)
        return out
