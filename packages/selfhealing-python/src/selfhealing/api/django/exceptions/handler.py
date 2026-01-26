"""
DRF 커스텀 예외 핸들러.

Django REST Framework의 예외 처리를 확장하여 표준화된 에러 응답을 반환합니다.
모든 예외는 ExceptionClassifier로 분류되고, StandardErrorResponse로 응답됩니다.
예외 발생 시 RequestAuditBuffer에 이벤트가 적재됩니다.

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


def selfhealing_exception_handler(
    exc: Exception,
    context: Dict[str, Any],
) -> Optional["Response"]:
    """
    DRF 커스텀 예외 핸들러.
    
    모든 예외를 표준화된 형식으로 변환하고 Audit 버퍼에 기록합니다.
    
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
    
    # 요청 정보 추출
    request = context.get("request")
    request_id = _extract_request_id(request)
    path = _extract_path(request)
    method = _extract_method(request)
    
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
    
    # Audit 버퍼에 이벤트 적재
    _record_audit_event(request, exc, classified, standard_response)
    
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
        
        buffer.add(
            event_type=event_type,
            source="ExceptionHandler",
            details=details,
            success=False,
            error_message=str(exc)[:500],  # 길이 제한
        )
        
    except Exception as e:
        # Audit 실패가 응답을 막지 않음
        logger.debug(f"[ExceptionHandler] Failed to record audit event: {e}")


def _get_audit_event_type(classified: "ClassifiedError") -> "AuditEventType":
    """분류된 예외에 해당하는 AuditEventType 반환."""
    from selfhealing.audit.event_buffer import AuditEventType
    from .classifier import ExceptionCategory
    
    # 카테고리별 매핑
    category_to_event_type = {
        ExceptionCategory.VALIDATION: AuditEventType.ERROR_DETECTED,
        ExceptionCategory.AUTH: AuditEventType.ERROR_DETECTED,
        ExceptionCategory.AUTHZ: AuditEventType.GOVERNANCE_BLOCKED,
        ExceptionCategory.NOT_FOUND: AuditEventType.ERROR_DETECTED,
        ExceptionCategory.CONFLICT: AuditEventType.ERROR_DETECTED,
        ExceptionCategory.RATE_LIMIT: AuditEventType.RATE_LIMITED,
        ExceptionCategory.INTERNAL: AuditEventType.ERROR_DETECTED,
        ExceptionCategory.SERVICE: AuditEventType.ERROR_DETECTED,
    }
    
    return category_to_event_type.get(
        classified.category,
        AuditEventType.ERROR_DETECTED,
    )


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
