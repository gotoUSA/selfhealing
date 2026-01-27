"""
표준 에러 코드 정의.

API 예외 응답에 사용되는 표준화된 에러 코드 체계.
각 에러 코드는 HTTP 상태 코드 및 재시도 가능 여부와 매핑됩니다.

코드 형식: {CATEGORY}_{SUBCATEGORY}_{DETAIL}

Categories:
    - VALIDATION: 입력값 검증 실패
    - AUTH: 인증 실패
    - AUTHZ: 인가(권한) 실패
    - RESOURCE: 리소스 관련
    - RATE: 요청 제한
    - CONFIG: 설정 관련
    - SYSTEM: 시스템 내부 오류
    - SERVICE: 외부 서비스 관련
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """
    표준 에러 코드.

    모든 API 예외 응답은 이 코드 중 하나를 사용합니다.
    """

    # =========================================================================
    # VALIDATION: 입력값 검증 실패 (400 Bad Request)
    # =========================================================================
    VALIDATION_FIELD_REQUIRED = "VALIDATION_FIELD_REQUIRED"
    """필수 필드 누락."""

    VALIDATION_FIELD_INVALID = "VALIDATION_FIELD_INVALID"
    """필드값 형식/타입 오류."""

    VALIDATION_INVALID_VALUE = "VALIDATION_INVALID_VALUE"
    """값이 허용 범위를 벗어남."""

    VALIDATION_SERIALIZER_ERROR = "VALIDATION_SERIALIZER_ERROR"
    """DRF Serializer 검증 실패."""

    VALIDATION_PARSE_ERROR = "VALIDATION_PARSE_ERROR"
    """요청 본문 파싱 실패 (JSON 등)."""

    # =========================================================================
    # AUTH: 인증 실패 (401 Unauthorized)
    # =========================================================================
    AUTH_NOT_AUTHENTICATED = "AUTH_NOT_AUTHENTICATED"
    """인증 정보 없음."""

    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    """토큰이 유효하지 않음."""

    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    """토큰 만료."""

    AUTH_CREDENTIALS_INVALID = "AUTH_CREDENTIALS_INVALID"
    """자격 증명 불일치."""

    # =========================================================================
    # AUTHZ: 인가(권한) 실패 (403 Forbidden)
    # =========================================================================
    AUTHZ_PERMISSION_DENIED = "AUTHZ_PERMISSION_DENIED"
    """권한 없음."""

    AUTHZ_GOVERNANCE_BLOCKED = "AUTHZ_GOVERNANCE_BLOCKED"
    """거버넌스 정책에 의해 차단."""

    AUTHZ_ERROR_BUDGET_BLOCKED = "AUTHZ_ERROR_BUDGET_BLOCKED"
    """에러 예산 소진으로 자동화 차단."""

    # =========================================================================
    # RESOURCE: 리소스 관련 (404 Not Found, 409 Conflict)
    # =========================================================================
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    """리소스를 찾을 수 없음."""

    RESOURCE_ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
    """리소스가 이미 존재함."""

    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    """리소스 상태 충돌."""

    # =========================================================================
    # RATE: 요청 제한 (429 Too Many Requests)
    # =========================================================================
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    """요청 제한 초과."""

    RATE_THROTTLED = "RATE_THROTTLED"
    """일시적 요청 제한."""

    # =========================================================================
    # CONFIG: 설정 관련 (409 Conflict)
    # =========================================================================
    CONFIG_LOCKED = "CONFIG_LOCKED"
    """설정이 다른 작업에 의해 잠김 (Canary 롤아웃 등)."""

    CONFIG_INVALID = "CONFIG_INVALID"
    """설정값이 유효하지 않음."""

    # =========================================================================
    # SYSTEM: 시스템 내부 오류 (500 Internal Server Error)
    # =========================================================================
    SYSTEM_INTERNAL_ERROR = "SYSTEM_INTERNAL_ERROR"
    """예기치 않은 내부 오류."""

    SYSTEM_DATABASE_ERROR = "SYSTEM_DATABASE_ERROR"
    """데이터베이스 오류."""

    SYSTEM_DLQ_ERROR = "SYSTEM_DLQ_ERROR"
    """DLQ 저장/조회 실패."""

    # =========================================================================
    # SERVICE: 외부 서비스 관련 (502, 503, 504)
    # =========================================================================
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    """외부 서비스 이용 불가."""

    SERVICE_CIRCUIT_OPEN = "SERVICE_CIRCUIT_OPEN"
    """Circuit Breaker가 OPEN 상태."""

    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    """외부 서비스 타임아웃."""

    SERVICE_BAD_GATEWAY = "SERVICE_BAD_GATEWAY"
    """외부 서비스 응답 오류."""


# =============================================================================
# HTTP 상태 코드 매핑
# =============================================================================

ERROR_CODE_TO_HTTP_STATUS: dict[ErrorCode, int] = {
    # VALIDATION → 400
    ErrorCode.VALIDATION_FIELD_REQUIRED: 400,
    ErrorCode.VALIDATION_FIELD_INVALID: 400,
    ErrorCode.VALIDATION_INVALID_VALUE: 400,
    ErrorCode.VALIDATION_SERIALIZER_ERROR: 400,
    ErrorCode.VALIDATION_PARSE_ERROR: 400,
    # AUTH → 401
    ErrorCode.AUTH_NOT_AUTHENTICATED: 401,
    ErrorCode.AUTH_TOKEN_INVALID: 401,
    ErrorCode.AUTH_TOKEN_EXPIRED: 401,
    ErrorCode.AUTH_CREDENTIALS_INVALID: 401,
    # AUTHZ → 403
    ErrorCode.AUTHZ_PERMISSION_DENIED: 403,
    ErrorCode.AUTHZ_GOVERNANCE_BLOCKED: 403,
    ErrorCode.AUTHZ_ERROR_BUDGET_BLOCKED: 403,
    # RESOURCE → 404, 409
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.RESOURCE_ALREADY_EXISTS: 409,
    ErrorCode.RESOURCE_CONFLICT: 409,
    # RATE → 429
    ErrorCode.RATE_LIMIT_EXCEEDED: 429,
    ErrorCode.RATE_THROTTLED: 429,
    # CONFIG → 409
    ErrorCode.CONFIG_LOCKED: 409,
    ErrorCode.CONFIG_INVALID: 400,
    # SYSTEM → 500
    ErrorCode.SYSTEM_INTERNAL_ERROR: 500,
    ErrorCode.SYSTEM_DATABASE_ERROR: 500,
    ErrorCode.SYSTEM_DLQ_ERROR: 500,
    # SERVICE → 502, 503, 504
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.SERVICE_CIRCUIT_OPEN: 503,
    ErrorCode.SERVICE_TIMEOUT: 504,
    ErrorCode.SERVICE_BAD_GATEWAY: 502,
}


# =============================================================================
# 재시도 가능 여부 매핑
# =============================================================================

ERROR_CODE_RETRYABLE: dict[ErrorCode, bool] = {
    # VALIDATION → 재시도 불가 (입력 수정 필요)
    ErrorCode.VALIDATION_FIELD_REQUIRED: False,
    ErrorCode.VALIDATION_FIELD_INVALID: False,
    ErrorCode.VALIDATION_INVALID_VALUE: False,
    ErrorCode.VALIDATION_SERIALIZER_ERROR: False,
    ErrorCode.VALIDATION_PARSE_ERROR: False,
    # AUTH → 재시도 불가 (재인증 필요)
    ErrorCode.AUTH_NOT_AUTHENTICATED: False,
    ErrorCode.AUTH_TOKEN_INVALID: False,
    ErrorCode.AUTH_TOKEN_EXPIRED: False,
    ErrorCode.AUTH_CREDENTIALS_INVALID: False,
    # AUTHZ → 재시도 불가 (권한 없음)
    ErrorCode.AUTHZ_PERMISSION_DENIED: False,
    ErrorCode.AUTHZ_GOVERNANCE_BLOCKED: False,
    ErrorCode.AUTHZ_ERROR_BUDGET_BLOCKED: False,
    # RESOURCE → 재시도 불가
    ErrorCode.RESOURCE_NOT_FOUND: False,
    ErrorCode.RESOURCE_ALREADY_EXISTS: False,
    ErrorCode.RESOURCE_CONFLICT: False,
    # RATE → 재시도 가능 (대기 후)
    ErrorCode.RATE_LIMIT_EXCEEDED: True,
    ErrorCode.RATE_THROTTLED: True,
    # CONFIG → 조건부 (락 해제 후 가능)
    ErrorCode.CONFIG_LOCKED: True,
    ErrorCode.CONFIG_INVALID: False,
    # SYSTEM → 조건부 (일시적 오류일 수 있음)
    ErrorCode.SYSTEM_INTERNAL_ERROR: True,
    ErrorCode.SYSTEM_DATABASE_ERROR: True,
    ErrorCode.SYSTEM_DLQ_ERROR: True,
    # SERVICE → 재시도 가능 (서비스 복구 후)
    ErrorCode.SERVICE_UNAVAILABLE: True,
    ErrorCode.SERVICE_CIRCUIT_OPEN: True,
    ErrorCode.SERVICE_TIMEOUT: True,
    ErrorCode.SERVICE_BAD_GATEWAY: True,
}


# =============================================================================
# 사용자 친화적 기본 메시지
# =============================================================================

ERROR_CODE_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    # VALIDATION
    ErrorCode.VALIDATION_FIELD_REQUIRED: "필수 필드가 누락되었습니다.",
    ErrorCode.VALIDATION_FIELD_INVALID: "필드값 형식이 올바르지 않습니다.",
    ErrorCode.VALIDATION_INVALID_VALUE: "값이 허용 범위를 벗어났습니다.",
    ErrorCode.VALIDATION_SERIALIZER_ERROR: "입력값 검증에 실패했습니다.",
    ErrorCode.VALIDATION_PARSE_ERROR: "요청 본문을 파싱할 수 없습니다.",
    # AUTH
    ErrorCode.AUTH_NOT_AUTHENTICATED: "인증이 필요합니다.",
    ErrorCode.AUTH_TOKEN_INVALID: "유효하지 않은 인증 토큰입니다.",
    ErrorCode.AUTH_TOKEN_EXPIRED: "인증 토큰이 만료되었습니다.",
    ErrorCode.AUTH_CREDENTIALS_INVALID: "자격 증명이 올바르지 않습니다.",
    # AUTHZ
    ErrorCode.AUTHZ_PERMISSION_DENIED: "이 작업을 수행할 권한이 없습니다.",
    ErrorCode.AUTHZ_GOVERNANCE_BLOCKED: "거버넌스 정책에 의해 차단되었습니다.",
    ErrorCode.AUTHZ_ERROR_BUDGET_BLOCKED: "에러 예산 소진으로 자동화가 차단되었습니다.",
    # RESOURCE
    ErrorCode.RESOURCE_NOT_FOUND: "요청한 리소스를 찾을 수 없습니다.",
    ErrorCode.RESOURCE_ALREADY_EXISTS: "리소스가 이미 존재합니다.",
    ErrorCode.RESOURCE_CONFLICT: "리소스 상태 충돌이 발생했습니다.",
    # RATE
    ErrorCode.RATE_LIMIT_EXCEEDED: "요청 제한을 초과했습니다. 잠시 후 다시 시도하세요.",
    ErrorCode.RATE_THROTTLED: "요청이 일시적으로 제한되었습니다.",
    # CONFIG
    ErrorCode.CONFIG_LOCKED: "설정이 다른 작업에 의해 잠겨 있습니다.",
    ErrorCode.CONFIG_INVALID: "설정값이 유효하지 않습니다.",
    # SYSTEM
    ErrorCode.SYSTEM_INTERNAL_ERROR: "내부 서버 오류가 발생했습니다.",
    ErrorCode.SYSTEM_DATABASE_ERROR: "데이터베이스 오류가 발생했습니다.",
    ErrorCode.SYSTEM_DLQ_ERROR: "DLQ 처리 중 오류가 발생했습니다.",
    # SERVICE
    ErrorCode.SERVICE_UNAVAILABLE: "서비스를 일시적으로 이용할 수 없습니다.",
    ErrorCode.SERVICE_CIRCUIT_OPEN: "서비스가 일시적으로 차단되었습니다. 잠시 후 다시 시도하세요.",
    ErrorCode.SERVICE_TIMEOUT: "서비스 응답 시간이 초과되었습니다.",
    ErrorCode.SERVICE_BAD_GATEWAY: "외부 서비스 응답 오류가 발생했습니다.",
}


# =============================================================================
# 유틸리티 함수
# =============================================================================


def get_http_status(code: ErrorCode) -> int:
    """에러 코드에 해당하는 HTTP 상태 코드 반환."""
    return ERROR_CODE_TO_HTTP_STATUS.get(code, 500)


def is_retryable(code: ErrorCode) -> bool:
    """에러 코드가 재시도 가능한지 여부 반환."""
    return ERROR_CODE_RETRYABLE.get(code, False)


def get_default_message(code: ErrorCode) -> str:
    """에러 코드의 기본 사용자 메시지 반환."""
    return ERROR_CODE_DEFAULT_MESSAGES.get(code, "오류가 발생했습니다.")


def get_error_info(code: ErrorCode) -> tuple[int, bool, str]:
    """
    에러 코드의 전체 정보 반환.

    Returns:
        Tuple of (http_status, retryable, default_message)
    """
    return (
        get_http_status(code),
        is_retryable(code),
        get_default_message(code),
    )


__all__ = [
    "ErrorCode",
    "ERROR_CODE_TO_HTTP_STATUS",
    "ERROR_CODE_RETRYABLE",
    "ERROR_CODE_DEFAULT_MESSAGES",
    "get_http_status",
    "is_retryable",
    "get_default_message",
    "get_error_info",
]
