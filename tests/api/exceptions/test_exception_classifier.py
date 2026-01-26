"""
Exception Handler 테스트 - 예외 분류기 (classifier.py).

ExceptionClassifier의 DRF/Django/커스텀/Python 예외 분류 검증.
"""

import pytest

from selfhealing.api.django.exceptions.classifier import (
    ExceptionCategory,
    ClassifiedError,
    ExceptionClassifier,
    get_exception_classifier,
)
from selfhealing.api.django.exceptions.codes import ErrorCode


class TestExceptionCategory:
    """ExceptionCategory enum 테스트."""
    
    def test_all_categories_have_string_values(self):
        """모든 카테고리는 문자열 값을 가져야 함."""
        for category in ExceptionCategory:
            assert isinstance(category.value, str)
            assert len(category.value) > 0
    
    def test_expected_categories_exist(self):
        """필요한 모든 카테고리가 존재해야 함."""
        expected = [
            "validation", "auth", "authz", "not_found",
            "conflict", "rate_limit", "internal", "service"
        ]
        actual = [c.value for c in ExceptionCategory]
        for exp in expected:
            assert exp in actual, f"Category '{exp}' is missing"


class TestClassifiedError:
    """ClassifiedError dataclass 테스트."""
    
    def test_create_classified_error(self):
        """ClassifiedError 인스턴스 생성 테스트."""
        error = ClassifiedError(
            category=ExceptionCategory.VALIDATION,
            code=ErrorCode.VALIDATION_FIELD_REQUIRED,
            http_status=400,
            message="Required field missing",
            detail="field 'amount' is required",
            field="amount",
            retryable=False,
        )
        assert error.category == ExceptionCategory.VALIDATION
        assert error.code == ErrorCode.VALIDATION_FIELD_REQUIRED
        assert error.http_status == 400
        assert error.field == "amount"
        assert not error.retryable
    
    def test_classified_error_with_extra(self):
        """extra 메타데이터 포함 ClassifiedError 테스트."""
        error = ClassifiedError(
            category=ExceptionCategory.CONFLICT,
            code=ErrorCode.CONFIG_LOCKED,
            http_status=409,
            message="Config locked",
            retryable=True,
            extra={"current_owner": "rollout-123", "config_type": "circuit_breaker"},
        )
        assert error.extra["current_owner"] == "rollout-123"
        assert error.extra["config_type"] == "circuit_breaker"


class TestExceptionClassifierPython:
    """Python 기본 예외 분류 테스트."""
    
    @pytest.fixture
    def classifier(self):
        return ExceptionClassifier()
    
    def test_classify_value_error(self, classifier):
        """ValueError 분류 테스트."""
        exc = ValueError("Invalid value")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_INVALID_VALUE
        assert result.http_status == 400
        assert not result.retryable
        assert "Invalid value" in result.detail
    
    def test_classify_type_error(self, classifier):
        """TypeError 분류 테스트."""
        exc = TypeError("Expected int, got str")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_FIELD_INVALID
        assert result.http_status == 400
        assert not result.retryable
    
    def test_classify_key_error(self, classifier):
        """KeyError 분류 테스트."""
        exc = KeyError("amount")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_FIELD_REQUIRED
        assert result.http_status == 400
        assert result.field == "amount"
        assert not result.retryable
    
    def test_classify_timeout_error(self, classifier):
        """TimeoutError 분류 테스트."""
        exc = TimeoutError("Connection timed out")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.SERVICE
        assert result.code == ErrorCode.SERVICE_TIMEOUT
        assert result.http_status == 504
        assert result.retryable
    
    def test_classify_connection_error(self, classifier):
        """ConnectionError 분류 테스트."""
        exc = ConnectionError("Failed to connect")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.SERVICE
        assert result.code == ErrorCode.SERVICE_UNAVAILABLE
        assert result.http_status == 503
        assert result.retryable
    
    def test_classify_generic_exception(self, classifier):
        """일반 Exception 분류 테스트."""
        exc = Exception("Unknown error")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.INTERNAL
        assert result.code == ErrorCode.SYSTEM_INTERNAL_ERROR
        assert result.http_status == 500
        assert result.retryable  # 일시적 오류일 수 있음
    
    def test_classify_stores_exception_class_name(self, classifier):
        """예외 클래스명이 저장되어야 함."""
        exc = ValueError("test")
        result = classifier.classify(exc)
        
        assert result.exception_class == "ValueError"


class TestExceptionClassifierDRF:
    """DRF 예외 분류 테스트."""
    
    @pytest.fixture
    def classifier(self):
        return ExceptionClassifier()
    
    def test_classify_drf_validation_error(self, classifier):
        """DRF ValidationError 분류 테스트."""
        from rest_framework.exceptions import ValidationError
        
        exc = ValidationError({"amount": ["This field is required."]})
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_SERIALIZER_ERROR
        assert result.http_status == 400
        assert result.field == "amount"
        assert not result.retryable
    
    def test_classify_drf_authentication_failed(self, classifier):
        """DRF AuthenticationFailed 분류 테스트."""
        from rest_framework.exceptions import AuthenticationFailed
        
        exc = AuthenticationFailed("Invalid credentials")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.AUTH
        assert result.code == ErrorCode.AUTH_CREDENTIALS_INVALID
        assert result.http_status == 401
        assert not result.retryable
    
    def test_classify_drf_not_authenticated(self, classifier):
        """DRF NotAuthenticated 분류 테스트."""
        from rest_framework.exceptions import NotAuthenticated
        
        exc = NotAuthenticated()
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.AUTH
        assert result.code == ErrorCode.AUTH_NOT_AUTHENTICATED
        assert result.http_status == 401
    
    def test_classify_drf_permission_denied(self, classifier):
        """DRF PermissionDenied 분류 테스트."""
        from rest_framework.exceptions import PermissionDenied
        
        exc = PermissionDenied("You don't have permission")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.AUTHZ
        assert result.code == ErrorCode.AUTHZ_PERMISSION_DENIED
        assert result.http_status == 403
        assert not result.retryable
    
    def test_classify_drf_not_found(self, classifier):
        """DRF NotFound 분류 테스트."""
        from rest_framework.exceptions import NotFound
        
        exc = NotFound("Resource not found")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.NOT_FOUND
        assert result.code == ErrorCode.RESOURCE_NOT_FOUND
        assert result.http_status == 404
        assert not result.retryable
    
    def test_classify_drf_throttled(self, classifier):
        """DRF Throttled 분류 테스트."""
        from rest_framework.exceptions import Throttled
        
        exc = Throttled(wait=30)
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.RATE_LIMIT
        assert result.code == ErrorCode.RATE_THROTTLED
        assert result.http_status == 429
        assert result.retryable
        assert result.extra["wait"] == 30
    
    def test_classify_drf_parse_error(self, classifier):
        """DRF ParseError 분류 테스트."""
        from rest_framework.exceptions import ParseError
        
        exc = ParseError("Malformed JSON")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_PARSE_ERROR
        assert result.http_status == 400


class TestExceptionClassifierDjango:
    """Django 예외 분류 테스트."""
    
    @pytest.fixture
    def classifier(self):
        return ExceptionClassifier()
    
    def test_classify_django_http404(self, classifier):
        """Django Http404 분류 테스트."""
        from django.http import Http404
        
        exc = Http404("Page not found")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.NOT_FOUND
        assert result.code == ErrorCode.RESOURCE_NOT_FOUND
        assert result.http_status == 404
    
    def test_classify_django_permission_denied(self, classifier):
        """Django PermissionDenied 분류 테스트."""
        from django.core.exceptions import PermissionDenied
        
        exc = PermissionDenied("Access denied")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.AUTHZ
        assert result.code == ErrorCode.AUTHZ_PERMISSION_DENIED
        assert result.http_status == 403
    
    def test_classify_django_validation_error(self, classifier):
        """Django ValidationError 분류 테스트."""
        from django.core.exceptions import ValidationError
        
        exc = ValidationError("Invalid input")
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.VALIDATION
        assert result.code == ErrorCode.VALIDATION_INVALID_VALUE
        assert result.http_status == 400


class TestExceptionClassifierCustom:
    """커스텀 예외 분류 테스트."""
    
    @pytest.fixture
    def classifier(self):
        return ExceptionClassifier()
    
    def test_classify_config_lock_error(self, classifier):
        """ConfigLockError 분류 테스트."""
        from selfhealing.services.canary.locking import ConfigLockError
        
        exc = ConfigLockError(
            message="Config locked by another rollout",
            config_type="circuit_breaker",
            current_owner="rollout-123",
        )
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.CONFLICT
        assert result.code == ErrorCode.CONFIG_LOCKED
        assert result.http_status == 409
        assert result.retryable  # 락 해제 후 재시도 가능
        assert result.extra["current_owner"] == "rollout-123"
        assert result.extra["config_type"] == "circuit_breaker"
    
    def test_classify_automation_blocked_error(self, classifier):
        """AutomationBlockedError 분류 테스트."""
        from selfhealing.services.error_budget_gate.exceptions import AutomationBlockedError
        
        exc = AutomationBlockedError(
            message="Error budget depleted",
            error_budget_percent=5.0,
            threshold_percent=10.0,
        )
        result = classifier.classify(exc)
        
        assert result.category == ExceptionCategory.AUTHZ
        assert result.code == ErrorCode.AUTHZ_ERROR_BUDGET_BLOCKED
        assert result.http_status == 403
        assert not result.retryable  # 예산 복구까지 대기 필요
        assert result.extra["error_budget_percent"] == 5.0
        assert result.extra["threshold_percent"] == 10.0


class TestGetExceptionClassifier:
    """싱글톤 인스턴스 테스트."""
    
    def test_returns_same_instance(self):
        """동일한 인스턴스를 반환해야 함."""
        c1 = get_exception_classifier()
        c2 = get_exception_classifier()
        assert c1 is c2
    
    def test_returns_exception_classifier(self):
        """ExceptionClassifier 인스턴스를 반환해야 함."""
        c = get_exception_classifier()
        assert isinstance(c, ExceptionClassifier)
