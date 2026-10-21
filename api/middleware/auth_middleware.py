"""
Authentication Middleware
Unified authentication for API keys, sessions, and SSO tokens
"""

import logging
from typing import Callable, Optional, TYPE_CHECKING
from dataclasses import dataclass

from fastapi import Request, Response, HTTPException, Depends
from fastapi.security import HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from api.auth import user_manager
from api.session import get_session_manager
from api.api_keys import get_api_key_manager
from api.billing.cache import begin_request_cache
from config import settings

if TYPE_CHECKING:
    from api.session import SessionManager
    from api.api_keys import APIKeyManager

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

#: Lifetime of the session cookie (and of its CSRF companion).
SESSION_COOKIE_MAX_AGE = 8 * 3600


def request_is_secure(request: Optional[Request]) -> bool:
    """True when the response cookies must carry the ``Secure`` attribute.

    Three ways to know the client is on HTTPS:

    1. ``session.force_secure_cookie`` — the operator declares this a
       hosted deployment and every cookie is marked ``Secure``.  This is
       the setting a production deployment must set: it cannot be
       downgraded by a spoofed header.
    2. ``X-Forwarded-Proto: https`` — a TLS-terminating reverse proxy
       (the app itself then only sees plain HTTP).
    3. the request URL scheme is ``https`` — direct TLS.

    Plain HTTP keeps the cookie *without* ``Secure`` so a local install
    (``http://localhost:8002``) still works.
    """
    if getattr(settings.session, "force_secure_cookie", False):
        return True
    if request is None:
        return False
    try:
        forwarded = request.headers.get("x-forwarded-proto", "") or ""
    except Exception:  # noqa: BLE001
        forwarded = ""
    if forwarded.split(",")[0].strip().lower() == "https":
        return True
    try:
        return request.url.scheme == "https"
    except Exception:  # noqa: BLE001
        return False


def apply_session_cookie(
    response: Response, session_id: str, request: Optional[Request] = None
) -> None:
    """Attach the session cookie (and its CSRF companion) to *response*.

    The session cookie is ``HttpOnly`` (JavaScript cannot read it) and
    ``SameSite=Lax``.  The ``csrf_token`` companion is deliberately
    **readable by JavaScript**: the SPA echoes it back in the
    ``X-CSRF-Token`` header, which is what makes the double-submit CSRF
    check work (see ``api/middleware/csrf.py``).

    ``Secure`` is set whenever the request reached us over HTTPS (directly
    or through a TLS-terminating proxy) or the deployment forces it.
    """
    secure = request_is_secure(request)
    try:
        response.set_cookie(
            "session_id", session_id,
            max_age=SESSION_COOKIE_MAX_AGE, httponly=True, secure=secure,
            samesite="lax", path="/",
        )
    except Exception:  # noqa: BLE001 — cookie is best-effort
        logger.debug("session cookie could not be set", exc_info=True)
    try:
        from api.middleware.csrf import set_csrf_cookie

        set_csrf_cookie(response, session_id, secure=secure)
    except Exception:  # noqa: BLE001 — cookie is best-effort
        logger.debug("csrf cookie could not be set", exc_info=True)


@dataclass
class User:
    """Authenticated user context."""
    user_id: str
    username: str
    role: str
    tenant_id: Optional[str] = None
    email: Optional[str] = None
    is_admin: bool = False
    auth_method: str = "unknown"
    session_id: Optional[str] = None
    api_key_id: Optional[str] = None
    scopes: set = None
    plan: Optional[str] = None
    # Org fields
    department_id: Optional[str] = None
    team_id: Optional[str] = None
    group_ids: Optional[list] = None

    def __post_init__(self):
        if self.scopes is None:
            self.scopes = set()
        self.is_admin = self.role == "admin"
        if self.group_ids is None:
            self.group_ids = []
        if self.plan is None:
            self.plan = self._resolve_plan()

    def _resolve_plan(self) -> str:
        """Resolve the user's plan from the billing tables (best-effort)."""
        try:
            from api.billing.entitlements import get_plan_for_user
            return get_plan_for_user(self.user_id)
        except Exception:  # noqa: BLE001
            return "free"

    def has_scope(self, scope: str) -> bool:
        """Check if user has a specific scope."""
        return scope in self.scopes

    def is_in_group(self, group_id: str) -> bool:
        """Check if user belongs to a group."""
        return group_id in (self.group_ids or [])


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for FastAPI."""

    def __init__(
        self,
        app,
        session_manager: Optional["SessionManager"] = None,
        api_key_manager: Optional["APIKeyManager"] = None,
    ):
        super().__init__(app)
        self.session_manager = session_manager or get_session_manager()
        self.api_key_manager = api_key_manager or get_api_key_manager()

        # Paths that don't require authentication (login gate + docs only).
        # Data, SQL, uploads, admin, and tenant surfaces must authenticate.
        self.public_paths = {
            "/",
            "/health",
            "/live",
            "/ready",
            "/config",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
            "/auth/login",
            "/auth/register",
            "/auth/callback",
            # Account lifecycle (ADR-009 D3): a caller who has lost their
            # password, or who is verifying an email or accepting an invite,
            # cannot present a session — these paths are the session.
            "/auth/forgot-password",
            "/auth/reset-password",
            "/auth/verify-email",
            "/auth/resend-verification",
            "/auth/accept-invite",
            # The SPA pages the account-lifecycle links point at. The token
            # travels in the URL fragment, so the server never sees it.
            "/reset-password",
            "/verify-email",
            "/accept-invite",
            # Prefix match so /auth/sso/login, /auth/google/login, etc. stay public.
            "/auth/sso/",
            "/auth/saml/",
            "/auth/google/",
            # Central accounts (billing server register/login/status).
            "/auth/accounts/",
            # NOTE: /auth/status is intentionally NOT public — it depends on
            # get_current_user(), which needs the middleware to authenticate
            # the request first.
            "/auth/verify",
            # Operational endpoints (ADR-009 D5): Prometheus scrapes and
            # k8s probes present no session. `/metrics` leaks no customer
            # data (labels are normalised and tenants are hashed).
            "/metrics",
            "/health/system",
            "/health/live",
            "/health/ready",
            # Static SPA assets — always public so the login gate can load.
            "/js/",
            "/css/",
            "/vendor/",
            "/components/",
            "/static/",
            # Legal pages (static HTML, no customer data).
            "/legal/",
        }

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public."""
        for public in self.public_paths:
            if public == "/":
                # Exact match for root only
                if path == "/":
                    return True
            elif public.endswith("/"):
                # Prefix match for paths ending with /
                if path.startswith(public):
                    return True
            else:
                # Exact match for other paths
                if path == public:
                    return True
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Start a per-request cache so plan/entitlement lookups run once.
        begin_request_cache()

        # Auth is always enforced for non-public paths. Local single-user mode
        # no longer injects a built-in admin: callers must present a session,
        # API key, or JWT (register/login remain public).

        # Skip auth for public paths — but still resolve the user when
        # credentials are present so gated-but-public routes can call
        # get_current_user() without a 401.
        if self._is_public_path(request.url.path):
            user = None
            user = await self._authenticate_bearer(request)
            if not user:
                user = await self._authenticate_api_key(request)
            if not user:
                user = await self._authenticate_session(request)
            if user:
                request.state.user = user
            return await call_next(request)

        # Try authentication methods in order.
        #
        # An **explicit** credential (Authorization header, X-API-Key) wins
        # over an **ambient** one (the session cookie).  A browser can be
        # holding a session cookie for one account while the SPA presents a
        # bearer token for another; letting the cookie win would silently
        # downgrade (or misattribute) the request.  A cross-site attacker
        # cannot set the Authorization header, so preferring it does not
        # weaken CSRF protection — the CSRF guard exempts header-credential
        # requests for the same reason.
        user = None

        # 1. Bearer token (OIDC/SSO, and the SPA's own JWT)
        user = await self._authenticate_bearer(request)

        # 2. API Key header
        if not user:
            user = await self._authenticate_api_key(request)

        # 3. Session cookie
        if not user:
            user = await self._authenticate_session(request)

        if not user:
            # Return a proper 401 response. Raising HTTPException inside a
            # BaseHTTPMiddleware dispatch() does not get converted to a
            # response in newer Starlette versions — it bubbles up as an
            # unhandled exception and yields a 500.
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer, ApiKey, Session"},
            )

        # Attach user to request state
        request.state.user = user
        return await call_next(request)

    async def _authenticate_session(self, request: Request) -> Optional[User]:
        """Authenticate via session cookie."""
        session_id = request.cookies.get("session_id")
        if not session_id:
            return None

        session = self.session_manager.get_session(session_id)
        if not session:
            return None

        # Get user info
        user_info = user_manager.get_user_info_by_id(session.user_id)
        if not user_info or not user_info.get("is_active", True):
            return None

        # Refresh session
        self.session_manager.refresh_session(session_id)

        return User(
            user_id=session.user_id,
            username=user_info["username"],
            role=user_info["role"],
            tenant_id=session.tenant_id,
            auth_method="session",
            session_id=session_id,
        )

    async def _authenticate_api_key(self, request: Request) -> Optional[User]:
        """Authenticate via API key (header only — never query string)."""
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return None

        # Validate API key
        context = self.api_key_manager.validate_key(api_key)
        if not context:
            return None

        # Get user info
        user_info = user_manager.get_user_info_by_id(context.user_id)
        if not user_info or not user_info.get("is_active", True):
            return None

        return User(
            user_id=context.user_id,
            username=user_info["username"],
            role=user_info["role"],
            # The key carries its own tenant (analytics.api_keys.tenant_id).
            # Prefer it and fall back to the owner's account record, so a key
            # issued for one tenant can never be used against another.
            tenant_id=context.tenant_id or user_info.get("tenant_id"),
            auth_method="api_key",
            api_key_id=context.key_id,
            scopes=context.scopes,
        )

    async def _authenticate_bearer(self, request: Request) -> Optional[User]:
        """Authenticate via Bearer token (app JWT or API key)."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]  # Remove "Bearer "

        # 1. App JWT (issued by /auth/login or /auth/accounts/login).
        from api.auth import verify_jwt_token
        payload = verify_jwt_token(token)
        if payload:
            username = payload.get("sub", "")
            user_info = user_manager.get_user_info(username)
            if user_info and user_info.get("is_active", True):
                return User(
                    user_id=user_info["user_id"],
                    username=username,
                    role=user_info["role"],
                    tenant_id=user_info.get("tenant_id"),
                    email=user_info.get("email"),
                    auth_method="jwt",
                    scopes={"read", "write", "admin"} if user_info.get("is_admin") else {"read", "write"},
                )

        # 2. API key fallback.
        context = self.api_key_manager.validate_key(token)
        if context:
            user_info = user_manager.get_user_info_by_id(context.user_id)
            if user_info and user_info.get("is_active", True):
                return User(
                    user_id=context.user_id,
                    username=user_info["username"],
                    role=user_info["role"],
                    # Same as the X-API-Key branch above: the key's own
                    # tenant first, the owner's account record as a fallback.
                    tenant_id=context.tenant_id or user_info.get("tenant_id"),
                    auth_method="api_key",
                    api_key_id=context.key_id,
                    scopes=context.scopes,
                )

        return None


# Dependency functions for endpoint protection

async def get_current_user(request: Request) -> User:
    """Get current authenticated user from request state."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_optional_user(request: Request) -> Optional[User]:
    """Get current user if authenticated, otherwise None."""
    return getattr(request.state, "user", None)


def require_role(*roles: str):
    """Dependency that requires one of the specified roles."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles and not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of roles: {', '.join(roles)}"
            )
        return user
    return Depends(checker)


def require_permission(permission: str):
    """Dependency that requires a specific permission."""
    from api.rbac import role_manager, Permission

    try:
        perm = Permission(permission)
    except ValueError:
        raise HTTPException(status_code=500, detail=f"Invalid permission: {permission}")

    async def checker(user: User = Depends(get_current_user)) -> User:
        if not role_manager.user_has_permission(user.role, perm) and not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail=f"Requires permission: {permission}"
            )
        return user
    return Depends(checker)


def require_scope(scope: str):
    """Dependency that requires a specific API key scope."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if not user.has_scope(scope) and not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail=f"Requires scope: {scope}"
            )
        return user
    return Depends(checker)


# Tenant context dependency
async def get_tenant_id(user: User = Depends(get_current_user)) -> Optional[str]:
    """Get tenant ID for current user."""
    return user.tenant_id


# Admin only dependency
require_admin = require_role("admin")


def add_auth_middleware(app, **kwargs):
    """Add auth middleware to FastAPI app."""
    app.add_middleware(AuthMiddleware, **kwargs)