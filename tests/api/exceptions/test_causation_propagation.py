"""
Causation Chain 전파 통합 테스트.

Django 설정이 필요한 ResponseMeta, StandardErrorResponse causation_id 테스트.
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=True)


class TestResponseMetaCausationId:
    """ResponseMeta causation_id 필드 통합 테스트."""
    
    def test_response_meta_includes_causation_id(self):
        """ResponseMeta에 causation_id 포함."""
        from selfhealing.api.django.exceptions.response import ResponseMeta
        
        meta = ResponseMeta(
            request_id="req-123",
            path="/api/test/",
            method="POST",
            causation_id="cascade-abc123",
        )
        
        result = meta.to_dict()
        
        assert result["request_id"] == "req-123"
        assert result["path"] == "/api/test/"
        assert result["method"] == "POST"
        assert result["causation_id"] == "cascade-abc123"
    
    def test_response_meta_omits_none_causation_id(self):
        """causation_id가 None이면 출력에서 제외."""
        from selfhealing.api.django.exceptions.response import ResponseMeta
        
        meta = ResponseMeta(
            request_id="req-123",
            causation_id=None,
        )
        
        result = meta.to_dict()
        
        assert "request_id" in result
        assert "causation_id" not in result


class TestStandardErrorResponseWithCausation:
    """StandardErrorResponse causation_id 통합 테스트."""
    
    def test_from_classified_error_with_causation_id(self):
        """from_classified_error가 causation_id 포함."""
        from selfhealing.api.django.exceptions.response import StandardErrorResponse
        from selfhealing.api.django.exceptions.classifier import ClassifiedError, ExceptionCategory
        from selfhealing.api.django.exceptions.codes import ErrorCode
        
        classified = ClassifiedError(
            category=ExceptionCategory.VALIDATION,
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            http_status=400,
            message="필수 필드 누락",
            retryable=False,
            exception_class="ValidationError",
        )
        
        response = StandardErrorResponse.from_classified_error(
            classified=classified,
            request_id="req-456",
            path="/api/orders/",
            method="POST",
            causation_id="cascade-test123",
        )
        
        assert response.meta.causation_id == "cascade-test123"
        
        # to_dict에도 포함
        result = response.to_dict()
        assert result["meta"]["causation_id"] == "cascade-test123"
    
    def test_from_classified_error_without_causation_id(self):
        """causation_id 미전달 시 None 유지."""
        from selfhealing.api.django.exceptions.response import StandardErrorResponse
        from selfhealing.api.django.exceptions.classifier import ClassifiedError, ExceptionCategory
        from selfhealing.api.django.exceptions.codes import ErrorCode
        
        classified = ClassifiedError(
            category=ExceptionCategory.INTERNAL,
            code=ErrorCode.SYSTEM_INTERNAL_ERROR,
            http_status=500,
            message="내부 오류",
            retryable=False,
            exception_class="Exception",
        )
        
        response = StandardErrorResponse.from_classified_error(
            classified=classified,
            request_id="req-789",
        )
        
        assert response.meta.causation_id is None
        
        result = response.to_dict()
        assert "causation_id" not in result["meta"]
