"""
Tests for distributed hash chain integrity components.

Tests cover:
1. PendingSequenceManager - PENDING state lifecycle (reserve/commit/abort)
2. DailyHashAnchor - Daily checkpoint creation and anchor-based verification
3. StartupHashChainSync - Redis/file state synchronization on startup
4. HashChainReconciler - Merging degraded entries back into main chain
"""

import json
import os
import threading
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, patch

import pytest

from selfhealing.audit.integrity import (
    PendingSequenceManager,
    DailyHashAnchor,
    StartupHashChainSync,
    HashChainReconciler,
    HashChainManager,
    compute_hash,
)


# =============================================================================
# Mock Redis Client (MockRedisClient와 동일 패턴)
# =============================================================================

class MockRedisClient:
    """테스트용 Mock Redis 클라이언트."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        value = self._data.get(key)
        if value is not None:
            return str(value).encode() if not isinstance(value, bytes) else value
        return None
    
    def set(self, key: str, value: Any, nx: bool = False, ex: int = None, px: int = None) -> bool:
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
    
    def keys(self, pattern: str) -> List[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        import fnmatch
        # Redis glob 패턴을 fnmatch 패턴으로 변환
        matching = [k.encode() for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]
        return matching
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        if value is not None:
            return str(value).encode()
        return None
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if mapping is None:
            mapping = kwargs
        with self._lock:
            if key not in self._hashes:
                self._hashes[key] = {}
            self._hashes[key].update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        return {k.encode(): v.encode() for k, v in hash_data.items()}
    
    def incr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            current = int(self._data.get(key, 0))
            new_value = current + 1
            self._data[key] = new_value
            return new_value
    
    def expire(self, key: str, seconds: int) -> int:
        # TTL은 테스트에서 무시
        return 1 if key in self._data or key in self._hashes else 0
    
    def pipeline(self):
        return MockPipeline(self)


class MockPipeline:
    """Mock Redis Pipeline."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands = []
    
    def get(self, key: str):
        self._commands.append(("get", key))
        return self
    
    def set(self, key: str, value: Any, ex: int = None, nx: bool = False):
        self._commands.append(("set", key, value, ex, nx))
        return self
    
    def delete(self, *keys: str):
        # Decode bytes keys if necessary
        decoded_keys = []
        for k in keys:
            if isinstance(k, bytes):
                decoded_keys.append(k.decode("utf-8"))
            else:
                decoded_keys.append(k)
        self._commands.append(("delete", tuple(decoded_keys)))
        return self
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs):
        self._commands.append(("hset", key, mapping or kwargs))
        return self
    
    def execute(self):
        results = []
        for cmd in self._commands:
            if cmd[0] == "get":
                results.append(self._redis.get(cmd[1]))
            elif cmd[0] == "set":
                key, value = cmd[1], cmd[2]
                self._redis.set(key, value)
                results.append(True)
            elif cmd[0] == "delete":
                for k in cmd[1]:
                    self._redis.delete(k)
                results.append(len(cmd[1]))
            elif cmd[0] == "hset":
                self._redis.hset(cmd[1], cmd[2])
                results.append(len(cmd[2]))
        self._commands = []
        return results


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_redis():
    """테스트용 Mock Redis 클라이언트."""
    return MockRedisClient()


@pytest.fixture
def failing_redis():
    """실패하는 Mock Redis 클라이언트."""
    return MockRedisClient(should_fail=True)


@pytest.fixture
def temp_log_dir(tmp_path):
    """임시 로그 디렉토리."""
    log_dir = tmp_path / "audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# =============================================================================
# Test: PendingSequenceManager
# =============================================================================

class TestPendingSequenceManager:
    """PendingSequenceManager 테스트."""
    
    def test_reserve_sequence_success(self, mock_redis):
        """시퀀스 예약 성공 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        result = manager.reserve_sequence(1, "hash123")
        
        assert result is True
        # Redis에 PENDING 키가 생성되었는지 확인
        pending_key = "test:audit:hash_chain:pending:1"
        assert mock_redis.get(pending_key) is not None
    
    def test_reserve_sequence_duplicate_blocked(self, mock_redis):
        """동일 시퀀스 중복 예약 차단 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # 첫 예약 성공
        assert manager.reserve_sequence(1, "hash123") is True
        
        # 같은 시퀀스 재예약 차단
        assert manager.reserve_sequence(1, "hash456") is False
    
    def test_commit_sequence(self, mock_redis):
        """시퀀스 커밋 테스트 (PENDING 제거)."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # 예약
        manager.reserve_sequence(1, "hash123")
        
        # 커밋
        result = manager.commit_sequence(1)
        
        assert result is True
        # PENDING 키가 삭제되었는지 확인
        pending_key = "test:audit:hash_chain:pending:1"
        assert mock_redis.get(pending_key) is None
    
    def test_abort_sequence(self, mock_redis):
        """시퀀스 중단 테스트 (PENDING -> ORPHANED)."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # 예약
        manager.reserve_sequence(1, "hash123")
        
        # 중단
        result = manager.abort_sequence(1)
        
        assert result is True
        # PENDING 제거, ORPHANED 생성 확인
        pending_key = "test:audit:hash_chain:pending:1"
        orphaned_key = "test:audit:hash_chain:orphaned:1"
        assert mock_redis.get(pending_key) is None
        assert mock_redis.get(orphaned_key) is not None
    
    def test_get_pending_sequences(self, mock_redis):
        """PENDING 시퀀스 목록 조회 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # 여러 시퀀스 예약
        manager.reserve_sequence(1, "hash1")
        manager.reserve_sequence(3, "hash3")
        manager.reserve_sequence(2, "hash2")
        
        # 조회 (정렬되어 반환)
        pending = manager.get_pending_sequences()
        
        assert pending == [1, 2, 3]
    
    def test_get_orphaned_sequences(self, mock_redis):
        """ORPHANED 시퀀스 목록 조회 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # 예약 후 중단
        manager.reserve_sequence(1, "hash1")
        manager.abort_sequence(1)
        manager.reserve_sequence(3, "hash3")
        manager.abort_sequence(3)
        
        # 조회
        orphaned = manager.get_orphaned_sequences()
        
        assert 1 in orphaned
        assert 3 in orphaned
    
    def test_get_expected_hash(self, mock_redis):
        """예상 해시 조회 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        # PENDING 상태
        manager.reserve_sequence(1, "pending_hash")
        assert manager.get_expected_hash(1) == "pending_hash"
        
        # ORPHANED 상태로 전환
        manager.abort_sequence(1)
        # ORPHANED에서도 해시 조회 가능
        assert manager.get_expected_hash(1) == "pending_hash"
    
    def test_clear_orphaned(self, mock_redis):
        """ORPHANED 정리 테스트."""
        manager = PendingSequenceManager(mock_redis, key_prefix="test:")
        
        manager.reserve_sequence(1, "hash1")
        manager.abort_sequence(1)
        
        # ORPHANED 정리
        result = manager.clear_orphaned(1)
        
        assert result is True
        assert manager.get_expected_hash(1) is None


# =============================================================================
# Test: DailyHashAnchor
# =============================================================================

class TestDailyHashAnchor:
    """DailyHashAnchor 테스트."""
    
    def test_create_anchor(self, mock_redis):
        """앵커 생성 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        result = anchor.create_anchor(
            date="2026-01-18",
            sequence=100,
            hash_value="abc123"
        )
        
        assert result["date"] == "2026-01-18"
        assert result["sequence"] == "100"
        assert result["hash"] == "abc123"
        assert "created_at" in result
    
    def test_create_anchor_auto_state(self, mock_redis):
        """현재 상태에서 자동 앵커 생성 테스트."""
        # Redis에 상태 설정
        state_key = "test:audit:hash_chain:state"
        mock_redis.hset(state_key, mapping={
            "sequence": "50",
            "previous_hash": "auto_hash_value"
        })
        
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 시퀀스/해시 없이 생성
        result = anchor.create_anchor(date="2026-01-18")
        
        assert result["sequence"] == "50"
        assert result["hash"] == "auto_hash_value"
    
    def test_get_anchor(self, mock_redis):
        """앵커 조회 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 앵커 생성
        anchor.create_anchor(
            date="2026-01-18",
            sequence=100,
            hash_value="abc123"
        )
        
        # 조회
        result = anchor.get_anchor("2026-01-18")
        
        assert result is not None
        assert result["sequence"] == 100
        assert result["hash"] == "abc123"
    
    def test_get_anchor_not_found(self, mock_redis):
        """없는 앵커 조회 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        result = anchor.get_anchor("1999-12-31")
        
        assert result is None
    
    def test_verify_from_anchor_success(self, mock_redis, temp_log_dir):
        """앵커 기반 검증 성공 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 앵커 생성
        anchor_hash = "previous_day_hash"
        anchor.create_anchor(
            date="2026-01-17",
            sequence=10,
            hash_value=anchor_hash
        )
        
        # 앵커 이후 엔트리 생성
        entries = []
        prev_hash = anchor_hash
        for i in range(3):
            entry = {
                "event": f"event_{i}",
                "integrity": {
                    "sequence": 11 + i,
                    "previous_hash": prev_hash,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            }
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            entries.append(entry)
            prev_hash = current_hash
        
        # 검증
        is_valid, error = anchor.verify_from_anchor(entries, "2026-01-17")
        
        assert is_valid is True
        assert error is None
    
    def test_verify_from_anchor_chain_broken(self, mock_redis):
        """앵커 경계 체인 깨짐 감지 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 앵커 생성
        anchor.create_anchor(
            date="2026-01-17",
            sequence=10,
            hash_value="correct_anchor_hash"
        )
        
        # 잘못된 previous_hash를 가진 엔트리
        entries = [{
            "event": "event_0",
            "integrity": {
                "sequence": 11,
                "previous_hash": "WRONG_HASH",  # 앵커와 불일치
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "current_hash": "some_hash",
            }
        }]
        
        # 검증
        is_valid, error = anchor.verify_from_anchor(entries, "2026-01-17")
        
        assert is_valid is False
        assert "broken" in error.lower()
    
    def test_list_anchors(self, mock_redis):
        """최근 앵커 목록 조회 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 오늘 앵커 생성
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        anchor.create_anchor(date=today, sequence=1, hash_value="today_hash")
        
        # 목록 조회
        anchors = anchor.list_anchors(days=7)
        
        assert len(anchors) >= 1
        assert any(a["date"] == today for a in anchors)
    
    def test_delete_anchor(self, mock_redis):
        """앵커 삭제 테스트."""
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        
        # 생성
        anchor.create_anchor(date="2026-01-18", sequence=1, hash_value="hash")
        assert anchor.get_anchor("2026-01-18") is not None
        
        # 삭제
        result = anchor.delete_anchor("2026-01-18")
        
        assert result is True
        assert anchor.get_anchor("2026-01-18") is None


# =============================================================================
# Test: StartupHashChainSync
# =============================================================================

class TestStartupHashChainSync:
    """StartupHashChainSync 테스트."""
    
    def test_sync_fresh_start(self, mock_redis, temp_log_dir):
        """빈 상태에서 시작 테스트."""
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = sync.sync()
        
        assert result["status"] == "success"
        assert result["action"] == "fresh_start"
        assert result["file_sequence"] == 0
        assert result["redis_sequence"] == 0
    
    def test_sync_redis_ahead(self, mock_redis, temp_log_dir):
        """Redis가 파일보다 앞선 경우 (정상 상태)."""
        # Redis에 상태 설정
        mock_redis.set("test:audit:hash_chain:seq", 10)
        mock_redis.hset("test:audit:hash_chain:state", mapping={
            "previous_hash": "redis_hash",
            "sequence": "10",
        })
        
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = sync.sync()
        
        assert result["status"] == "success"
        assert result["action"] == "redis_ahead_ok"
        assert result["redis_sequence"] == 10
        assert result["file_sequence"] == 0
    
    def test_sync_file_ahead(self, mock_redis, temp_log_dir):
        """파일이 Redis보다 앞선 경우 (Redis 복구 필요)."""
        # 파일에 엔트리 작성
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        entries = []
        prev_hash = "GENESIS"
        for i in range(5):
            entry = {
                "event": f"event_{i}",
                "integrity": {
                    "sequence": i + 1,
                    "previous_hash": prev_hash,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            }
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            entries.append(entry)
            prev_hash = current_hash
        
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        
        # Redis는 비어있음 (시퀀스 0)
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = sync.sync()
        
        assert result["status"] == "success"
        assert result["action"] == "synced_redis_to_file"
        assert result["file_sequence"] == 5
        # Redis가 파일에 맞춰 업데이트 되었는지 확인
        assert int(mock_redis.get("test:audit:hash_chain:seq")) == 5
    
    def test_sync_in_sync(self, mock_redis, temp_log_dir):
        """이미 동기화된 상태 테스트."""
        # 파일에 엔트리 작성
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        entry = {
            "event": "test",
            "integrity": {
                "sequence": 5,
                "previous_hash": "prev",
                "current_hash": "curr",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(entry) + "\n")
        
        # Redis도 동일 시퀀스
        mock_redis.set("test:audit:hash_chain:seq", 5)
        mock_redis.hset("test:audit:hash_chain:state", mapping={
            "previous_hash": "curr",
            "sequence": "5",
        })
        
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = sync.sync()
        
        assert result["status"] == "success"
        assert result["action"] == "in_sync"
    
    def test_sync_cleanup_pending(self, mock_redis, temp_log_dir):
        """PENDING 시퀀스 정리 테스트."""
        # PENDING 상태 설정
        mock_redis.set("test:audit:hash_chain:pending:10", "hash10")
        mock_redis.set("test:audit:hash_chain:pending:11", "hash11")
        
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = sync.sync()
        
        assert result["pending_cleaned"] == 2
        # PENDING이 ORPHANED로 이동되었는지 확인
        assert mock_redis.get("test:audit:hash_chain:pending:10") is None
        assert mock_redis.get("test:audit:hash_chain:orphaned:10") is not None
    
    def test_sync_idempotent(self, mock_redis, temp_log_dir):
        """동기화 멱등성 테스트 (한 번만 실행)."""
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        
        # 첫 실행
        result1 = sync.sync()
        assert result1["status"] == "success"
        
        # 재실행
        result2 = sync.sync()
        assert result2["status"] == "already_synced"


# =============================================================================
# Test: HashChainReconciler
# =============================================================================

class TestHashChainReconciler:
    """HashChainReconciler 테스트."""
    
    def test_reconcile_no_degraded(self, mock_redis, temp_log_dir):
        """degraded 엔트리 없을 때 테스트."""
        reconciler = HashChainReconciler(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = reconciler.reconcile()
        
        assert result["status"] == "no_degraded_entries"
        assert result["degraded_entries_found"] == 0
    
    def test_reconcile_with_degraded_entries(self, mock_redis, temp_log_dir):
        """degraded 엔트리 병합 테스트."""
        # 파일에 degraded 엔트리 작성
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        
        degraded_entries = []
        for i in range(3):
            entry = {
                "event": f"degraded_event_{i}",
                "integrity": {
                    "sequence": -1,  # degraded 시퀀스
                    "previous_hash": "DEGRADED",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "degraded": True,
                    "current_hash": f"hash_{i}",
                }
            }
            degraded_entries.append(entry)
        
        with open(log_file, "w") as f:
            for entry in degraded_entries:
                f.write(json.dumps(entry) + "\n")
        
        # Redis 초기 상태
        mock_redis.set("test:audit:hash_chain:seq", 0)
        mock_redis.hset("test:audit:hash_chain:state", mapping={
            "previous_hash": "GENESIS",
            "sequence": "0",
        })
        
        reconciler = HashChainReconciler(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = reconciler.reconcile()
        
        assert result["status"] == "success"
        assert result["degraded_entries_found"] == 3
        assert result["entries_merged"] == 3
        assert result["new_sequence_start"] == 1
        assert result["new_sequence_end"] == 3
        
        # Redis 상태 업데이트 확인
        assert int(mock_redis.get("test:audit:hash_chain:seq")) == 3
    
    def test_reconcile_skips_already_reconciled(self, mock_redis, temp_log_dir):
        """이미 reconciled된 엔트리는 건너뛰기 테스트."""
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        
        entries = [
            {
                "event": "already_reconciled",
                "integrity": {
                    "sequence": 1,
                    "previous_hash": "GENESIS",
                    "degraded": True,
                    "reconciled": True,  # 이미 처리됨
                    "current_hash": "hash1",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            },
            {
                "event": "needs_reconciliation",
                "integrity": {
                    "sequence": -1,
                    "previous_hash": "DEGRADED",
                    "degraded": True,
                    # reconciled 없음
                    "current_hash": "hash2",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            },
        ]
        
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        
        reconciler = HashChainReconciler(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = reconciler.reconcile()
        
        # 이미 reconciled된 것은 제외
        assert result["degraded_entries_found"] == 1
        assert result["entries_merged"] == 1
    
    def test_reconcile_preserves_chain_continuity(self, mock_redis, temp_log_dir):
        """병합 후 체인 연속성 보장 테스트."""
        # 기존 체인 상태
        existing_hash = "existing_chain_hash"
        mock_redis.set("test:audit:hash_chain:seq", 5)
        mock_redis.hset("test:audit:hash_chain:state", mapping={
            "previous_hash": existing_hash,
            "sequence": "5",
        })
        
        # degraded 엔트리
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        entry = {
            "event": "degraded_event",
            "integrity": {
                "sequence": -1,
                "previous_hash": "DEGRADED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "degraded": True,
                "current_hash": "temp_hash",
            }
        }
        with open(log_file, "w") as f:
            f.write(json.dumps(entry) + "\n")
        
        reconciler = HashChainReconciler(mock_redis, temp_log_dir, key_prefix="test:")
        
        result = reconciler.reconcile()
        
        # 새 시퀀스는 기존 체인 이어서 시작
        assert result["new_sequence_start"] == 6
        assert result["new_sequence_end"] == 6
    
    def test_get_stats(self, mock_redis, temp_log_dir):
        """통계 조회 테스트."""
        reconciler = HashChainReconciler(mock_redis, temp_log_dir, key_prefix="test:")
        
        # 초기 상태
        stats = reconciler.get_stats()
        assert stats["last_reconciliation"] is None
        assert str(temp_log_dir) in stats["log_dir"]
        
        # reconcile 후
        reconciler.reconcile()
        stats = reconciler.get_stats()
        # no_degraded_entries라도 마지막 reconciliation 시간은 None
        # (엔트리가 있을 때만 업데이트)


# =============================================================================
# Integration Test: Full Workflow
# =============================================================================

class TestHashChainCoreIntegration:
    """Distributed hash chain core integration tests."""
    
    def test_full_write_flow_with_pending(self, mock_redis, temp_log_dir):
        """전체 쓰기 플로우 테스트 (PENDING 상태 포함)."""
        from selfhealing.audit.backends.local import LocalFileBackend
        
        backend = LocalFileBackend(
            log_dir=str(temp_log_dir),
            enable_hash_chain=True,
            distributed_hash_chain=True,
            redis_client=mock_redis,
            redis_key_prefix="test:",
            enable_pending_manager=True,
            enable_anchor_backup=False,  # 테스트 단순화
        )
        
        # 엔트리 작성
        entry = {"event": "test_event", "data": "test_data"}
        result = backend.write(entry)
        
        assert result is True
        
        # 파일에 기록되었는지 확인
        log_files = list(temp_log_dir.glob("audit_*.jsonl"))
        assert len(log_files) > 0
        
        # PENDING이 commit 되었는지 확인 (pending 키 없음)
        pending_keys = mock_redis.keys("test:audit:hash_chain:pending:*")
        assert len(pending_keys) == 0
    
    def test_startup_sync_after_crash_recovery(self, mock_redis, temp_log_dir):
        """크래시 후 시작 시 복구 테스트."""
        # 시뮬레이션: 파일에는 쓰였지만 Redis가 리셋됨
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        
        # 이전에 성공적으로 쓴 엔트리들
        entries = []
        prev_hash = "GENESIS"
        for i in range(10):
            entry = {
                "event": f"event_{i}",
                "integrity": {
                    "sequence": i + 1,
                    "previous_hash": prev_hash,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            }
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            entries.append(entry)
            prev_hash = current_hash
        
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        
        # Redis는 리셋됨 (비어있음)
        
        # 시작 시 동기화
        sync = StartupHashChainSync(mock_redis, temp_log_dir, key_prefix="test:")
        result = sync.sync()
        
        assert result["action"] == "synced_redis_to_file"
        assert result["file_sequence"] == 10
        
        # Redis가 파일 상태로 복구되었는지 확인
        assert int(mock_redis.get("test:audit:hash_chain:seq")) == 10
    
    def test_daily_anchor_created_on_day_boundary(self, mock_redis, temp_log_dir):
        """일 경계에서 앵커 생성 테스트."""
        from selfhealing.audit.backends.local import LocalFileBackend
        
        # Redis에 상태 설정
        mock_redis.set("test:audit:hash_chain:seq", 50)
        mock_redis.hset("test:audit:hash_chain:state", mapping={
            "previous_hash": "day_end_hash",
            "sequence": "50",
        })
        
        backend = LocalFileBackend(
            log_dir=str(temp_log_dir),
            enable_hash_chain=True,
            distributed_hash_chain=True,
            redis_client=mock_redis,
            redis_key_prefix="test:",
            enable_pending_manager=False,
            enable_anchor_backup=True,
        )
        
        # 어제 날짜로 시뮬레이션
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        backend._last_anchor_date = yesterday
        
        # 오늘 첫 쓰기 → 어제 앵커 생성되어야 함
        backend.write({"event": "first_write_today"})
        
        # 앵커 확인
        anchor = DailyHashAnchor(mock_redis, key_prefix="test:")
        yesterday_anchor = anchor.get_anchor(yesterday)
        
        assert yesterday_anchor is not None
        assert yesterday_anchor["date"] == yesterday


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
