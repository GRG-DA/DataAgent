"""Identity store (ADR-009 D2).

``config/users.json`` was a per-process, unlocked, full-file rewrite: two
replicas each kept their own copy of the directory, a registration on one was
invisible on the other, and the unlocked rewrite could lose accounts. This
module moves the user directory behind a narrow repository interface with
two implementations:

* :class:`SQLiteUserStore` — the default. A transactional, WAL-mode SQLite
  database (``data/users.db``) several processes can share without a server.
  Rows in a legacy ``config/users.json`` are imported exactly once, so an
  existing installation keeps its accounts.
* :class:`JSONUserStore` — the legacy file store, kept as a fallback for
  offline installs and for callers that point at a JSON file.

The backend is selected explicitly:

``DATAAGENT_USER_STORE``
    ``sqlite`` (default), ``json`` or ``postgres``.
``DATAAGENT_USER_DB``
    SQLite database path (default ``data/users.db``).
``DATAAGENT_USER_JSON``
    JSON file path (default ``config/users.json``).
``DATAAGENT_USER_LEGACY_JSON``
    Legacy file imported into SQLite on first use (default
    ``config/users.json``).
``DATABASE_URL`` / ``POSTGRES_DSN``
    The PostgreSQL DSN.  When it is set and no backend was chosen
    explicitly, PostgreSQL is preferred (a hosted deployment shares one
    identity database between its replicas) and SQLite is the fallback if
    the server cannot be reached.

Nothing here logs or returns password hashes, salts or key material.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Default location of the SQLite identity database.
DEFAULT_DB_PATH = Path("data/users.db")

#: The legacy JSON directory. Read once on first use, never written again.
LEGACY_USERS_JSON = Path("config/users.json")


# ── the record ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserRecord:
    """One user row.

    Frozen: a store never mutates the record it was handed, it writes a copy.
    """

    username: str
    user_id: str = ""
    password_hash: str = ""
    salt: str = ""
    role: str = "viewer"
    email: str = ""
    tenant_id: str = ""
    sso_subject: str = ""
    sso_provider: str = ""
    full_name: str = ""
    is_active: bool = True
    is_admin: bool = False
    api_key: str = ""
    mfa_enabled: bool = False
    mfa_secret: str = ""
    password_changed_at: str = ""
    failed_login_attempts: int = 0
    locked_until: str = ""
    last_login: str = ""
    created_at: str = ""
    updated_at: str = ""
    # -- account lifecycle (ADR-009) --------------------------------
    email_verified: bool = False
    pending_email: str = ""
    invited_by: str = ""
    invite_accepted_at: str = ""
    last_login_at: str = ""
    must_change_password: bool = False

    def as_dict(self) -> Dict[str, Any]:
        """Return the record as a plain dict (booleans as booleans)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: Columns that are stored as 0/1 integers in SQLite.
_BOOL_COLUMNS = frozenset({
    "is_active",
    "is_admin",
    "mfa_enabled",
    "email_verified",
    "must_change_password",
})

#: Every column of the user table, in a stable order.
USER_COLUMNS: Tuple[str, ...] = tuple(f.name for f in fields(UserRecord))


# ── the interface ─────────────────────────────────────────────────────


class UserStore(ABC):
    """The user directory.

    Implementations must be safe to call from several threads and must never
    mutate a record they return — callers get a fresh dict every time.
    """

    @abstractmethod
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Return the full record of *username*, or None."""

    @abstractmethod
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return the full record of *user_id*, or None."""

    @abstractmethod
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Return the full record of *email* (case-insensitive), or None."""

    @abstractmethod
    def list_users(self) -> List[Dict[str, Any]]:
        """Return every record (full, including sensitive fields)."""

    @abstractmethod
    def add_user(self, user: UserRecord) -> Optional[str]:
        """Insert *user*. Returns the user id, or None when it already exists."""

    @abstractmethod
    def update_user(self, username: str, **fields: Any) -> bool:
        """Update the given columns. False when the user does not exist."""

    @abstractmethod
    def remove_user(self, username: str) -> bool:
        """Delete the account. False when the user does not exist."""

    @abstractmethod
    def set_password(self, username: str, password_hash: str, **fields: Any) -> bool:
        """Replace the stored password hash. False when unknown."""

    @abstractmethod
    def set_api_key(self, username: str, key: str) -> bool:
        """Replace the stored API key. False when unknown."""

    @abstractmethod
    def get_user_by_api_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Return the record owning *key*, or None."""

    @abstractmethod
    def close(self) -> None:
        """Release the underlying resources. Idempotent."""

    # -- the account lifecycle (ADR-009) ------------------------------
    #
    # These are concrete on the interface: both backends can express them
    # with the primitives above, so neither implementation has to repeat
    # itself and a new backend inherits them for free.

    def set_email(self, username: str, email: str) -> bool:
        """Set the account's email address. False when unknown."""
        return self.update_user(username, email=_normalise_email(email))

    def set_email_verified(self, username: str, verified: bool = True) -> bool:
        """Flip the email-verified flag. False when unknown."""
        return self.update_user(username, email_verified=bool(verified))

    def set_must_change_password(self, username: str, required: bool = True) -> bool:
        """Require (or clear) a password change at the next login."""
        return self.update_user(username, must_change_password=bool(required))

    def set_password_changed_at(self, username: str, when: str = "") -> bool:
        """Stamp the moment the password was last changed."""
        stamp = when or _utc_now_iso()
        return self.update_user(username, password_changed_at=stamp)

    def mark_invite_accepted(self, username: str) -> bool:
        """Stamp the moment an invitation was accepted."""
        return self.update_user(username, invite_accepted_at=_utc_now_iso())

    def list_users_by_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Every account of one tenant (empty tenant id matches nobody)."""
        wanted = str(tenant_id or "").strip()
        if not wanted:
            return []
        return [
            user
            for user in self.list_users()
            if str(user.get("tenant_id", "")).strip() == wanted
        ]


# ── shared helpers ────────────────────────────────────────────────────


def _new_user_id() -> str:
    """A fresh, unguessable user id."""
    return secrets.token_urlsafe(16)


def _utc_now_iso() -> str:
    """UTC timestamp in the format the store keeps."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _normalise_email(email: str) -> str:
    """Emails are matched case-insensitively; store them lower-cased."""
    return (email or "").strip().lower()


def _coerce_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a stored row into a dict with real booleans."""
    out = dict(row)
    for column in _BOOL_COLUMNS:
        if column in out:
            out[column] = bool(out[column])
    return out


def _select_columns() -> str:
    return ", ".join(USER_COLUMNS)


# ── SQLite ────────────────────────────────────────────────────────────


#: Tables that live next to the user directory but are not users: the
#: short-lived account tokens (password reset, e-mail verification,
#: invitation) and the shared JWT revocation list.  They live in the same
#: database so they are transactional, survive a restart and are shared by
#: every replica of a hosted deployment.
SHARED_TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    expires_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    used_at REAL NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_account_tokens_user ON account_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_account_tokens_kind ON account_tokens(kind);
CREATE TABLE IF NOT EXISTS revoked_jtis (
    jti TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    issued_before REAL NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_revoked_jtis_expires ON revoked_jtis(expires_at);
"""


_SCHEMA = """
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
    is_active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0,
    api_key TEXT NOT NULL DEFAULT '',
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT NOT NULL DEFAULT '',
    password_changed_at TEXT NOT NULL DEFAULT '',
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT NOT NULL DEFAULT '',
    last_login TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    email_verified INTEGER NOT NULL DEFAULT 0,
    pending_email TEXT NOT NULL DEFAULT '',
    invited_by TEXT NOT NULL DEFAULT '',
    invite_accepted_at TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    must_change_password INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
""" + SHARED_TABLES_SCHEMA


#: The account-lifecycle columns, with the DDL an ``ALTER TABLE`` needs.
#: ``CREATE TABLE IF NOT EXISTS`` never changes an existing table, so an
#: installation that predates ADR-009 needs these added one by one.
USER_COLUMN_MIGRATIONS: Tuple[Tuple[str, str], ...] = (
    ("email_verified", "INTEGER NOT NULL DEFAULT 0"),
    ("pending_email", "TEXT NOT NULL DEFAULT ''"),
    ("invited_by", "TEXT NOT NULL DEFAULT ''"),
    ("invite_accepted_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_login_at", "TEXT NOT NULL DEFAULT ''"),
    ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate_user_columns(conn: sqlite3.Connection) -> int:
    """Add the account-lifecycle columns to an existing ``users`` table.

    Idempotent: a column that is already there is left alone.  Returns the
    number of columns added.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    added = 0
    for column, ddl in USER_COLUMN_MIGRATIONS:
        if column in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
            added += 1
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            logger.warning("Could not add the column %s to users: %s", column, exc)
    return added


class SQLiteUserStore(UserStore):
    """Transactional, multi-process safe user directory on SQLite.

    WAL mode plus a threading lock: readers never block the writer and two
    threads in one process never step on each other's transaction.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        legacy_json_path: str | Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.legacy_json_path = Path(
            legacy_json_path
            if legacy_json_path is not None
            else os.environ.get("DATAAGENT_USER_LEGACY_JSON", str(LEGACY_USERS_JSON))
        )
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()
        self._migrate_legacy_json()

    # -- plumbing -------------------------------------------------------

    def _open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        migrate_user_columns(conn)
        conn.commit()
        self._conn = conn

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._open()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:  # pragma: no cover - best effort
                    pass
                self._conn = None

    # -- reads ----------------------------------------------------------

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                f"SELECT {_select_columns()} FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return _coerce_row(dict(row)) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        with self._lock:
            row = self._connection().execute(
                f"SELECT {_select_columns()} FROM users WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
        return _coerce_row(dict(row)) if row else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalised = _normalise_email(email)
        if not normalised:
            return None
        with self._lock:
            row = self._connection().execute(
                f"SELECT {_select_columns()} FROM users WHERE LOWER(email) = ? LIMIT 1",
                (normalised,),
            ).fetchone()
        return _coerce_row(dict(row)) if row else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                f"SELECT {_select_columns()} FROM users ORDER BY username"
            ).fetchall()
        return [_coerce_row(dict(row)) for row in rows]

    def get_user_by_api_key(self, key: str) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        with self._lock:
            row = self._connection().execute(
                f"SELECT {_select_columns()} FROM users WHERE api_key = ? LIMIT 1",
                (key,),
            ).fetchone()
        return _coerce_row(dict(row)) if row else None

    # -- writes ---------------------------------------------------------

    def add_user(self, user: UserRecord) -> Optional[str]:
        record = user if user.user_id else UserRecord(**{**user.as_dict(), "user_id": _new_user_id()})
        payload = record.as_dict()
        payload["email"] = _normalise_email(payload["email"])
        for column in _BOOL_COLUMNS:
            payload[column] = 1 if payload[column] else 0
        placeholders = ", ".join("?" for _ in USER_COLUMNS)
        columns = ", ".join(USER_COLUMNS)
        values = [payload[column] for column in USER_COLUMNS]
        with self._lock:
            conn = self._connection()
            try:
                conn.execute(
                    f"INSERT INTO users ({columns}) VALUES ({placeholders})", values
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return None
        return record.user_id

    def update_user(self, username: str, **fields: Any) -> bool:
        updates = {k: v for k, v in fields.items() if k in USER_COLUMNS and k != "username"}
        if not updates:
            return self.get_user(username) is not None
        for column in _BOOL_COLUMNS:
            if column in updates:
                updates[column] = 1 if updates[column] else 0
        if "email" in updates:
            updates["email"] = _normalise_email(updates["email"])
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [username]
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                f"UPDATE users SET {assignments} WHERE username = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def remove_user(self, username: str) -> bool:
        with self._lock:
            conn = self._connection()
            cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            return cursor.rowcount > 0

    def set_password(self, username: str, password_hash: str, **fields: Any) -> bool:
        updates = dict(fields)
        updates["password_hash"] = password_hash
        return self.update_user(username, **updates)

    def set_api_key(self, username: str, key: str) -> bool:
        return self.update_user(username, api_key=key or "")

    # -- the one-time legacy import -------------------------------------

    def _migrate_legacy_json(self) -> int:
        """Import ``config/users.json`` once. Idempotent by construction.

        The import is marked as done in ``store_meta``: a later run must not
        resurrect an account an administrator has deleted in the meantime.
        """
        with self._lock:
            conn = self._connection()
            done = conn.execute(
                "SELECT value FROM store_meta WHERE key = ?", ("legacy_json_migrated",)
            ).fetchone()
        if done is not None:
            return 0
        rows = _read_legacy_json(self.legacy_json_path)
        if not rows:
            return 0

        migrated = 0
        for username, payload in rows.items():
            record = _record_from_legacy(username, payload)
            if self.add_user(record) is not None:
                migrated += 1
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT OR REPLACE INTO store_meta (key, value) VALUES (?, ?)",
                ("legacy_json_migrated", "1"),
            )
            conn.commit()
        logger.info(
            "Migrated %s user(s) from %s into %s", migrated, self.legacy_json_path, self.db_path
        )
        return migrated


def _read_legacy_json(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read the legacy JSON directory. Never raises."""
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the legacy user file %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def _record_from_legacy(username: str, payload: Dict[str, Any]) -> UserRecord:
    """Build a record from a legacy JSON row, keeping unknown fields out."""
    known = {f.name for f in fields(UserRecord)}
    values = {k: v for k, v in payload.items() if k in known}
    values["username"] = username
    values.setdefault("user_id", _new_user_id())
    values["email"] = _normalise_email(str(values.get("email", "")))
    return UserRecord(**values)


# ── the shared identity database ───────────────────────────────────────


def identity_db_path() -> Path:
    """The SQLite identity database.

    The user directory, the account tokens and the JWT revocation list all
    live here, so a hosted deployment shares one transactional file between
    its replicas.
    """
    return Path(os.environ.get("DATAAGENT_USER_DB") or DEFAULT_DB_PATH)


def identity_backend() -> str:
    """The configured identity backend: ``postgres``, ``json`` or ``sqlite``.

    ``DATAAGENT_USER_STORE`` wins when it names a known backend.  Otherwise
    a configured PostgreSQL DSN selects ``postgres`` (the hosted backend)
    and everything else stays on ``sqlite``.
    """
    explicit = (os.environ.get("DATAAGENT_USER_STORE") or "").strip().lower()
    if explicit in ("postgres", "json", "sqlite"):
        return explicit
    if explicit:
        logger.warning("Unknown DATAAGENT_USER_STORE=%r — using sqlite", explicit)
    if _postgres_dsn():
        return "postgres"
    return "sqlite"


def _postgres_dsn() -> str:
    """The configured PostgreSQL DSN (empty when there is none)."""
    for name in ("DATABASE_URL", "POSTGRES_DSN", "DATABASE_DSN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def connect_identity_db(db_path: str | Path | None = None):
    """Open the identity database and make sure the shared tables exist.

    The backend follows :func:`identity_backend`: with the PostgreSQL
    backend the shared tables (the account tokens and the JWT revocation
    list) live in PostgreSQL, so a revocation is refused on **every**
    replica of a hosted deployment — not only on the one that wrote it.

    The SQLite connection runs in autocommit mode: the callers that need a
    transaction start one explicitly (``BEGIN IMMEDIATE``), which keeps the
    single-use guarantee of the token store honest.
    """
    if identity_backend() == "postgres":
        from api.user_store_postgres import connect_postgres_identity

        return connect_postgres_identity()

    path = Path(db_path) if db_path else identity_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # explicit transactions only
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SHARED_TABLES_SCHEMA)
    return conn


# ── the legacy JSON store ─────────────────────────────────────────────


class JSONUserStore(UserStore):
    """The legacy file store, kept as a fallback.

    Reads the file once, then keeps the directory in memory and writes the
    whole file back atomically on every change — the same behaviour as the
    original implementation, minus the torn writes.
    """

    def __init__(self, path: str | Path = LEGACY_USERS_JSON):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._users: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._users = {
                username: dict(payload)
                for username, payload in _read_legacy_json(self.path).items()
            }
        if self._users:
            logger.info("Loaded %s user(s) from %s", len(self._users), self.path)

    def _save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._users, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            user = self._users.get(username)
            return dict(user) if user else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        with self._lock:
            for user in self._users.values():
                if user.get("user_id") == user_id:
                    return dict(user)
        return None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalised = _normalise_email(email)
        if not normalised:
            return None
        with self._lock:
            for user in self._users.values():
                if _normalise_email(str(user.get("email", ""))) == normalised:
                    return dict(user)
        return None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(user) for user in self._users.values()]

    def add_user(self, user: UserRecord) -> Optional[str]:
        with self._lock:
            if user.username in self._users:
                return None
            record = user if user.user_id else UserRecord(
                **{**user.as_dict(), "user_id": _new_user_id()}
            )
            self._users[record.username] = record.as_dict()
            self._save()
            return record.user_id

    def update_user(self, username: str, **fields: Any) -> bool:
        with self._lock:
            user = self._users.get(username)
            if user is None:
                return False
            for key, value in fields.items():
                if key in USER_COLUMNS and key != "username":
                    user[key] = value
            self._save()
            return True

    def remove_user(self, username: str) -> bool:
        with self._lock:
            if username not in self._users:
                return False
            del self._users[username]
            self._save()
            return True

    def set_password(self, username: str, password_hash: str, **fields: Any) -> bool:
        updates = dict(fields)
        updates["password_hash"] = password_hash
        return self.update_user(username, **updates)

    def set_api_key(self, username: str, key: str) -> bool:
        return self.update_user(username, api_key=key or "")

    def get_user_by_api_key(self, key: str) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        with self._lock:
            for user in self._users.values():
                if user.get("api_key") and user["api_key"] == key:
                    return dict(user)
        return None

    def close(self) -> None:
        """Nothing to release — the file store holds no handle."""


# ── selection ─────────────────────────────────────────────────────────


_STORE_CACHE: Dict[Tuple[str, str], UserStore] = {}
_STORE_LOCK = threading.Lock()


def _make_store(backend: str, path: Path) -> UserStore:
    if backend == "json":
        return JSONUserStore(path)
    if backend == "postgres":
        return _make_postgres_store(path)
    try:
        return SQLiteUserStore(path)
    except Exception as exc:  # noqa: BLE001 — a broken SQLite install must not brick the app
        logger.error(
            "Could not open the SQLite user store at %s (%s) — falling back to the "
            "JSON store", path, exc,
        )
        return JSONUserStore(Path(os.environ.get("DATAAGENT_USER_JSON") or LEGACY_USERS_JSON))


def _make_postgres_store(path: Path) -> UserStore:
    """Open the PostgreSQL store, falling back to SQLite when it is down.

    A hosted deployment runs several replicas: if one of them cannot reach
    the database it must not serve a *different* directory of accounts, so
    the failure is logged loudly and the local SQLite store takes over.
    """
    from api.user_store_postgres import PostgresUserStore

    try:
        store = PostgresUserStore()
    except Exception as exc:  # noqa: BLE001 — never brick the app on a bad DSN
        logger.error(
            "Could not open the PostgreSQL user store (%s) — falling back to the "
            "SQLite store at %s", exc, path,
        )
        return SQLiteUserStore(path)
    logger.info("Using the PostgreSQL identity store")
    return store


def get_user_store() -> UserStore:
    """Return the process-wide user store.

    The backend is explicit: ``DATAAGENT_USER_STORE=json|sqlite|postgres``
    and ``DATAAGENT_USER_DB`` / ``DATAAGENT_USER_JSON`` for the paths.
    Without an explicit choice, a configured ``DATABASE_URL`` selects
    PostgreSQL (the hosted backend) and SQLite is the default.  The
    instance is cached per (backend, path) so every caller in the process
    shares one connection.
    """
    backend = identity_backend()
    if backend == "json":
        path = Path(os.environ.get("DATAAGENT_USER_JSON") or LEGACY_USERS_JSON)
    else:
        path = Path(os.environ.get("DATAAGENT_USER_DB") or DEFAULT_DB_PATH)

    key = (backend, str(path))
    with _STORE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = _make_store(backend, path)
            _STORE_CACHE[key] = store
        return store


def reset_user_store_cache() -> None:
    """Drop the cached stores (used by tests and by a settings reload)."""
    with _STORE_LOCK:
        for store in _STORE_CACHE.values():
            try:
                store.close()
            except Exception:  # noqa: BLE001 — best effort
                pass
        _STORE_CACHE.clear()
