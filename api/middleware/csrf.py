"""CSRF protection for cookie-authenticated browser requests.

Threat model
------------
The app authenticates a browser two ways:

* ``Authorization: Bearer <jwt>`` — a header the browser only sends when
  JavaScript sets it.  A cross-site attacker cannot attach that header to a
  forged request (an HTML form cannot set custom headers, and a CORS
  preflight from an origin that is not on the allow-list is refused), so
  those requests are **not** CSRF-able.
* the ``session_id`` HttpOnly cookie — the browser attaches it to *every*
  request to this origin, including one triggered by somebody else's page.
  Those requests **are** CSRF-able and must carry proof that the caller
  could read a cookie the attacker cannot.

The guard is a **signed double-submit cookie** (OWASP CSRF Cheat Sheet):

* the server issues a ``csrf_token`` cookie that is deliberately **readable
  by JavaScript** (not ``HttpOnly``) so the SPA can echo it back;
* the SPA sends the same value in the ``X-CSRF-Token`` header;
* the middleware compares header and cookie with a constant-time compare
  *and* checks the value against the one this session is entitled to
  (``HMAC-SHA256(session_id)``).

The last check is what a plain double-submit lacks: a hostile sibling
subdomain that can *inject* a cookie (``csrf_token=known-to-the-attacker``)
would otherwise let the attacker forge the matching header.  Deriving the
token from the session id keeps it unforgeable without a second secret, and
it is stable across replicas and across a restart.

The middleware runs **after** the auth middleware (it needs
``request.state.user``) and only guards requests that are actually
cookie-authenticated, so API clients (JWT / API key) and the
unauthenticated entry points are untouched.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Callable, Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

#: Name of the double-submit cookie.  Readable by JS on purpose.
CSRF_COOKIE_NAME = "csrf_token"

#: Header the SPA echoes the cookie value in.
CSRF_HEADER_NAME = "X-CSRF-Token"

#: Domain separation string for the token derivation.
CSRF_CONTEXT = b"dataagent-csrf-v1"

#: Lifetime of the CSRF cookie (matches the session cookie).
CSRF_COOKIE_MAX_AGE = 8 * 3600

#: Methods that cannot change state and therefore need no token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Endpoints that are never CSRF-checked, each with the reason it is safe.
EXEMPT_PATHS = frozenset({
    # No session exists yet when these are called, so no CSRF token can
    # exist either — checking them would make signing in impossible.
    "/auth/login",
    "/auth/register",
    # The central-accounts surface is the billing server's own login and
    # registration; it mints the session itself.
    "/auth/accounts/login",
    "/auth/accounts/register",
    # Password reset / verification / invite: the caller proves possession
    # of a single-use token from the email, not of a session.
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/verify-email",
    "/auth/resend-verification",
    "/auth/accept-invite",
    # Logout: a forged logout is a nuisance, not a breach, and refusing it
    # would strand a user whose CSRF cookie is missing (they could not log
    # out of the session they are in).
    "/auth/logout",
})

#: Error body returned when the token is missing or wrong.  It never
#: contains the expected token.
CSRF_ERROR_DETAIL = (
    "CSRF token missing or invalid. Send the csrf_token cookie value in "
    "the X-CSRF-Token header."
)


def derive_csrf_token(session_id: str) -> str:
    """The CSRF token this session is entitled to.

    Deterministic per session, so it survives a restart and is identical on
    every replica.  Empty when there is no session (nothing to bind to).
    """
    if not session_id:
        return ""
    return hmac.new(
        session_id.encode("utf-8"), CSRF_CONTEXT, hashlib.sha256
    ).hexdigest()


def tokens_match(left: str, right: str) -> bool:
    """Constant-time comparison of two tokens (never raises on odd input)."""
    if not left or not right:
        return False
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _has_header_credential(request: Request) -> bool:
    """True when the request carries a non-cookie credential.

    A browser form cannot set ``Authorization`` or ``X-API-Key``, so a
    request that carries one was not forged by a third-party page.
    """
    auth_header = request.headers.get("authorization", "") or ""
    if auth_header.lower().startswith("bearer "):
        return True
    return bool(request.headers.get("x-api-key"))


def write_csrf_cookie(response: Response, token: str, secure: bool = False) -> None:
    """Attach an already-derived CSRF token to *response*."""
    if not token:
        return
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=CSRF_COOKIE_MAX_AGE,
        httponly=False,  # the SPA must read it to echo it back
        secure=secure,
        samesite="lax",
        path="/",
    )


def set_csrf_cookie(response: Response, session_id: str, secure: bool = False) -> None:
    """Attach the double-submit cookie for *session_id* to *response*."""
    write_csrf_cookie(response, derive_csrf_token(session_id), secure=secure)


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Reject cookie-authenticated state-changing requests without a token."""

    def __init__(self, app, exempt_paths: Optional[Iterable[str]] = None):
        super().__init__(app)
        self.exempt_paths = frozenset(exempt_paths) if exempt_paths else EXEMPT_PATHS

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        user = getattr(request.state, "user", None)
        if user is None:
            # Not authenticated (the auth middleware answers 401) — a CSRF
            # check here would only break the public entry points.
            return await call_next(request)

        session_id = getattr(user, "session_id", None) or ""
        expected = derive_csrf_token(session_id)

        if self._requires_token(request, user) and not self._token_is_valid(
            request, expected
        ):
            return self._reject(request, expected)

        response = await call_next(request)
        self._ensure_token_cookie(response, request, expected)
        return response

    # ── internals ──────────────────────────────────────────────────────

    def _requires_token(self, request: Request, user) -> bool:
        """True when this request needs a CSRF token."""
        if request.method.upper() in SAFE_METHODS:
            return False
        if request.url.path in self.exempt_paths:
            return False
        if getattr(user, "auth_method", "") != "session":
            # Authenticated by a JWT or an API key — not CSRF-able.
            return False
        if _has_header_credential(request):
            # The SPA sends its JWT on every request; a forged request can
            # never carry that header.
            return False
        return True

    def _token_is_valid(self, request: Request, expected: str) -> bool:
        """The header must equal the cookie and equal the expected token."""
        header_token = request.headers.get(CSRF_HEADER_NAME, "") or ""
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "") or ""
        if not header_token or not cookie_token:
            return False
        if not tokens_match(header_token, cookie_token):
            return False
        return tokens_match(header_token, expected)

    def _reject(self, request: Request, expected: str) -> JSONResponse:
        """403 with a clear detail — never a 500, never the token."""
        logger.warning(
            "CSRF check failed: %s %s", request.method, request.url.path
        )
        response = JSONResponse(status_code=403, content={"detail": CSRF_ERROR_DETAIL})
        # Hand out a valid token so a client that simply lost its cookie
        # can recover with one retry.
        self._ensure_token_cookie(response, request, expected)
        return response

    def _ensure_token_cookie(
        self, response: Response, request: Request, expected: str
    ) -> None:
        """Issue (or refresh) the CSRF cookie when it is missing or stale."""
        if not expected:
            return
        if tokens_match(request.cookies.get(CSRF_COOKIE_NAME, ""), expected):
            return
        try:
            from api.middleware.auth_middleware import request_is_secure

            secure = request_is_secure(request)
        except Exception:  # noqa: BLE001 — never fail a response over a cookie
            secure = False
        try:
            write_csrf_cookie(response, expected, secure=secure)
        except Exception:  # noqa: BLE001
            logger.debug("csrf cookie could not be set", exc_info=True)


def add_csrf_middleware(app) -> None:
    """Register the CSRF guard on *app*."""
    app.add_middleware(CSRFProtectionMiddleware)
