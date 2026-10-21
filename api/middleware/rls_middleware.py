"""
RLS Middleware
Injects tenant context and rewrites queries for row-level security
"""

import logging
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from api.rls import rls_context, RLSManager, get_rls_manager
from api.middleware.auth_middleware import User

logger = logging.getLogger(__name__)


class RLSMiddleware(BaseHTTPMiddleware):
    """Middleware to inject RLS context for query rewriting."""

    def __init__(self, app, rls_manager: Optional[RLSManager] = None):
        super().__init__(app)
        self.rls_manager = rls_manager or get_rls_manager()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get authenticated user
        user: Optional[User] = getattr(request.state, "user", None)

        if user:
            # Set RLS context
            rls_context.set_user(user)
        else:
            # Clear context for unauthenticated requests
            rls_context.clear()

        try:
            response = await call_next(request)
        finally:
            # Clear context after request
            rls_context.clear()

        return response


def add_rls_middleware(app, rls_manager: Optional[RLSManager] = None):
    """Add RLS middleware to FastAPI app."""
    app.add_middleware(RLSMiddleware, rls_manager=rls_manager)


# SQL Tool integration
class RLSEnabledSQLTool:
    """Wrapper for SQLTool that applies RLS."""

    def __init__(self, sql_tool, rls_manager: Optional[RLSManager] = None):
        self.sql_tool = sql_tool
        self.rls_manager = rls_manager or get_rls_manager()

    def execute(self, query: str, user: Optional[User] = None):
        """Execute query with RLS applied."""
        # Get user from context if not provided
        if user is None:
            user = rls_context.get_user()

        if user and self.rls_manager:
            # Rewrite query with tenant filter
            original_query = query
            query = self.rls_manager.rewrite_query(query, user)

            if query != original_query:
                logger.debug(f"RLS rewrote query: {original_query[:100]}... -> {query[:100]}...")

        # Execute the (possibly rewritten) query
        return self.sql_tool.execute(query)