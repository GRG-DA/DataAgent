"""
Audit Middleware
Logs all requests and responses for compliance
"""

import time
import uuid
import logging
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from api.audit import audit_logger, AuditEventType, AuditResult, AuthMethod, AuditEvent

logger = logging.getLogger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware to audit all HTTP requests."""

    # Paths to exclude from audit logging
    EXCLUDED_PATHS = {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/static",
        "/favicon.ico",
    }

    def __init__(
        self,
        app,
        excluded_paths: Optional[set] = None,
        log_request_body: bool = False,
        log_response_body: bool = False,
    ):
        super().__init__(app)
        self.excluded_paths = excluded_paths or self.EXCLUDED_PATHS
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip excluded paths
        if any(request.url.path.startswith(path) for path in self.excluded_paths):
            return await call_next(request)

        # Generate request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])

        # Start timing
        start_time = time.perf_counter()

        # Extract client info
        ip_address = self._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")

        # Log request
        request_body = None
        if self.log_request_body and request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                request_body = body.decode("utf-8")[:1000]
            except Exception:
                pass

        response = None
        error = None
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            error = str(e)
            logger.exception(f"Request {request_id} failed: {e}")
            raise
        finally:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Extract user info AFTER call_next: AuthMiddleware (added after
            # this middleware, so it runs *inside* / before this wrapper's
            # post-processing) attaches request.state.user during request
            # handling. Reading it before call_next always yielded None.
            # Audit is registered last among Auth/RLS/Audit so it wraps Auth:
            # Auth 401s that never call_next still return through this layer.
            user_id = None
            tenant_id = None
            if hasattr(request.state, "user") and request.state.user:
                user_id = getattr(request.state.user, "user_id", None)
                tenant_id = getattr(request.state.user, "tenant_id", None)

            # Determine auth method
            auth_method = self._get_auth_method(request)

            # Log based on path
            await self._log_request(
                request=request,
                response=response,
                request_id=request_id,
                user_id=user_id,
                tenant_id=tenant_id,
                ip_address=ip_address,
                user_agent=user_agent,
                duration_ms=duration_ms,
                status_code=status_code,
                error=error,
                auth_method=auth_method,
                request_body=request_body,
            )

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP address (proxy headers only from loopback)."""
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
            return peer or "unknown"
        return _valid(peer) or "unknown"

    def _get_auth_method(self, request: Request) -> AuthMethod:
        """Determine authentication method used."""
        # Check for session cookie
        if request.cookies.get("session_id"):
            return AuthMethod.SESSION

        # Check for API key (header only — query-string api_key is rejected by auth)
        if request.headers.get("X-API-Key"):
            return AuthMethod.API_KEY

        # Check for Bearer token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # Could be OIDC or SAML token
            return AuthMethod.SSO_OIDC

        # Check for basic auth
        if auth_header.startswith("Basic "):
            return AuthMethod.PASSWORD

        return AuthMethod.PASSWORD  # Default

    async def _log_request(
        self,
        request: Request,
        response: Optional[Response],
        request_id: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        ip_address: str,
        user_agent: str,
        duration_ms: float,
        status_code: int,
        error: Optional[str],
        auth_method: AuthMethod,
        request_body: Optional[str],
    ):
        """Log the request to audit logger."""

        # Determine event type based on path
        event_type = AuditEventType.QUERY_EXECUTED
        action = "request"
        resource = "api"

        if request.url.path.startswith("/auth/"):
            resource = "auth"
            path = request.url.path
            if "login" in path:
                event_type = AuditEventType.USER_LOGIN
            elif "logout" in path:
                event_type = AuditEventType.USER_LOGOUT
            elif "register" in path or "users" in path:
                event_type = AuditEventType.USER_CREATED
            else:  # /auth/me, /auth/refresh, ... — session activity, not logout
                event_type = AuditEventType.DATA_ACCESSED
        elif request.url.path.startswith("/api/") or request.url.path.startswith("/ask"):
            event_type = AuditEventType.QUERY_EXECUTED
            resource = "query"
        elif request.url.path.startswith("/data/") or request.url.path.startswith("/tables"):
            event_type = AuditEventType.DATA_ACCESSED
            resource = "data"

        # Determine result
        if error:
            result = AuditResult.FAILURE
        elif status_code == 401:
            result = AuditResult.DENIED
        elif status_code == 403:
            result = AuditResult.DENIED
        elif status_code >= 400:
            result = AuditResult.FAILURE
        else:
            result = AuditResult.SUCCESS

        # Log the event
        audit_logger.log(AuditEvent(
            event_type=event_type,
            user_id=user_id,
            tenant_id=tenant_id,
            resource=resource,
            action=request.method.lower(),
            result=result,
            details={
                "path": request.url.path,
                "query_params": {
                    k: ("[REDACTED]" if any(
                        frag in k.lower()
                        for frag in (
                            "token", "key", "password", "passwd", "pwd",
                            "secret", "auth", "session", "code", "jwt",
                        )
                    ) else v)
                    for k, v in request.query_params.items()
                },
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "request_body": request_body,
                "error": error,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        ))


# Convenience function to add middleware
def add_audit_middleware(app, **kwargs):
    """Add audit middleware to FastAPI app."""
    app.add_middleware(AuditMiddleware, **kwargs)