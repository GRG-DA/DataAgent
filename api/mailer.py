"""Transactional e-mail for the account lifecycle (ADR-009).

The application had no mailer of its own: the notification digests borrowed
``api/notifications.py``'s private SMTP helper, which swallows failures and
cannot be reused for a password reset (which needs a return value and a
hard guarantee that a password never reaches the log).

The rules this module keeps:

* **It never raises.**  A mail outage must not break a signup or a password
  reset, so every failure is logged and turned into a ``MailResult``.
* **It never logs a secret.**  The recipient and the subject are logged; the
  body is not — the body carries the reset token.
* **It is honest.**  "There is no mail server on this install" and "the
  mail server refused the message" are DIFFERENT outcomes, and both are
  different from "the message was handed over".  A caller that cannot tell
  them apart ends up telling a user that a password reset was sent when
  nothing left the machine (C1).

The SMTP configuration comes from ``settings.notifications``
(``smtp_host``, ``smtp_port``, ``smtp_user``, ``smtp_password``,
``smtp_from``).
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from api.redact import redact_secrets
from config import settings

logger = logging.getLogger(__name__)

#: How long to wait for the SMTP server, in seconds.  A reset link that
#: arrives a minute late is still useful; a hung request is not.
SMTP_TIMEOUT_SECONDS = 15

#: The message reached the mail server.
STATUS_SENT = "sent"

#: No mail server is configured on this install — nothing was even attempted.
STATUS_UNCONFIGURED = "unconfigured"

#: A mail server is configured but the message could not be handed over.
STATUS_FAILED = "failed"

#: There was nobody to send the message to.
STATUS_NO_RECIPIENT = "no_recipient"


@dataclass(frozen=True)
class MailResult:
    """What happened to one message.

    ``status`` is one of :data:`STATUS_SENT`, :data:`STATUS_UNCONFIGURED`,
    :data:`STATUS_FAILED` or :data:`STATUS_NO_RECIPIENT`.  The object is
    falsy for everything but a delivered message, so ``if result:`` still
    reads as "did it go out?".
    """

    status: str
    detail: str = ""

    @property
    def sent(self) -> bool:
        """True when the message was handed over to the mail server."""
        return self.status == STATUS_SENT

    @property
    def configured(self) -> bool:
        """True when this install has a mail server at all."""
        return self.status != STATUS_UNCONFIGURED

    def __bool__(self) -> bool:
        return self.sent


def mail_configured() -> bool:
    """True when a mail server is configured at all."""
    return bool(str(getattr(settings.notifications, "smtp_host", "") or "").strip())


def mail_status() -> MailResult:
    """The install-wide mail state, without sending anything.

    ``STATUS_UNCONFIGURED`` means *no* message can leave this install; the
    account-lifecycle endpoints use it to answer the user truthfully
    instead of claiming a mail was sent.
    """
    if mail_configured():
        return MailResult(STATUS_SENT, "mail server configured")
    return MailResult(STATUS_UNCONFIGURED, "no mail server is configured")


def send_email(to: str, subject: str, body: str, html: Optional[str] = None) -> MailResult:
    """Send one mail and report exactly what happened.

    Never raises: a misconfigured or unreachable server is logged and
    reported as a failure.  The returned :class:`MailResult` distinguishes
    "not configured" from "configured but the send failed" from "sent", so
    a caller can answer the user honestly.
    """
    recipient = str(to or "").strip()
    if not recipient:
        logger.info("Mail not sent: no recipient")
        return MailResult(STATUS_NO_RECIPIENT, "no recipient")

    host = str(getattr(settings.notifications, "smtp_host", "") or "").strip()
    if not host:
        logger.warning(
            "Mail not configured — the message %r to %s was NOT sent. "
            "Configure notifications.smtp_host to enable the account "
            "lifecycle (password reset, e-mail verification, invites).",
            subject,
            recipient,
        )
        return MailResult(STATUS_UNCONFIGURED, "no mail server is configured")

    try:
        message = _build_message(recipient, subject, body, html)
        _deliver(host, message)
    except Exception as exc:  # noqa: BLE001 — a mail failure is never fatal
        logger.error(
            "Could not send the message %r to %s: %s",
            subject,
            recipient,
            redact_secrets(exc),
        )
        return MailResult(STATUS_FAILED, redact_secrets(exc))

    logger.info("Sent the message %r to %s", subject, recipient)
    return MailResult(STATUS_SENT)


def _build_message(recipient: str, subject: str, body: str, html: Optional[str]) -> MIMEMultipart:
    """Build the MIME message. The body may be plain text or HTML."""
    sender = str(getattr(settings.notifications, "smtp_from", "") or "noreply@localhost")
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.attach(MIMEText(body or "", "plain", "utf-8"))
    if html:
        message.attach(MIMEText(html, "html", "utf-8"))
    return message


def _deliver(host: str, message: MIMEMultipart) -> None:
    """Hand the message to the SMTP server."""
    port = int(getattr(settings.notifications, "smtp_port", 587) or 587)
    user = str(getattr(settings.notifications, "smtp_user", "") or "")
    password = str(getattr(settings.notifications, "smtp_password", "") or "")

    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
        try:
            server.starttls()
        except smtplib.SMTPException as exc:
            # Sending credentials over a plaintext connection is not an
            # option, so the mail is dropped instead.
            raise RuntimeError(
                f"the mail server does not support STARTTLS: {redact_secrets(exc)}"
            ) from exc
        if user:
            server.login(user, password)
        server.send_message(message)
