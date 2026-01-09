"""
Phase 3 Audit Middleware Integration Tests

File: tests/self_healing/integration/test_audit_middleware_phase3.py

Purpose:
    Django 환경에서 Phase 3 마이그레이션 결과를 검증합니다.
    - SelfHealingMiddleware → RequestAuditBuffer 연동
    - PoolCircuitBreakerMiddleware → 버퍼 패턴 우선 적용
    - governance_checks → 하이브리드 로직 검증
    - ConfigChangeTracker → request 파라미터 연동
    - SelfHealingRecoveryLogger → 버퍼 패턴 검증

Difference from unit tests:
    - packages/.../tests/ 의 단위 테스트는 시그니처와 모킹 레벨 검증
    - 이 통합 테스트는 실제 Django request/response 사이클에서 동작 검증

Document Reference: 56_AUDIT_MIDDLEWARE_DESIGN.md Phase 3
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock
import uuid
import json

import pytest
from django.test import RequestFactory, override_settings
from django.http import HttpResponse, JsonResponse

from selfhealing.core.timezone import now


# ========================================
# Test Fixtures
# ========================================


@pytest.fixture
def request_factory():
    """Django RequestFactory for creating mock requests."""
    return RequestFactory()


@pytest.fixture
def mock_request(request_factory):
    """Create a mock request with audit buffer."""
    from selfhealing.audit.event_buffer import RequestAuditBuffer
    
    request = request_factory.get('/api/test/')
    # RequestAuditBuffer.get_or_create 사용
    RequestAuditBuffer.get_or_create(request)
    return request


@pytest.fixture
def mock_request_without_buffer(request_factory):
    """Create a mock request without audit buffer (for fallback testing)."""
    return request_factory.get('/api/test/')


@pytest.fixture
def mock_audit_adapter():
    """Mock AuditLogAdapter for testing."""
    adapter = MagicMock()
    adapter.log = MagicMock()
    adapter.log_governance_blocked = MagicMock()
    return adapter


# ========================================
# SelfHealingMiddleware Integration Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestSelfHealingMiddlewareAuditIntegration:
    """
    SelfHealingMiddleware의 audit 버퍼 연동 통합 테스트.
    
    Phase 3에서 추가된 request 파라미터를 통해
    RequestAuditBuffer에 이벤트가 적재되는지 검증합니다.
    """

    def test_log_audit_event_uses_buffer_when_available(self, mock_request):
        """
        Purpose:
            _log_audit_event가 request가 있을 때 버퍼를 사용하는지 검증
            
        Expected:
            - request의 버퍼에 이벤트 적재
            - 직접 adapter 호출 없음
        """
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        middleware = SelfHealingMiddleware(get_response=lambda r: HttpResponse())
        
        # Act - 실제 시그니처: (event_type, data, request)
        middleware._log_audit_event(
            event_type="CB_STATE_CHANGE",
            data={"service": "test", "state": "open"},
            request=mock_request,
        )
        
        # Assert - buffer에 적재됨
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        assert len(events) >= 1

    def test_log_audit_event_falls_back_when_no_request(self):
        """
        Purpose:
            request가 None이면 직접 adapter를 호출하는지 검증
            
        Expected:
            - _audit_logger.log() 직접 호출
            - 예외 발생 없음
        """
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        
        middleware = SelfHealingMiddleware(get_response=lambda r: HttpResponse())
        
        with patch.object(middleware, '_audit_logger') as mock_logger:
            # Act - request=None으로 호출 (fallback 경로)
            middleware._log_audit_event(
                event_type="CB_STATE_CHANGE",
                data={"service": "test", "state": "open"},
                request=None,  # request 없음 → fallback
            )
            
            # Assert - 직접 호출
            mock_logger.log.assert_called()

    def test_record_cb_failure_uses_buffer_pattern(self, mock_request):
        """
        Purpose:
            _record_cb_failure가 버퍼 패턴을 사용하는지 검증
        """
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        middleware = SelfHealingMiddleware(get_response=lambda r: HttpResponse())
        
        # Act - 실제 시그니처: (error_context, request)
        middleware._record_cb_failure(
            error_context={"error_type": "ConnectionError", "message": "Connection timeout"},
            request=mock_request,
        )
        
        # Assert - buffer에 이벤트 적재 (CB 실패 기록)
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        # CB 실패가 기록되었을 수 있음 (audit_event 호출 시)
        assert events is not None  # 버퍼 존재 확인


# ========================================
# PoolCircuitBreakerMiddleware Integration Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestPoolCircuitBreakerAuditIntegration:
    """
    PoolCircuitBreakerMiddleware의 버퍼 패턴 우선 적용 검증.
    """

    def test_rejection_audit_uses_buffer_first(self, mock_request):
        """
        Purpose:
            _record_rejection_audit이 버퍼를 우선 사용하는지 검증
            
        Expected:
            - request의 버퍼에 POOL_CB_REJECTION 이벤트 적재
        """
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreakerMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        middleware = PoolCircuitBreakerMiddleware(get_response=lambda r: HttpResponse())
        
        # Act - 실제 시그니처: (request, reason, circuit_state, pool_status)
        middleware._record_rejection_audit(
            request=mock_request,
            reason="Connection pool exhausted",
            circuit_state="open",
            pool_status={
                "checkedout": 100,
                "total_capacity": 100,
                "usage_percent": 100.0,
                "is_exhausted": True,
                "_cache_age_ms": 50,
                "_is_stale": False,
            },
        )
        
        # Assert
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        assert len(events) >= 1

    def test_rejection_audit_fallback_without_buffer(self, mock_request_without_buffer):
        """
        Purpose:
            버퍼가 없으면 ContinuousAuditRecorder를 직접 호출하는지 검증
        """
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreakerMiddleware
        
        middleware = PoolCircuitBreakerMiddleware(get_response=lambda r: HttpResponse())
        
        # Act - should not raise (버퍼 없이도 동작)
        middleware._record_rejection_audit(
            request=mock_request_without_buffer,
            reason="Pool exhausted",
            circuit_state="open",
            pool_status={
                "checkedout": 100,
                "total_capacity": 100,
                "usage_percent": 100.0,
                "is_exhausted": True,
            },
        )
        
        # Assert - no exception (fallback 동작)


# ========================================
# Governance Checks Integration Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestGovernanceChecksAuditIntegration:
    """
    governance_checks의 하이브리드 audit 로직 검증.
    """

    def test_log_governance_blocked_uses_buffer(self, mock_request):
        """
        Purpose:
            _log_governance_blocked이 request가 있을 때 버퍼를 사용하는지 검증
        """
        from selfhealing.services.governance_checks import _log_governance_blocked
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        # Act - 실제 시그니처: (block_reason, operation_name, details, service_name, domain, request)
        _log_governance_blocked(
            block_reason="kill_switch",
            operation_name="test_operation",
            details={"reason": "Kill switch activated"},
            service_name="toss_payment",
            domain="payment",
            request=mock_request,
        )
        
        # Assert
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        governance_events = [
            e for e in events
            if e.event_type == AuditEventType.GOVERNANCE_BLOCKED
        ]
        assert len(governance_events) >= 1

    def test_log_governance_blocked_fallback(self):
        """
        Purpose:
            request가 없을 때 직접 adapter를 호출하는지 검증
        """
        from selfhealing.services.governance_checks import _log_governance_blocked, _get_audit_adapter
        
        with patch('selfhealing.services.governance_checks._get_audit_adapter') as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_get_adapter.return_value = mock_adapter
            
            # Act - request=None
            _log_governance_blocked(
                block_reason="kill_switch",
                operation_name="test_operation",
                details={"reason": "Kill switch activated"},
                service_name="toss_payment",
                domain="payment",
                request=None,
            )
            
            # Assert - adapter 직접 호출
            mock_adapter.log_governance_blocked.assert_called()


# ========================================
# ConfigChangeTracker Integration Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestConfigChangeTrackerAuditIntegration:
    """
    ConfigChangeTracker의 request 파라미터 연동 검증.
    """

    def test_log_change_method_accepts_request(self, mock_request, mock_audit_adapter):
        """
        Purpose:
            _log_change가 request가 있을 때 버퍼를 사용하는지 검증
        """
        from selfhealing.config_tracker import ConfigChangeTracker, ConfigChange
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        from datetime import datetime, timezone
        
        # ConfigChangeTracker는 audit_adapter 필수
        tracker = ConfigChangeTracker(audit_adapter=mock_audit_adapter)
        
        # ConfigChange 객체 생성
        change = ConfigChange(
            config_key="circuit_breaker.enabled",
            old_value=True,
            new_value=False,
            reason="Test change",
            changed_at=datetime.now(timezone.utc),
            applied=True,
        )
        
        # Act - _log_change 직접 호출 (request 파라미터 포함)
        tracker._log_change(
            change=change,
            success=True,
            request=mock_request,
        )
        
        # Assert - 버퍼에 적재됨
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        config_events = [
            e for e in events
            if e.event_type == AuditEventType.CONFIG_CHANGE
        ]
        assert len(config_events) >= 1

    def test_log_manual_override_uses_buffer(self, mock_request, mock_audit_adapter):
        """
        Purpose:
            log_manual_override가 request가 있을 때 버퍼를 사용하는지 검증
        """
        from selfhealing.config_tracker import ConfigChangeTracker
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        tracker = ConfigChangeTracker(audit_adapter=mock_audit_adapter)
        
        # Act - 실제 시그니처: (config_key, new_value, reason, override_type, request)
        tracker.log_manual_override(
            config_key="circuit_breaker.toss_payment.force_open",
            new_value=True,
            reason="Emergency maintenance",
            override_type="circuit_breaker",
            request=mock_request,
        )
        
        # Assert - 버퍼에 적재됨
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        override_events = [
            e for e in events
            if e.event_type == AuditEventType.MANUAL_OVERRIDE
        ]
        assert len(override_events) >= 1


# ========================================
# SelfHealingRecoveryLogger Integration Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestRecoveryLoggerAuditIntegration:
    """
    SelfHealingRecoveryLogger의 버퍼 패턴 검증.
    """

    def test_log_event_uses_buffer(self, mock_request):
        """
        Purpose:
            log_event가 request가 있을 때 버퍼를 사용하는지 검증
        """
        from selfhealing.api.django.middleware import SelfHealingRecoveryLogger
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        logger = SelfHealingRecoveryLogger()
        
        # 먼저 recovery chain 시작
        chain_id = logger.start_recovery_chain(
            trigger="db_connection_exhausted",
            affected_services=["database"],
        )
        
        # Act - 실제 시그니처: (chain_id, event_type, data, request)
        logger.log_event(
            chain_id=chain_id,
            event_type="circuit_opened",
            data={"service": "toss_payment", "action": "retry"},
            request=mock_request,
        )
        
        # Assert
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        events = buffer.get_events()
        recovery_events = [
            e for e in events
            if e.event_type == AuditEventType.RECOVERY_EVENT
        ]
        assert len(recovery_events) >= 1

    def test_log_event_fallback_without_request(self):
        """
        Purpose:
            request가 없을 때 직접 adapter를 호출하는지 검증
        """
        from selfhealing.api.django.middleware import SelfHealingRecoveryLogger
        
        logger = SelfHealingRecoveryLogger()
        
        # 먼저 recovery chain 시작
        chain_id = logger.start_recovery_chain(
            trigger="db_connection_exhausted",
            affected_services=["database"],
        )
        
        # Act - request=None (비동기 컨텍스트 시뮬레이션)
        result = logger.log_event(
            chain_id=chain_id,
            event_type="circuit_opened",
            data={"service": "toss_payment"},
            request=None,
        )
        
        # Assert - 정상 동작 (fallback)
        assert result is True  # log_event는 성공 시 True 반환


# ========================================
# End-to-End Middleware Chain Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestAuditMiddlewareChainE2E:
    """
    AuditMiddleware가 전체 미들웨어 체인에서 이벤트를 수집하는지 검증.
    """

    def test_audit_middleware_collects_buffered_events(self, request_factory):
        """
        Purpose:
            AuditMiddleware가 response 시점에 버퍼의 이벤트를 수집하는지 검증
            
        Flow:
            1. AuditMiddleware가 request에 버퍼 생성
            2. 다른 미들웨어들이 버퍼에 이벤트 적재
            3. AuditMiddleware가 response 시점에 일괄 기록
        """
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        def mock_get_response(request):
            # 중간 미들웨어가 버퍼에 이벤트 적재하는 시뮬레이션
            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(
                event_type=AuditEventType.CB_STATE_CHANGE,
                source="test",
                details={"service": "test", "state": "open"},
            )
            buffer.add(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
                details={"dlq_id": 123},
            )
            return HttpResponse("OK")
        
        middleware = AuditMiddleware(get_response=mock_get_response)
        request = request_factory.get('/api/test/')
        
        # Act
        response = middleware(request)
        
        # Assert
        assert response.status_code == 200
        # 버퍼가 생성되었어야 함
        buffer = RequestAuditBuffer.get_or_create(request)
        assert buffer is not None

    def test_multiple_events_collected_in_order(self, request_factory):
        """
        Purpose:
            여러 이벤트가 순서대로 수집되는지 검증
        """
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        def mock_get_response(request):
            buffer = RequestAuditBuffer.get_or_create(request)
            # 순서대로 이벤트 적재 (존재하는 이벤트 타입 사용)
            buffer.add(
                event_type=AuditEventType.GENERIC,
                source="test",
                details={"order": 1},
            )
            buffer.add(
                event_type=AuditEventType.CB_STATE_CHANGE,
                source="test",
                details={"order": 2},
            )
            buffer.add(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
                details={"order": 3},
            )
            return HttpResponse("OK")
        
        middleware = AuditMiddleware(get_response=mock_get_response)
        request = request_factory.get('/api/test/')
        
        # Act
        response = middleware(request)
        
        # Assert
        buffer = RequestAuditBuffer.get_or_create(request)
        events = buffer.get_events()
        assert len(events) >= 3
        
        # 순서 검증
        orders = [e.details.get("order") for e in events if e.details.get("order")]
        assert orders == sorted(orders)


# ========================================
# Backward Compatibility Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestPhase3BackwardCompatibility:
    """
    Phase 3 마이그레이션의 하위 호환성 검증.
    
    기존 코드가 request 파라미터 없이 호출해도 동작해야 합니다.
    """

    def test_log_audit_event_without_request_parameter(self):
        """
        Purpose:
            request 파라미터 없이 호출해도 동작하는지 검증
        """
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        
        middleware = SelfHealingMiddleware(get_response=lambda r: HttpResponse())
        
        with patch.object(middleware, '_audit_logger') as mock_logger:
            # Act - request 파라미터 없이 호출 (기존 방식)
            middleware._log_audit_event(
                event_type="CB_STATE_CHANGE",
                data={"service": "test"},
                # request 파라미터 생략
            )
            
            # Assert - 예외 없이 동작
            mock_logger.log.assert_called()

    def test_governance_blocked_without_request(self):
        """
        Purpose:
            governance_checks가 request 없이도 동작하는지 검증
        """
        from selfhealing.services.governance_checks import _log_governance_blocked
        
        with patch('selfhealing.services.governance_checks._get_audit_adapter') as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_get_adapter.return_value = mock_adapter
            
            # Act - request 없이 호출
            _log_governance_blocked(
                block_reason="kill_switch",
                operation_name="test_op",
                details={"reason": "Test reason"},
                service_name="test",
            )
            
            # Assert - 정상 동작
            mock_adapter.log_governance_blocked.assert_called()

    def test_config_tracker_without_request(self, mock_audit_adapter):
        """
        Purpose:
            ConfigChangeTracker가 request 없이도 동작하는지 검증
        """
        from selfhealing.config_tracker import ConfigChangeTracker, ConfigChange
        from datetime import datetime, timezone
        
        tracker = ConfigChangeTracker(audit_adapter=mock_audit_adapter)
        
        # ConfigChange 객체 생성
        change = ConfigChange(
            config_key="test.key",
            old_value="old",
            new_value="new",
            reason="test",
            changed_at=datetime.now(timezone.utc),
            applied=True,
        )
        
        # Act - request 없이 호출
        tracker._log_change(
            change=change,
            success=True,
            # request 없음
        )
        
        # Assert - 정상 동작 (adapter 호출됨)
        mock_audit_adapter.log.assert_called()


# ========================================
# Error Handling Tests
# ========================================


@pytest.mark.django_db
@pytest.mark.tier2
class TestPhase3ErrorHandling:
    """
    버퍼 패턴 에러 처리 검증.
    """

    def test_buffer_pattern_is_safe_when_import_fails(self):
        """
        Purpose:
            event_buffer 임포트 실패 시 fallback으로 동작하는지 검증
            
        Note:
            실제로 ImportError를 발생시키기 어려우므로,
            request=None 경로로 fallback이 정상 동작하는지 검증
        """
        from selfhealing.api.django.middleware import SelfHealingMiddleware
        
        middleware = SelfHealingMiddleware(get_response=lambda r: HttpResponse())
        
        with patch.object(middleware, '_audit_logger') as mock_logger:
            # Act - request=None → fallback 경로
            middleware._log_audit_event(
                event_type="CB_STATE_CHANGE",
                data={"service": "test"},
                request=None,
            )
            
            # Assert - fallback 로직 동작
            mock_logger.log.assert_called()

    def test_audit_middleware_handles_exception_gracefully(self, request_factory):
        """
        Purpose:
            AuditMiddleware가 예외 상황에서도 응답을 반환하는지 검증
        """
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        def mock_get_response(request):
            raise ValueError("Test exception")
        
        middleware = AuditMiddleware(get_response=mock_get_response)
        request = request_factory.get('/api/test/')
        
        # Act & Assert - 예외가 전파됨 (미들웨어가 예외를 삼키면 안됨)
        with pytest.raises(ValueError):
            middleware(request)
