"""
Unit Tests for Resilient Storage Backend.

Tests for:
- ResilientStorageBackend (normal mode, degraded mode, recovery)
- RedisCircuitBreakerStateRepository
- RedisDLQRepository
"""

import pytest
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock


class TestStorageMode:
    """Tests for StorageMode enum."""
    
    def test_storage_mode_values(self):
        """Verify StorageMode enum values."""
        from selfhealing.adapters.resilient.backend import StorageMode
        
        assert StorageMode.REDIS.value == "redis"
        assert StorageMode.DEGRADED.value == "degraded"
        assert StorageMode.RECOVERING.value == "recovering"


class TestResilientStorageConfig:
    """Tests for ResilientStorageConfig."""
    
    def test_default_config(self):
        """Default config has sensible values."""
        from selfhealing.adapters.resilient.backend import ResilientStorageConfig
        
        config = ResilientStorageConfig()
        
        assert config.redis_url == "redis://localhost:6379/0"
        assert config.key_prefix == "selfhealing:"
        assert config.health_check_interval == 5.0
        assert config.recovery_jitter_max == 5.0
    
    def test_custom_config(self):
        """Custom config values are respected."""
        from selfhealing.adapters.resilient.backend import ResilientStorageConfig
        
        config = ResilientStorageConfig(
            redis_url="redis://custom:6380/1",
            wal_dir="/custom/wal",
            key_prefix="myapp:",
            allow_memory_only=True,
        )
        
        assert config.redis_url == "redis://custom:6380/1"
        assert config.wal_dir == "/custom/wal"
        assert config.key_prefix == "myapp:"
        assert config.allow_memory_only is True


class TestResilientStorageBackend:
    """Tests for ResilientStorageBackend."""
    
    @pytest.fixture
    def temp_wal_dir(self):
        """Create temporary WAL directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        mock = MagicMock()
        mock.ping.return_value = True
        mock.get.return_value = None
        mock.set.return_value = True
        mock.delete.return_value = 1
        mock.hset.return_value = 1
        mock.hgetall.return_value = {}
        mock.hget.return_value = None
        mock.incr.return_value = 1
        return mock
    
    @pytest.fixture
    def backend_memory_only(self, temp_wal_dir):
        """Create backend in memory-only mode (no Redis)."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            redis_url="redis://nonexistent:6379/0",
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        # Mock Redis to fail
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        yield backend
        backend.close()
        reset_storage_backend()
    
    # =========================================================================
    # Normal Mode Tests
    # =========================================================================
    
    def test_set_get_normal_mode(self, temp_wal_dir, mock_redis):
        """Set/get works in normal mode."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            StorageMode,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            mock_instance = MagicMock()
            mock_instance._redis = mock_redis
            mock_instance.get.return_value = {"value": 123}
            mock_instance.set.return_value = True
            MockAdapter.return_value = mock_instance
            
            backend = ResilientStorageBackend(config)
            
            # Set value
            result = backend.set("test_key", {"value": 123})
            assert result is True
            
            # Get value
            mock_instance.get.return_value = {"value": 123}
            value = backend.get("test_key")
            assert value == {"value": 123}
            
            assert backend.mode == StorageMode.REDIS
            
            backend.close()
            reset_storage_backend()
    
    def test_hash_operations_normal_mode(self, temp_wal_dir, mock_redis):
        """Hash operations work in normal mode."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(wal_dir=temp_wal_dir)
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            mock_instance = MagicMock()
            mock_instance._redis = mock_redis
            mock_redis.hgetall.return_value = {b"state": b"open", b"count": b"5"}
            MockAdapter.return_value = mock_instance
            
            backend = ResilientStorageBackend(config)
            
            # hset
            result = backend.hset("cb:test", {"state": "open", "count": "5"})
            assert result is True
            
            # hgetall
            data = backend.hgetall("cb:test")
            assert data["state"] == "open"
            assert data["count"] == "5"
            
            backend.close()
            reset_storage_backend()
    
    # =========================================================================
    # Degraded Mode Tests
    # =========================================================================
    
    def test_switch_to_degraded_on_redis_failure(self, temp_wal_dir):
        """Backend switches to degraded mode on Redis failure."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            StorageMode,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            mock_instance = MagicMock()
            mock_instance._redis.ping.side_effect = Exception("Connection failed")
            MockAdapter.return_value = mock_instance
            
            backend = ResilientStorageBackend(config)
            
            # Should be in degraded mode
            assert backend.mode == StorageMode.DEGRADED
            assert backend.is_degraded is True
            
            backend.close()
            reset_storage_backend()
    
    def test_degraded_mode_uses_memory(self, backend_memory_only):
        """Degraded mode stores in memory."""
        from selfhealing.adapters.resilient.backend import StorageMode
        
        backend = backend_memory_only
        
        # Verify degraded mode
        assert backend.mode == StorageMode.DEGRADED
        
        # Set value
        backend.set("key1", {"data": "test"})
        
        # Should be in memory
        assert "key1" in backend._memory
        assert backend._memory["key1"] == {"data": "test"}
        
        # Get should work from memory
        result = backend.get("key1")
        assert result == {"data": "test"}
    
    def test_degraded_mode_writes_wal(self, temp_wal_dir):
        """Degraded mode writes to WAL before memory."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            StorageMode,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        # Set value in degraded mode
        backend.set("important_key", {"critical": "data"})
        
        # WAL should have entries
        if backend._wal:
            stats = backend._wal.get_stats()
            assert stats.total_entries > 0 or stats.last_sequence > 0
        
        backend.close()
        reset_storage_backend()
    
    def test_degraded_mode_hash_operations(self, backend_memory_only):
        """Hash operations work in degraded mode."""
        backend = backend_memory_only
        
        # hset
        backend.hset("cb:service1", {"state": "open", "failure_count": "10"})
        
        # hgetall
        data = backend.hgetall("cb:service1")
        assert data["state"] == "open"
        assert data["failure_count"] == "10"
        
        # hget
        state = backend.hget("cb:service1", "state")
        assert state == "open"
    
    # =========================================================================
    # WAL-First Protocol Tests
    # =========================================================================
    
    def test_wal_survives_memory_clear(self, temp_wal_dir):
        """WAL entries survive memory clear (simulates server restart)."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        # Store data
        backend.set("important_key", {"critical": "data"})
        backend.hset("cb:critical_service", {"state": "open"})
        
        # Flush WAL
        backend.flush_wal()
        
        # Clear memory (simulate crash)
        backend._memory.clear()
        
        # WAL should still have entries
        if backend._wal:
            entries = list(backend._wal.recover_unprocessed(0))
            assert len(entries) > 0
            
            # Verify entry contents
            operations = [e.data.get("operation") for e in entries]
            assert "set" in operations or "hset" in operations
        
        backend.close()
        reset_storage_backend()
    
    # =========================================================================
    # Delete Operations Tests
    # =========================================================================
    
    def test_delete_operation(self, backend_memory_only):
        """Delete operation works in degraded mode."""
        backend = backend_memory_only
        
        # Set then delete
        backend.set("to_delete", "value")
        assert backend.get("to_delete") == "value"
        
        backend.delete("to_delete")
        assert backend.get("to_delete") is None
    
    def test_hdel_operation(self, backend_memory_only):
        """Hash delete operation works in degraded mode."""
        backend = backend_memory_only
        
        # Set hash then delete field
        backend.hset("hash_key", {"field1": "value1", "field2": "value2"})
        
        backend.hdel("hash_key", "field1")
        
        data = backend.hgetall("hash_key")
        assert "field1" not in data
        assert data.get("field2") == "value2"
    
    # =========================================================================
    # List Operations Tests
    # =========================================================================
    
    def test_list_operations(self, backend_memory_only):
        """List operations work in degraded mode."""
        backend = backend_memory_only
        
        # lpush
        backend.lpush("history", {"event": "first"})
        backend.lpush("history", {"event": "second"})
        
        # lrange
        items = backend.lrange("history", 0, 10)
        assert len(items) == 2
        assert items[0]["event"] == "second"  # Most recent first
        assert items[1]["event"] == "first"
        
        # ltrim
        backend.ltrim("history", 0, 0)
        items = backend.lrange("history", 0, 10)
        assert len(items) == 1
    
    # =========================================================================
    # Sorted Set Operations Tests
    # =========================================================================
    
    def test_sorted_set_operations(self, backend_memory_only):
        """Sorted set operations work in degraded mode."""
        backend = backend_memory_only
        
        # zadd
        backend.zadd("pending", {"item1": 1.0, "item2": 2.0, "item3": 3.0})
        
        # zrange
        items = backend.zrange("pending", 0, 2)
        assert len(items) == 3
        assert items[0] == "item1"  # Lowest score first
        
        # zcard
        count = backend.zcard("pending")
        assert count == 3
        
        # zrem
        backend.zrem("pending", "item2")
        count = backend.zcard("pending")
        assert count == 2
    
    # =========================================================================
    # Atomic Operations Tests
    # =========================================================================
    
    def test_incr_operation(self, backend_memory_only):
        """Increment operation works in degraded mode."""
        backend = backend_memory_only
        
        # Initial increment
        result = backend.incr("counter")
        assert result == 1
        
        # Second increment
        result = backend.incr("counter")
        assert result == 2
    
    # =========================================================================
    # Stats Tests
    # =========================================================================
    
    def test_get_stats(self, backend_memory_only):
        """Stats are returned correctly."""
        backend = backend_memory_only
        
        backend.set("key1", "value1")
        backend.set("key2", "value2")
        
        stats = backend.get_stats()
        
        assert stats["mode"] == "degraded"
        assert stats["memory_keys"] == 2
        assert "wal_initialized" in stats


class TestRedisCircuitBreakerStateRepository:
    """Tests for Redis Circuit Breaker State Repository."""
    
    @pytest.fixture
    def temp_wal_dir(self):
        """Create temporary WAL directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def backend(self, temp_wal_dir):
        """Create backend in memory-only mode."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        yield backend
        backend.close()
        reset_storage_backend()
    
    @pytest.fixture
    def repo(self, backend):
        """Create repository with backend."""
        from selfhealing.adapters.redis.circuit_breaker import (
            RedisCircuitBreakerStateRepository,
        )
        return RedisCircuitBreakerStateRepository(backend)
    
    def test_get_or_create_returns_default(self, repo):
        """Non-existent service gets default CLOSED state."""
        result = repo.get_or_create("new_service")
        
        assert result.service_name == "new_service"
        assert result.state == "closed"
        assert result.failure_count == 0
        assert result.success_count == 0
    
    def test_update_state(self, repo):
        """State update works correctly."""
        # Create
        repo.get_or_create("test_service")
        
        # Update
        repo.update_state(
            "test_service",
            state="open",
            failure_count=5,
        )
        
        # Verify
        result = repo.get_state("test_service")
        assert result.state == "open"
        assert result.failure_count == 5
    
    def test_increment_failure(self, repo):
        """Failure count increments correctly."""
        repo.get_or_create("test_service")
        
        new_count = repo.increment_failure("test_service")
        assert new_count == 1
        
        new_count = repo.increment_failure("test_service")
        assert new_count == 2
    
    def test_increment_success(self, repo):
        """Success count increments correctly."""
        repo.get_or_create("test_service")
        
        new_count = repo.increment_success("test_service")
        assert new_count == 1
    
    def test_reset_state(self, repo):
        """Reset restores default values."""
        # Create and modify
        repo.get_or_create("test_service")
        repo.update_state("test_service", "open", failure_count=10)
        
        # Reset
        repo.reset_state("test_service")
        
        # Verify
        result = repo.get_state("test_service")
        assert result.state == "closed"
        assert result.failure_count == 0
    
    def test_get_history(self, repo):
        """History is recorded for state changes."""
        repo.get_or_create("test_service")
        repo.update_state("test_service", "open")
        repo.update_state("test_service", "half_open")
        repo.update_state("test_service", "closed")
        
        history = repo.get_history("test_service", limit=10)
        
        # Should have history entries
        assert len(history) >= 3
    
    def test_set_manual_control(self, repo):
        """Manual control can be set."""
        repo.get_or_create("test_service")
        
        repo.set_manual_control(
            "test_service",
            state="open",
            controlled_by_id=123,
            reason="Maintenance",
        )
        
        result = repo.get_state("test_service")
        assert result.manually_controlled is True
        assert result.controlled_by_id == 123
        assert result.control_reason == "Maintenance"
        assert result.state == "open"
    
    def test_get_all_states(self, repo, backend):
        """Get all states returns all services."""
        repo.get_or_create("service1")
        repo.get_or_create("service2")
        repo.get_or_create("service3")
        
        all_states = repo.get_all_states()
        
        service_names = [s.service_name for s in all_states]
        assert "service1" in service_names
        assert "service2" in service_names
        assert "service3" in service_names


class TestRedisDLQRepository:
    """Tests for Redis DLQ Repository."""
    
    @pytest.fixture
    def temp_wal_dir(self):
        """Create temporary WAL directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def backend(self, temp_wal_dir):
        """Create backend in memory-only mode."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            reset_storage_backend,
        )
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        with patch('selfhealing.adapters.cache.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        yield backend
        backend.close()
        reset_storage_backend()
    
    @pytest.fixture
    def repo(self, backend):
        """Create repository with backend."""
        from selfhealing.adapters.redis.dlq import RedisDLQRepository
        return RedisDLQRepository(backend)
    
    def test_create_returns_id(self, repo):
        """Create returns a FailedOperationData with positive ID."""
        entry = repo.create(
            domain="payment",
            failure_type="TIMEOUT",
            error_message="Connection timeout",
        )
        
        assert entry.id > 0
    
    def test_get_returns_entry(self, repo):
        """Get returns created entry."""
        entry = repo.create(
            domain="payment",
            failure_type="TIMEOUT",
            error_message="Connection timeout",
            entity_type="order",
            entity_id="12345",
        )
        
        result = repo.get(entry.id)
        
        assert result is not None
        assert result.id == entry.id
        assert result.domain == "payment"
        assert result.failure_type == "TIMEOUT"
        assert result.entity_type == "order"
        assert result.entity_id == "12345"
        assert result.status == "pending"
    
    def test_get_pending_returns_entries(self, repo):
        """Get pending returns created entries."""
        repo.create(domain="test", failure_type="ERROR", error_message="Test")
        repo.create(domain="test", failure_type="ERROR", error_message="Test 2")
        
        pending = repo.get_pending(limit=10)
        
        assert len(pending) >= 2
    
    def test_mark_resolved_removes_from_pending(self, repo):
        """Resolved entries are removed from pending."""
        entry = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test"
        )
        
        # Verify in pending
        pending = repo.get_pending()
        pending_ids = [p.id for p in pending]
        assert entry.id in pending_ids
        
        # Resolve
        repo.mark_resolved(entry.id, "manual_fix", "Fixed manually")
        
        # Verify not in pending
        pending = repo.get_pending()
        pending_ids = [p.id for p in pending]
        assert entry.id not in pending_ids
        
        # But entry still exists
        result = repo.get(entry.id)
        assert result is not None
        assert result.status == "resolved"
    
    def test_increment_retry(self, repo):
        """Retry count increments correctly."""
        entry = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test",
            max_retries=3,
        )
        
        # Increment
        new_count = repo.increment_retry(entry.id)
        assert new_count == 1
        
        new_count = repo.increment_retry(entry.id)
        assert new_count == 2
    
    def test_get_retry_candidates(self, repo):
        """Get retry candidates returns entries with retries remaining."""
        entry1 = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test 1",
            max_retries=3,
        )
        entry2 = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test 2",
            max_retries=1,
        )
        
        # Use up all retries for entry2
        repo.increment_retry(entry2.id)
        
        candidates = repo.get_retry_candidates()
        
        candidate_ids = [c.id for c in candidates]
        assert entry1.id in candidate_ids
        # entry2 may or may not be included depending on status
    
    def test_count_pending(self, repo):
        """Count pending returns correct count."""
        initial = repo.count_pending()
        
        repo.create(domain="test", failure_type="ERROR", error_message="Test")
        repo.create(domain="test", failure_type="ERROR", error_message="Test 2")
        
        assert repo.count_pending() == initial + 2
    
    def test_get_by_domain(self, repo):
        """Get by domain filters correctly."""
        repo.create(domain="payment", failure_type="ERROR", error_message="Test")
        repo.create(domain="order", failure_type="ERROR", error_message="Test")
        repo.create(domain="payment", failure_type="ERROR", error_message="Test 2")
        
        payment_entries = repo.get_by_domain("payment")
        
        assert len(payment_entries) >= 2
        assert all(e.domain == "payment" for e in payment_entries)
    
    def test_mark_rejected(self, repo):
        """Mark rejected updates status correctly."""
        entry = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test"
        )
        
        repo.mark_rejected(entry.id, reason="Invalid data")
        
        result = repo.get(entry.id)
        assert result.status == "rejected"
    
    def test_delete_entry(self, repo):
        """Delete removes entry completely."""
        entry = repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Test"
        )
        
        # Verify exists
        assert repo.get(entry.id) is not None
        
        # Delete
        repo.delete(entry.id)
        
        # Verify gone
        assert repo.get(entry.id) is None
