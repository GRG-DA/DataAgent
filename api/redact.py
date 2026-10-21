"""Redaction helpers for log messages.

Driver error messages can echo the connection string they were built from,
and a connection string can carry a password (``redis://user:secret@host``).
Logging one verbatim would put key material in the log file, so every
message that embeds a third-party exception goes through
:func:`redact_secrets` first.
"""
from __future__ import annotations

import re

# scheme://user:password@host  ->  scheme://user:***@host
_CREDENTIAL_RE = re.compile(r"(?i)(://[^:/\s@]+:)[^@/\s]*(@)")

# key=value / key: value pairs for well-known secret names.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|authorization)\b"
    r"(\s*[:=]\s*)(\"|')?[^\s\"',;]+"
)


def redact_secrets(text: object) -> str:
    """Return *text* with obvious credentials replaced by ``***``."""
    value = str(text)
    value = _CREDENTIAL_RE.sub(r"\1***\2", value)
    value = _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", value)
    return value
