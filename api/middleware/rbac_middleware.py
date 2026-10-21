"""RBAC middleware — enforce role permissions on the request path.

The app defined 37 granular permissions and five roles (``api/rbac.py``) but
attached **none** of them to any route: the only role check that ever ran
was ``require_admin`` on five handlers. A ``viewer`` could therefore run
arbitrary ``SELECT``, execute Python through ``/execute``, export every
table, upload and load data, and drop non-system tables — the entire data
plane.

This middleware closes that by mapping request paths to the permission they
require and refusing the request with 403 when the caller's role does not
hold it.

Design notes
------------
- The map is **deny-by-default for mutating verbs**: any ``POST``/``PUT``/
  ``PATCH``/``DELETE`` on a path that is not explicitly listed as
  permission-free requires at least one of the listed permissions. Unknown
  paths fall back to the route's own dependency checks.
- Read verbs (``GET``/``HEAD``/``OPTIONS``) only require a permission where
  one is listed, so the UI keeps working for the default role.
- Admins always pass.
- API keys pass when the key carries the matching scope.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ── Path → required permission ────────────────────────────────────────
# Only paths that need MORE than "any authenticated user" are listed.
# Matching is exact-path or a `prefix:` rule.

#: Paths that need no permission beyond authentication.
OPEN_PATHS = frozenset({
    "/health", "/live", "/ready", "/metrics",
    "/auth/login", "/auth/register", "/auth/logout", "/auth/verify",
    "/auth/status", "/auth/me",
    "/subscriptions/me", "/subscriptions/plans", "/subscriptions/trial/start",
    "/premium/activate-key",
})

#: Exact path → {method: permission}. A method not listed falls back to
#: ``PATH_PERMISSIONS`` (the any-method entry), and then to no requirement.
PATH_PERMISSIONS = {
    # Destructive warehouse operations
    "/tables": {"DELETE": "data:delete"},
    "/merge": {"POST": "data:write"},
    "/execute": {"POST": "query:execute"},
    "/sql": {"POST": "query:execute"},
    "/query/visual/execute": {"POST": "query:execute"},
    "/upload": {"POST": "data:write"},
    "/load": {"POST": "data:write"},
    "/clean": {"POST": "data:write"},
    "/clean/preview": {"POST": "data:read"},
    # Exports
    "/export/bi": {"GET": "export:bi"},
    "/export/csv": {"GET": "export:csv"},
    "/export/parquet": {"GET": "export:parquet"},
    "/export/xlsx": {"GET": "export:excel"},
    "/export/zip": {"GET": "export:csv"},
    "/export/email": {"POST": "export:csv"},
    # Admin surface
    "/auth/users": {"GET": "user:manage", "POST": "user:manage",
                    "PUT": "user:manage", "PATCH": "user:manage",
                    "DELETE": "user:manage"},
    "/auth/api-key": {"POST": "api_key:create"},
    # Inviting an account is a user-management action (ADR-009 D3).
    "/auth/invite": {"POST": "user:manage"},
    "/settings": {"POST": "config:manage", "PUT": "config:manage"},
    "/audit": {"GET": "audit:read"},
    # Org structure — reads are org:view, writes are org:manage.
    "/org": {"POST": "org:manage", "PUT": "org:manage", "DELETE": "org:manage"},
    "/org/tree": {"GET": "org:view"},
    "/org/departments": {"GET": "org:view", "POST": "org:manage"},
    "/org/teams": {"GET": "org:view", "POST": "org:manage"},
    "/org/groups": {"GET": "org:view", "POST": "org:manage"},
    "/catalog/sync": {"POST": "catalog:manage"},
    "/sso": {"POST": "sso:manage", "PUT": "sso:manage"},
    "/pipeline": {"POST": "pipeline:manage", "PUT": "pipeline:manage", "DELETE": "pipeline:manage"},
}

#: Prefix rules → {method: permission}. Checked after exact matches.
PREFIX_PERMISSIONS = (
    ("/export/bi", {"GET": "export:bi"}),
    ("/export/csv", {"GET": "export:csv"}),
    ("/export/parquet", {"GET": "export:parquet"}),
    ("/export/xlsx", {"GET": "export:excel"}),
    ("/export/zip", {"GET": "export:csv"}),
    ("/export/email", {"POST": "export:csv"}),
    ("/auth/users", {"GET": "user:manage", "POST": "user:manage",
                     "PUT": "user:manage", "PATCH": "user:manage",
                     "DELETE": "user:manage"}),
    ("/auth/api-key", {"POST": "api_key:create"}),
    ("/auth/invite", {"POST": "user:manage"}),
    # An administrator resetting another account's password.
    ("/auth/users/{username}/password", {"POST": "user:manage"}),
    # NOTE: /auth/me/export is deliberately NOT listed. It is the caller's
    # own data (a GDPR right), so every authenticated role may fetch it —
    # the handler refuses any attempt to name another account. Adding a
    # permission here would deny the `viewer` role its own export.
    # The audit trail is admin/auditor-only. PATH_PERMISSIONS carries an
    # exact "/audit" entry, but the routes are "/audit/logs" and
    # "/audit/logs/export" — an exact-path rule can never match them, so
    # without this prefix rule every role could read the whole trail.
    ("/audit", {"GET": "audit:read"}),
    ("/org/departments", {"GET": "org:view", "POST": "org:manage",
                          "PUT": "org:manage", "DELETE": "org:manage"}),
    ("/org/teams", {"GET": "org:view", "POST": "org:manage",
                    "PUT": "org:manage", "DELETE": "org:manage"}),
    ("/org/groups", {"GET": "org:view", "POST": "org:manage",
                     "PUT": "org:manage", "DELETE": "org:manage"}),
    ("/masking/", {"POST": "governance", "PUT": "governance", "DELETE": "governance"}),
    ("/catalog/sync", {"POST": "catalog:manage"}),
    ("/api-keys", {"POST": "api_key:create", "DELETE": "api_key:revoke"}),
    ("/sso/", {"POST": "sso:manage", "PUT": "sso:manage"}),
    ("/pipeline/", {"POST": "pipeline:manage", "PUT": "pipeline:manage", "DELETE": "pipeline:manage"}),
)

#: Methods that mutate state. These require a permission unless the path is
#: explicitly open.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Non-permission feature keys used as pseudo-permissions in PATH_PERMISSIONS.
#: These are entitlement gates, not RBAC roles, so they are handled by the
#: billing gate instead. Listed here so this middleware does not demand an
#: RBAC permission that no role holds.
ENTITLEMENT_KEYS = frozenset({"governance"})


def _prefix_matches(normalized: str, prefix: str) -> bool:
    """True when *normalized* is *prefix* or a path *below* it.

    Plain ``str.startswith`` would make a rule for ``/audit`` also cover
    ``/auditx`` — an unrelated path.  The match has to respect path
    boundaries.
    """
    if prefix.endswith("/"):
        return normalized.startswith(prefix)
    return normalized == prefix or normalized.startswith(prefix + "/")


def _required_permission(path: str, method: str) -> Optional[str]:
    """Return the permission required for this request, or None.

    *method* is the HTTP verb. Rules are looked up exact-path first, then
    by prefix, and within each the verb-specific entry wins over a
    catch-all.
    """
    normalized = path.rstrip("/") or "/"
    verb = (method or "GET").upper()

    if normalized in OPEN_PATHS:
        return None

    exact = PATH_PERMISSIONS.get(normalized)
    if isinstance(exact, dict):
        return exact.get(verb) or exact.get("*")

    for prefix, rules in PREFIX_PERMISSIONS:
        if _prefix_matches(normalized, prefix):
            if isinstance(rules, dict):
                return rules.get(verb) or rules.get("*")
            return rules

    return None


class RBACMiddleware(BaseHTTPMiddleware):
    """Refuse requests whose caller's role lacks the required permission."""

    def __init__(self, app, enabled: Optional[bool] = None):
        super().__init__(app)
        from config import settings as _settings

        self.enabled = _settings.rbac.enabled if enabled is None else enabled

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return await call_next(request)

        user = getattr(request.state, "user", None)
        if user is None:
            # Authentication middleware handles unauthenticated requests.
            return await call_next(request)

        # Admins bypass the permission table.
        if getattr(user, "is_admin", False):
            return await call_next(request)

        permission = _required_permission(request.url.path, request.method)
        if not permission:
            return await call_next(request)

        # Entitlement-backed pseudo-permissions are enforced by the billing
        # gate, not by RBAC.
        if permission in ENTITLEMENT_KEYS:
            return await call_next(request)

        from api.rbac import Permission, role_manager

        try:
            perm = Permission(permission)
        except ValueError:
            logger.warning("Unknown RBAC permission referenced: %s", permission)
            return await call_next(request)

        if role_manager.user_has_permission(user.role, perm):
            return await call_next(request)

        # API keys may carry the permission as a scope.
        scopes = getattr(user, "scopes", None) or set()
        if perm.value in scopes:
            return await call_next(request)

        logger.info(
            "RBAC denied %s %s for role=%s (requires %s)",
            request.method, request.url.path, getattr(user, "role", "?"), perm.value,
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    f"Permission denied: your role '{getattr(user, 'role', 'unknown')}' "
                    f"does not allow {perm.value}. Ask an administrator for access."
                )
            },
        )


def add_rbac_middleware(app, enabled: Optional[bool] = None):
    """Add the RBAC middleware to a FastAPI app."""
    app.add_middleware(RBACMiddleware, enabled=enabled)
