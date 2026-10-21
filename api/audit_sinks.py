"""Shared-backend audit sinks.

``logs/audit.log`` is a per-container file: it is lost when the container is
replaced and it is split across replicas. ADR-009 D5 makes the audit trail
authoritative rather than per-process, so this module adds two sinks that
live outside the process:

* :class:`SQLiteAuditHandler` — a transactional, WAL-mode SQLite database
  (``data/audit.db``). No extra infrastructure and multi-process safe.
* :class:`RedisAuditHandler` — appends to a capped Redis list.

Both implement the same ``handle(AuditEvent)`` contract as the handlers in
:mod:`api.audit`. A sink never lets a handler error escape:
``AuditLogger.log`` catches per-handler exceptions, so a sink outage
degrades to "the event was not recorded" rather than a failed request.

Selection follows the convention already used by
:func:`api.session.build_session_store`: ``AUDIT_SINK`` wins, otherwise a
configured ``REDIS_URL`` selects the Redis sink. With neither set the trail
stays a local file, so a desktop install is unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from api.audit_base import AuditHandler

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.audit import AuditEvent

logger = logging.getLogger(__name__)

# Where the SQLite sink writes when nothing else is configured.
DEFAULT_SQLITE_PATH = "data/audit.db"
# The Redis list the Redis sink appends to.
DEFAULT_REDIS_KEY = "audit:events"
# How many events the Redis list keeps before the oldest are dropped.
DEFAULT_MAX_ENTRIES = 10000

# The sink types the environment may select.
SINK_TYPES = ("file", "sqlite", "redis")

# A table name is interpolated into DDL, so it must be a plain identifier.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def audit_sqlite_path() -> str:
    """Where the SQLite audit sink writes."""
    return (os.environ.get("DATAAGENT_AUDIT_DB") or "").strip() or DEFAULT_SQLITE_PATH


def audit_redis_url() -> str:
    """The configured Redis URL (empty when Redis is not configured)."""
    return (os.environ.get("REDIS_URL") or "").strip()


def resolve_audit_sink() -> str:
    """Which audit sink the environment selects: 'file', 'sqlite' or 'redis'.

    ``AUDIT_SINK`` wins. Without it a configured ``REDIS_URL`` selects the
    Redis sink — the same convention ``api/session.py`` uses for sessions —
    and with neither set the trail stays a local file.
    """
    explicit = (os.environ.get("AUDIT_SINK") or "").strip().lower()
    if explicit in SINK_TYPES:
        return explicit
    if explicit:
        logger.warning("Unknown AUDIT_SINK %r — keeping the file sink", explicit)
        return "file"
    if audit_redis_url():
        return "redis"
    return "file"


class SQLiteAuditHandler(AuditHandler):
    """Append audit events to a SQLite database (WAL mode).

    The database is opened once and guarded by a lock, so concurrent writers
    in the same process serialise cleanly; WAL mode lets a reader (or
    another process) read while a write is in flight. Every statement is
    parameterised — event fields are data, never SQL.
    """

    TABLE = "audit_events"
    COLUMNS = (
        "timestamp", "event_type", "user_id", "tenant_id", "resource",
        "action", "result", "details", "ip_address", "user_agent", "request_id",
    )

    def __init__(self, path: Optional[str] = None, table: Optional[str] = None):
        self.path = path or audit_sqlite_path()
        self.table = table or self.TABLE
        if not _IDENTIFIER_RE.match(self.table):
            raise ValueError(f"invalid audit table name: {self.table!r}")
        self._lock = threading.Lock()
        self._conn = self._connect(self.path)
        self._ensure_schema()

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        db_path = Path(path)
        if db_path.parent and str(db_path.parent) not in ("", "."):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{self.table}" ('
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, "
                "event_type TEXT NOT NULL, "
                "user_id TEXT, "
                "tenant_id TEXT, "
                "resource TEXT, "
                "action TEXT, "
                "result TEXT, "
                "details TEXT, "
                "ip_address TEXT, "
                "user_agent TEXT, "
                "request_id TEXT)"
            )
            self._conn.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{self.table}_timestamp" '
                f'ON "{self.table}" (timestamp)'
            )
            self._conn.commit()

    def handle(self, event: "AuditEvent") -> None:
        payload: Dict[str, Any] = event.to_dict()
        row = (
            payload.get("timestamp"),
            payload.get("event_type"),
            payload.get("user_id"),
            payload.get("tenant_id"),
            payload.get("resource"),
            payload.get("action"),
            payload.get("result"),
            json.dumps(payload.get("details") or {}, sort_keys=True, default=str),
            payload.get("ip_address"),
            payload.get("user_agent"),
            payload.get("request_id"),
        )
        placeholders = ", ".join("?" * len(self.COLUMNS))
        columns = ", ".join(f'"{c}"' for c in self.COLUMNS)
        with self._lock:
            self._conn.execute(
                f'INSERT INTO "{self.table}" ({columns}) VALUES ({placeholders})',
                row,
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                logger.debug("Could not close the audit database: %s", exc)


class RedisAuditHandler(AuditHandler):
    """Append audit events to a capped Redis list.

    The list is trimmed on every write, so it holds at most
    ``max_entries`` events and cannot grow without bound.
    """

    def __init__(self, redis_url: Optional[str] = None, client=None,
                 key: Optional[str] = None, max_entries: int = DEFAULT_MAX_ENTRIES):
        if client is not None:
            self.client = client
        else:
            import redis

            self.client = redis.from_url(
                redis_url or "redis://localhost:6379", decode_responses=True
            )
        self.key = key or DEFAULT_REDIS_KEY
        self.max_entries = max(1, int(max_entries))

    def handle(self, event: "AuditEvent") -> None:
        payload = json.dumps(event.to_dict(), default=str)
        pipe = self.client.pipeline()
        pipe.rpush(self.key, payload)
        pipe.ltrim(self.key, -self.max_entries, -1)
        pipe.execute()

    def close(self) -> None:
        closer = getattr(self.client, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                logger.debug("Could not close the audit Redis client: %s", exc)


def build_audit_sink(sink_type: str) -> Optional[AuditHandler]:
    """Build the sink named by *sink_type* (None for the file sink).

    The file sink is the default and is configured through
    ``settings.audit.destinations``; this helper only builds the shared
    backends.
    """
    if sink_type == "sqlite":
        return SQLiteAuditHandler(path=audit_sqlite_path())
    if sink_type == "redis":
        return RedisAuditHandler(redis_url=audit_redis_url(), key=DEFAULT_REDIS_KEY)
    return None
