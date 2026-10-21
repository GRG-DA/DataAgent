"""
Authentication Module
User management with API keys, password authentication, and SSO support
"""

import logging
import hashlib
import secrets
import os
import threading
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import bcrypt
from pydantic import BaseModel

from api.redact import redact_secrets
from api.user_store import (
    JSONUserStore,
    UserRecord,
    UserStore,
    connect_identity_db,
    get_user_store,
    identity_db_path,
)

logger = logging.getLogger(__name__)

#: The registration payload (ADR-009 account lifecycle).
#:
#: ``email`` is optional: an account without an address can sign in, it
#: just cannot receive a password reset or an invitation.  ``api/main.py``
#: keeps its own ``RegisterRequest``; this model is the canonical shape.
class RegisterRequest(BaseModel):
    """The payload of ``POST /auth/register``."""

    username: str
    password: str
    role: str = "user"
    bootstrap_token: str = ""
    email: str = ""

# Legacy JSON directory. Read once by the SQLite store on first use; the
# application never writes it again (ADR-009 D2).
AUTH_FILE = Path("config/users.json")

#: A failed login locks the account for this many minutes.
LOCKOUT_MINUTES = 30

#: Failed logins before the account is locked.
MAX_FAILED_ATTEMPTS = 5


def _utc_now_iso() -> str:
    """UTC timestamp in the format the store keeps."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class UserManager:
    """User management with password auth, API keys, and SSO support.

    The directory lives in an :class:`api.user_store.UserStore` (SQLite by
    default, see ADR-009 D2) instead of an in-process dict, so two app
    replicas share one directory and a write is transactional. The public
    surface of this class is unchanged: every caller keeps working.
    """

    def __init__(self, auth_file: str = "", store: Optional[UserStore] = None):
        self.auth_file = Path(auth_file) if auth_file else AUTH_FILE
        self._store = store or self._default_store(explicit=bool(auth_file))

    def _default_store(self, explicit: bool) -> UserStore:
        """Pick the store.

        A named file means "this directory is that file" (the legacy JSON
        store); otherwise the process-wide store is used, which is SQLite
        unless ``DATAAGENT_USER_STORE=json`` says otherwise.
        """
        if explicit:
            return JSONUserStore(self.auth_file)
        return get_user_store()

    @property
    def store(self) -> UserStore:
        """The backing user store."""
        return self._store

    def add_user(
        self,
        username: str,
        password: str = "",
        role: str = "viewer",
        email: str = "",
        tenant_id: str = "",
        sso_subject: str = "",
        sso_provider: str = "",
        full_name: str = "",
    ) -> Optional[str]:
        """
        Add a new user.
        Returns user_id on success, None on failure.
        """
        if self._store.get_user(username):
            return None

        user_id = secrets.token_urlsafe(16)
        # No plaintext API key is minted or stored here. API keys live in the
        # hashed store (api/api_keys.py — bcrypt + a 12-char prefix index),
        # which is the store AuthMiddleware actually validates against.
        # Keeping a second, cleartext copy in the user directory meant a
        # writable checkout leaked usable credentials.
        api_key = ""

        # Hash password if provided
        password_hash = ""
        salt = ""
        if password:
            salt = secrets.token_hex(16)
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        now = _utc_now_iso()
        record = UserRecord(
            username=username,
            user_id=user_id,
            password_hash=password_hash,
            salt=salt,
            role=role,
            email=email.strip().lower() if email else "",
            tenant_id=tenant_id,
            sso_subject=sso_subject,
            sso_provider=sso_provider,
            full_name=full_name,
            is_active=True,
            is_admin=role == "admin",
            api_key=api_key,
            mfa_enabled=False,
            password_changed_at=now if password else "",
            last_login="",
            created_at=now,
            updated_at=now,
        )

        created = self._store.add_user(record)
        if not created:
            return None
        logger.info("Created user: %s (id=%s, role=%s)", username, user_id, role)
        return user_id

    def remove_user(self, username: str) -> bool:
        """Remove a user."""
        return self._store.remove_user(username)

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        Authenticate a user with username/password.
        Returns user_id on success, None on failure.
        """
        user = self._store.get_user(username)
        if not user:
            return None

        if not user.get("is_active", True):
            return None

        # Check lockout
        if self._is_locked(user):
            return None

        # Verify password
        password_hash = user.get("password_hash", "")
        if not password_hash:
            # User has no password (SSO only)
            return None

        if not bcrypt.checkpw(password.encode(), password_hash.encode()):
            self._register_failed_login(username, user)
            return None

        # Success - reset failed attempts and update last login
        now = _utc_now_iso()
        self._store.update_user(
            username,
            failed_login_attempts=0,
            locked_until="",
            last_login=now,
            updated_at=now,
        )
        return user.get("user_id")

    def _is_locked(self, user: Dict[str, Any]) -> bool:
        """True while a lockout window is still open."""
        locked_until = user.get("locked_until", "")
        if not locked_until:
            return False
        try:
            lock_time = datetime.fromisoformat(locked_until)
        except (TypeError, ValueError):
            return False
        return datetime.now(timezone.utc).replace(tzinfo=None) < lock_time

    def _register_failed_login(self, username: str, user: Dict[str, Any]) -> None:
        """Count a failed attempt and lock the account when it repeats."""
        attempts = int(user.get("failed_login_attempts", 0) or 0) + 1
        updates: Dict[str, Any] = {
            "failed_login_attempts": attempts,
            "updated_at": _utc_now_iso(),
        }
        if attempts >= MAX_FAILED_ATTEMPTS:
            lock_until = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(minutes=LOCKOUT_MINUTES)
            ).isoformat()
            updates["locked_until"] = lock_until
            logger.warning(
                "User %s locked for %s minutes after failed attempts",
                username, LOCKOUT_MINUTES,
            )
        self._store.update_user(username, **updates)

    def authenticate_api_key(self, api_key: str) -> Optional[str]:
        """Authenticate via API key. Returns username or None.

        Only a legacy *cleartext* key stored on the record matches — the
        application never writes one (keys are issued and verified by the
        hashed store, api/api_keys.py), so in practice this returns None and
        no cleartext key path can be revived by accident. Use
        ``api.api_keys.api_key_manager.verify_key()`` instead.
        """
        if not api_key:
            return None
        user = self._store.get_user_by_api_key(api_key)
        return user.get("username") if user else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Get user info by user_id."""
        user = self._store.get_user_by_id(user_id)
        return self._public_info(user, user.get("username", "")) if user else None

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user info by email."""
        user = self._store.get_user_by_email(email)
        return self._public_info(user, user.get("username", "")) if user else None

    def get_user_info(self, username: str) -> Optional[Dict]:
        """Get user info (without sensitive fields)."""
        user = self._store.get_user(username)
        return self._public_info(user, username) if user else None

    def get_user_info_by_id(self, user_id: str) -> Optional[Dict]:
        """Get user info by user_id."""
        user = self._store.get_user_by_id(user_id)
        return self._public_info(user, user.get("username", "")) if user else None

    @staticmethod
    def _public_info(user: Dict[str, Any], username: str) -> Dict[str, Any]:
        """The shareable view of a user — never any secret material."""
        return {
            "user_id": user.get("user_id", ""),
            "username": username,
            "role": user.get("role", "viewer"),
            "email": user.get("email", ""),
            "email_verified": bool(user.get("email_verified", False)),
            "invite_accepted_at": user.get("invite_accepted_at", ""),
            "tenant_id": user.get("tenant_id", ""),
            "sso_subject": user.get("sso_subject", ""),
            "sso_provider": user.get("sso_provider", ""),
            "full_name": user.get("full_name", ""),
            "is_active": user.get("is_active", True),
            "is_admin": user.get("is_admin", False),
            "mfa_enabled": user.get("mfa_enabled", False),
            "must_change_password": bool(user.get("must_change_password", False)),
            "created_at": user.get("created_at", ""),
            "last_login": user.get("last_login", ""),
            "has_api_key": bool(user.get("api_key", "")),
        }

    def update_user(self, username: str, **kwargs) -> bool:
        """Update user fields."""
        if not self._store.get_user(username):
            return False

        allowed_fields = {
            "role", "email", "tenant_id", "full_name", "is_active",
            "is_admin", "sso_subject", "sso_provider", "mfa_enabled", "mfa_secret",
            "last_login",
            # The account lifecycle (ADR-009).
            "email_verified", "pending_email", "invited_by", "invite_accepted_at",
            "last_login_at", "must_change_password",
        }
        updates = {key: value for key, value in kwargs.items() if key in allowed_fields}
        # The admin flag follows the role, otherwise a demoted admin keeps
        # passing the admin checks.
        if "role" in updates:
            updates["is_admin"] = updates["role"] == "admin"
        updates["updated_at"] = _utc_now_iso()
        return self._store.update_user(username, **updates)

    def set_password(self, username: str, new_password: str) -> bool:
        """Set/reset user password."""
        if not self._store.get_user(username):
            return False

        password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        now = _utc_now_iso()
        return self._store.set_password(
            username,
            password_hash,
            salt=secrets.token_hex(16),
            password_changed_at=now,
            updated_at=now,
        )

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change user password."""
        user = self._store.get_user(username)
        if not user:
            return False

        password_hash = user.get("password_hash", "")
        if not password_hash:
            return False
        if not bcrypt.checkpw(old_password.encode(), password_hash.encode()):
            return False

        return self.set_password(username, new_password)

    # -- the account lifecycle (ADR-009) -------------------------------

    def admin_reset_password(self, username: str, new_password: str) -> bool:
        """Reset somebody else's password (an administrator does this).

        Stamps ``password_changed_at`` like :meth:`set_password` does and
        additionally raises ``must_change_password``, so the account has to
        choose a password of its own before it is used again.
        """
        if not self.set_password(username, new_password):
            return False
        return self.set_must_change_password(username, True)

    def set_email(self, username: str, email: str) -> bool:
        """Set the account's e-mail address."""
        return self._store.set_email(username, email)

    def set_email_verified(self, username: str, verified: bool = True) -> bool:
        """Flip the e-mail-verified flag."""
        return self._store.set_email_verified(username, verified)

    def set_must_change_password(self, username: str, required: bool = True) -> bool:
        """Require (or clear) a password change at the next login."""
        return self._store.set_must_change_password(username, required)

    def set_password_changed_at(self, username: str, when: str = "") -> bool:
        """Stamp the moment the password was last changed."""
        return self._store.set_password_changed_at(username, when)

    def mark_invite_accepted(self, username: str) -> bool:
        """Stamp the moment an invitation was accepted."""
        return self._store.mark_invite_accepted(username)

    def list_users_by_tenant(self, tenant_id: str) -> List[Dict]:
        """Every account of one tenant."""
        return self._store.list_users_by_tenant(tenant_id)

    def get_users(self) -> List[Dict]:
        """Get all users (without sensitive fields)."""
        return [
            self._public_info(user, user.get("username", ""))
            for user in self._store.list_users()
        ]

    def generate_api_key(self, username: str) -> Optional[str]:
        """Generate a new API key for a user.

        Delegates to the hashed key store (api/api_keys.py), which is what
        AuthMiddleware validates against. This method used to mint and store
        a *cleartext* key inside the user directory, leaving usable
        credentials in a file that is copied around and shipped in release
        zips.
        """
        user = self._store.get_user(username)
        if not user:
            return None
        try:
            from api.api_keys import api_key_manager

            user_id = user.get("user_id", "")
            _record, raw_key = api_key_manager.create_key(
                user_id, name="legacy", scopes=["read"]
            )
            if raw_key:
                return raw_key
        except Exception:  # noqa: BLE001 — fall back to the legacy path
            logger.debug("hashed API key store unavailable for %s", username, exc_info=True)

        new_key = f"da_{secrets.token_hex(24)}"
        self._store.set_api_key(username, new_key)
        self._store.update_user(username, updated_at=_utc_now_iso())
        return new_key

    def revoke_api_key(self, username: str) -> bool:
        """Revoke user's API key. False when there was nothing to revoke.

        Keys issued by the hashed store are revoked there; the legacy
        cleartext entry is cleared too.
        """
        user = self._store.get_user(username)
        if not user:
            return False

        revoked = False
        try:
            from api.api_keys import api_key_manager

            user_id = user.get("user_id", "")
            for record in api_key_manager.list_keys(user_id):
                key_id = record.get("id") if isinstance(record, dict) else None
                if not key_id:
                    continue
                # Only count keys that are still active — revoking an
                # already-revoked key is a no-op, not a revocation.
                if record.get("revoked"):
                    continue
                try:
                    if api_key_manager.revoke_key(user_id, key_id):
                        revoked = True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001 — fall through to the legacy path
            logger.debug("hashed API key store unavailable for %s", username, exc_info=True)

        if user.get("api_key"):
            self._store.set_api_key(username, "")
            revoked = True
        if revoked:
            self._store.update_user(username, updated_at=_utc_now_iso())
        return revoked

    def has_users(self) -> bool:
        """Check if any users are configured."""
        return bool(self._store.list_users())

    def validate_password_policy(self, password: str) -> tuple[bool, List[str]]:
        """Validate password against policy."""
        from config import settings
        policy = settings.password_policy
        errors = []

        if len(password) < policy.min_length:
            errors.append(f"Password must be at least {policy.min_length} characters")

        if policy.require_uppercase and not any(c.isupper() for c in password):
            errors.append("Password must contain uppercase letter")

        if policy.require_lowercase and not any(c.islower() for c in password):
            errors.append("Password must contain lowercase letter")

        if policy.require_numbers and not any(c.isdigit() for c in password):
            errors.append("Password must contain number")

        if policy.require_special and not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            errors.append("Password must contain special character")

        return len(errors) == 0, errors


# Global instance
user_manager = UserManager()


def require_api_key(func):
    """Decorator for FastAPI endpoints requiring API key authentication."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        from fastapi import Request, HTTPException

        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break

        if request is None:
            for _, v in kwargs.items():
                if isinstance(v, Request):
                    request = v
                    break

        if request is None:
            raise HTTPException(status_code=500, detail="No request object found")

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            raise HTTPException(status_code=401, detail="API key required")

        username = user_manager.authenticate_api_key(api_key)
        if not username:
            raise HTTPException(status_code=403, detail="Invalid API key")

        # Attach user to request
        user_info = user_manager.get_user_info(username)
        if not user_info or not user_info.get("is_active", True):
            raise HTTPException(status_code=403, detail="Invalid API key")
        request.state.user = type('User', (), {
            'user_id': user_info.get('user_id'),
            'username': username,
            'role': user_info.get('role'),
            'tenant_id': user_info.get('tenant_id'),
            'is_admin': user_info.get('is_admin', False),
            'auth_method': 'api_key',
        })()

        return await func(*args, **kwargs)

    return wrapper

import jwt as pyjwt

# A published default (docker-compose.yml ships
# JWT_SECRET=change-me-in-production) must never become a live signing key:
# every deployment sharing it can have its session tokens forged. Fail closed.
_PLACEHOLDER_JWT_SECRETS = frozenset({
    "change-me-in-production",
    "change-me",
    "changeme",
    "your-secret-key",
    "your_secret_key",
    "secret",
    "jwt-secret",
    "jwt_secret",
    "development",
})
_MIN_JWT_SECRET_LENGTH = 16


def _validate_jwt_secret(secret: str, source: str) -> str:
    """Reject placeholder/short signing keys. Never echoes the value itself."""
    if secret.strip().lower() in _PLACEHOLDER_JWT_SECRETS:
        raise RuntimeError(
            "Refusing to start: the JWT signing key from {} is a published "
            "placeholder. Set JWT_SECRET to a random value of at least {} "
            'characters, e.g. python -c "import secrets; '
            'print(secrets.token_hex(32))"'.format(source, _MIN_JWT_SECRET_LENGTH)
        )
    if len(secret.strip()) < _MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            "Refusing to start: the JWT signing key from {} is shorter than {} "
            'characters. Generate one with python -c "import secrets; '
            'print(secrets.token_hex(32))"'.format(source, _MIN_JWT_SECRET_LENGTH)
        )
    return secret


def _load_jwt_secret() -> str:
    env_secret = os.environ.get("JWT_SECRET")
    if env_secret:
        return _validate_jwt_secret(env_secret, "the JWT_SECRET environment variable")
    secret_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", ".jwt_secret",
    )
    try:
        with open(secret_file, "r", encoding="utf-8") as f:
            existing = f.read().strip()
            if existing:
                return _validate_jwt_secret(existing, "data/.jwt_secret")
    except (FileNotFoundError, OSError):
        pass
    new_secret = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(new_secret)
    except OSError:
        pass
    return new_secret


JWT_SECRET = _load_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "dataagent"
JWT_AUDIENCE = "dataagent-api"

# Revoked JWT ids (logout / admin revoke). Checked on every verify.
# Persisted next to the user store so logout survives restarts; the path
# follows user_manager.auth_file, which keeps tests isolated via the
# isolated_user_store fixture.
_REVOKED_JTIS: set = set()
_REVOKED_EXP: Dict[str, float] = {}
_REVOKED_LOCK = None  # lazy threading.Lock


def _revoked_lock():
    import threading as _th

    global _REVOKED_LOCK
    if _REVOKED_LOCK is None:
        _REVOKED_LOCK = _th.Lock()
    return _REVOKED_LOCK


def _revoked_file() -> Path:
    override = os.environ.get("DATAAGENT_REVOKED_JTIS", "").strip()
    if override:
        return Path(override)
    try:
        return Path(user_manager.auth_file).parent / "revoked_jtis.json"
    except Exception:  # noqa: BLE001
        return Path("config/revoked_jtis.json")


# ── the shared revocation list (ADR-009 D2) ─────────────────────────────
#
# The in-process set plus the JSON file only ever covered the replica that
# wrote them: a token revoked on replica A stayed valid on replica B.  The
# authoritative list therefore lives in the shared SQLite identity database,
# so a revocation is refused on every replica.  The in-process set and the
# JSON file stay as the fallback for an install whose identity database is
# not available.


#: Namespaces a subject-level revocation so it can never collide with a
#: real token id (those are 32 hex characters).
_SUBJECT_PREFIX = "sub:"


def _subject_key(subject: str) -> str:
    return f"{_SUBJECT_PREFIX}{subject}"


def _epoch(value: Any) -> float:
    """A timestamp as epoch seconds (accepts a datetime or a number)."""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class RevocationStore:
    """The shared list of revoked token ids.

    Backed by the ``revoked_jtis`` table of the identity database, so every
    replica of a hosted deployment sees the same revocations.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else identity_db_path()
        self._lock = threading.RLock()
        self._conn = None
        self._open()

    # -- plumbing -------------------------------------------------------

    def _open(self) -> None:
        self._conn = connect_identity_db(self.db_path)

    def _connection(self):
        if self._conn is None:
            self._open()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001 — best effort
                    pass
                self._conn = None

    # -- writes ---------------------------------------------------------

    def create_revocation(self, jti: str, exp: float) -> bool:
        """Record a revoked token id. False when there is no id."""
        if not jti:
            return False
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO revoked_jtis (jti, subject, issued_before, expires_at, created_at) "
                "VALUES (?, '', 0, ?, ?) "
                "ON CONFLICT(jti) DO UPDATE SET expires_at = excluded.expires_at",
                (str(jti), float(exp or 0), time.time()),
            )
        return True

    def revoke_subject(self, subject: str, exp: float) -> bool:
        """Refuse every token of *subject* that was issued before now."""
        if not subject:
            return False
        now = time.time()
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO revoked_jtis (jti, subject, issued_before, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(jti) DO UPDATE SET issued_before = excluded.issued_before, "
                "expires_at = excluded.expires_at",
                (_subject_key(subject), str(subject), now, float(exp or 0), now),
            )
        return True

    def purge_expired(self) -> int:
        """Drop the revocations whose token has expired anyway."""
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "DELETE FROM revoked_jtis WHERE expires_at > 0 AND expires_at <= ?",
                (time.time(),),
            )
        return int(cursor.rowcount or 0)

    # -- reads ----------------------------------------------------------

    def is_revoked(self, jti: str) -> bool:
        """True when *jti* was revoked and the revocation is still valid."""
        if not jti:
            return False
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT expires_at FROM revoked_jtis WHERE jti = ?", (str(jti),)
            ).fetchone()
        if row is None:
            return False
        expires_at = float(row["expires_at"] or 0)
        return expires_at <= 0 or expires_at > time.time()

    def is_subject_revoked(self, subject: str, issued_at: Any) -> bool:
        """True when a token of *subject* issued at *issued_at* is dead."""
        if not subject or issued_at is None:
            return False
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT issued_before, expires_at FROM revoked_jtis WHERE jti = ?",
                (_subject_key(subject),),
            ).fetchone()
        if row is None:
            return False
        expires_at = float(row["expires_at"] or 0)
        if expires_at > 0 and expires_at <= time.time():
            return False
        cutoff = float(row["issued_before"] or 0)
        if cutoff <= 0:
            return False
        return _epoch(issued_at) <= cutoff


_REVOCATION_STORES: Dict[str, RevocationStore] = {}
_REVOCATION_STORES_LOCK = threading.Lock()
_revocation_warned = False


def get_revocation_store(db_path: str | Path | None = None) -> RevocationStore:
    """The revocation store for *db_path* (cached per path)."""
    global _revocation_warned
    path = Path(db_path) if db_path else identity_db_path()
    key = str(path)
    with _REVOCATION_STORES_LOCK:
        store = _REVOCATION_STORES.get(key)
        if store is None:
            store = RevocationStore(path)
            _REVOCATION_STORES[key] = store
        _revocation_warned = False
        return store


def reset_revocation_store_cache() -> None:
    """Drop the cached stores (used by tests and by a settings reload)."""
    global _revocation_warned
    with _REVOCATION_STORES_LOCK:
        for store in _REVOCATION_STORES.values():
            try:
                store.close()
            except Exception:  # noqa: BLE001 — best effort
                pass
        _REVOCATION_STORES.clear()
        _revocation_warned = False


def _revocation_store() -> Optional[RevocationStore]:
    """The shared store, or None when the identity database is unavailable."""
    global _revocation_warned
    try:
        return get_revocation_store()
    except Exception as exc:  # noqa: BLE001 — fall back to the local list
        if not _revocation_warned:
            _revocation_warned = True
            logger.warning(
                "The shared revocation list is unavailable (%s) — "
                "falling back to the local one",
                redact_secrets(exc),
            )
        return None


def create_revocation(jti: str, exp: float) -> bool:
    """Record a revoked token id in the shared list."""
    store = _revocation_store()
    if store is None:
        return False
    try:
        return store.create_revocation(jti, exp)
    except Exception as exc:  # noqa: BLE001 — never break a request
        logger.warning("Could not record the revocation: %s", redact_secrets(exc))
        return False


def is_revoked(jti: str) -> bool:
    """True when *jti* was revoked (the shared list first, the local one next)."""
    if not jti:
        return False
    if jti in _REVOKED_JTIS:
        return True
    store = _revocation_store()
    if store is None:
        return False
    try:
        return store.is_revoked(jti)
    except Exception as exc:  # noqa: BLE001 — never break a request
        logger.warning("Could not read the revocation list: %s", redact_secrets(exc))
        return False


def purge_expired() -> int:
    """Drop the revocations whose token has expired anyway."""
    store = _revocation_store()
    if store is None:
        return 0
    try:
        return store.purge_expired()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not purge the revocation list: %s", redact_secrets(exc))
        return 0


def _jwt_lifetime_hours() -> float:
    """How long an issued token stays valid, in hours."""
    try:
        from config import settings

        return float(settings.session.token_expiry_hours or 24)
    except Exception:  # noqa: BLE001
        return 24.0


def revoke_user_sessions(username: str, lifetime_hours: Optional[float] = None) -> bool:
    """Refuse every session token of *username* that was issued before now.

    A password reset has to kill the sessions that were opened with the old
    password, otherwise a stolen token outlives the credential it was issued
    for.  The revocation is recorded in the shared list, so it holds on every
    replica.
    """
    if not username:
        return False
    hours = _jwt_lifetime_hours() if lifetime_hours is None else float(lifetime_hours)
    expires_at = time.time() + max(1.0, hours) * 3600
    store = _revocation_store()
    if store is None:
        return False
    try:
        return store.revoke_subject(username, expires_at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not revoke the sessions: %s", redact_secrets(exc))
        return False


def is_user_session_revoked(username: str, issued_at: Any) -> bool:
    """True when a token of *username* issued at *issued_at* was revoked."""
    if not username:
        return False
    store = _revocation_store()
    if store is None:
        return False
    try:
        return store.is_subject_revoked(username, issued_at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the revocation list: %s", redact_secrets(exc))
        return False


def _load_revoked() -> None:
    """Load persisted revocations, dropping expired entries."""
    import time as _time

    path = _revoked_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return
    now = _time.time()
    if isinstance(raw, dict):
        for jti, exp in raw.items():
            try:
                if float(exp) > now:
                    _REVOKED_JTIS.add(str(jti))
                    _REVOKED_EXP[str(jti)] = float(exp)
            except (ValueError, TypeError):
                continue


def _save_revoked() -> None:
    """Persist revocations (best-effort, prunes expired first)."""
    import time as _time

    with _revoked_lock():
        now = _time.time()
        for jti in [j for j, exp in _REVOKED_EXP.items() if exp <= now]:
            _REVOKED_EXP.pop(jti, None)
            _REVOKED_JTIS.discard(jti)
        path = _revoked_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            import os as _os
            import tempfile as _tf
            fd, tmp = _tf.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with open(fd, "w", encoding="utf-8") as fh:
                    json.dump(_REVOKED_EXP, fh, indent=2)
                _os.replace(tmp, path)
            finally:
                try:
                    _os.unlink(tmp)
                except Exception:  # noqa: BLE001
                    pass
        except OSError as e:
            logger.warning(f"Failed to persist JWT revocations: {e}")


_load_revoked()


def create_jwt_token(username: str, role: str = "viewer", expires_delta: Optional[int] = None) -> str:
    """Create a JWT token for a user.

    Args:
        username: Username
        role: User role (admin, analyst, viewer)
        expires_delta: Token expiry in hours (defaults to
            ``settings.session.token_expiry_hours``, 24h)

    Returns:
        JWT token string
    """
    if expires_delta is None:
        try:
            from config import settings
            expires_delta = settings.session.token_expiry_hours
        except Exception:  # noqa: BLE001
            expires_delta = 24
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    payload = {
        "sub": username,
        "role": role,
        "jti": secrets.token_hex(16),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=expires_delta),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def revoke_jwt_token(token: str) -> bool:
    """Revoke a JWT so subsequent verify_jwt_token calls reject it.

    Returns True if a jti was recorded, False if the token was unusable.

    The revocation is written to the *shared* list first, so it holds on
    every replica; the in-process set and the JSON file stay as the local
    fallback for an install without a usable identity database.
    """
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                               audience=JWT_AUDIENCE, issuer=JWT_ISSUER)
    except pyjwt.PyJWTError:
        return False
    jti = payload.get("jti")
    if not jti:
        return False
    try:
        expires_at = float(payload.get("exp") or 0)
    except (ValueError, TypeError):
        expires_at = 0
    create_revocation(jti, expires_at)
    _REVOKED_JTIS.add(jti)
    _REVOKED_EXP[jti] = expires_at
    _save_revoked()
    return True


def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a JWT token and return the payload.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict, or None if invalid/expired/revoked
    """
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                               audience=JWT_AUDIENCE, issuer=JWT_ISSUER)
    except pyjwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except pyjwt.PyJWTError as e:
        logger.warning(f"Invalid JWT token: {e}")
        return None
    jti = payload.get("jti")
    if jti and is_revoked(jti):
        logger.warning("JWT token revoked")
        return None
    subject = payload.get("sub", "")
    if subject and is_user_session_revoked(subject, payload.get("iat")):
        logger.warning("JWT token revoked for the account %s", subject)
        return None
    return payload


def require_role(roles: List[str]):
    """Decorator for FastAPI endpoints requiring specific roles.
    
    Args:
        roles: List of allowed role names
    
    Usage:
        @router.get("/admin")
        @require_role(["admin"])
        async def admin_endpoint(request: Request):
            ...
    """
    from fastapi import Request, HTTPException
    from functools import wraps
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                for _, v in kwargs.items():
                    if isinstance(v, Request):
                        request = v
                        break
            if request is None or not hasattr(request.state, 'user'):
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_role = getattr(request.state.user, 'role', '')
            if user_role not in roles:
                raise HTTPException(status_code=403, detail=f"Requires one of: {roles}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator