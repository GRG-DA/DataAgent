"""Pluggable rate-limit backends.

A plain in-process dict cannot serve a hosted deployment for two reasons:

* **A per-user dimension.** A single user behind one NAT must not share a
  bucket with everybody else behind that NAT, and one user must not be able
  to dodge the limit by rotating source IPs. The key is computed by the
  caller (:mod:`api.middleware.rate_limit`) and passed in here.
* **Cross-replica state.** With more than one API replica the counter has to
  live outside the process, otherwise every replica enforces its own budget.

``RateLimitBackend`` is the seam. The default implementation keeps the
counters in process memory (right for a single-process desktop install);
``RedisRateLimitBackend`` keeps them in Redis so every replica shares one
sliding window.

Backend selection follows the convention already used by
:func:`api.session.build_session_store`: Redis when ``REDIS_URL`` is set,
in-memory otherwise.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

logger = logging.getLogger(__name__)

# How often the in-memory backend sweeps fully-expired buckets.
_SWEEP_INTERVAL = 300.0

# Upper bound on the number of Redis keys the backend remembers, so that
# reset() can clean up without growing without bound.
_MAX_TRACKED_KEYS = 10000

# Atomic sliding window (Redis sorted set). Returns
# ``{allowed, count, retry_after_seconds}`` so the decision is made in one
# round trip and cannot interleave with another replica's increment.
#
# KEYS[1] = sorted-set key
# ARGV[1] = now (epoch seconds)
# ARGV[2] = window (seconds)
# ARGV[3] = limit (max attempts)
# ARGV[4] = unique member for this hit
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', '(' .. (now - window))
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then
    retry = (tonumber(oldest[2]) + window) - now
  end
  return {0, count, tostring(retry)}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, math.ceil(window))
return {1, count + 1, '0'}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of one limiter hit.

    ``retry_after`` is the number of seconds until the oldest hit in the
    window expires (0 when the request was allowed).
    """

    allowed: bool
    count: int
    retry_after: float = 0.0


class RateLimitBackend(ABC):
    """A sliding-window counter store."""

    @abstractmethod
    def hit(self, key: str, bucket: str, max_attempts: int, window: float) -> RateLimitDecision:
        """Count one hit against *key* in *bucket*.

        Returns a decision; never raises for a "limit reached" condition.
        """

    @abstractmethod
    def reset(self) -> None:
        """Drop the counters this backend owns."""


class InMemoryRateLimitBackend(RateLimitBackend):
    """Process-local sliding window (default backend).

    One deque of timestamps per ``(bucket, key)`` pair, guarded by a mutex.
    This is the behaviour the limiter had before ADR-009: correct for a
    single-process install, and the fallback when a shared backend is
    unreachable.
    """

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    @staticmethod
    def _slot(bucket: str, key: str) -> str:
        return f"{bucket}|{key}"

    def hit(self, key: str, bucket: str, max_attempts: int, window: float) -> RateLimitDecision:
        now = time.monotonic()
        slot = self._slot(bucket, key)
        with self._lock:
            window_deque = self._hits.get(slot)
            if window_deque is None:
                window_deque = deque()
                self._hits[slot] = window_deque

            cutoff = now - window
            while window_deque and window_deque[0] < cutoff:
                window_deque.popleft()

            if len(window_deque) >= max_attempts:
                retry_after = max(0.0, window - (now - window_deque[0]))
                return RateLimitDecision(False, len(window_deque), retry_after)

            window_deque.append(now)
            self._maybe_sweep(now)
            return RateLimitDecision(True, len(window_deque), 0.0)

    def _maybe_sweep(self, now: float) -> None:
        """Drop fully-expired buckets so idle keys don't accumulate."""
        if now - self._last_sweep <= _SWEEP_INTERVAL:
            return
        self._last_sweep = now
        for slot, window_deque in list(self._hits.items()):
            bucket = slot.split("|", 1)[0]
            limit_window = _bucket_window(bucket)
            cutoff = now - limit_window
            while window_deque and window_deque[0] < cutoff:
                window_deque.popleft()
            if not window_deque:
                del self._hits[slot]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_sweep = 0.0


def _bucket_window(bucket: str, default: float = 60.0) -> float:
    """The configured window for *bucket* (used only for sweeping)."""
    try:
        from api.middleware.rate_limit import _LIMITS

        return float(_LIMITS.get(bucket, (60, default))[1])
    except Exception:  # noqa: BLE001 - sweeping must never break the limiter
        return default


class RedisRateLimitBackend(RateLimitBackend):
    """Sliding-window counter kept in Redis so replicas share one budget.

    Each ``(bucket, key)`` pair is a sorted set of hit timestamps. The
    window is trimmed on every hit, so the set never grows without bound.

    The counter is incremented with a Lua script so "check the count" and
    "record the hit" happen atomically — two replicas cannot both squeeze
    past the limit. When the server cannot run scripts the backend falls
    back to the equivalent sequence of primitive commands; that path is not
    atomic and may admit one extra request under contention.
    """

    def __init__(self, redis_url: Optional[str] = None, client=None,
                 prefix: str = "ratelimit:"):
        if client is not None:
            self.client = client
        else:
            import redis

            self.client = redis.from_url(
                redis_url or "redis://localhost:6379", decode_responses=True
            )
        self.prefix = prefix
        self._lock = threading.Lock()
        self._keys: set[str] = set()

    def _redis_key(self, key: str, bucket: str) -> str:
        return f"{self.prefix}{bucket}:{key}"

    @staticmethod
    def _member(now: float) -> str:
        """A unique member id — two hits may share a timestamp."""
        return f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    def hit(self, key: str, bucket: str, max_attempts: int, window: float) -> RateLimitDecision:
        redis_key = self._redis_key(key, bucket)
        # Wall clock, not monotonic: the timestamps are shared between
        # replicas and monotonic clocks are per-process.
        now = time.time()
        with self._lock:
            self._keys.add(redis_key)
            if len(self._keys) > _MAX_TRACKED_KEYS:
                # The tracked set only exists so reset() can clean up; drop it
                # rather than grow without bound in a long-running process.
                self._keys.clear()
        try:
            result = self.client.eval(
                _SLIDING_WINDOW_LUA, 1, redis_key, now, window,
                max_attempts, self._member(now),
            )
        except Exception as exc:  # noqa: BLE001 - fall back, never fail the request
            logger.debug("Atomic rate-limit script unavailable (%s) — using primitives", exc)
            return self._hit_primitives(redis_key, now, max_attempts, window)
        # A malformed reply must not be retried through the primitive path:
        # Redis already recorded the hit, and counting it twice would make
        # the limiter stricter than configured.
        return _decision_from_script(result)

    def _hit_primitives(self, redis_key: str, now: float,
                        max_attempts: int, window: float) -> RateLimitDecision:
        """Non-atomic equivalent of the Lua script (best effort)."""
        client = self.client
        client.zremrangebyscore(redis_key, "-inf", f"({now - window}")
        count = int(client.zcard(redis_key))
        if count >= max_attempts:
            retry_after = window
            oldest = client.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                retry_after = max(0.0, float(oldest[0][1]) + window - now)
            return RateLimitDecision(False, count, retry_after)
        client.zadd(redis_key, {self._member(now): now})
        client.expire(redis_key, max(1, int(math.ceil(window))))
        return RateLimitDecision(True, count + 1, 0.0)

    def reset(self) -> None:
        """Delete the keys this instance has written (test/admin helper)."""
        with self._lock:
            keys = list(self._keys)
            self._keys.clear()
        for redis_key in keys:
            try:
                self.client.delete(redis_key)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not clear rate-limit key: %s", exc)


def _decision_from_script(result) -> RateLimitDecision:
    """Map the Lua script's ``{allowed, count, retry}`` reply to a decision."""
    try:
        allowed = int(result[0])
        count = int(result[1])
        retry_after = float(result[2])
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"unexpected rate-limit script reply: {result!r}") from exc
    return RateLimitDecision(bool(allowed), count, max(0.0, retry_after))


def build_rate_limit_backend(redis_url: Optional[str] = None) -> RateLimitBackend:
    """Pick the rate-limit backend.

    Redis is used when ``REDIS_URL`` is configured (or a URL is passed in) so
    several app replicas share one budget; otherwise the in-memory backend
    is used, which is the right default for a single-process install. A
    missing or broken Redis client degrades to the in-memory backend rather
    than taking the app down.
    """
    url = redis_url if redis_url is not None else (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return InMemoryRateLimitBackend()
    try:
        return RedisRateLimitBackend(redis_url=url)
    except Exception as exc:  # noqa: BLE001 — never block start-up on Redis
        logger.warning("Redis rate-limit backend unavailable (%s) — using in-memory", exc)
        return InMemoryRateLimitBackend()
