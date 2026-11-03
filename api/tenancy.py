"""Multi-tenant context and schema resolution (ADR-009 D1).

DataAgent is a hosted multi-tenant SaaS.  Each tenant owns a **dedicated DuckDB
schema**, ``analytics_t_<slug>``, inside the shared warehouse file:

    analytics            platform tables (billing, users, dashboards, catalog...)
    analytics_t_u_abc123 tenant "u_abc123"'s own data tables

The tenant schema is selected with ``SET search_path``, which DuckDB applies
**per connection** — so a request that opens a connection with the tenant's
search path physically cannot see another tenant's tables.  A connection whose
search path was never set has *no* schema in scope, so unqualified table
names fail: the default is fail-closed.

The ``default`` tenant deliberately maps to the ``analytics`` schema so an
existing single-user install keeps its data and the desktop build is
unaffected.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

#: The tenant every user belongs to when tenancy is disabled, and the
#: tenant that owns the ``analytics`` schema.
DEFAULT_TENANT_ID = "default"

#: Platform schema — tables shared by every tenant (billing, users, ...).
PLATFORM_SCHEMA = "analytics"

#: Prefix for a tenant-owned schema.
TENANT_SCHEMA_PREFIX = "analytics_t_"

#: Environment override for the per-tenant database mode.
PER_TENANT_DATABASE_ENV = "DATAAGENT_PER_TENANT_DATABASE"

#: Directory (below the data root) that holds the per-tenant warehouses.
TENANT_DB_DIRNAME = "tenants"

#: Values that mean "off" for a boolean environment variable.
_FALSEY = frozenset({"0", "false", "no", "off"})

_SAFE_SLUG = re.compile(r"^[a-z0-9_]{1,40}$")

_current_tenant: ContextVar[Optional[str]] = ContextVar("dataagent_tenant", default=None)
_schema_lock = threading.Lock()
_ensured_schemas: set[str] = set()


# ── Tenant identity ────────────────────────────────────────────────────────

def tenant_slug(tenant_id: Optional[str]) -> str:
    """Return a filesystem/SQL-safe slug for *tenant_id*.

    Tenant ids come from the identity store and are trusted, but they end up
    in a SQL identifier, so the result is restricted to ``[a-z0-9_]`` and
    suffixed with a short hash so two different ids can never collapse onto
    the same slug.
    """
    raw = str(tenant_id or DEFAULT_TENANT_ID).strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_") or "t"
    raw = raw[:32]
    digest = hashlib.sha256(str(tenant_id or DEFAULT_TENANT_ID).encode("utf-8")).hexdigest()[:8]
    return f"{raw}_{digest}"


def tenant_schema(tenant_id: Optional[str] = None) -> str:
    """Return the DuckDB schema that owns *tenant_id*'s data.

    The ``default`` tenant maps to the platform ``analytics`` schema so an
    existing install (and the desktop build) is unaffected.
    """
    if tenant_id is None:
        tenant_id = current_tenant_id()
    tid = str(tenant_id or DEFAULT_TENANT_ID).strip()
    if not tid or tid == DEFAULT_TENANT_ID:
        return PLATFORM_SCHEMA
    return f"{TENANT_SCHEMA_PREFIX}{tenant_slug(tid)}"


def tenant_search_path(tenant_id: Optional[str] = None) -> str:
    """Return the ``search_path`` value for *tenant_id*.

    The tenant schema comes first, so an unqualified table name resolves to
    the tenant's own copy.  The platform schema follows so app code that
    references platform tables without a schema qualifier still works.
    """
    schema = tenant_schema(tenant_id)
    if schema == PLATFORM_SCHEMA:
        return PLATFORM_SCHEMA
    # DuckDB takes a single comma-separated string; no space after the comma.
    return f"{schema},{PLATFORM_SCHEMA}"


# ── Per-tenant database files (opt-in) ─────────────────────────────────

@lru_cache(maxsize=1)
def _configured_per_tenant_database() -> bool:
    """Read ``security.tenancy.per_tenant_database`` from settings.yaml.

    The flag is deliberately read from the YAML rather than from a typed
    settings object: the setting is opt-in and must default to *off*, so an
    install that never heard of it behaves exactly as before.
    """
    try:
        import yaml

        path = Path("config/settings.yaml")
        if not path.exists():
            return False
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - never break the data plane
        logger.debug("could not read the per-tenant database flag: %s", exc)
        return False
    security = data.get("security") or {}
    tenancy = security.get("tenancy") or {}
    return bool(tenancy.get("per_tenant_database", False))


def per_tenant_database_enabled() -> bool:
    """True when every tenant owns a separate DuckDB file.

    DuckDB has **no per-session schema ACLs**: ``search_path`` decides how
    *unqualified* names resolve, but an explicitly qualified reference
    (``other_schema.sales``) still resolves.  The SQL policy refuses those
    references, which is enough against a hostile *query* — but a hostile
    *DuckDB client* that opens the file directly is not stopped by the
    policy.  This mode closes that gap by giving each tenant its own file.

    Off by default, so an existing install is unchanged.
    """
    raw = os.environ.get(PER_TENANT_DATABASE_ENV, "").strip().lower()
    if raw:
        return raw not in _FALSEY
    try:
        from config import settings

        value = getattr(getattr(settings, "tenancy", None), "per_tenant_database", None)
        if value is not None:
            return bool(value)
    except Exception as exc:  # noqa: BLE001 - never break the data plane
        logger.debug("could not read the tenancy settings: %s", exc)
    return _configured_per_tenant_database()


def tenant_owns_own_file(tenant_id: Optional[str] = None) -> bool:
    """True when *tenant_id*'s data lives in its own DuckDB file."""
    tid = tenant_id if tenant_id is not None else current_tenant_id()
    if not per_tenant_database_enabled():
        return False
    # The platform tenant owns the shared file (it holds `analytics`).
    return str(tid or DEFAULT_TENANT_ID).strip() != DEFAULT_TENANT_ID


def tenant_database_path(
    tenant_id: Optional[str] = None, shared_path: Optional[str] = None
) -> str:
    """Return the DuckDB file that holds *tenant_id*'s data tables.

    With the per-tenant database mode off (the default) every tenant uses
    the shared warehouse and only the *schema* differs.  With it on, a
    tenant's data lives in ``<root>/tenants/<tenant-slug>/warehouse.duckdb``
    and the platform schema stays in the shared file.
    """
    tid = tenant_id if tenant_id is not None else current_tenant_id()
    if shared_path is None:
        try:
            from config import settings

            shared_path = settings.duckdb.database_path
        except Exception:  # noqa: BLE001 - never break the data plane
            shared_path = "data/curated/warehouse.duckdb"
    shared = str(shared_path)
    if not tenant_owns_own_file(tid):
        return shared
    root = Path(shared).parent.parent / TENANT_DB_DIRNAME / tenant_slug(tid)
    return str(root / "warehouse.duckdb")


def tenant_file_search_path(tenant_id: Optional[str] = None) -> str:
    """Search path for a connection that opens the tenant's own file.

    The tenant's file holds only the tenant's schema — the platform schema
    stays in the shared file — so the path names a single schema.
    """
    schema = tenant_schema(tenant_id)
    if schema == PLATFORM_SCHEMA:
        return PLATFORM_SCHEMA
    if tenant_owns_own_file(tenant_id):
        return schema
    return f"{schema},{PLATFORM_SCHEMA}"


# ── Request-scoped tenant context ─────────────────────────────────────────

def current_tenant_id() -> str:
    """Return the tenant of the in-flight request.

    Falls back to ``default`` when no request context is active (CLI
    scripts, background jobs), which keeps single-user tooling working.
    """
    return _current_tenant.get() or DEFAULT_TENANT_ID


def set_current_tenant(tenant_id: Optional[str]):
    """Set the request-scoped tenant; returns the contextvar token."""
    return _current_tenant.set(str(tenant_id) if tenant_id else None)


def reset_current_tenant(token) -> None:
    """Restore the previous tenant (see :func:`set_current_tenant`)."""
    try:
        _current_tenant.reset(token)
    except ValueError:  # pragma: no cover - token from another context
        _current_tenant.set(None)


@contextmanager
def use_tenant(tenant_id: Optional[str]) -> Iterator[str]:
    """Bind *tenant_id* for the duration of the block."""
    token = set_current_tenant(tenant_id)
    try:
        yield current_tenant_id()
    finally:
        reset_current_tenant(token)


def resolve_tenant_id(user) -> str:
    """Return the tenant that owns *user*.

    A user with no tenant is placed in the ``default`` tenant, which owns
    the platform ``analytics`` schema.  That keeps the existing local
    install working while a hosted deployment assigns each signup its own
    tenant through the identity store.
    """
    if user is None:
        return DEFAULT_TENANT_ID
    tenant = getattr(user, "tenant_id", None)
    return str(tenant).strip() if tenant else DEFAULT_TENANT_ID


def assign_tenant(user_id: str, is_first_user: bool = False) -> str:
    """Return the tenant a newly created account belongs to.

    The **first** account on an install is the operator's bootstrap admin
    and keeps the ``default`` tenant, so an existing single-user install
    (and the desktop build) keeps its data in the ``analytics`` schema.
    Every later signup gets its own tenant, and therefore its own schema.
    """
    if is_first_user:
        return DEFAULT_TENANT_ID
    uid = str(user_id or "").strip()
    return f"u_{uid}" if uid else DEFAULT_TENANT_ID


def ensure_user_tenants() -> int:
    """Give every tenant-less account its own tenant (startup migration).

    An install that predates tenancy has accounts with no ``tenant_id``.
    The oldest account is the operator's bootstrap admin and keeps the
    ``default`` tenant (so its existing ``analytics`` data stays visible);
    every other account is moved to its own tenant so it can no longer see
    the admin's data.

    Returns the number of accounts that were assigned a tenant.
    """
    try:
        from api.auth import user_manager

        users = user_manager.get_users()
    except Exception as exc:  # noqa: BLE001
        logger.debug("tenant migration skipped: %s", exc)
        return 0

    if not users:
        return 0

    def _created(u):
        return str(u.get("created_at") or "")

    ordered = sorted(users, key=_created)
    changed = 0
    for index, user in enumerate(ordered):
        if user.get("tenant_id"):
            continue
        tenant = assign_tenant(user.get("user_id", ""), is_first_user=(index == 0))
        try:
            user_manager.update_user(user.get("username", ""), tenant_id=tenant)
            changed += 1
            logger.info("assigned tenant %s to user %s", tenant, user.get("username"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not assign a tenant to %s: %s", user.get("username"), exc)
    return changed


# ── Schema creation ───────────────────────────────────────────────────────

def ensure_tenant_schema(tenant_id: Optional[str] = None) -> str:
    """Create the tenant's schema if it does not exist; return its name.

    The platform schema is always present.  Creation is idempotent and
    guarded by a lock so concurrent first-use of the same tenant cannot
    race.  With the per-tenant database mode on, the schema is created in
    the tenant's *own* file, not in the shared warehouse.
    """
    schema = tenant_schema(tenant_id)
    if schema == PLATFORM_SCHEMA:
        return schema
    if schema in _ensured_schemas:
        return schema
    with _schema_lock:
        if schema in _ensured_schemas:
            return schema
        try:
            if tenant_owns_own_file(tenant_id):
                _create_schema_in_tenant_file(tenant_id, schema)
            else:
                from api.db import get_connection

                get_connection().execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not create tenant schema %s: %s", schema, exc)
            return schema
        _ensured_schemas.add(schema)
        logger.info("created tenant schema %s", schema)
    return schema


def _create_schema_in_tenant_file(tenant_id: Optional[str], schema: str) -> None:
    """Create *schema* inside the tenant's own DuckDB file.

    The connection is opened with the same pragmas the loader uses, so
    DuckDB does not refuse it as "a different configuration" of a file
    that is already open.
    """
    import duckdb

    from config import settings

    path = tenant_database_path(tenant_id)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(path)
    try:
        cfg = getattr(settings.duckdb, "config", {}) or {}
        conn.execute(f"PRAGMA threads={int(cfg.get('threads', 4))}")
        conn.execute(f"PRAGMA memory_limit='{cfg.get('memory_limit', '2GB')}'")
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    finally:
        conn.close()


def apply_tenant_search_path(
    conn, tenant_id: Optional[str] = None, include_platform: Optional[bool] = None
) -> str:
    """Point *conn* at the tenant's schema; returns the search path used.

    ``include_platform`` defaults to "yes, unless the tenant owns its own
    file" — a per-tenant file holds only the tenant's schema, so the
    platform schema is not on its search path.
    """
    if include_platform is None:
        include_platform = not tenant_owns_own_file(tenant_id)
    schema = tenant_schema(tenant_id)
    if schema == PLATFORM_SCHEMA or not include_platform:
        path = schema
    else:
        path = f"{schema},{PLATFORM_SCHEMA}"
    conn.execute(f"SET search_path = '{path}'")
    return path


def current_search_path(conn) -> str:
    """Return the connection's current ``search_path`` ("" when unset).

    Used to restore a shared connection's search path after a tenant-scoped
    block, so a process-wide connection never keeps a tenant's schema in
    scope once the request that needed it is done.
    """
    try:
        row = conn.execute("SELECT current_setting('search_path')").fetchone()
    except Exception:  # noqa: BLE001 - never break the data plane
        return ""
    return str(row[0]) if row and row[0] is not None else ""


def apply_tenant_search_path_safe(conn, tenant_id: Optional[str] = None) -> str:
    """Point *conn* at the tenant's schema, creating it when it is missing.

    ``SET search_path`` fails on a schema that does not exist, so a brand-new
    tenant (whose schema has not been created yet) would otherwise make every
    query fail instead of simply returning no rows.
    """
    schema = tenant_schema(tenant_id)
    try:
        return apply_tenant_search_path(conn, tenant_id)
    except Exception:  # noqa: BLE001
        pass
    # A schema on the path does not exist yet — create it (and the platform
    # schema, which every tenant's path carries) and try again.
    for name in (schema, PLATFORM_SCHEMA):
        try:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{name}"')
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not create the schema %s: %s", name, exc)
    return apply_tenant_search_path(conn, tenant_id)


@contextmanager
def tenant_search_path_scope(conn, tenant_id: Optional[str] = None) -> Iterator[str]:
    """Point *conn* at the tenant's schema for the duration of the block.

    The previous search path is **restored on exit**.  This matters for the
    process-wide shared connection: without the restore, the schema of
    whichever tenant hit the path last would stay in scope and the *next*
    request would resolve that tenant's tables (a cross-tenant read).

    Yields the search path that was applied.
    """
    previous = current_search_path(conn)
    path = apply_tenant_search_path_safe(conn, tenant_id)
    try:
        yield path
    finally:
        try:
            if previous:
                conn.execute(f"SET search_path = '{previous}'")
            else:
                # No schema in scope = fail-closed (unqualified names resolve
                # to nothing), which is the state a fresh connection is in.
                conn.execute("SET search_path = ''")
        except Exception:  # noqa: BLE001
            logger.warning("could not restore the search path", exc_info=True)


def ensure_search_path(conn, schema: str) -> str:
    """Point *conn* at *schema*, creating it first if it is missing.

    ``SET search_path`` fails when the named schema does not exist, so a
    fresh warehouse needs the schema created before the path is set.
    """
    import duckdb

    try:
        conn.execute(f"SET search_path = '{schema}'")
    except duckdb.CatalogException:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.execute(f"SET search_path = '{schema}'")
    return schema


@contextmanager
def tenant_connection(tenant_id: Optional[str] = None, shared_path: Optional[str] = None):
    """Open a DuckDB connection scoped to the caller's tenant.

    With the per-tenant-file mode on, the tenant gets its **own** warehouse
    file (``data/tenants/<slug>/warehouse.duckdb``) so even a hostile
    DuckDB client cannot reach another tenant's schema.  With the mode off
    (the default) the shared platform file is used and the tenant's schema
    is simply first on the search path.

    The schema is created if it does not exist, and the connection is closed
    on exit.
    """
    import duckdb

    from config import settings

    tid = tenant_id if tenant_id is not None else current_tenant_id()
    base = str(shared_path) if shared_path else str(settings.duckdb.database_path)
    path = tenant_database_path(tid, shared_path=base)
    conn = duckdb.connect(path)
    try:
        schema = tenant_schema(tid)
        if tenant_owns_own_file(tid):
            ensure_search_path(conn, schema)
        else:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}")
            apply_tenant_search_path(conn, tid)
        yield conn
    finally:
        conn.close()


#: DuckDB's "Did you mean …?" suggestion. It happily names another
#: tenant's schema, so it must never reach a user.
_DID_YOU_MEAN = re.compile(r'Did you mean "[^"]*"\?', re.IGNORECASE)

#: Any tenant schema name that leaked into an error message.
_TENANT_SCHEMA_LEAK = re.compile(r"analytics_t_[A-Za-z0-9_]+", re.IGNORECASE)


def sanitize_error_message(message: str) -> str:
    """Strip cross-tenant details out of a warehouse error message.

    DuckDB's "Table with name X does not exist! Did you mean
    "analytics_t_<other-tenant>.X"?" discloses another tenant's schema and
    table name.  The schema list is also reduced to the caller's own.
    """
    text = str(message or "")
    text = _DID_YOU_MEAN.sub("Did you mean a table in your workspace?", text)
    text = _TENANT_SCHEMA_LEAK.sub("<other-workspace>", text)
    return text
