"""
API Dependencies
Centralized dependency injection for FastAPI
"""

from typing import Optional, Callable, List
from functools import wraps

from fastapi import Request, Depends, HTTPException, Header, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.auth import user_manager
from api.rbac import Permission, role_manager, get_role_manager
from api.audit import audit_logger, get_audit_logger, AuditEventType, AuditResult
from api.rls import RLSManager, get_rls_manager
from api.session import session_manager, get_session_manager
from api.api_keys import api_key_manager, get_api_key_manager
from api.sso import sso_manager, get_sso_manager
from api.org import get_org_manager
from api.middleware.auth_middleware import User

security = HTTPBearer(auto_error=False)


# ─── Core Dependencies ────────────────────────────────────────────────

async def get_current_user(request: Request) -> User:
    """Get current authenticated user."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def get_optional_user(request: Request) -> Optional[User]:
    """Get current user if authenticated, None otherwise."""
    return getattr(request.state, "user", None)


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Require admin role."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ─── Permission Dependencies ──────────────────────────────────────────

def require_permission(permission: Permission) -> Callable:
    """Dependency factory for permission checking.

    Returns the *checker coroutine function* (not a ``Depends`` object), so
    it composes correctly in both supported forms::

        async def x(user = Depends(require_permission(Permission.DATA_READ))): ...
        app.post("/y", dependencies=[Depends(require_permission(Permission.DATA_READ))])

    The previous version returned a ``Depends`` and was then wrapped in
    *another* ``Depends`` by the helpers below, producing
    ``Depends(Depends(...))`` — which FastAPI rejects at import time with
    "Depends(...) is not a callable object".
    """
    from api.rbac import Permission as _Permission
    from api.middleware.auth_middleware import get_current_user

    perm = _Permission(permission.value if isinstance(permission, Permission) else permission)

    async def checker(user: User = Depends(get_current_user)) -> User:
        if not role_manager.user_has_permission(user.role, perm):
            # Also check API key scopes
            scopes = getattr(user, "scopes", None) or set()
            if perm.value in scopes:
                return user
            if getattr(user, "is_admin", False):
                return user
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: requires {perm.value}"
            )
        return user

    return checker


def require_role(*roles: str) -> Callable:
    """Dependency factory for role checking."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles and not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of roles: {', '.join(roles)}"
            )
        return user
    return Depends(checker)


def require_scope(scope: str) -> Callable:
    """Dependency factory for API key scope checking."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if scope not in user.scopes and not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail=f"Requires scope: {scope}"
            )
        return user
    return Depends(checker)


# ─── Resource Access Dependencies ─────────────────────────────────────

async def require_data_read(user: User = Depends(require_permission(Permission.DATA_READ))):
    """Require data read permission."""
    return user


async def require_data_write(user: User = Depends(require_permission(Permission.DATA_WRITE))):
    """Require data write permission."""
    return user


async def require_query_execute(user: User = Depends(require_permission(Permission.QUERY_EXECUTE))):
    """Require query execute permission."""
    return user


async def require_pipeline_manage(user: User = Depends(require_permission(Permission.PIPELINE_MANAGE))):
    """Require pipeline manage permission."""
    return user


async def require_schema_manage(user: User = Depends(require_permission(Permission.SCHEMA_MANAGE))):
    """Require schema manage permission."""
    return user


async def require_export(user: User = Depends(require_permission(Permission.EXPORT_CSV))):
    """Require export permission."""
    return user


async def require_audit_read(user: User = Depends(require_permission(Permission.AUDIT_READ))):
    """Require audit read permission."""
    return user


async def require_user_manage(user: User = Depends(require_permission(Permission.USER_MANAGE))):
    """Require user management permission."""
    return user


async def require_config_manage(user: User = Depends(require_permission(Permission.CONFIG_MANAGE))):
    """Require config management permission."""
    return user

# ─── Tenant Context ───────────────────────────────────────────────────

async def get_tenant_id(user: User = Depends(get_current_user)) -> Optional[str]:
    """Get tenant ID for current user."""
    return user.tenant_id


async def require_tenant(tenant_id: str = Depends(get_tenant_id)) -> str:
    """Require a tenant context (for RLS)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    return tenant_id


# ─── Audit Helpers ────────────────────────────────────────────────────

async def audit_query(
    query: str,
    row_count: int,
    execution_time_ms: float,
    user: User = Depends(get_current_user),
    error: Optional[str] = None,
):
    """Audit a query execution."""
    audit_logger.log_query(
        user_id=user.user_id,
        query=query,
        row_count=row_count,
        execution_time_ms=execution_time_ms,
        tenant_id=user.tenant_id,
        result=AuditResult.FAILURE if error else AuditResult.SUCCESS,
        error=error,
    )


async def audit_data_access(
    table: str,
    columns: List[str],
    row_count: int,
    user: User = Depends(get_current_user),
):
    """Audit data access."""
    audit_logger.log_data_access(
        user_id=user.user_id,
        table=table,
        columns=columns,
        row_count=row_count,
        tenant_id=user.tenant_id,
    )


# ─── Request Context ──────────────────────────────────────────────────

def get_request_id(request: Request) -> str:
    """Get or generate request ID."""
    return request.headers.get("X-Request-ID", "")


def get_ip_address(request: Request) -> str:
    """Get client IP address.

    Proxy headers are honored only from a loopback peer (our own nginx) —
    direct clients must not spoof them.
    """
    import ipaddress as _ip

    def _valid(v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        try:
            _ip.ip_address(v)
            return v
        except ValueError:
            return ""

    try:
        peer = request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        peer = ""
    if peer in ("127.0.0.1", "::1", "localhost"):
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if _valid(fwd):
            return fwd
        rip = _valid(request.headers.get("X-Real-IP") or "")
        if rip:
            return rip
    return _valid(peer) or "unknown"


def get_user_agent(request: Request) -> str:
    """Get user agent."""
    return request.headers.get("User-Agent", "")


# ─── Manager Dependencies ─────────────────────────────────────────────

# Re-export manager getters
get_role_mgr = get_role_manager
get_audit_mgr = get_audit_logger
get_rls_mgr = get_rls_manager
get_session_mgr = get_session_manager
get_api_key_mgr = get_api_key_manager
get_sso_mgr = get_sso_manager
get_org_mgr = get_org_manager


# ─── Org Dependencies ─────────────────────────────────────────────────────

async def get_current_user_groups(user: User = Depends(get_current_user)) -> List[str]:
    """Get group IDs for the current user."""
    if user.group_ids:
        return user.group_ids
    try:
        groups = get_org_manager().get_user_groups(user.user_id)
        return [g["id"] for g in groups]
    except Exception:
        return []


async def require_org_manage(user: User = Depends(get_current_user)) -> User:
    """Require org management permission."""
    if not role_manager.user_has_permission(user.role, Permission.ORG_MANAGE) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied: requires org:manage")
    return user


async def require_catalog_read(user: User = Depends(get_current_user)) -> User:
    """Require catalog read permission."""
    if not role_manager.user_has_permission(user.role, Permission.CATALOG_READ) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied: requires catalog:read")
    return user


async def require_catalog_manage(user: User = Depends(get_current_user)) -> User:
    """Require catalog manage permission."""
    if not role_manager.user_has_permission(user.role, Permission.CATALOG_MANAGE) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied: requires catalog:manage")
    return user


async def require_dashboard_manage(user: User = Depends(get_current_user)) -> User:
    """Require dashboard manage permission."""
    if not role_manager.user_has_permission(user.role, Permission.DASHBOARD_CREATE) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied: requires dashboard:create")
    return user


async def require_notification_manage(user: User = Depends(get_current_user)) -> User:
    """Require notification management permission."""
    if not role_manager.user_has_permission(user.role, Permission.NOTIFICATION_MANAGE) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied: requires notification:manage")
    return user