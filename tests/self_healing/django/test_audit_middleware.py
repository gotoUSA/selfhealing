"""
Test AuditMiddleware - Django 환경 필요 테스트

56_AUDIT_MIDDLEWARE_DESIGN.md 구현 테스트:
- AuditMiddleware: 버퍼 낚아채기 및 기록
- Gateway Pipeline 통합 테스트
- 모듈 export 테스트

이 테스트는 Django 설정이 필요하므로 전역 tests 폴더에 위치합니다.

Author: SelfHealing Team
"""

from unittest.mock import MagicMock, patch


# =============================================================================
# Test AuditMiddleware
# =============================================================================

class TestAuditMiddleware:
    """AuditMiddleware 테스트."""
    
    def test_middleware_initialization(self):
        """미들웨어 초기화."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        get_response = MagicMock(return_value=MagicMock(status_code=200))
        middleware = AuditMiddleware(get_response)
        
        assert middleware.get_response is get_response
        assert middleware._initialized is False
    
    def test_middleware_skips_excluded_paths(self):
        """제외 경로는 처리하지 않음."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        mock_response = MagicMock(status_code=200)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        # Health 경로 요청
        mock_request = MagicMock()
        mock_request.path = "/api/self-healing/health/"
        mock_request.META = {}
        
        response = middleware(mock_request)
        
        # 응답은 정상 반환
        assert response == mock_response
    
    def test_middleware_creates_buffer(self):
        """미들웨어가 버퍼를 생성하는지 확인."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        mock_response = MagicMock(status_code=200)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "GET"
        mock_request.META = {}
        mock_request.user = MagicMock(is_authenticated=False)
        
        response = middleware(mock_request)
        
        # 버퍼가 생성되었는지 확인
        assert RequestAuditBuffer.META_KEY in mock_request.META
    
    def test_middleware_captures_error_responses(self):
        """4xx/5xx 응답 시 에러 이벤트 추가."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_response = MagicMock(status_code=500)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "POST"
        mock_request.META = {}
        mock_request.user = MagicMock(is_authenticated=False)
        
        response = middleware(mock_request)
        
        # 버퍼에 에러 이벤트가 추가되었는지 확인
        buffer = RequestAuditBuffer.get(mock_request)
        error_events = buffer.get_events_by_type(AuditEventType.ERROR_DETECTED)
        
        assert len(error_events) >= 1
        assert error_events[0].details["status_code"] == 500
    
    def test_middleware_uses_x_request_id_header(self):
        """X-Request-ID 헤더가 있으면 사용."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        mock_response = MagicMock(status_code=200)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "GET"
        mock_request.META = {"HTTP_X_REQUEST_ID": "custom-request-id-123"}
        mock_request.user = MagicMock(is_authenticated=False)
        
        middleware(mock_request)
        
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer.request_id == "custom-request-id-123"
    
    def test_middleware_fail_open_policy(self):
        """Fail-Open: 기록 실패해도 응답은 정상 반환."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_response = MagicMock(status_code=200)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        # 버퍼에 이벤트 추가
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "GET"
        mock_request.META = {}
        mock_request.user = MagicMock(is_authenticated=False)
        
        # 버퍼 생성 및 이벤트 추가
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="Test")
        
        # Recorder를 None으로 설정하여 기록 실패 시뮬레이션
        middleware._initialized = True
        middleware._recorder = None
        
        # 응답은 정상 반환되어야 함
        response = middleware(mock_request)
        assert response == mock_response
    
    def test_middleware_excluded_paths_list(self):
        """제외 경로 목록 확인."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        
        expected_paths = [
            "/api/self-healing/health/",
            "/health/",
            "/api/self-healing/metrics/",
            "/metrics/",
        ]
        
        for path in expected_paths:
            assert any(
                path.startswith(excluded)
                for excluded in AuditMiddleware.EXCLUDED_PATHS
            ), f"{path} should be excluded"


# =============================================================================
# Test Gateway Pipeline Integration
# =============================================================================

class TestGatewayPipelineIntegration:
    """관문형 파이프라인 통합 테스트."""
    
    def test_full_request_lifecycle(self):
        """전체 요청 생명주기 테스트."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        from selfhealing.services.audit import (
            log_dlq_store_audit,
            log_cb_state_change_audit,
        )
        
        # Setup
        mock_response = MagicMock(status_code=200)
        get_response = MagicMock(return_value=mock_response)
        middleware = AuditMiddleware(get_response)
        
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "POST"
        mock_request.META = {}
        mock_request.user = MagicMock(is_authenticated=False)
        
        # 시뮬레이션: View 처리 중 여러 이벤트 발생
        def simulate_view_processing(req):
            # DLQ 저장
            log_dlq_store_audit(
                dlq_id=1,
                domain="payment",
                failure_type="PG_TIMEOUT",
                request=req,
            )
            # CB 상태 변경
            log_cb_state_change_audit(
                cb_name="payment_cb",
                old_state="closed",
                new_state="open",
                request=req,
            )
            return mock_response
        
        get_response.side_effect = simulate_view_processing
        
        # 미들웨어 실행
        response = middleware(mock_request)
        
        # 검증
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer is not None
        assert buffer.event_count() >= 2  # DLQ_STORE + CB_STATE_CHANGE
    
    def test_multiple_events_in_single_request(self):
        """단일 요청에서 여러 이벤트 수집."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        buffer = RequestAuditBuffer.get_or_create(mock_request)
        
        # 여러 이벤트 추가
        buffer.add(event_type=AuditEventType.RATE_LIMITED, source="RateLimiter")
        buffer.add(event_type=AuditEventType.CB_STATE_CHANGE, source="CircuitBreaker")
        buffer.add(event_type=AuditEventType.DLQ_STORE, source="DLQService")
        buffer.add(event_type=AuditEventType.ERROR_DETECTED, source="View")
        
        # 모든 이벤트가 수집됨
        assert buffer.event_count() == 4
        
        # 버퍼 딕셔너리로 변환
        buffer_dict = buffer.to_dict()
        assert buffer_dict["event_count"] == 4
    
    def test_events_collected_before_middleware_finalizes(self):
        """미들웨어 최종 처리 전에 모든 이벤트가 수집되는지 확인."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        collected_events = []
        
        def capture_events(req):
            # View에서 이벤트 추가
            buffer = RequestAuditBuffer.get_or_create(req)
            buffer.add(event_type=AuditEventType.DLQ_STORE, source="View")
            buffer.add(event_type=AuditEventType.CB_STATE_CHANGE, source="View")
            collected_events.extend(buffer.get_events())
            return MagicMock(status_code=200)
        
        middleware = AuditMiddleware(capture_events)
        
        mock_request = MagicMock()
        mock_request.path = "/api/test/"
        mock_request.method = "POST"
        mock_request.META = {}
        mock_request.user = MagicMock(is_authenticated=False)
        
        middleware(mock_request)
        
        # View에서 수집한 이벤트
        assert len(collected_events) == 2


# =============================================================================
# Test Module Exports
# =============================================================================

class TestModuleExports:
    """모듈 export 테스트."""
    
    def test_audit_module_exports_event_buffer(self):
        """audit 모듈에서 event_buffer 컴포넌트 export."""
        from selfhealing.audit import (
            AuditEvent,
            BufferEventType,
            RequestAuditBuffer,
            add_audit_event,
        )
        
        assert AuditEvent is not None
        assert BufferEventType is not None
        assert RequestAuditBuffer is not None
        assert add_audit_event is not None
    
    def test_django_api_exports_audit_middleware(self):
        """Django API 모듈에서 AuditMiddleware export."""
        from selfhealing.api.django import (
            AuditMiddleware,
            is_audit_middleware_enabled,
        )
        
        assert AuditMiddleware is not None
        assert callable(is_audit_middleware_enabled)
    
    def test_is_audit_middleware_enabled_default(self):
        """기본값은 활성화."""
        from selfhealing.api.django.audit_middleware import is_audit_middleware_enabled
        
        # 환경변수 설정 없으면 기본값 True
        with patch.dict('os.environ', {}, clear=True):
            # 기본값 확인 (환경변수 없으면 TRUE)
            result = is_audit_middleware_enabled()
            assert result is True
    
    def test_is_audit_middleware_enabled_disabled(self):
        """환경변수로 비활성화 가능."""
        from selfhealing.api.django.audit_middleware import is_audit_middleware_enabled
        import os
        
        with patch.dict(os.environ, {"AUDIT_MIDDLEWARE_ENABLED": "FALSE"}):
            result = is_audit_middleware_enabled()
            assert result is False


# =============================================================================
# Test Audit Helpers with Request Context
# =============================================================================

class TestAuditHelpersWithRequest:
    """audit_helpers 하이브리드 로직 테스트 (request 컨텍스트)."""
    
    def test_log_dlq_store_with_request_uses_buffer(self):
        """request가 있으면 버퍼에 적재."""
        from selfhealing.services.audit import log_dlq_store_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_dlq_store_audit(
            dlq_id=123,
            domain="payment",
            failure_type="PG_TIMEOUT",
            request=mock_request,
        )
        
        # 버퍼에 추가되었는지 확인
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer is not None
        assert buffer.event_count() == 1
        
        events = buffer.get_events_by_type(AuditEventType.DLQ_STORE)
        assert len(events) == 1
        assert events[0].details["dlq_id"] == 123
    
    def test_log_dlq_replay_with_request_uses_buffer(self):
        """request가 있으면 버퍼에 적재."""
        from selfhealing.services.audit import log_dlq_replay_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_dlq_replay_audit(
            dlq_id=789,
            domain="payment",
            success=True,
            request=mock_request,
        )
        
        buffer = RequestAuditBuffer.get(mock_request)
        assert buffer is not None
        
        events = buffer.get_events_by_type(AuditEventType.DLQ_REPLAY)
        assert len(events) == 1
    
    def test_log_cb_state_change_with_request(self):
        """CB 상태 변경을 request 버퍼에 적재."""
        from selfhealing.services.audit import log_cb_state_change_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_cb_state_change_audit(
            cb_name="payment_cb",
            old_state="closed",
            new_state="open",
            reason="threshold_exceeded",
            request=mock_request,
        )
        
        buffer = RequestAuditBuffer.get(mock_request)
        events = buffer.get_events_by_type(AuditEventType.CB_STATE_CHANGE)
        
        assert len(events) == 1
        assert events[0].details["cb_name"] == "payment_cb"
    
    def test_log_governance_blocked_with_request(self):
        """Governance 차단을 request 버퍼에 적재."""
        from selfhealing.services.audit import log_governance_blocked_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_governance_blocked_audit(
            action="auto_replay",
            block_reason="kill_switch_active",
            request=mock_request,
        )
        
        buffer = RequestAuditBuffer.get(mock_request)
        events = buffer.get_events_by_type(AuditEventType.GOVERNANCE_BLOCKED)
        
        assert len(events) == 1
        assert events[0].success is False
    
    def test_log_rate_limited_with_request(self):
        """Rate Limit 차단을 request 버퍼에 적재."""
        from selfhealing.services.audit import log_rate_limited_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_rate_limited_audit(
            client_ip="192.168.1.100",
            endpoint="/api/test/",
            limit_type="global",
            request=mock_request,
        )
        
        buffer = RequestAuditBuffer.get(mock_request)
        events = buffer.get_events_by_type(AuditEventType.RATE_LIMITED)
        
        assert len(events) == 1
    
    def test_log_pool_cb_rejection_with_request(self):
        """Pool CB 거부를 request 버퍼에 적재."""
        from selfhealing.services.audit import log_pool_cb_rejection_audit
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        mock_request = MagicMock()
        mock_request.META = {}
        
        log_pool_cb_rejection_audit(
            pool_name="default",
            current_utilization=0.95,
            threshold=0.90,
            request=mock_request,
        )
        
        buffer = RequestAuditBuffer.get(mock_request)
        events = buffer.get_events_by_type(AuditEventType.POOL_CB_REJECTION)
        
        assert len(events) == 1
        assert events[0].details["current_utilization"] == 0.95
