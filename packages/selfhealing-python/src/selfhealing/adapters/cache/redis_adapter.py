"""
Redis Cache Adapter for Self-Healing System

Concrete implementation of CacheProviderInterface using Redis.
Provides distributed caching with locking support for circuit breakers.

Requirements:
    - redis>=4.0.0
    - django-redis (optional, for Django cache integration)

Related:
    - interfaces/cache_provider.py: Interface definition
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import timedelta
from typing import Any

from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)

logger = logging.getLogger(__name__)


class RedisDistributedLock(DistributedLock):
    """
    Redis-based distributed lock using SET NX with TTL.

    Uses the Redis SET command with NX (not exists) and PX (expire)
    options for atomic lock acquisition.

    Features:
        - Atomic acquire/release operations
        - Automatic TTL-based expiration (prevents deadlocks)
        - Owner identification for safe release
        - TTL extension support
    """

    def __init__(
        self,
        redis_client: Any,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: float | None = None,
        sleep_interval: float = 0.1,
    ) -> None:
        """
        Initialize Redis distributed lock.

        Args:
            redis_client: Redis client instance
            name: Lock name (key in Redis)
            timeout: Lock auto-expire time
            blocking_timeout: Max time to wait when acquiring
            sleep_interval: Time between acquire retries
        """
        self._redis = redis_client
        self._name = f"lock:{name}"
        self._timeout = timeout
        self._blocking_timeout = blocking_timeout
        self._sleep_interval = sleep_interval

        # Unique owner ID for this lock instance
        self._owner_id = f"{threading.get_ident()}:{id(self)}:{time.time()}"
        self._acquired = False

    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """
        Acquire the lock.

        Uses SET NX PX for atomic acquisition with TTL.

        Args:
            blocking: If True, retry until acquired or timeout
            timeout: Override blocking_timeout

        Returns:
            True if lock was acquired
        """
        blocking_timeout = timeout if timeout is not None else self._blocking_timeout
        timeout_ms = int(self._timeout.total_seconds() * 1000)
        stop_time = None

        if blocking and blocking_timeout is not None:
            stop_time = time.time() + blocking_timeout

        while True:
            # Try to acquire: SET key value NX PX timeout
            acquired = self._redis.set(
                self._name,
                self._owner_id,
                nx=True,
                px=timeout_ms,
            )

            if acquired:
                self._acquired = True
                logger.debug(f"[RedisLock] Acquired lock: {self._name}")
                return True

            if not blocking:
                return False

            if stop_time is not None and time.time() >= stop_time:
                logger.debug(f"[RedisLock] Timeout acquiring lock: {self._name}")
                return False

            time.sleep(self._sleep_interval)

    def release(self) -> None:
        """
        Release the lock if owned by this instance.

        Uses Lua script for atomic check-and-delete.

        Raises:
            LockNotOwnedError: If lock is not owned by this instance
        """
        if not self._acquired:
            logger.warning(f"[RedisLock] Attempting to release non-acquired lock: {self._name}")
            return

        # Lua script for atomic check-and-delete
        # Only delete if the value matches our owner ID
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = self._redis.eval(lua_script, 1, self._name, self._owner_id)
            if result == 1:
                self._acquired = False
                logger.debug(f"[RedisLock] Released lock: {self._name}")
            else:
                logger.warning(f"[RedisLock] Lock not owned or expired: {self._name}")
                self._acquired = False
        except Exception as e:
            logger.error(f"[RedisLock] Error releasing lock: {e}")
            self._acquired = False
            raise

    def locked(self) -> bool:
        """Check if lock is currently held by anyone."""
        return self._redis.exists(self._name) > 0

    def owned(self) -> bool:
        """Check if lock is held by this instance."""
        if not self._acquired:
            return False
        current_owner = self._redis.get(self._name)
        if isinstance(current_owner, bytes):
            current_owner = current_owner.decode("utf-8")
        return current_owner == self._owner_id

    def extend(self, additional_time: timedelta) -> bool:
        """
        Extend the lock's TTL.

        Uses Lua script to atomically verify ownership and extend.

        Args:
            additional_time: Time to add to current TTL

        Returns:
            True if extension was successful
        """
        if not self._acquired:
            return False

        additional_ms = int(additional_time.total_seconds() * 1000)

        # Lua script: verify ownership then extend TTL
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("pexpire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        try:
            result = self._redis.eval(lua_script, 1, self._name, self._owner_id, additional_ms)
            return result == 1
        except Exception as e:
            logger.error(f"[RedisLock] Error extending lock: {e}")
            return False


class RedisCacheAdapter(CacheProviderInterface):
    """
    Redis implementation of CacheProviderInterface.

    This adapter provides full Redis caching functionality including
    distributed locks, atomic counters, and TTL management.

    Configuration:
        Can be initialized with explicit Redis URL or will use
        Django settings REDIS_URL.

    Example:
        >>> cache = RedisCacheAdapter(url="redis://localhost:6379/0")
        >>> cache.set("key", {"data": "value"}, ttl=timedelta(minutes=5))
        >>> data = cache.get("key")
        >>> with cache.get_lock("my_lock") as lock:
        ...     # Critical section
        ...     pass
    """

    def __init__(
        self,
        url: str | None = None,
        client: Any | None = None,
        key_prefix: str = "selfhealing:",
        default_ttl: timedelta | None = None,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        retry_on_timeout: bool = True,
    ) -> None:
        """
        Initialize Redis cache adapter.

        Args:
            url: Redis URL (e.g., "redis://localhost:6379/0")
            client: Pre-configured Redis client (takes precedence over url)
            key_prefix: Prefix for all cache keys
            default_ttl: Default TTL for set operations
            socket_timeout: Socket timeout for operations
            socket_connect_timeout: Socket connection timeout
            retry_on_timeout: Retry on timeout errors
        """
        self._key_prefix = key_prefix
        self._default_ttl = default_ttl

        if client is not None:
            self._redis = client
        else:
            import redis

            if url is None:
                from django.conf import settings

                url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")

            self._redis = redis.from_url(
                url,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                retry_on_timeout=retry_on_timeout,
                decode_responses=False,  # We handle encoding ourselves
            )

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    def _serialize(self, value: Any) -> bytes:
        """Serialize value to bytes."""
        return json.dumps(value, default=str).encode("utf-8")

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize bytes to Python object."""
        if data is None:
            return None
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    @property
    def provider_name(self) -> str:
        """Return 'redis' as the provider identifier."""
        return "redis"

    # =========================================================================
    # Basic Operations
    # =========================================================================

    def get(self, key: str) -> Any | None:
        """Get value by key."""
        try:
            data = self._redis.get(self._make_key(key))
            if data is None:
                return None
            return self._deserialize(data)
        except Exception as e:
            logger.error(f"[RedisCache] Get error for {key}: {e}")
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None,
    ) -> bool:
        """Set value with optional TTL."""
        try:
            ttl = ttl or self._default_ttl
            serialized = self._serialize(value)

            if ttl:
                return bool(
                    self._redis.set(
                        self._make_key(key),
                        serialized,
                        ex=int(ttl.total_seconds()),
                    )
                )
            else:
                return bool(self._redis.set(self._make_key(key), serialized))
        except Exception as e:
            logger.error(f"[RedisCache] Set error for {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        try:
            return self._redis.delete(self._make_key(key)) > 0
        except Exception as e:
            logger.error(f"[RedisCache] Delete error for {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        try:
            return self._redis.exists(self._make_key(key)) > 0
        except Exception as e:
            logger.error(f"[RedisCache] Exists error for {key}: {e}")
            return False

    # =========================================================================
    # Atomic Operations
    # =========================================================================

    def incr(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter."""
        try:
            return self._redis.incr(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"[RedisCache] Incr error for {key}: {e}")
            return 0

    def decr(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter."""
        try:
            return self._redis.decr(self._make_key(key), amount)
        except Exception as e:
            logger.error(f"[RedisCache] Decr error for {key}: {e}")
            return 0

    def expire(self, key: str, ttl: timedelta) -> bool:
        """Set expiration on existing key."""
        try:
            return bool(
                self._redis.expire(
                    self._make_key(key),
                    int(ttl.total_seconds()),
                )
            )
        except Exception as e:
            logger.error(f"[RedisCache] Expire error for {key}: {e}")
            return False

    def ttl(self, key: str) -> int | None:
        """Get remaining TTL in seconds."""
        try:
            result = self._redis.ttl(self._make_key(key))
            if result == -1:
                return None  # No expiration
            elif result == -2:
                return -2  # Key doesn't exist
            return result
        except Exception as e:
            logger.error(f"[RedisCache] TTL error for {key}: {e}")
            return -2

    def setnx(self, key: str, value: Any, ttl: timedelta | None = None) -> bool:
        """Set value only if key does not exist."""
        try:
            serialized = self._serialize(value)
            if ttl:
                return bool(
                    self._redis.set(
                        self._make_key(key),
                        serialized,
                        nx=True,
                        ex=int(ttl.total_seconds()),
                    )
                )
            else:
                return bool(self._redis.setnx(self._make_key(key), serialized))
        except Exception as e:
            logger.error(f"[RedisCache] SetNX error for {key}: {e}")
            return False

    # =========================================================================
    # Distributed Locking
    # =========================================================================

    def get_lock(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: float | None = None,
    ) -> DistributedLock:
        """Get a distributed lock instance."""
        return RedisDistributedLock(
            redis_client=self._redis,
            name=name,
            timeout=timeout,
            blocking_timeout=blocking_timeout,
        )

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def mget(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values at once."""
        if not keys:
            return {}

        try:
            prefixed_keys = [self._make_key(k) for k in keys]
            values = self._redis.mget(prefixed_keys)

            result = {}
            for key, value in zip(keys, values):
                if value is not None:
                    result[key] = self._deserialize(value)
            return result
        except Exception as e:
            logger.error(f"[RedisCache] MGet error: {e}")
            return {}

    def mset(
        self,
        mapping: dict[str, Any],
        ttl: timedelta | None = None,
    ) -> bool:
        """Set multiple values at once."""
        if not mapping:
            return True

        try:
            prefixed_mapping = {self._make_key(k): self._serialize(v) for k, v in mapping.items()}

            # MSET doesn't support TTL, so we use pipeline
            if ttl:
                pipe = self._redis.pipeline()
                ttl_seconds = int(ttl.total_seconds())
                for key, value in prefixed_mapping.items():
                    pipe.set(key, value, ex=ttl_seconds)
                pipe.execute()
            else:
                self._redis.mset(prefixed_mapping)

            return True
        except Exception as e:
            logger.error(f"[RedisCache] MSet error: {e}")
            return False

    def mdelete(self, keys: list[str]) -> int:
        """Delete multiple keys at once."""
        if not keys:
            return 0

        try:
            prefixed_keys = [self._make_key(k) for k in keys]
            return self._redis.delete(*prefixed_keys)
        except Exception as e:
            logger.error(f"[RedisCache] MDelete error: {e}")
            return 0

    # =========================================================================
    # Hash Operations
    # =========================================================================

    def hget(self, name: str, key: str) -> Any | None:
        """Get a field from a hash."""
        try:
            data = self._redis.hget(self._make_key(name), key)
            if data is None:
                return None
            return self._deserialize(data)
        except Exception as e:
            logger.error(f"[RedisCache] HGet error for {name}:{key}: {e}")
            return None

    def hset(self, name: str, key: str, value: Any) -> bool:
        """Set a field in a hash."""
        try:
            serialized = self._serialize(value)
            self._redis.hset(self._make_key(name), key, serialized)
            return True
        except Exception as e:
            logger.error(f"[RedisCache] HSet error for {name}:{key}: {e}")
            return False

    def hgetall(self, name: str) -> dict[str, Any]:
        """Get all fields from a hash."""
        try:
            raw_data = self._redis.hgetall(self._make_key(name))
            result = {}
            for k, v in raw_data.items():
                if isinstance(k, bytes):
                    k = k.decode("utf-8")
                result[k] = self._deserialize(v)
            return result
        except Exception as e:
            logger.error(f"[RedisCache] HGetAll error for {name}: {e}")
            return {}

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return self._redis.ping()
        except Exception as e:
            logger.error(f"[RedisCache] Health check failed: {e}")
            return False

    def flush_all(self) -> bool:
        """Clear all keys with our prefix (not entire Redis DB)."""
        try:
            # Use SCAN to find keys with our prefix
            cursor = 0
            pattern = f"{self._key_prefix}*"
            deleted = 0

            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    deleted += self._redis.delete(*keys)
                if cursor == 0:
                    break

            logger.info(f"[RedisCache] Flushed {deleted} keys")
            return True
        except Exception as e:
            logger.error(f"[RedisCache] Flush error: {e}")
            return False

    # =========================================================================
    # Key Pattern Operations
    # =========================================================================

    def keys(self, pattern: str = "*") -> list[str]:
        """Find keys matching a pattern."""
        try:
            full_pattern = self._make_key(pattern)
            raw_keys = self._redis.keys(full_pattern)
            # Remove prefix from returned keys
            prefix_len = len(self._key_prefix)
            return [(k.decode("utf-8")[prefix_len:] if isinstance(k, bytes) else k[prefix_len:]) for k in raw_keys]
        except Exception as e:
            logger.error(f"[RedisCache] Keys error for {pattern}: {e}")
            return []

    def scan(
        self,
        pattern: str = "*",
        count: int = 100,
    ) -> tuple[int, list[str]]:
        """Incrementally iterate keys matching a pattern."""
        try:
            full_pattern = self._make_key(pattern)
            cursor, raw_keys = self._redis.scan(0, match=full_pattern, count=count)
            prefix_len = len(self._key_prefix)
            keys = [(k.decode("utf-8")[prefix_len:] if isinstance(k, bytes) else k[prefix_len:]) for k in raw_keys]
            return (cursor, keys)
        except Exception as e:
            logger.error(f"[RedisCache] Scan error for {pattern}: {e}")
            return (0, [])

    def reconnect(self) -> bool:
        """
        커넥션 풀 리셋 — 기존 dead 커넥션 해제 후 재연결.

        redis-py의 ConnectionPool.disconnect()는 풀 내 모든 커넥션을 닫는다.
        이후 ping() 호출 시 풀이 자동으로 새 커넥션을 생성한다.

        Returns:
            재연결 성공 여부
        """
        try:
            self._redis.connection_pool.disconnect()
            return self._redis.ping()
        except Exception as e:
            logger.error(f"[RedisCache] Reconnect failed: {e}")
            return False
