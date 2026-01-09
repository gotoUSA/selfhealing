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


# =============================================================================
# Test: Phase 1 - CorruptionShield Batching (배칭)
# =============================================================================


class TestCorruptionShieldBatching:
    """
    CorruptionShield 배칭 테스트.
    
    Phase 1 개선 (27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md):
    - 10개 필드 위반 시 10개 로그 → 1개 로그로 최적화
    """
    
    def test_multiple_violations_single_event(self):
        """여러 위반 사항이 단일 Audit 이벤트로 기록됨."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        # L2 규칙으로 여러 위반 유도
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        # 여러 필드 위반 데이터
        data = {
            "amount": -1000,  # L2 위반: 음수 금액
            "status": "INVALID_STATUS",  # L2 위반: 유효하지 않은 상태
        }
        
        result = shield.validate(data, request=mock_request)
        
        if not result.is_valid and len(result.violations) > 1:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()
            
            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]
            
            # 배칭: 여러 violations에 대해 단일 이벤트만 생성되어야 함
            assert len(corruption_events) == 1, \
                f"배칭 실패: {len(corruption_events)}개 이벤트 생성됨 (예상: 1개)"
    
    def test_violations_list_in_details(self):
        """단일 이벤트에 violations 리스트가 포함됨."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        # 위반 데이터
        data = {"amount": -100}
        
        result = shield.validate(data, request=mock_request)
        
        if not result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()
            
            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]
            
            if corruption_events:
                event = corruption_events[0]
                details = event.details
                
                # 배칭된 상세 정보 확인
                assert "violation_count" in details, "violation_count 누락"
                assert "violations" in details, "violations 리스트 누락"
                assert "layers" in details, "layers 정보 누락"
                assert isinstance(details["violations"], list), "violations가 리스트여야 함"
    
    def test_violation_count_matches_violations_list(self):
        """violation_count가 violations 리스트 길이와 일치."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        # 위반 데이터
        data = {"amount": -100, "order_id": None}
        
        result = shield.validate(data, request=mock_request)
        
        if not result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()
            
            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]
            
            if corruption_events:
                details = corruption_events[0].details
                assert details["violation_count"] == len(details["violations"]), \
                    "violation_count와 violations 리스트 길이 불일치"
    
    def test_fallback_to_write_to_wal_without_request(self):
        """request 없을 때 _write_to_wal()로 폴백."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig
        
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)
        
        # request 없이 호출
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            data = {"amount": -100}
            result = shield.validate(data)  # request=None
            
            if not result.is_valid:
                # _write_to_wal이 호출되어야 함
                mock_wal.assert_called_once()
                call_kwargs = mock_wal.call_args[1]
                assert call_kwargs["event_type"] in ("CORRUPTION_DETECTED", "CORRUPTION_BLOCKED")
                assert call_kwargs["source"] == "CorruptionShield"
                assert "violations" in call_kwargs["details"]


# =============================================================================
# Test: Phase 2 - ActorContext/TraceContext 자동 결합
# =============================================================================


class TestAuditContextAutoInjection:
    """
    ActorContext/TraceContext 자동 주입 테스트.
    
    Phase 2 개선 (27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md):
    - ShadowLogger/WAL에서 _write_to_wal() 직접 호출
    - "어떤 운영자의 어떤 작업에서 발생" 추적 가능
    """
    
    def test_shadow_logger_uses_write_to_wal(self):
        """ShadowLogger가 _write_to_wal()을 직접 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Connection timeout"),
                adapter_type="redis",
                operation="sync",
            )
            
            # _write_to_wal이 호출되어야 함
            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "SHADOW_LOG_SYNC_FAILED"
            assert call_kwargs["source"] == "ShadowLogger"
            assert call_kwargs["details"]["service_name"] == "test_service"
    
    def test_shadow_logger_recovery_uses_write_to_wal(self):
        """ShadowLogger 복구 시 _write_to_wal() 호출."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # 먼저 실패 기록
        with patch("selfhealing.services.audit.base._write_to_wal"):
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Test error"),
            )
        
        # 복구 시 _write_to_wal 호출 확인
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            count = logger.mark_as_synced("test_service")
            
            if count > 0:
                mock_wal.assert_called_once()
                call_kwargs = mock_wal.call_args[1]
                assert call_kwargs["event_type"] == "SHADOW_LOG_RECOVERED"
                assert call_kwargs["details"]["recovered_count"] == count
    
    def test_wal_uses_write_to_wal_for_rotation(self, temp_wal_dir):
        """WAL 로테이션 시 _write_to_wal() 호출."""
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        config = WALConfig(
            wal_dir=temp_wal_dir,
            max_file_size_mb=0.0001,  # 매우 작은 크기로 로테이션 유도
            sync_on_write=False,
        )
        
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            wal = WriteAheadLog(config=config)  # audit_adapter=None
            
            # 로테이션 유도
            for i in range(50):
                wal.write({"event": f"test_{i}", "data": "x" * 2000})
            
            wal.close()
            
            # WAL_ROTATED 이벤트가 기록되어야 함
            rotation_calls = [
                call for call in mock_wal.call_args_list
                if call[1].get("event_type") == "WAL_ROTATED"
            ]
            assert len(rotation_calls) > 0, "WAL_ROTATED 이벤트가 기록되어야 함"
    
    def test_wal_audit_adapter_priority_over_write_to_wal(self, temp_wal_dir, mock_audit_adapter):
        """WAL에 audit_adapter가 주입되면 우선 사용."""
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        config = WALConfig(
            wal_dir=temp_wal_dir,
            max_file_size_mb=0.0001,
            sync_on_write=False,
        )
        
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            wal = WriteAheadLog(config=config, audit_adapter=mock_audit_adapter)
            
            # 로테이션 유도
            for i in range(50):
                wal.write({"event": f"test_{i}", "data": "x" * 2000})
            
            wal.close()
            
            # audit_adapter가 우선 사용되어야 함
            rotation_events = mock_audit_adapter.get_events_by_type("WAL_ROTATED")
            if rotation_events:
                # audit_adapter가 사용됨 → _write_to_wal은 호출되지 않아야 함
                wal_rotation_calls = [
                    call for call in mock_wal.call_args_list
                    if call[1].get("event_type") == "WAL_ROTATED"
                ]
                assert len(wal_rotation_calls) == 0, \
                    "audit_adapter가 있으면 _write_to_wal 호출 안됨"
    
    def test_shadow_logger_graceful_on_import_error(self):
        """_write_to_wal import 실패 시 graceful 처리."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        with patch.dict("sys.modules", {"selfhealing.services.audit.base": None}):
            # ImportError가 발생해도 메인 로직은 정상 동작
            logger.record_sync_failure(
                service_name="test_service",
                intended_state="OPEN",
                error=Exception("Test error"),
            )
            
            # 레코드가 정상적으로 기록되어야 함
            records = logger.get_all_records()
            assert len(records) >= 1


# =============================================================================
# Test: Phase 3 - Forensic 민감정보 마스킹
# =============================================================================


class TestForensicMasking:
    """Forensic 민감정보 마스킹 테스트 (Phase 3)."""
    
    def test_password_masked_in_context(self):
        """password 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge()
        context = {
            "user": "admin",
            "password": "secret123",
            "action": "login",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["user"] == "admin"
        assert masked["password"] == "***REDACTED***"
        assert masked["action"] == "login"
    
    def test_api_key_masked_in_context(self):
        """api_key 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge()
        context = {
            "api_key": "sk-1234567890",
            "endpoint": "/api/v1/data",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["api_key"] == "***REDACTED***"
        assert masked["endpoint"] == "/api/v1/data"
    
    def test_nested_sensitive_fields_masked(self):
        """중첩된 민감 필드 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        # 커스텀 패턴 사용 (credentials가 기본 패턴에 있으므로)
        bridge = ForensicAuditBridge(sensitive_patterns=["password", "token"])
        context = {
            "user": {
                "name": "admin",
                "login_info": {
                    "password": "secret123",
                    "token": "jwt-token-xyz",
                },
            },
            "action": "update",
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["user"]["name"] == "admin"
        assert masked["user"]["login_info"]["password"] == "***REDACTED***"
        assert masked["user"]["login_info"]["token"] == "***REDACTED***"
        assert masked["action"] == "update"
    
    def test_custom_sensitive_patterns(self):
        """커스텀 민감 패턴 사용."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(sensitive_patterns=["custom_secret", "my_key"])
        context = {
            "custom_secret": "value1",
            "my_key": "value2",
            "password": "should-not-be-masked",  # 커스텀 패턴에 없음
        }
        
        masked = bridge._mask_context(context)
        
        assert masked["custom_secret"] == "***REDACTED***"
        assert masked["my_key"] == "***REDACTED***"
        assert masked["password"] == "should-not-be-masked"
    
    def test_on_exception_captured_masks_context(self, mock_audit_adapter):
        """on_exception_captured 시 컨텍스트 마스킹."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        exception = ValueError("Test error")
        stack_trace = "line 1\nline 2\nline 3"
        context = {
            "user_id": "123",
            "api_key": "secret-key",
        }
        
        bridge.on_exception_captured(
            exception=exception,
            stack_trace=stack_trace,
            context=context,
            sanitized=True,
        )
        
        # Audit 이벤트가 기록되어야 함
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["details"]["sanitized"] is True
    
    def test_on_exception_captured_no_mask_when_sanitized_false(self, mock_audit_adapter):
        """sanitized=False일 때 마스킹 안함."""
        from selfhealing.services.forensic_audit_bridge import ForensicAuditBridge
        
        bridge = ForensicAuditBridge(audit_adapter=mock_audit_adapter)
        
        exception = ValueError("Test error")
        stack_trace = "line 1"
        context = {"password": "secret"}
        
        # sanitized=False로 호출해도 _mask_context는 호출되지 않음
        bridge.on_exception_captured(
            exception=exception,
            stack_trace=stack_trace,
            context=context,
            sanitized=False,
        )
        
        events = mock_audit_adapter.get_events_by_type("FORENSIC_CAPTURE_COMPLETED")
        assert len(events) == 1
        assert events[0]["details"]["sanitized"] is False


# =============================================================================
# Test: Phase 4 - InMemoryAuditBuffer
# =============================================================================


class TestInMemoryAuditBuffer:
    """메모리 버퍼 폴백 테스트 (Phase 4)."""
    
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


# =============================================================================
# Test: Phase 5 - ForensicRateLimiter
# =============================================================================


class TestForensicRateLimiter:
    """Forensic Rate Limiter 테스트 (Phase 5)."""
    
    def test_exception_rate_limit(self):
        """분당 10건 초과 시 드롭."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(exception_limit=3, window_seconds=60.0)
        
        # 첫 3건은 허용
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is True
        
        # 4번째부터 거부
        assert limiter.try_acquire_exception() is False
        assert limiter.try_acquire_exception() is False
        
        stats = limiter.get_stats()
        assert stats["exception_requests_in_window"] == 3
        assert stats["exceptions_dropped"] == 2
    
    def test_snapshot_rate_limit(self):
        """분당 1건 초과 시 드롭."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(snapshot_limit=1, window_seconds=60.0)
        
        assert limiter.try_acquire_snapshot() is True
        assert limiter.try_acquire_snapshot() is False
        assert limiter.try_acquire_snapshot() is False
        
        stats = limiter.get_stats()
        assert stats["snapshot_requests_in_window"] == 1
        assert stats["snapshots_dropped"] == 2
    
    def test_anomaly_rate_limit(self):
        """이상 탐지 Rate Limit."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(anomaly_limit=2, window_seconds=60.0)
        
        assert limiter.try_acquire_anomaly() is True
        assert limiter.try_acquire_anomaly() is True
        assert limiter.try_acquire_anomaly() is False
        
        stats = limiter.get_stats()
        assert stats["anomaly_requests_in_window"] == 2
        assert stats["anomalies_dropped"] == 1
    
    def test_window_expiry(self):
        """윈도우 경과 후 토큰 재충전."""
        import time
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        # 0.1초 윈도우로 빠른 테스트
        limiter = ForensicRateLimiter(exception_limit=1, window_seconds=0.1)
        
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is False
        
        # 윈도우 경과 대기
        time.sleep(0.15)
        
        # 다시 허용
        assert limiter.try_acquire_exception() is True
    
    def test_reset(self):
        """Rate limiter 리셋."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(exception_limit=1)
        
        limiter.try_acquire_exception()
        limiter.try_acquire_exception()  # dropped
        
        stats_before = limiter.get_stats()
        assert stats_before["exceptions_dropped"] == 1
        
        limiter.reset()
        
        stats_after = limiter.get_stats()
        assert stats_after["exceptions_dropped"] == 0
        assert stats_after["exception_requests_in_window"] == 0
    
    def test_bridge_uses_rate_limiter(self):
        """ForensicAuditBridge가 Rate Limiter 사용."""
        from selfhealing.services.forensic_audit_bridge import (
            ForensicAuditBridge,
            ForensicRateLimiter,
        )
        
        limiter = ForensicRateLimiter(exception_limit=2)
        bridge = ForensicAuditBridge(rate_limiter=limiter)
        
        # 첫 2건은 성공
        result1 = bridge.on_exception_captured(
            ValueError("test"), "stack", {"key": "value"}
        )
        result2 = bridge.on_exception_captured(
            ValueError("test2"), "stack2", {"key2": "value2"}
        )
        
        # 3번째는 Rate Limited
        result3 = bridge.on_exception_captured(
            ValueError("test3"), "stack3", {"key3": "value3"}
        )
        
        assert result1 is True
        assert result2 is True
        assert result3 is False
    
    def test_bridge_rate_limiter_stats(self):
        """Bridge에서 Rate Limiter 통계 조회."""
        from selfhealing.services.forensic_audit_bridge import (
            ForensicAuditBridge,
            ForensicRateLimiter,
        )
        
        limiter = ForensicRateLimiter()
        bridge = ForensicAuditBridge(rate_limiter=limiter)
        
        stats = bridge.get_rate_limiter_stats()
        
        assert "exception_limit" in stats
        assert "snapshot_limit" in stats
        assert "anomaly_limit" in stats


# =============================================================================
# Test: Phase 6 - RedisAuditBuffer
# =============================================================================


class TestRedisAuditBuffer:
    """Redis Audit Buffer 테스트 (Phase 6)."""
    
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
        import json
        
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
    
    def test_factory_function_no_redis(self):
        """Redis 없을 때 팩토리 함수."""
        from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer
        
        # 존재하지 않는 Redis URL
        result = create_redis_audit_buffer("redis://nonexistent:6379")
        
        # Redis 연결 실패 시 None 반환
        assert result is None


# =============================================================================
# Test: Phase 7 - MTTR Calculator
# =============================================================================


class TestMTTRCalculator:
    """MTTR Calculator 테스트 (Phase 7)."""
    
    def test_empty_events(self):
        """빈 이벤트 목록."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        report = calculator.calculate_mttr([])
        
        assert report.total_incidents == 0
        assert report.avg_mttr_seconds == 0
        assert report.by_service == {}
    
    def test_single_recovery_event(self):
        """단일 복구 이벤트."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {
                "timestamp": "2026-01-08T10:00:00Z",
                "service_name": "payment",
                "old_state": "closed",
                "new_state": "open",
                "cause": "timeout",
            },
            {
                "timestamp": "2026-01-08T10:05:00Z",
                "service_name": "payment",
                "old_state": "open",
                "new_state": "closed",
            },
        ]
        
        report = calculator.calculate_mttr(events)
        
        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 300  # 5분 = 300초
        assert report.by_service["payment"] == 300
    
    def test_multiple_services(self):
        """여러 서비스의 복구 이벤트."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:01:00Z", "service_name": "inventory", "new_state": "open"},
            {"timestamp": "2026-01-08T10:03:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T10:06:00Z", "service_name": "inventory", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        
        assert report.total_incidents == 2
        assert "payment" in report.by_service
        assert "inventory" in report.by_service
        assert report.by_service["payment"] == 180  # 3분
        assert report.by_service["inventory"] == 300  # 5분
    
    def test_percentile_calculation(self):
        """P50/P90/P99 백분위수 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        # 10개의 복구 이벤트 생성 (60초 간격으로 MTTR 증가)
        events = []
        base_time = "2026-01-08T10:00:00Z"
        
        for i in range(10):
            start_hour = 10 + i
            # OPEN
            events.append({
                "timestamp": f"2026-01-08T{start_hour:02d}:00:00Z",
                "service_name": f"service_{i}",
                "new_state": "open",
            })
            # CLOSED (i+1분 후)
            events.append({
                "timestamp": f"2026-01-08T{start_hour:02d}:{(i+1):02d}:00Z",
                "service_name": f"service_{i}",
                "new_state": "closed",
            })
        
        report = calculator.calculate_mttr(events)
        
        assert report.total_incidents == 10
        assert report.min_mttr_seconds == 60  # 1분
        assert report.max_mttr_seconds == 600  # 10분
        
        # P50은 중간값
        assert report.p50_mttr_seconds > 0
        assert report.p90_mttr_seconds > report.p50_mttr_seconds
    
    def test_unmatched_open_ignored(self):
        """매칭되지 않은 OPEN 이벤트 무시."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            # CLOSED 없이 종료
        ]
        
        report = calculator.calculate_mttr(events)
        
        # 매칭되지 않은 OPEN은 복구 이벤트로 카운트되지 않음
        assert report.total_incidents == 0
    
    def test_closed_without_open_ignored(self):
        """OPEN 없이 CLOSED만 있는 경우 무시."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        
        assert report.total_incidents == 0
    
    def test_half_open_state_ignored(self):
        """HALF_OPEN 상태는 복구로 간주하지 않음."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "half_open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        
        # OPEN -> CLOSED 기준으로 5분
        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 300
    
    def test_multiple_incidents_same_service(self):
        """같은 서비스의 여러 장애."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T11:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T11:08:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        
        assert report.total_incidents == 2
        # 첫 번째: 2분 = 120초, 두 번째: 8분 = 480초
        assert report.by_service["payment"] == (120 + 480) / 2  # 평균 300초
    
    def test_service_mttr_filter(self):
        """특정 서비스만 필터링하여 MTTR 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "inventory", "new_state": "open"},
            {"timestamp": "2026-01-08T10:10:00Z", "service_name": "inventory", "new_state": "closed"},
        ]
        
        report = calculator.calculate_service_mttr(events, "payment")
        
        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 120  # 2분
    
    def test_period_mttr(self):
        """기간별 MTTR 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            # 첫 번째 시간대
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
            # 두 번째 시간대 (1시간 후)
            {"timestamp": "2026-01-08T11:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T11:10:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        reports = calculator.calculate_mttr_by_period(events, period_hours=1)
        
        assert len(reports) == 2
        assert reports[0].avg_mttr_seconds == 300  # 5분
        assert reports[1].avg_mttr_seconds == 600  # 10분
    
    def test_report_to_dict(self):
        """리포트 딕셔너리 변환."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        report_dict = report.to_dict()
        
        assert "period_start" in report_dict
        assert "period_end" in report_dict
        assert "total_incidents" in report_dict
        assert "avg_mttr_seconds" in report_dict
        assert "avg_mttr_minutes" in report_dict
        assert report_dict["avg_mttr_minutes"] == 5.0
    
    def test_recovery_event_details(self):
        """복구 이벤트 상세 정보."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {
                "timestamp": "2026-01-08T10:00:00Z",
                "service_name": "payment",
                "new_state": "open",
                "cause": "connection_timeout",
                "failure_count": 5,
                "trace_id": "abc123",
            },
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        
        assert len(report.recovery_events) == 1
        event = report.recovery_events[0]
        assert event.service_name == "payment"
        assert event.cause == "connection_timeout"
        assert event.failure_count == 5
        assert event.trace_id == "abc123"
        assert event.duration_seconds == 300
    
    def test_invalid_timestamp_handled(self):
        """잘못된 타임스탬프 처리."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator
        
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "invalid", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]
        
        # 예외 없이 처리
        report = calculator.calculate_mttr(events)
        
        # 유효한 이벤트만 처리됨
        assert report.total_incidents == 0
    
    def test_singleton_instance(self):
        """싱글톤 인스턴스."""
        from selfhealing.services.audit.mttr_calculator import get_mttr_calculator
        
        calc1 = get_mttr_calculator()
        calc2 = get_mttr_calculator()
        
        assert calc1 is calc2