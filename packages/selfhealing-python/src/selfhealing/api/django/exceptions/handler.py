"""
DRF 커스텀 예외 핸들러.

Django REST Framework의 예외 처리를 확장하여 표준화된 에러 응답을 반환합니다.
모든 예외는 ExceptionClassifier로 분류되고, StandardErrorResponse로 응답됩니다.
예외 발생 시 RequestAuditBuffer에 이벤트가 적재됩니다.

주요 기능:
    - 표준화된 에러 응답 포맷
    - Audit 버퍼 연동 (AuditMiddleware와 통합)
    - 민감정보 자동 마스킹
    - Prometheus 메트릭 수집
    - Pool Timeout (SQLAlchemy) 감지 및 503 반환

설정 방법 (settings.py):
    REST_FRAMEWORK = {
        'EXCEPTION_HANDLER': 'selfhealing.api.django.exceptions.handler.selfhealing_exception_handler',
    }
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.response import Response

logger = logging.getLogger(__name__)


# =============================================================================
# Prometheus 메트릭 (선택적 의존성)
# =============================================================================

_METRICS_INITIALIZED = False
_api_exception_total = None
_api_exception_by_code = None
_api_exception_by_category = None


def _init_metrics():
    """Prometheus 메트릭 초기화 (prometheus_client 있을 때만)."""
    global _METRICS_INITIALIZED, _api_exception_total, _api_exception_by_code, _api_exception_by_category

    if _METRICS_INITIALIZED:
        return

    try:
        from prometheus_client import Counter, REGISTRY

        # 이미 등록된 메트릭이 있는지 확인
        try:
            _api_exception_total = REGISTRY._names_to_collectors.get("selfhealing_api_exception_total")
        except (AttributeError, KeyError):
            _api_exception_total = None

        if _api_exception_total is None:
            _api_exception_total = Counter(
                "selfhealing_api_exception_total",
                "API 예외 발생 총 횟수",
                ["path", "method", "status_code"],
            )

        try:
            _api_exception_by_code = REGISTRY._names_to_collectors.get("selfhealing_api_exception_by_code")
        except (AttributeError, KeyError):
            _api_exception_by_code = None

        if _api_exception_by_code is None:
            _api_exception_by_code = Counter(
                "selfhealing_api_exception_by_code",
                "에러 코드별 API 예외 횟수",
                ["error_code"],
            )

        try:
            _api_exception_by_category = REGISTRY._names_to_collectors.get("selfhealing_api_exception_by_category")
        except (AttributeError, KeyError):
            _api_exception_by_category = None

        if _api_exception_by_category is None:
            _api_exception_by_category = Counter(
                "selfhealing_api_exception_by_category",
                "카테고리별 API 예외 횟수",
                ["category"],
            )

        _METRICS_INITIALIZED = True
        logger.debug("[ExceptionHandler] Prometheus metrics initialized")

    except ImportError:
        logger.debug("[ExceptionHandler] prometheus_client not available, metrics disabled")
        _METRICS_INITIALIZED = True


def _record_metrics(
    path: Optional[str],
    method: Optional[str],
    status_code: int,
    error_code: str,
    category: str,
) -> None:
    """Prometheus 메트릭 기록."""
    _init_metrics()

    try:
        if _api_exception_total is not None:
            _api_exception_total.labels(
                path=path or "unknown",
                method=method or "unknown",
                status_code=str(status_code),
            ).inc()

        if _api_exception_by_code is not None:
            _api_exception_by_code.labels(error_code=error_code).inc()

        if _api_exception_by_category is not None:
            _api_exception_by_category.labels(category=category).inc()

    except Exception as e:
        logger.debug(f"[ExceptionHandler] Failed to record metrics: {e}")


# =============================================================================
# Pool Timeout 감지 (SQLAlchemy 호환)
# =============================================================================

try:
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SATimeoutError = type(None)  # Never matches
    SQLALCHEMY_AVAILABLE = False


def _is_pool_timeout(exc: Exception) -> bool:
    """SQLAlchemy Pool Timeout 여부 확인."""
    error_str = str(exc).lower()
    error_type = type(exc).__name__

    return (
        (SQLALCHEMY_AVAILABLE and isinstance(exc, SATimeoutError))
        or "queuepool limit" in error_str
        or "connection timed out" in error_str
        or "pool exhausted" in error_str
        or ("timeout" in error_type.lower() and "pool" in error_str)
    )


def selfhealing_exception_handler(
    exc: Exception,
    context: Dict[str, Any],
) -> Optional["Response"]:
    """
    DRF 커스텀 예외 핸들러.

    모든 예외를 표준화된 형식으로 변환하고 Audit 버퍼에 기록합니다.
    Pool Timeout 감지 시 503 Service Unavailable을 반환합니다.

    Args:
        exc: 발생한 예외
        context: DRF 컨텍스트 (view, request, format, args, kwargs)

    Returns:
        Response 객체 또는 None (None이면 예외 재발생)
    """
    from rest_framework.response import Response
    from rest_framework.views import exception_handler as drf_exception_handler

    from .classifier import get_exception_classifier, ClassifiedError
    from .response import StandardErrorResponse
    from .codes import ErrorCode

    # 요청 정보 추출
    request = context.get("request")
    request_id = _extract_request_id(request)
    path = _extract_path(request)
    method = _extract_method(request)

    # Pool Timeout 우선 처리 (SQLAlchemy 연동)
    if _is_pool_timeout(exc):
        logger.error(f"[ExceptionHandler] Pool Timeout detected: {type(exc).__name__}: {exc}")

        # 표준 응답 생성 (SERVICE_UNAVAILABLE)
        from .classifier import ExceptionCategory, ClassifiedError

        pool_classified = ClassifiedError(
            category=ExceptionCategory.SERVICE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            http_status=503,
            message="서비스를 일시적으로 이용할 수 없습니다.",
            detail="Database connection pool exhausted",
            retryable=True,
            exception_class=type(exc).__name__,
            extra={"retry_after": 10},
        )

        standard_response = StandardErrorResponse.from_classified_error(
            classified=pool_classified,
            request_id=request_id,
            path=path,
            method=method,
        )

        # Audit 및 메트릭 기록
        _record_audit_event(request, exc, pool_classified, standard_response)
        _record_metrics(path, method, 503, ErrorCode.SERVICE_UNAVAILABLE.value, "service")

        response = Response(
            data=standard_response.to_dict(),
            status=503,
        )
        response["Retry-After"] = "10"
        return response

    # DRF 기본 핸들러 먼저 호출 (DRF가 처리할 수 있는 예외인지 확인)
    drf_response = drf_exception_handler(exc, context)

    # 예외 분류
    classifier = get_exception_classifier()
    classified = classifier.classify(exc)

    # 표준 응답 생성
    standard_response = StandardErrorResponse.from_classified_error(
        classified=classified,
        request_id=request_id,
        path=path,
        method=method,
    )

    # Audit 버퍼에 이벤트 적재 (민감정보 마스킹 포함)
    _record_audit_event(request, exc, classified, standard_response)

    # Prometheus 메트릭 기록
    _record_metrics(
        path=path,
        method=method,
        status_code=classified.http_status,
        error_code=classified.code.value,
        category=classified.category.value,
    )

    # 로깅
    _log_exception(exc, classified, request_id, path, method)

    # DRF Response 생성
    response = Response(
        data=standard_response.to_dict(),
        status=standard_response.http_status,
    )

    # DRF 응답에서 헤더 복사 (Throttled의 Retry-After 등)
    if drf_response is not None:
        for header_name, header_value in drf_response.items():
            response[header_name] = header_value

    return response


def _extract_request_id(request: Optional["Request"]) -> Optional[str]:
    """요청에서 request_id 추출 또는 생성."""
    if request is None:
        return str(uuid.uuid4())

    # META에서 기존 request_id 찾기
    if hasattr(request, "META"):
        # 일반적인 헤더 패턴들
        for header in [
            "HTTP_X_REQUEST_ID",
            "HTTP_X_CORRELATION_ID",
            "HTTP_REQUEST_ID",
        ]:
            if request.META.get(header):
                return request.META[header]

        # AuditMiddleware가 생성한 request_id
        from selfhealing.audit.event_buffer import RequestAuditBuffer

        buffer = RequestAuditBuffer.get(request)
        if buffer and buffer.request_id:
            return buffer.request_id

    return str(uuid.uuid4())


def _extract_path(request: Optional["Request"]) -> Optional[str]:
    """요청에서 경로 추출."""
    if request is None:
        return None
    return getattr(request, "path", None)


def _extract_method(request: Optional["Request"]) -> Optional[str]:
    """요청에서 HTTP 메서드 추출."""
    if request is None:
        return None
    return getattr(request, "method", None)


def _record_audit_event(
    request: Optional["Request"],
    exc: Exception,
    classified: "ClassifiedError",
    response: StandardErrorResponse,
) -> None:
    """
    Audit 버퍼에 예외 이벤트 적재.

    AuditMiddleware가 응답 반환 시 이 이벤트를 수집하여 기록합니다.
    민감정보는 자동으로 마스킹됩니다.
    Audit 기록 실패가 응답을 막지 않습니다 (fail-open).
    """
    if request is None:
        return

    try:
        from selfhealing.audit.event_buffer import (
            RequestAuditBuffer,
            AuditEventType,
        )

        buffer = RequestAuditBuffer.get_or_create(request)

        # 예외 카테고리에 따라 이벤트 타입 결정
        event_type = _get_audit_event_type(classified)

        # 상세 정보 구성
        details: Dict[str, Any] = {
            "error_code": classified.code.value,
            "exception_class": classified.exception_class,
            "category": classified.category.value,
            "http_status": classified.http_status,
            "path": response.meta.path,
            "method": response.meta.method,
        }

        # 추가 메타데이터
        if classified.field:
            details["field"] = classified.field

        if classified.extra:
            details["extra"] = classified.extra

        # 에러 메시지에서 민감정보 마스킹
        error_message = _mask_error_message(str(exc)[:500])

        buffer.add(
            event_type=event_type,
            source="ExceptionHandler",
            details=details,
            success=False,
            error_message=error_message,
        )

    except Exception as e:
        # Audit 실패가 응답을 막지 않음
        logger.debug(f"[ExceptionHandler] Failed to record audit event: {e}")


def _get_audit_event_type(classified: "ClassifiedError") -> "AuditEventType":
    """
    분류된 예외에 해당하는 AuditEventType 반환.

    API 예외 전용 이벤트 타입을 사용하여 AuditMiddleware에서
    ERROR_DETECTED 중복 기록을 방지합니다.
    """
    from selfhealing.audit.event_buffer import AuditEventType
    from .classifier import ExceptionCategory

    # 카테고리별 매핑 - API 예외 전용 이벤트 타입 사용
    category_to_event_type = {
        ExceptionCategory.VALIDATION: AuditEventType.API_VALIDATION_ERROR,
        ExceptionCategory.AUTH: AuditEventType.API_AUTH_ERROR,
        ExceptionCategory.AUTHZ: AuditEventType.API_AUTH_ERROR,
        ExceptionCategory.NOT_FOUND: AuditEventType.API_NOT_FOUND,
        ExceptionCategory.CONFLICT: AuditEventType.API_EXCEPTION,
        ExceptionCategory.RATE_LIMIT: AuditEventType.API_THROTTLED,
        ExceptionCategory.INTERNAL: AuditEventType.API_EXCEPTION,
        ExceptionCategory.SERVICE: AuditEventType.API_EXCEPTION,
    }

    return category_to_event_type.get(
        classified.category,
        AuditEventType.API_EXCEPTION,
    )


def _mask_error_message(message: str) -> str:
    """
    에러 메시지에서 민감정보 마스킹.

    패스워드, 토큰, API 키 등의 패턴을 감지하여 마스킹합니다.
    """
    try:
        from selfhealing.audit.masking import mask_sensitive_fields

        # 메시지를 딕셔너리로 감싸서 마스킹 후 다시 추출
        # 단순 문자열에서 민감 패턴 감지
        sensitive_patterns = [
            "password",
            "token",
            "api_key",
            "apikey",
            "secret",
            "authorization",
            "credential",
        ]

        message_lower = message.lower()
        for pattern in sensitive_patterns:
            if pattern in message_lower:
                # 민감정보가 포함된 것으로 보이면 상세 정보 숨김
                return f"[MASKED] Error message may contain sensitive data"

        return message

    except ImportError:
        return message
    except Exception:
        return message


def _log_exception(
    exc: Exception,
    classified: "ClassifiedError",
    request_id: Optional[str],
    path: Optional[str],
    method: Optional[str],
) -> None:
    """예외 로깅."""
    log_msg = (
        f"[ExceptionHandler] {classified.exception_class}: "
        f"code={classified.code.value}, "
        f"status={classified.http_status}, "
        f"path={path}, method={method}, "
        f"request_id={request_id}"
    )

    # 5xx 에러는 ERROR 레벨, 4xx는 WARNING
    if classified.http_status >= 500:
        logger.error(log_msg, exc_info=exc)
    else:
        logger.warning(log_msg)


__all__ = [
    "selfhealing_exception_handler",
]
