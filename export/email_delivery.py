"""
Email Delivery Module
Send report emails via SMTP, SendGrid, or Amazon SES.

Usage:
    from export.email_delivery import send_report_email
    send_report_email("user@example.com", "Report", "<html>...", [("file.pdf", pdf_bytes, "application/pdf")], smtp_config)

Security
--------
This module sends mail with the **operator's** SMTP identity to an address the
**caller** chooses. That combination is a phishing relay if the body and the
recipient are taken verbatim from a request body, so:

* the recipient address is validated (shape + length + no header characters);
* the subject is stripped of CR/LF (header injection) and length-capped;
* the HTML body is sanitised: scripts, frames, forms, event handlers and
  remote images are removed, so a caller cannot smuggle a fake login page or
  a tracking pixel out of the app's own mail identity;
* the attachment filename is sanitised before it reaches a header;
* failures are reported generically, so an SMTP host, username or password
  never reaches the caller.

Pass ``allow_html=True`` only for markup the application itself generated.
"""

import html as _html
import logging
import re
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Maximum length of the subject line.
MAX_SUBJECT_LENGTH = 200

#: Maximum length of a recipient address.
MAX_RECIPIENT_LENGTH = 254

#: Elements removed together with their content.
_STRIP_ELEMENTS = frozenset({"script", "style", "iframe", "object", "embed",
                             "form", "link", "meta", "base", "applet", "frame",
                             "frameset"})

#: Elements removed, but their content is kept.
_STRIP_TAGS_ONLY = frozenset({"input", "button", "textarea", "select", "option"})

#: Attributes that carry a URL.
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "background",
                        "poster", "data", "codebase", "cite"})

#: URL schemes that are never allowed.
_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:text/html", "data:application/")

#: URL schemes a report body may link to.
_SAFE_URL_SCHEMES = ("http:", "https:", "mailto:", "#", "/")

#: Data-URI image types a report body may embed.  SVG is EXCLUDED: an SVG can
#: carry <script> and event handlers, so it is a script vector, not a picture.
_SAFE_DATA_IMAGES = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/gif;base64,")

#: CSS constructs that can execute or fetch.  A style attribute is kept (the
#: report is styled), but these are removed from it.
_DANGEROUS_CSS = re.compile(
    r"(expression\s*\(|url\s*\(|javascript\s*:|vbscript\s*:|@import|behaviou?r\s*:|-moz-binding)",
    re.IGNORECASE,
)

#: Whitespace and control characters are stripped from a URL before its
#: scheme is tested: every HTML and URL parser removes them, so
#: "java\nscript:" IS "javascript:".
_URL_NOISE = re.compile(r"[\s\x00-\x20]+")

#: A conservative address shape: one @, no whitespace, no header characters.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

#: Characters that must never appear in a header value.
_HEADER_UNSAFE = re.compile(r"[\r\n\x00]")


class _EmailHtmlSanitizer(HTMLParser):
    """Strip everything that can make an email body dangerous.

    Removes script/frame/form elements (with their content), event-handler
    attributes, ``javascript:`` URLs, and remote images (a tracking pixel).
    ``data:`` images are kept: the report charts are embedded that way.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0
        self._skip_tag: str = ""

    # -- helpers ---------------------------------------------------------
    def _attrs(self, tag: str, attrs) -> str:
        out = []
        for name, value in attrs:
            lname = (name or "").lower()
            if lname.startswith("on"):
                continue  # event handler
            if lname in ("style",) and value:
                # Keep the formatting, drop the constructs that can execute
                # or fetch (expression(), url(), @import, behaviour:).
                value = _DANGEROUS_CSS.sub("", value)
                if not value.strip():
                    continue
            if lname in _URL_ATTRS and value:
                # Strip every whitespace/control char first: browsers do the
                # same before resolving a URL, so "java\nscript:alert(1)" IS
                # "javascript:alert(1)".  Testing the raw value let it through.
                probe = _URL_NOISE.sub("", value).lower()
                if probe.startswith(_DANGEROUS_SCHEMES):
                    continue
                if probe.startswith("data:"):
                    # An SVG data URI can carry <script> - only real raster
                    # images are allowed.
                    if not probe.startswith(_SAFE_DATA_IMAGES):
                        continue
                elif not probe.startswith(_SAFE_URL_SCHEMES):
                    continue
            if value is None:
                out.append(f' {name}')
            else:
                out.append(f' {name}="{_html.escape(value, quote=True)}"')
        return "".join(out)

    # -- HTMLParser API --------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _STRIP_ELEMENTS:
            self._skip_depth = 1
            self._skip_tag = tag
            return
        if tag in _STRIP_TAGS_ONLY:
            return
        if tag == "img":
            # Keep the element only for an embedded raster image; drop a
            # remote one (a tracking pixel) and an SVG (a script vector).
            src = ""
            for name, value in attrs:
                if (name or "").lower() == "src" and value:
                    src = value.strip().lower()
            if not src.startswith(_SAFE_DATA_IMAGES):
                return
        self._parts.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._skip_depth or tag in _STRIP_ELEMENTS or tag in _STRIP_TAGS_ONLY:
            return
        if tag == "img":
            src = ""
            for name, value in attrs:
                if (name or "").lower() == "src" and value:
                    src = value.strip().lower()
            if not src.startswith(_SAFE_DATA_IMAGES):
                return
        self._parts.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = ""
            return
        if tag in _STRIP_ELEMENTS or tag in _STRIP_TAGS_ONLY:
            return
        self._parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._parts.append(_html.escape(data, quote=False))

    def handle_comment(self, data):
        return

    def result(self) -> str:
        return "".join(self._parts)


def sanitize_email_html(html_body: str) -> str:
    """Return *html_body* with everything dangerous removed.

    The result is still valid HTML: the report markup (headings, tables,
    embedded charts) survives; scripts, frames, forms, inline event handlers,
    ``javascript:`` links and remote images do not.
    """
    if not html_body:
        return ""
    parser = _EmailHtmlSanitizer()
    try:
        parser.feed(html_body)
        parser.close()
        return parser.result()
    except Exception as exc:  # noqa: BLE001 — never fail a send over markup
        logger.warning("Email HTML sanitisation failed; sending plain text: %s", exc)
        return _html.escape(html_body)


def validate_recipient(to_email: str) -> str:
    """Return the validated recipient address, or raise ValueError."""
    value = (to_email or "").strip()
    if not value:
        raise ValueError("A recipient address is required.")
    if len(value) > MAX_RECIPIENT_LENGTH:
        raise ValueError("The recipient address is too long.")
    if _HEADER_UNSAFE.search(value):
        raise ValueError("The recipient address contains invalid characters.")
    if not _EMAIL_RE.match(value):
        raise ValueError("The recipient address is not a valid email address.")
    return value


def sanitize_subject(subject: str) -> str:
    """Return a header-safe subject line."""
    value = _HEADER_UNSAFE.sub(" ", subject or "").strip()
    if len(value) > MAX_SUBJECT_LENGTH:
        value = value[:MAX_SUBJECT_LENGTH].rstrip()
    return value


def sanitize_attachment_name(filename: str) -> str:
    """Return a header-safe attachment filename."""
    value = _HEADER_UNSAFE.sub("", filename or "report").replace('"', "").replace("\\", "_")
    value = re.sub(r"[\r\n\x00]", "", value).strip() or "report"
    return value[:120]


def _strip_tags(html_body: str) -> str:
    """A crude but dependency-free HTML-to-text conversion."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_body or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return _html.unescape(text)


def send_report_email(
    to_email: str,
    subject: str,
    html_body: str = "",
    attachments: Optional[List[Tuple[str, bytes, str]]] = None,
    smtp_config: Optional[Dict] = None,
    allow_html: bool = False,
) -> Dict:
    """
    Send a report email with optional attachments.

    Args:
        to_email: Recipient email address (validated).
        subject: Email subject line (sanitised).
        html_body: HTML email body.  Sanitised unless ``allow_html`` is set —
            pass ``allow_html=True`` only for markup the application built.
        attachments: List of (filename, bytes, mime_type) tuples.
        smtp_config: SMTP config dict with host, port, username, password.
        allow_html: Skip the HTML sanitiser.  Never set this for a body that
            came from a request.

    Returns:
        Dict with success status and message.  The message never contains an
        SMTP host, username, password or a stack trace.
    """
    if smtp_config is None:
        smtp_config = {"host": "localhost", "port": 25, "username": "", "password": ""}

    try:
        recipient = validate_recipient(to_email)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    safe_subject = sanitize_subject(subject)
    body_html = html_body if allow_html else sanitize_email_html(html_body)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = safe_subject
        msg["From"] = smtp_config.get("from_address", "reports@example.com")
        msg["To"] = recipient
        msg["Date"] = formatdate(localtime=True)

        # Plain text fallback
        text_part = MIMEText(_strip_tags(body_html), "plain")
        msg.attach(text_part)

        # HTML part
        if body_html:
            html_part = MIMEText(body_html, "html")
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
                safe_name = sanitize_attachment_name(filename)
                part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                mixed.attach(part)
            msg = mixed

        # Send via SMTP
        provider = smtp_config.get("provider", "smtp")
        if provider == "sendgrid":
            # SendGrid API
            import urllib.request
            data = {
                "personalizations": [{"to": [{"email": recipient}]}],
                "from": {"email": smtp_config.get("from_address", "reports@example.com")},
                "subject": safe_subject,
                "content": [{"type": "text/html", "value": body_html}],
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

        logger.info("Email sent to %s: %s", recipient, safe_subject)
        return {"success": True, "message": f"Email sent to {recipient}"}

    except Exception as e:  # noqa: BLE001
        # Log the detail server-side; return a generic message so an SMTP
        # host, username or password never reaches the caller.
        logger.error("Email delivery failed: %s", e)
        return {
            "success": False,
            "message": "The email could not be sent. Check the mail server configuration.",
        }
