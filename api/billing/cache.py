"""
Per-request entitlement cache.

A ContextVar holds a dict (or None) scoped to the current request. The auth
middleware calls begin_request_cache() at the top of dispatch() so that plan
resolution and entitlement lookups happen at most once per request. Direct
calls outside a request (tests, scripts) see no cache and always compute.
"""
from __future__ import annotations

import contextvars
from typing import Optional

_request_cache: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "request_cache", default=None
)


def begin_request_cache() -> None:
    """Start a fresh per-request cache (call at the top of dispatch)."""
    _request_cache.set({})


def request_cache() -> Optional[dict]:
    """Return the current request cache, or None outside a request."""
    return _request_cache.get()


def clear_request_cache() -> None:
    """Drop the cache (used in tests / teardown)."""
    _request_cache.set(None)