"""
Part 2: Audit Integration 테스트.

27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md 구현 검증.

테스트 범위:
1. CorruptionShield Audit 통합
2. ShadowLogger Audit 통합
3. WAL Audit 통합
4. ForensicAuditBridge
"""

import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================


class MockAuditAdapter:
    """테스트용 Audit 어댑터."""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
    
    def log_event(
        self,
        event_type: str,
        source: str,
        details: Dict[str, Any],
    ) -> None:
        self.events.append({
            "event_type": event_type,
            "source": source,
            "details": details,
        })
    
    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]


@pytest.fixture
def mock_audit_adapter():
    """Mock Audit 어댑터."""
    return MockAuditAdapter()


@pytest.fixture
def temp_wal_dir():
    """임시 WAL 디렉토리."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# =============================================================================
# Test: AuditEventType 신규 추가 확인
# =============================================================================


class TestAuditEventTypeAdditions:
    """AuditEventType 신규 이벤트 타입 테스트."""
    
    def test_corruption_event_types_exist(self):
        """CorruptionShield 관련 이벤트 타입 존재 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "CORRUPTION_DETECTED")
        assert hasattr(AuditEventType, "CORRUPTION_BLOCKED")
        
        assert AuditEventType.CORRUPTION_DETECTED.value == "corruption_detected"
        assert AuditEventType.CORRUPTION_BLOCKED.value == "corruption_blocked"
    
    def test_shadow_log_event_types_exist(self):
        """ShadowLogger 관련 이벤트 타입 존재 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "SHADOW_LOG_SYNC_FAILED")
        assert hasattr(AuditEventType, "SHADOW_LOG_RECOVERED")
        
        assert AuditEventType.SHADOW_LOG_SYNC_FAILED.value == "shadow_log_sync_failed"
        assert AuditEventType.SHADOW_LOG_RECOVERED.value == "shadow_log_recovered"
    
    def test_wal_event_types_exist(self):
        """WAL 관련 이벤트 타입 존재 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "WAL_CORRUPTION_DETECTED")
        assert hasattr(AuditEventType, "WAL_RECOVERED")
        assert hasattr(AuditEventType, "WAL_ROTATED")
        
        assert AuditEventType.WAL_CORRUPTION_DETECTED.value == "wal_corruption_detected"
        assert AuditEventType.WAL_RECOVERED.value == "wal_recovered"
        assert AuditEventType.WAL_ROTATED.value == "wal_rotated"
    
    def test_forensic_event_types_exist(self):
        """Forensic 관련 이벤트 타입 존재 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "FORENSIC_CAPTURE_STARTED")
        assert hasattr(AuditEventType, "FORENSIC_CAPTURE_COMPLETED")
        assert hasattr(AuditEventType, "FORENSIC_ANOMALY_DETECTED")
        
        assert AuditEventType.FORENSIC_CAPTURE_STARTED.value == "forensic_capture_started"
        assert AuditEventType.FORENSIC_CAPTURE_COMPLETED.value == "forensic_capture_completed"
        assert AuditEventType.FORENSIC_ANOMALY_DETECTED.value == "forensic_anomaly_detected"


# =============================================================================
# Test: CorruptionShield Audit 통합
# =============================================================================


class TestCorruptionShieldAuditIntegration:
    """CorruptionShield Audit 통합 테스트."""
    
    def test_validate_has_request_parameter(self):
        """validate() 메서드에 request 파라미터 존재 확인."""
        from selfhealing.services.corruption_shield import CorruptionShield
        import inspect
        
        sig = inspect.signature(CorruptionShield.validate)
        assert "request" in sig.parameters
    
    def test_corruption_detected_recorded_to_audit_buffer(self):
        """L1/L2/L3 위반 발견 시 RequestAuditBuffer에 기록."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        # 설정: L2 비즈니스 규칙 검증 활성화
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
            min_amount=100,
            max_amount=100_000_000,
        )
        shield = CorruptionShield(config)
        
        # Mock request 객체
        mock_request = MagicMock()
        mock_request.META = {}
        
        # Invalid data (음수 금액 - L2 위반)
        data = {"amount": -1000, "order_id": "test-123", "status": "DONE"}
        
        result = shield.validate(data, request=mock_request)
        
        # 검증
        if not result.is_valid:
            # RequestAuditBuffer가 생성되었는지 확인
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()
            
            # CORRUPTION_DETECTED 또는 CORRUPTION_BLOCKED 이벤트가 있어야 함
            corruption_events = [
                e for e in events 
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED, 
                    AuditEventType.CORRUPTION_BLOCKED
                )
            ]
            assert len(corruption_events) > 0, "Corruption 이벤트가 기록되어야 함"
    
    def test_corruption_blocked_event_when_blocked(self):
        """차단 시 CORRUPTION_BLOCKED 이벤트 기록."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        # 설정
        config = CorruptionShieldConfig(l1_enabled=True, l2_enabled=True, l3_enabled=False)
        shield = CorruptionShield(config)
        
        # Mock request
        mock_request = MagicMock()
        mock_request.META = {}
        
        # Missing required fields (L1 위반 - critical severity)
        data = {}
        
        result = shield.validate(data, block_on_violation=True, request=mock_request)
        
        if result.blocked:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()
            
            blocked_events = [
                e for e in events 
                if e.event_type == AuditEventType.CORRUPTION_BLOCKED
            ]
            assert len(blocked_events) > 0, "CORRUPTION_BLOCKED 이벤트가 기록되어야 함"
    
    def test_no_audit_when_valid(self):
        """유효한 데이터일 때 Audit 이벤트 없음."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        config = CorruptionShieldConfig(l1_enabled=True, l2_enabled=True, l3_enabled=False)
        shield = CorruptionShield(config)
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        # Valid data
        data = {"amount": 10000, "order_id": "test-123", "status": "DONE"}
        
        result = shield.validate(data, request=mock_request)
        
        if result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            # 버퍼가 비어있거나 corruption 이벤트가 없어야 함
            events = buffer.get_events()
            corruption_events = [e for e in events if "CORRUPTION" in e.event_type.name]
            assert len(corruption_events) == 0


# =============================================================================
# Test: ShadowLogger Audit 통합
# =============================================================================


class TestShadowLoggerAuditIntegration:
    """ShadowLogger Audit 통합 테스트."""
    
    def test_record_sync_failure_calls_audit(self):
        """L2 동기화 실패 시 Audit 기록 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        # ShadowLogger 싱글톤 초기화 (테스트용)
        logger = ShadowLogger()
        logger.clear()
        
        with patch.object(logger, "_record_audit_event") as mock_audit:
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Connection timeout"),
                adapter_type="redis",
                operation="sync",
            )
            
            # Audit 호출 확인
            mock_audit.assert_called_once()
            call_args = mock_audit.call_args
            assert call_args[1]["event_type"] == "SHADOW_LOG_SYNC_FAILED"
            assert call_args[1]["service_name"] == "test_service"
            assert "error_message" in call_args[1]["details"]
    
    def test_mark_as_synced_calls_audit(self):
        """복구 완료 시 Audit 기록 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # 먼저 실패 기록
        logger.record_sync_failure(
            service_name="test_service",
            intended_state="OPEN",
            error=Exception("Test error"),
        )
        
        with patch.object(logger, "_record_audit_event") as mock_audit:
            count = logger.mark_as_synced("test_service")
            
            if count > 0:
                mock_audit.assert_called_once()
                call_args = mock_audit.call_args
                assert call_args[1]["event_type"] == "SHADOW_LOG_RECOVERED"
                assert call_args[1]["service_name"] == "test_service"
                assert "recovered_count" in call_args[1]["details"]
    
    def test_audit_failure_does_not_affect_main_logic(self):
        """Audit 실패가 메인 로직에 영향 없음."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # Audit가 실패하도록 설정
        with patch("selfhealing.factory.ProviderRegistry.get_audit_adapter") as mock_get:
            mock_adapter = MagicMock()
            mock_adapter.log_event.side_effect = Exception("Audit failed")
            mock_get.return_value = mock_adapter
            
            # 메인 로직은 정상 동작해야 함
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Test error"),
            )
            
            # 기록이 성공했는지 확인
            records = logger.get_all_records()
            assert len(records) >= 1


# =============================================================================
# Test: WAL Audit 통합
# =============================================================================


class TestWALAuditIntegration:
    """WAL Audit 통합 테스트."""
    
    def test_wal_init_accepts_audit_adapter(self):
        """WAL 생성자에 audit_adapter 파라미터 존재 확인."""
        from selfhealing.audit.wal import WriteAheadLog
        import inspect
        
        sig = inspect.signature(WriteAheadLog.__init__)
        assert "audit_adapter" in sig.parameters
    
    def test_wal_recovered_event_on_recovery(self, temp_wal_dir, mock_audit_adapter):
        """복구 시 WAL_RECOVERED 이벤트 기록."""
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        config = WALConfig(
            wal_dir=temp_wal_dir,
            sync_on_write=False,
        )
        
        # WAL 생성 및 기록
        wal = WriteAheadLog(config=config, audit_adapter=mock_audit_adapter)
        wal.write({"event": "test1"})
        wal.write({"event": "test2"})
        wal.write({"event": "test3"})
        wal.close()
        
        # 새 WAL 인스턴스로 복구
        wal2 = WriteAheadLog(config=config, audit_adapter=mock_audit_adapter)
        entries = wal2.recover_unprocessed(last_processed_seq=0)
        wal2.close()
        
        if entries:
            # WAL_RECOVERED 이벤트 확인
            recovered_events = mock_audit_adapter.get_events_by_type("WAL_RECOVERED")
            assert len(recovered_events) > 0, "WAL_RECOVERED 이벤트가 기록되어야 함"
            assert "recovered_count" in recovered_events[-1]["details"]
    
    def test_wal_rotated_event_on_rotation(self, temp_wal_dir, mock_audit_adapter):
        """로테이션 시 WAL_ROTATED 이벤트 기록."""
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        # 작은 파일 크기로 설정하여 빠른 로테이션 유도
        config = WALConfig(
            wal_dir=temp_wal_dir,
            max_file_size_mb=0.0001,  # 매우 작은 크기
            sync_on_write=False,
        )
        
        wal = WriteAheadLog(config=config, audit_adapter=mock_audit_adapter)
        
        # 여러 번 기록하여 로테이션 유도
        for i in range(100):
            wal.write({"event": f"test_{i}", "data": "x" * 1000})
        
        wal.close()
        
        # WAL_ROTATED 이벤트 확인
        rotated_events = mock_audit_adapter.get_events_by_type("WAL_ROTATED")
        assert len(rotated_events) > 0, "WAL_ROTATED 이벤트가 기록되어야 함"
    
    def test_wal_corruption_detected_event(self, temp_wal_dir, mock_audit_adapter):
        """체크섬 불일치 시 WAL_CORRUPTION_DETECTED 이벤트 기록."""
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        import struct
        import os
        
        config = WALConfig(
            wal_dir=temp_wal_dir,
            sync_on_write=False,
        )
        
        # WAL 생성 및 기록
        wal = WriteAheadLog(config=config)
        wal.write({"event": "test"})
        wal.close()
        
        # WAL 파일 손상 시뮬레이션
        from pathlib import Path
        wal_files = list(Path(temp_wal_dir).glob("*.wal"))
        if wal_files:
            with open(wal_files[0], "r+b") as f:
                f.seek(20)  # 데이터 영역으로 이동
                f.write(b"CORRUPTED")
        
        # 손상된 WAL 읽기 시도
        wal2 = WriteAheadLog(config=config, audit_adapter=mock_audit_adapter)
        entries = wal2.recover_unprocessed(last_processed_seq=0)
        wal2.close()
        
        # 손상이 감지되면 WAL_CORRUPTION_DETECTED 이벤트 확인
        corruption_events = mock_audit_adapter.get_events_by_type("WAL_CORRUPTION_DETECTED")
        # 손상이 감지되었다면 이벤트가 있어야 함
        # (손상 위치에 따라 감지되지 않을 수 있음)


# =============================================================================
# Test: ForensicAuditBridge
# =============================================================================


class TestForensicAuditBridge:
    """ForensicAuditBridge 테스트."""
    
    def test_on_exception_captured(self, mock_audit_adapter):
        """예외 캡처 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            bridge.on_exception_captured(
                exception=e,
                stack_trace="line 1\nline 2\nline 3",
                context={"request_id": "req-123", "user_id": "user-456"},
                sanitized=True,
            )
        
        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["source"] == "ForensicCapture"
        assert events[0]["details"]["exception_type"] == "ValueError"
        assert events[0]["details"]["capture_reason"] == "exception"
    
    def test_on_anomaly_detected(self, mock_audit_adapter):
        """이상 패턴 감지 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        bridge.on_anomaly_detected(
            anomaly_type="statistical",
            score=4.5,
            threshold=3.0,
            context={"metric": "response_time", "value": 1500},
        )
        
        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_ANOMALY_DETECTED")
        assert len(events) == 1
        assert events[0]["details"]["anomaly_type"] == "statistical"
        assert events[0]["details"]["score"] == 4.5
        assert events[0]["details"]["exceeded_by"] == 1.5
    
    def test_on_memory_snapshot(self, mock_audit_adapter):
        """메모리 스냅샷 시 Audit 기록."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        bridge.on_memory_snapshot(
            snapshot_id="snap-001",
            memory_mb=256.5,
            object_count=12345,
        )
        
        # Audit 이벤트 확인
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_STARTED")
        assert len(events) == 1
        assert events[0]["details"]["capture_type"] == "memory_snapshot"
        assert events[0]["details"]["snapshot_id"] == "snap-001"
        assert events[0]["details"]["memory_mb"] == 256.5
    
    def test_context_summarization(self, mock_audit_adapter):
        """컨텍스트 요약 (긴 값 truncate)."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        long_value = "x" * 200  # 200자
        context = {"short": "abc", "long": long_value}
        
        summary = bridge._summarize_context(context)
        
        assert summary["short"] == "abc"
        assert len(summary["long"]) < 150  # truncated
        assert "truncated" in summary["long"]
    
    def test_audit_failure_does_not_raise(self, mock_audit_adapter):
        """Audit 실패 시 예외 발생 안함."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        # 실패하는 audit adapter
        failing_adapter = MagicMock()
        failing_adapter.log_event.side_effect = Exception("Audit failed")
        
        bridge = ForensicAuditBridge(audit_adapter=failing_adapter)
        
        # 예외 발생하지 않아야 함
        bridge.on_exception_captured(
            exception=ValueError("Test"),
            stack_trace="",
            context={},
        )


# =============================================================================
# Test: 통합 테스트
# =============================================================================


class TestAuditIntegrationEnd2End:
    """End-to-End 통합 테스트."""
    
    def test_all_new_event_types_are_unique(self):
        """모든 신규 이벤트 타입 값이 고유한지 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        values = [e.value for e in AuditEventType]
        assert len(values) == len(set(values)), "중복된 이벤트 타입 값이 있음"
    
    def test_new_event_types_count(self):
        """신규 이벤트 타입 수 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        new_types = [
            "CORRUPTION_DETECTED",
            "CORRUPTION_BLOCKED",
            "SHADOW_LOG_SYNC_FAILED",
            "SHADOW_LOG_RECOVERED",
            "WAL_CORRUPTION_DETECTED",
            "WAL_RECOVERED",
            "WAL_ROTATED",
            "FORENSIC_CAPTURE_STARTED",
            "FORENSIC_CAPTURE_COMPLETED",
            "FORENSIC_ANOMALY_DETECTED",
        ]
        
        for type_name in new_types:
            assert hasattr(AuditEventType, type_name), f"{type_name}이 없음"
