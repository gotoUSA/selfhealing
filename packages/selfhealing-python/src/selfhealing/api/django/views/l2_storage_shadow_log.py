"""
L2 Storage Shadow Log API Views.

Endpoints:
- GET  /api/self-healing/l2-storage/shadow-log/                    - Get shadow log entries
- GET  /api/self-healing/l2-storage/shadow-log/stats/              - Get shadow log stats
- POST /api/self-healing/l2-storage/shadow-log/clear/              - Clear shadow log
- GET  /api/self-healing/l2-storage/shadow-log/analyze/            - Analyze L2 failures
- POST /api/self-healing/l2-storage/shadow-log/replay/             - Replay unsynced records
- GET  /api/self-healing/l2-storage/shadow-log/service/<name>/     - Get logs by service
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsOperator, IsSelfHealingAdmin
from selfhealing.api.django.views.l2_storage_utils import (
    get_layered_repository,
    get_shadow_logger,
)

logger = logging.getLogger(__name__)


class ShadowLogListView(APIView):
    """
    Shadow Log List API.

    GET /api/self-healing/l2-storage/shadow-log/ - Get shadow log entries (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get shadow log entries."""
        shadow_logger = get_shadow_logger()
        
        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")
        
        # Query parameters
        unsynced_only = request.query_params.get("unsynced_only", "false").lower() == "true"
        limit = int(request.query_params.get("limit", 100))
        
        if unsynced_only:
            records = shadow_logger.get_unsynced_records()
        else:
            records = shadow_logger.get_all_records()
        
        # Apply limit
        records = records[-limit:] if len(records) > limit else records
        
        # Serialize
        entries = [
            {
                "service_name": r.service_name,
                "intended_state": r.intended_state,
                "failure_time": r.failure_time.isoformat(),
                "error_message": r.error_message,
                "l1_state_at_failure": r.l1_state_at_failure,
                "adapter_type": r.adapter_type,
                "operation": r.operation,
                "synced_after_recovery": r.synced_after_recovery,
                "recovery_time": r.recovery_time.isoformat() if r.recovery_time else None,
            }
            for r in records
        ]
        
        return Response(
            {
                "status": "success",
                "count": len(entries),
                "entries": entries,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class ShadowLogStatsView(APIView):
    """
    Shadow Log Statistics API.

    GET /api/self-healing/l2-storage/shadow-log/stats/ - Get shadow log stats (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """Get shadow log statistics."""
        shadow_logger = get_shadow_logger()
        
        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")
        
        stats = shadow_logger.get_stats()
        
        return Response(
            {
                "status": "success",
                "stats": stats,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class ShadowLogClearView(APIView):
    """
    Shadow Log Clear API.

    POST /api/self-healing/l2-storage/shadow-log/clear/ - Clear shadow log (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Clear all shadow log entries."""
        shadow_logger = get_shadow_logger()
        
        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")
        
        # Get stats before clearing
        stats_before = shadow_logger.get_stats()
        
        shadow_logger.clear()
        
        logger.warning(
            f"[L2StorageAPI] Shadow log cleared by {request.user}. "
            f"Cleared {stats_before['total_records']} entries."
        )
        
        return Response(
            {
                "status": "success",
                "message": "Shadow log cleared",
                "cleared_count": stats_before["total_records"],
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class ShadowLogAnalyzeView(APIView):
    """
    Shadow Log Forensic Analysis API.

    GET /api/self-healing/l2-storage/shadow-log/analyze/ - Analyze L2 failures (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """
        Analyze L2 failures for forensic investigation.

        Returns a comprehensive analysis of L2 failures including:
        - Timeline of failures
        - Affected services
        - Adapter-specific statistics
        - Recommendations for recovery
        """
        shadow_logger = get_shadow_logger()

        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")

        analysis = shadow_logger.analyze_l2_failures()

        return Response(
            {
                "status": "success",
                "analysis": analysis,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class ShadowLogReplayView(APIView):
    """
    Shadow Log Replay API.

    POST /api/self-healing/l2-storage/shadow-log/replay/ - Replay unsynced records (Operator)
    """

    permission_classes = [IsOperator]

    def post(self, request: Request) -> Response:
        """
        Replay unsynced shadow log records to L2.

        This attempts to re-synchronize all failed L2 operations
        that were recorded in the shadow log during L2 outages.

        Request body (optional):
            service_name: Replay only for specific service
            mark_synced: Whether to mark records as synced after replay (default: true)

        Returns:
            Replay results with success/failure counts
        """
        shadow_logger = get_shadow_logger()
        repo = get_layered_repository()

        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")

        if repo is None:
            raise ValueError("Layered storage not configured")

        # Parse options
        service_name = request.data.get("service_name")
        mark_synced = request.data.get("mark_synced", True)

        # Get records to replay
        if service_name:
            records = shadow_logger.get_records_by_service(service_name)
            records = [r for r in records if not r.synced_after_recovery]
        else:
            records = shadow_logger.get_unsynced_records()

        if not records:
            return Response(
                {
                    "status": "success",
                    "message": "No unsynced records to replay",
                    "replayed": 0,
                    "failed": 0,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        # Attempt replay via force sync to L2
        result = repo.force_sync_to_l2()

        # Mark records as synced if requested and sync was successful
        marked_count = 0
        if mark_synced and result.get("success", False):
            if service_name:
                marked_count = shadow_logger.mark_as_synced(service_name)
            else:
                marked_count = shadow_logger.mark_all_as_synced()

        logger.info(
            f"[L2StorageAPI] Shadow log replay by {request.user}: "
            f"synced={result.get('synced', 0)}, failed={result.get('failed', 0)}, "
            f"marked={marked_count}"
        )

        return Response(
            {
                "status": "success" if result.get("success", False) else "partial",
                "message": "Shadow log replay completed",
                "records_found": len(records),
                "sync_result": result,
                "marked_as_synced": marked_count,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class ShadowLogByServiceView(APIView):
    """
    Shadow Log by Service API.

    GET /api/self-healing/l2-storage/shadow-log/service/<service_name>/ - Get logs by service (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, service_name: str) -> Response:
        """Get shadow log entries for a specific service."""
        shadow_logger = get_shadow_logger()

        if shadow_logger is None:
            raise RuntimeError("Shadow logger not available")

        records = shadow_logger.get_records_by_service(service_name)

        entries = [
            {
                "service_name": r.service_name,
                "intended_state": r.intended_state,
                "failure_time": r.failure_time.isoformat(),
                "error_message": r.error_message,
                "l1_state_at_failure": r.l1_state_at_failure,
                "adapter_type": r.adapter_type,
                "operation": r.operation,
                "synced_after_recovery": r.synced_after_recovery,
                "recovery_time": r.recovery_time.isoformat() if r.recovery_time else None,
            }
            for r in records
        ]

        return Response(
            {
                "status": "success",
                "service_name": service_name,
                "count": len(entries),
                "entries": entries,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리
