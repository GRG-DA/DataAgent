"""Rate limiter for the auth, analytics and write endpoints.

Sliding-window limiter with a **per-user dimension** and a **pluggable
backend** (ADR-009 D5).

* *Per-user dimension* — an authenticated request is counted against the
  user, not the client IP. A single user behind one NAT therefore has their
  own budget instead of sharing it with everybody behind that NAT, and one
  user cannot dodge the limit by rotating source IPs. Requests that are not
  authenticated (and the pre-authentication buckets, where the identity is
  not established yet) are counted against the client IP.
* *Pluggable backend* — the counters live behind
  :class:`api.middleware.rate_limit_backends.RateLimitBackend`. The default
  is in-process memory (a single-process desktop install); when
  ``REDIS_URL`` is set the counters move to Redis so every replica shares
  one budget. See :func:`api.session.build_session_store` for the same
  convention.

Limits are configurable via settings.yaml ``security.rate_limit`` with sane
defaults (login: 10/min, register: 5/min, ask: 20/min, ask_sql: 30/min,
upload: 20/min, load: 20/min).

Response on limit: HTTP 429 with Retry-After header.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request

from api.middleware.rate_limit_backends import (
    InMemoryRateLimitBackend,
    RateLimitBackend,
    build_rate_limit_backend,
)
from api.redact import redact_secrets

logger = logging.getLogger(__name__)

# endpoint key -> (max requests, window seconds)
DEFAULT_LIMITS: Dict[str, Tuple[int, float]] = {
    "login": (10, 60.0),
    "register": (5, 60.0),
    "accounts": (10, 60.0),
    "execute": (30, 60.0),
    "sql": (120, 60.0),
    "llm_test": (10, 60.0),
    # The expensive LLM and write paths (ADR-009 D5). These were not
    # limited at all, so one client could spend the whole LLM budget or
    # fill the warehouse in a single burst.
    "ask": (20, 60.0),
    "ask_sql": (30, 60.0),
    "upload": (20, 60.0),
    "load": (20, 60.0),
}

# Buckets that are hit BEFORE the caller is identified. The identity is not
# established yet, so the client IP is the only key we can trust — keying
# them on a claimed user id would let an attacker rotate usernames to get a
# fresh bucket per attempt.
IP_ONLY_BUCKETS = frozenset({"login", "register", "accounts"})

# Settings.yaml override, resolved once at import: security.rate_limit.login = 10
_LIMITS: Dict[str, Tuple[int, float]] = dict(DEFAULT_LIMITS)


def _load_configured_limits() -> None:
    try:
        from config import settings
        rl = getattr(getattr(settings, "security", None), "rate_limit", None)
        if isinstance(rl, dict):
            for key, val in rl.items():
                if key in DEFAULT_LIMITS and val:
                    # val may be an int (per minute) or a dict {attempts, window_seconds}
                    if isinstance(val, dict):
                        attempts = int(val.get("attempts", DEFAULT_LIMITS[key][0]))
                        window = float(val.get("window_seconds", DEFAULT_LIMITS[key][1]))
                    else:
                        attempts, window = int(val), 60.0
                    if attempts > 0 and window > 0:
                        _LIMITS[key] = (attempts, window)
    except Exception:  # noqa: BLE001 - never let config break the limiter
        pass


_load_configured_limits()


def _trusted_proxy_networks() -> list:
    """Return the networks whose X-Forwarded-For header we trust.

    Loopback is always trusted (our own nginx in front on a desktop install).
    Docker puts nginx on a bridge network, so the api container sees the
    *bridge* address — not loopback — and the header was ignored entirely.
    That put every user in one shared rate-limit bucket: 10 failed logins
    anywhere locked everyone out.

    Extra networks can be added with ``TRUSTED_PROXY_NETWORKS``
    (comma-separated CIDRs or plain IPs, e.g. ``172.16.0.0/12``).
    """
    import ipaddress as _ip

    nets = []
    for raw in (os.environ.get("TRUSTED_PROXY_NETWORKS") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(_ip.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


def _client_ip(request: Request) -> str:
    # Honor X-Forwarded-For only from a trusted proxy (our own nginx). Direct
    # clients spoofing the header must not rotate buckets.
    import ipaddress as _ip

    def _valid(v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        try:
            _ip.ip_address(v)
            return v
        except ValueError:
            return ""

    try:
        peer = request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        peer = ""
    peer_ip = None
    try:
        peer_ip = _ip.ip_address(peer) if peer else None
    except ValueError:
        peer_ip = None

    trusted = peer in ("127.0.0.1", "::1", "localhost")
    if not trusted and peer_ip is not None:
        for net in _trusted_proxy_networks():
            try:
                if peer_ip in net:
                    trusted = True
                    break
            except TypeError:  # pragma: no cover - defensive
                continue

    if trusted:
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if _valid(xff):
            return xff
        rip = _valid(request.headers.get("x-real-ip") or "")
        if rip:
            return rip
    return _valid(peer) or "unknown"


def _request_user_id(request: Request) -> Optional[str]:
    """The authenticated user's id, or ``None`` for an anonymous request.

    ``AuthMiddleware`` puts the authenticated principal on
    ``request.state.user`` before the route handler runs, so the limiter can
    key on it without touching the auth plumbing.
    """
    state = getattr(request, "state", None)
    user = getattr(state, "user", None)
    if user is None:
        return None
    user_id = getattr(user, "user_id", None) or getattr(user, "username", None)
    return str(user_id) if user_id else None


def rate_limit_key(request: Request, bucket: str) -> str:
    """The key a request is counted against.

    Authenticated requests are counted per user so that a NAT-shared
    address does not merge unrelated users into one budget, and so that one
    user cannot spread their traffic over many IPs. Pre-authentication
    buckets and anonymous requests fall back to the client IP.
    """
    if bucket not in IP_ONLY_BUCKETS:
        user_id = _request_user_id(request)
        if user_id:
            return f"user:{user_id}"
    return f"ip:{_client_ip(request)}"


# --- backend wiring ---------------------------------------------------------

_BACKEND: Optional[RateLimitBackend] = None
_BACKEND_LOCK = threading.Lock()
# Fallback used when the shared backend is unreachable: a limiter outage
# must never turn into a 500.
_LOCAL_BACKEND = InMemoryRateLimitBackend()
# Throttle the outage warning so a Redis outage cannot flood the log.
_OUTAGE_LOG_INTERVAL = 60.0
_last_outage_log = 0.0


def get_rate_limit_backend() -> RateLimitBackend:
    """The active backend (Redis when ``REDIS_URL`` is set, else in-memory)."""
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = build_rate_limit_backend()
    return _BACKEND


def set_rate_limit_backend(backend: Optional[RateLimitBackend]) -> None:
    """Install a backend explicitly (tests / embedding).

    ``None`` drops the cached backend so the next call re-resolves
    ``REDIS_URL``.
    """
    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = backend


def reset_rate_limit_backend() -> None:
    """Forget the cached backend so the next call re-resolves the backend."""
    set_rate_limit_backend(None)


def _hit(key: str, bucket: str, max_attempts: int, window: float):
    """Count one hit, degrading to local counters if the backend is down."""
    global _last_outage_log
    try:
        return get_rate_limit_backend().hit(key, bucket, max_attempts, window)
    except Exception as exc:  # noqa: BLE001 - a limiter outage must not 500
        now = time.monotonic()
        if now - _last_outage_log > _OUTAGE_LOG_INTERVAL:
            _last_outage_log = now
            logger.warning(
                "Rate-limit backend unavailable (%s) — using local counters",
                redact_secrets(exc),
            )
        else:
            logger.debug("Rate-limit backend unavailable: %s", redact_secrets(exc))
        return _LOCAL_BACKEND.hit(key, bucket, max_attempts, window)


def check_rate_limit(request: Request, bucket: str) -> None:
    """Raise HTTP 429 when the caller exceeds the bucket's limit.

    Buckets: 'login', 'register', 'accounts', 'ask', 'ask_sql', 'upload',
    'load', 'execute', 'sql', 'llm_test' (extensible via _LIMITS).
    """
    max_attempts, window = _LIMITS.get(bucket, (60, 60.0))
    key = rate_limit_key(request, bucket)
    decision = _hit(key, bucket, max_attempts, window)

    if not decision.allowed:
        retry_after = max(1, int(decision.retry_after) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def reset_rate_limits() -> None:
    """Clear all counters (test helper / admin action)."""
    try:
        get_rate_limit_backend().reset()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not reset the rate-limit backend: %s", exc)
    _LOCAL_BACKEND.reset()


def current_limits() -> Dict[str, Tuple[int, float]]:
    """Effective limits, for docs/health introspection."""
    return dict(_LIMITS)
