"""
L2 Storage Drift Reconciliation API Views.

Endpoints:
- GET  /api/self-healing/l2-storage/drift/stats/                     - Get drift reconciliation stats
- GET  /api/self-healing/l2-storage/drift/history/                   - Get drift reconciliation history
- POST /api/self-healing/l2-storage/drift/reconcile/                 - Force drift reconciliation
- POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/  - Reconcile single service

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.views.l2_storage_utils import get_layered_repository

logger = logging.getLogger(__name__)


class DriftReconciliationStatsView(APIView):
    """
    Drift Reconciliation Statistics API.

    GET /api/self-healing/l2-storage/drift/stats/ - Get drift reconciliation stats
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get drift reconciliation statistics."""
        try:
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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error getting drift stats: {e}",
                exc_info=True,
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DriftReconciliationHistoryView(APIView):
    """
    Drift Reconciliation History API.

    GET /api/self-healing/l2-storage/drift/history/ - Get drift reconciliation history
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get drift reconciliation history."""
        try:
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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error getting drift history: {e}",
                exc_info=True,
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DriftReconciliationTriggerView(APIView):
    """
    Drift Reconciliation Trigger API.

    POST /api/self-healing/l2-storage/drift/reconcile/ - Force drift reconciliation
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """
        Force drift reconciliation for all services.

        This triggers the "Most Restrictive Wins" drift reconciliation
        for all circuit breaker states between L1 and L2.

        Jitter is NOT applied in manual trigger mode.

        Returns:
            Reconciliation results with counts
        """
        try:
            repo = get_layered_repository()

            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = repo.force_drift_reconciliation()

            logger.info(
                f"[L2StorageAPI] Manual drift reconciliation by {request.user}: "
                f"reconciled={result.get('reconciled', 0)}, "
                f"l1_wins={result.get('l1_wins', 0)}, "
                f"l2_wins={result.get('l2_wins', 0)}"
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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error triggering drift reconciliation: {e}",
                exc_info=True,
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DriftReconciliationServiceView(APIView):
    """
    Drift Reconciliation for Single Service API.

    POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/ 
        - Reconcile single service
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §6
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request, service_name: str) -> Response:
        """
        Force drift reconciliation for a specific service.

        Args:
            service_name: Name of the service to reconcile

        Returns:
            Reconciliation result for the service
        """
        try:
            repo = get_layered_repository()

            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = repo.reconcile_single_service(service_name)

            if not result.get("success", False):
                logger.warning(
                    f"[L2StorageAPI] Drift reconciliation failed for {service_name}: "
                    f"{result.get('reason', 'unknown')}"
                )
                return Response(
                    {
                        "status": "error",
                        "error": result.get("reason", "Reconciliation failed"),
                        "result": result,
                        "timestamp": timezone.now(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(
                f"[L2StorageAPI] Drift reconciliation for {service_name} by {request.user}: "
                f"action={result.get('action', 'none')}, winner={result.get('winner', 'n/a')}"
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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error reconciling {service_name}: {e}",
                exc_info=True,
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
