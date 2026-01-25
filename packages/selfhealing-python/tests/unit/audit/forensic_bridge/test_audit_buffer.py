"""
Audit Buffer 테스트.

테스트 대상:
- TestInMemoryAuditBuffer: 메모리 버퍼 폴백
- TestRedisAuditBuffer: Redis Audit Buffer
"""

import json
import pytest
from unittest.mock import MagicMock, patch


class TestInMemoryAuditBuffer:
    """메모리 버퍼 폴백 테스트."""
    
    def test_buffer_add_entry(self):
        """엔트리 추가."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        
        # 새 인스턴스 생성 (테스트 격리)
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        entry = {"event_type": "TEST", "data": "test"}
        result = buffer.add(entry)
        
        assert result is True
        assert buffer.get_buffer_size() == 1
        
        stats = buffer.get_stats()
        assert stats["buffered_entries"] == 1
        assert stats["total_buffered"] == 1
        assert stats["total_dropped"] == 0
    
    def test_buffer_overflow_drops_oldest(self):
        """버퍼 초과 시 가장 오래된 엔트리 삭제."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        # MAX_ENTRIES를 임시로 줄여서 테스트
        original_max = InMemoryAuditBuffer.MAX_ENTRIES
        InMemoryAuditBuffer.MAX_ENTRIES = 3
        
        try:
            buffer.add({"id": 1})
            buffer.add({"id": 2})
            buffer.add({"id": 3})
            
            # 4번째 추가 시 1번이 삭제됨
            result = buffer.add({"id": 4})
            
            assert result is False  # dropped 발생
            assert buffer.get_buffer_size() == 3
            
            stats = buffer.get_stats()
            assert stats["total_dropped"] == 1
        finally:
            InMemoryAuditBuffer.MAX_ENTRIES = original_max
    
    def test_buffer_flush_success(self):
        """버퍼 플러시 성공."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        buffer.add({"event_type": "TEST1"})
        buffer.add({"event_type": "TEST2"})
        
        # Mock WAL 쓰기 함수
        written = []
        def mock_wal_write(entry):
            written.append(entry)
            return len(written)  # sequence 반환
        
        flushed = buffer.try_flush(mock_wal_write)
        
        assert flushed == 2
        assert buffer.get_buffer_size() == 0
        assert len(written) == 2
    
    def test_buffer_flush_partial_failure(self):
        """플러시 중 일부 실패."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        buffer.add({"event_type": "SUCCESS"})
        buffer.add({"event_type": "FAIL"})
        buffer.add({"event_type": "SUCCESS2"})
        
        call_count = [0]
        def mock_wal_write(entry):
            call_count[0] += 1
            if entry["event_type"] == "FAIL":
                return None  # 실패
            return call_count[0]
        
        flushed = buffer.try_flush(mock_wal_write)
        
        assert flushed == 2  # SUCCESS, SUCCESS2
        assert buffer.get_buffer_size() == 1  # FAIL이 남음
    
    def test_wal_failure_triggers_memory_buffer(self, temp_wal_dir):
        """WAL 실패 시 메모리 버퍼 저장."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        from selfhealing.services.audit.base import _write_to_wal, _get_wal
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        # WAL write가 실패하도록 Mock
        with patch("selfhealing.services.audit.base._get_wal") as mock_get_wal:
            mock_wal = MagicMock()
            mock_wal.write.side_effect = IOError("Disk full")
            mock_get_wal.return_value = mock_wal
            
            result = _write_to_wal(
                event_type="TEST_EVENT",
                source="TestSource",
                details={"key": "value"},
            )
            
            assert result is None  # WAL 쓰기 실패
            assert buffer.get_buffer_size() >= 1  # 메모리 버퍼에 저장됨
    
    def test_buffer_flush_on_wal_recovery(self, temp_wal_dir):
        """WAL 복구 시 버퍼 플러시."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        from selfhealing.services.audit.base import _try_flush_memory_buffer
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        # 수동으로 버퍼에 엔트리 추가
        buffer.add({"event_type": "BUFFERED1", "data": "test1"})
        buffer.add({"event_type": "BUFFERED2", "data": "test2"})
        
        assert buffer.get_buffer_size() == 2
        
        # 정상 WAL로 플러시
        config = WALConfig(wal_dir=temp_wal_dir, sync_on_write=False)
        wal = WriteAheadLog(config=config)
        
        try:
            with patch("selfhealing.services.audit.base._get_wal", return_value=wal):
                flushed = _try_flush_memory_buffer()
                
                assert flushed == 2
                assert buffer.get_buffer_size() == 0
        finally:
            wal.close()
    
    def test_buffer_stats(self):
        """버퍼 통계 확인."""
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        
        InMemoryAuditBuffer.reset_instance()
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.clear()
        
        buffer.add({"event_type": "TEST"})
        
        stats = buffer.get_stats()
        
        assert "buffered_entries" in stats
        assert "max_entries" in stats
        assert "total_buffered" in stats
        assert "total_dropped" in stats
        assert "flush_failures" in stats
        assert "last_flush_attempt" in stats


class TestRedisAuditBuffer:
    """Redis Audit Buffer 테스트."""
    
    def test_log_success(self):
        """Redis 기록 성공."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        # Mock Redis
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        result = buffer.log({"event_type": "TEST"}, domain="test")
        
        assert result is True
        mock_redis.pipeline.assert_called_once()
        mock_pipe.lpush.assert_called_once()
        mock_pipe.expire.assert_called_once()
        mock_pipe.execute.assert_called_once()
    
    def test_log_failure_uses_fallback(self):
        """Redis 실패 시 폴백 사용."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.side_effect = Exception("Redis down")
        mock_redis.pipeline.return_value = mock_pipe
        
        # spec을 사용하여 log_raw가 없는 fallback 시뮬레이션
        mock_fallback = MagicMock(spec=['log'])
        
        buffer = RedisAuditBuffer(
            redis_client=mock_redis,
            fallback_adapter=mock_fallback,
        )
        
        result = buffer.log({"event_type": "TEST"})
        
        assert result is False
        mock_fallback.log.assert_called_once()
    
    def test_on_fallback_callback(self):
        """폴백 콜백 호출."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.side_effect = Exception("Connection refused")
        mock_redis.pipeline.return_value = mock_pipe
        
        callback_called = []
        def on_fallback(e):
            callback_called.append(str(e))
        
        buffer = RedisAuditBuffer(
            redis_client=mock_redis,
            on_fallback=on_fallback,
        )
        
        buffer.log({"event_type": "TEST"})
        
        assert len(callback_called) == 1
        assert "Connection refused" in callback_called[0]
    
    def test_consecutive_failures_tracking(self):
        """연속 실패 추적."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.side_effect = Exception("Error")
        mock_redis.pipeline.return_value = mock_pipe
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        buffer.log({"event_type": "TEST1"})
        buffer.log({"event_type": "TEST2"})
        buffer.log({"event_type": "TEST3"})
        
        stats = buffer.get_buffer_stats()
        assert stats["consecutive_failures"] == 3
        assert stats["total_fallbacks"] == 3
    
    def test_success_resets_failure_count(self):
        """성공 시 실패 카운트 리셋."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        # 수동으로 failure 설정
        buffer._consecutive_failures = 5
        
        buffer.log({"event_type": "TEST"})
        
        assert buffer._consecutive_failures == 0
    
    def test_should_use_fallback(self):
        """폴백 사용 여부 판단."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        buffer._consecutive_failures = 2
        assert buffer.should_use_fallback() is False
        
        buffer._consecutive_failures = 3
        assert buffer.should_use_fallback() is True
    
    def test_is_healthy(self):
        """Redis 연결 상태 확인."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        # Healthy
        mock_redis.ping.return_value = True
        assert buffer.is_healthy() is True
        
        # Unhealthy
        mock_redis.ping.side_effect = Exception("Connection lost")
        assert buffer.is_healthy() is False
    
    def test_get_pending_count(self):
        """대기 엔트리 수 조회."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 42
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        count = buffer.get_pending_count("test")
        
        assert count == 42
        mock_redis.llen.assert_called_with("audit:buffer:test")
    
    def test_flush_to_external(self):
        """외부 저장소로 플러시."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        
        # scan_iter 설정
        mock_redis.scan_iter.return_value = [b"audit:buffer:test"]
        
        # rpop 설정 (2개 항목 후 None)
        entries = [
            json.dumps({"entry": {"event": "e1"}, "timestamp": "2026-01-08T00:00:00Z", "instance_id": "test"}),
            json.dumps({"entry": {"event": "e2"}, "timestamp": "2026-01-08T00:00:01Z", "instance_id": "test"}),
            None,
        ]
        mock_redis.rpop.side_effect = entries
        
        # spec을 사용하여 log_raw가 없는 target 시뮬레이션
        mock_target = MagicMock(spec=['log'])
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        flushed = buffer.flush_to_external(mock_target, domain="test")
        
        assert flushed == 2
        assert mock_target.log.call_count == 2
    
    def test_clear_domain(self):
        """도메인 삭제."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 5
        
        buffer = RedisAuditBuffer(redis_client=mock_redis)
        
        count = buffer.clear_domain("test")
        
        assert count == 5
        mock_redis.delete.assert_called_with("audit:buffer:test")
    
    def test_custom_key_prefix(self):
        """커스텀 키 프리픽스."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
        
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        buffer = RedisAuditBuffer(
            redis_client=mock_redis,
            key_prefix="custom:audit:",
        )
        
        buffer.log({"event": "test"}, domain="myapp")
        
        # lpush가 custom:audit:myapp 키로 호출되었는지 확인
        call_args = mock_pipe.lpush.call_args
        assert "custom:audit:myapp" in str(call_args)
    
    # NOTE: test_factory_function_no_redis는 실제 Redis 연결을 시도하므로
    # tests/integration/selfhealing/test_regional_gate_integration.py로 이동됨
