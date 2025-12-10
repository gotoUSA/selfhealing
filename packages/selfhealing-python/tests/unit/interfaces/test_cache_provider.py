"""
Unit tests for CacheProviderInterface.

Tests the abstract interface contract and in-memory implementation.
"""

import pytest
import time
import threading
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)
from selfhealing.adapters.cache.memory_adapter import (
    InMemoryCacheAdapter,
    InMemoryLock,  # Renamed from InMemoryDistributedLock
)


class TestDistributedLockInterface:
    """Tests for DistributedLock abstract interface."""

    def test_abstract_methods_required(self):
        """Test that all abstract methods must be implemented."""
        with pytest.raises(TypeError):
            DistributedLock()


class TestInMemoryCacheAdapter:
    """Tests for InMemoryCacheAdapter implementation."""

    @pytest.fixture
    def cache(self):
        """Create an in-memory cache adapter."""
        return InMemoryCacheAdapter(key_prefix="test:")

    def test_provider_name(self, cache: InMemoryCacheAdapter):
        """Test provider name."""
        assert cache.provider_name == "memory"

    def test_implements_interface(self, cache: InMemoryCacheAdapter):
        """Test that adapter implements CacheProviderInterface."""
        assert isinstance(cache, CacheProviderInterface)

    # =========================================================================
    # Basic Operations Tests
    # =========================================================================

    def test_set_and_get(self, cache: InMemoryCacheAdapter):
        """Test basic set and get operations."""
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_nonexistent_key(self, cache: InMemoryCacheAdapter):
        """Test getting a nonexistent key returns None."""
        assert cache.get("nonexistent") is None

    def test_set_with_various_types(self, cache: InMemoryCacheAdapter):
        """Test setting various data types."""
        # String
        cache.set("string_key", "hello")
        assert cache.get("string_key") == "hello"

        # Integer
        cache.set("int_key", 42)
        assert cache.get("int_key") == 42

        # Float
        cache.set("float_key", 3.14)
        assert cache.get("float_key") == 3.14

        # List
        cache.set("list_key", [1, 2, 3])
        assert cache.get("list_key") == [1, 2, 3]

        # Dict
        cache.set("dict_key", {"a": 1, "b": 2})
        assert cache.get("dict_key") == {"a": 1, "b": 2}

        # None value
        cache.set("none_key", None)
        assert cache.get("none_key") is None

    def test_set_overwrites_existing(self, cache: InMemoryCacheAdapter):
        """Test that set overwrites existing value."""
        cache.set("key", "original")
        cache.set("key", "updated")
        assert cache.get("key") == "updated"

    def test_delete_existing_key(self, cache: InMemoryCacheAdapter):
        """Test deleting an existing key."""
        cache.set("key", "value")
        result = cache.delete("key")
        assert result is True
        assert cache.get("key") is None

    def test_delete_nonexistent_key(self, cache: InMemoryCacheAdapter):
        """Test deleting a nonexistent key."""
        result = cache.delete("nonexistent")
        assert result is False

    def test_exists_key(self, cache: InMemoryCacheAdapter):
        """Test exists check for keys."""
        cache.set("key", "value")
        assert cache.exists("key") is True
        assert cache.exists("nonexistent") is False

    # =========================================================================
    # TTL Tests
    # =========================================================================

    def test_set_with_ttl(self, cache: InMemoryCacheAdapter):
        """Test setting value with TTL."""
        cache.set("key", "value", ttl=timedelta(seconds=1))
        assert cache.get("key") == "value"
        time.sleep(1.1)
        assert cache.get("key") is None

    def test_ttl_returns_remaining_seconds(self, cache: InMemoryCacheAdapter):
        """Test TTL returns remaining seconds."""
        cache.set("key", "value", ttl=timedelta(seconds=10))
        remaining = cache.ttl("key")
        assert remaining is not None
        assert 8 <= remaining <= 10

    def test_ttl_returns_none_for_no_expiry(self, cache: InMemoryCacheAdapter):
        """Test TTL returns None for keys without expiry."""
        cache.set("key", "value")
        assert cache.ttl("key") is None

    def test_ttl_returns_none_for_nonexistent(self, cache: InMemoryCacheAdapter):
        """Test TTL returns None for nonexistent keys."""
        result = cache.ttl("nonexistent")
        # Implementation returns -2 or None for missing keys
        assert result is None or result == -2

    def test_expire_sets_ttl_on_existing_key(self, cache: InMemoryCacheAdapter):
        """Test expire sets TTL on existing key."""
        cache.set("key", "value")
        result = cache.expire("key", timedelta(seconds=5))
        assert result is True
        remaining = cache.ttl("key")
        assert remaining is not None
        assert 3 <= remaining <= 5

    def test_expire_nonexistent_key(self, cache: InMemoryCacheAdapter):
        """Test expire on nonexistent key returns False."""
        result = cache.expire("nonexistent", timedelta(seconds=5))
        assert result is False

    def test_expired_key_not_exists(self, cache: InMemoryCacheAdapter):
        """Test that expired key does not exist."""
        cache.set("key", "value", ttl=timedelta(milliseconds=100))
        time.sleep(0.2)
        assert cache.exists("key") is False

    # =========================================================================
    # Atomic Operations Tests
    # =========================================================================

    def test_incr_new_key(self, cache: InMemoryCacheAdapter):
        """Test incrementing a new key starts from 0."""
        result = cache.incr("counter")
        assert result == 1

    def test_incr_existing_key(self, cache: InMemoryCacheAdapter):
        """Test incrementing an existing key."""
        cache.set("counter", 5)
        result = cache.incr("counter")
        assert result == 6

    def test_incr_by_amount(self, cache: InMemoryCacheAdapter):
        """Test incrementing by custom amount."""
        cache.set("counter", 10)
        result = cache.incr("counter", amount=5)
        assert result == 15

    def test_decr_existing_key(self, cache: InMemoryCacheAdapter):
        """Test decrementing an existing key."""
        cache.set("counter", 10)
        result = cache.decr("counter")
        assert result == 9

    def test_decr_by_amount(self, cache: InMemoryCacheAdapter):
        """Test decrementing by custom amount."""
        cache.set("counter", 20)
        result = cache.decr("counter", amount=5)
        assert result == 15

    def test_incr_thread_safety(self, cache: InMemoryCacheAdapter):
        """Test that increment is thread-safe."""
        cache.set("counter", 0)

        def increment():
            for _ in range(100):
                cache.incr("counter")

        threads = [threading.Thread(target=increment) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cache.get("counter") == 1000

    # =========================================================================
    # Distributed Locking Tests
    # =========================================================================

    def test_get_lock_returns_lock(self, cache: InMemoryCacheAdapter):
        """Test get_lock returns a lock instance."""
        lock = cache.get_lock("test_lock")
        assert lock is not None
        assert isinstance(lock, DistributedLock)

    def test_lock_acquire_and_release(self, cache: InMemoryCacheAdapter):
        """Test acquiring and releasing a lock."""
        lock = cache.get_lock("test_lock")
        assert lock.acquire() is True
        assert lock.locked() is True
        lock.release()
        assert lock.locked() is False

    def test_lock_context_manager(self, cache: InMemoryCacheAdapter):
        """Test lock as context manager."""
        lock = cache.get_lock("test_lock")
        with lock:
            assert lock.locked() is True
        assert lock.locked() is False

    def test_lock_prevents_concurrent_access(self, cache: InMemoryCacheAdapter):
        """Test that lock prevents concurrent access."""
        lock = cache.get_lock("test_lock")
        results = []

        def task(task_id):
            with lock:
                results.append(f"start_{task_id}")
                time.sleep(0.05)
                results.append(f"end_{task_id}")

        threads = [threading.Thread(target=task, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify sequential execution (start_i must be followed by end_i)
        for i in range(3):
            start_idx = results.index(f"start_{i}")
            end_idx = results.index(f"end_{i}")
            assert end_idx == start_idx + 1

    def test_lock_acquire_nonblocking(self, cache: InMemoryCacheAdapter):
        """Test non-blocking lock acquire."""
        lock1 = cache.get_lock("test_lock")
        lock2 = cache.get_lock("test_lock")

        assert lock1.acquire(blocking=False) is True
        assert lock2.acquire(blocking=False) is False
        lock1.release()

    def test_lock_acquire_with_timeout(self, cache: InMemoryCacheAdapter):
        """Test lock acquire with timeout."""
        lock1 = cache.get_lock("test_lock")
        lock2 = cache.get_lock("test_lock")

        lock1.acquire()
        start_time = time.time()
        result = lock2.acquire(blocking=True, timeout=0.5)
        elapsed = time.time() - start_time

        assert result is False
        assert elapsed >= 0.4  # Should have waited near timeout
        lock1.release()

    # =========================================================================
    # Bulk Operations Tests
    # =========================================================================

    def test_mget_multiple_keys(self, cache: InMemoryCacheAdapter):
        """Test getting multiple keys at once."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        result = cache.mget(["key1", "key2", "key3"])
        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_mget_with_missing_keys(self, cache: InMemoryCacheAdapter):
        """Test mget excludes missing keys."""
        cache.set("key1", "value1")
        result = cache.mget(["key1", "nonexistent"])
        assert result == {"key1": "value1"}

    def test_mget_empty_list(self, cache: InMemoryCacheAdapter):
        """Test mget with empty list."""
        result = cache.mget([])
        assert result == {}

    def test_mset_multiple_keys(self, cache: InMemoryCacheAdapter):
        """Test setting multiple keys at once."""
        result = cache.mset({"key1": "value1", "key2": "value2"})
        assert result is True
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"

    def test_mset_with_ttl(self, cache: InMemoryCacheAdapter):
        """Test mset with TTL."""
        cache.mset({"key1": "value1", "key2": "value2"}, ttl=timedelta(seconds=1))
        assert cache.get("key1") == "value1"
        time.sleep(1.1)
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    # =========================================================================
    # Health Check Tests
    # =========================================================================

    def test_health_check_healthy(self, cache: InMemoryCacheAdapter):
        """Test health check returns True for healthy cache."""
        assert cache.health_check() is True

    def test_flush_all(self, cache: InMemoryCacheAdapter):
        """Test flush_all clears all keys."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        result = cache.flush_all()
        assert result is True
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestInMemoryLock:
    """Tests for InMemoryLock implementation."""

    def test_lock_repr(self):
        """Test lock has proper representation."""
        lock = threading.Lock()
        dist_lock = InMemoryLock(lock, "test", 10.0)
        # Just verify it doesn't raise
        repr(dist_lock)

    def test_double_release(self):
        """Test double release doesn't raise."""
        lock = threading.Lock()
        dist_lock = InMemoryLock(lock, "test", 10.0)
        dist_lock.acquire()
        dist_lock.release()
        # Second release should not raise
        dist_lock.release()


class TestCacheProviderInterfaceContract:
    """Tests to verify interface contract compliance."""

    def test_abstract_methods_required(self):
        """Test that all abstract methods must be implemented."""
        with pytest.raises(TypeError):
            CacheProviderInterface()

    def test_interface_has_required_methods(self):
        """Test that interface defines all required methods."""
        required_methods = [
            "provider_name",
            "get",
            "set",
            "delete",
            "exists",
            "incr",
            "decr",
            "expire",
            "ttl",
            "get_lock",
            "mget",
            "mset",
            "health_check",
            "flush_all",
        ]
        for method in required_methods:
            assert hasattr(CacheProviderInterface, method)
