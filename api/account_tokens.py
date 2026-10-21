"""Short-lived, single-use account tokens (ADR-009 account lifecycle).

Three flows need a one-shot secret that travels by mail: a password reset,
an e-mail verification and an invitation.  All three are the same shape:

* **short-lived** — a token that was mailed an hour ago is worthless now;
* **single-use** — the second attempt to redeem it fails, even in a race;
* **hashed at rest** — the database only ever holds a SHA-256 digest, so a
  leaked ``data/users.db`` does not hand out live reset links.

The tokens live in the shared SQLite identity database (next to the user
directory), so they survive a restart and every replica of a hosted
deployment sees the same token.

Nothing here logs a token: only its kind and the account it belongs to.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.user_store import connect_identity_db, identity_db_path

logger = logging.getLogger(__name__)

#: The kinds of token the store accepts.  An unknown kind is refused, so a
#: token minted for one flow can never be redeemed in another.
KIND_PASSWORD_RESET = "password_reset"
KIND_EMAIL_VERIFICATION = "email_verification"
KIND_INVITE = "invite"

TOKEN_KINDS = (KIND_PASSWORD_RESET, KIND_EMAIL_VERIFICATION, KIND_INVITE)

#: How long a password-reset token stays valid.
DEFAULT_TTL_SECONDS = 3600

#: How many bytes of entropy a raw token carries.
_TOKEN_BYTES = 32


def hash_token(raw_token: str) -> str:
    """The digest stored for *raw_token*.

    SHA-256 of the raw token: the token is already 256 bits of entropy, so
    there is nothing to stretch, and a fast digest keeps the lookup cheap.
    """
    return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()


class AccountTokenStore:
    """The account-token table."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else identity_db_path()
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # -- plumbing -------------------------------------------------------

    def _open(self) -> None:
        self._conn = connect_identity_db(self.db_path)

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

    # -- writes ---------------------------------------------------------

    def create_token(
        self,
        kind: str,
        user_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Mint a token and return it **once**.

        The raw token is never stored: only its digest goes into the table,
        so the caller has to put it in the mail right away.
        """
        if kind not in TOKEN_KINDS:
            raise ValueError(f"Unknown token kind: {kind}")
        raw = secrets.token_urlsafe(_TOKEN_BYTES)
        now = time.time()
        expires_at = now + max(1, int(ttl_seconds))
        payload = json.dumps(meta or {}, sort_keys=True)
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO account_tokens "
                "(token_hash, kind, user_id, expires_at, created_at, used_at, meta) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (hash_token(raw), kind, str(user_id or ""), expires_at, now, payload),
            )
        logger.info("Minted a %s token for the account %s", kind, user_id)
        return raw

    def consume_token(self, kind: str, raw_token: str) -> Optional[Dict[str, Any]]:
        """Redeem a token. Returns the record, or None when it is not usable.

        Fails closed: an unknown kind, an unknown token, an expired token
        and an already-used token all return None.  The redemption is a
        single ``UPDATE ... WHERE used_at = 0`` inside an immediate
        transaction, so two racing requests cannot both win.
        """
        if kind not in TOKEN_KINDS or not raw_token:
            return None
        token_hash = hash_token(raw_token)
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = self._fetch(conn, kind, token_hash)
                if row is None or not hmac.compare_digest(str(row["token_hash"]), token_hash):
                    conn.execute("ROLLBACK")
                    return None
                if float(row["used_at"] or 0) > 0:
                    conn.execute("ROLLBACK")
                    return None
                if float(row["expires_at"] or 0) <= time.time():
                    conn.execute(
                        "DELETE FROM account_tokens WHERE token_hash = ?", (token_hash,)
                    )
                    conn.execute("COMMIT")
                    return None
                cursor = conn.execute(
                    "UPDATE account_tokens SET used_at = ? "
                    "WHERE token_hash = ? AND used_at = 0",
                    (time.time(), token_hash),
                )
                if cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute("COMMIT")
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                logger.warning("Could not redeem the %s token: %s", kind, exc)
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                return None
        return self._as_record(row)

    def revoke_user_tokens(self, user_id: str) -> int:
        """Drop every outstanding token of one account. Returns the count."""
        if not user_id:
            return 0
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "DELETE FROM account_tokens WHERE user_id = ?", (str(user_id),)
            )
        return int(cursor.rowcount or 0)

    def purge_expired(self) -> int:
        """Delete the tokens that have expired. Returns the count."""
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "DELETE FROM account_tokens WHERE expires_at <= ?", (time.time(),)
            )
        return int(cursor.rowcount or 0)

    # -- reads ----------------------------------------------------------

    def list_tokens(self, kind: str = "", user_id: str = "") -> List[Dict[str, Any]]:
        """The stored tokens (without the raw value — there is none)."""
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if user_id:
            clauses.append("user_id = ?")
            params.append(str(user_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                f"SELECT * FROM account_tokens{where} ORDER BY created_at", params
            ).fetchall()
        return [self._as_record(row) for row in rows]

    # -- internals ------------------------------------------------------

    @staticmethod
    def _fetch(
        conn: sqlite3.Connection, kind: str, token_hash: str
    ) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM account_tokens WHERE token_hash = ? AND kind = ? LIMIT 1",
            (token_hash, kind),
        ).fetchone()

    @staticmethod
    def _as_record(row: sqlite3.Row) -> Dict[str, Any]:
        """The public view of a token row."""
        try:
            meta = json.loads(row["meta"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return {
            "kind": row["kind"],
            "user_id": row["user_id"],
            "expires_at": float(row["expires_at"] or 0),
            "created_at": float(row["created_at"] or 0),
            "meta": meta,
        }


# ── the process-wide store ─────────────────────────────────────────────


_STORES: Dict[str, AccountTokenStore] = {}
_STORES_LOCK = threading.Lock()


def get_token_store(db_path: str | Path | None = None) -> AccountTokenStore:
    """The account-token store for *db_path* (cached per path)."""
    path = Path(db_path) if db_path else identity_db_path()
    key = str(path)
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = AccountTokenStore(path)
            _STORES[key] = store
        return store


def reset_token_store_cache() -> None:
    """Drop the cached stores (used by tests and by a settings reload)."""
    with _STORES_LOCK:
        for store in _STORES.values():
            try:
                store.close()
            except Exception:  # noqa: BLE001 — best effort
                pass
        _STORES.clear()


def create_token(
    kind: str,
    user_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Mint a token and return it once."""
    return get_token_store().create_token(kind, user_id, ttl_seconds, meta)


def consume_token(kind: str, raw_token: str) -> Optional[Dict[str, Any]]:
    """Redeem a token (single-use)."""
    return get_token_store().consume_token(kind, raw_token)


def revoke_user_tokens(user_id: str) -> int:
    """Drop every outstanding token of one account."""
    return get_token_store().revoke_user_tokens(user_id)
