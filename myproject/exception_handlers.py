"""
Custom DRF Exception Handler - Pool Timeout 503 반환.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)

# SQLAlchemy TimeoutError import
try:
    from sqlalchemy.exc import TimeoutError as SATimeoutError
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SATimeoutError = type(None)  # Never matches
    SQLALCHEMY_AVAILABLE = False


def custom_exception_handler(exc, context):
    """
    DRF 커스텀 예외 핸들러.
    
    SQLAlchemy Pool Timeout 발생 시 503 반환.
    """
    # Pool Timeout 감지
    error_str = str(exc).lower()
    error_type = type(exc).__name__
    
    is_pool_timeout = (
        (SQLALCHEMY_AVAILABLE and isinstance(exc, SATimeoutError)) or
        "queuepool limit" in error_str or
        "connection timed out" in error_str or
        "pool exhausted" in error_str or
        "timeout" in error_type.lower()
    )
    
    if is_pool_timeout:
        logger.error(f"[PoolTimeout] 503 반환! {error_type}: {exc}")
        return Response(
            {
                "error": "Service temporarily unavailable",
                "reason": "Database connection pool exhausted",
                "error_type": error_type,
                "retry_after": 10,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "10"},
        )
    
    # 기본 DRF 핸들러 호출
    response = exception_handler(exc, context)
    
    # 처리 안 된 예외 로깅
    if response is None:
        logger.error(f"[UnhandledException] {error_type}: {exc}")
    
    return response
