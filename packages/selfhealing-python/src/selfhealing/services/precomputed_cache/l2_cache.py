"""
Pre-computed Cache Service - L2 Redis Cache.

Pre-computed JSON storage in Redis for low-latency access.
"""

from __future__ import annotations

import logging

from .constants import _get_l2_ttl_seconds

logger = logging.getLogger(__name__)


# =============================================================================
# L2 Redis Cache
# =============================================================================


class L2RedisCache:
    """
    L2 Redis Cache for pre-computed JSON.

    Stores pre-serialized JSON strings to eliminate serialization overhead
    on read path.
    """

    def __init__(self):
        self._redis = None
        self._initialized = False

    def _get_redis(self):
        """Lazy load Redis connection."""
        if self._redis is None:
            try:
                from django.core.cache import caches

                # Use 'default' cache which should be Redis in production
                # Note: Django caches uses dict-style access, not .get() method
                self._redis = caches["default"]
                self._initialized = True
            except Exception as e:
                logger.warning(f"[PrecomputedCache] Redis not available: {e}")
                self._redis = None
        return self._redis

    def get(self, key: str) -> str | None:
        """Get pre-computed JSON string from Redis."""
        try:
            redis = self._get_redis()
            if redis:
                value = redis.get(key)
                if isinstance(value, bytes):
                    return value.decode("utf-8")
                return value
        except Exception as e:
            logger.debug(f"[PrecomputedCache] Redis get failed: {e}")
        return None

    def set(self, key: str, value: str, ttl: float | None = None) -> bool:
        """Set pre-computed JSON string in Redis."""
        if ttl is None:
            ttl = _get_l2_ttl_seconds()
        try:
            redis = self._get_redis()
            if redis:
                redis.set(key, value, timeout=int(ttl))
                return True
        except Exception as e:
            logger.debug(f"[PrecomputedCache] Redis set failed: {e}")
        return False

    def is_available(self) -> bool:
        """Check if Redis is available."""
        try:
            redis = self._get_redis()
            return redis is not None
        except Exception:
            return False


# Global L2 cache instance
_l2_cache = L2RedisCache()


def get_l2_cache() -> L2RedisCache:
    """Get the global L2 Redis cache instance."""
    return _l2_cache
