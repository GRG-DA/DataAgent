"""
Email Delivery Module
Send report emails via SMTP, SendGrid, or Amazon SES.

Usage:
    from export.email_delivery import send_report_email
    send_report_email("user@example.com", "Report", "<html>...", [("file.pdf", pdf_bytes, "application/pdf")], smtp_config)
"""

import logging
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def send_report_email(
    to_email: str,
    subject: str,
    html_body: str = "",
    attachments: Optional[List[Tuple[str, bytes, str]]] = None,
    smtp_config: Optional[Dict] = None,
) -> Dict:
    """
    Send a report email with optional attachments.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        html_body: HTML email body
        attachments: List of (filename, bytes, mime_type) tuples
        smtp_config: SMTP config dict with host, port, username, password

    Returns:
        Dict with success status and message
    """
    if smtp_config is None:
        smtp_config = {"host": "localhost", "port": 25, "username": "", "password": ""}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_config.get("from_address", "reports@example.com")
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)

        # Plain text fallback
        text_part = MIMEText(html_body.replace("<br>", "\n").replace("</p>", "\n").replace("<[^>]+>", ""), "plain")
        msg.attach(text_part)

        # HTML part
        if html_body:
            html_part = MIMEText(html_body, "html")
            msg.attach(html_part)

        # Attachments
        if attachments:
            mixed = MIMEMultipart("mixed")
            mixed.attach(msg)
            for filename, data, mime_type in attachments:
                part = MIMEBase(*mime_type.split("/"))
                part.set_payload(data)
                import email.encoders
                email.encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
                mixed.attach(part)
            msg = mixed

        # Send via SMTP
        provider = smtp_config.get("provider", "smtp")
        if provider == "sendgrid":
            # SendGrid API
            import urllib.request
            data = {
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": smtp_config.get("from_address", "reports@example.com")},
                "subject": subject,
                "content": [{"type": "text/html", "value": html_body}],
            }
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=json.dumps(data).encode(),
                headers={
                    "Authorization": f"Bearer {smtp_config.get('api_key', '')}",
                    "Content-Type": "application/json",
                },
            )
            urllib.request.urlopen(req)
        else:
            # Standard SMTP
            host = smtp_config.get("host", "localhost")
            port = int(smtp_config.get("port", 25))
            username = smtp_config.get("username", "")
            password = smtp_config.get("password", "")

            with smtplib.SMTP(host, port, timeout=30) as server:
                if port == 587:
                    server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)

        logger.info(f"Email sent to {to_email}: {subject}")
        return {"success": True, "message": f"Email sent to {to_email}"}

    except Exception as e:
        logger.error(f"Email delivery failed: {e}")
        return {"success": False, "message": str(e)}
