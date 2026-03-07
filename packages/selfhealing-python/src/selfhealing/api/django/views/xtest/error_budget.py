"""
X-Test-Mode Error Budget Views

Error Budget 관련 테스트 API:
- InjectErrorBudgetView: Error Budget 차감 주입
"""

import structlog
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .base import XTestModeMixin

logger = structlog.get_logger()


class InjectErrorBudgetView(XTestModeMixin, APIView):
    """
    Error Budget 차감 주입 API.

    POST /api/self-healing/xtest/inject-error-budget/

    Request:
        {
            "error_type": "critical",  // critical, major, minor
            "count": 10
        }
    """

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        error_type = request.data.get("error_type", "critical")
        count = int(request.data.get("count", 10))

        # 최대 주입 횟수 제한
        max_injection = 100
        if count > max_injection:
            return Response(
                {
                    "status": "error",
                    "error": "injection_limit_exceeded",
                    "message": f"Maximum injection count is {max_injection}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Exception은 exception handler가 처리 (ImportError는 모듈 미설치 케이스로 별도 처리)
        try:
            from selfhealing.services.error_budget import (
                get_error_budget_service,
            )
        except ImportError:
            return Response(
                {
                    "status": "warning",
                    "message": "Error Budget service not available",
                    "hint": "Error Budget may not be configured in this environment",
                }
            )

        eb_service = get_error_budget_service()

        # 이전 상태
        initial_budget = eb_service.get_remaining_budget_percent()

        # 에러 주입
        for i in range(count):
            eb_service.record_error(
                error_type=error_type,
                context={
                    "source": "x-test-mode",
                    "injection_number": i + 1,
                    "user": str(request.user),
                },
            )

        # 현재 상태
        current_budget = eb_service.get_remaining_budget_percent()
        budget_status = eb_service.get_budget_status()

        logger.info(
            "test_mode_error_budget",
            error_type=error_type,
            count=count,
            initial_budget=initial_budget,
            current_budget=current_budget,
            request_user=request.user,
        )

        return Response(
            {
                "status": "success",
                "error_type": error_type,
                "injected_count": count,
                "initial_budget_percent": initial_budget,
                "current_budget_percent": current_budget,
                "budget_consumed": initial_budget - current_budget,
                "budget_status": budget_status,
                "timestamp": timezone.now().isoformat(),
            }
        )


__all__ = [
    "InjectErrorBudgetView",
]
