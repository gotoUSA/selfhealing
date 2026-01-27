"""
L2 Storage Status and Health API Views.

Endpoints:
- GET  /api/self-healing/l2-storage/status/      - Get L2 storage status (Viewer)
- GET  /api/self-healing/l2-storage/health/      - Get L2 health status (Viewer)
- POST /api/self-healing/l2-storage/health/reset - Reset L2 health status (Admin)
- POST /api/self-healing/l2-storage/sync/from-l2 - Force sync from L2 (Admin)
- POST /api/self-healing/l2-storage/sync/to-l2   - Force sync to L2 (Admin)
- GET  /api/self-healing/l2-storage/metrics/     - Get L2 storage metrics (Viewer)
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.views.l2_storage_utils import get_layered_repository

logger = logging.getLogger(__name__)


class L2StorageStatusView(APIView):
    """
    L2 Storage Status API.

    GET /api/self-healing/l2-storage/status/ - Get storage status (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get L2 storage status including metrics."""
        repo = get_layered_repository()

        if repo is None:
            return Response(
                {
                    "status": "success",
                    "message": "Layered storage not configured",
                    "storage_type": "memory_only",
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        storage_info = repo.get_storage_info()

        return Response(
            {
                "status": "success",
                "storage_info": storage_info,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageHealthView(APIView):
    """
    L2 Storage Health API.

    GET /api/self-healing/l2-storage/health/ - Get health status (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get L2 health status."""
        repo = get_layered_repository()

        if repo is None:
            return Response(
                {
                    "status": "success",
                    "health": {
                        "healthy": True,
                        "message": "Layered storage not configured (memory-only mode)",
                    },
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        health = repo.get_l2_health()

        return Response(
            {
                "status": "success",
                "health": health,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageHealthResetView(APIView):
    """
    L2 Storage Health Reset API.

    POST /api/self-healing/l2-storage/health/reset/ - Reset health status (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Reset L2 health status (mark as healthy)."""
        repo = get_layered_repository()

        if repo is None:
            raise ValueError("Layered storage not configured")

        repo.reset_l2_health()

        logger.info(f"[L2StorageAPI] L2 health reset by {request.user}")

        return Response(
            {
                "status": "success",
                "message": "L2 health status reset",
                "health": repo.get_l2_health(),
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageSyncFromL2View(APIView):
    """
    L2 Storage Sync From L2 API.

    POST /api/self-healing/l2-storage/sync/from-l2 - Force sync from L2 (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Force sync from L2 to L1."""
        repo = get_layered_repository()

        if repo is None:
            raise ValueError("Layered storage not configured")

        success = repo.force_sync_from_l2()

        if not success:
            raise RuntimeError("Sync from L2 failed")

        logger.info(f"[L2StorageAPI] Force sync from L2 by {request.user}")
        return Response(
            {
                "status": "success",
                "message": "Synced from L2 successfully",
                "storage_info": repo.get_storage_info(),
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageSyncToL2View(APIView):
    """
    L2 Storage Sync To L2 API.

    POST /api/self-healing/l2-storage/sync/to-l2 - Force sync to L2 (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Force sync from L1 to L2."""
        repo = get_layered_repository()

        if repo is None:
            raise ValueError("Layered storage not configured")

        result = repo.force_sync_to_l2()

        logger.info(f"[L2StorageAPI] Force sync to L2 by {request.user}: {result}")

        return Response(
            {
                "status": "success" if result["success"] else "partial",
                "message": "Sync to L2 completed",
                "result": result,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageMetricsView(APIView):
    """
    L2 Storage Metrics API.

    GET /api/self-healing/l2-storage/metrics/ - Get L2 storage metrics (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get L2 storage metrics."""
        repo = get_layered_repository()

        if repo is None:
            return Response(
                {
                    "status": "success",
                    "message": "Layered storage not configured",
                    "metrics": {},
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        metrics = repo.get_metrics()

        # Calculate derived metrics
        if metrics.get("l2_latency_count", 0) > 0:
            metrics["avg_latency_ms"] = round(
                metrics["l2_latency_total_ms"] / metrics["l2_latency_count"],
                2,
            )
        else:
            metrics["avg_latency_ms"] = 0.0

        return Response(
            {
                "status": "success",
                "metrics": metrics,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리
