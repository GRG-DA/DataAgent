"""
API Key Management Module
Scoped API keys with rotation and expiration
"""

import hashlib
import secrets
import bcrypt
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class APIKeyScope(str, Enum):
    """API key scopes."""
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


@dataclass
class APIKey:
    """API key record."""
    id: str
    user_id: str
    name: str
    key_hash: str
    key_prefix: str
    scopes: List[str]
    # Naive UTC, like every other timestamp in this module (see is_valid()).
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if key is valid (not revoked, not expired)."""
        if self.revoked:
            return False
        if self.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > self.expires_at:
            return False
        return True

    def is_expiring_soon(self, warning_days: int = 14) -> bool:
        """Check if key is expiring within warning period."""
        if not self.expires_at:
            return False
        warning_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=warning_days)
        return self.expires_at <= warning_date

    def to_dict(self, include_hash: bool = False) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        if self.expires_at:
            data["expires_at"] = self.expires_at.isoformat()
        if self.last_used_at:
            data["last_used_at"] = self.last_used_at.isoformat()
        if self.revoked_at:
            data["revoked_at"] = self.revoked_at.isoformat()
        if not include_hash:
            data.pop("key_hash", None)
        return data


@dataclass
class APIKeyContext:
    """Context from validated API key."""
    user_id: str
    key_id: str
    scopes: Set[APIKeyScope]
    tenant_id: Optional[str] = None


class APIKeyStore(ABC):
    """Abstract API key store."""

    @abstractmethod
    def create(self, key: APIKey) -> APIKey:
        pass

    @abstractmethod
    def get(self, key_id: str) -> Optional[APIKey]:
        pass

    @abstractmethod
    def get_by_prefix(self, prefix: str) -> Optional[APIKey]:
        pass

    @abstractmethod
    def update(self, key: APIKey) -> bool:
        pass

    @abstractmethod
    def delete(self, key_id: str) -> bool:
        pass

    @abstractmethod
    def list_user_keys(self, user_id: str) -> List[APIKey]:
        pass


class DuckDBAPIKeyStore(APIKeyStore):
    """DuckDB-backed API key store."""

    def __init__(self, db_path: str = "data/curated/warehouse.duckdb"):
        self.db_path = db_path
        self._init_table()

    def _get_conn(self):
        import duckdb
        conn = duckdb.connect(self.db_path)
        conn.execute("SET search_path = 'analytics'")
        return conn

    def _init_table(self):
        with self._get_conn() as conn:
            # Ensure the schema exists before creating the table (the module
            # may be imported before the warehouse schema is created).
            conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.api_keys (
                    key_id VARCHAR NOT NULL,
                    user_id VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    key_prefix VARCHAR(8) NOT NULL,
                    scopes VARCHAR[] NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP,
                    last_used_at TIMESTAMP,
                    revoked BOOLEAN DEFAULT FALSE,
                    revoked_at TIMESTAMP
                )
            """)
            self._migrate_drop_indexes(conn)

    @staticmethod
    def _migrate_drop_indexes(conn):
        """Drop PK/secondary indexes from legacy api_keys tables.

        DuckDB cannot UPDATE (or DELETE+INSERT in one transaction) rows in a
        table that has a PRIMARY KEY or secondary ART index — it raises
        'Duplicate key ... violates primary key constraint'. That made every
        key mutation (revoke/rotate/last_used tracking) fail. Uniqueness of
        key_id is guaranteed by the ID generator, so indexes are unnecessary;
        rebuild the table without them if a legacy definition is found.
        """
        has_pk = conn.execute("""
            SELECT COUNT(*) FROM duckdb_constraints()
            WHERE schema_name = 'analytics'
              AND table_name = 'api_keys'
              AND constraint_type = 'PRIMARY KEY'
        """).fetchone()[0]
        has_index = conn.execute("""
            SELECT COUNT(*) FROM duckdb_indexes()
            WHERE schema_name = 'analytics'
              AND table_name = 'api_keys'
        """).fetchone()[0]
        if not has_pk and not has_index:
            return
        # Drop dependent indexes first — ALTER TABLE RENAME fails while any
        # index still references the table.
        conn.execute("DROP INDEX IF EXISTS analytics.idx_api_keys_user")
        conn.execute("DROP INDEX IF EXISTS analytics.idx_api_keys_prefix")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("ALTER TABLE analytics.api_keys RENAME TO api_keys_legacy")
            conn.execute("""
                CREATE TABLE analytics.api_keys (
                    key_id VARCHAR NOT NULL,
                    user_id VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    key_prefix VARCHAR(8) NOT NULL,
                    scopes VARCHAR[] NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP,
                    last_used_at TIMESTAMP,
                    revoked BOOLEAN DEFAULT FALSE,
                    revoked_at TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO analytics.api_keys
                SELECT * FROM analytics.api_keys_legacy
            """)
            conn.execute("DROP TABLE analytics.api_keys_legacy")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _row_to_key(self, row) -> APIKey:
        return APIKey(
            id=row[0],
            user_id=row[1],
            name=row[2],
            key_hash=row[3],
            key_prefix=row[4],
            scopes=list(row[5]) if row[5] else [],
            created_at=row[6],
            expires_at=row[7],
            last_used_at=row[8],
            revoked=row[9],
            revoked_at=row[10],
        )

    def create(self, key: APIKey) -> APIKey:
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.api_keys
                (key_id, user_id, name, key_hash, key_prefix, scopes, created_at, expires_at, last_used_at, revoked, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                key.id, key.user_id, key.name, key.key_hash, key.key_prefix,
                key.scopes, key.created_at, key.expires_at, key.last_used_at,
                key.revoked, key.revoked_at
            ])
        return key

    def get(self, key_id: str) -> Optional[APIKey]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.api_keys WHERE key_id = ?", [key_id]
            ).fetchone()
        return self._row_to_key(row) if row else None

    def get_by_prefix(self, prefix: str) -> Optional[APIKey]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.api_keys WHERE key_prefix = ? AND revoked = FALSE", [prefix]
            ).fetchone()
        return self._row_to_key(row) if row else None

    def update(self, key: APIKey) -> bool:
        # NOTE: Plain UPDATE fails on this table with
        #   ConstraintException: Duplicate key "key_id: ..." violates primary key
        # because DuckDB implements UPDATE as delete+insert internally, which
        # collides with the PRIMARY KEY / secondary ART indexes. DELETE+INSERT
        # inside one transaction achieves the same result reliably.
        with self._get_conn() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "DELETE FROM analytics.api_keys WHERE key_id = ?", [key.id]
                )
                conn.execute("""
                    INSERT INTO analytics.api_keys
                    (key_id, user_id, name, key_hash, key_prefix, scopes,
                     created_at, expires_at, last_used_at, revoked, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    key.id, key.user_id, key.name, key.key_hash, key.key_prefix,
                    key.scopes, key.created_at, key.expires_at, key.last_used_at,
                    key.revoked, key.revoked_at
                ])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True

    def delete(self, key_id: str) -> bool:
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for DML; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.api_keys WHERE key_id = ?", [key_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.api_keys WHERE key_id = ?", [key_id]
            )
            return True

    def list_user_keys(self, user_id: str) -> List[APIKey]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM analytics.api_keys WHERE user_id = ? ORDER BY created_at DESC", [user_id]
            ).fetchall()
        return [self._row_to_key(row) for row in rows]


class InMemoryAPIKeyStore(APIKeyStore):
    """In-memory API key store (fallback)."""

    def __init__(self):
        self._keys: Dict[str, APIKey] = {}
        self._prefix_index: Dict[str, str] = {}

    def create(self, key: APIKey) -> APIKey:
        self._keys[key.id] = key
        self._prefix_index[key.key_prefix] = key.id
        return key

    def get(self, key_id: str) -> Optional[APIKey]:
        return self._keys.get(key_id)

    def get_by_prefix(self, prefix: str) -> Optional[APIKey]:
        key_id = self._prefix_index.get(prefix)
        if key_id:
            return self._keys.get(key_id)
        return None

    def update(self, key: APIKey) -> bool:
        if key.id in self._keys:
            self._keys[key.id] = key
            return True
        return False

    def delete(self, key_id: str) -> bool:
        key = self._keys.pop(key_id, None)
        if key:
            self._prefix_index.pop(key.key_prefix, None)
            return True
        return False

    def list_user_keys(self, user_id: str) -> List[APIKey]:
        return [k for k in self._keys.values() if k.user_id == user_id]


class APIKeyManager:
    """Manages API keys with scoping, rotation, and expiration."""

    def __init__(
        self,
        store: Optional[APIKeyStore] = None,
        config: Optional[dict] = None
    ):
        self.config = config or settings.api_keys.model_dump()
        self.store = store or self._create_default_store()

    def _create_default_store(self) -> APIKeyStore:
        try:
            return DuckDBAPIKeyStore()
        except Exception as e:
            logger.warning(f"DuckDB store unavailable, using in-memory: {e}")
            return InMemoryAPIKeyStore()

    def _hash_key(self, key: str) -> str:
        """Hash API key with bcrypt."""
        return bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()

    def _verify_key(self, key: str, key_hash: str) -> bool:
        """Verify API key against hash."""
        try:
            return bcrypt.checkpw(key.encode(), key_hash.encode())
        except Exception:
            return False

    def _generate_key(self) -> tuple[str, str, str]:
        """Generate new API key. Returns (full_key, prefix, key_id)."""
        key_id = secrets.token_urlsafe(16)
        raw_key = f"da_{secrets.token_urlsafe(32)}"
        prefix = raw_key[:12]  # da_xxxxxxxx
        return raw_key, prefix, key_id

    def create_key(
        self,
        user_id: str,
        name: str,
        scopes: List[str],
        expires_days: Optional[int] = None,
        tenant_id: Optional[str] = None
    ) -> tuple[APIKey, str]:
        """
        Create a new API key.
        Returns (APIKey object, raw_key).
        """
        # Validate scopes
        valid_scopes = set(self.config.get("scopes", ["read", "write", "admin"]))
        for scope in scopes:
            if scope not in valid_scopes:
                raise ValueError(f"Invalid scope: {scope}")

        # Check max keys per user
        existing = self.store.list_user_keys(user_id)
        active = [k for k in existing if k.is_valid()]
        max_keys = self.config.get("max_per_user", 5)
        if len(active) >= max_keys:
            raise ValueError(f"Maximum {max_keys} API keys per user")

        # Generate key
        raw_key, prefix, key_id = self._generate_key()
        key_hash = self._hash_key(raw_key)

        # Calculate expiry
        expiry_days = expires_days or self.config.get("default_expiry_days", 90)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expiry_days)

        api_key = APIKey(
            id=key_id,
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            key_prefix=prefix,
            scopes=scopes,
            expires_at=expires_at,
        )

        self.store.create(api_key)
        return api_key, raw_key

    def validate_key(self, raw_key: str, required_scope: Optional[str] = None) -> Optional[APIKeyContext]:
        """
        Validate an API key and return context.
        Returns None if invalid.
        """
        prefix = raw_key[:12]
        api_key = self.store.get_by_prefix(prefix)

        if not api_key or not api_key.is_valid():
            return None

        if not self._verify_key(raw_key, api_key.key_hash):
            return None

        # Update last used
        api_key.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.store.update(api_key)

        # Check scope
        key_scopes = {APIKeyScope(s) for s in api_key.scopes}
        if required_scope and APIKeyScope(required_scope) not in key_scopes:
            return None

        return APIKeyContext(
            user_id=api_key.user_id,
            key_id=api_key.id,
            scopes=key_scopes,
        )

    def revoke_key(self, user_id: str, key_id: str) -> bool:
        """Revoke an API key."""
        api_key = self.store.get(key_id)
        if not api_key or api_key.user_id != user_id:
            return False

        api_key.revoked = True
        api_key.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.store.update(api_key)

    def rotate_key(self, user_id: str, key_id: str) -> tuple[APIKey, str]:
        """Rotate an API key (revoke old, create new with same settings)."""
        api_key = self.store.get(key_id)
        if not api_key or api_key.user_id != user_id:
            raise ValueError("Key not found or access denied")

        # Revoke the old key FIRST: create_key() enforces the max-keys-per-user
        # cap, so rotating while at the cap would otherwise always fail.
        self.revoke_key(user_id, key_id)

        # Create new key with same settings
        new_key, raw_key = self.create_key(
            user_id=user_id,
            name=api_key.name,
            scopes=api_key.scopes,
            expires_days=None,  # Use default
        )

        return new_key, raw_key

    def list_keys(self, user_id: str) -> List[Dict[str, Any]]:
        """List user's API keys (without hashes)."""
        keys = self.store.list_user_keys(user_id)
        return [k.to_dict(include_hash=False) for k in keys]

    def get_key_info(self, user_id: str, key_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed key info."""
        api_key = self.store.get(key_id)
        if not api_key or api_key.user_id != user_id:
            return None
        return api_key.to_dict(include_hash=False)

    def check_expiring_keys(self, user_id: str) -> List[Dict[str, Any]]:
        """Check for keys expiring soon."""
        keys = self.store.list_user_keys(user_id)
        warning_days = self.config.get("rotation_warning_days", 14)
        expiring = []
        for key in keys:
            if key.is_valid() and key.is_expiring_soon(warning_days):
                expiring.append(key.to_dict(include_hash=False))
        return expiring


# Global instance
api_key_manager = APIKeyManager()


def get_api_key_manager() -> APIKeyManager:
    """Dependency injection for API key manager."""
    return api_key_manager