"""
In-Memory Cache Adapter for Self-Healing System

Thread-safe in-memory implementation of CacheProviderInterface.
Designed for testing and development - NOT for production use.

Features:
    - Thread-safe operations with locks
    - TTL support with automatic expiration
    - Distributed lock simulation (single-process only)
    - Full interface compliance

Warning:
    This adapter is for TESTING ONLY. It does not persist data
    and locks are only effective within a single process.

Version: 6.5.0 - Drift Detection metrics moved to MetricsAwareCacheAdapter
"""

from __future__ import annotations

import fnmatch
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog

from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
    generate_lock_owner_id,
)

logger = structlog.get_logger()


@dataclass
class CacheEntry:
    """Internal cache entry with value and expiration."""

    value: Any
    expires_at: float | None = None  # Unix timestamp

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class InMemoryLock(DistributedLock):
    """
    In-memory distributed lock simulation.

    WARNING: Only works within a single process.
    For multi-process scenarios, use RedisDistributedLock.
    """

    # Class-level lock registry
    _locks: dict[str, InMemoryLock] = {}
    _registry_lock = threading.Lock()

    def __init__(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: float | None = None,
    ) -> None:
        """
        Initialize in-memory lock.

        Args:
            name: Lock name
            timeout: Lock auto-release timeout
            blocking_timeout: Max time to wait when acquiring
        """
        self._name = name
        self._timeout = timeout
        self._blocking_timeout = blocking_timeout
        self._owner_id = generate_lock_owner_id()
        self._lock = threading.Lock()
        self._acquired = False
        self._expires_at: float | None = None

    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, retry until acquired or timeout
            timeout: Override blocking_timeout

        Returns:
            True if lock was acquired
        """
        blocking_timeout = timeout if timeout is not None else self._blocking_timeout
        stop_time = None

        if blocking and blocking_timeout is not None:
            stop_time = time.time() + blocking_timeout

        while True:
            with InMemoryLock._registry_lock:
                # Check for existing lock
                existing = InMemoryLock._locks.get(self._name)

                if existing is None or existing._is_expired():
                    # Clean up expired lock
                    if existing is not None:
                        del InMemoryLock._locks[self._name]

                    # Acquire lock
                    self._expires_at = time.time() + self._timeout.total_seconds()
                    self._acquired = True
                    InMemoryLock._locks[self._name] = self
                    logger.debug(
                        "in_memory_lock.acquired_lock",
                        name=self._name,
                    )
                    return True

            if not blocking:
                return False

            if stop_time is not None and time.time() >= stop_time:
                logger.debug(
                    "in_memory_lock.timeout_acquiring_lock",
                    name=self._name,
                )
                return False

            time.sleep(0.01)  # Short sleep between retries

    def _is_expired(self) -> bool:
        """Check if lock has expired."""
        if self._expires_at is None:
            return True
        return time.time() > self._expires_at

    def release(self) -> None:
        """Release the lock."""
        with InMemoryLock._registry_lock:
            if self._name in InMemoryLock._locks:
                current = InMemoryLock._locks[self._name]
                if current._owner_id == self._owner_id:
                    del InMemoryLock._locks[self._name]
                    self._acquired = False
                    logger.debug(
                        "in_memory_lock.released_lock",
                        name=self._name,
                    )
                else:
                    logger.warning(
                        "in_memory_lock.lock_owned",
                        name=self._name,
                    )
            else:
                self._acquired = False

    def locked(self) -> bool:
        """Check if lock is currently held by anyone."""
        with InMemoryLock._registry_lock:
            existing = InMemoryLock._locks.get(self._name)
            if existing is None:
                return False
            if existing._is_expired():
                del InMemoryLock._locks[self._name]
                return False
            return True

    def owned(self) -> bool:
        """Check if lock is held by this instance."""
        with InMemoryLock._registry_lock:
            existing = InMemoryLock._locks.get(self._name)
            if existing is None:
                return False
            if existing._is_expired():
                del InMemoryLock._locks[self._name]
                return False
            return existing._owner_id == self._owner_id

    def extend(self, additional_time: timedelta) -> bool:
        """Extend the lock's TTL."""
        with InMemoryLock._registry_lock:
            if not self.owned():
                return False
            self._expires_at = time.time() + additional_time.total_seconds()
            return True

    @classmethod
    def clear_all_locks(cls) -> None:
        """Clear all locks (for testing cleanup)."""
        with cls._registry_lock:
            cls._locks.clear()


class InMemoryCacheAdapter(CacheProviderInterface):
    """
    Thread-safe in-memory cache implementation.

    This adapter provides a complete cache implementation using
    Python dictionaries with thread-safe operations.

    Features:
        - Full CacheProviderInterface compliance
        - Thread-safe operations
        - TTL support with lazy expiration
        - Mock distributed locks (single-process only)

    Example:
        >>> cache = InMemoryCacheAdapter()
        >>> cache.set("key", {"data": "value"}, ttl=timedelta(minutes=5))
        >>> data = cache.get("key")
        >>> with cache.get_lock("my_lock") as lock:
        ...     # Critical section (only within single process!)
        ...     pass

    Warning:
        This is for TESTING ONLY. Data is not persisted and
        locks only work within a single process.
    """

    def __init__(self, key_prefix: str = "test:", cache_name: str = "memory") -> None:
        """
        Initialize in-memory cache.

        Args:
            key_prefix: Prefix for all cache keys
            cache_name: Name for metrics identification
        """
        self._key_prefix = key_prefix
        self._cache_name = cache_name
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._healthy = True

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    def _cleanup_expired(self) -> None:
        """Remove expired entries (should be called periodically)."""
        current_time = time.time()
        expired_keys = [
            k
            for k, v in self._store.items()
            if v.expires_at is not None and v.expires_at < current_time
        ]
        for key in expired_keys:
            del self._store[key]

    @property
    def provider_name(self) -> str:
        """Return 'memory' as the provider identifier."""
        return "memory"

    # =========================================================================
    # Basic Operations
    # =========================================================================

    def get(self, key: str) -> Any | None:
        """Get value by key."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is None:
                return None

            if entry.is_expired():
                del self._store[full_key]
                return None

            return entry.value

    def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None,
    ) -> bool:
        """Set value with optional TTL."""
        with self._lock:
            full_key = self._make_key(key)
            expires_at = None
            if ttl is not None:
                expires_at = time.time() + ttl.total_seconds()

            self._store[full_key] = CacheEntry(value=value, expires_at=expires_at)
            return True

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        with self._lock:
            full_key = self._make_key(key)
            if full_key in self._store:
                del self._store[full_key]
                return True
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is None:
                return False

            if entry.is_expired():
                del self._store[full_key]
                return False

            return True

    # =========================================================================
    # Atomic Operations
    # =========================================================================

    def incr(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is None or entry.is_expired():
                # Create new counter
                self._store[full_key] = CacheEntry(value=amount)
                return amount

            # Increment existing
            new_value = int(entry.value) + amount
            entry.value = new_value
            return new_value

    def decr(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter."""
        return self.incr(key, -amount)

    def expire(self, key: str, ttl: timedelta) -> bool:
        """Set expiration on existing key."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is None or entry.is_expired():
                return False

            entry.expires_at = time.time() + ttl.total_seconds()
            return True

    def ttl(self, key: str) -> int | None:
        """Get remaining TTL in seconds."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is None:
                return -2  # Key doesn't exist

            if entry.is_expired():
                del self._store[full_key]
                return -2

            if entry.expires_at is None:
                return None  # No expiration

            remaining = entry.expires_at - time.time()
            return max(0, int(remaining))

    def setnx(self, key: str, value: Any, ttl: timedelta | None = None) -> bool:
        """Set value only if key does not exist."""
        with self._lock:
            full_key = self._make_key(key)
            entry = self._store.get(full_key)

            if entry is not None and not entry.is_expired():
                return False

            expires_at = None
            if ttl is not None:
                expires_at = time.time() + ttl.total_seconds()

            self._store[full_key] = CacheEntry(value=value, expires_at=expires_at)
            return True

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
        return InMemoryLock(
            name=name,
            timeout=timeout,
            blocking_timeout=blocking_timeout,
        )

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def mget(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values at once."""
        result = {}
        with self._lock:
            for key in keys:
                full_key = self._make_key(key)
                entry = self._store.get(full_key)
                if entry is not None and not entry.is_expired():
                    result[key] = entry.value
        return result

    def mset(
        self,
        mapping: dict[str, Any],
        ttl: timedelta | None = None,
    ) -> bool:
        """Set multiple values at once."""
        with self._lock:
            expires_at = None
            if ttl is not None:
                expires_at = time.time() + ttl.total_seconds()

            for key, value in mapping.items():
                full_key = self._make_key(key)
                self._store[full_key] = CacheEntry(value=value, expires_at=expires_at)
        return True

    def mdelete(self, keys: list[str]) -> int:
        """Delete multiple keys at once."""
        deleted = 0
        with self._lock:
            for key in keys:
                full_key = self._make_key(key)
                if full_key in self._store:
                    del self._store[full_key]
                    deleted += 1
        return deleted

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if cache is healthy."""
        return self._healthy

    def set_health_status(self, healthy: bool) -> None:
        """Set health status for testing."""
        self._healthy = healthy

    def flush_all(self) -> bool:
        """Clear all keys."""
        with self._lock:
            # Only clear keys with our prefix
            keys_to_delete = [
                k for k in self._store.keys() if k.startswith(self._key_prefix)
            ]
            for key in keys_to_delete:
                del self._store[key]
            logger.info(
                "in_memory_cache.flushed_keys",
                keys_to_delete_count=len(keys_to_delete),
            )

        # Also clear locks
        InMemoryLock.clear_all_locks()
        return True

    # =========================================================================
    # Key Pattern Operations
    # =========================================================================

    def keys(self, pattern: str = "*") -> list[str]:
        """Find keys matching a pattern."""
        with self._lock:
            self._cleanup_expired()
            full_pattern = self._make_key(pattern)
            prefix_len = len(self._key_prefix)

            matching = []
            for key in self._store.keys():
                if fnmatch.fnmatch(key, full_pattern):
                    matching.append(key[prefix_len:])

            return matching

    def scan(
        self,
        pattern: str = "*",
        count: int = 100,
    ) -> tuple[int, list[str]]:
        """Incrementally iterate keys matching a pattern."""
        # In-memory implementation just returns all matching keys
        return (0, self.keys(pattern)[:count])

    # =========================================================================
    # Testing Utilities
    # =========================================================================

    def get_store_size(self) -> int:
        """Get number of entries in store (for testing)."""
        with self._lock:
            self._cleanup_expired()
            return len(self._store)

    def clear_all(self) -> None:
        """Clear entire store including all prefixes (for testing cleanup)."""
        with self._lock:
            self._store.clear()
        InMemoryLock.clear_all_locks()
