"""
Pre-computed Cache Service - L1 In-Process Cache.

Zero network overhead TTLCache implementation.
"""

from __future__ import annotations

import threading
import time

from .constants import HAS_CACHETOOLS, _get_l1_maxsize, _get_l1_ttl_seconds

if HAS_CACHETOOLS:
    from cachetools import TTLCache


# =============================================================================
# L1 In-Process Cache (TTLCache)
# =============================================================================


class L1Cache:
    """
    L1 In-Process Cache using cachetools.TTLCache.

    Zero network overhead - immediate response.
    Falls back to simple dict if cachetools not installed.
    """

    def __init__(self, maxsize: int | None = None, ttl: float | None = None):
        if maxsize is None:
            maxsize = _get_l1_maxsize()
        if ttl is None:
            ttl = _get_l1_ttl_seconds()
        self._ttl = ttl
        if HAS_CACHETOOLS:
            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        else:
            # Fallback: simple dict with timestamp
            self._cache: dict[str, tuple] = {}
            self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        """Get value from L1 cache."""
        with self._lock:
            if HAS_CACHETOOLS:
                return self._cache.get(key)
            else:
                # Manual TTL check for fallback
                entry = self._cache.get(key)
                if entry:
                    value, timestamp = entry
                    if time.time() - timestamp < self._ttl:
                        return value
                    else:
                        del self._cache[key]
                return None

    def set(self, key: str, value: str) -> None:
        """Set value in L1 cache."""
        with self._lock:
            if HAS_CACHETOOLS:
                self._cache[key] = value
            else:
                # Evict oldest if at capacity
                if len(self._cache) >= self._maxsize:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                self._cache[key] = (value, time.time())

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()


# Global L1 cache instance
_l1_cache = L1Cache()


def get_l1_cache() -> L1Cache:
    """Get the global L1 cache instance."""
    return _l1_cache
