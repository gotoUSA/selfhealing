"""
Exception Handler 테스트 - DRF 핸들러 (handler.py).

selfhealing_exception_handler 함수 및 Audit 버퍼 연동 검증.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from selfhealing.api.django.exceptions.handler import (
    selfhealing_exception_handler,
    _extract_request_id,
    _extract_path,
    _extract_method,
    _get_audit_event_type,
)
from selfhealing.api.django.exceptions.codes import ErrorCode
from selfhealing.api.django.exceptions.classifier import ExceptionCategory, ClassifiedError


class TestExtractRequestId:
    """_extract_request_id 함수 테스트."""
    
    def test_returns_uuid_when_request_is_none(self):
        """request가 None이면 UUID를 생성해야 함."""
        result = _extract_request_id(None)
        assert result is not None
        assert len(result) == 36  # UUID 형식
    
    def test_returns_header_x_request_id(self):
        """HTTP_X_REQUEST_ID 헤더가 있으면 반환해야 함."""
        request = Mock()
        request.META = {"HTTP_X_REQUEST_ID": "test-request-id-123"}
        
        result = _extract_request_id(request)
        assert result == "test-request-id-123"
    
    def test_returns_header_x_correlation_id(self):
        """HTTP_X_CORRELATION_ID 헤더가 있으면 반환해야 함."""
        request = Mock()
        request.META = {"HTTP_X_CORRELATION_ID": "correlation-id-456"}
        
        result = _extract_request_id(request)
        assert result == "correlation-id-456"
    
    def test_fallback_to_uuid_when_no_header(self):
        """헤더가 없으면 UUID를 생성해야 함."""
        request = Mock()
        request.META = {}
        
        result = _extract_request_id(request)
        assert result is not None
        assert len(result) == 36


class TestExtractPath:
    """_extract_path 함수 테스트."""
    
    def test_returns_none_when_request_is_none(self):
        """request가 None이면 None을 반환해야 함."""
        result = _extract_path(None)
        assert result is None
    
    def test_returns_request_path(self):
        """request.path를 반환해야 함."""
        request = Mock()
        request.path = "/api/payments/"
        
        result = _extract_path(request)
        assert result == "/api/payments/"


class TestExtractMethod:
    """_extract_method 함수 테스트."""
    
    def test_returns_none_when_request_is_none(self):
        """request가 None이면 None을 반환해야 함."""
        result = _extract_method(None)
        assert result is None
    
    def test_returns_request_method(self):
        """request.method를 반환해야 함."""
        request = Mock()
        request.method = "POST"
        
        result = _extract_method(request)
        assert result == "POST"


class TestGetAuditEventType:
    """_get_audit_event_type 함수 테스트."""
    
    def test_validation_category_returns_api_validation_error(self):
        """VALIDATION 카테고리는 API_VALIDATION_ERROR를 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.VALIDATION,
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            http_status=400,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_VALIDATION_ERROR
    
    def test_rate_limit_category_returns_api_throttled(self):
        """RATE_LIMIT 카테고리는 API_THROTTLED를 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.RATE_LIMIT,
            code=ErrorCode.RATE_THROTTLED,
            http_status=429,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_THROTTLED
    
    def test_authz_category_returns_api_auth_error(self):
        """AUTHZ 카테고리는 API_AUTH_ERROR를 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.AUTHZ,
            code=ErrorCode.AUTHZ_PERMISSION_DENIED,
            http_status=403,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_AUTH_ERROR
    
    def test_auth_category_returns_api_auth_error(self):
        """AUTH 카테고리는 API_AUTH_ERROR를 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.AUTH,
            code=ErrorCode.AUTH_NOT_AUTHENTICATED,
            http_status=401,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_AUTH_ERROR
    
    def test_not_found_category_returns_api_not_found(self):
        """NOT_FOUND 카테고리는 API_NOT_FOUND를 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.NOT_FOUND,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            http_status=404,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_NOT_FOUND
    
    def test_internal_category_returns_api_exception(self):
        """INTERNAL 카테고리는 API_EXCEPTION을 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.INTERNAL,
            code=ErrorCode.SYSTEM_INTERNAL_ERROR,
            http_status=500,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_EXCEPTION
    
    def test_conflict_category_returns_api_exception(self):
        """CONFLICT 카테고리는 API_EXCEPTION을 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.CONFLICT,
            code=ErrorCode.CONFIG_LOCKED,
            http_status=409,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_EXCEPTION
    
    def test_service_category_returns_api_exception(self):
        """SERVICE 카테고리는 API_EXCEPTION을 반환해야 함."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        classified = ClassifiedError(
            category=ExceptionCategory.SERVICE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            http_status=503,
            message="test",
        )
        result = _get_audit_event_type(classified)
        assert result == AuditEventType.API_EXCEPTION


class TestSelfHealingExceptionHandler:
    """selfhealing_exception_handler 함수 테스트."""
    
    def test_handles_value_error(self):
        """ValueError를 표준 응답으로 처리해야 함."""
        exc = ValueError("Invalid input")
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response is not None
        assert response.status_code == 400
        assert response.data["success"] is False
        assert response.data["error"]["code"] == "VALIDATION_INVALID_VALUE"
    
    def test_handles_drf_validation_error(self):
        """DRF ValidationError를 표준 응답으로 처리해야 함."""
        from rest_framework.exceptions import ValidationError
        
        exc = ValidationError({"amount": ["This field is required."]})
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 400
        assert response.data["error"]["code"] == "VALIDATION_SERIALIZER_ERROR"
        assert response.data["error"]["field"] == "amount"
    
    def test_handles_drf_not_authenticated(self):
        """DRF NotAuthenticated를 표준 응답으로 처리해야 함."""
        from rest_framework.exceptions import NotAuthenticated
        
        exc = NotAuthenticated()
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 401
        assert response.data["error"]["code"] == "AUTH_NOT_AUTHENTICATED"
    
    def test_handles_drf_permission_denied(self):
        """DRF PermissionDenied를 표준 응답으로 처리해야 함."""
        from rest_framework.exceptions import PermissionDenied
        
        exc = PermissionDenied("Access denied")
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 403
        assert response.data["error"]["code"] == "AUTHZ_PERMISSION_DENIED"
    
    def test_handles_drf_not_found(self):
        """DRF NotFound를 표준 응답으로 처리해야 함."""
        from rest_framework.exceptions import NotFound
        
        exc = NotFound()
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 404
        assert response.data["error"]["code"] == "RESOURCE_NOT_FOUND"
    
    def test_handles_drf_throttled(self):
        """DRF Throttled를 표준 응답으로 처리해야 함."""
        from rest_framework.exceptions import Throttled
        
        exc = Throttled(wait=30)
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 429
        assert response.data["error"]["code"] == "RATE_THROTTLED"
        assert response.data["error"]["retryable"] is True
    
    def test_handles_generic_exception(self):
        """일반 Exception을 표준 응답으로 처리해야 함."""
        exc = Exception("Unknown error")
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 500
        assert response.data["error"]["code"] == "SYSTEM_INTERNAL_ERROR"
    
    def test_response_includes_meta(self):
        """응답에 meta 정보가 포함되어야 함."""
        exc = ValueError("test")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {"HTTP_X_REQUEST_ID": "test-123"}
        context = {"request": request}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert "meta" in response.data
        assert response.data["meta"]["request_id"] == "test-123"
        assert response.data["meta"]["path"] == "/api/test/"
        assert response.data["meta"]["method"] == "POST"
    
    def test_records_audit_event(self):
        """Audit 버퍼에 이벤트를 기록해야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        exc = ValueError("test error")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        # 버퍼 생성
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        response = selfhealing_exception_handler(exc, context)
        
        # 이벤트가 기록되었는지 확인
        assert buffer.has_events()
        events = buffer.get_events()
        assert len(events) >= 1
        
        # 마지막 이벤트 확인
        last_event = events[-1]
        assert last_event.source == "ExceptionHandler"
        assert last_event.success is False
        assert last_event.details["error_code"] == "VALIDATION_INVALID_VALUE"
    
    def test_audit_failure_does_not_block_response(self):
        """Audit 기록 실패가 응답을 막지 않아야 함."""
        exc = ValueError("test")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        # RequestAuditBuffer.get_or_create가 예외를 발생시키도록 설정
        with patch(
            "selfhealing.audit.event_buffer.RequestAuditBuffer.get_or_create"
        ) as mock_get_or_create:
            mock_get_or_create.side_effect = Exception("Audit failed")
            
            # 응답이 정상적으로 반환되어야 함
            response = selfhealing_exception_handler(exc, context)
            
            assert response is not None
            assert response.status_code == 400


class TestHandlerWithCustomExceptions:
    """커스텀 예외 처리 테스트."""
    
    def test_handles_config_lock_error(self):
        """ConfigLockError를 표준 응답으로 처리해야 함."""
        from selfhealing.services.canary.locking import ConfigLockError
        
        exc = ConfigLockError(
            message="Config locked",
            config_type="circuit_breaker",
            current_owner="rollout-123",
        )
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 409
        assert response.data["error"]["code"] == "CONFIG_LOCKED"
        assert response.data["error"]["current_owner"] == "rollout-123"
        assert response.data["error"]["config_type"] == "circuit_breaker"
    
    def test_handles_automation_blocked_error(self):
        """AutomationBlockedError를 표준 응답으로 처리해야 함."""
        from selfhealing.services.error_budget_gate.exceptions import AutomationBlockedError
        
        exc = AutomationBlockedError(
            message="Error budget depleted",
            error_budget_percent=5.0,
            threshold_percent=10.0,
        )
        context = {"request": None}
        
        response = selfhealing_exception_handler(exc, context)
        
        assert response.status_code == 403
        assert response.data["error"]["code"] == "AUTHZ_ERROR_BUDGET_BLOCKED"
        assert response.data["error"]["error_budget_percent"] == 5.0


class TestHandlerResponseFormat:
    """응답 포맷 일관성 테스트."""
    
    def test_response_format_consistency(self):
        """다양한 예외에 대해 응답 포맷이 일관되어야 함."""
        from rest_framework.exceptions import ValidationError, NotAuthenticated, Throttled
        
        exceptions = [
            ValueError("test"),
            ValidationError({"field": ["error"]}),
            NotAuthenticated(),
            Throttled(wait=10),
            Exception("unknown"),
        ]
        
        for exc in exceptions:
            context = {"request": None}
            response = selfhealing_exception_handler(exc, context)
            
            # 공통 구조 검증
            assert "success" in response.data
            assert "error" in response.data
            assert "meta" in response.data
            
            assert response.data["success"] is False
            assert "code" in response.data["error"]
            assert "message" in response.data["error"]
            assert "retryable" in response.data["error"]
            assert "timestamp" in response.data["meta"]


class TestAuditEventTypeMapping:
    """Audit 이벤트 타입 매핑 테스트 - API 예외 전용 이벤트 타입 사용 확인."""
    
    def test_validation_error_uses_api_validation_error(self):
        """ValidationError는 API_VALIDATION_ERROR 이벤트 타입을 사용해야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        exc = ValueError("Invalid value")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        selfhealing_exception_handler(exc, context)
        
        events = buffer.get_events()
        assert len(events) >= 1
        
        event = events[-1]
        assert event.event_type == AuditEventType.API_VALIDATION_ERROR
        assert event.source == "ExceptionHandler"
    
    def test_auth_error_uses_api_auth_error(self):
        """인증/인가 오류는 API_AUTH_ERROR 이벤트 타입을 사용해야 함."""
        from rest_framework.exceptions import NotAuthenticated
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        exc = NotAuthenticated()
        request = Mock()
        request.path = "/api/test/"
        request.method = "GET"
        request.META = {}
        context = {"request": request}
        
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        selfhealing_exception_handler(exc, context)
        
        events = buffer.get_events()
        event = events[-1]
        
        assert event.event_type == AuditEventType.API_AUTH_ERROR
        assert event.source == "ExceptionHandler"
    
    def test_permission_denied_uses_api_auth_error(self):
        """PermissionDenied는 API_AUTH_ERROR 이벤트 타입을 사용해야 함."""
        from rest_framework.exceptions import PermissionDenied
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        exc = PermissionDenied()
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        selfhealing_exception_handler(exc, context)
        
        events = buffer.get_events()
        event = events[-1]
        
        assert event.event_type == AuditEventType.API_AUTH_ERROR
    
    def test_generic_exception_uses_api_exception(self):
        """일반 예외는 API_EXCEPTION 이벤트 타입을 사용해야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        exc = RuntimeError("Something went wrong")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        selfhealing_exception_handler(exc, context)
        
        events = buffer.get_events()
        event = events[-1]
        
        assert event.event_type == AuditEventType.API_EXCEPTION
        assert event.source == "ExceptionHandler"
    
    def test_event_source_is_exception_handler(self):
        """모든 예외 이벤트의 source는 ExceptionHandler여야 함."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        exc = Exception("test")
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}
        
        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer
        
        selfhealing_exception_handler(exc, context)
        
        # has_event_from_source 메서드로 확인
        assert buffer.has_event_from_source("ExceptionHandler") is True
        assert buffer.has_event_from_source("AuditMiddleware") is False


class TestPoolTimeoutHandling:
    """Pool Timeout 처리 테스트."""
    
    def test_pool_timeout_returns_503(self):
        """Pool Timeout 예외는 503 응답을 반환해야 함."""
        from selfhealing.api.django.exceptions.handler import _is_pool_timeout
        
        # QueuePool limit 감지
        exc = Exception("QueuePool limit of size 5 overflow 10 reached")
        assert _is_pool_timeout(exc) is True
    
    def test_connection_timed_out_detected(self):
        """Connection timed out 메시지 감지."""
        from selfhealing.api.django.exceptions.handler import _is_pool_timeout
        
        exc = Exception("connection timed out")
        assert _is_pool_timeout(exc) is True
    
    def test_pool_exhausted_detected(self):
        """Pool exhausted 메시지 감지."""
        from selfhealing.api.django.exceptions.handler import _is_pool_timeout
        
        exc = Exception("pool exhausted")
        assert _is_pool_timeout(exc) is True
    
    def test_normal_exception_not_pool_timeout(self):
        """일반 예외는 Pool Timeout으로 감지되지 않아야 함."""
        from selfhealing.api.django.exceptions.handler import _is_pool_timeout
        
        exc = ValueError("Invalid value")
        assert _is_pool_timeout(exc) is False


class TestSensitiveMasking:
    """민감정보 마스킹 테스트."""
    
    def test_mask_password_in_message(self):
        """password 단어가 포함된 메시지 마스킹."""
        from selfhealing.api.django.exceptions.handler import _mask_error_message
        
        message = "Invalid password: abc123"
        result = _mask_error_message(message)
        assert "MASKED" in result
        assert "abc123" not in result
    
    def test_mask_token_in_message(self):
        """token 단어가 포함된 메시지 마스킹."""
        from selfhealing.api.django.exceptions.handler import _mask_error_message
        
        message = "Token validation failed: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = _mask_error_message(message)
        assert "MASKED" in result
    
    def test_normal_message_not_masked(self):
        """일반 메시지는 마스킹되지 않아야 함."""
        from selfhealing.api.django.exceptions.handler import _mask_error_message
        
        message = "Resource not found"
        result = _mask_error_message(message)
        assert result == "Resource not found"


class TestPrometheusMetrics:
    """Prometheus 메트릭 테스트."""
    
    def test_metrics_initialization(self):
        """메트릭 초기화가 오류 없이 수행되어야 함."""
        from selfhealing.api.django.exceptions.handler import _init_metrics
        
        # 초기화 함수 호출 (예외 발생 안함)
        _init_metrics()
    
    def test_record_metrics_does_not_raise(self):
        """메트릭 기록이 예외를 발생시키지 않아야 함."""
        from selfhealing.api.django.exceptions.handler import _record_metrics
        
        # 메트릭 기록 (예외 발생 안함)
        _record_metrics(
            path="/api/test/",
            method="POST",
            status_code=400,
            error_code="VALIDATION_FIELD_REQUIRED",
            category="validation",
        )
