"""PostgreSQL session store — the shared session backend for a hosted deployment.

``api/session.py`` keeps the session contract behind a small interface.
This module is the PostgreSQL implementation: the same contract as
``RedisSessionStore``, a shared database table instead of a shared Redis
instance, so a deployment that already runs PostgreSQL for its identity
store needs no second piece of infrastructure.

The driver is imported lazily: the module imports cleanly on an install
without ``psycopg`` (see ``requirements-saas.txt``).

The store never logs or returns a session's payload verbatim beyond what
the session contract requires.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, List, Optional

from api.session import Session, SessionStore

logger = logging.getLogger(__name__)

#: Default number of pooled connections.
DEFAULT_POOL_SIZE = 10

#: Default connect timeout, in seconds.
DEFAULT_CONNECT_TIMEOUT = 10


POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


def _psycopg():
    """Import ``psycopg`` (v3) on demand."""
    try:
        import psycopg  # noqa: PLC0415 — lazy on purpose
    except ImportError as exc:  # pragma: no cover — depends on the install
        raise RuntimeError(
            "psycopg (v3) is required for the PostgreSQL session store. "
            "Install requirements-saas.txt, or select another backend with "
            "SESSION_BACKEND=memory."
        ) from exc
    return psycopg


def postgres_dsn() -> str:
    """The configured PostgreSQL DSN (empty when there is none)."""
    for name in ("DATABASE_URL", "POSTGRES_DSN", "DATABASE_DSN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        from config import settings

        return (settings.postgres.url or settings.postgres.dsn or "").strip()
    except Exception:  # noqa: BLE001 — the config is optional
        return ""


def _pool_settings() -> tuple[int, int]:
    """(pool size, connect timeout) from the configuration."""
    try:
        from config import settings

        return (
            max(1, int(settings.postgres.pool_size or DEFAULT_POOL_SIZE)),
            max(1, int(settings.postgres.connect_timeout or DEFAULT_CONNECT_TIMEOUT)),
        )
    except Exception:  # noqa: BLE001 — the config is optional
        return DEFAULT_POOL_SIZE, DEFAULT_CONNECT_TIMEOUT


def _now() -> datetime:
    """The current UTC time, naive (the store's timestamp shape)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _split_statements(ddl: str) -> List[str]:
    """Split a DDL script into its statements."""
    return [part.strip() for part in ddl.split(";") if part.strip()]


class PostgresSessionStore(SessionStore):
    """Sessions in a shared PostgreSQL table.

    Every replica reads and writes the same rows, so a session opened on
    one replica is valid on all of them — the property a hosted
    deployment needs and the in-memory store cannot provide.
    """

    def __init__(self, dsn: Optional[str] = None, pool_size: Optional[int] = None):
        self.dsn = dsn or postgres_dsn()
        if not self.dsn:
            raise RuntimeError(
                "no PostgreSQL DSN configured — set DATABASE_URL or POSTGRES_DSN"
            )
        default_size, default_timeout = _pool_settings()
        self._pool = _Pool(
            self.dsn,
            max_size=pool_size or default_size,
            connect_timeout=default_timeout,
        )
        self._lock = threading.RLock()
        self._ready = False

    # -- plumbing -------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the table if it does not exist. Idempotent."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    for statement in _split_statements(POSTGRES_SCHEMA_SQL):
                        cur.execute(statement)
                conn.commit()
            self._ready = True

    def ping(self) -> bool:
        """True when the store can reach the database."""
        try:
            self._ensure_schema()
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("The PostgreSQL session store is unreachable: %s", exc)
            return False

    def close(self) -> None:
        """Release the pooled connections. Idempotent."""
        with self._lock:
            self._pool.close()

    # -- the session contract -------------------------------------------

    def create(self, session: Session) -> Session:
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sessions (
                            id, user_id, tenant_id, ip_address, user_agent,
                            created_at, last_activity, expires_at, revoked, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            tenant_id = EXCLUDED.tenant_id,
                            ip_address = EXCLUDED.ip_address,
                            user_agent = EXCLUDED.user_agent,
                            created_at = EXCLUDED.created_at,
                            last_activity = EXCLUDED.last_activity,
                            expires_at = EXCLUDED.expires_at,
                            revoked = EXCLUDED.revoked,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            session.id,
                            session.user_id,
                            session.tenant_id or "",
                            session.ip_address or "",
                            session.user_agent or "",
                            session.created_at,
                            session.last_activity,
                            session.expires_at,
                            bool(session.revoked),
                            json.dumps(session.metadata or {}),
                        ),
                    )
                conn.commit()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, user_id, tenant_id, ip_address, user_agent,
                               created_at, last_activity, expires_at, revoked, metadata
                        FROM sessions WHERE id = %s
                        """,
                        (session_id,),
                    )
                    row = cur.fetchone()
        session = _row_to_session(row)
        if session is None:
            return None
        if not session.is_valid():
            self.delete(session_id)
            return None
        session.last_activity = _now()
        self.update(session)
        return session

    def update(self, session: Session) -> bool:
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE sessions SET
                            user_id = %s, tenant_id = %s, ip_address = %s,
                            user_agent = %s, created_at = %s, last_activity = %s,
                            expires_at = %s, revoked = %s, metadata = %s
                        WHERE id = %s
                        """,
                        (
                            session.user_id,
                            session.tenant_id or "",
                            session.ip_address or "",
                            session.user_agent or "",
                            session.created_at,
                            session.last_activity,
                            session.expires_at,
                            bool(session.revoked),
                            json.dumps(session.metadata or {}),
                            session.id,
                        ),
                    )
                    changed = cur.rowcount > 0
                conn.commit()
        return changed

    def delete(self, session_id: str) -> bool:
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
                    removed = cur.rowcount > 0
                conn.commit()
        return removed

    def get_user_sessions(self, user_id: str) -> List[Session]:
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, user_id, tenant_id, ip_address, user_agent,
                               created_at, last_activity, expires_at, revoked, metadata
                        FROM sessions WHERE user_id = %s ORDER BY created_at DESC
                        """,
                        (user_id,),
                    )
                    rows = cur.fetchall()
        sessions = []
        for row in rows:
            session = _row_to_session(row)
            if session is not None and session.is_valid():
                sessions.append(session)
        return sessions

    def cleanup_expired(self) -> int:
        """Drop the sessions whose expiry has passed. Returns the count."""
        self._ensure_schema()
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at <= %s",
                        (_now(),),
                    )
                    removed = cur.rowcount
                conn.commit()
        return int(removed or 0)


def _row_to_session(row: Any) -> Optional[Session]:
    """Turn a result row into a :class:`Session`."""
    if row is None:
        return None
    values = tuple(row)
    if len(values) < 10:
        return None
    try:
        metadata = json.loads(values[9] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return Session(
        id=str(values[0]),
        user_id=str(values[1]),
        tenant_id=str(values[2] or "") or None,
        ip_address=str(values[3] or ""),
        user_agent=str(values[4] or ""),
        created_at=values[5],
        last_activity=values[6],
        expires_at=values[7],
        revoked=bool(values[8]),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


class _Pool:
    """A small connection pool (see ``api.user_store_postgres.PostgresPool``)."""

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
        with self._cond:
            self._in_use -= 1
            if self._closed or getattr(conn, "closed", False):
                self._cond.notify()
                return
            self._idle.append(conn)
            self._cond.notify()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            idle, self._idle = self._idle, []
        for conn in idle:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best effort
                pass
