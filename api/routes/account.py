"""The account lifecycle: reset, verification, invitations, self-serve delete.

ADR-009 turns the single-user desktop app into a hosted multi-tenant SaaS.
An account there has a lifecycle, and this module is its HTTP surface:

* ``POST /auth/forgot-password``  — ask for a reset link (never enumerates).
* ``POST /auth/reset-password``   — redeem it (single-use, policy-checked).
* ``POST /auth/verify-email``     — confirm the address of a new account.
* ``POST /auth/resend-verification`` — ask for a new confirmation mail.
* ``POST /auth/invite``            — an administrator creates an account.
* ``POST /auth/accept-invite``     — the invitee sets its own password.
* ``POST /auth/delete-account``    — an account deletes itself.
* ``GET  /auth/me``                — the caller's own profile.
* ``GET  /auth/me/export``         — the caller's own data (GDPR Art. 15/20).

The router stands on its own: it imports nothing from ``api.main`` (that
would be a circular import) and every handler resolves what it needs itself.
``api/main.py`` mounts it with ``app.include_router(account.router)``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.account_tokens import (
    KIND_EMAIL_VERIFICATION,
    KIND_INVITE,
    KIND_PASSWORD_RESET,
    consume_token,
    create_token,
    revoke_user_tokens,
)
from api.audit import AuditEvent, AuditEventType, audit_logger
from api.auth import revoke_user_sessions, user_manager
from api.mailer import (
    STATUS_FAILED,
    STATUS_SENT,
    STATUS_UNCONFIGURED,
    mail_configured,
    send_email,
)
from api.tenancy import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

router = APIRouter()

#: The answer every "does this account exist?" question gets.  It must not
#: depend on the input, or the endpoint becomes an account enumerator.
GENERIC_MESSAGE = "If that account exists, a reset link has been sent"

#: What the user is told when this install cannot deliver mail at all.
#: The desktop build ships without a mail server, so this is the *normal*
#: answer there — hiding it is what turned every forgotten password into an
#: unrecoverable account and every "forgot password" click into a support
#: ticket (C1).  It describes the INSTALL, never the account, so it cannot
#: be used to find out whether an address is registered.
MAIL_UNCONFIGURED_MESSAGE = (
    "Email delivery is not configured on this install, so no reset link was "
    "sent. Please ask the administrator to reset your password."
)

#: The same for the verification mail.
VERIFICATION_UNCONFIGURED_MESSAGE = (
    "Email delivery is not configured on this install, so no confirmation "
    "mail was sent. Please ask the administrator to confirm your address."
)

#: The delivery state of an install that CAN deliver mail.  It says nothing
#: about whether the request found an account — that must stay hidden.
DELIVERY_AVAILABLE = "available"

#: How long a reset link stays valid.
RESET_TOKEN_TTL_SECONDS = 3600

#: How long a verification link stays valid.
VERIFICATION_TOKEN_TTL_SECONDS = 86400

#: How long an invitation stays valid.
INVITE_TOKEN_TTL_SECONDS = 7 * 24 * 3600


# ── request models ─────────────────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    """Ask for a reset link.  Either the address or the name is enough."""

    email: str = ""
    username: str = ""


class ResetPasswordRequest(BaseModel):
    """Redeem a reset link."""

    token: str
    password: str


class VerifyEmailRequest(BaseModel):
    """Redeem a verification link."""

    token: str


class ResendVerificationRequest(BaseModel):
    """Ask for a new verification mail."""

    email: str = ""


class InviteRequest(BaseModel):
    """An administrator creates an account and invites it."""

    username: str
    email: str
    role: str = "user"
    tenant_id: str = ""


class AcceptInviteRequest(BaseModel):
    """Redeem an invitation."""

    token: str
    password: str


class DeleteAccountRequest(BaseModel):
    """An account deletes itself."""

    password: str
    confirm: bool = False


# ── helpers ────────────────────────────────────────────────────────────


def _current_user(request: Request) -> Any:
    """The authenticated caller.

    The router does not depend on ``api.dependencies`` (which pulls in the
    billing and session machinery) so it can be mounted on its own.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _require_admin(request: Request) -> Any:
    """The authenticated caller, and it has to be an administrator."""
    user = _current_user(request)
    if not (getattr(user, "is_admin", False) or getattr(user, "role", "") == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _rate_limit(request: Request, bucket: str = "accounts") -> None:
    """Keep the account endpoints from being used as a mail cannon."""
    from api.middleware.rate_limit import check_rate_limit

    check_rate_limit(request, bucket)


def _client_ip(request: Request) -> Optional[str]:
    try:
        return request.client.host if request.client else None
    except Exception:  # noqa: BLE001 — never let the audit break a request
        return None


def public_base_url() -> str:
    """The address the links in the mails point at.

    ``DATAAGENT_PUBLIC_URL`` wins (a hosted deployment knows its own name);
    otherwise the configured host and port are used.
    """
    override = (os.environ.get("DATAAGENT_PUBLIC_URL") or "").strip().rstrip("/")
    if override:
        return override
    try:
        from config import settings

        return f"http://{settings.app.host}:{settings.app.port}"
    except Exception:  # noqa: BLE001
        return "http://localhost:8002"


def _link(path: str, token: str) -> str:
    """A link carrying the token in its **fragment**.

    A fragment is never sent to the server, so the token cannot leak into an
    access log — the same reason ``/auth/verify`` refuses ``?token=``.
    """
    return f"{public_base_url()}{path}#token={quote(token, safe='')}"


def _audit(
    action: str,
    request: Request,
    user_id: str = "",
    resource: str = "account",
    details: Optional[Dict[str, Any]] = None,
    result: AuditEventType = AuditEventType.CONFIG_CHANGED,
) -> None:
    """Record an account-lifecycle event. Never breaks the request."""
    try:
        audit_logger.log(
            AuditEvent(
                event_type=result,
                user_id=user_id or "",
                resource=resource,
                action=action,
                details=details or {},
                ip_address=_client_ip(request),
            )
        )
    except Exception as exc:  # noqa: BLE001 — an audit write must not fail
        logger.warning("The audit of %s failed: %s", action, exc)


def _find_account(email: str = "", username: str = "") -> Optional[Dict[str, Any]]:
    """The account behind an address or a name, if there is one."""
    if email:
        found = user_manager.get_user_by_email(email)
        if found:
            return found
    if username:
        return user_manager.get_user_info(username)
    return None


def _policy_ok(password: str) -> None:
    """Raise when the password does not satisfy the policy."""
    ok, errors = user_manager.validate_password_policy(password or "")
    if not ok:
        raise HTTPException(status_code=400, detail="; ".join(errors))


def _delivery_status(result: Any) -> str:
    """Normalise a mail result into a delivery status.

    ``api.mailer.send_email`` returns a :class:`~api.mailer.MailResult`;
    a plain boolean is accepted too so a caller that patches the mailer
    keeps working.
    """
    status = getattr(result, "status", None)
    if status:
        return str(status)
    return STATUS_SENT if result else STATUS_FAILED


def _send_reset_link(account: Dict[str, Any]) -> str:
    """Mint a reset token and mail the link. Returns the delivery status."""
    token = create_token(
        KIND_PASSWORD_RESET,
        account.get("user_id", ""),
        RESET_TOKEN_TTL_SECONDS,
        meta={"username": account.get("username", "")},
    )
    link = _link("/reset-password", token)
    body = (
        f"Hello {account.get('username', '')},\n\n"
        "Somebody asked to reset the password of your DataAgent account.\n"
        "Open the link below to choose a new password. It works once and "
        f"expires in {RESET_TOKEN_TTL_SECONDS // 3600} hour(s).\n\n"
        f"{link}\n\n"
        "If this was not you, ignore this mail — nothing has changed."
    )
    return _delivery_status(
        send_email(account.get("email", ""), "Reset your DataAgent password", body)
    )


def _send_verification_link(account: Dict[str, Any]) -> str:
    """Mint a verification token and mail the link. Returns the delivery status."""
    token = create_token(
        KIND_EMAIL_VERIFICATION,
        account.get("user_id", ""),
        VERIFICATION_TOKEN_TTL_SECONDS,
        meta={"username": account.get("username", "")},
    )
    link = _link("/verify-email", token)
    body = (
        f"Welcome to DataAgent, {account.get('username', '')}.\n\n"
        "Confirm this address so we know it really is yours:\n\n"
        f"{link}\n\n"
        "The link works once."
    )
    return _delivery_status(
        send_email(account.get("email", ""), "Confirm your DataAgent account", body)
    )


def _send_invite_link(account: Dict[str, Any], token: str) -> str:
    """Mail the invitation link. Returns the delivery status."""
    link = _link("/accept-invite", token)
    body = (
        f"You have been invited to DataAgent as {account.get('username', '')}.\n\n"
        "Choose your password here:\n\n"
        f"{link}\n\n"
        "The link works once."
    )
    return _delivery_status(
        send_email(account.get("email", ""), "You are invited to DataAgent", body)
    )


def _send_welcome_mail(account: Dict[str, Any]) -> str:
    """Mail the welcome note. Returns the delivery status."""
    body = (
        f"Welcome to DataAgent, {account.get('username', '')}.\n\n"
        "Your account is ready. Your password is the one you just chose.\n"
        "Please confirm your e-mail address so we can reach you."
    )
    return _delivery_status(
        send_email(account.get("email", ""), "Welcome to DataAgent", body)
    )


# ── password reset ─────────────────────────────────────────────────────


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request_ctx: Request):
    """Ask for a password-reset link.

    Always answers the same way, whether or not the account exists: an
    endpoint that says "no such account" hands an attacker the user list.
    The attempt is audited either way.

    The answer is also honest about the INSTALL: when no mail server is
    configured (the shipped desktop build) the user is told that no mail
    was sent and to ask the administrator, instead of being told that a
    reset link is on its way.  That state is a property of the install,
    not of the account, so it cannot be used to enumerate accounts.

    A mail server that is configured but refuses the message is a
    per-request fact (only an existing account is mailed), so it is
    recorded in the audit log instead of being reported here — reporting
    it would turn the endpoint into an account enumerator.
    """
    _rate_limit(request_ctx)
    account = _find_account(body.email, body.username)
    unconfigured = not mail_configured()
    delivered = False
    if account and account.get("email"):
        delivered = _send_reset_link(account) == STATUS_SENT
    _audit(
        "password_reset_requested",
        request_ctx,
        user_id=(account or {}).get("user_id", ""),
        details={
            "username": (account or {}).get("username", ""),
            "delivered": bool(delivered),
            "mail_configured": not unconfigured,
        },
    )
    if unconfigured:
        return {
            "success": False,
            "message": MAIL_UNCONFIGURED_MESSAGE,
            "delivery": STATUS_UNCONFIGURED,
        }
    return {
        "success": True,
        "message": GENERIC_MESSAGE,
        "delivery": DELIVERY_AVAILABLE,
    }


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest, request_ctx: Request):
    """Redeem a reset link and set a new password.

    The token is single-use, the password has to satisfy the policy, and
    every session the account holds is revoked: a stolen token must not
    outlive the password it was issued for.
    """
    _rate_limit(request_ctx)
    _policy_ok(body.password)
    record = consume_token(KIND_PASSWORD_RESET, body.token)
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    account = user_manager.get_user_info_by_id(record.get("user_id", ""))
    if not account:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    username = account.get("username", "")
    if not user_manager.set_password(username, body.password):
        raise HTTPException(status_code=400, detail="Could not update the password")
    user_manager.set_must_change_password(username, False)
    user_manager.set_password_changed_at(username)

    # The account's outstanding tokens die with the old password.
    revoke_user_tokens(record.get("user_id", ""))
    revoke_user_sessions(username)

    _audit(
        "password_reset",
        request_ctx,
        user_id=record.get("user_id", ""),
        resource=username,
    )
    return {"success": True, "message": "Your password has been reset"}


# ── e-mail verification ─────────────────────────────────────────────────


@router.post("/auth/verify-email")
async def verify_email(body: VerifyEmailRequest, request_ctx: Request):
    """Confirm an address: the token flips the ``email_verified`` flag."""
    _rate_limit(request_ctx)
    record = consume_token(KIND_EMAIL_VERIFICATION, body.token)
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    account = user_manager.get_user_info_by_id(record.get("user_id", ""))
    if not account:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    user_manager.set_email_verified(account.get("username", ""), True)
    _audit(
        "email_verified",
        request_ctx,
        user_id=record.get("user_id", ""),
        resource=account.get("username", ""),
    )
    return {"success": True, "message": "Your e-mail address is confirmed"}


@router.post("/auth/resend-verification")
async def resend_verification(body: ResendVerificationRequest, request_ctx: Request):
    """Send a fresh verification mail.

    Always answers the same way, so the endpoint cannot be used to find out
    which addresses have an account — and it never claims a mail was sent
    when this install has no mail server (C1).
    """
    _rate_limit(request_ctx)
    email = (body.email or "").strip()
    if not email:
        user = getattr(request_ctx.state, "user", None)
        email = str(getattr(user, "email", "") or "")
    account = user_manager.get_user_by_email(email) if email else None
    unconfigured = not mail_configured()
    delivered = False
    if account and not account.get("email_verified"):
        delivered = _send_verification_link(account) == STATUS_SENT
    _audit(
        "verification_resent",
        request_ctx,
        user_id=(account or {}).get("user_id", ""),
        details={"delivered": bool(delivered), "mail_configured": not unconfigured},
    )
    if unconfigured:
        return {
            "success": False,
            "message": VERIFICATION_UNCONFIGURED_MESSAGE,
            "delivery": STATUS_UNCONFIGURED,
        }
    return {
        "success": True,
        "message": "If that account exists, a confirmation mail has been sent",
        "delivery": DELIVERY_AVAILABLE,
    }


# ── invitations ────────────────────────────────────────────────────────


@router.post("/auth/invite")
async def invite_user(body: InviteRequest, request_ctx: Request, admin=Depends(_require_admin)):
    """Create an account in the *invited* state and mail the invitation.

    Admin only.  The account has no password yet, so it cannot be used
    before the invitee accepts.

    The response never claims an invitation was sent when it was not: on an
    install without a mail server the admin is told the account exists but
    the invitation did not leave, and gets the link to hand over by hand.
    """
    from api.user_admin import validate_role

    try:
        role = validate_role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    username = (body.username or "").strip()
    email = (body.email or "").strip().lower()
    if not username or not email:
        raise HTTPException(status_code=400, detail="A username and an e-mail are required")
    if user_manager.get_user_info(username):
        raise HTTPException(status_code=409, detail=f"User '{username}' already exists")

    user_id = user_manager.add_user(
        username, "", role, email=email, tenant_id=(body.tenant_id or "").strip()
    )
    if not user_id:
        raise HTTPException(status_code=409, detail=f"User '{username}' already exists")
    user_manager.update_user(username, invited_by=getattr(admin, "user_id", ""))

    account = user_manager.get_user_info_by_id(user_id) or {}
    token = create_token(
        KIND_INVITE,
        user_id,
        INVITE_TOKEN_TTL_SECONDS,
        meta={
            "username": username,
            "role": role,
            "tenant_id": (body.tenant_id or "").strip(),
        },
    )
    delivery = _send_invite_link(account, token)

    _audit(
        "invite_sent",
        request_ctx,
        user_id=getattr(admin, "user_id", ""),
        resource=username,
        result=AuditEventType.USER_CREATED,
        details={
            "role": role,
            "tenant_id": (body.tenant_id or "").strip(),
            "delivery": delivery,
        },
    )
    if delivery == STATUS_SENT:
        return {
            "success": True,
            "message": f"An invitation has been sent to {email}",
            "delivery": delivery,
            "user": account,
        }
    if delivery == STATUS_UNCONFIGURED:
        message = (
            "The account was created, but the invitation e-mail was not "
            "delivered: email delivery is not configured on this install. "
            "Configure notifications.smtp_host, or hand the invite link below "
            "to the user directly."
        )
    else:
        message = (
            "The account was created, but the mail server refused the "
            "invitation, so it was not delivered. Check the mail server, or "
            "hand the invite link below to the user directly."
        )
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "message": message,
            "delivery": delivery,
            "invite_link": _link("/accept-invite", token),
            "user": account,
        },
    )


@router.post("/auth/accept-invite")
async def accept_invite(body: AcceptInviteRequest, request_ctx: Request):
    """Redeem an invitation: set the password and take the tenant."""
    _rate_limit(request_ctx)
    _policy_ok(body.password)
    record = consume_token(KIND_INVITE, body.token)
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    account = user_manager.get_user_info_by_id(record.get("user_id", ""))
    if not account:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    username = account.get("username", "")
    if not user_manager.set_password(username, body.password):
        raise HTTPException(status_code=400, detail="Could not update the password")
    user_manager.mark_invite_accepted(username)
    user_manager.set_must_change_password(username, False)

    tenant_id = str((record.get("meta") or {}).get("tenant_id") or "").strip()
    if not tenant_id:
        from api.tenancy import assign_tenant

        tenant_id = assign_tenant(record.get("user_id", ""))
    user_manager.update_user(username, tenant_id=tenant_id)

    delivery = _send_welcome_mail(user_manager.get_user_info(username) or account)
    _audit(
        "invite_accepted",
        request_ctx,
        user_id=record.get("user_id", ""),
        resource=username,
        details={"tenant_id": tenant_id, "delivery": delivery},
    )
    # The account IS ready — the welcome note is a courtesy, so the answer
    # stays successful but reports whether that mail actually left.
    return {
        "success": True,
        "message": "Your account is ready",
        "delivery": delivery,
    }


# ── self-serve delete ──────────────────────────────────────────────────


@router.post("/auth/delete-account")
async def delete_account(body: DeleteAccountRequest, request_ctx: Request,
                         user=Depends(_current_user)):
    """Delete the caller's own account and everything it owns.

    The cascade is the same one the administrator's delete runs: the
    account's warehouse rows and its tenant schema go with it.  The current
    password and an explicit confirmation are both required — the request
    must be deliberate.
    """
    from api.user_admin import CascadeError, delete_user_data, is_last_admin

    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                'Confirmation required: send {"confirm": true} to delete the '
                "account. This action is irreversible."
            ),
        )

    username = getattr(user, "username", "")
    if not user_manager.authenticate(username, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    account = user_manager.get_user_info(username)
    if not account:
        raise HTTPException(status_code=404, detail="User not found")
    if is_last_admin(account, user_manager.get_users()):
        raise HTTPException(status_code=409, detail="Cannot delete the last admin account")

    try:
        cascade = delete_user_data(account)
    except CascadeError as exc:
        logger.error("The delete cascade for %s failed: %s", username, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from None

    revoke_user_sessions(username)
    revoke_user_tokens(account.get("user_id", ""))
    user_manager.remove_user(username)

    _audit(
        "account_deleted",
        request_ctx,
        user_id=account.get("user_id", ""),
        resource=username,
        result=AuditEventType.USER_DELETED,
        details={"cascade": cascade},
    )
    return {"success": True, "deleted": username, "cascade": cascade}


# ── the caller's own profile ───────────────────────────────────────────


@router.get("/auth/me")
async def me(request: Request, user=Depends(_current_user)):
    """The caller's own profile.

    Self-only by construction: there is no parameter that could point the
    lookup at somebody else's account.
    """
    account = user_manager.get_user_info(getattr(user, "username", "")) or {}
    return {
        "success": True,
        "user": {
            "username": account.get("username", getattr(user, "username", "")),
            "email": account.get("email", ""),
            "email_verified": bool(account.get("email_verified", False)),
            "role": account.get("role", getattr(user, "role", "")),
            "tenant_id": account.get("tenant_id", getattr(user, "tenant_id", "") or ""),
            "plan": getattr(user, "plan", None) or "free",
            "must_change_password": bool(account.get("must_change_password", False)),
        },
    }


# ── data portability (GDPR Art. 15/20) ─────────────────────────────────


#: A filename that is safe to put in a Content-Disposition header.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.get("/auth/me/export")
async def export_my_data(request: Request, user=Depends(_current_user),
                         user_id: str = ""):
    """Everything the platform holds about the caller, as one JSON document.

    Self-only **by construction**: the document is built from the
    authenticated caller, never from a parameter, so there is nothing to
    point at somebody else's account.  A ``user_id`` that names another
    account is refused outright rather than ignored.

    The export carries a hard cap per table and per document; a cap that
    is hit is reported in ``truncated`` / ``truncation`` rather than
    silently dropping rows.  It never carries a password hash, an API
    key, an API-key hash, a token or a session id.
    """
    from api.user_data import build_user_export

    # The export can read the whole workspace, so it is rate limited like
    # the other account-lifecycle endpoints.
    _rate_limit(request)

    caller_id = str(getattr(user, "user_id", "") or "")
    if user_id and user_id != caller_id:
        raise HTTPException(
            status_code=403, detail="You may only export your own data"
        )

    tenant_id = str(getattr(user, "tenant_id", "") or DEFAULT_TENANT_ID)
    document = build_user_export(caller_id, tenant_id)

    _audit(
        "data_export",
        request,
        user_id=caller_id,
        resource="self",
        result=AuditEventType.DATA_ACCESSED,
        details={
            "tenant_id": tenant_id,
            "truncated": bool(document.get("truncated", False)),
        },
    )

    username = _UNSAFE_FILENAME.sub("_", str(getattr(user, "username", "") or "user"))
    return JSONResponse(
        content=document,
        headers={
            "Content-Disposition": f'attachment; filename="dataagent-export-{username}.json"',
        },
    )
