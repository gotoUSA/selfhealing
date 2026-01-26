"""
Exception Handler 테스트 - 표준 응답 (response.py).

StandardErrorResponse, ErrorInfo, ResponseMeta 및 편의 함수 검증.
"""

import pytest
from datetime import datetime, timezone

from selfhealing.api.django.exceptions.response import (
    ErrorInfo,
    ResponseMeta,
    StandardErrorResponse,
    create_error_response,
)
from selfhealing.api.django.exceptions.codes import ErrorCode
from selfhealing.api.django.exceptions.classifier import (
    ExceptionCategory,
    ClassifiedError,
)


class TestErrorInfo:
    """ErrorInfo dataclass 테스트."""
    
    def test_create_error_info(self):
        """ErrorInfo 생성 테스트."""
        info = ErrorInfo(
            code="VALIDATION_FIELD_REQUIRED",
            message="필수 필드가 누락되었습니다.",
            detail="The 'amount' field is required.",
            field="amount",
            retryable=False,
        )
        assert info.code == "VALIDATION_FIELD_REQUIRED"
        assert info.message == "필수 필드가 누락되었습니다."
        assert info.field == "amount"
        assert not info.retryable
    
    def test_to_dict_includes_required_fields(self):
        """to_dict는 필수 필드를 포함해야 함."""
        info = ErrorInfo(
            code="VALIDATION_FIELD_REQUIRED",
            message="필수 필드가 누락되었습니다.",
        )
        result = info.to_dict()
        
        assert "code" in result
        assert "message" in result
        assert "retryable" in result
        assert result["code"] == "VALIDATION_FIELD_REQUIRED"
    
    def test_to_dict_includes_optional_fields_when_set(self):
        """to_dict는 설정된 선택 필드를 포함해야 함."""
        info = ErrorInfo(
            code="VALIDATION_FIELD_REQUIRED",
            message="필수 필드가 누락되었습니다.",
            detail="The 'amount' field is required.",
            field="amount",
        )
        result = info.to_dict()
        
        assert result["detail"] == "The 'amount' field is required."
        assert result["field"] == "amount"
    
    def test_to_dict_excludes_none_fields(self):
        """to_dict는 None인 선택 필드를 제외해야 함."""
        info = ErrorInfo(
            code="SYSTEM_INTERNAL_ERROR",
            message="내부 오류",
        )
        result = info.to_dict()
        
        assert "detail" not in result
        assert "field" not in result


class TestResponseMeta:
    """ResponseMeta dataclass 테스트."""
    
    def test_create_response_meta(self):
        """ResponseMeta 생성 테스트."""
        meta = ResponseMeta(
            request_id="abc-123",
            path="/api/payments/",
            method="POST",
        )
        assert meta.request_id == "abc-123"
        assert meta.path == "/api/payments/"
        assert meta.method == "POST"
    
    def test_default_timestamp(self):
        """기본 timestamp는 현재 시간이어야 함."""
        before = datetime.now(timezone.utc)
        meta = ResponseMeta()
        after = datetime.now(timezone.utc)
        
        assert before <= meta.timestamp <= after
    
    def test_to_dict_includes_timestamp(self):
        """to_dict는 항상 timestamp를 포함해야 함."""
        meta = ResponseMeta()
        result = meta.to_dict()
        
        assert "timestamp" in result
        # ISO 형식 검증
        assert "T" in result["timestamp"]
    
    def test_to_dict_includes_optional_fields_when_set(self):
        """to_dict는 설정된 선택 필드를 포함해야 함."""
        meta = ResponseMeta(
            request_id="abc-123",
            path="/api/test/",
            method="GET",
        )
        result = meta.to_dict()
        
        assert result["request_id"] == "abc-123"
        assert result["path"] == "/api/test/"
        assert result["method"] == "GET"


class TestStandardErrorResponse:
    """StandardErrorResponse dataclass 테스트."""
    
    def test_create_standard_response(self):
        """StandardErrorResponse 생성 테스트."""
        response = StandardErrorResponse(
            success=False,
            error=ErrorInfo(code="TEST", message="Test error"),
            meta=ResponseMeta(request_id="123"),
            http_status=400,
        )
        assert response.success is False
        assert response.error.code == "TEST"
        assert response.http_status == 400
    
    def test_default_success_is_false(self):
        """기본 success는 False여야 함."""
        response = StandardErrorResponse()
        assert response.success is False
    
    def test_to_dict_structure(self):
        """to_dict 결과 구조 검증."""
        response = StandardErrorResponse(
            error=ErrorInfo(code="TEST", message="Test"),
            meta=ResponseMeta(request_id="123"),
        )
        result = response.to_dict()
        
        assert "success" in result
        assert "error" in result
        assert "meta" in result
        assert result["success"] is False
        assert isinstance(result["error"], dict)
        assert isinstance(result["meta"], dict)
    
    def test_to_dict_merges_extra(self):
        """to_dict는 extra를 error에 병합해야 함."""
        response = StandardErrorResponse(
            error=ErrorInfo(code="CONFIG_LOCKED", message="Locked"),
            extra={"current_owner": "rollout-123"},
        )
        result = response.to_dict()
        
        assert result["error"]["current_owner"] == "rollout-123"
    
    def test_http_status_not_in_response_body(self):
        """http_status는 응답 본문에 포함되지 않아야 함."""
        response = StandardErrorResponse(http_status=500)
        result = response.to_dict()
        
        assert "http_status" not in result


class TestFromClassifiedError:
    """from_classified_error 클래스 메서드 테스트."""
    
    def test_from_classified_error(self):
        """ClassifiedError로부터 응답 생성 테스트."""
        classified = ClassifiedError(
            category=ExceptionCategory.VALIDATION,
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            http_status=400,
            message="필수 필드 누락",
            detail="'amount' is required",
            field="amount",
            retryable=False,
        )
        
        response = StandardErrorResponse.from_classified_error(
            classified=classified,
            request_id="abc-123",
            path="/api/payments/",
            method="POST",
        )
        
        assert response.success is False
        assert response.error.code == "VALIDATION_FIELD_REQUIRED"
        assert response.error.field == "amount"
        assert response.http_status == 400
        assert response.meta.request_id == "abc-123"
        assert response.meta.path == "/api/payments/"
    
    def test_from_classified_error_with_extra(self):
        """extra가 있는 ClassifiedError 처리 테스트."""
        classified = ClassifiedError(
            category=ExceptionCategory.CONFLICT,
            code=ErrorCode.CONFIG_LOCKED,
            http_status=409,
            message="Config locked",
            retryable=True,
            extra={"current_owner": "rollout-123"},
        )
        
        response = StandardErrorResponse.from_classified_error(classified=classified)
        
        assert response.extra["current_owner"] == "rollout-123"


class TestFromException:
    """from_exception 클래스 메서드 테스트."""
    
    def test_from_value_error(self):
        """ValueError로부터 응답 생성 테스트."""
        exc = ValueError("Invalid input")
        response = StandardErrorResponse.from_exception(
            exc=exc,
            request_id="test-123",
        )
        
        assert response.success is False
        assert response.error.code == "VALIDATION_INVALID_VALUE"
        assert response.http_status == 400
        assert "Invalid input" in response.error.detail
    
    def test_from_generic_exception(self):
        """일반 Exception으로부터 응답 생성 테스트."""
        exc = Exception("Unknown error")
        response = StandardErrorResponse.from_exception(exc=exc)
        
        assert response.error.code == "SYSTEM_INTERNAL_ERROR"
        assert response.http_status == 500


class TestCreateErrorResponse:
    """create_error_response 편의 함수 테스트."""
    
    def test_create_basic_error_response(self):
        """기본 에러 응답 생성 테스트."""
        response = create_error_response(
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
        )
        
        assert response.error.code == "VALIDATION_FIELD_REQUIRED"
        assert response.http_status == 400
        assert not response.error.retryable
        # 기본 메시지 사용
        assert len(response.error.message) > 0
    
    def test_create_error_response_with_custom_message(self):
        """커스텀 메시지로 에러 응답 생성 테스트."""
        response = create_error_response(
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            message="Custom error message",
        )
        
        assert response.error.message == "Custom error message"
    
    def test_create_error_response_with_all_options(self):
        """모든 옵션을 사용한 에러 응답 생성 테스트."""
        response = create_error_response(
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            message="Field required",
            detail="The 'amount' field is required.",
            field="amount",
            request_id="test-123",
            path="/api/test/",
            method="POST",
            extra={"hint": "Provide a numeric value"},
        )
        
        assert response.error.field == "amount"
        assert response.meta.request_id == "test-123"
        assert response.meta.path == "/api/test/"
        assert response.extra["hint"] == "Provide a numeric value"
    
    def test_create_error_response_retryable_codes(self):
        """재시도 가능한 에러 코드 테스트."""
        response = create_error_response(code=ErrorCode.RATE_THROTTLED)
        assert response.error.retryable is True
        
        response = create_error_response(code=ErrorCode.SERVICE_TIMEOUT)
        assert response.error.retryable is True
    
    def test_create_error_response_non_retryable_codes(self):
        """재시도 불가능한 에러 코드 테스트."""
        response = create_error_response(code=ErrorCode.AUTH_NOT_AUTHENTICATED)
        assert response.error.retryable is False
        
        response = create_error_response(code=ErrorCode.AUTHZ_PERMISSION_DENIED)
        assert response.error.retryable is False
