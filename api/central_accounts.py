"""Central accounts proxy (ADR-002): email/password auth via the billing server.

Scope: the billing server stores ONLY the credential (email + bcrypt hash) and
purchase records. This router forwards register/login to it, then provisions
the same local session local accounts get (JWT + user record keyed by email).
Google sign-in shares the same email identity; entitlement sync pulls paid
plans down afterwards (best-effort, never downgrades).

Fail-open contract: unconfigured or unreachable → 503 with no partial state.
The SPA keeps using local accounts when central accounts are not configured.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.billing import cloud_sync
from api.bootstrap_gate import bootstrap_gate_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/accounts", tags=["accounts"])

REQUEST_TIMEOUT = 8.0
SSO_PROVIDER = "billing_accounts"

#: Error messages shared with ``/auth/register`` (api/main.py) so the two
#: bootstrap gates read identically.
NO_BOOTSTRAP_TOKEN_MESSAGE = (
    "No users exist and no bootstrap token is configured. "
    "Set DATAAGENT_BOOTSTRAP_TOKEN (or create an admin out-of-band) "
    "before registering."
)
INVALID_BOOTSTRAP_TOKEN_MESSAGE = (
    "A valid bootstrap token is required to create the first account."
)


class EmailInUseError(Exception):
    """The billing account's email is already linked to a different local
    sign-in method (local password / Google). Refusing to link prevents an
    unverified billing registration from taking over an existing local user."""


class AccountCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    #: Only needed for the very first account on a fresh instance.
    bootstrap_token: str = ""


def _base_url() -> str:
    """Billing server base URL, or "" when central accounts are unconfigured."""
    try:
        return (cloud_sync.billing_sync_base_url() or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def provision_user(
    email: str,
    store_user_id: str,
    bootstrap_token: str = "",
    request: Any = None,
) -> Optional[Dict[str, Any]]:
    """Find or create the local user for a central account. Returns user info.

    Mirrors the Google-login provisioning: username derived from the email
    local-part, no local password (the billing server verifies credentials).

    The first account on a fresh instance becomes admin. On a network-reachable
    instance that requires ``DATAAGENT_BOOTSTRAP_TOKEN`` — the same gate
    ``/auth/register`` enforces (ADR-009 D3). A loopback-only desktop install
    is exempt, matching local registration (ADR-019). Without the gate,
    "whoever registers first is admin" would be a takeover, not a bootstrap.
    """
    from api.auth import user_manager, self_serve_default_role

    email = (email or "").strip().lower()
    if not email:
        return None

    existing = user_manager.get_user_by_email(email)
    if existing:
        if existing.get("sso_provider") != SSO_PROVIDER:
            # Takeover guard: the billing server does not verify email
            # ownership yet, so only accounts created by this very path may
            # be re-entered. Linking to local/Google users needs a verified
            # email signal (follow-up).
            raise EmailInUseError(email)
        username = existing["username"]
        if not existing.get("sso_subject") and store_user_id:
            user_manager.update_user(username, sso_subject=store_user_id)
        return user_manager.get_user_info(username)

    base_username = email.split("@")[0] or "user"
    username = base_username
    counter = 1
    while user_manager.get_user_info(username):
        username = f"{base_username}{counter}"
        counter += 1

    first_user = not user_manager.get_users()
    # Same rule as local self-registration: the first account becomes
    # admin, everyone else gets the self-serve standard — not read-only
    # "viewer". A billing/checkout account must never land read-only.
    role = "admin" if first_user else self_serve_default_role()
    if first_user:
        _enforce_bootstrap_gate(bootstrap_token, request)

    user_manager.add_user(
        username=username,
        password="",  # central accounts: credentials live on the billing server
        role=role,
        email=email,
        sso_subject=store_user_id,
        sso_provider=SSO_PROVIDER,
    )
    return user_manager.get_user_info(username)


def _enforce_bootstrap_gate(supplied_token: str, request: Any = None) -> None:
    """Refuse to create the first admin without the bootstrap token.

    Raises ``HTTPException`` (403) with the same wording as
    ``/auth/register``. Loopback non-production installs are exempt; any
    forwarded/off-host request is gated. When no token is configured on a
    gated instance the first admin cannot be created over HTTP — an admin
    has to be provisioned out-of-band.
    """
    if not bootstrap_gate_required(request):
        return
    expected = (os.environ.get("DATAAGENT_BOOTSTRAP_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail=NO_BOOTSTRAP_TOKEN_MESSAGE)
    supplied = (supplied_token or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail=INVALID_BOOTSTRAP_TOKEN_MESSAGE)


def _complete_login(user: Dict[str, Any], email: str, request_ctx: Request) -> JSONResponse:
    """Issue the app JWT (+ best-effort session cookie), sync entitlements."""
    from api.auth import create_jwt_token
    from api.auth import self_serve_default_role
    from api.session import session_manager

    try:
        cloud_sync.sync_user(user.get("user_id", ""), email,
                             str(user.get("sso_subject", "")))
    except Exception as e:  # noqa: BLE001 — never block login on sync
        logger.warning("central accounts entitlement sync failed: %s", e)

    token = create_jwt_token(
        user["username"], role=user.get("role") or self_serve_default_role()
    )
    resp = JSONResponse(status_code=200, content={
        "success": True, "message": "Login successful",
        "user": user, "token": token,
    })
    try:
        session = session_manager.create_session(
            user_id=user.get("user_id", ""),
            tenant_id=user.get("tenant_id", ""),
            ip_address=request_ctx.client.host if request_ctx.client else "",
            user_agent=request_ctx.headers.get("user-agent", "")[:200],
        )
        # Behind a TLS-terminating proxy the scheme reads http, so honor the
        # first X-Forwarded-Proto hop as well.
        forwarded = request_ctx.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        use_https = request_ctx.url.scheme == "https" or forwarded == "https"
        resp.set_cookie(
            "session_id", session.id,
            max_age=8 * 3600, httponly=True, secure=use_https,
            samesite="lax", path="/",
        )
    except Exception:  # noqa: BLE001 — cookie is best-effort
        pass
    return resp


def _proxy(path: str, payload: Dict[str, str], request_ctx: Request) -> Dict[str, Any]:
    """POST to the billing server; raise HTTPException on failures."""
    base = _base_url()
    if not base:
        raise HTTPException(status_code=503, detail="Central accounts not configured")
    try:
        resp = httpx.post(f"{base}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("central accounts unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Central accounts unavailable — check your connection and try again",
        ) from exc
    return {"status": resp.status_code, "data": _safe_json(resp)}


def _safe_json(resp: httpx.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


@router.get("/status")
async def accounts_status() -> Dict[str, Any]:
    """Feature gate for the SPA: central accounts exist; configured = usable."""
    return {"enabled": True, "configured": bool(_base_url())}


@router.post("/register")
async def accounts_register(credentials: AccountCredentials, request_ctx: Request) -> JSONResponse:
    from api.middleware.rate_limit import check_rate_limit

    check_rate_limit(request_ctx, "accounts")
    email = credentials.email.strip().lower()
    result = _proxy("/v1/accounts/register",
                    {"email": email, "password": credentials.password}, request_ctx)

    status = result["status"]
    data = result["data"]
    if status == 409:
        raise HTTPException(status_code=409, detail="This email is already registered")
    if status == 400:
        raise HTTPException(status_code=400,
                            detail=str(data.get("detail") or "Password does not meet the policy"))
    if status == 422:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if status == 429:
        raise HTTPException(status_code=429, detail="Too many attempts — try again shortly")
    if status != 200 or not data.get("ok"):
        logger.warning("central register failed: HTTP %s", status)
        raise HTTPException(status_code=502, detail="Central accounts error — try again later")

    try:
        user = provision_user(data.get("email", email), str(data.get("user_id", "")),
                              credentials.bootstrap_token, request_ctx)
    except EmailInUseError:
        raise HTTPException(
            status_code=409,
            detail="An account for this email already exists on this machine "
                   "with a different sign-in method — sign in with that method",
        ) from None
    if not user:
        raise HTTPException(status_code=500, detail="Account created but local provisioning failed")
    return _complete_login(user, email, request_ctx)


@router.post("/login")
async def accounts_login(credentials: AccountCredentials, request_ctx: Request) -> JSONResponse:
    from api.middleware.rate_limit import check_rate_limit

    check_rate_limit(request_ctx, "accounts")
    email = credentials.email.strip().lower()
    result = _proxy("/v1/accounts/login",
                    {"email": email, "password": credentials.password}, request_ctx)

    status = result["status"]
    data = result["data"]
    if status == 401:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if status == 429:
        raise HTTPException(status_code=429, detail="Too many attempts — try again shortly")
    if status != 200 or not data.get("ok"):
        logger.warning("central login failed: HTTP %s", status)
        raise HTTPException(status_code=502, detail="Central accounts error — try again later")

    try:
        user = provision_user(data.get("email", email), str(data.get("user_id", "")),
                              credentials.bootstrap_token, request_ctx)
    except EmailInUseError:
        raise HTTPException(
            status_code=409,
            detail="An account for this email already exists on this machine "
                   "with a different sign-in method — sign in with that method",
        ) from None
    if not user:
        raise HTTPException(status_code=500, detail="Login succeeded but local provisioning failed")
    return _complete_login(user, email, request_ctx)
