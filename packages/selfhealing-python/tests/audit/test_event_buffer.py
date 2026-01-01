"""
Test Event Buffer - 순수 Python 테스트 (Django 불필요)

56_AUDIT_MIDDLEWARE_DESIGN.md 구현 테스트:
- AuditEventType: 이벤트 유형 enum
- AuditEvent: 이벤트 데이터 클래스
- RequestAuditBuffer: 이벤트 버퍼

Django 관련 테스트는 tests/self_healing/unit/test_audit_middleware.py에 있습니다.

Author: SelfHealing Team
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock


# =============================================================================
# Test AuditEventType
# =============================================================================

class TestAuditEventType:
    """AuditEventType enum 테스트."""
    
    def test_event_types_exist(self):
        """모든 필수 이벤트 타입이 존재하는지 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        required_types = [
            "DLQ_STORE",
            "DLQ_REPLAY",
            "CB_STATE_CHANGE",
            "CB_REJECTION",
            "GOVERNANCE_BLOCKED",
            "RATE_LIMITED",
            "POOL_CB_REJECTION",
            "ERROR_DETECTED",
            "CONFIG_CHANGE",
        ]
        
        for type_name in required_types:
            assert hasattr(AuditEventType, type_name), f"{type_name} should exist"
    
    def test_event_type_values(self):
        """이벤트 타입 값이 올바른지 확인."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert AuditEventType.DLQ_STORE.value == "dlq_store"
        assert AuditEventType.CB_STATE_CHANGE.value == "circuit_breaker_state_change"
        assert AuditEventType.POOL_CB_REJECTION.value == "pool_circuit_breaker_rejection"


# =============================================================================
# Test AuditEvent
# =============================================================================

class TestAuditEvent:
    """AuditEvent 데이터 클래스 테스트."""
    
    def test_create_event_with_defaults(self):
        """기본값으로 이벤트 생성."""
        from selfhealing.audit.event_buffer import AuditEvent, AuditEventType
        
        event = AuditEvent(
            event_type=AuditEventType.DLQ_STORE,
            source="TestService",
        )
        
        assert event.event_type == AuditEventType.DLQ_STORE
        assert event.source == "TestService"
        assert event.success is True
        assert event.actor_type == "system"
        assert isinstance(event.timestamp, datetime)
    
    def test_create_event_with_details(self):
        """상세 정보와 함께 이벤트 생성."""
        from selfhealing.audit.event_buffer import AuditEvent, AuditEventType
        
        event = AuditEvent(
            event_type=AuditEventType.DLQ_STORE,
            source="DLQService",
            details={"dlq_id": 123, "domain": "payment"},
            success=True,
            domain="payment",
            target_id="123",
        )
        
        assert event.details["dlq_id"] == 123
        assert event.domain == "payment"
        assert event.target_id == "123"
    
    def test_create_failed_event(self):
        """실패 이벤트 생성."""
        from selfhealing.audit.event_buffer import AuditEvent, AuditEventType
        
        event = AuditEvent(
            event_type=AuditEventType.ERROR_DETECTED,
            source="View",
            success=False,
            error_message="Internal Server Error",
        )
        
        assert event.success is False
        assert event.error_message == "Internal Server Error"
    
    def test_event_to_dict(self):
        """이벤트의 to_dict() 메서드 테스트."""
        from selfhealing.audit.event_buffer import AuditEvent, AuditEventType
        
        event = AuditEvent(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="CircuitBreaker",
            details={"cb_name": "payment", "new_state": "open"},
        )
        
        event_dict = event.to_dict()
        
        assert event_dict["event_type"] == "circuit_breaker_state_change"
        assert event_dict["source"] == "CircuitBreaker"
        assert "cb_name" in event_dict["details"]
        assert "timestamp" in event_dict
    
    def test_event_repr(self):
        """이벤트의 __repr__ 테스트."""
        from selfhealing.audit.event_buffer import AuditEvent, AuditEventType
        
        event = AuditEvent(
            event_type=AuditEventType.DLQ_STORE,
            source="Test",
        )
        
        repr_str = repr(event)
        assert "dlq_store" in repr_str
        assert "Test" in repr_str


# =============================================================================
# Test RequestAuditBuffer
# =============================================================================

class TestRequestAuditBuffer:
    """RequestAuditBuffer 클래스 테스트."""
    
    def test_create_empty_buffer(self):
        """빈 버퍼 생성."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        buffer = RequestAuditBuffer()
        
        assert buffer.has_events() is False
        assert buffer.event_count() == 0
        assert buffer.request_id is None
    
    def test_add_event_to_buffer(self):
        """버퍼에 이벤트 추가."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        
        event = buffer.add(
            event_type=AuditEventType.DLQ_STORE,
            source="TestService",
            details={"dlq_id": 1},
        )
        
        assert buffer.has_events() is True
        assert buffer.event_count() == 1
        assert event.event_type == AuditEventType.DLQ_STORE
    
    def test_add_multiple_events(self):
        """버퍼에 여러 이벤트 추가."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="A")
        buffer.add(event_type=AuditEventType.CB_STATE_CHANGE, source="B")
        buffer.add(event_type=AuditEventType.RATE_LIMITED, source="C")
        
        assert buffer.event_count() == 3
    
    def test_add_event_with_all_params(self):
        """모든 파라미터로 이벤트 추가."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        
        event = buffer.add(
            event_type=AuditEventType.GOVERNANCE_BLOCKED,
            source="GovernanceGuard",
            details={"action": "auto_replay"},
            actor_id="user123",
            actor_type="user",
            success=False,
            error_message="Kill switch active",
            target_type="replay_service",
            target_id="replay-1",
            domain="payment",
            reason="kill_switch_active",
        )
        
        assert event.actor_id == "user123"
        assert event.actor_type == "user"
        assert event.success is False
        assert event.domain == "payment"
    
    def test_get_events_returns_copy(self):
        """get_events()가 복사본을 반환하는지 확인."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="Test")
        
        events = buffer.get_events()
        events.clear()  # 복사본 수정
        
        # 원본은 영향 없음
        assert buffer.event_count() == 1
    
    def test_get_events_by_type(self):
        """특정 타입의 이벤트만 가져오기."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="A")
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="B")
        buffer.add(event_type=AuditEventType.CB_STATE_CHANGE, source="C")
        
        dlq_events = buffer.get_events_by_type(AuditEventType.DLQ_STORE)
        
        assert len(dlq_events) == 2
    
    def test_get_failed_events(self):
        """실패 이벤트만 가져오기."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="A", success=True)
        buffer.add(event_type=AuditEventType.ERROR_DETECTED, source="B", success=False)
        buffer.add(event_type=AuditEventType.GOVERNANCE_BLOCKED, source="C", success=False)
        
        failed_events = buffer.get_failed_events()
        
        assert len(failed_events) == 2
    
    def test_set_request_metadata(self):
        """요청 메타데이터 설정."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        buffer = RequestAuditBuffer()
        buffer.set_request_metadata(
            path="/api/test/",
            method="POST",
            user_id="user123",
        )
        
        assert buffer._path == "/api/test/"
        assert buffer._method == "POST"
        assert buffer._user_id == "user123"
    
    def test_get_elapsed_seconds(self):
        """경과 시간 계산."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        import time
        
        buffer = RequestAuditBuffer()
        time.sleep(0.01)  # 10ms
        
        elapsed = buffer.get_elapsed_seconds()
        
        assert elapsed >= 0.01
    
    def test_buffer_to_dict(self):
        """버퍼 전체를 딕셔너리로 변환."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        buffer.request_id = "test-request-123"
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="Test")
        
        buffer_dict = buffer.to_dict()
        
        assert buffer_dict["request_id"] == "test-request-123"
        assert buffer_dict["event_count"] == 1
        assert len(buffer_dict["events"]) == 1
        assert "elapsed_seconds" in buffer_dict
    
    def test_get_or_create_with_mock_request(self):
        """Mock request에서 버퍼 가져오기/생성."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        # Mock Django request
        mock_request = MagicMock()
        mock_request.META = {}
        
        # 첫 번째 호출: 생성
        buffer1 = RequestAuditBuffer.get_or_create(mock_request)
        assert buffer1 is not None
        
        # 두 번째 호출: 기존 버퍼 반환
        buffer2 = RequestAuditBuffer.get_or_create(mock_request)
        assert buffer1 is buffer2
    
    def test_get_returns_none_when_no_buffer(self):
        """버퍼가 없으면 None 반환."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        result = RequestAuditBuffer.get(mock_request)
        
        assert result is None
    
    def test_exists_check(self):
        """버퍼 존재 여부 확인."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        assert RequestAuditBuffer.exists(mock_request) is False
        
        RequestAuditBuffer.get_or_create(mock_request)
        
        assert RequestAuditBuffer.exists(mock_request) is True
    
    def test_clear_buffer(self):
        """버퍼 초기화."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer()
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="Test")
        buffer.add(event_type=AuditEventType.CB_STATE_CHANGE, source="Test")
        
        assert buffer.event_count() == 2
        
        buffer.clear()
        
        assert buffer.event_count() == 0
    
    def test_get_or_create_without_meta(self):
        """META 속성이 없는 request 처리."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        # META 속성이 없는 객체
        mock_request = MagicMock(spec=[])  # META 없음
        
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        
        # 새 버퍼가 생성됨
        assert buffer is not None
        assert isinstance(buffer, RequestAuditBuffer)


# =============================================================================
# Test add_audit_event Function
# =============================================================================

class TestAddAuditEventFunction:
    """add_audit_event 편의 함수 테스트."""
    
    def test_add_audit_event_to_request(self):
        """request에 이벤트 추가."""
        from selfhealing.audit.event_buffer import add_audit_event, AuditEventType, RequestAuditBuffer
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        event = add_audit_event(
            mock_request,
            AuditEventType.DLQ_STORE,
            "TestService",
            details={"test": True},
        )
        
        assert event is not None
        assert event.event_type == AuditEventType.DLQ_STORE
        
        # 버퍼에 추가되었는지 확인
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer.event_count() == 1
    
    def test_add_audit_event_multiple_times(self):
        """여러 번 이벤트 추가."""
        from selfhealing.audit.event_buffer import add_audit_event, AuditEventType, RequestAuditBuffer
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        add_audit_event(mock_request, AuditEventType.DLQ_STORE, "A")
        add_audit_event(mock_request, AuditEventType.CB_STATE_CHANGE, "B")
        add_audit_event(mock_request, AuditEventType.ERROR_DETECTED, "C")
        
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer.event_count() == 3


# =============================================================================
# Test audit_helpers Hybrid Logic (No Django required)
# =============================================================================

class TestAuditHelpersHybridNoRequest:
    """audit_helpers 하이브리드 로직 테스트 (request 없음)."""
    
    def test_log_dlq_store_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_dlq_store_audit
        
        # request 없이 호출 - 에러 없이 완료되어야 함
        log_dlq_store_audit(
            dlq_id=456,
            domain="point",
            failure_type="TIMEOUT",
        )
        # 에러 없이 완료되면 성공
    
    def test_log_dlq_replay_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_dlq_replay_audit
        
        log_dlq_replay_audit(
            dlq_id=789,
            domain="payment",
            success=True,
        )
        # 에러 없이 완료되면 성공
    
    def test_log_cb_state_change_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_cb_state_change_audit
        
        log_cb_state_change_audit(
            cb_name="payment_cb",
            old_state="closed",
            new_state="open",
        )
        # 에러 없이 완료되면 성공
    
    def test_log_governance_blocked_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_governance_blocked_audit
        
        log_governance_blocked_audit(
            action="auto_replay",
            block_reason="kill_switch_active",
        )
        # 에러 없이 완료되면 성공
    
    def test_log_rate_limited_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_rate_limited_audit
        
        log_rate_limited_audit(
            client_ip="192.168.1.100",
            endpoint="/api/test/",
            limit_type="global",
        )
        # 에러 없이 완료되면 성공
    
    def test_log_pool_cb_rejection_without_request(self):
        """request 없이 호출 - 직접 로깅."""
        from selfhealing.services.audit_helpers import log_pool_cb_rejection_audit
        
        log_pool_cb_rejection_audit(
            pool_name="default",
            current_utilization=0.95,
            threshold=0.90,
        )
        # 에러 없이 완료되면 성공
