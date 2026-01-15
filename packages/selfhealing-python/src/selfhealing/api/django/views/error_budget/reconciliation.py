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

import logging
from datetime import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin

logger = logging.getLogger(__name__)


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
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            status_data = service.get_status()

            return Response(
                {
                    "status": "success",
                    "data": status_data,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Status failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        try:
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
                        "active_period": tracker.get_active_period().to_dict() if tracker.get_active_period() else None,
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] FailSafe periods failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

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

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budgets failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            period_id = request.data.get("period_id")
            if not period_id:
                return Response(
                    {"status": "error", "error": "period_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_reconciliation_service()
            shadow = service.calculate_shadow_budget(period_id)

            if not shadow:
                return Response(
                    {"status": "error", "error": "Failed to calculate shadow budget"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget calculation failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetDetailView(APIView):
    """
    Shadow Budget 상세 조회 API.

    GET /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            shadow = service.get_shadow_budget(calculation_id)

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "status": "success",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget detail failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetApproveView(APIView):
    """
    Shadow Budget 승인 API.

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/approve/

    승인 시 Primary Budget에 반영됩니다 (Capped 모드: 최대 10%p/cycle).
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            justification = request.data.get("justification", "")
            if not justification:
                return Response(
                    {"status": "error", "error": "justification is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            approved_by = getattr(request.user, "username", str(request.user))

            service = get_reconciliation_service()
            shadow = service.approve_shadow_budget(
                calculation_id=calculation_id,
                approved_by=approved_by,
                justification=justification,
            )

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found or invalid status"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "message": f"Shadow budget approved and applied (adjustment: {shadow.adjustment_percent:.2f}%)",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget approve failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetRejectView(APIView):
    """
    Shadow Budget 거부 API (Excluded Period로 처리).

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/reject/
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            reason = request.data.get("reason", "")
            if not reason:
                return Response(
                    {"status": "error", "error": "reason is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            rejected_by = getattr(request.user, "username", str(request.user))

            service = get_reconciliation_service()
            shadow = service.reject_shadow_budget(
                calculation_id=calculation_id,
                rejected_by=rejected_by,
                reason=reason,
            )

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found or invalid status"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "message": "Shadow budget rejected, period excluded from calculation",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget reject failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

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

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Excluded periods failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            start_str = request.data.get("start")
            end_str = request.data.get("end")
            reason = request.data.get("reason", "")

            if not all([start_str, end_str, reason]):
                return Response(
                    {"status": "error", "error": "start, end, and reason are required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Exclude period failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ExcludedPeriodDetailView(APIView):
    """
    Excluded Period 삭제 API.

    DELETE /api/self-healing/reconciliation/excluded-periods/{exclusion_id}/

    제외된 기간을 다시 Budget 계산에 포함시킵니다.
    """

    permission_classes = [IsSelfHealingAdmin]

    def delete(self, request: Request, exclusion_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            success = service.remove_exclusion(exclusion_id)

            if not success:
                return Response(
                    {"status": "error", "error": "Exclusion not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "status": "success",
                    "message": "Exclusion removed, period re-included in calculation",
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Remove exclusion failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            config = service.get_config()

            return Response(
                {
                    "status": "success",
                    "data": config.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Config get failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

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

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Config update failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
