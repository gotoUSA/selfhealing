"""
Cache Provider Interface for Self-Healing System

Abstract interface for cache and distributed state management.
Supports distributed locking critical for circuit breakers.

Design Principles:
1. Pure Python - no framework dependencies
2. ABC for provider contracts
3. Context manager support for locks
4. Atomic operations for counters
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any


def generate_lock_owner_id() -> str:
    """Standard lock owner ID for all DistributedLock implementations."""
    return (
        f"{socket.gethostname()}:{os.getpid()}"
        f":{threading.get_ident()}:{uuid.uuid4().hex[:8]}"
    )


# ============================================================================
# Distributed Lock Interface
# ============================================================================


class DistributedLock(ABC):
    """
    Distributed lock interface for cross-process synchronization.

    Used by CircuitBreaker for state transitions and other
    critical sections that require mutual exclusion across
    multiple processes or servers.

    Supports context manager protocol for safe usage:

        with cache.get_lock("circuit_breaker:payment") as lock:
            # Critical section - only one process can execute
            circuit_breaker.transition_state()

    Implementations:
        - RedisDistributedLock (Redis-based)
        - InMemoryLock (for testing - single process only)
    """

    @abstractmethod
    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, block until lock is acquired
            timeout: Max seconds to wait (None = infinite)

        Returns:
            True if lock was acquired, False otherwise

        Note:
            If blocking=False and lock is held, returns False immediately.
            If blocking=True and timeout expires, returns False.
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """
        Release the lock.

        Raises:
            LockNotOwnedError: If lock is not held by current owner
        """
        pass

    @abstractmethod
    def locked(self) -> bool:
        """
        Check if lock is currently held by anyone.

        Returns:
            True if lock is held, False if available
        """
        pass

    @abstractmethod
    def owned(self) -> bool:
        """
        Check if lock is held by current owner.

        Returns:
            True if lock is held by this instance
        """
        pass

    def extend(self, additional_time: timedelta) -> bool:
        """
        Extend the lock's TTL.

        Args:
            additional_time: Time to add to current TTL

        Returns:
            True if extension was successful

        Note:
            Default implementation returns False (not supported).
            Override in implementations that support TTL extension.
        """
        return False

    def __enter__(self) -> DistributedLock:
        """Enter context manager, acquiring the lock."""
        acquired = self.acquire()
        if not acquired:
            raise LockAcquisitionError("Failed to acquire lock")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager, releasing the lock."""
        self.release()


class LockAcquisitionError(Exception):
    """Raised when lock acquisition fails."""

    pass


class LockNotOwnedError(Exception):
    """Raised when trying to release a lock not owned by current instance."""

    pass


# ============================================================================
# Cache Provider Interface
# ============================================================================


class CacheProviderInterface(ABC):
    """
    Abstract interface for cache/state storage.

    This interface abstracts cache operations including basic
    get/set, atomic counters, and distributed locking.

    Implementations:
        - RedisCacheAdapter (current - Redis)
        - InMemoryCacheAdapter (for testing)
        - MemcachedCacheAdapter (planned)
        - DynamoDBCacheAdapter (planned - AWS serverless)

    Example:
        >>> cache = ProviderRegistry.get_cache()
        >>>
        >>> # Basic operations
        >>> cache.set("key", "value", ttl=timedelta(minutes=5))
        >>> value = cache.get("key")
        >>>
        >>> # Atomic counter (for rate limiting)
        >>> count = cache.incr("request_count")
        >>> if count == 1:
        ...     cache.expire("request_count", timedelta(minutes=1))
        >>>
        >>> # Distributed locking
        >>> with cache.get_lock("payment:process") as lock:
        ...     process_payment()
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Return the provider name.

        Returns:
            Provider identifier (e.g., 'redis', 'memcached', 'memory')
        """
        pass

    # =========================================================================
    # Basic Operations
    # =========================================================================

    @abstractmethod
    def get(self, key: str) -> Any | None:
        """
        Get value by key.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        pass

    @abstractmethod
    def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None,
    ) -> bool:
        """
        Set value with optional TTL.

        Args:
            key: Cache key
            value: Value to cache (must be serializable)
            ttl: Time-to-live (None = no expiration)

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key existed and was deleted
        """
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key to check

        Returns:
            True if key exists and is not expired
        """
        pass

    def get_or_set(
        self,
        key: str,
        default_factory: callable,
        ttl: timedelta | None = None,
    ) -> Any:
        """
        Get value or compute and cache it if missing.

        Args:
            key: Cache key
            default_factory: Callable to compute value if missing
            ttl: Time-to-live for new value

        Returns:
            Cached or newly computed value
        """
        value = self.get(key)
        if value is None:
            value = default_factory()
            self.set(key, value, ttl)
        return value

    # =========================================================================
    # Atomic Operations (Critical for Circuit Breaker)
    # =========================================================================

    @abstractmethod
    def incr(self, key: str, amount: int = 1) -> int:
        """
        Atomically increment a counter.

        Args:
            key: Counter key
            amount: Increment amount (default 1)

        Returns:
            New counter value after increment

        Note:
            Creates key with value 0 if not exists, then increments.
            This is an atomic operation - safe for concurrent access.
        """
        pass

    @abstractmethod
    def decr(self, key: str, amount: int = 1) -> int:
        """
        Atomically decrement a counter.

        Args:
            key: Counter key
            amount: Decrement amount (default 1)

        Returns:
            New counter value after decrement
        """
        pass

    @abstractmethod
    def expire(self, key: str, ttl: timedelta) -> bool:
        """
        Set expiration on existing key.

        Args:
            key: Cache key
            ttl: Time-to-live duration

        Returns:
            True if key exists and expiration was set
        """
        pass

    @abstractmethod
    def ttl(self, key: str) -> int | None:
        """
        Get remaining TTL in seconds.

        Args:
            key: Cache key

        Returns:
            - Positive int: seconds until expiration
            - None: key has no expiration
            - -2: key does not exist
        """
        pass

    def setnx(self, key: str, value: Any, ttl: timedelta | None = None) -> bool:
        """
        Set value only if key does not exist (SET if Not eXists).

        Args:
            key: Cache key
            value: Value to set
            ttl: Optional time-to-live

        Returns:
            True if key was set (didn't exist), False otherwise
        """
        if not self.exists(key):
            return self.set(key, value, ttl)
        return False

    # =========================================================================
    # Distributed Locking
    # =========================================================================

    @abstractmethod
    def get_lock(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: float | None = None,
    ) -> DistributedLock:
        """
        Get a distributed lock instance.

        Args:
            name: Lock name (should be unique across application)
            timeout: Lock auto-release timeout (prevents deadlocks)
            blocking_timeout: Max time to wait when acquiring

        Returns:
            DistributedLock instance

        Example:
            >>> with cache.get_lock("circuit_breaker:payment") as lock:
            ...     # Critical section - only one process executes this
            ...     transition_circuit_breaker_state()

        Note:
            Always use locks with context manager to ensure release.
            The timeout parameter prevents deadlocks if a process
            crashes while holding the lock.
        """
        pass

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    @abstractmethod
    def mget(self, keys: list[str]) -> dict[str, Any]:
        """
        Get multiple values at once.

        Args:
            keys: List of cache keys

        Returns:
            Dict mapping keys to values (missing keys omitted)
        """
        pass

    @abstractmethod
    def mset(
        self,
        mapping: dict[str, Any],
        ttl: timedelta | None = None,
    ) -> bool:
        """
        Set multiple values at once.

        Args:
            mapping: Key-value pairs to set
            ttl: Optional TTL for all keys

        Returns:
            True if successful
        """
        pass

    def mdelete(self, keys: list[str]) -> int:
        """
        Delete multiple keys at once.

        Args:
            keys: List of cache keys to delete

        Returns:
            Number of keys that were deleted
        """
        deleted = 0
        for key in keys:
            if self.delete(key):
                deleted += 1
        return deleted

    # =========================================================================
    # Hash Operations (for structured data)
    # =========================================================================

    def hget(self, name: str, key: str) -> Any | None:
        """
        Get a field from a hash.

        Args:
            name: Hash name
            key: Field key within the hash

        Returns:
            Field value or None
        """
        hash_data = self.get(name)
        if isinstance(hash_data, dict):
            return hash_data.get(key)
        return None

    def hset(self, name: str, key: str, value: Any) -> bool:
        """
        Set a field in a hash.

        Args:
            name: Hash name
            key: Field key within the hash
            value: Field value

        Returns:
            True if successful
        """
        hash_data = self.get(name) or {}
        hash_data[key] = value
        return self.set(name, hash_data)

    def hgetall(self, name: str) -> dict[str, Any]:
        """
        Get all fields from a hash.

        Args:
            name: Hash name

        Returns:
            Dict of all fields and values
        """
        hash_data = self.get(name)
        return hash_data if isinstance(hash_data, dict) else {}

    # =========================================================================
    # Health Check
    # =========================================================================

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if cache backend is reachable.

        Returns:
            True if healthy and connected
        """
        pass

    @abstractmethod
    def flush_all(self) -> bool:
        """
        Clear all keys (USE WITH CAUTION - mainly for testing).

        Returns:
            True if successful

        Warning:
            This will delete ALL data in the cache. Only use
            in testing environments or with explicit confirmation.
        """
        pass

    def ping(self) -> bool:
        """
        Simple connectivity check.

        Returns:
            True if connection is alive
        """
        return self.health_check()

    # =========================================================================
    # Key Pattern Operations
    # =========================================================================

    def keys(self, pattern: str = "*") -> list[str]:
        """
        Find keys matching a pattern.

        Args:
            pattern: Glob-style pattern (e.g., "circuit_breaker:*")

        Returns:
            List of matching keys

        Warning:
            Use with caution in production - may be slow with many keys.
            Default implementation returns empty list.
        """
        return []

    def scan(
        self,
        pattern: str = "*",
        count: int = 100,
    ) -> tuple[int, list[str]]:
        """
        Incrementally iterate keys matching a pattern.

        Args:
            pattern: Glob-style pattern
            count: Approximate number of keys per iteration

        Returns:
            Tuple of (cursor, keys) - cursor 0 means scan complete

        Note:
            Default implementation returns (0, []).
            Override for implementations that support scanning.
        """
        return (0, [])
