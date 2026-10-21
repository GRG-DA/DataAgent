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
  ``PATCH``/``DELETE`` on a path that is not explicitly listed requires a
  permission. A mutating request that no rule covers is **refused** with
  403 — it is never allowed through "because nobody thought about it".
- Read verbs (``GET``/``HEAD``/``OPTIONS``) only require a permission where
  one is listed, so the UI keeps working for the default role.
- Admins always pass.
- API keys pass when the key carries the matching scope.
- Every mutating route of the app must be registered. The test
  ``tests/test_launch_middleware_tenancy.py::test_every_mutating_route_has_a_permission_rule``
  walks ``app.routes`` and fails when a mutating route has no rule, so a
  new route cannot be added without a decision about its permission.

Permission vocabulary
----------------------
The permissions below come from :class:`api.rbac.Permission`; the roles
that hold them come from ``config/settings.yaml``
(``security.rbac.roles``).  The ``user`` role is the self-serve default:

* ``data:read`` / ``data:write`` / ``query:execute`` — the data and the
  analysis plane.  A ``viewer`` holds neither ``data:write`` nor
  ``query:execute``, so it is locked out of the data plane.
* ``export:*`` — every export format, including the html/pptx/pdf/markdown
  ones, so a format is never a way around the export permissions.
* ``dashboard:create`` / ``dashboard:manage`` — the dashboard builder.
* ``data:read`` — also used for the **caller-scoped** routes: the rows a
  request may write are its own (chats, preferences, query history,
  notification state, own comments/annotations, own consent record).  The
  handler enforces the ownership, so the permission only has to say "an
  authenticated account acting on itself".
* The remaining permissions (``user:manage``, ``config:manage``,
  ``audit:read``, ``org:manage``, ``catalog:write``, ``pipeline:manage``,
  ``schema:manage``, ``api_key:create``, ``collaboration:share``, …) are
  **not** held by the ``user`` role: the routes that use them are the
  operator's surface and are admin-only until ``config/settings.yaml``
  grants them to the self-serve role.
"""
from __future__ import annotations

import logging
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
    # The account-lifecycle endpoints are public (the caller has no
    # session yet — see the auth middleware's public path list).  A
    # browser that still holds a session must be able to reach them, so
    # they are open here too.
    "/auth/forgot-password", "/auth/reset-password", "/auth/verify-email",
    "/auth/resend-verification", "/auth/accept-invite",
    "/auth/accounts/login", "/auth/accounts/register",
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
    "/ask": {"POST": "query:execute"},
    "/ask-sql": {"POST": "query:execute"},
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
    # Caller-scoped account actions.
    "/auth/check-access": {"POST": "data:read"},
    "/auth/delete-account": {"POST": "data:read"},
    # The caller's own privacy request; the *execution* of a request is
    # a compliance action and is registered under /compliance.
    "/compliance/privacy-request": {"POST": "data:read"},
    # Publishing a product to the (shared) marketplace is an operator
    # action; rating and requesting one are covered by the prefix rule.
    "/marketplace/products": {"POST": "catalog:write"},
    # The caller's own subscription and its entitlement sync.
    "/subscriptions/me": {"POST": "data:read"},
    "/subscriptions/sync": {"POST": "data:read"},
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

#: Prefix rules → {method: permission}. Checked after exact matches, in
#: order: the first rule that matches wins.
PREFIX_PERMISSIONS = (
    # ── exports ──────────────────────────────────────────────────────
    ("/export/bi", {"GET": "export:bi"}),
    ("/export/csv", {"GET": "export:csv"}),
    ("/export/parquet", {"GET": "export:parquet"}),
    ("/export/xlsx", {"GET": "export:excel"}),
    ("/export/zip", {"GET": "export:csv"}),
    ("/export/email", {"POST": "export:csv"}),
    # The other export formats are table exports too — they must not be a
    # way around the export permissions.
    ("/export/html", {"GET": "export:csv"}),
    ("/export/markdown", {"GET": "export:csv"}),
    ("/export/pdf", {"GET": "export:csv"}),
    ("/export/pptx", {"GET": "export:csv"}),
    # ── admin surface ───────────────────────────────────────────────
    ("/auth/users/{username}/password", {"POST": "user:manage"}),
    ("/auth/users", {"GET": "user:manage", "POST": "user:manage",
                     "PUT": "user:manage", "PATCH": "user:manage",
                     "DELETE": "user:manage"}),
    ("/auth/api-key", {"POST": "api_key:create"}),
    ("/auth/api-keys", {"POST": "api_key:create", "DELETE": "api_key:revoke"}),
    ("/auth/invite", {"POST": "user:manage"}),
    # The audit trail is admin/auditor-only. PATH_PERMISSIONS carries an
    # exact "/audit" entry, but the routes are "/audit/logs" and
    # "/audit/logs/export" — an exact-path rule can never match them, so
    # without this prefix rule every role could read the whole trail.
    ("/audit", {"GET": "audit:read"}),
    # ── org structure ───────────────────────────────────────────────
    ("/org/departments", {"GET": "org:view", "POST": "org:manage",
                          "PUT": "org:manage", "DELETE": "org:manage"}),
    ("/org/teams", {"GET": "org:view", "POST": "org:manage",
                    "PUT": "org:manage", "DELETE": "org:manage"}),
    ("/org/groups", {"GET": "org:view", "POST": "org:manage",
                     "PUT": "org:manage", "DELETE": "org:manage"}),
    # ── governance: catalog, masking ────────────────────────────────
    # NOTE: /masking/rules is an entitlement gate, not an RBAC one — the
    # "governance" pseudo-permission is handled by the billing gate.
    ("/masking/", {"POST": "governance", "PUT": "governance", "DELETE": "governance"}),
    ("/catalog/terms", {"POST": "catalog:write", "PUT": "catalog:write",
                        "DELETE": "catalog:write"}),
    ("/catalog/metrics", {"POST": "catalog:write", "PUT": "catalog:write",
                          "DELETE": "catalog:write"}),
    ("/catalog/assets", {"PUT": "catalog:write"}),
    ("/catalog/certify", {"POST": "catalog:certify"}),
    ("/catalog/sync", {"POST": "catalog:manage"}),
    # ── api keys ────────────────────────────────────────────────────
    ("/api-keys", {"POST": "api_key:create", "DELETE": "api_key:revoke"}),
    # ── sso ─────────────────────────────────────────────────────────
    ("/sso/", {"POST": "sso:manage", "PUT": "sso:manage"}),
    # ── pipelines ───────────────────────────────────────────────────
    ("/pipeline/", {"POST": "pipeline:manage", "PUT": "pipeline:manage",
                    "DELETE": "pipeline:manage"}),
    # ── caller-scoped routes: the rows a request may write are its own
    ("/user", {"POST": "data:read", "PUT": "data:read", "DELETE": "data:read"}),
    ("/notifications", {"POST": "data:read", "PUT": "data:read"}),
    ("/comments", {"POST": "data:read", "PUT": "data:read", "DELETE": "data:read"}),
    ("/annotations", {"POST": "data:read"}),
    ("/compliance/consent", {"POST": "data:read"}),
    # Rating and requesting a marketplace product (the publishing route is
    # an exact-path rule above).
    ("/marketplace/products", {"POST": "data:read"}),
    # ── the data plane ─────────────────────────────────────────────
    ("/tables", {"DELETE": "data:delete", "POST": "data:read"}),
    ("/upload", {"POST": "data:write"}),
    ("/load", {"POST": "data:write"}),
    ("/clean", {"POST": "data:write"}),
    ("/merge", {"POST": "data:write"}),
    ("/metadata", {"PUT": "schema:manage"}),
    ("/alerts", {"POST": "alerts_thresholds", "DELETE": "alerts_thresholds"}),
    ("/alert", {"POST": "alerts_thresholds"}),
    ("/experiments", {"POST": "data:write", "DELETE": "data:write"}),
    ("/schedule", {"POST": "data:write", "DELETE": "data:write"}),
    ("/watcher", {"POST": "data:write"}),
    ("/quality", {"POST": "data:write"}),
    ("/lineage", {"POST": "data:write"}),
    ("/tasks", {"POST": "data:write"}),
    ("/cache", {"POST": "data:write"}),
    ("/queries", {"POST": "query:execute", "DELETE": "query:execute"}),
    ("/connect", {"POST": "data:write"}),
    # ── the query / analysis plane ──────────────────────────────────
    ("/analysis", {"POST": "query:execute"}),
    ("/query/visual", {"POST": "query:execute"}),
    ("/bi", {"POST": "query:execute"}),
    ("/llm/chat", {"POST": "query:execute"}),
    ("/llm/generate-code", {"POST": "query:execute"}),
    ("/llm/rewrite", {"POST": "query:execute"}),
    ("/llm/chart-recommend", {"POST": "query:execute"}),
    # ── dashboards and the BI builder ───────────────────────────────
    ("/dashboards", {"POST": "dashboard:create", "PUT": "dashboard:manage",
                     "DELETE": "dashboard:manage"}),
    ("/dashboard", {"POST": "dashboard:create"}),
    # The DashStudio imports write into the warehouse, the model edits
    # change the dashboard model and the visual query runs a query.
    ("/dashstudio/import-file", {"POST": "data:write"}),
    ("/dashstudio/import-paste", {"POST": "data:write"}),
    ("/dashstudio/import-pbix", {"POST": "data:write"}),
    ("/dashstudio/import-warehouse", {"POST": "data:write"}),
    ("/dashstudio/build-from-image", {"POST": "dashboard:create"}),
    ("/dashstudio/build-from-pbix", {"POST": "dashboard:create"}),
    ("/dashstudio/analyze-image", {"POST": "query:execute"}),
    ("/dashstudio/visual-data", {"POST": "query:execute"}),
    ("/dashstudio/model", {"POST": "dashboard:manage"}),
    ("/dashstudio/save", {"POST": "dashboard:manage"}),
    ("/dashstudio", {"POST": "dashboard:manage", "DELETE": "dashboard:manage"}),
    # The persistent calculation layer belongs to the dashboard builder.
    ("/measures", {"POST": "dashboard:manage", "PUT": "dashboard:manage",
                   "DELETE": "dashboard:manage"}),
    # ── compliance: the operator's surface ─────────────────────────
    ("/compliance/privacy-requests", {"POST": "audit:read"}),
    ("/compliance/pii-scan", {"POST": "audit:read"}),
    ("/compliance/retention-policies", {"POST": "config:manage",
                                        "PUT": "config:manage",
                                        "DELETE": "config:manage"}),
    ("/compliance/retention", {"POST": "config:manage", "PUT": "config:manage",
                               "DELETE": "config:manage"}),
    # ── enterprise: cost, SLA, workflows ────────────────────────────
    ("/cost", {"POST": "config:manage", "PUT": "config:manage",
               "DELETE": "config:manage"}),
    ("/sla", {"POST": "config:manage", "PUT": "config:manage",
              "DELETE": "config:manage"}),
    ("/workflows", {"POST": "pipeline:manage"}),
    # ── instance configuration ──────────────────────────────────────
    ("/premium", {"POST": "config:manage", "DELETE": "config:manage"}),
    # Fetching a proxy list through the relay is a ticket-gated read of
    # the caller's own pool, not a configuration change — it must stay
    # reachable for a paying account (tests/test_feature_tickets.py).
    ("/llm/providers/opencode/proxies", {"POST": "data:read"}),
    ("/llm/providers", {"POST": "config:manage"}),
    ("/llm/ollama", {"POST": "config:manage"}),
    ("/llm/lmstudio", {"POST": "config:manage"}),
    ("/llm/warp", {"POST": "config:manage"}),
    # ── sharing ─────────────────────────────────────────────────────
    # A share link publishes a table to anybody who has it, so it is an
    # operator action rather than a self-serve one.
    ("/share", {"POST": "collaboration:share", "DELETE": "collaboration:share"}),
    # ── subscriptions ───────────────────────────────────────────────
    # Setting another account's subscription is an admin action.
    ("/subscriptions/", {"POST": "user:manage"}),
)

#: Methods that mutate state. These require a permission unless the path is
#: explicitly open.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The permission reported for a mutating request that no rule covers.
#: It is deliberately not a member of :class:`api.rbac.Permission`, so no
#: role — and no API-key scope — can ever satisfy it.
UNMAPPED_PERMISSION = "rbac:unmapped"

#: Non-permission feature keys used as pseudo-permissions in PATH_PERMISSIONS.
#: These are entitlement gates, not RBAC roles, so they are handled by the
#: billing gate instead. Listed here so this middleware does not demand an
#: RBAC permission that no role holds.
#:
#: ``alerts_thresholds`` — the alert surface is plan-gated (every alert
#: route carries ``require_feature(FEATURE_ALERTS_THRESHOLDS)``), so the
#: plan decides who may use it, not the role.
ENTITLEMENT_KEYS = frozenset({"governance", "alerts_thresholds"})


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

    A **mutating** request that no rule covers returns
    :data:`UNMAPPED_PERMISSION` — the caller must refuse it. A read that
    no rule covers returns ``None``: reads stay open where they are open
    today.
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

    if verb in MUTATING_METHODS:
        return UNMAPPED_PERMISSION
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

        if permission == UNMAPPED_PERMISSION:
            # A mutating request no rule covers: refuse it. Letting it
            # through would make every route that was never registered
            # reachable by every authenticated role.
            logger.info(
                "RBAC denied %s %s for role=%s (no rule registered)",
                request.method, request.url.path, getattr(user, "role", "?"),
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Permission denied: your role "
                        f"'{getattr(user, 'role', 'unknown')}' may not use this "
                        "endpoint. Ask an administrator for access."
                    )
                },
            )

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
