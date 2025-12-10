"""
Cache Provider Interface for the self-healing system.

This module defines the abstract interface for cache/state storage operations,
allowing different implementations (Redis, Memcached, In-Memory, DynamoDB, etc.)
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, TypeVar
from datetime import timedelta
from contextlib import contextmanager

T = TypeVar("T")


class DistributedLock(ABC):
    """
    Distributed lock interface for cross-process synchronization.

    Used by CircuitBreaker for state transitions.
    """

    @abstractmethod
    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, block until lock acquired
            timeout: Max seconds to wait (None = infinite)

        Returns:
            True if lock acquired, False otherwise
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """Release the lock."""
        pass

    @abstractmethod
    def locked(self) -> bool:
        """Check if lock is currently held."""
        pass

    def __enter__(self) -> "DistributedLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class CacheProviderInterface(ABC):
    """
    Abstract interface for cache/state storage.

    Implementations:
        - RedisCacheAdapter (current)
        - MemcachedCacheAdapter (planned)
        - InMemoryCacheAdapter (for testing)
        - DynamoDBCacheAdapter (planned)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'redis', 'memcached')"""
        pass

    # =========================================================================
    # Basic Operations
    # =========================================================================

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
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
        ttl: Optional[timedelta] = None,
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
        """Check if key exists in cache."""
        pass

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
    def ttl(self, key: str) -> Optional[int]:
        """
        Get remaining TTL in seconds.

        Returns:
            Seconds until expiration, None if no TTL, -2 if key missing
        """
        pass

    # =========================================================================
    # Distributed Locking
    # =========================================================================

    @abstractmethod
    def get_lock(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: Optional[float] = None,
    ) -> DistributedLock:
        """
        Get a distributed lock instance.

        Args:
            name: Lock name (should be unique across application)
            timeout: Lock auto-release timeout
            blocking_timeout: Max time to wait when acquiring

        Returns:
            DistributedLock instance

        Example:
            with cache.get_lock("circuit_breaker:payment") as lock:
                # Critical section
                pass
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
        ttl: Optional[timedelta] = None,
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

    # =========================================================================
    # Health Check
    # =========================================================================

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if cache backend is reachable.

        Returns:
            True if healthy
        """
        pass

    @abstractmethod
    def flush_all(self) -> bool:
        """
        Clear all keys (USE WITH CAUTION - mainly for testing).

        Returns:
            True if successful
        """
        pass
