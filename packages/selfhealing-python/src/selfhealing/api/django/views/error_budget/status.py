"""
Error Budget Status Views.

Error Budget 상태 조회 및 기록 관련 API.

Endpoints:
- GET  /api/self-healing/error-budget/status/           - Get Error Budget status (V3: cached)
- GET  /api/self-healing/error-budget/history/          - Get budget consumption history
- POST /api/self-healing/error-budget/record/           - Record errors (Chaos Engineering)
- POST /api/self-healing/error-budget/exhaust/          - Simulate budget exhaustion (Test)
- POST /api/self-healing/error-budget/reset-simulation/ - Reset simulation stats

FAIL-SAFE DESIGN:
- Error Budget 시스템 장애 시 → 기본값 PROCEED (fail-open)
- 배포를 막는 것보다 시스템 가용성이 더 중요

V3 Optimization:
- L1 In-process cache (2s TTL) + L2 Redis cache (15s TTL)
- Target: P95 < 20ms for /error-budget/status/
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.error_budget_service import (
    get_error_budget_service,
    get_failsafe_status_response,
)

logger = logging.getLogger(__name__)


class ErrorBudgetStatusView(APIView):
    """
    Error Budget 상태 조회 API.

    GET /api/self-healing/error-budget/status/

    Returns current Error Budget status including:
    - Budget remaining (minutes, percentage)
    - Burn rate (1h, 6h)
    - SLO information
    - Health status

    Query Parameters:
    - slo_name: SLO name to check (default: "availability")
    - nocache: Set to "true" to bypass cache (V3)
    
    V3 Optimization: Uses multi-tier cache for P95 < 20ms target.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            slo_name = request.query_params.get("slo_name", "availability")
            use_cache = request.query_params.get("nocache", "").lower() != "true"
            
            # V3: Use cached response for default SLO
            if use_cache and slo_name == "availability":
                try:
                    from selfhealing.services.precomputed_cache import get_cached_error_budget
                    return Response(get_cached_error_budget())
                except ImportError:
                    pass  # Fall through to direct computation

            # Direct computation for non-default SLO or if cache unavailable
            service = get_error_budget_service()
            budget_status = service.get_budget_status(slo_name)

            return Response(
                {
                    "status": "success",
                    "data": budget_status.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Status failed: {e}", exc_info=True)
            # FAIL-SAFE: 시스템 장애 시에도 기본 응답 제공 (200 OK)
            return Response(get_failsafe_status_response(str(e)))


class ErrorBudgetHistoryView(APIView):
    """
    Error Budget 결정 이력 조회 API.

    GET /api/self-healing/error-budget/history/

    Query Parameters:
    - limit: Maximum records to return (default: 50)
    - decision_type: Filter by type (freeze_acknowledged, override_approved, freeze_lifted)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            limit = int(request.query_params.get("limit", 50))
            decision_type = request.query_params.get("decision_type")

            service = get_error_budget_service()
            history = service.get_decision_history(limit=limit, decision_type=decision_type)

            return Response(
                {
                    "status": "success",
                    "data": {
                        "records": [r.to_dict() for r in history],
                        "count": len(history),
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] History failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetRecordView(APIView):
    """
    Error Budget 에러 기록 API (Chaos Engineering / Test).

    POST /api/self-healing/error-budget/record/

    Request Body:
    {
        "error_count": 10,           // 기록할 에러 수 (default: 1)
        "error_type": "timeout",     // 에러 유형 (default: "simulated")
        "service_name": "payment"    // 서비스 이름 (default: "test")
    }

    ⚠️ WARNING: 이 API는 테스트/Chaos 환경에서만 사용해야 합니다.
    실제 운영 환경에서는 사용하지 마십시오.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            # 다양한 형식 지원 (호환성)
            error_count = int(request.data.get("error_count", 1))
            error_type = request.data.get("error_type", "simulated")
            service_name = request.data.get("service_name", "test")
            
            # Extended format: domain, severity, multiplier
            domain = request.data.get("domain", service_name)
            severity = request.data.get("severity", "medium")
            multiplier = float(request.data.get("multiplier", 1.0))
            reason = request.data.get("reason", "")
            
            # severity에 따른 가중치 조정
            severity_weights = {
                "low": 1,
                "medium": 3,
                "high": 5,
                "critical": 10,
            }
            base_errors = severity_weights.get(severity, 1)
            effective_errors = int(base_errors * multiplier * error_count)

            if effective_errors < 1:
                effective_errors = 1

            service = get_error_budget_service()
            result = service.record_error(
                error_count=effective_errors,
                error_type=error_type,
                service_name=domain,
            )
            
            # 확장 정보 추가
            result["severity"] = severity
            result["multiplier"] = multiplier
            result["domain"] = domain
            result["effective_errors"] = effective_errors
            if reason:
                result["reason"] = reason

            logger.info(
                f"[ErrorBudgetAPI] Recorded {effective_errors} errors "
                f"(domain={domain}, severity={severity}, multiplier={multiplier})"
            )

            return Response(
                {
                    "status": "success",
                    "message": f"{effective_errors} error(s) recorded for budget consumption",
                    "data": result,
                    "remaining_percent": result.get("budget_remaining_percent"),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Record failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetExhaustView(APIView):
    """
    Error Budget 고갈 시뮬레이션 API (Chaos Engineering / Test).

    POST /api/self-healing/error-budget/exhaust/

    Request Body:
    {
        "target_remaining_percent": 0.0  // 목표 잔여 비율 (default: 0.0 = 완전 고갈)
    }

    ⚠️ WARNING: 이 API는 테스트/Chaos 환경에서만 사용해야 합니다.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            target = float(request.data.get("target_remaining_percent", 0.0))

            if target < 0 or target > 100:
                return Response(
                    {
                        "status": "error",
                        "error": "target_remaining_percent must be between 0 and 100",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            result = service.simulate_budget_exhaustion(target_remaining_percent=target)

            logger.warning(
                f"[ErrorBudgetAPI] Budget exhaustion simulated: target={target}%"
            )

            return Response(
                {
                    "status": "success",
                    "message": f"Budget exhaustion simulated to {target}%",
                    "data": result,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Exhaust failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetResetSimulationView(APIView):
    """
    Error Budget 시뮬레이션 통계 초기화 API.

    POST /api/self-healing/error-budget/reset-simulation/

    시뮬레이션으로 기록된 에러/요청 통계를 초기화합니다.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            service = get_error_budget_service()
            result = service.reset_simulated_stats()

            logger.info("[ErrorBudgetAPI] Simulation stats reset")

            return Response(
                {
                    "status": "success",
                    "message": "Simulation stats reset",
                    "data": result,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Reset simulation failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    "ErrorBudgetStatusView",
    "ErrorBudgetHistoryView",
    "ErrorBudgetRecordView",
    "ErrorBudgetExhaustView",
    "ErrorBudgetResetSimulationView",
]
