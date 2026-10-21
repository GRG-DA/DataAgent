"""
Session Management Module
Session handling with Redis or in-memory storage
"""

import os
import uuid
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Set
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from threading import Lock

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """User session."""
    id: str
    user_id: str
    tenant_id: Optional[str] = None
    ip_address: str = ""
    user_agent: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    last_activity: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    expires_at: Optional[datetime] = None
    revoked: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Check if session is still valid."""
        if self.revoked:
            return False
        if self.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > self.expires_at:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["last_activity"] = self.last_activity.isoformat()
        if self.expires_at:
            data["expires_at"] = self.expires_at.isoformat()
        return data


class SessionStore(ABC):
    """Abstract session store."""

    @abstractmethod
    def create(self, session: Session) -> Session:
        pass

    @abstractmethod
    def get(self, session_id: str) -> Optional[Session]:
        pass

    @abstractmethod
    def update(self, session: Session) -> bool:
        pass

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        pass

    @abstractmethod
    def get_user_sessions(self, user_id: str) -> List[Session]:
        pass

    @abstractmethod
    def cleanup_expired(self) -> int:
        pass


class InMemorySessionStore(SessionStore):
    """In-memory session store (for development/single instance)."""

    def __init__(self):
        from threading import RLock
        self._sessions: Dict[str, Session] = {}
        self._user_sessions: Dict[str, Set[str]] = {}
        self._lock = RLock()

    def create(self, session: Session) -> Session:
        with self._lock:
            self._sessions[session.id] = session
            if session.user_id not in self._user_sessions:
                self._user_sessions[session.user_id] = set()
            self._user_sessions[session.user_id].add(session.id)
        return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.is_valid():
                session.last_activity = datetime.now(timezone.utc).replace(tzinfo=None)
                return session
            elif session and not session.is_valid():
                self.delete(session_id)
            return None

    def update(self, session: Session) -> bool:
        with self._lock:
            if session.id in self._sessions:
                self._sessions[session.id] = session
                return True
        return False

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                self._user_sessions.get(session.user_id, set()).discard(session_id)
                return True
        return False

    def get_user_sessions(self, user_id: str) -> List[Session]:
        with self._lock:
            session_ids = self._user_sessions.get(user_id, set())
            return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if not s.is_valid()
            ]
            for sid in expired:
                self.delete(sid)
            return len(expired)


class RedisSessionStore(SessionStore):
    """Redis-backed session store (for production/multi-instance)."""

    def __init__(self, redis_url: str = "redis://localhost:6379", prefix: str = "session:"):
        import redis
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.prefix = prefix
        self.user_prefix = "user_sessions:"

    def _session_key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"

    def _user_key(self, user_id: str) -> str:
        return f"{self.user_prefix}{user_id}"

    def create(self, session: Session) -> Session:
        import json
        key = self._session_key(session.id)
        data = json.dumps(session.to_dict())
        ttl = int((session.expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()) if session.expires_at else 86400
        self.client.setex(key, ttl, data)
        # Add to user's session set
        self.client.sadd(self._user_key(session.user_id), session.id)
        self.client.expire(self._user_key(session.user_id), 86400 * 30)  # 30 days
        return session

    def get(self, session_id: str) -> Optional[Session]:
        import json
        key = self._session_key(session_id)
        data = self.client.get(key)
        if not data:
            return None
        try:
            session_data = json.loads(data)
            # to_dict() isoformats datetimes — parse them back so
            # is_valid() compares datetime-to-datetime, not datetime-to-str.
            for _f in ("created_at", "last_activity", "expires_at"):
                _v = session_data.get(_f)
                if isinstance(_v, str) and _v:
                    try:
                        session_data[_f] = datetime.fromisoformat(_v)
                    except ValueError:
                        session_data[_f] = None
            session = Session(**session_data)
            if session.is_valid():
                # Update last activity
                session.last_activity = datetime.now(timezone.utc).replace(tzinfo=None)
                self.update(session)
                return session
            else:
                self.delete(session_id)
        except Exception as e:
            logger.error(f"Failed to parse session: {e}")
        return None

    def update(self, session: Session) -> bool:
        import json
        key = self._session_key(session.id)
        if not self.client.exists(key):
            return False
        data = json.dumps(session.to_dict())
        ttl = int((session.expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()) if session.expires_at else 86400
        self.client.setex(key, ttl, data)
        return True

    def delete(self, session_id: str) -> bool:
        key = self._session_key(session_id)
        # We need to get user_id first to remove from user set
        # For simplicity, just delete the key
        result = self.client.delete(key)
        return result > 0

    def get_user_sessions(self, user_id: str) -> List[Session]:
        import json
        session_ids = self.client.smembers(self._user_key(user_id))
        sessions = []
        for sid in session_ids:
            session = self.get(sid)
            if session:
                sessions.append(session)
        return sessions

    def cleanup_expired(self) -> int:
        # Redis handles TTL automatically
        return 0


class SessionManager:
    """Manages user sessions."""

    def __init__(
        self,
        store: Optional[SessionStore] = None,
        config: Optional[dict] = None
    ):
        self.config = config or settings.session.model_dump()
        self.store = store or InMemorySessionStore()
        self._cleanup_interval = 300  # 5 minutes
        self._last_cleanup = time.time()

    def _maybe_cleanup(self):
        """Periodic cleanup of expired sessions."""
        if time.time() - self._last_cleanup > self._cleanup_interval:
            self.store.cleanup_expired()
            self._last_cleanup = time.time()

    def create_session(
        self,
        user_id: str,
        tenant_id: Optional[str] = None,
        ip_address: str = "",
        user_agent: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Session:
        """Create a new session."""
        self._maybe_cleanup()

        timeout_minutes = self.config.get("timeout_minutes", 60)
        absolute_timeout_hours = self.config.get("absolute_timeout_hours", 8)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = now + timedelta(minutes=timeout_minutes)
        absolute_expires = now + timedelta(hours=absolute_timeout_hours)

        session = Session(
            id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=now,
            last_activity=now,
            expires_at=min(expires_at, absolute_expires),
            metadata=metadata or {},
        )
        return self.store.create(session)

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session by ID."""
        self._maybe_cleanup()
        return self.store.get(session_id)

    def refresh_session(self, session_id: str) -> Optional[Session]:
        """Refresh session activity timestamp, honoring the absolute cap."""
        session = self.get_session(session_id)
        if session:
            timeout_minutes = self.config.get("timeout_minutes", 60)
            absolute_hours = self.config.get("absolute_timeout_hours", 8)
            try:
                absolute_cap = session.created_at + timedelta(hours=float(absolute_hours))
            except Exception:  # noqa: BLE001
                absolute_cap = None
            next_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=timeout_minutes)
            session.last_activity = datetime.now(timezone.utc).replace(tzinfo=None)
            session.expires_at = min(next_expiry, absolute_cap) if absolute_cap else next_expiry
            self.store.update(session)
        return session

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a session."""
        session = self.store.get(session_id)
        if session:
            session.revoked = True
            return self.store.update(session)
        return False

    def revoke_all_user_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user."""
        sessions = self.store.get_user_sessions(user_id)
        count = 0
        for session in sessions:
            session.revoked = True
            if self.store.update(session):
                count += 1
        return count

    def get_user_sessions(self, user_id: str) -> List[Session]:
        """Get all active sessions for a user."""
        return self.store.get_user_sessions(user_id)


def build_session_store(redis_url: Optional[str] = None, backend: Optional[str] = None) -> SessionStore:
    """Pick the session store explicitly.

    The backend is chosen by :func:`select_session_backend`:
    ``SESSION_BACKEND=memory|redis|postgres`` wins, otherwise a configured
    ``REDIS_URL`` selects redis and a configured ``DATABASE_URL`` selects
    postgres, so several app replicas share their sessions.  Without any
    of those the in-memory store is used, which is the right default for a
    single-process install.  A missing or broken backend degrades to the
    in-memory store rather than taking the app down.

    Passing a redis URL explicitly (the historical signature) selects the
    redis backend; an empty URL selects the in-memory store.
    """
    choice = (backend or "").strip().lower()
    if not choice:
        if redis_url is not None:
            choice = "redis" if redis_url.strip() else "memory"
        else:
            choice = select_session_backend()
    if choice == "redis":
        url = redis_url if redis_url is not None else (os.environ.get("REDIS_URL") or "").strip()
        if not url:
            logger.warning("SESSION_BACKEND=redis but REDIS_URL is not set — using in-memory")
            return InMemorySessionStore()
        try:
            return RedisSessionStore(redis_url=url)
        except Exception as exc:  # noqa: BLE001 — never block start-up on Redis
            logger.warning("Redis session store unavailable (%s) — using in-memory", exc)
            return InMemorySessionStore()
    if backend == "postgres":
        return _build_postgres_session_store()
    return InMemorySessionStore()


def _build_postgres_session_store() -> SessionStore:
    """The PostgreSQL session store, or the in-memory one when it is down."""
    try:
        from api.session_postgres import PostgresSessionStore

        store = PostgresSessionStore()
    except Exception as exc:  # noqa: BLE001 — never block start-up on Postgres
        logger.warning("PostgreSQL session store unavailable (%s) — using in-memory", exc)
        return InMemorySessionStore()
    logger.info("Using the PostgreSQL session store")
    return store


def select_session_backend(environ: Optional[Dict[str, str]] = None) -> str:
    """The session backend for this deployment.

    ``memory``, ``redis`` or ``postgres``.

    ``SESSION_BACKEND`` wins when it names a known backend.  Otherwise a
    configured ``REDIS_URL`` selects redis and a configured
    ``DATABASE_URL``/``POSTGRES_DSN`` selects postgres; with neither, the
    in-memory store is used (a single-process install).
    """
    env = environ if environ is not None else os.environ
    explicit = (env.get("SESSION_BACKEND") or "").strip().lower()
    if explicit:
        if explicit not in ("memory", "redis", "postgres"):
            logger.warning("Unknown SESSION_BACKEND=%r — using in-memory", explicit)
            return "memory"
        return explicit
    if (env.get("REDIS_URL") or "").strip():
        return "redis"
    if (env.get("DATABASE_URL") or "").strip() or (env.get("POSTGRES_DSN") or "").strip():
        return "postgres"
    return "memory"


# Global instance (Redis / PostgreSQL when configured)
session_manager = SessionManager(store=build_session_store())


def get_session_manager() -> SessionManager:
    """Dependency injection for session manager.

    The store behind the manager is chosen by :func:`build_session_store`:
    redis when ``REDIS_URL`` is set, postgres when ``DATABASE_URL`` is set,
    the in-memory store otherwise.
    """
    return session_manager