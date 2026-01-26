"""
FinOps DNA API Views

FinOps DNA 서비스의 REST API 엔드포인트
"""

from decimal import Decimal
import logging
from typing import List

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import BasePermission

from selfhealing.api.django.permissions import IsViewer, IsOperator, IsSelfHealingAdmin

logger = logging.getLogger(__name__)


def get_finops_service():
    """FinOps 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.finops.service import FinOpsService

        return FinOpsService()
    except ImportError:
        return None


class FinOpsBudgetView(APIView):
    """
    FinOps 예산 관리 API

    GET    /api/self-healing/finops/budget/              - 예산 조회 (Viewer)
    GET    /api/self-healing/finops/budget/<stage_name>/ - 예산 조회 (Viewer)
    POST   /api/self-healing/finops/budget/<stage_name>/ - 예산 설정 (Admin)
    DELETE /api/self-healing/finops/budget/<stage_name>/ - 예산 리셋 (Admin)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request, stage_name: str = None):
        """예산 조회"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if stage_name:
            budget = service.get_budget(stage_name)
            if budget:
                return Response(budget.to_dict())
            return Response(
                {"error": "Budget not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
            budgets = service.get_all_budgets()
            return Response({"budgets": budgets})

    def post(self, request, stage_name: str):
        """예산 설정"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
        budget = service.set_budget(
            stage_name=stage_name,
            max_budget=Decimal(str(data.get("max_budget", "10.00"))),
            alert_threshold=data.get("alert_threshold", 0.8),
            hard_limit=data.get("hard_limit", True),
            reset_period=data.get("reset_period", "daily"),
        )
        return Response(budget.to_dict(), status=status.HTTP_201_CREATED)

    def delete(self, request, stage_name: str):
        """예산 리셋"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.reset_budget(stage_name):
            return Response({"message": "Budget reset successfully"})
        return Response(
            {"error": "Budget not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


class FinOpsCostView(APIView):
    """
    FinOps 비용 기록 API

    POST /api/self-healing/finops/cost/ - 비용 기록 (Operator)
    """

    permission_classes = [IsOperator]

    def post(self, request):
        """비용 기록"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
        cost = data.get("cost")
        record = service.record_cost(
            operation=data.get("operation"),
            stage_name=data.get("stage_name"),
            cost=Decimal(str(cost)) if cost else None,
            success=data.get("success", True),
            metadata=data.get("metadata", {}),
        )
        return Response(record.to_dict(), status=status.HTTP_201_CREATED)


class FinOpsReportView(APIView):
    """
    FinOps 리포트 API

    GET /api/self-healing/finops/report/ - 리포트 생성 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        """리포트 생성"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        period = request.query_params.get("period", "daily")
        stage_name = request.query_params.get("stage_name")

        report = service.generate_report(period=period, stage_name=stage_name)
        return Response(report.to_dict())


class FinOpsAlertsView(APIView):
    """
    FinOps 알림 API

    GET  /api/self-healing/finops/alerts/                - 알림 조회 (Viewer)
    POST /api/self-healing/finops/alerts/<alert_index>/  - 알림 확인 처리 (Operator)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request):
        """알림 조회"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        unacknowledged = (
            request.query_params.get("unacknowledged", "false").lower() == "true"
        )

        alerts = service.get_alerts(
            stage_name=stage_name,
            unacknowledged_only=unacknowledged,
        )
        return Response({"alerts": [a.to_dict() for a in alerts]})

    def post(self, request, alert_index: int):
        """알림 확인 처리"""
        service = get_finops_service()
        if not service:
            return Response(
                {"error": "FinOps service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.acknowledge_alert(alert_index):
            return Response({"message": "Alert acknowledged"})
        return Response(
            {"error": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
