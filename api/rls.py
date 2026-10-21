"""
Row-Level Security Module
Tenant isolation via query rewriting
"""

import re
import sqlglot
import logging
from sqlglot import exp
from typing import Optional, List, Set, TYPE_CHECKING
from dataclasses import dataclass

from config import settings

if TYPE_CHECKING:
    from api.middleware.auth_middleware import User

logger = logging.getLogger(__name__)


class RLSRewriteError(Exception):
    """Raised when a query cannot be filtered for the requesting tenant.

    The query is refused rather than run unfiltered.
    """


@dataclass
class RLSConfig:
    """RLS configuration."""
    enabled: bool = False
    tenant_column: str = "tenant_id"
    default_tenant: str = "default"
    admin_roles: List[str] = None

    def __post_init__(self):
        if self.admin_roles is None:
            self.admin_roles = ["admin"]


class RLSManager:
    """Manages row-level security for multi-tenant isolation."""

    def __init__(self, config: Optional[RLSConfig] = None):
        self.config = config or RLSConfig(
            enabled=settings.rls.enabled,
            tenant_column=settings.rls.tenant_column,
            default_tenant=settings.rls.default_tenant,
        )

    def is_admin(self, user_role: str) -> bool:
        """Check if user role is admin (bypasses RLS)."""
        return user_role in self.config.admin_roles

    def get_tenant_id(self, user: "User") -> str:
        """Get tenant ID for a user."""
        if hasattr(user, 'tenant_id') and user.tenant_id:
            return user.tenant_id
        return self.config.default_tenant

    def get_tenant_filter(self, user: "User") -> str:
        """Generate WHERE clause for tenant isolation."""
        if self.is_admin(getattr(user, 'role', 'viewer')):
            return "1=1"
        tenant_id = self.get_tenant_id(user)
        # Escape single quotes
        tenant_id = tenant_id.replace("'", "''")
        return f"{self.config.tenant_column} = '{tenant_id}'"

    def rewrite_query(self, query: str, user: "User") -> str:
        """
        Rewrite query to inject tenant filter.
        Uses sqlglot for proper AST manipulation.

        Fails **closed**: if the query cannot be parsed or the tenant filter
        cannot be injected, the query is rejected rather than returned
        unfiltered. Returning the original query on error (the old
        behaviour) meant any rewrite failure silently exposed every tenant's
        rows.
        """
        if not self.config.enabled:
            return query

        if self.is_admin(getattr(user, 'role', 'viewer')):
            return query

        tenant_filter = self.get_tenant_filter(user)
        if tenant_filter == "1=1":
            return query

        try:
            # Parse the query
            parsed = sqlglot.parse_one(query, dialect="duckdb")

            # Find all table references and add tenant filter
            self._inject_tenant_filter(parsed, tenant_filter)

            rewritten = parsed.sql(dialect="duckdb")
        except Exception as e:
            logger.error("RLS query rewrite failed — refusing query: %s", e)
            raise RLSRewriteError(
                "This query could not be filtered for your workspace, so it was "
                "not run. Try a simpler question or run the SQL directly."
            ) from e

        # A rewrite that produced no tenant predicate at all is a silent
        # failure — refuse it rather than return unfiltered rows.
        if self.config.tenant_column not in rewritten:
            logger.error(
                "RLS rewrite produced no tenant predicate — refusing query: %s", rewritten
            )
            raise RLSRewriteError(
                "This query could not be filtered for your workspace, so it was "
                "not run."
            )
        return rewritten

    def _inject_tenant_filter(self, node: exp.Expression, tenant_filter: str):
        """Recursively inject tenant filter into all table scans."""
        if isinstance(node, exp.Table):
            # Add tenant filter to table
            self._add_filter_to_table(node, tenant_filter)

        # Recurse into children. Materialize the iterator first: injecting a
        # WHERE clause mutates the parent's args while we iterate.
        for child in list(node.iter_expressions()):
            self._inject_tenant_filter(child, tenant_filter)

    def _add_filter_to_table(self, table: exp.Table, tenant_filter: str):
        """Add tenant filter to a table reference."""
        # Check if this table has the tenant column
        # For simplicity, we assume all tables in analytics schema have it
        table_name = table.name.lower()

        # Skip system tables
        if table_name.startswith(('information_schema', 'pg_', 'sqlite_')):
            return

        # Build a well-formed Where node: sqlglot cannot parse a bare
        # "WHERE …" fragment, so parse a minimal complete statement and
        # lift its WHERE clause.
        where_node = sqlglot.parse_one(
            f"SELECT 1 WHERE {tenant_filter}", dialect="duckdb"
        ).args.get("where")
        if where_node is None:
            return

        # Find or create SELECT parent
        select = table.find_ancestor(exp.Select)
        if select:
            # Merge with existing WHERE
            existing_where = select.args.get("where")
            if existing_where:
                # Combine with AND
                combined = exp.And(
                    this=existing_where.this,
                    expression=where_node.this
                )
                select.set("where", exp.Where(this=combined))
            else:
                select.set("where", where_node.copy())

    def validate_table_access(self, table: str, user: "User") -> bool:
        """Check if user can access a table.

        A table is accessible when it carries the tenant column (so the
        filter has something to bind to) or when the user is an admin. The
        previous implementation returned True for every table, which made
        this a no-op.
        """
        if self.is_admin(getattr(user, 'role', 'viewer')):
            return True
        try:
            columns = self.get_tenant_columns(table)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tenant column lookup failed for %s: %s", table, exc)
            return False
        return self.config.tenant_column in columns

    def get_tenant_columns(self, table: str) -> List[str]:
        """Get the column names of *table* in the analytics schema.

        Uses the process-wide warehouse connection.  A fresh
        ``read_only=True`` connection to the same file is refused by DuckDB
        while the app holds it read-write ("a different configuration than
        existing connections"), which made this return ``[]`` and made
        ``validate_table_access()`` deny every non-admin.
        """
        try:
            from api.db import get_connection, _bi_query_lock

            with _bi_query_lock:
                rows = get_connection().execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ?",
                    [settings.duckdb.schema, table],
                ).fetchall()
            return [str(r[0]) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.debug("tenant column lookup failed for %s: %s", table, exc)
            return []


class RLSContext:
    """Thread-local RLS context for request processing."""

    def __init__(self):
        self._user: Optional["User"] = None
        self._enabled: bool = False

    def set_user(self, user: "User"):
        self._user = user
        self._enabled = True

    def get_user(self) -> Optional["User"]:
        return self._user

    def is_enabled(self) -> bool:
        return self._enabled and self._user is not None

    def clear(self):
        self._user = None
        self._enabled = False


# Global context (use thread-local in production)
rls_context = RLSContext()


def get_rls_manager() -> RLSManager:
    """Dependency injection for RLS manager."""
    return RLSManager()


def apply_rls(query: str, user: Optional["User"]) -> str:
    """Rewrite *query* with the requesting user's tenant filter.

    No-op when no user is provided; ``RLSManager.rewrite_query`` itself is a
    no-op when RLS is disabled or the user is an admin.

    Fails **closed**: a rewrite error raises :class:`RLSRewriteError`
    instead of returning the original query. The old fail-open behaviour
    meant any rewrite failure (a parse error, an unusual construct) returned
    the query unfiltered and exposed every tenant's rows.
    """
    if user is None:
        return query
    return get_rls_manager().rewrite_query(query, user)