"""PostgreSQL identity store — the hosted backend (ADR-009 D2, scale step).

``api/user_store.py`` keeps the user directory behind a narrow repository
interface so the store can be swapped without touching the callers.  This
module is the PostgreSQL implementation of that interface: the same schema,
the same semantics, a connection pool, and parameterised statements only.

The module imports cleanly **without** ``psycopg`` installed: the driver is
imported lazily, so a desktop install that never selects the PostgreSQL
backend does not need the extra dependency at all.  Install it with
``requirements-saas.txt``.

Nothing here logs or returns password hashes, salts or key material.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from api.user_store import (
    USER_COLUMNS,
    UserRecord,
    UserStore,
    _BOOL_COLUMNS,
    _coerce_row,
    _new_user_id,
    _normalise_email,
    _select_columns,
)

logger = logging.getLogger(__name__)

#: Default number of pooled connections.
DEFAULT_POOL_SIZE = 10

#: Default connect timeout, in seconds.
DEFAULT_CONNECT_TIMEOUT = 10


# ── the driver (imported lazily) ──────────────────────────────────────


def _psycopg():
    """Import ``psycopg`` (v3) on demand.

    The import is deliberately inside the function: the module must import
    cleanly on an install that has no PostgreSQL driver at all.
    """
    try:
        import psycopg  # noqa: PLC0415 — lazy on purpose
    except ImportError as exc:  # pragma: no cover — depends on the install
        raise RuntimeError(
            "psycopg (v3) is required for the PostgreSQL identity store. "
            "Install requirements-saas.txt, or select another backend with "
            "DATAAGENT_USER_STORE=sqlite."
        ) from exc
    return psycopg


def postgres_available() -> bool:
    """True when the PostgreSQL driver can be imported."""
    try:
        _psycopg()
    except Exception:  # noqa: BLE001
        return False
    return True


def postgres_dsn() -> str:
    """The configured PostgreSQL DSN.

    ``DATABASE_URL`` wins, then ``POSTGRES_DSN``, then ``DATABASE_DSN``.
    """
    for name in ("DATABASE_URL", "POSTGRES_DSN", "DATABASE_DSN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        from config import settings

        return (settings.postgres.url or settings.postgres.dsn or "").strip()
    except Exception:  # noqa: BLE001 — the config is optional
        return ""


def _pool_settings() -> Tuple[int, int]:
    """(pool size, connect timeout) from the configuration."""
    try:
        from config import settings

        return (
            max(1, int(settings.postgres.pool_size or DEFAULT_POOL_SIZE)),
            max(1, int(settings.postgres.connect_timeout or DEFAULT_CONNECT_TIMEOUT)),
        )
    except Exception:  # noqa: BLE001 — the config is optional
        return DEFAULT_POOL_SIZE, DEFAULT_CONNECT_TIMEOUT


# ── the schema ────────────────────────────────────────────────────────
#
# Column-for-column the same as the SQLite schema in ``api.user_store``:
# the store is swapped, the data model is not.  Booleans are real
# ``BOOLEAN`` columns here (SQLite stores them as 0/1 integers), everything
# else keeps the SQLite type.

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    salt TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer',
    email TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT '',
    sso_subject TEXT NOT NULL DEFAULT '',
    sso_provider TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    api_key TEXT NOT NULL DEFAULT '',
    mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_secret TEXT NOT NULL DEFAULT '',
    password_changed_at TEXT NOT NULL DEFAULT '',
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT NOT NULL DEFAULT '',
    last_login TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    pending_email TEXT NOT NULL DEFAULT '',
    invited_by TEXT NOT NULL DEFAULT '',
    invite_accepted_at TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS account_tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    used_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_account_tokens_user ON account_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_account_tokens_kind ON account_tokens(kind);
CREATE TABLE IF NOT EXISTS revoked_jtis (
    jti TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    issued_before DOUBLE PRECISION NOT NULL DEFAULT 0,
    expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_revoked_jtis_expires ON revoked_jtis(expires_at);
"""

#: The tables the identity database owns.
POSTGRES_TABLES = ("users", "store_meta", "account_tokens", "revoked_jtis")


# ── statement construction (testable without a server) ────────────────


def build_insert_statement() -> Tuple[str, Tuple[str, ...]]:
    """The INSERT statement and the column list it binds.

    Values are never interpolated: every one of them is a bound
    placeholder.
    """
    columns = tuple(USER_COLUMNS)
    placeholders = ", ".join("%s" for _ in columns)
    sql = (
        f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT (username) DO NOTHING"
    )
    return sql, columns


def build_update_statement(columns: Sequence[str]) -> str:
    """The UPDATE statement for *columns* (plus the username key)."""
    assignments = ", ".join(f"{column} = %s" for column in columns)
    return f"UPDATE users SET {assignments} WHERE username = %s"


def translate_placeholders(sql: str) -> str:
    """Rewrite ``?`` placeholders to psycopg's ``%s``.

    Question marks inside a string literal are left alone.  The shared
    revocation list (``api/auth.py``) speaks the ``?`` dialect, so the
    adapter below uses this to run its statements unchanged.
    """
    out: List[str] = []
    in_string = False
    for char in sql:
        if char == "'":
            in_string = not in_string
            out.append(char)
        elif char == "?" and not in_string:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


# ── the pool ──────────────────────────────────────────────────────────


class PostgresPool:
    """A small connection pool for ``psycopg`` (v3).

    ``psycopg_pool`` is an optional extra; this keeps the store working
    with a plain ``psycopg`` install.  Idle connections are reused, the
    number of live connections is capped, and a caller waits for a
    connection to come back instead of opening an unbounded number.
    """

    def __init__(self, dsn: str, max_size: int = DEFAULT_POOL_SIZE,
                 connect_timeout: int = DEFAULT_CONNECT_TIMEOUT):
        self.dsn = dsn
        self.max_size = max(1, int(max_size))
        self.connect_timeout = max(1, int(connect_timeout))
        self._idle: List[Any] = []
        self._in_use = 0
        self._closed = False
        self._cond = threading.Condition(threading.Lock())

    def _connect(self):
        psycopg = _psycopg()
        return psycopg.connect(self.dsn, connect_timeout=self.connect_timeout)

    def acquire(self):
        """Take a connection from the pool (opening one when allowed)."""
        with self._cond:
            while True:
                if self._closed:
                    raise RuntimeError("the PostgreSQL pool is closed")
                if self._idle:
                    conn = self._idle.pop()
                    self._in_use += 1
                    return conn
                if self._in_use < self.max_size:
                    self._in_use += 1
                    break
                self._cond.wait(timeout=30)
        try:
            return self._connect()
        except Exception:
            with self._cond:
                self._in_use -= 1
                self._cond.notify()
            raise

    def release(self, conn) -> None:
        """Give a connection back to the pool."""
        with self._cond:
            self._in_use -= 1
            if self._closed or getattr(conn, "closed", False):
                self._cond.notify()
                return
            self._idle.append(conn)
            self._cond.notify()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Borrow a connection for the duration of the block."""
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def close(self) -> None:
        """Close every idle connection. Idempotent."""
        with self._cond:
            self._closed = True
            idle, self._idle = self._idle, []
        for conn in idle:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best effort
                pass


# ── row helpers ───────────────────────────────────────────────────────


class PostgresRow:
    """One result row, addressable by index *and* by column name."""

    def __init__(self, values: Sequence[Any], description: Any):
        self._values = tuple(values)
        self._names = [d[0] for d in (description or [])]

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._names.index(key)]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"PostgresRow({self._names})"


class PostgresCursor:
    """A cursor shaped like the ``sqlite3`` one the callers expect."""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self) -> Optional[PostgresRow]:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PostgresRow(row, self._cursor.description)

    def fetchall(self) -> List[PostgresRow]:
        description = self._cursor.description
        return [PostgresRow(row, description) for row in self._cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    def close(self) -> None:
        try:
            self._cursor.close()
        except Exception:  # noqa: BLE001 — best effort
            pass


def _user_row(row: Optional[PostgresRow]) -> Optional[Dict[str, Any]]:
    """Turn a result row into the dict the interface promises."""
    if row is None:
        return None
    return _coerce_row(dict(zip(USER_COLUMNS, tuple(row))))


def _bool_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce the boolean columns to real booleans (Postgres is strict)."""
    out = dict(payload)
    for column in _BOOL_COLUMNS:
        if column in out:
            out[column] = bool(out[column])
    return out


# ── the store ─────────────────────────────────────────────────────────


class PostgresUserStore(UserStore):
    """The user directory on PostgreSQL.

    Safe to call from several threads: the pool hands out one connection
    per call and the store never mutates a record it returns.
    """

    def __init__(self, dsn: Optional[str] = None, pool_size: Optional[int] = None):
        self.dsn = dsn or postgres_dsn()
        if not self.dsn:
            raise RuntimeError(
                "no PostgreSQL DSN configured — set DATABASE_URL or POSTGRES_DSN"
            )
        default_size, default_timeout = _pool_settings()
        self._pool = PostgresPool(
            self.dsn,
            max_size=pool_size or default_size,
            connect_timeout=default_timeout,
        )
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- plumbing -------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the tables if they do not exist. Idempotent."""
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    for statement in _split_statements(POSTGRES_SCHEMA_SQL):
                        cur.execute(statement)
                conn.commit()
        logger.info("PostgreSQL identity schema is ready")

    def close(self) -> None:
        """Release the pooled connections. Idempotent."""
        with self._lock:
            self._pool.close()

    # -- reads ----------------------------------------------------------

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        sql = f"SELECT {_select_columns()} FROM users WHERE username = %s"
        return self._fetch_one(sql, (username,))

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        sql = f"SELECT {_select_columns()} FROM users WHERE user_id = %s LIMIT 1"
        return self._fetch_one(sql, (user_id,))

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalised = _normalise_email(email)
        if not normalised:
            return None
        sql = f"SELECT {_select_columns()} FROM users WHERE LOWER(email) = %s LIMIT 1"
        return self._fetch_one(sql, (normalised,))

    def get_user_by_api_key(self, key: str) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        sql = f"SELECT {_select_columns()} FROM users WHERE api_key = %s LIMIT 1"
        return self._fetch_one(sql, (key,))

    def list_users(self) -> List[Dict[str, Any]]:
        sql = f"SELECT {_select_columns()} FROM users ORDER BY username"
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
        return [row for row in (_user_row(r) for r in rows) if row is not None]

    def _fetch_one(self, sql: str, params: Sequence[Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
        return _user_row(row)

    # -- writes ---------------------------------------------------------

    def add_user(self, user: UserRecord) -> Optional[str]:
        record = user if user.user_id else UserRecord(
            **{**user.as_dict(), "user_id": _new_user_id()}
        )
        payload = _bool_payload(record.as_dict())
        payload["email"] = _normalise_email(payload["email"])
        sql, columns = build_insert_statement()
        values = [payload[column] for column in columns]
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, values)
                    inserted = cur.rowcount > 0
                conn.commit()
        return record.user_id if inserted else None

    def update_user(self, username: str, **fields: Any) -> bool:
        updates = {
            key: value
            for key, value in fields.items()
            if key in USER_COLUMNS and key != "username"
        }
        if not updates:
            return self.get_user(username) is not None
        updates = _bool_payload(updates)
        if "email" in updates:
            updates["email"] = _normalise_email(updates["email"])
        sql = build_update_statement(list(updates))
        values = list(updates.values()) + [username]
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, values)
                    changed = cur.rowcount > 0
                conn.commit()
        return changed

    def remove_user(self, username: str) -> bool:
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username = %s", (username,))
                    removed = cur.rowcount > 0
                conn.commit()
        return removed

    def set_password(self, username: str, password_hash: str, **fields: Any) -> bool:
        updates = dict(fields)
        updates["password_hash"] = password_hash
        return self.update_user(username, **updates)

    def set_api_key(self, username: str, key: str) -> bool:
        return self.update_user(username, api_key=key or "")


def _split_statements(ddl: str) -> List[str]:
    """Split a DDL script into its statements."""
    return [part.strip() for part in ddl.split(";") if part.strip()]


# ── the shared identity database ──────────────────────────────────────


class PostgresIdentityConnection:
    """A ``sqlite3``-shaped view of a PostgreSQL connection.

    ``api/auth.py`` stores the revoked token ids through
    ``conn.execute(sql, params)`` with ``?`` placeholders and reads the
    result with ``row["column"]``.  This adapter translates that dialect to
    psycopg's so the shared revocation list keeps working unchanged when
    the identity store moves to PostgreSQL.
    """

    def __init__(self, pool: PostgresPool):
        self._pool = pool
        self._conn = None
        self._lock = threading.RLock()

    def _connection(self):
        if self._conn is None or getattr(self._conn, "closed", False):
            self._conn = self._pool.acquire()
        return self._conn

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> PostgresCursor:
        """Run one statement and return its cursor."""
        with self._lock:
            conn = self._connection()
            values = tuple(params) if params else None
            cursor = conn.cursor()
            cursor.execute(translate_placeholders(sql), values)
            conn.commit()
            return PostgresCursor(cursor)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._pool.release(self._conn)
                except Exception:  # noqa: BLE001 — best effort
                    pass
                self._conn = None


def connect_postgres_identity(dsn: Optional[str] = None) -> PostgresIdentityConnection:
    """Open the shared identity tables on PostgreSQL."""
    target = dsn or postgres_dsn()
    if not target:
        raise RuntimeError(
            "no PostgreSQL DSN configured — set DATABASE_URL or POSTGRES_DSN"
        )
    default_size, default_timeout = _pool_settings()
    pool = PostgresPool(
        target,
        max_size=default_size,
        connect_timeout=default_timeout,
    )
    connection = PostgresIdentityConnection(pool)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for statement in _split_statements(POSTGRES_SCHEMA_SQL):
                cur.execute(statement)
        conn.commit()
    return connection
