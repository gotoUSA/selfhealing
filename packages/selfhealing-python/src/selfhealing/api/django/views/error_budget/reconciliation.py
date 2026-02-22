"""
Reconciliation Views (Shadow Budget).

Shadow Budget 계산 및 Fail-Safe 기간 관리 API.

Endpoints:
- GET  /api/self-healing/reconciliation/status/
- GET  /api/self-healing/reconciliation/failsafe-periods/
- GET  /api/self-healing/reconciliation/shadow-budgets/
- POST /api/self-healing/reconciliation/shadow-budgets/
- GET  /api/self-healing/reconciliation/shadow-budgets/{id}/
- POST /api/self-healing/reconciliation/shadow-budgets/{id}/approve/
- POST /api/self-healing/reconciliation/shadow-budgets/{id}/reject/
- GET  /api/self-healing/reconciliation/excluded-periods/
- POST /api/self-healing/reconciliation/excluded-periods/
- DELETE /api/self-healing/reconciliation/excluded-periods/{id}/
- GET  /api/self-healing/reconciliation/config/
- PUT  /api/self-healing/reconciliation/config/

Core Principle: "시스템은 계산하고, 반영은 사람이 결정한다."
"""

import structlog
from datetime import datetime

from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer

logger = structlog.get_logger()


class ReconciliationStatusView(APIView):
    """
    Reconciliation 상태 조회 API.

    GET /api/self-healing/reconciliation/status/

    Returns:
    - Fail-Safe period tracker status
    - Pending shadow budgets count
    - Excluded periods count
    - Configuration
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        service = get_reconciliation_service()
        status_data = service.get_status()

        return Response(
            {
                "status": "success",
                "data": status_data,
                "timestamp": timezone.now().isoformat(),
            }
        )


class FailSafePeriodsView(APIView):
    """
    Fail-Safe 기간 목록 조회 API.

    GET /api/self-healing/reconciliation/failsafe-periods/

    Query Parameters:
    - limit: Maximum records (default: 50)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import get_period_tracker

        limit = int(request.query_params.get("limit", 50))
        tracker = get_period_tracker()
        periods = tracker.get_all_periods(limit=limit)

        return Response(
            {
                "status": "success",
                "data": {
                    "periods": [p.to_dict() for p in periods],
                    "count": len(periods),
                    "active_period": (tracker.get_active_period().to_dict() if tracker.get_active_period() else None),
                },
                "timestamp": timezone.now().isoformat(),
            }
        )


class ShadowBudgetsView(APIView):
    """
    Shadow Budget 목록 조회 및 계산 트리거 API.

    GET /api/self-healing/reconciliation/shadow-budgets/
    POST /api/self-healing/reconciliation/shadow-budgets/

    Query Parameters (GET):
    - limit: Maximum records (default: 50)
    - pending_only: Only pending approval (default: false)

    Request Body (POST):
    {
        "period_id": "uuid"
    }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        limit = int(request.query_params.get("limit", 50))
        pending_only = request.query_params.get("pending_only", "false").lower() == "true"

        service = get_reconciliation_service()

        if pending_only:
            budgets = service.get_pending_shadow_budgets()
        else:
            budgets = service.get_all_shadow_budgets(limit=limit)

        return Response(
            {
                "status": "success",
                "data": {
                    "shadow_budgets": [sb.to_dict() for sb in budgets],
                    "count": len(budgets),
                },
                "timestamp": timezone.now().isoformat(),
            }
        )

    def post(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        period_id = request.data.get("period_id")
        if not period_id:
            raise ValueError("period_id is required")

        service = get_reconciliation_service()
        shadow = service.calculate_shadow_budget(period_id)

        if not shadow:
            raise ValueError("Failed to calculate shadow budget")

        return Response(
            {
                "status": "success",
                "data": shadow.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class ShadowBudgetDetailView(APIView):
    """
    Shadow Budget 상세 조회 API.

    GET /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, calculation_id: str) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        service = get_reconciliation_service()
        shadow = service.get_shadow_budget(calculation_id)

        if not shadow:
            from django.http import Http404

            raise Http404(f"Shadow budget {calculation_id} not found")

        return Response(
            {
                "status": "success",
                "data": shadow.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class ShadowBudgetApproveView(APIView):
    """
    Shadow Budget 승인 API.

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/approve/

    승인 시 Primary Budget에 반영됩니다 (Capped 모드: 최대 10%p/cycle).
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, calculation_id: str) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        justification = request.data.get("justification", "")
        if not justification:
            raise ValueError("justification is required")

        approved_by = getattr(request.user, "username", str(request.user))

        service = get_reconciliation_service()
        shadow = service.approve_shadow_budget(
            calculation_id=calculation_id,
            approved_by=approved_by,
            justification=justification,
        )

        if not shadow:
            raise ValueError("Shadow budget not found or invalid status")

        return Response(
            {
                "status": "success",
                "message": f"Shadow budget approved and applied (adjustment: {shadow.adjustment_percent:.2f}%)",
                "data": shadow.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class ShadowBudgetRejectView(APIView):
    """
    Shadow Budget 거부 API (Excluded Period로 처리).

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/reject/
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, calculation_id: str) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        reason = request.data.get("reason", "")
        if not reason:
            raise ValueError("reason is required")

        rejected_by = getattr(request.user, "username", str(request.user))

        service = get_reconciliation_service()
        shadow = service.reject_shadow_budget(
            calculation_id=calculation_id,
            rejected_by=rejected_by,
            reason=reason,
        )

        if not shadow:
            raise ValueError("Shadow budget not found or invalid status")

        return Response(
            {
                "status": "success",
                "message": "Shadow budget rejected, period excluded from calculation",
                "data": shadow.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class ExcludedPeriodsView(APIView):
    """
    Excluded Period 관리 API.

    GET /api/self-healing/reconciliation/excluded-periods/
    POST /api/self-healing/reconciliation/excluded-periods/

    Query Parameters (GET):
    - limit: Maximum records (default: 50)

    Request Body (POST):
    {
        "start": "2024-01-15T10:00:00Z",
        "end": "2024-01-15T10:30:00Z",
        "reason": "Chaos Engineering 실험",
        "notes": "optional notes"
    }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        limit = int(request.query_params.get("limit", 50))
        service = get_reconciliation_service()
        periods = service.get_excluded_periods(limit=limit)

        return Response(
            {
                "status": "success",
                "data": {
                    "excluded_periods": [p.to_dict() for p in periods],
                    "count": len(periods),
                },
                "timestamp": timezone.now().isoformat(),
            }
        )

    def post(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        start_str = request.data.get("start")
        end_str = request.data.get("end")
        reason = request.data.get("reason", "")

        if not all([start_str, end_str, reason]):
            raise ValueError("start, end, and reason are required")

        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        notes = request.data.get("notes", "")
        excluded_by = getattr(request.user, "username", str(request.user))

        service = get_reconciliation_service()
        exclusion = service.exclude_period(
            start=start,
            end=end,
            reason=reason,
            excluded_by=excluded_by,
            notes=notes,
        )

        return Response(
            {
                "status": "success",
                "message": "Period excluded from budget calculation",
                "data": exclusion.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class ExcludedPeriodDetailView(APIView):
    """
    Excluded Period 삭제 API.

    DELETE /api/self-healing/reconciliation/excluded-periods/{exclusion_id}/

    제외된 기간을 다시 Budget 계산에 포함시킵니다.
    """

    permission_classes = [IsSelfHealingAdmin]

    def delete(self, request: Request, exclusion_id: str) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        service = get_reconciliation_service()
        success = service.remove_exclusion(exclusion_id)

        if not success:
            from django.http import Http404

            raise Http404(f"Exclusion {exclusion_id} not found")

        return Response(
            {
                "status": "success",
                "message": "Exclusion removed, period re-included in calculation",
                "timestamp": timezone.now().isoformat(),
            }
        )


class ReconciliationConfigView(APIView):
    """
    Reconciliation 설정 조회/변경 API.

    GET /api/self-healing/reconciliation/config/
    PUT /api/self-healing/reconciliation/config/

    Request Body (PUT):
    {
        "auto_calculate": true,
        "auto_apply": false,
        "apply_mode": "capped",
        "max_adjustment_percent_per_cycle": 10.0
    }
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        service = get_reconciliation_service()
        config = service.get_config()

        return Response(
            {
                "status": "success",
                "data": config.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )

    def put(self, request: Request) -> Response:
        from selfhealing.services.error_budget.reconciliation import (
            get_reconciliation_service,
        )

        service = get_reconciliation_service()
        config = service.update_config(**request.data)

        return Response(
            {
                "status": "success",
                "message": "Reconciliation config updated",
                "data": config.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


__all__ = [
    "ReconciliationStatusView",
    "FailSafePeriodsView",
    "ShadowBudgetsView",
    "ShadowBudgetDetailView",
    "ShadowBudgetApproveView",
    "ShadowBudgetRejectView",
    "ExcludedPeriodsView",
    "ExcludedPeriodDetailView",
    "ReconciliationConfigView",
]
