"""Transactional e-mail for the account lifecycle (ADR-009).

The application had no mailer of its own: the notification digests borrowed
``api/notifications.py``'s private SMTP helper, which swallows failures and
cannot be reused for a password reset (which needs a return value and a
hard guarantee that a password never reaches the log).

The rules this module keeps:

* **It never raises.**  A mail outage must not break a signup or a password
  reset, so every failure is logged and turned into ``False``.
* **It never logs a secret.**  The recipient and the subject are logged; the
  body is not — the body carries the reset token.
* **It is opt-in.**  With no ``smtp_host`` configured the mailer logs at
  INFO and returns ``False``, so a desktop install without a mail server
  keeps working.

The SMTP configuration comes from ``settings.notifications``
(``smtp_host``, ``smtp_port``, ``smtp_user``, ``smtp_password``,
``smtp_from``).
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from api.redact import redact_secrets
from config import settings

logger = logging.getLogger(__name__)

#: How long to wait for the SMTP server, in seconds.  A reset link that
#: arrives a minute late is still useful; a hung request is not.
SMTP_TIMEOUT_SECONDS = 15


def mail_configured() -> bool:
    """True when a mail server is configured at all."""
    return bool(str(getattr(settings.notifications, "smtp_host", "") or "").strip())


def send_email(to: str, subject: str, body: str, html: Optional[str] = None) -> bool:
    """Send one mail. Returns True when it was handed to the server.

    Never raises: a misconfigured or unreachable server is logged and
    reported as ``False``.
    """
    recipient = str(to or "").strip()
    if not recipient:
        logger.info("Mail not sent: no recipient")
        return False

    host = str(getattr(settings.notifications, "smtp_host", "") or "").strip()
    if not host:
        logger.info(
            "Mail not configured — not sending the message %r to %s", subject, recipient
        )
        return False

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
        return False

    logger.info("Sent the message %r to %s", subject, recipient)
    return True


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
