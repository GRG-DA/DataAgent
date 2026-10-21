"""
Per-request entitlement context — lets the shared LLM client know whether
the current request's user is entitled to IP rotation (WARP auto-reset).

Standalone module (no imports) so both `agent/llm_client.py` and the API
endpoints can import it without circular-import risk. The value is a
contextvar: it propagates through the async call chain and is safe under
concurrency.
"""
from __future__ import annotations

from contextvars import ContextVar

# None = not set (fall back to the client's configured auto_warp_reset).
# True/False = explicit entitlement for the current request.
current_ip_rotation: ContextVar = ContextVar("current_ip_rotation", default=None)


def set_ip_rotation(enabled: bool) -> None:
    """Set the current request's IP-rotation entitlement."""
    current_ip_rotation.set(bool(enabled))


def ip_rotation_enabled() -> bool:
    """True when the current request is entitled to IP rotation.

    Returns None-equivalent (False) when unset — callers that need the
    "unset" distinction should read current_ip_rotation.get() directly.
    """
    value = current_ip_rotation.get()
    return bool(value)