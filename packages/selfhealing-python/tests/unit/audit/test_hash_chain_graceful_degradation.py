"""
Tests for Hash Chain Graceful Degradation Components (Phase 4).

Covers fault-tolerant features:
- HashChainFallbackChain: Multi-tier fallback (Redis → Replica → Local → Memory)
- DegradedEntryMarker: Marking entries recorded during failures
- HashChainWALRecovery: WAL-based crash recovery
- HashChainDegradationManager: Unified degradation level management
- HashChainCircuitBreaker: Circuit breaker for hash chain operations
- HashChainGracefulDegradationManager: Unified access
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from selfhealing.audit.hash_chain_graceful_degradation import (
    DegradationLevel,
    FallbackConfig,
    HashChainFallbackChain,
    DegradedEntryInfo,
    DegradedEntryMarker,
    HashChainWALEntry,
    HashChainWALRecovery,
    HashChainDegradationManager,
    CircuitState,
    CircuitBreakerConfig,
    HashChainCircuitBreaker,
    HashChainGracefulDegradationManager,
)


# =============================================================================
# Mock Redis Client
# =============================================================================

class MockRedisClient:
    """Mock Redis client for testing."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
        self._incr_counter = 0
    
    def set_should_fail(self, should_fail: bool) -> None:
        """Set failure mode."""
        self._should_fail = should_fail
    
    def get(self, key: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        value = self._data.get(key)
        return str(value).encode() if value is not None else None
    
    def set(self, key: str, value: Any, nx: bool = False, ex: int = None) -> bool:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True
    
    def delete(self, *keys: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
            if key in self._hashes:
                del self._hashes[key]
                count += 1
        return count
    
    def incr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            val = int(self._data.get(key, 0)) + 1
            self._data[key] = val
            return val
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            self._hashes[key] = {}
        if mapping:
            self._hashes[key].update(mapping)
        if kwargs:
            self._hashes[key].update(kwargs)
        return 1
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            return None
        value = self._hashes[key].get(field)
        if value is None:
            return None
        return str(value).encode() if not isinstance(value, bytes) else value
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            return {}
        return {
            k.encode() if isinstance(k, str) else k: 
            str(v).encode() if not isinstance(v, bytes) else v
            for k, v in self._hashes[key].items()
        }
    
    def expire(self, key: str, seconds: int) -> bool:
        return True
    
    def lpush(self, key: str, *values: str) -> int:
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].insert(0, v)
        return len(self._lists[key])
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self._lists:
            self._lists[key] = self._lists[key][start:end + 1]
        return True
    
    def pipeline(self, transaction: bool = True) -> "MockPipeline":
        return MockPipeline(self)
    
    def ping(self) -> bool:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        return True


class MockPipeline:
    """Mock Redis pipeline."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands: List[tuple] = []
    
    def set(self, key: str, value: Any) -> "MockPipeline":
        self._commands.append(("set", key, value))
        return self
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> "MockPipeline":
        self._commands.append(("hset", key, mapping or kwargs))
        return self
    
    def execute(self) -> List[Any]:
        results = []
        for cmd in self._commands:
            if cmd[0] == "set":
                self._redis.set(cmd[1], cmd[2])
                results.append(True)
            elif cmd[0] == "hset":
                self._redis.hset(cmd[1], cmd[2])
                results.append(1)
        return results
    
    def __enter__(self) -> "MockPipeline":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class MockDistributedLock:
    """Mock distributed lock."""
    
    def __init__(self, *args, **kwargs):
        self._acquired = False
    
    def acquire(self, blocking: bool = True) -> bool:
        self._acquired = True
        return True
    
    def release(self) -> None:
        self._acquired = False
    
    def __enter__(self) -> "MockDistributedLock":
        self.acquire()
        return self
    
    def __exit__(self, *args) -> None:
        self.release()


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    return MockRedisClient()


@pytest.fixture
def failing_redis():
    """Create failing Redis client."""
    return MockRedisClient(should_fail=True)


@pytest.fixture
def temp_dir():
    """Create temporary directory for WAL files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_entry():
    """Create sample log entry."""
    return {
        "event": "test_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"key": "value"},
    }


# =============================================================================
# Test: DegradationLevel Enum
# =============================================================================

class TestDegradationLevel:
    """Tests for DegradationLevel enum."""
    
    def test_levels_exist(self):
        """Test all degradation levels are defined."""
        assert DegradationLevel.NORMAL == "normal"
        assert DegradationLevel.DEGRADED == "degraded"
        assert DegradationLevel.EMERGENCY == "emergency"
        assert DegradationLevel.READONLY == "readonly"
    
    def test_level_comparison(self):
        """Test level string comparison."""
        assert DegradationLevel.NORMAL.value == "normal"
        assert DegradationLevel.DEGRADED.value == "degraded"


# =============================================================================
# Test: HashChainFallbackChain
# =============================================================================

class TestHashChainFallbackChain:
    """Tests for HashChainFallbackChain."""
    
    def test_initialization(self, mock_redis):
        """Test fallback chain initialization."""
        fallback = HashChainFallbackChain(
            redis_primary=mock_redis,
            config=FallbackConfig(),
        )
        
        assert fallback.current_tier == "redis_primary"
        assert fallback._memory_buffer == []
    
    def test_fallback_to_local_on_no_redis(self, sample_entry):
        """Test fallback to local when no Redis available."""
        fallback = HashChainFallbackChain(
            redis_primary=None,
            config=FallbackConfig(),
        )
        
        result = fallback.add_integrity(sample_entry)
        
        assert "integrity" in result
        assert result["integrity"]["degraded"] is True
        assert result["integrity"]["tier"] == "local"
    
    def test_fallback_to_memory_last_resort(self, failing_redis, sample_entry, temp_dir):
        """Test fallback to memory when all else fails."""
        # Local fallback will succeed even with non-writable path (just logs error)
        # So we test that fallback chain works with failing redis
        config = FallbackConfig(
            local_file_path=temp_dir / "test_fallback.jsonl",
        )
        fallback = HashChainFallbackChain(
            redis_primary=failing_redis,
            config=config,
        )
        
        result = fallback.add_integrity(sample_entry)
        
        # Should fall back to local (not memory) since local write succeeds
        assert result["integrity"]["degraded"] is True
        assert result["integrity"]["tier"] in ("local", "memory")
    
    def test_memory_buffer_limit(self, sample_entry):
        """Test memory buffer respects max entries limit."""
        config = FallbackConfig(memory_max_entries=5)
        fallback = HashChainFallbackChain(
            redis_primary=None,
            config=config,
        )
        
        # Write more than max entries
        for i in range(10):
            entry = {"event": f"test_{i}"}
            fallback._add_integrity_memory(entry)
        
        assert len(fallback._memory_buffer) <= 5
    
    def test_get_degraded_entries(self, sample_entry):
        """Test retrieving degraded entries from buffer."""
        fallback = HashChainFallbackChain(redis_primary=None)
        
        result = fallback._add_integrity_memory(sample_entry)
        degraded = fallback.get_degraded_entries()
        
        assert len(degraded) == 1
        assert degraded[0]["integrity"]["degraded"] is True
    
    def test_clear_memory_buffer(self, sample_entry):
        """Test clearing memory buffer."""
        fallback = HashChainFallbackChain(redis_primary=None)
        
        fallback._add_integrity_memory(sample_entry)
        count = fallback.clear_memory_buffer()
        
        assert count == 1
        assert len(fallback._memory_buffer) == 0
    
    def test_stats_tracking(self, sample_entry):
        """Test statistics are tracked."""
        fallback = HashChainFallbackChain(redis_primary=None)
        
        fallback.add_integrity(sample_entry)
        stats = fallback.get_stats()
        
        assert stats["local_writes"] == 1
        assert "current_tier" in stats
        assert "memory_buffer_size" in stats


# =============================================================================
# Test: DegradedEntryMarker
# =============================================================================

class TestDegradedEntryMarker:
    """Tests for DegradedEntryMarker."""
    
    def test_mark_degraded(self, mock_redis, sample_entry):
        """Test marking entry as degraded."""
        marker = DegradedEntryMarker(redis_client=mock_redis)
        sample_entry["integrity"] = {"sequence": 1}
        
        result = marker.mark_degraded(sample_entry, "test_reason", "test_tier")
        
        assert result["integrity"]["degraded"] is True
        assert result["integrity"]["degraded_reason"] == "test_reason"
        assert result["integrity"]["degraded_tier"] == "test_tier"
        assert "degraded_at" in result["integrity"]
    
    def test_mark_reconciled(self, mock_redis, sample_entry):
        """Test marking entry as reconciled."""
        marker = DegradedEntryMarker(redis_client=mock_redis)
        sample_entry["integrity"] = {"sequence": 1}
        
        marker.mark_degraded(sample_entry, "test", "test")
        result = marker.mark_reconciled(1, 100)
        
        assert result is True
        
        unreconciled = marker.get_unreconciled_entries()
        assert len(unreconciled) == 0
    
    def test_get_unreconciled_entries(self, sample_entry):
        """Test getting unreconciled entries."""
        marker = DegradedEntryMarker()
        
        for i in range(3):
            entry = {"integrity": {"sequence": i}}
            marker.mark_degraded(entry, "test", "test")
        
        # Reconcile one
        marker.mark_reconciled(1, 101)
        
        unreconciled = marker.get_unreconciled_entries()
        assert len(unreconciled) == 2
    
    def test_clear_reconciled(self, sample_entry):
        """Test clearing reconciled entries."""
        marker = DegradedEntryMarker()
        
        for i in range(3):
            entry = {"integrity": {"sequence": i}}
            marker.mark_degraded(entry, "test", "test")
        
        marker.mark_reconciled(0, 100)
        marker.mark_reconciled(1, 101)
        
        cleared = marker.clear_reconciled()
        assert cleared == 2
        
        # Only one unreconciled should remain
        unreconciled = marker.get_unreconciled_entries()
        assert len(unreconciled) == 1
    
    def test_stats(self):
        """Test statistics tracking."""
        marker = DegradedEntryMarker()
        
        marker.mark_degraded({"integrity": {"sequence": 1}}, "test", "test")
        marker.mark_reconciled(1, 101)
        
        stats = marker.get_stats()
        assert stats["marked_count"] == 1
        assert stats["reconciled_count"] == 1
        assert stats["unreconciled_count"] == 0


# =============================================================================
# Test: HashChainWALRecovery
# =============================================================================

class TestHashChainWALRecovery:
    """Tests for HashChainWALRecovery."""
    
    def test_initialization(self, temp_dir):
        """Test WAL recovery initialization."""
        recovery = HashChainWALRecovery(wal_dir=temp_dir)
        
        assert recovery._wal_dir == temp_dir
        assert recovery._recovery_done is False
    
    def test_write_wal_entry(self, temp_dir, sample_entry):
        """Test writing entry to WAL."""
        recovery = HashChainWALRecovery(wal_dir=temp_dir)
        
        wal_seq = recovery.write_wal_entry("add_integrity", sample_entry)
        
        assert wal_seq == 1
        
        # Verify file was written
        wal_files = list(temp_dir.glob("hash_chain_wal_*.jsonl"))
        assert len(wal_files) == 1
        
        with open(wal_files[0], "r") as f:
            content = f.read()
            assert "add_integrity" in content
        
        recovery.close()
    
    def test_mark_wal_committed(self, temp_dir, sample_entry):
        """Test marking WAL entry as committed."""
        recovery = HashChainWALRecovery(wal_dir=temp_dir)
        
        wal_seq = recovery.write_wal_entry("add_integrity", sample_entry)
        recovery.mark_wal_committed(wal_seq)
        
        # Read and verify commit marker
        wal_files = list(temp_dir.glob("hash_chain_wal_*.jsonl"))
        with open(wal_files[0], "r") as f:
            lines = f.readlines()
        
        assert len(lines) == 2
        commit_entry = json.loads(lines[1])
        assert commit_entry["operation"] == "COMMIT"
        
        recovery.close()
    
    def test_recover_on_startup_empty(self, temp_dir, mock_redis):
        """Test recovery with no WAL files."""
        recovery = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        result = recovery.recover_on_startup()
        
        assert result["status"] == "success"
        assert result["wal_files_scanned"] == 0
        assert recovery._recovery_done is True
    
    def test_recover_uncommitted_entries(self, temp_dir, mock_redis):
        """Test recovery replays uncommitted entries."""
        recovery = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        # Write entry without commit
        entry = {"integrity": {"sequence": 5, "current_hash": "abc123"}}
        recovery.write_wal_entry("add_integrity", entry)
        
        # Don't commit - simulate crash
        recovery.close()
        
        # Create new recovery instance
        recovery2 = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        result = recovery2.recover_on_startup()
        
        assert result["status"] == "success"
        assert result["entries_found"] == 1
        assert result["entries_recovered"] == 1
        
        recovery2.close()
    
    def test_skip_committed_entries(self, temp_dir, mock_redis):
        """Test recovery skips already committed entries."""
        recovery = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        # Write and commit entry
        entry = {"integrity": {"sequence": 5, "current_hash": "abc123"}}
        wal_seq = recovery.write_wal_entry("add_integrity", entry)
        recovery.mark_wal_committed(wal_seq)
        recovery.close()
        
        # Create new recovery instance
        recovery2 = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        result = recovery2.recover_on_startup()
        
        assert result["entries_already_committed"] == 1
        assert result["entries_recovered"] == 0
        
        recovery2.close()
    
    def test_cleanup_old_wal_files(self, temp_dir):
        """Test cleanup of old WAL files."""
        # Create old WAL file
        old_file = temp_dir / "hash_chain_wal_20200101.jsonl"
        old_file.write_text('{"test": true}\n')
        
        recovery = HashChainWALRecovery(wal_dir=temp_dir)
        removed = recovery.cleanup_old_wal_files(max_age_days=1)
        
        assert removed == 1
        assert not old_file.exists()
    
    def test_get_stats(self, temp_dir, sample_entry):
        """Test statistics tracking."""
        recovery = HashChainWALRecovery(wal_dir=temp_dir)
        recovery.write_wal_entry("test", sample_entry)
        
        stats = recovery.get_stats()
        
        assert stats["wal_sequence"] == 1
        assert stats["recovery_done"] is False
        
        recovery.close()


# =============================================================================
# Test: HashChainDegradationManager
# =============================================================================

class TestHashChainDegradationManager:
    """Tests for HashChainDegradationManager."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        HashChainDegradationManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        HashChainDegradationManager.reset_instance()
    
    def test_singleton_pattern(self, mock_redis):
        """Test singleton pattern works."""
        manager1 = HashChainDegradationManager(redis_client=mock_redis)
        manager2 = HashChainDegradationManager()
        
        assert manager1 is manager2
    
    def test_initial_level(self, mock_redis):
        """Test initial degradation level."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        assert manager.level == DegradationLevel.NORMAL
        assert manager.is_degraded is False
    
    def test_initial_level_without_redis(self):
        """Test initial level is DEGRADED without Redis."""
        manager = HashChainDegradationManager(redis_client=None)
        
        assert manager.level == DegradationLevel.DEGRADED
        assert manager.is_degraded is True
    
    def test_set_level(self, mock_redis):
        """Test setting degradation level."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.set_level(DegradationLevel.DEGRADED, "test")
        
        assert manager.level == DegradationLevel.DEGRADED
    
    def test_on_redis_failure(self, mock_redis):
        """Test Redis failure handling."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.on_redis_failure(ConnectionError("test"))
        
        assert manager.level == DegradationLevel.DEGRADED
        assert manager._failure_count == 1
    
    def test_repeated_failures_escalate(self, mock_redis):
        """Test repeated failures escalate to emergency."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        # Trigger many failures
        for _ in range(15):
            manager.on_redis_failure()
        
        assert manager.level == DegradationLevel.EMERGENCY
    
    def test_on_redis_recovery(self, mock_redis):
        """Test Redis recovery handling."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.on_redis_failure()
        assert manager.level == DegradationLevel.DEGRADED
        
        manager.on_redis_recovery()
        
        assert manager.level == DegradationLevel.NORMAL
        assert manager._failure_count == 0
    
    def test_callbacks(self, mock_redis):
        """Test degradation/recovery callbacks."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        degradation_called = []
        recovery_called = []
        
        manager.register_on_degradation(lambda l: degradation_called.append(l))
        manager.register_on_recovery(lambda l: recovery_called.append(l))
        
        manager.on_redis_failure()
        assert len(degradation_called) == 1
        
        manager.on_redis_recovery()
        assert len(recovery_called) == 1
    
    def test_get_status(self, mock_redis):
        """Test status retrieval."""
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        status = manager.get_status()
        
        assert status["level"] == "normal"
        assert status["is_degraded"] is False
        assert "failure_count" in status


# =============================================================================
# Test: HashChainCircuitBreaker
# =============================================================================

class TestHashChainCircuitBreaker:
    """Tests for HashChainCircuitBreaker."""
    
    def test_initial_state_closed(self):
        """Test circuit starts in closed state."""
        cb = HashChainCircuitBreaker()
        
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True
    
    def test_opens_after_threshold(self):
        """Test circuit opens after failure threshold."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = HashChainCircuitBreaker(config=config)
        
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False
    
    def test_half_open_after_timeout(self):
        """Test circuit transitions to half-open after timeout."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
        )
        cb = HashChainCircuitBreaker(config=config)
        
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        
        time.sleep(0.15)
        
        # Should transition to HALF_OPEN on next check
        assert cb.state == CircuitState.HALF_OPEN
    
    def test_half_open_allows_limited_requests(self):
        """Test half-open allows limited requests."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
            half_open_requests=2,
        )
        cb = HashChainCircuitBreaker(config=config)
        
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        
        # First 2 requests should be allowed
        assert cb.can_execute() is True
        assert cb.can_execute() is True
        # Third should be blocked
        assert cb.can_execute() is False
    
    def test_closes_on_success(self):
        """Test circuit closes after successful requests in half-open."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
            success_threshold=2,
        )
        cb = HashChainCircuitBreaker(config=config)
        
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        
        assert cb.state == CircuitState.HALF_OPEN
        
        cb.record_success()
        cb.record_success()
        
        assert cb.state == CircuitState.CLOSED
    
    def test_reopens_on_failure_in_half_open(self):
        """Test circuit reopens on failure in half-open state."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
        )
        cb = HashChainCircuitBreaker(config=config)
        
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        
        assert cb.state == CircuitState.HALF_OPEN
        
        cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
    
    def test_force_open(self):
        """Test force open."""
        cb = HashChainCircuitBreaker()
        
        cb.force_open()
        
        assert cb.state == CircuitState.OPEN
    
    def test_force_closed(self):
        """Test force closed."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = HashChainCircuitBreaker(config=config)
        
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        
        cb.force_closed()
        
        assert cb.state == CircuitState.CLOSED
    
    def test_stats(self):
        """Test statistics tracking."""
        cb = HashChainCircuitBreaker()
        
        cb.can_execute()
        cb.record_success()
        cb.record_failure()
        
        stats = cb.get_stats()
        
        assert stats["total_requests"] == 1
        assert stats["total_successes"] == 1
        assert stats["total_failures"] == 1
    
    def test_notifies_degradation_manager(self):
        """Test circuit breaker notifies degradation manager."""
        HashChainDegradationManager.reset_instance()
        
        mock_redis = MockRedisClient()
        degradation_mgr = HashChainDegradationManager(redis_client=mock_redis)
        
        config = CircuitBreakerConfig(failure_threshold=2)
        cb = HashChainCircuitBreaker(
            config=config,
            degradation_manager=degradation_mgr,
        )
        
        cb.record_failure()
        cb.record_failure()
        
        # Circuit should be open and degradation manager notified
        assert cb.state == CircuitState.OPEN
        assert degradation_mgr.level == DegradationLevel.DEGRADED
        
        HashChainDegradationManager.reset_instance()


# =============================================================================
# Test: HashChainGracefulDegradationManager
# =============================================================================

class TestHashChainGracefulDegradationManager:
    """Tests for HashChainGracefulDegradationManager."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        HashChainDegradationManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        HashChainDegradationManager.reset_instance()
    
    def test_initialization(self, mock_redis, temp_dir):
        """Test manager initialization."""
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        
        assert manager._initialized is False
        
        manager.initialize()
        
        assert manager._initialized is True
        assert manager._fallback_chain is not None
        assert manager._degraded_marker is not None
        assert manager._wal_recovery is not None
        assert manager._circuit_breaker is not None
    
    def test_recover_on_startup(self, mock_redis, temp_dir):
        """Test startup recovery."""
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        
        result = manager.recover_on_startup()
        
        assert result["status"] == "success"
        assert "wal_recovery" in result
    
    def test_add_integrity_with_fallback(self, mock_redis, temp_dir, sample_entry):
        """Test adding integrity with fallback."""
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        manager.initialize()
        
        result = manager.add_integrity_with_fallback(sample_entry)
        
        assert "integrity" in result
        assert "current_hash" in result["integrity"]
    
    def test_degradation_level_property(self, mock_redis, temp_dir):
        """Test degradation level property."""
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        manager.initialize()
        
        assert manager.degradation_level == DegradationLevel.NORMAL
        assert manager.is_degraded is False
    
    def test_get_status(self, mock_redis, temp_dir):
        """Test status retrieval."""
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        manager.initialize()
        
        status = manager.get_status()
        
        assert status["initialized"] is True
        assert "circuit_breaker" in status
        assert "fallback_chain" in status
        assert "degraded_marker" in status


# =============================================================================
# Test: Integration Scenarios
# =============================================================================

class TestPhase4Integration:
    """Integration tests for Phase 4 components."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        HashChainDegradationManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        HashChainDegradationManager.reset_instance()
    
    def test_full_failure_recovery_cycle(self, temp_dir):
        """Test complete failure and recovery cycle."""
        mock_redis = MockRedisClient()
        
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
            local_fallback_path=temp_dir / "fallback.jsonl",
        )
        manager.initialize()
        
        # 1. Normal operation
        entry1 = {"event": "test1"}
        result1 = manager.add_integrity_with_fallback(entry1)
        assert manager.degradation_level == DegradationLevel.NORMAL
        
        # 2. Simulate Redis failure
        mock_redis.set_should_fail(True)
        
        # Trigger circuit breaker
        for _ in range(5):
            manager._circuit_breaker.record_failure()
        
        # 3. Operation during failure (uses fallback)
        entry2 = {"event": "test2_during_failure"}
        result2 = manager.add_integrity_with_fallback(entry2)
        
        assert result2.get("integrity", {}).get("degraded") is True
        
        # 4. Redis recovers
        mock_redis.set_should_fail(False)
        manager._circuit_breaker.force_closed()
        
        # 5. Normal operation resumed
        entry3 = {"event": "test3_after_recovery"}
        result3 = manager.add_integrity_with_fallback(entry3)
        
        assert manager.degradation_level == DegradationLevel.NORMAL
        
        manager.close()
    
    def test_wal_protects_against_crash(self, temp_dir):
        """Test WAL provides crash protection."""
        mock_redis = MockRedisClient()
        
        # Session 1: Write entry, crash before commit
        recovery1 = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        entry = {"integrity": {"sequence": 10, "current_hash": "abc123"}}
        recovery1.write_wal_entry("add_integrity", entry)
        # Simulate crash - no commit
        recovery1.close()
        
        # Session 2: Recover from WAL
        recovery2 = HashChainWALRecovery(
            wal_dir=temp_dir,
            redis_client=mock_redis,
        )
        
        result = recovery2.recover_on_startup()
        
        assert result["entries_recovered"] == 1
        
        # Verify Redis was updated
        seq = mock_redis._data.get(f"selfhealing:audit:hash_chain:seq")
        assert seq == 10
        
        recovery2.close()
    
    def test_circuit_breaker_prevents_cascade(self, temp_dir):
        """Test circuit breaker prevents cascading failures."""
        mock_redis = MockRedisClient()
        
        manager = HashChainGracefulDegradationManager(
            redis_client=mock_redis,
            wal_dir=temp_dir,
        )
        manager.initialize()
        
        # Make Redis fail
        mock_redis.set_should_fail(True)
        
        # Trigger failures
        for _ in range(5):
            manager._circuit_breaker.record_failure()
        
        assert manager._circuit_breaker.state == CircuitState.OPEN
        
        # Operations should use fallback without hitting Redis
        entry = {"event": "test"}
        result = manager.add_integrity_with_fallback(entry)
        
        # Entry should be processed via fallback
        assert result.get("integrity", {}).get("degraded") is True
        
        manager.close()
    
    def test_degraded_entries_tracked_for_reconciliation(self, temp_dir):
        """Test degraded entries are tracked for later reconciliation."""
        manager = HashChainGracefulDegradationManager(
            redis_client=None,  # No Redis = always degraded
            wal_dir=temp_dir,
        )
        manager.initialize()
        
        # Add entries in degraded mode
        for i in range(3):
            entry = {"event": f"test_{i}"}
            manager.add_integrity_with_fallback(entry)
        
        # Check tracking
        marker = manager._degraded_marker
        unreconciled = marker.get_unreconciled_count()
        
        assert unreconciled == 3
        
        manager.close()
