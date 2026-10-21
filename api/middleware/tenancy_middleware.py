"""Bind the request to its tenant (ADR-009 D1).

Runs **after** the auth middleware (so ``request.state.user`` is populated)
and **before** the router, so every handler — and every helper that opens a
warehouse connection — sees the caller's tenant.

The tenant is carried in a :class:`contextvars.ContextVar`, which is
per-task: concurrent requests for different tenants cannot see each other's
tenant, even though they share one process and one warehouse file.
"""
from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.tenancy import (
    current_tenant_id,
    ensure_tenant_schema,
    reset_current_tenant,
    resolve_tenant_id,
    set_current_tenant,
)

logger = logging.getLogger(__name__)


class TenancyMiddleware(BaseHTTPMiddleware):
    """Resolve the caller's tenant and bind it for the request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        user = getattr(request.state, "user", None)
        tenant_id = resolve_tenant_id(user)

        token = set_current_tenant(tenant_id)
        try:
            # Make the tenant's schema exist before any handler queries it.
            # Idempotent and cached, so this is a no-op after the first call.
            if tenant_id:
                try:
                    ensure_tenant_schema(tenant_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("tenant schema setup failed for %s: %s", tenant_id, exc)
            response = await call_next(request)
            return response
        finally:
            reset_current_tenant(token)
            # Belt and braces: if the reset raced (token from another
            # context), make sure no tenant leaks into the next request.
            if current_tenant_id() != "default":
                set_current_tenant(None)


def add_tenancy_middleware(app) -> None:
    """Register the tenancy middleware on *app*."""
    app.add_middleware(TenancyMiddleware)
