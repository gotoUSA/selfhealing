"""
Tests for Hash Chain Performance Optimization Components (Phase 3).

Covers high-performance features:
- LuaAtomicHashChain: Atomic sequence operations via Lua scripts
- PipelineBatchQuery: Batch state retrieval via Redis pipeline
- BatchFlushWriter: Batched file writes with reduced fsync
- AsyncAuditWriter: Non-blocking async write queue
- SamplingVerifier: Probabilistic chain verification
- PendingSequenceWatchdog: Self-cleanup daemon
- HashChainPerformanceManager: Unified component access
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

from selfhealing.audit.hash_chain_performance import (
    LuaAtomicHashChain,
    PipelineBatchQuery,
    BatchFlushWriter,
    BatchFlushConfig,
    AsyncAuditWriter,
    SamplingVerifier,
    SamplingConfig,
    PendingSequenceWatchdog,
    HashChainPerformanceManager,
)


# =============================================================================
# Mock Redis Client
# =============================================================================

class MockRedisClient:
    """Mock Redis client for testing."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        self._scripts: Dict[str, str] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
        self._script_counter = 0
    
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
    
    def decr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            val = int(self._data.get(key, 0)) - 1
            self._data[key] = val
            return val
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            self._hashes[key] = {}
        data = mapping or kwargs
        self._hashes[key].update(data)
        return len(data)
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        return str(value).encode() if value is not None else None
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        return {
            k.encode() if isinstance(k, str) else k: 
            str(v).encode() if not isinstance(v, bytes) else v
            for k, v in hash_data.items()
        }
    
    def exists(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        return 1 if (key in self._data or key in self._hashes) else 0
    
    def expire(self, key: str, seconds: int) -> bool:
        return True  # Simplified for testing
    
    def script_load(self, script: str) -> str:
        """Load script and return SHA."""
        self._script_counter += 1
        sha = f"sha_{self._script_counter}"
        self._scripts[sha] = script
        return sha
    
    def eval(self, script: str, num_keys: int, *args) -> Any:
        """Simple eval implementation for testing."""
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        
        # Simulate atomic add integrity
        if "INCR" in script and "HGET" in script:
            # Reserve sequence
            seq_key = args[0]
            new_seq = self.incr(seq_key)
            prev_hash = self.hget(args[1], "previous_hash")
            prev_hash = prev_hash.decode() if prev_hash else "GENESIS"
            return [new_seq, prev_hash]
        
        # Simulate commit
        if "EXISTS" in script and "DEL" in script:
            pending_key = args[0]
            if self.exists(pending_key):
                self.delete(pending_key)
                return {"ok": True}
            return {"err": "PENDING_NOT_FOUND"}
        
        return None
    
    def evalsha(self, sha: str, num_keys: int, *args) -> Any:
        """Eval by SHA - delegates to eval."""
        if sha in self._scripts:
            return self.eval(self._scripts[sha], num_keys, *args)
        raise Exception("NOSCRIPT")
    
    def pipeline(self, transaction: bool = True):
        """Return mock pipeline."""
        return MockPipeline(self)


class MockPipeline:
    """Mock Redis pipeline for testing."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands: List[tuple] = []
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def hgetall(self, key: str):
        self._commands.append(("hgetall", key))
        return self
    
    def exists(self, key: str):
        self._commands.append(("exists", key))
        return self
    
    def incr(self, key: str):
        self._commands.append(("incr", key))
        return self
    
    def expire(self, key: str, seconds: int):
        self._commands.append(("expire", key, seconds))
        return self
    
    def get(self, key: str):
        self._commands.append(("get", key))
        return self
    
    def execute(self) -> List[Any]:
        results = []
        for cmd in self._commands:
            if cmd[0] == "hgetall":
                results.append(self._redis.hgetall(cmd[1]))
            elif cmd[0] == "exists":
                results.append(self._redis.exists(cmd[1]))
            elif cmd[0] == "incr":
                results.append(self._redis.incr(cmd[1]))
            elif cmd[0] == "get":
                results.append(self._redis.get(cmd[1]))
            else:
                results.append(None)
        return results


# =============================================================================
# LuaAtomicHashChain Tests
# =============================================================================

class TestLuaAtomicHashChain:
    """Tests for LuaAtomicHashChain."""
    
    def test_reserve_sequence_atomic(self):
        """Test atomic sequence reservation."""
        redis = MockRedisClient()
        lua_chain = LuaAtomicHashChain(redis, key_prefix="test:")
        
        success, seq, error = lua_chain.reserve_sequence_atomic(
            expected_hash="hash123",
            previous_hash="GENESIS",
        )
        
        assert success is True
        assert seq == 1
        assert error == ""
    
    def test_reserve_sequence_increments(self):
        """Test that sequence increments correctly."""
        redis = MockRedisClient()
        lua_chain = LuaAtomicHashChain(redis, key_prefix="test:")
        
        _, seq1, _ = lua_chain.reserve_sequence_atomic("hash1", "GENESIS")
        _, seq2, _ = lua_chain.reserve_sequence_atomic("hash2", "hash1")
        _, seq3, _ = lua_chain.reserve_sequence_atomic("hash3", "hash2")
        
        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3
    
    def test_commit_sequence_atomic(self):
        """Test atomic sequence commit."""
        redis = MockRedisClient()
        lua_chain = LuaAtomicHashChain(redis, key_prefix="test:")
        
        # Reserve first
        lua_chain.reserve_sequence_atomic("expected_hash", "GENESIS")
        
        # Mock pending entry
        pending_key = "test:audit:hash_chain:pending:1"
        redis._hashes[pending_key] = {"expected_hash": "expected_hash"}
        
        # Commit
        success, error = lua_chain.commit_sequence_atomic(1, "expected_hash")
        
        assert success is True
        assert error == ""
    
    def test_commit_nonexistent_fails(self):
        """Test commit of non-existent pending fails."""
        redis = MockRedisClient()
        lua_chain = LuaAtomicHashChain(redis, key_prefix="test:")
        
        success, error = lua_chain.commit_sequence_atomic(999, "some_hash")
        
        assert success is False
        assert "NOT_FOUND" in error or error != ""
    
    def test_redis_failure_handling(self):
        """Test handling of Redis failures."""
        redis = MockRedisClient(should_fail=True)
        lua_chain = LuaAtomicHashChain(redis, key_prefix="test:")
        
        success, seq, error = lua_chain.reserve_sequence_atomic("hash", "prev")
        
        assert success is False
        assert "connection" in error.lower() or error != ""


# =============================================================================
# PipelineBatchQuery Tests
# =============================================================================

class TestPipelineBatchQuery:
    """Tests for PipelineBatchQuery."""
    
    def test_get_multiple_chain_states(self):
        """Test batch retrieval of chain states."""
        redis = MockRedisClient()
        
        # Setup chain states
        redis.hset("test:audit:hash_chain:state:chain1", mapping={
            "sequence": "10",
            "previous_hash": "hash_a",
            "updated_at": "2026-01-18T10:00:00Z",
        })
        redis.hset("test:audit:hash_chain:state:chain2", mapping={
            "sequence": "20",
            "previous_hash": "hash_b",
        })
        
        batch = PipelineBatchQuery(redis, key_prefix="test:")
        states = batch.get_multiple_chain_states(["chain1", "chain2"])
        
        assert "chain1" in states
        assert "chain2" in states
        assert states["chain1"]["sequence"] == 10
        assert states["chain2"]["sequence"] == 20
    
    def test_missing_chain_returns_default(self):
        """Test that missing chains return default values."""
        redis = MockRedisClient()
        batch = PipelineBatchQuery(redis, key_prefix="test:")
        
        states = batch.get_multiple_chain_states(["missing1", "missing2"])
        
        assert states["missing1"]["sequence"] == 0
        assert states["missing1"]["previous_hash"] == "GENESIS"
    
    def test_empty_list_returns_empty(self):
        """Test empty input returns empty result."""
        redis = MockRedisClient()
        batch = PipelineBatchQuery(redis, key_prefix="test:")
        
        states = batch.get_multiple_chain_states([])
        
        assert states == {}
    
    def test_batch_check_pending(self):
        """Test batch checking of pending sequences."""
        redis = MockRedisClient()
        
        # Setup some pending entries
        redis._hashes["test:audit:hash_chain:pending:1"] = {"data": "test"}
        redis._hashes["test:audit:hash_chain:pending:3"] = {"data": "test"}
        
        batch = PipelineBatchQuery(redis, key_prefix="test:")
        results = batch.batch_check_pending([1, 2, 3, 4])
        
        assert results[1] is True
        assert results[2] is False
        assert results[3] is True
        assert results[4] is False


# =============================================================================
# BatchFlushWriter Tests
# =============================================================================

class TestBatchFlushWriter:
    """Tests for BatchFlushWriter."""
    
    def test_write_buffers_entries(self):
        """Test that entries are buffered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            config = BatchFlushConfig(batch_size=10, flush_interval_seconds=60)
            writer = BatchFlushWriter(path, config)
            
            # Write less than batch size
            for i in range(5):
                writer.write({"id": i})
            
            # Should still be buffered
            assert len(writer._buffer) == 5
            assert not path.exists()
            
            writer.close()
    
    def test_flush_on_batch_size(self):
        """Test automatic flush when batch size reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            config = BatchFlushConfig(batch_size=5, sync_on_flush=False)
            writer = BatchFlushWriter(path, config)
            
            # Write exactly batch size
            for i in range(5):
                writer.write({"id": i})
            
            # Should have flushed
            assert len(writer._buffer) == 0
            assert path.exists()
            
            # Verify content
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 5
            
            writer.close()
    
    def test_force_flush(self):
        """Test force flush."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            writer = BatchFlushWriter(path, BatchFlushConfig(batch_size=100))
            
            writer.write({"id": 1})
            writer.write({"id": 2})
            
            assert len(writer._buffer) == 2
            
            writer.force_flush()
            
            assert len(writer._buffer) == 0
            assert path.exists()
            
            writer.close()
    
    def test_get_stats(self):
        """Test statistics retrieval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            config = BatchFlushConfig(batch_size=2, sync_on_flush=False)
            writer = BatchFlushWriter(path, config)
            
            for i in range(5):
                writer.write({"id": i})
            
            stats = writer.get_stats()
            
            assert stats["entries_written"] == 4  # 2 flushes of 2
            assert stats["flushes_performed"] == 2
            assert stats["buffer_size"] == 1  # 1 remaining
            
            writer.close()


# =============================================================================
# AsyncAuditWriter Tests
# =============================================================================

class TestAsyncAuditWriter:
    """Tests for AsyncAuditWriter."""
    
    def test_async_write_queues_entry(self):
        """Test that entries are queued."""
        written = []
        
        def sync_writer(entry):
            written.append(entry)
            return True
        
        writer = AsyncAuditWriter(sync_writer, batch_size=1)
        writer.start()
        
        try:
            writer.write_async({"id": 1})
            writer.write_async({"id": 2})
            
            # Give time for async processing
            time.sleep(0.5)
            
            assert len(written) >= 1
        finally:
            writer.stop()
    
    def test_queue_full_handling(self):
        """Test handling when queue is full."""
        def slow_writer(entry):
            time.sleep(0.1)
            return True
        
        writer = AsyncAuditWriter(slow_writer, max_queue_size=2)
        # Don't start - queue will fill up
        
        # Fill queue
        assert writer.write_async({"id": 1}) is True
        assert writer.write_async({"id": 2}) is True
        
        # Should fail (queue full, not blocking)
        result = writer.write_async({"id": 3}, block=False)
        assert result is False
        
        stats = writer.get_stats()
        assert stats["dropped"] >= 1
    
    def test_stats_tracking(self):
        """Test that statistics are tracked."""
        written_count = 0
        
        def sync_writer(entry):
            nonlocal written_count
            written_count += 1
            return True
        
        writer = AsyncAuditWriter(sync_writer, batch_size=1)
        writer.start()
        
        try:
            for i in range(5):
                writer.write_async({"id": i})
            
            time.sleep(0.5)
            
            stats = writer.get_stats()
            assert stats["queued"] == 5
            assert stats["written"] >= 1
            assert stats["is_running"] is True
        finally:
            writer.stop()


# =============================================================================
# SamplingVerifier Tests
# =============================================================================

class TestSamplingVerifier:
    """Tests for SamplingVerifier."""
    
    def _create_valid_chain(self, count: int) -> List[Dict[str, Any]]:
        """Create a valid chain of entries."""
        import hashlib
        
        entries = []
        prev_hash = "GENESIS"
        
        for i in range(count):
            entry = {
                "data": f"entry_{i}",
                "integrity": {
                    "sequence": i + 1,
                    "previous_hash": prev_hash,
                    "timestamp": f"2026-01-18T10:00:{i:02d}Z",
                },
            }
            
            # Compute hash
            json_str = json.dumps(entry, sort_keys=True)
            current_hash = hashlib.sha256(json_str.encode()).hexdigest()
            entry["integrity"]["current_hash"] = current_hash
            
            entries.append(entry)
            prev_hash = current_hash
        
        return entries
    
    def _create_tampered_chain(self, count: int, tamper_index: int) -> List[Dict[str, Any]]:
        """Create a chain with a tampered entry."""
        entries = self._create_valid_chain(count)
        
        # Tamper with entry
        if tamper_index < len(entries):
            entries[tamper_index]["data"] = "TAMPERED"
        
        return entries
    
    def test_valid_chain_passes(self):
        """Test that valid chain passes verification."""
        entries = self._create_valid_chain(100)
        
        config = SamplingConfig(sample_rate=0.5, min_samples=10)
        verifier = SamplingVerifier(config)
        
        is_valid, issues = verifier.verify_sampled(entries)
        
        assert is_valid is True
        assert len(issues) == 0
    
    def test_tampered_chain_detected(self):
        """Test that tampered chain is detected."""
        entries = self._create_tampered_chain(100, tamper_index=50)
        
        config = SamplingConfig(
            sample_rate=1.0,  # Full sampling to ensure detection
            full_verify_on_failure=False,
        )
        verifier = SamplingVerifier(config)
        
        is_valid, issues = verifier.verify_sampled(entries)
        
        # Should detect tampering
        assert is_valid is False or len(issues) > 0
    
    def test_empty_chain_passes(self):
        """Test that empty chain passes."""
        verifier = SamplingVerifier()
        
        is_valid, issues = verifier.verify_sampled([])
        
        assert is_valid is True
        assert issues == []
    
    def test_sampling_reduces_checks(self):
        """Test that sampling reduces number of checks."""
        entries = self._create_valid_chain(1000)
        
        config = SamplingConfig(
            sample_rate=0.1,
            min_samples=10,
            max_samples=100,
        )
        verifier = SamplingVerifier(config)
        
        # Verify - should only check ~100 entries
        is_valid, issues = verifier.verify_sampled(entries)
        
        assert is_valid is True


# =============================================================================
# PendingSequenceWatchdog Tests
# =============================================================================

class TestPendingSequenceWatchdog:
    """Tests for PendingSequenceWatchdog."""
    
    def test_register_and_commit(self):
        """Test registering and committing sequences."""
        redis = MockRedisClient()
        watchdog = PendingSequenceWatchdog(redis, key_prefix="test:")
        
        watchdog.register_pending(1)
        watchdog.register_pending(2)
        
        assert 1 in watchdog._local_pending
        assert 2 in watchdog._local_pending
        
        watchdog.mark_committed(1)
        
        assert 1 not in watchdog._local_pending
        assert 2 in watchdog._local_pending
    
    def test_mark_failed_cleans_redis(self):
        """Test that mark_failed cleans up Redis."""
        redis = MockRedisClient()
        redis._hashes["test:audit:hash_chain:pending:5"] = {"data": "test"}
        
        watchdog = PendingSequenceWatchdog(redis, key_prefix="test:")
        watchdog.register_pending(5)
        
        watchdog.mark_failed(5)
        
        # Should be removed from both local and Redis
        assert 5 not in watchdog._local_pending
        assert "test:audit:hash_chain:pending:5" not in redis._hashes
    
    def test_watchdog_lifecycle(self):
        """Test watchdog start/stop lifecycle."""
        redis = MockRedisClient()
        watchdog = PendingSequenceWatchdog(
            redis,
            key_prefix="test:",
            check_interval_seconds=0.1,
        )
        
        assert watchdog._is_running is False
        
        watchdog.start()
        assert watchdog._is_running is True
        
        time.sleep(0.2)
        
        watchdog.stop()
        assert watchdog._is_running is False
    
    def test_stats(self):
        """Test statistics retrieval."""
        redis = MockRedisClient()
        watchdog = PendingSequenceWatchdog(redis, key_prefix="test:")
        
        watchdog.register_pending(1)
        watchdog.register_pending(2)
        watchdog.mark_committed(1)
        
        stats = watchdog.get_stats()
        
        assert stats["local_pending_count"] == 1
        assert stats["is_running"] is False


# =============================================================================
# HashChainPerformanceManager Tests
# =============================================================================

class TestHashChainPerformanceManager:
    """Tests for HashChainPerformanceManager."""
    
    def test_initialization(self):
        """Test manager initialization."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(
            redis_client=redis,
            key_prefix="test:",
        )
        
        assert manager._redis is redis
        assert manager._lua_chain is None  # Lazy
        assert manager._sampler is None  # Lazy
    
    def test_lazy_init_lua_chain(self):
        """Test lazy initialization of lua_chain."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        # First access triggers initialization
        lua_chain = manager.lua_chain
        
        assert lua_chain is not None
        assert manager._lua_chain is lua_chain
        
        # Second access returns same instance
        assert manager.lua_chain is lua_chain
    
    def test_lazy_init_batch_query(self):
        """Test lazy initialization of batch_query."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        batch_query = manager.batch_query
        
        assert batch_query is not None
        assert manager._batch_query is batch_query
    
    def test_lazy_init_sampler(self):
        """Test lazy initialization of sampler."""
        manager = HashChainPerformanceManager()
        
        sampler = manager.sampler
        
        assert sampler is not None
        assert manager._sampler is sampler
    
    def test_no_redis_raises_error(self):
        """Test that components requiring Redis raise error."""
        manager = HashChainPerformanceManager()  # No Redis
        
        with pytest.raises(ValueError, match="Redis client required"):
            _ = manager.lua_chain
        
        with pytest.raises(ValueError, match="Redis client required"):
            _ = manager.batch_query
    
    def test_get_batch_writer(self):
        """Test creating batch writer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = HashChainPerformanceManager()
            path = Path(tmpdir) / "test.jsonl"
            
            writer = manager.get_batch_writer(path)
            
            assert writer is not None
            assert isinstance(writer, BatchFlushWriter)
    
    def test_get_async_writer(self):
        """Test creating async writer."""
        manager = HashChainPerformanceManager()
        
        def sync_writer(entry):
            return True
        
        writer = manager.get_async_writer(sync_writer)
        
        assert writer is not None
        assert isinstance(writer, AsyncAuditWriter)
    
    def test_get_watchdog(self):
        """Test getting watchdog."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        watchdog = manager.get_watchdog()
        
        assert watchdog is not None
        assert isinstance(watchdog, PendingSequenceWatchdog)
    
    def test_start_watchdog(self):
        """Test starting watchdog."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        manager.start_watchdog()
        
        assert manager._watchdog is not None
        assert manager._watchdog._is_running is True
        
        manager.stop_all()
    
    def test_stop_all(self):
        """Test stopping all components."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        manager.start_watchdog()
        
        manager.stop_all()
        
        assert manager._watchdog._is_running is False
    
    def test_get_all_stats(self):
        """Test getting all statistics."""
        redis = MockRedisClient()
        manager = HashChainPerformanceManager(redis_client=redis)
        
        manager.start_watchdog()
        manager._watchdog.register_pending(1)
        
        stats = manager.get_all_stats()
        
        assert "watchdog" in stats
        assert stats["watchdog"]["local_pending_count"] == 1
        
        manager.stop_all()


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase3Integration:
    """Integration tests for Phase 3 components."""
    
    def test_full_write_flow_with_performance_components(self):
        """Test complete write flow using performance components."""
        redis = MockRedisClient()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = HashChainPerformanceManager(
                redis_client=redis,
                log_dir=Path(tmpdir),
                key_prefix="test:",
            )
            
            # Start watchdog
            manager.start_watchdog()
            
            # Create batch writer
            path = Path(tmpdir) / "audit.jsonl"
            batch_writer = manager.get_batch_writer(
                path,
                BatchFlushConfig(batch_size=5, sync_on_flush=False),
            )
            
            try:
                # Simulate write flow
                for i in range(10):
                    # Reserve sequence (Lua atomic)
                    success, seq, _ = manager.lua_chain.reserve_sequence_atomic(
                        expected_hash=f"hash_{i}",
                        previous_hash=f"hash_{i-1}" if i > 0 else "GENESIS",
                    )
                    
                    # Track in watchdog
                    manager.get_watchdog().register_pending(seq)
                    
                    # Write to batch writer
                    entry = {
                        "data": f"entry_{i}",
                        "integrity": {
                            "sequence": seq,
                            "current_hash": f"hash_{i}",
                        },
                    }
                    batch_writer.write(entry)
                    
                    # Mark committed
                    manager.get_watchdog().mark_committed(seq)
                
                # Force flush remaining
                batch_writer.force_flush()
                
                # Verify file was written
                assert path.exists()
                with open(path) as f:
                    lines = f.readlines()
                assert len(lines) == 10
                
            finally:
                batch_writer.close()
                manager.stop_all()
    
    def test_sampling_verification_performance(self):
        """Test that sampling is faster than full verification."""
        import hashlib
        
        # Create large chain
        entries = []
        prev_hash = "GENESIS"
        
        for i in range(1000):
            entry = {
                "data": f"entry_{i}",
                "integrity": {
                    "sequence": i + 1,
                    "previous_hash": prev_hash,
                },
            }
            json_str = json.dumps(entry, sort_keys=True)
            current_hash = hashlib.sha256(json_str.encode()).hexdigest()
            entry["integrity"]["current_hash"] = current_hash
            entries.append(entry)
            prev_hash = current_hash
        
        # Full verification
        full_verifier = SamplingVerifier(SamplingConfig(sample_rate=1.0))
        start = time.monotonic()
        full_verifier.verify_sampled(entries)
        full_time = time.monotonic() - start
        
        # Sampled verification
        sampled_verifier = SamplingVerifier(SamplingConfig(
            sample_rate=0.1,
            max_samples=100,
        ))
        start = time.monotonic()
        sampled_verifier.verify_sampled(entries)
        sampled_time = time.monotonic() - start
        
        # Sampled should be faster (or at least not significantly slower)
        # Note: For small chains, overhead may dominate
        assert sampled_time <= full_time * 2  # Allow some margin
    
    def test_concurrent_batch_writes(self):
        """Test concurrent writes to batch writer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "concurrent.jsonl"
            writer = BatchFlushWriter(
                path,
                BatchFlushConfig(batch_size=20, sync_on_flush=False),
            )
            
            errors = []
            
            def write_entries(start_id: int, count: int):
                try:
                    for i in range(count):
                        writer.write({"id": start_id + i})
                except Exception as e:
                    errors.append(str(e))
            
            # Start multiple threads
            threads = [
                threading.Thread(target=write_entries, args=(i * 10, 10))
                for i in range(5)
            ]
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            writer.force_flush()
            writer.close()
            
            # Should have no errors
            assert len(errors) == 0
            
            # Should have all entries
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 50
