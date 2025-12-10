"""
In-Memory Cache Adapter for the self-healing system.

Implements CacheProviderInterface using in-memory storage.
Intended for testing and development environments.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from typing import Any, Optional

from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)

logger = logging.getLogger(__name__)


class InMemoryDistributedLock(DistributedLock):
    """
    In-memory distributed lock implementation.

    Uses threading.Lock for single-process synchronization.
    NOT suitable for multi-process/multi-server deployments.
    """

    def __init__(self, lock: threading.Lock, name: str, timeout: float):
        """
        Initialize the in-memory lock.

        Args:
            lock: threading.Lock instance
            name: Lock name for logging
            timeout: Lock auto-release timeout in seconds
        """
        self._lock = lock
        self._name = name
        self._timeout = timeout
        self._acquired = False
        self._acquired_at: Optional[float] = None

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """Acquire the lock."""
        try:
            if timeout is not None:
                result = self._lock.acquire(blocking=blocking, timeout=timeout)
            elif blocking:
                result = self._lock.acquire(blocking=True)
            else:
                result = self._lock.acquire(blocking=False)

            if result:
                self._acquired = True
                self._acquired_at = time.time()
            return result
        except Exception as e:
            logger.error(f"[InMemoryLock] Failed to acquire lock '{self._name}': {e}")
            return False

    def release(self) -> None:
        """Release the lock."""
        try:
            if self._acquired:
                self._lock.release()
                self._acquired = False
                self._acquired_at = None
        except RuntimeError:
            # Lock not held, ignore
            pass
        except Exception as e:
            logger.warning(f"[InMemoryLock] Failed to release lock '{self._name}': {e}")

    def locked(self) -> bool:
        """Check if lock is currently held."""
        return self._lock.locked()


class InMemoryCacheAdapter(CacheProviderInterface):
    """
    In-memory implementation of CacheProviderInterface.

    Stores data in a Python dictionary with TTL support.
    Uses threading.Lock for thread safety.

    WARNING: This adapter is intended for testing only.
    Data is lost when the process restarts.
    Does not support multi-process/multi-server deployments.
    """

    def __init__(self, key_prefix: str = "selfhealing:"):
        """
        Initialize the in-memory cache adapter.

        Args:
            key_prefix: Prefix for all cache keys (default: "selfhealing:")
        """
        self._key_prefix = key_prefix
        self._store: dict[str, tuple[Any, Optional[float]]] = {}  # key -> (value, expires_at)
        self._lock = threading.Lock()
        self._named_locks: dict[str, threading.Lock] = {}
        self._named_locks_lock = threading.Lock()

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    def _cleanup_expired(self) -> None:
        """Remove expired entries (called internally)."""
        now = time.time()
        expired_keys = [
            k for k, (_, expires_at) in self._store.items()
            if expires_at is not None and expires_at <= now
        ]
        for key in expired_keys:
            del self._store[key]

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "memory"

    # =========================================================================
    # Basic Operations
    # =========================================================================

    def get(self, key: str) -> Optional[Any]:
        """Get value by key."""
        with self._lock:
            prefixed_key = self._make_key(key)
            if prefixed_key not in self._store:
                return None

            value, expires_at = self._store[prefixed_key]

            # Check if expired
            if expires_at is not None and expires_at <= time.time():
                del self._store[prefixed_key]
                return None

            return value

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """Set value with optional TTL."""
        with self._lock:
            prefixed_key = self._make_key(key)
            expires_at = None
            if ttl:
                expires_at = time.time() + ttl.total_seconds()
            self._store[prefixed_key] = (value, expires_at)
            return True

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        with self._lock:
            prefixed_key = self._make_key(key)
            if prefixed_key in self._store:
                del self._store[prefixed_key]
                return True
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        with self._lock:
            prefixed_key = self._make_key(key)
            if prefixed_key not in self._store:
                return False

            _, expires_at = self._store[prefixed_key]

            # Check if expired
            if expires_at is not None and expires_at <= time.time():
                del self._store[prefixed_key]
                return False

            return True

    # =========================================================================
    # Atomic Operations
    # =========================================================================

    def incr(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter."""
        with self._lock:
            prefixed_key = self._make_key(key)

            if prefixed_key in self._store:
                value, expires_at = self._store[prefixed_key]
                # Check if expired
                if expires_at is not None and expires_at <= time.time():
                    value = 0
                    expires_at = None
            else:
                value = 0
                expires_at = None

            try:
                new_value = int(value) + amount
            except (ValueError, TypeError):
                new_value = amount

            self._store[prefixed_key] = (new_value, expires_at)
            return new_value

    def decr(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter."""
        return self.incr(key, -amount)

    def expire(self, key: str, ttl: timedelta) -> bool:
        """Set expiration on existing key."""
        with self._lock:
            prefixed_key = self._make_key(key)
            if prefixed_key not in self._store:
                return False

            value, _ = self._store[prefixed_key]
            expires_at = time.time() + ttl.total_seconds()
            self._store[prefixed_key] = (value, expires_at)
            return True

    def ttl(self, key: str) -> Optional[int]:
        """Get remaining TTL in seconds."""
        with self._lock:
            prefixed_key = self._make_key(key)
            if prefixed_key not in self._store:
                return -2

            _, expires_at = self._store[prefixed_key]

            if expires_at is None:
                return None

            remaining = int(expires_at - time.time())
            if remaining <= 0:
                del self._store[prefixed_key]
                return -2

            return remaining

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
        lock_key = self._make_key(f"lock:{name}")

        with self._named_locks_lock:
            if lock_key not in self._named_locks:
                self._named_locks[lock_key] = threading.Lock()
            lock = self._named_locks[lock_key]

        return InMemoryDistributedLock(
            lock=lock,
            name=name,
            timeout=timeout.total_seconds(),
        )

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def mget(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values at once."""
        result = {}
        for key in keys:
            value = self.get(key)
            if value is not None:
                result[key] = value
        return result

    def mset(
        self,
        mapping: dict[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """Set multiple values at once."""
        for key, value in mapping.items():
            self.set(key, value, ttl)
        return True

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if cache is operational (always True for in-memory)."""
        return True

    def flush_all(self) -> bool:
        """Clear all keys."""
        with self._lock:
            # Only clear keys with our prefix
            keys_to_delete = [
                k for k in self._store.keys()
                if k.startswith(self._key_prefix)
            ]
            for key in keys_to_delete:
                del self._store[key]
        return True

    def size(self) -> int:
        """Get number of entries in cache (for testing)."""
        with self._lock:
            self._cleanup_expired()
            return len(self._store)
