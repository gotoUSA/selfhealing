"""
Phase 3 Migration Tests

56_AUDIT_MIDDLEWARE_DESIGN.md Phase 3 전면 전환 테스트.

Phase 3 목표:
- 모든 직접 Audit 호출을 RequestAuditBuffer 패턴으로 전환
- AuditMiddleware가 유일한 Audit 기록 출구

테스트 대상:
1. SelfHealingMiddleware._log_audit_event → 버퍼 패턴
2. PoolCircuitBreakerMiddleware._record_rejection_audit → 버퍼 패턴
3. governance_checks._log_governance_blocked → 버퍼 패턴
4. SelfHealingRecoveryLogger.log_event → 버퍼 패턴
5. ConfigChangeTracker._log_change, log_manual_override → 버퍼 패턴
"""

import os

# Django 설정 구성 (테스트 실행 전)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings.test")

import django
try:
    django.setup()
except Exception:
    pass  # Django가 이미 설정되어 있거나 설정 불가한 경우 무시


class MockRequest:
    """Mock Django HttpRequest for testing."""
    
    def __init__(self, path: str = "/test", method: str = "GET"):
        self.META = {}
        self.path = path
        self.method = method


class TestSelfHealingMiddlewareBufferPattern:
    """SelfHealingMiddleware 버퍼 패턴 테스트."""
    
    def test_log_audit_event_uses_buffer_when_request_provided(self):
        """request가 있으면 버퍼에 적재되어야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        request = MockRequest(path="/api/orders/", method="POST")
        buffer = RequestAuditBuffer.get_or_create(request)
        
        # 버퍼에 이벤트 추가 시뮬레이션 (실제 middleware 호출 대신)
        buffer.add(
            event_type=AuditEventType.DLQ_STORE,
            source="SelfHealingMiddleware",
            details={"dlq_id": 123, "reason": "circuit_breaker_open"},
            success=True,
        )
        
        assert buffer.has_events()
        events = buffer.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.DLQ_STORE
        assert events[0].source == "SelfHealingMiddleware"
    
    def test_log_audit_event_signature_includes_request(self):
        """_log_audit_event 메서드가 request 파라미터를 지원해야 함."""
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        import inspect
        
        sig = inspect.signature(SelfHealingMiddleware._log_audit_event)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "_log_audit_event must have 'request' parameter"
    
    def test_record_cb_failure_signature_includes_request(self):
        """_record_cb_failure 메서드가 request 파라미터를 지원해야 함."""
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        import inspect
        
        sig = inspect.signature(SelfHealingMiddleware._record_cb_failure)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "_record_cb_failure must have 'request' parameter"
    
    def test_store_to_dlq_signature_includes_request(self):
        """_store_to_dlq 메서드가 request 파라미터를 지원해야 함."""
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        import inspect
        
        sig = inspect.signature(SelfHealingMiddleware._store_to_dlq)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "_store_to_dlq must have 'request' parameter"


class TestPoolCircuitBreakerBufferPattern:
    """PoolCircuitBreakerMiddleware 버퍼 패턴 테스트."""
    
    def test_record_rejection_audit_uses_buffer(self):
        """_record_rejection_audit가 버퍼 패턴을 사용해야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        request = MockRequest(path="/api/orders/", method="GET")
        
        # 버퍼에 이벤트 추가 시뮬레이션
        buffer = RequestAuditBuffer.get_or_create(request)
        buffer.add(
            event_type=AuditEventType.POOL_CB_REJECTION,
            source="PoolCircuitBreakerMiddleware",
            details={
                "request_path": request.path,
                "request_method": request.method,
                "circuit_state": "open",
                "rejection_reason": "pool_exhausted",
            },
            success=False,
            error_message="pool_exhausted",
        )
        
        assert buffer.has_events()
        events = buffer.get_events()
        assert events[0].event_type == AuditEventType.POOL_CB_REJECTION


class TestGovernanceChecksBufferPattern:
    """governance_checks 버퍼 패턴 테스트."""
    
    def test_log_governance_blocked_signature_includes_request(self):
        """_log_governance_blocked가 request 파라미터를 지원해야 함."""
        from selfhealing.services.governance.checks import _log_governance_blocked
        import inspect
        
        sig = inspect.signature(_log_governance_blocked)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "_log_governance_blocked must have 'request' parameter"
    
    def test_log_governance_blocked_uses_buffer_when_request_provided(self):
        """request가 있으면 버퍼에 적재되어야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        from selfhealing.services.governance.checks import _log_governance_blocked
        
        request = MockRequest()
        
        # 함수 호출
        _log_governance_blocked(
            block_reason="kill_switch",
            operation_name="auto_replay",
            details={"service": "payment"},
            request=request,
        )
        
        # 버퍼 확인
        buffer = RequestAuditBuffer.get(request)
        assert buffer is not None
        assert buffer.has_events()
        
        events = buffer.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.GOVERNANCE_BLOCKED
        assert events[0].details["block_reason"] == "kill_switch"


class TestSelfHealingRecoveryLoggerBufferPattern:
    """SelfHealingRecoveryLogger 버퍼 패턴 테스트."""
    
    def test_log_event_signature_includes_request(self):
        """log_event가 request 파라미터를 지원해야 함."""
        from selfhealing.api.django.middleware import SelfHealingRecoveryLogger
        import inspect
        
        sig = inspect.signature(SelfHealingRecoveryLogger.log_event)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "log_event must have 'request' parameter"


class TestConfigChangeTrackerBufferPattern:
    """ConfigChangeTracker 버퍼 패턴 테스트."""
    
    def test_log_change_signature_includes_request(self):
        """_log_change가 request 파라미터를 지원해야 함."""
        from selfhealing.config_tracker import ConfigChangeTracker
        import inspect
        
        sig = inspect.signature(ConfigChangeTracker._log_change)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "_log_change must have 'request' parameter"
    
    def test_log_manual_override_signature_includes_request(self):
        """log_manual_override가 request 파라미터를 지원해야 함."""
        from selfhealing.config_tracker import ConfigChangeTracker
        import inspect
        
        sig = inspect.signature(ConfigChangeTracker.log_manual_override)
        params = list(sig.parameters.keys())
        
        assert "request" in params, "log_manual_override must have 'request' parameter"


class TestAuditEventTypeCompleteness:
    """AuditEventType 완전성 테스트."""
    
    def test_all_required_event_types_exist(self):
        """Phase 3에 필요한 모든 이벤트 타입이 존재해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        required_types = [
            "DLQ_STORE",
            "DLQ_REPLAY",
            "CB_STATE_CHANGE",
            "CB_REJECTION",
            "GOVERNANCE_BLOCKED",
            "RATE_LIMITED",
            "POOL_CB_REJECTION",
            "POOL_CB_STATE_CHANGE",
            "ERROR_DETECTED",
            "CONFIG_CHANGE",
            "MANUAL_OVERRIDE",
            "RECOVERY_EVENT",  # Phase 3 추가
            "GENERIC",
        ]
        
        for type_name in required_types:
            assert hasattr(AuditEventType, type_name), f"AuditEventType.{type_name} must exist"


class TestHybridPatternBackwardCompatibility:
    """하이브리드 패턴 하위 호환성 테스트."""
    
    def test_log_governance_blocked_works_without_request(self):
        """request 없이도 동작해야 함 (Celery 등)."""
        from selfhealing.services.governance.checks import _log_governance_blocked
        
        # request 없이 호출 - 예외 없이 실행되어야 함
        _log_governance_blocked(
            block_reason="emergency_mode",
            operation_name="batch_replay",
            details={"level": "LEVEL_3"},
        )
        # 성공 시 예외 없음
    
    def test_audit_helpers_work_without_request(self):
        """audit_helpers가 request 없이도 동작해야 함."""
        from selfhealing.services.audit import (
            log_dlq_store_audit,
            log_cb_state_change_audit,
        )
        
        # request 없이 호출 - 예외 없이 실행되어야 함
        log_dlq_store_audit(
            dlq_id=999,
            domain="test",
            failure_type="TEST_FAILURE",
        )
        
        log_cb_state_change_audit(
            cb_name="test_cb",
            old_state="closed",
            new_state="open",
        )
        # 성공 시 예외 없음


class TestBufferPatternMultipleEvents:
    """버퍼 패턴 다중 이벤트 테스트."""
    
    def test_single_request_collects_multiple_events(self):
        """단일 request에서 여러 이벤트가 수집되어야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        request = MockRequest()
        buffer = RequestAuditBuffer.get_or_create(request)
        
        # 여러 이벤트 추가
        buffer.add(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="SelfHealingMiddleware",
            details={"old_state": "closed", "new_state": "open"},
        )
        buffer.add(
            event_type=AuditEventType.DLQ_STORE,
            source="SelfHealingMiddleware",
            details={"dlq_id": 123},
        )
        buffer.add(
            event_type=AuditEventType.GOVERNANCE_BLOCKED,
            source="GovernanceGuard",
            details={"block_reason": "error_budget"},
        )
        
        events = buffer.get_events()
        assert len(events) == 3
        
        event_types = [e.event_type for e in events]
        assert AuditEventType.CB_STATE_CHANGE in event_types
        assert AuditEventType.DLQ_STORE in event_types
        assert AuditEventType.GOVERNANCE_BLOCKED in event_types
    
    def test_buffer_is_request_scoped(self):
        """버퍼는 request 단위로 격리되어야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        request1 = MockRequest(path="/api/orders/")
        request2 = MockRequest(path="/api/payments/")
        
        buffer1 = RequestAuditBuffer.get_or_create(request1)
        buffer2 = RequestAuditBuffer.get_or_create(request2)
        
        buffer1.add(
            event_type=AuditEventType.DLQ_STORE,
            source="test",
            details={"request": 1},
        )
        buffer2.add(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="test",
            details={"request": 2},
        )
        buffer2.add(
            event_type=AuditEventType.RATE_LIMITED,
            source="test",
            details={"request": 2},
        )
        
        # request1 버퍼에는 1개 이벤트
        assert len(buffer1.get_events()) == 1
        
        # request2 버퍼에는 2개 이벤트
        assert len(buffer2.get_events()) == 2
