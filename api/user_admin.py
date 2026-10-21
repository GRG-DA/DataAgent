"""Admin operations on user accounts (ADR-009 D4).

Roles become a managed resource: an administrator changes them over HTTP and
deleting an account cascades to everything that account owns. The logic lives
here — not in ``api/main.py`` — so the HTTP layer stays thin and the cascade
can be exercised on its own.

The warehouse is DuckDB and single-writer, so the cascade goes through the
shared connection in ``api/db.py`` under its query lock.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

logger = logging.getLogger(__name__)

#: Platform tables holding rows that belong to exactly one account, with the
#: column that carries the owner's user id.
USER_SCOPED_TABLES: Sequence[tuple[str, str]] = (
    ("dashboards", "owner_id"),
    ("pbi_studios", "owner_id"),
    ("chat_conversations", "user_id"),
    ("chat_messages", "user_id"),
    ("query_history", "user_id"),
    ("user_preferences", "user_id"),
    ("api_keys", "user_id"),
    ("user_subscriptions", "user_id"),
    ("notification_preferences", "user_id"),
    ("notifications", "user_id"),
    ("webhook_endpoints", "user_id"),
)

#: The tenant schema prefix of ADR-009 D1 (``analytics_t_<tenant_slug>``).
TENANT_SCHEMA_PREFIX = "analytics_t_"

#: A schema name that is safe to interpolate into DDL.
_SAFE_SCHEMA = re.compile(r"^analytics_t_[a-z0-9_]{1,64}$")


class CascadeError(RuntimeError):
    """The warehouse could not be cleaned up.

    The account must stay in place when this is raised: removing the login
    while its data survives would leave the data orphaned and unreachable.
    """


# ── roles ─────────────────────────────────────────────────────────────


def known_roles() -> List[str]:
    """The roles defined in ``config/settings.yaml``."""
    from config import settings

    return sorted(settings.rbac.roles.keys())


def validate_role(role: str) -> str:
    """Return the normalised role or raise ``ValueError``.

    Only the roles in ``config/settings.yaml`` are accepted, so a typo can
    never create an account nobody can administer.
    """
    normalised = (role or "").strip().lower()
    roles = known_roles()
    if normalised not in roles:
        raise ValueError(
            f"Unknown role '{role}'. Valid roles: {', '.join(roles)}"
        )
    return normalised


def is_admin(user_info: Dict[str, Any]) -> bool:
    """True when the user holds the admin role."""
    return bool(user_info.get("is_admin")) or user_info.get("role") == "admin"


def is_last_admin(user_info: Dict[str, Any], users: Sequence[Dict[str, Any]]) -> bool:
    """True when removing *user_info* would leave the instance without an admin."""
    if not is_admin(user_info):
        return False
    admins = [user for user in users if is_admin(user)]
    return len(admins) <= 1


# ── the delete cascade ────────────────────────────────────────────────


def tenant_schema_for(tenant_id: str) -> str:
    """The warehouse schema of a tenant (``analytics_t_<slug>``).

    ADR-009 D1 gives every tenant its own schema, so a tenant's tables are
    the account's warehouse data.  The canonical naming lives in
    :mod:`api.tenancy` — this wrapper keeps the previous "empty string means
    no tenant" contract for the cascade.
    """
    from api.tenancy import DEFAULT_TENANT_ID, tenant_schema

    tid = (tenant_id or "").strip()
    if not tid or tid == DEFAULT_TENANT_ID:
        return ""
    return tenant_schema(tid)


def delete_user_data(user_info: Dict[str, Any]) -> Dict[str, Any]:
    """Remove everything the account owns in the warehouse.

    Returns a summary of what was removed. Raises :class:`CascadeError` when
    the warehouse cannot be reached — the caller must then keep the account.
    """
    from api.db import _bi_query_lock

    user_id = str(user_info.get("user_id", ""))
    summary: Dict[str, Any] = {"tables": [], "rows": 0, "schemas": [], "errors": []}
    if not user_id:
        return summary

    conn = _warehouse_connection()
    with _bi_query_lock:
        schema = _warehouse_schema()
        for table, column in USER_SCOPED_TABLES:
            try:
                deleted = _delete_user_rows(conn, schema, table, column, user_id)
            except Exception as exc:  # noqa: BLE001 — one bad table must not stop the rest
                logger.error("Could not clear %s for the deleted account: %s", table, exc)
                summary["errors"].append({"table": table, "error": str(exc)})
                continue
            if deleted:
                summary["tables"].append(table)
                summary["rows"] += deleted
        summary["schemas"] = _drop_tenant_schema(conn, user_info)
    return summary


def _warehouse_connection():
    """The shared warehouse connection (api/db.py)."""
    import api.db as db

    try:
        return db.get_connection()
    except Exception as exc:  # noqa: BLE001
        cached = getattr(db, "_conn", None)
        if cached is not None and _is_missing_schema_error(exc):
            # A fresh install has no `analytics` schema yet. Every statement
            # below is schema-qualified, so the missing search_path is fine.
            return cached
        raise CascadeError(f"the warehouse is unavailable: {exc}") from exc


def _is_missing_schema_error(exc: Exception) -> bool:
    """True for the "schema does not exist" failure of a fresh install."""
    try:
        import duckdb

        if isinstance(exc, duckdb.CatalogException):
            return True
    except Exception:  # noqa: BLE001 — duckdb is always installed here
        pass
    return "no catalog" in str(exc).lower()


def _warehouse_schema() -> str:
    from config import settings

    return settings.duckdb.schema


def _table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    return row is not None


def _delete_user_rows(conn, schema: str, table: str, column: str, user_id: str) -> int:
    """Delete the rows of one user-scoped table. Returns the row count."""
    if not _table_exists(conn, schema, table):
        return 0
    result = conn.execute(
        f"DELETE FROM {schema}.{table} WHERE {column} = ?", [user_id]
    )
    row = result.fetchone()
    return int(row[0]) if row else 0


def _drop_tenant_schema(conn, user_info: Dict[str, Any]) -> List[str]:
    """Drop the tenant schema when the account is its last member.

    A tenant that still has other members keeps its data: the schema is
    shared by everyone in the tenant, not owned by one account.
    """
    tenant_id = str(user_info.get("tenant_id", "")).strip()
    if not tenant_id:
        return []
    schema = tenant_schema_for(tenant_id)
    if not schema:
        return []
    exists = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?", [schema]
    ).fetchone()
    if not exists:
        return []
    if not _is_last_tenant_member(tenant_id, str(user_info.get("user_id", ""))):
        logger.info(
            "Keeping tenant schema %s — other members are still active", schema
        )
        return []
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    logger.info("Dropped tenant schema %s with the deleted account", schema)
    dropped = [schema]
    # In the per-tenant-file mode the tenant's data lives in its own
    # warehouse file, so the file has to go too (ADR-009 D1).
    dropped.extend(_drop_tenant_file(tenant_id))
    return dropped


def _drop_tenant_file(tenant_id: str) -> List[str]:
    """Remove the tenant's own warehouse file, if the mode is on.

    Returns the list of removed file paths (empty when the tenant does not
    own a file, or the mode is off).
    """
    from api.tenancy import per_tenant_database_enabled, tenant_slug

    if not per_tenant_database_enabled():
        return []
    tenant_dir = Path("data/tenants") / tenant_slug(tenant_id)
    if not tenant_dir.exists():
        return []
    removed = []
    for path in sorted(tenant_dir.rglob("*")):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(str(path))
    for path in sorted(tenant_dir.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
    tenant_dir.rmdir()
    logger.info("Removed tenant data directory %s", tenant_dir)
    return removed


def _is_last_tenant_member(tenant_id: str, user_id: str) -> bool:
    """True when no other account belongs to the tenant."""
    from api.auth import user_manager

    for user in user_manager.get_users():
        if user.get("user_id") == user_id:
            continue
        if str(user.get("tenant_id", "")).strip() == tenant_id:
            return False
    return True
