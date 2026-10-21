"""
Result Caching Module
Simple in-memory cache for analysis results with TTL support
"""
import logging
import time
import hashlib
import json
from typing import Any, Optional, Callable
from functools import wraps

logger = logging.getLogger(__name__)


class AnalysisCache:
    """Time-based cache for analysis results with metrics.

    Features:
    - TTL-based expiration
    - LRU-like eviction (oldest entries removed when full)
    - Key generation from function name + arguments
    - Hit/miss metrics for monitoring
    - Memory usage estimation
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 100):
        self._cache = {}  # key -> (timestamp, result)
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[Any]:
        """Get cached value. Returns None if missing or expired."""
        if key not in self._cache:
            self._misses += 1
            return None
        ts, value = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, key: str, value: Any):
        """Cache a value."""
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            del self._cache[oldest]
            self._evictions += 1
        self._cache[key] = (time.time(), value)

    def invalidate(self, key_prefix: str = ""):
        """Invalidate cache entries matching a key prefix."""
        if not key_prefix:
            self._cache.clear()
            return
        keys_to_delete = [k for k in self._cache if k.startswith(key_prefix)]
        for k in keys_to_delete:
            del self._cache[k]

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()

    def get_stats(self) -> dict:
        """Return cache statistics."""
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
        return {
            "entries": len(self._cache),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": round(hit_rate, 1),
            "evictions": self._evictions,
            "total_requests": total_requests,
        }

    @staticmethod
    def make_key(prefix: str, *args, **kwargs) -> str:
        """Generate a cache key from arguments."""
        key_parts = [prefix]
        for arg in args:
            key_parts.append(str(arg))
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}={v}")
        raw = ":".join(key_parts)
        return f"{prefix}:{hashlib.md5(raw.encode()).hexdigest()[:16]}"


# Global cache instance
cache = AnalysisCache(ttl_seconds=300, max_size=200)


def cached(ttl_seconds: int = 300):
    """Decorator that caches function results.

    Usage:
        @cached(ttl_seconds=600)
        def my_expensive_function(arg1, arg2):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = AnalysisCache.make_key(func.__name__, *args, **kwargs)
            result = cache.get(cache_key)
            if result is not None:
                logger.debug(f"Cache hit: {cache_key}")
                return result
            result = func(*args, **kwargs)
            cache.set(cache_key, result)
            logger.debug(f"Cache miss: {cache_key}")
            return result
        return wrapper
    return decorator


def invalidate_cache_for(prefix: str):
    """Convenience function to invalidate cache entries."""
    cache.invalidate(prefix)
