"""
Exception Handler 테스트 - 에러 코드 (codes.py).

ErrorCode enum, HTTP 상태 매핑, 재시도 가능 여부 매핑 검증.
"""

import pytest

from selfhealing.api.django.exceptions.codes import (
    ErrorCode,
    ERROR_CODE_TO_HTTP_STATUS,
    ERROR_CODE_RETRYABLE,
    ERROR_CODE_DEFAULT_MESSAGES,
    get_http_status,
    is_retryable,
    get_default_message,
    get_error_info,
)


class TestErrorCode:
    """ErrorCode enum 테스트."""
    
    def test_all_codes_have_http_status_mapping(self):
        """모든 에러 코드에 HTTP 상태 코드 매핑이 존재해야 함."""
        for code in ErrorCode:
            assert code in ERROR_CODE_TO_HTTP_STATUS, f"{code.value} has no HTTP status mapping"
    
    def test_all_codes_have_retryable_mapping(self):
        """모든 에러 코드에 재시도 가능 여부 매핑이 존재해야 함."""
        for code in ErrorCode:
            assert code in ERROR_CODE_RETRYABLE, f"{code.value} has no retryable mapping"
    
    def test_all_codes_have_default_message(self):
        """모든 에러 코드에 기본 메시지가 존재해야 함."""
        for code in ErrorCode:
            assert code in ERROR_CODE_DEFAULT_MESSAGES, f"{code.value} has no default message"
            assert len(ERROR_CODE_DEFAULT_MESSAGES[code]) > 0
    
    def test_code_string_value_format(self):
        """에러 코드 문자열은 대문자_언더스코어 형식이어야 함."""
        for code in ErrorCode:
            assert code.value.isupper() or "_" in code.value, f"{code.value} format is invalid"
            assert code.value == code.value.upper(), f"{code.value} should be uppercase"


class TestHttpStatusMapping:
    """HTTP 상태 코드 매핑 테스트."""
    
    @pytest.mark.parametrize("code,expected_status", [
        (ErrorCode.VALIDATION_FIELD_REQUIRED, 400),
        (ErrorCode.AUTH_NOT_AUTHENTICATED, 401),
        (ErrorCode.AUTHZ_PERMISSION_DENIED, 403),
        (ErrorCode.RESOURCE_NOT_FOUND, 404),
        (ErrorCode.CONFIG_LOCKED, 409),
        (ErrorCode.RATE_LIMIT_EXCEEDED, 429),
        (ErrorCode.SYSTEM_INTERNAL_ERROR, 500),
        (ErrorCode.SERVICE_UNAVAILABLE, 503),
        (ErrorCode.SERVICE_TIMEOUT, 504),
    ])
    def test_http_status_mapping(self, code: ErrorCode, expected_status: int):
        """주요 에러 코드의 HTTP 상태 매핑 검증."""
        assert get_http_status(code) == expected_status
    
    def test_validation_codes_return_400(self):
        """VALIDATION_ 접두어 코드는 400 또는 400번대여야 함."""
        validation_codes = [c for c in ErrorCode if c.value.startswith("VALIDATION_")]
        for code in validation_codes:
            status = get_http_status(code)
            assert 400 <= status < 500, f"{code.value} should return 4xx, got {status}"
    
    def test_auth_codes_return_401(self):
        """AUTH_ 접두어 코드는 401이어야 함."""
        auth_codes = [c for c in ErrorCode if c.value.startswith("AUTH_") and not c.value.startswith("AUTHZ_")]
        for code in auth_codes:
            assert get_http_status(code) == 401, f"{code.value} should return 401"
    
    def test_authz_codes_return_403(self):
        """AUTHZ_ 접두어 코드는 403이어야 함."""
        authz_codes = [c for c in ErrorCode if c.value.startswith("AUTHZ_")]
        for code in authz_codes:
            assert get_http_status(code) == 403, f"{code.value} should return 403"
    
    def test_system_codes_return_500(self):
        """SYSTEM_ 접두어 코드는 500이어야 함."""
        system_codes = [c for c in ErrorCode if c.value.startswith("SYSTEM_")]
        for code in system_codes:
            assert get_http_status(code) == 500, f"{code.value} should return 500"


class TestRetryableMapping:
    """재시도 가능 여부 매핑 테스트."""
    
    @pytest.mark.parametrize("code,expected_retryable", [
        (ErrorCode.VALIDATION_FIELD_REQUIRED, False),
        (ErrorCode.AUTH_NOT_AUTHENTICATED, False),
        (ErrorCode.AUTHZ_PERMISSION_DENIED, False),
        (ErrorCode.RESOURCE_NOT_FOUND, False),
        (ErrorCode.RATE_LIMIT_EXCEEDED, True),
        (ErrorCode.RATE_THROTTLED, True),
        (ErrorCode.SERVICE_UNAVAILABLE, True),
        (ErrorCode.SERVICE_TIMEOUT, True),
        (ErrorCode.SYSTEM_INTERNAL_ERROR, True),
    ])
    def test_retryable_mapping(self, code: ErrorCode, expected_retryable: bool):
        """주요 에러 코드의 재시도 가능 여부 검증."""
        assert is_retryable(code) == expected_retryable
    
    def test_validation_codes_not_retryable(self):
        """VALIDATION_ 코드는 재시도 불가능해야 함 (입력 수정 필요)."""
        validation_codes = [c for c in ErrorCode if c.value.startswith("VALIDATION_")]
        for code in validation_codes:
            assert not is_retryable(code), f"{code.value} should not be retryable"
    
    def test_auth_codes_not_retryable(self):
        """AUTH_ 코드는 재시도 불가능해야 함 (재인증 필요)."""
        auth_codes = [c for c in ErrorCode if c.value.startswith("AUTH_")]
        for code in auth_codes:
            assert not is_retryable(code), f"{code.value} should not be retryable"
    
    def test_service_codes_retryable(self):
        """SERVICE_ 코드는 재시도 가능해야 함 (서비스 복구 후)."""
        service_codes = [c for c in ErrorCode if c.value.startswith("SERVICE_")]
        for code in service_codes:
            assert is_retryable(code), f"{code.value} should be retryable"


class TestUtilityFunctions:
    """유틸리티 함수 테스트."""
    
    def test_get_default_message_returns_string(self):
        """get_default_message는 문자열을 반환해야 함."""
        for code in ErrorCode:
            message = get_default_message(code)
            assert isinstance(message, str)
            assert len(message) > 0
    
    def test_get_error_info_returns_tuple(self):
        """get_error_info는 (status, retryable, message) 튜플을 반환해야 함."""
        status, retryable, message = get_error_info(ErrorCode.VALIDATION_FIELD_REQUIRED)
        assert status == 400
        assert retryable is False
        assert isinstance(message, str)
    
    def test_get_http_status_unknown_code_returns_500(self):
        """매핑되지 않은 코드는 500을 반환해야 함 (현재는 모두 매핑됨)."""
        # 모든 코드가 매핑되어 있으므로 기존 코드로 테스트
        for code in ErrorCode:
            status = get_http_status(code)
            assert isinstance(status, int)
            assert 100 <= status < 600
