"""
Integration Tests for Resilient Storage Backend.

Tests with real Redis connection using Docker Compose.

Prerequisites:
- Docker Compose running with Redis:
  docker-compose up -d redis
  
Or use the test docker-compose file:
  docker-compose -f docker-compose.test.yml up -d

Reference: docs/self_healing/middleware_system/05_RESILIENT_STORAGE_BACKEND.md
"""

import os
import pytest
import tempfile
import time
from datetime import datetime, timezone


# Skip all tests if Redis is not available
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/15")  # Use DB 15 for tests


def redis_available():
    """Check if Redis is available."""
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not redis_available(),
    reason="Redis not available. Run: docker-compose up -d redis"
)


@pytest.fixture(scope="function")
def temp_wal_dir():
    """Create temporary WAL directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture(scope="function")
def real_backend(temp_wal_dir):
    """Create backend with real Redis connection."""
    from selfhealing.adapters.resilient.backend import (
        ResilientStorageBackend,
        ResilientStorageConfig,
        reset_storage_backend,
    )
    
    reset_storage_backend()
    
    config = ResilientStorageConfig(
        redis_url=REDIS_URL,
        wal_dir=temp_wal_dir,
        key_prefix="selfhealing_test:",
    )
    
    backend = ResilientStorageBackend(config)
    
    yield backend
    
    # Cleanup: flush test keys
    try:
        if backend._redis and backend._redis._redis:
            keys = backend._redis._redis.keys("selfhealing_test:*")
            if keys:
                backend._redis._redis.delete(*keys)
    except Exception:
        pass
    
    backend.close()
    reset_storage_backend()


@pytest.fixture(scope="function")
def cb_repo(real_backend):
    """Create Circuit Breaker repository with real backend."""
    from selfhealing.adapters.redis.circuit_breaker import (
        RedisCircuitBreakerStateRepository,
    )
    return RedisCircuitBreakerStateRepository(real_backend)


@pytest.fixture(scope="function")
def dlq_repo(real_backend):
    """Create DLQ repository with real backend."""
    from selfhealing.adapters.redis.dlq import RedisDLQRepository
    return RedisDLQRepository(real_backend)


@pytest.mark.integration
class TestResilientStorageIntegrationNormalFlow:
    """Normal flow integration tests with real Redis."""
    
    def test_backend_connects_to_redis(self, real_backend):
        """Backend successfully connects to Redis."""
        from selfhealing.adapters.resilient.backend import StorageMode
        
        assert real_backend.mode == StorageMode.REDIS
        assert real_backend.is_redis_available is True
        assert real_backend.is_degraded is False
    
    def test_set_get_round_trip(self, real_backend):
        """Set/get works correctly with real Redis."""
        test_data = {"key": "value", "number": 42}
        
        result = real_backend.set("test_key", test_data)
        assert result is True
        
        retrieved = real_backend.get("test_key")
        assert retrieved == test_data
    
    def test_hash_operations_round_trip(self, real_backend):
        """Hash operations work correctly with real Redis."""
        real_backend.hset("test_hash", {
            "field1": "value1",
            "field2": "value2",
            "count": "100",
        })
        
        # hgetall
        all_data = real_backend.hgetall("test_hash")
        assert all_data["field1"] == "value1"
        assert all_data["field2"] == "value2"
        assert all_data["count"] == "100"
        
        # hget
        value = real_backend.hget("test_hash", "field1")
        assert value == "value1"
    
    def test_sorted_set_operations(self, real_backend):
        """Sorted set operations work correctly."""
        # zadd
        real_backend.zadd("test_zset", {
            "member1": 1.0,
            "member2": 2.0,
            "member3": 3.0,
        })
        
        # zrange
        members = real_backend.zrange("test_zset", 0, -1)
        assert "member1" in members
        assert "member2" in members
        assert "member3" in members
        
        # zcard
        assert real_backend.zcard("test_zset") == 3
        
        # zrem
        real_backend.zrem("test_zset", "member2")
        assert real_backend.zcard("test_zset") == 2
    
    def test_list_operations(self, real_backend):
        """List operations work correctly."""
        # lpush
        real_backend.lpush("test_list", {"event": "first"})
        real_backend.lpush("test_list", {"event": "second"})
        real_backend.lpush("test_list", {"event": "third"})
        
        # lrange
        items = real_backend.lrange("test_list", 0, -1)
        assert len(items) == 3
        assert items[0]["event"] == "third"  # Most recent first
        
        # ltrim
        real_backend.ltrim("test_list", 0, 1)
        items = real_backend.lrange("test_list", 0, -1)
        assert len(items) == 2
    
    def test_incr_operation(self, real_backend):
        """Increment operation works correctly."""
        result1 = real_backend.incr("test_counter")
        assert result1 == 1
        
        result2 = real_backend.incr("test_counter")
        assert result2 == 2
        
        result3 = real_backend.incr("test_counter")
        assert result3 == 3
    
    def test_delete_operation(self, real_backend):
        """Delete operation works correctly."""
        real_backend.set("to_delete", "value")
        assert real_backend.get("to_delete") == "value"
        
        real_backend.delete("to_delete")
        assert real_backend.get("to_delete") is None


@pytest.mark.integration
class TestCircuitBreakerIntegration:
    """Circuit Breaker repository integration tests."""
    
    def test_end_to_end_circuit_breaker_flow(self, cb_repo):
        """Complete circuit breaker lifecycle."""
        service_name = f"test_service_{int(time.time())}"
        
        # 1. Create
        state = cb_repo.get_or_create(service_name)
        assert state.state == "closed"
        assert state.failure_count == 0
        
        # 2. Record failures
        cb_repo.increment_failure(service_name)
        cb_repo.increment_failure(service_name)
        cb_repo.increment_failure(service_name)
        
        # 3. Open circuit
        cb_repo.update_state(service_name, "open", failure_count=3)
        
        state = cb_repo.get_state(service_name)
        assert state.state == "open"
        assert state.failure_count == 3
        
        # 4. Half-open
        cb_repo.update_state(service_name, "half_open")
        state = cb_repo.get_state(service_name)
        assert state.state == "half_open"
        
        # 5. Success in half-open
        cb_repo.increment_success(service_name)
        
        # 6. Close circuit
        cb_repo.update_state(service_name, "closed", failure_count=0, success_count=0)
        
        state = cb_repo.get_state(service_name)
        assert state.state == "closed"
        
        # 7. Check history
        history = cb_repo.get_history(service_name)
        assert len(history) > 0
        
        # Cleanup
        cb_repo.delete_state(service_name)
    
    def test_multiple_services(self, cb_repo):
        """Multiple services can be managed independently."""
        timestamp = int(time.time())
        service1 = f"service1_{timestamp}"
        service2 = f"service2_{timestamp}"
        service3 = f"service3_{timestamp}"
        
        # Create multiple services
        cb_repo.get_or_create(service1)
        cb_repo.get_or_create(service2)
        cb_repo.get_or_create(service3)
        
        # Update differently
        cb_repo.update_state(service1, "open", failure_count=5)
        cb_repo.update_state(service2, "half_open", failure_count=2)
        # service3 stays closed
        
        # Verify independent
        assert cb_repo.get_state(service1).state == "open"
        assert cb_repo.get_state(service2).state == "half_open"
        assert cb_repo.get_state(service3).state == "closed"
        
        # Get all
        all_states = cb_repo.get_all_states()
        service_names = [s.service_name for s in all_states]
        
        assert service1 in service_names
        assert service2 in service_names
        assert service3 in service_names
        
        # Cleanup
        cb_repo.delete_state(service1)
        cb_repo.delete_state(service2)
        cb_repo.delete_state(service3)
    
    def test_manual_control(self, cb_repo):
        """Manual control works correctly."""
        service_name = f"manual_test_{int(time.time())}"
        
        cb_repo.get_or_create(service_name)
        
        cb_repo.set_manual_control(
            service_name,
            state="open",
            controlled_by_id=42,
            reason="Scheduled maintenance",
        )
        
        state = cb_repo.get_state(service_name)
        assert state.manually_controlled is True
        assert state.controlled_by_id == 42
        assert state.control_reason == "Scheduled maintenance"
        assert state.state == "open"
        
        # Clear control
        cb_repo.clear_manual_control(service_name)
        
        state = cb_repo.get_state(service_name)
        assert state.manually_controlled is False
        
        # Cleanup
        cb_repo.delete_state(service_name)


@pytest.mark.integration
class TestDLQIntegration:
    """DLQ repository integration tests."""
    
    def test_end_to_end_dlq_flow(self, dlq_repo):
        """Complete DLQ lifecycle."""
        # 1. Create entry
        entry = dlq_repo.create(
            domain="payment",
            failure_type="GATEWAY_TIMEOUT",
            error_message="Payment gateway timed out",
            error_code="PG001",
            entity_type="order",
            entity_id="ORD-12345",
            max_retries=3,
            snapshot_data={"amount": 10000, "currency": "KRW"},
        )
        
        entry_id = entry.id
        assert entry_id > 0
        
        # 2. Verify in pending
        pending = dlq_repo.get_pending(limit=100)
        pending_ids = [p.id for p in pending]
        assert entry_id in pending_ids
        
        # 3. Get entry details
        result = dlq_repo.get(entry_id)
        assert result.domain == "payment"
        assert result.failure_type == "GATEWAY_TIMEOUT"
        assert result.entity_type == "order"
        assert result.entity_id == "ORD-12345"
        assert result.status == "pending"
        
        # 4. Retry
        retry_count = dlq_repo.increment_retry(entry_id)
        assert retry_count == 1
        
        # 5. More retries
        dlq_repo.increment_retry(entry_id)
        dlq_repo.increment_retry(entry_id)
        
        # 6. Resolve
        dlq_repo.mark_resolved(
            entry_id,
            resolution_type="manual_intervention",
            resolution_note="Fixed by operator",
            resolved_by_id=1,
        )
        
        # 7. Verify resolved
        entry = dlq_repo.get(entry_id)
        assert entry.status == "resolved"
        assert entry.resolution_type == "manual_intervention"
        
        # 8. Not in pending anymore
        pending = dlq_repo.get_pending(limit=100)
        pending_ids = [p.id for p in pending]
        assert entry_id not in pending_ids
        
        # Cleanup
        dlq_repo.delete(entry_id)
    
    def test_get_by_domain(self, dlq_repo):
        """Filter by domain works correctly."""
        timestamp = int(time.time())
        
        # Create entries in different domains
        entry1 = dlq_repo.create(
            domain=f"payment_{timestamp}",
            failure_type="ERROR",
            error_message="Test 1"
        )
        entry2 = dlq_repo.create(
            domain=f"order_{timestamp}",
            failure_type="ERROR",
            error_message="Test 2"
        )
        entry3 = dlq_repo.create(
            domain=f"payment_{timestamp}",
            failure_type="ERROR",
            error_message="Test 3"
        )
        
        # Filter by domain
        payment_entries = dlq_repo.get_by_domain(f"payment_{timestamp}")
        
        assert len(payment_entries) == 2
        for entry in payment_entries:
            assert entry.domain == f"payment_{timestamp}"
        
        # Cleanup
        dlq_repo.delete(entry1.id)
        dlq_repo.delete(entry2.id)
        dlq_repo.delete(entry3.id)
    
    def test_retry_candidates(self, dlq_repo):
        """Get retry candidates works correctly."""
        # Create entry with retries remaining
        entry1 = dlq_repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Retryable",
            max_retries=5,
        )
        
        # Create entry with no retries remaining
        entry2 = dlq_repo.create(
            domain="test",
            failure_type="ERROR",
            error_message="Exhausted",
            max_retries=1,
        )
        dlq_repo.increment_retry(entry2.id)  # Use up the retry
        
        candidates = dlq_repo.get_retry_candidates()
        candidate_ids = [c.id for c in candidates]
        
        assert entry1.id in candidate_ids
        
        # Cleanup
        dlq_repo.delete(entry1.id)
        dlq_repo.delete(entry2.id)
    
    def test_count_pending(self, dlq_repo):
        """Count pending works correctly."""
        initial = dlq_repo.count_pending()
        
        entry1 = dlq_repo.create(domain="test", failure_type="ERROR", error_message="Test 1")
        entry2 = dlq_repo.create(domain="test", failure_type="ERROR", error_message="Test 2")
        
        assert dlq_repo.count_pending() == initial + 2
        
        # Resolve one
        dlq_repo.mark_resolved(entry1.id, "test")
        
        assert dlq_repo.count_pending() == initial + 1
        
        # Cleanup
        dlq_repo.delete(entry1.id)
        dlq_repo.delete(entry2.id)


@pytest.mark.integration
class TestDegradedModeAndRecovery:
    """Tests for degraded mode operation and recovery."""
    
    def test_degraded_mode_data_preserved(self, temp_wal_dir):
        """Data in degraded mode is preserved in memory and WAL."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageBackend,
            ResilientStorageConfig,
            StorageMode,
            reset_storage_backend,
        )
        from unittest.mock import patch
        
        reset_storage_backend()
        
        config = ResilientStorageConfig(
            redis_url="redis://nonexistent:6379/0",  # Invalid URL
            wal_dir=temp_wal_dir,
            allow_memory_only=True,
        )
        
        # Force Redis failure
        with patch('selfhealing.adapters.cache.redis_adapter.RedisCacheAdapter') as MockAdapter:
            MockAdapter.side_effect = Exception("Redis unavailable")
            backend = ResilientStorageBackend(config)
        
        assert backend.mode == StorageMode.DEGRADED
        
        # Store data
        backend.set("key1", {"data": "value1"})
        backend.hset("cb:service1", {"state": "open", "count": "5"})
        
        # Verify in memory
        assert "key1" in backend._memory
        assert "cb:service1" in backend._memory
        
        # Verify WAL has entries
        if backend._wal:
            backend.flush_wal()
            stats = backend._wal.get_stats()
            assert stats.last_sequence > 0
        
        backend.close()
        reset_storage_backend()
    
    def test_recovery_syncs_data_to_redis(self, real_backend):
        """Recovery syncs memory data to Redis."""
        from selfhealing.adapters.resilient.backend import StorageMode
        
        # Use unique test keys
        test_key = f"recovery_test_{int(time.time())}"
        cb_key = f"cb:recovery_test_{int(time.time())}"
        
        # Force degraded mode
        real_backend._mode = StorageMode.DEGRADED
        
        # Store data in memory (as hash since ResilientStorage uses hset for dicts)
        real_backend._memory[cb_key] = {"state": "open", "count": "10"}
        
        # Perform recovery
        result = real_backend._do_recovery()
        
        assert result is True
        assert real_backend.mode == StorageMode.REDIS
        
        # Verify circuit breaker data is in Redis
        cb_data = real_backend.hgetall(cb_key)
        assert cb_data["state"] == "open"
        
        # Memory should be cleared
        assert len(real_backend._memory) == 0


@pytest.mark.integration
class TestFactoryIntegration:
    """Tests for factory functions with real Redis."""
    
    def test_factory_get_storage_backend(self, temp_wal_dir):
        """Factory returns working storage backend."""
        from selfhealing.adapters.resilient.backend import (
            ResilientStorageConfig,
            reset_storage_backend,
            _storage_backend,
        )
        import selfhealing.adapters.resilient.backend as backend_module
        
        reset_storage_backend()
        
        # Set config via environment or directly
        original_config = getattr(backend_module, '_storage_backend', None)
        
        from selfhealing.factory import get_storage_backend
        
        backend = get_storage_backend()
        
        assert backend is not None
        
        # Should be singleton
        backend2 = get_storage_backend()
        assert backend is backend2
        
        reset_storage_backend()
    
    def test_factory_get_circuit_breaker_repo(self, temp_wal_dir):
        """Factory returns working CB repo."""
        from selfhealing.adapters.resilient.backend import reset_storage_backend
        
        reset_storage_backend()
        
        from selfhealing.factory import get_circuit_breaker_repo
        
        repo = get_circuit_breaker_repo()
        
        assert repo is not None
        
        # Basic operation should work
        service_name = f"factory_test_{int(time.time())}"
        state = repo.get_or_create(service_name)
        assert state.state == "closed"
        
        # Cleanup
        repo.delete_state(service_name)
        reset_storage_backend()
    
    def test_factory_get_dlq_repo(self, temp_wal_dir):
        """Factory returns working DLQ repo."""
        from selfhealing.adapters.resilient.backend import reset_storage_backend
        
        reset_storage_backend()
        
        from selfhealing.factory import get_dlq_repo
        
        repo = get_dlq_repo()
        
        assert repo is not None
        
        # Basic operation should work
        entry = repo.create(
            domain="factory_test",
            failure_type="TEST",
            error_message="Factory test"
        )
        assert entry.id > 0
        
        # Cleanup
        repo.delete(entry.id)
        reset_storage_backend()
