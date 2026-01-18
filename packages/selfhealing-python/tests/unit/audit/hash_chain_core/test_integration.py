"""
HashChainCore 통합 테스트.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest


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
            enable_anchor_backup=False,
        )
        
        # 엔트리 작성
        entry = {"event": "test_event", "data": "test_data"}
        result = backend.write(entry)
        
        assert result is True
        
        # 파일에 기록되었는지 확인
        log_files = list(temp_log_dir.glob("audit_*.jsonl"))
        assert len(log_files) > 0
        
        # PENDING이 commit 되었는지 확인
        pending_keys = mock_redis.keys("test:audit:hash_chain:pending:*")
        assert len(pending_keys) == 0
    
    def test_startup_sync_after_crash_recovery(self, mock_redis, temp_log_dir):
        """크래시 후 시작 시 복구 테스트."""
        from selfhealing.audit.integrity import StartupHashChainSync, compute_hash
        
        # 시뮬레이션: 파일에는 쓰였지만 Redis가 리셋됨
        log_file = temp_log_dir / "audit_2026-01-18.jsonl"
        
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
        from selfhealing.audit.integrity import DailyHashAnchor
        
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
