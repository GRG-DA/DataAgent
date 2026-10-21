"""Ordered, versioned schema migrations.

The ad-hoc ``ALTER TABLE`` migrations that ran inside each module's
``__init__`` worked, but they had two problems for a hosted deployment:

* they were **unordered** — a fresh database and an upgraded one could end
  up with different shapes depending on which module was imported first;
* there was **no ledger**, so nobody could tell what had been applied.

This module is a small, dependency-free migration runner:

    from api.migrations import run_migrations
    run_migrations()          # applies everything not yet recorded

Each migration is a ``(version, name, sql_or_callable)``.  Versions are
integers and must increase.  The ledger lives in the identity database
(``data/users.db``) so it is shared across replicas and survives a
warehouse rebuild.

Migrations must be **idempotent** (``IF NOT EXISTS``) because a partially
applied run has to be safe to repeat.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: A migration is either a list of SQL statements or a callable that
#: performs the work (for a migration that needs Python).  A callable
#: receives the database connection.
Migration = Union[Sequence[str], Callable[[Any], None]]

#: The ordered migration ledger. Append only — never renumber, never edit a
#: released migration, because a deployed instance would skip or repeat it.
MIGRATIONS: List[Tuple[int, str, Migration]] = [
    (
        1,
        "user_store_columns",
        # The user store already creates these columns on a fresh database,
        # so the migration must tolerate them being present. `add_column` is
        # a no-op when the column exists, which keeps the migration safe to
        # re-run after a partial application.
        lambda conn: _add_columns(
            conn,
            "users",
            [
                ("email_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("pending_email", "TEXT"),
                ("invited_by", "TEXT"),
                ("invite_accepted_at", "TEXT"),
                ("last_login_at", "TEXT"),
                ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
            ],
        ),
    ),
    (
        2,
        "account_tokens",
        [
            """CREATE TABLE IF NOT EXISTS account_tokens (
                   id TEXT PRIMARY KEY,
                   token_hash TEXT NOT NULL UNIQUE,
                   kind TEXT NOT NULL,
                   user_id TEXT NOT NULL,
                   expires_at TIMESTAMP NOT NULL,
                   used_at TIMESTAMP,
                   created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   meta TEXT
               )""",
            "CREATE INDEX IF NOT EXISTS ix_account_tokens_user ON account_tokens(user_id)",
            "CREATE INDEX IF NOT EXISTS ix_account_tokens_expires ON account_tokens(expires_at)",
        ],
    ),
    (
        3,
        "revoked_jtis",
        [
            """CREATE TABLE IF NOT EXISTS revoked_jtis (
                   jti TEXT PRIMARY KEY,
                   expires_at TIMESTAMP,
                   revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   subject TEXT
               )""",
            "CREATE INDEX IF NOT EXISTS ix_revoked_jtis_expires ON revoked_jtis(expires_at)",
        ],
    ),
]


def _ledger_table() -> str:
    return "schema_migrations"


def _columns(conn, table: str) -> set:
    """Return the column names of *table* (empty when the table is absent)."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:  # noqa: BLE001
        return set()
    return {str(r[1]) for r in rows}


def _add_columns(conn, table: str, columns: List[Tuple[str, str]]) -> None:
    """Add *columns* to *table*, skipping any that already exist.

    ``ALTER TABLE ... ADD COLUMN`` has no ``IF NOT EXISTS`` in SQLite, and
    the user store creates these columns itself on a fresh database, so
    the statement has to be guarded.
    """
    existing = _columns(conn, table)
    for name, spec in columns:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")
        existing.add(name)


def ensure_ledger(conn) -> None:
    """Create the ledger table if it does not exist."""
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_ledger_table()} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
    )


def applied_versions(conn) -> set:
    """Return the set of versions already recorded in the ledger."""
    ensure_ledger(conn)
    try:
        rows = conn.execute(f"SELECT version FROM {_ledger_table()}").fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read the migration ledger: %s", exc)
        return set()
    return {int(r[0]) for r in rows}


def _apply_one(conn, version: int, name: str, migration: Migration) -> None:
    if callable(migration):
        migration(conn)
    else:
        for statement in migration:
            if not statement or not statement.strip():
                continue
            conn.execute(statement)
    conn.execute(
        f"INSERT OR REPLACE INTO {_ledger_table()} (version, name) VALUES (?, ?)",
        (version, name),
    )


def run_migrations(conn=None) -> List[int]:
    """Apply every migration that is not yet recorded.

    Returns the list of versions applied in this run. A migration that
    fails is **not** recorded, so the next start retries it — but the
    statements must be idempotent, because a partial application is
    possible.
    """
    owned = conn is None
    if owned:
        from api.user_store import connect_identity_db

        conn = connect_identity_db()
    try:
        done = applied_versions(conn)
        applied: List[int] = []
        for version, name, migration in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version in done:
                continue
            try:
                _apply_one(conn, version, name, migration)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "migration %d (%s) failed: %s — it will be retried on the "
                    "next start", version, name, exc,
                )
                break
            applied.append(version)
            logger.info("applied migration %d (%s)", version, name)
        return applied
    finally:
        if owned:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
