"""
Simple Pool Timeout Middleware.

SQLAlchemy Pool Timeout 발생 시 즉시 503 반환.
복잡한 Circuit Breaker 로직 없이 직접적으로 처리.
"""

import logging
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# SQLAlchemy TimeoutError import
try:
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SATimeoutError = Exception
    SQLALCHEMY_AVAILABLE = False


class PoolTimeoutMiddleware:
    """
    Pool Timeout 발생 시 503 반환하는 단순 미들웨어.

    SQLAlchemy Pool에서 연결을 가져오지 못하면 (timeout),
    즉시 503 Service Unavailable 반환.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._timeout_count = 0
        logger.info("[PoolTimeoutMiddleware] Initialized - will catch Pool timeouts!")

    def __call__(self, request):
        try:
            response = self.get_response(request)
            return response
        except Exception as e:
            # Pool Timeout 감지
            error_str = str(e).lower()
            error_type = type(e).__name__

            is_pool_timeout = (
                (SQLALCHEMY_AVAILABLE and isinstance(e, SATimeoutError))
                or "timeout" in error_str
                or "queuepool limit" in error_str
                or "pool exhausted" in error_str
                or "no connections available" in error_str
                or "can't get connection" in error_str
            )

            if is_pool_timeout:
                self._timeout_count += 1
                logger.error(
                    f"[PoolTimeoutMiddleware] Pool Timeout #{self._timeout_count}! "
                    f"Path: {request.path}, Error: {error_type}: {e}"
                )
                return JsonResponse(
                    {
                        "error": "Service temporarily unavailable",
                        "reason": "Database connection pool exhausted",
                        "error_type": error_type,
                        "retry_after": 10,
                    },
                    status=503,
                    headers={"Retry-After": "10"},
                )

            # 다른 예외는 그대로 전파
            raise
