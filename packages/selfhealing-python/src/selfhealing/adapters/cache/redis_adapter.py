"""
Redis Cache Adapter for the self-healing system.

Implements CacheProviderInterface using Redis as the backend.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Optional

from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)

logger = logging.getLogger(__name__)


class RedisDistributedLock(DistributedLock):
    """
    Redis-based distributed lock implementation.

    Uses Redis SET NX with expiration for distributed locking.
    """

    def __init__(self, redis_lock):
        """
        Initialize with a redis-py Lock instance.

        Args:
            redis_lock: redis.lock.Lock instance
        """
        self._lock = redis_lock

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """Acquire the lock."""
        try:
            if timeout is not None:
                return self._lock.acquire(blocking=blocking, blocking_timeout=timeout)
            return self._lock.acquire(blocking=blocking)
        except Exception as e:
            logger.error(f"[RedisLock] Failed to acquire lock: {e}")
            return False

    def release(self) -> None:
        """Release the lock."""
        try:
            self._lock.release()
        except Exception as e:
            logger.warning(f"[RedisLock] Failed to release lock: {e}")

    def locked(self) -> bool:
        """Check if lock is currently held."""
        try:
            return self._lock.locked()
        except Exception:
            return False


class RedisCacheAdapter(CacheProviderInterface):
    """
    Redis implementation of CacheProviderInterface.

    Uses redis-py for all operations.
    Supports distributed locking via Redis SET NX.
    """

    def __init__(self, redis_client=None, key_prefix: str = "selfhealing:"):
        """
        Initialize the Redis cache adapter.

        Args:
            redis_client: Optional redis.Redis instance. If None, creates
                          a new client from Django settings or defaults.
            key_prefix: Prefix for all cache keys (default: "selfhealing:")
        """
        self._client = redis_client
        self._key_prefix = key_prefix

    @property
    def client(self):
        """Get Redis client, creating one if needed."""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self):
        """Create a Redis client from settings."""
        try:
            # Try Django cache first
            from django.core.cache import cache
            from django_redis import get_redis_connection

            return get_redis_connection("default")
        except ImportError:
            pass

        try:
            # Fallback to direct redis connection
            import redis

            return redis.Redis(host="localhost", port=6379, db=0)
        except ImportError:
            raise ImportError("redis-py is required for RedisCacheAdapter")

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "redis"

    # =========================================================================
    # Basic Operations
    # =========================================================================

    def get(self, key: str) -> Optional[Any]:
        """Get value by key."""
        try:
            value = self.client.get(self._make_key(key))
            if value is None:
                return None
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
        except Exception as e:
            logger.error(f"[RedisCache] Get failed for key '{key}': {e}")
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """Set value with optional TTL."""
        try:
            serialized = json.dumps(value)
            if ttl:
                return bool(self.client.setex(self._make_key(key), int(ttl.total_seconds()), serialized))
            return bool(self.client.set(self._make_key(key), serialized))
        except Exception as e:
            logger.error(f"[RedisCache] Set failed for key '{key}': {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        try:
            return bool(self.client.delete(self._make_key(key)))
        except Exception as e:
            logger.error(f"[RedisCache] Delete failed for key '{key}': {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        try:
            return bool(self.client.exists(self._make_key(key)))
        except Exception as e:
            logger.error(f"[RedisCache] Exists check failed for key '{key}': {e}")
            return False

    # =========================================================================
    # Atomic Operations
    # =========================================================================

    def incr(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter."""
        try:
            return self.client.incrby(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"[RedisCache] Incr failed for key '{key}': {e}")
            return 0

    def decr(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter."""
        try:
            return self.client.decrby(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"[RedisCache] Decr failed for key '{key}': {e}")
            return 0

    def expire(self, key: str, ttl: timedelta) -> bool:
        """Set expiration on existing key."""
        try:
            return bool(self.client.expire(self._make_key(key), int(ttl.total_seconds())))
        except Exception as e:
            logger.error(f"[RedisCache] Expire failed for key '{key}': {e}")
            return False

    def ttl(self, key: str) -> Optional[int]:
        """Get remaining TTL in seconds."""
        try:
            result = self.client.ttl(self._make_key(key))
            if result == -2:  # Key doesn't exist
                return -2
            if result == -1:  # No TTL set
                return None
            return result
        except Exception as e:
            logger.error(f"[RedisCache] TTL check failed for key '{key}': {e}")
            return -2

    # =========================================================================
    # Distributed Locking
    # =========================================================================

    def get_lock(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: Optional[float] = None,
    ) -> DistributedLock:
        """Get a distributed lock instance."""
        lock = self.client.lock(
            self._make_key(f"lock:{name}"),
            timeout=timeout.total_seconds(),
            blocking_timeout=blocking_timeout,
        )
        return RedisDistributedLock(lock)

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def mget(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values at once."""
        if not keys:
            return {}

        try:
            prefixed_keys = [self._make_key(k) for k in keys]
            values = self.client.mget(prefixed_keys)

            result = {}
            for key, value in zip(keys, values):
                if value is not None:
                    try:
                        result[key] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        result[key] = value
            return result
        except Exception as e:
            logger.error(f"[RedisCache] Mget failed: {e}")
            return {}

    def mset(
        self,
        mapping: dict[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """Set multiple values at once."""
        if not mapping:
            return True

        try:
            prefixed_mapping = {self._make_key(k): json.dumps(v) for k, v in mapping.items()}

            if ttl:
                # Redis doesn't support MSET with TTL, use pipeline
                pipe = self.client.pipeline()
                for key, value in prefixed_mapping.items():
                    pipe.setex(key, int(ttl.total_seconds()), value)
                pipe.execute()
                return True
            else:
                return bool(self.client.mset(prefixed_mapping))
        except Exception as e:
            logger.error(f"[RedisCache] Mset failed: {e}")
            return False

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return self.client.ping()
        except Exception as e:
            logger.error(f"[RedisCache] Health check failed: {e}")
            return False

    def flush_all(self) -> bool:
        """Clear all keys with our prefix (USE WITH CAUTION)."""
        try:
            # Only delete keys with our prefix
            pattern = f"{self._key_prefix}*"
            cursor = 0
            while True:
                cursor, keys = self.client.scan(cursor, match=pattern, count=100)
                if keys:
                    self.client.delete(*keys)
                if cursor == 0:
                    break
            return True
        except Exception as e:
            logger.error(f"[RedisCache] Flush failed: {e}")
            return False
