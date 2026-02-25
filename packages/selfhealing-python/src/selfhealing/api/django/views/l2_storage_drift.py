"""
L2 Storage Drift Reconciliation API Views.

Endpoints:
- GET  /api/self-healing/l2-storage/drift/stats/                     - Get drift reconciliation stats
- GET  /api/self-healing/l2-storage/drift/history/                   - Get drift reconciliation history
- POST /api/self-healing/l2-storage/drift/reconcile/                 - Force drift reconciliation
- POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/  - Reconcile single service
"""

import structlog
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.views.l2_storage_utils import get_layered_repository

logger = structlog.get_logger()


class DriftReconciliationStatsView(APIView):
    """
    Drift Reconciliation Statistics API.

    GET /api/self-healing/l2-storage/drift/stats/ - Get drift reconciliation stats (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get drift reconciliation statistics."""
        repo = get_layered_repository()

        if repo is None:
            return Response(
                {
                    "status": "success",
                    "message": "Layered storage not configured",
                    "stats": {},
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        stats = repo.get_drift_reconciler_stats()

        return Response(
            {
                "status": "success",
                "stats": stats,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class DriftReconciliationHistoryView(APIView):
    """
    Drift Reconciliation History API.

    GET /api/self-healing/l2-storage/drift/history/ - Get drift reconciliation history (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get drift reconciliation history."""
        repo = get_layered_repository()

        if repo is None:
            return Response(
                {
                    "status": "success",
                    "message": "Layered storage not configured",
                    "history": [],
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        # Query parameters
        limit = int(request.query_params.get("limit", 100))

        history = repo.get_drift_reconciliation_history()

        # Apply limit
        if len(history) > limit:
            history = history[-limit:]

        return Response(
            {
                "status": "success",
                "count": len(history),
                "history": history,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class DriftReconciliationTriggerView(APIView):
    """
    Drift Reconciliation Trigger API.

    POST /api/self-healing/l2-storage/drift/reconcile/ - Force drift reconciliation (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """
        Force drift reconciliation for all services.

        This triggers the "Most Restrictive Wins" drift reconciliation
        for all circuit breaker states between L1 and L2.

        Jitter is NOT applied in manual trigger mode.

        Returns:
            Reconciliation results with counts
        """
        repo = get_layered_repository()

        if repo is None:
            raise ValueError("Layered storage not configured")

        result = repo.force_drift_reconciliation()

        logger.info(
            "l2_storage_api.manual_drift_reconciliation",
            request=request.user,
            result=result.get("reconciled", 0),
            l1_wins=result.get("l1_wins", 0),
            l2_wins=result.get("l2_wins", 0),
        )

        return Response(
            {
                "status": "success" if result.get("success", False) else "partial",
                "message": "Drift reconciliation completed",
                "result": result,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class DriftReconciliationServiceView(APIView):
    """
    Drift Reconciliation for Single Service API.

    POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/
        - Reconcile single service (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, service_name: str) -> Response:
        """
        Force drift reconciliation for a specific service.

        Args:
            service_name: Name of the service to reconcile

        Returns:
            Reconciliation result for the service
        """
        repo = get_layered_repository()

        if repo is None:
            raise ValueError("Layered storage not configured")

        result = repo.reconcile_single_service(service_name)

        if not result.get("success", False):
            logger.warning(
                "l2_storage_api.drift_reconciliation_failed",
                service_name=service_name,
                result=result.get("reason", "unknown"),
            )
            raise ValueError(result.get("reason", "Reconciliation failed"))

        logger.info(
            "l2_storage_api.drift_reconciliation",
            service_name=service_name,
            request=request.user,
            result=result.get("action", "none"),
            winner=result.get("winner", "n/a"),
        )

        return Response(
            {
                "status": "success",
                "message": f"Drift reconciliation for {service_name} completed",
                "service_name": service_name,
                "result": result,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리
