"""
L2 Storage Resilience API Views.

REST API endpoints for L2 storage configuration and monitoring.

Endpoints:
- GET  /api/self-healing/l2-storage/config/      - Get L2 storage config
- PUT  /api/self-healing/l2-storage/config/      - Update L2 storage config
- POST /api/self-healing/l2-storage/config/reset - Reset config to defaults
- GET  /api/self-healing/l2-storage/status/      - Get L2 storage status
- GET  /api/self-healing/l2-storage/health/      - Get L2 health status
- POST /api/self-healing/l2-storage/health/reset - Reset L2 health status
- GET  /api/self-healing/l2-storage/shadow-log/  - Get shadow log entries
- GET  /api/self-healing/l2-storage/shadow-log/stats/ - Get shadow log stats
- POST /api/self-healing/l2-storage/shadow-log/clear/ - Clear shadow log
- POST /api/self-healing/l2-storage/sync/from-l2 - Force sync from L2
- POST /api/self-healing/l2-storage/sync/to-l2   - Force sync to L2
- GET  /api/self-healing/l2-storage/drift/stats/ - Get drift reconciliation stats
- GET  /api/self-healing/l2-storage/drift/history/ - Get drift reconciliation history
- POST /api/self-healing/l2-storage/drift/reconcile/ - Force drift reconciliation
- POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/ - Reconcile single service

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md
"""

import logging
from typing import Optional

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers.config import (
    L2StorageConfigSerializer,
    L2StorageStatusSerializer,
    ShadowLogEntrySerializer,
    ShadowLogStatsSerializer,
)
from selfhealing.config import get_l2_storage_runtime_config

logger = logging.getLogger(__name__)


def _get_layered_repository():
    """Get LayeredCircuitBreakerStateRepository if available."""
    try:
        from selfhealing.services.factory import get_service_factory
        factory = get_service_factory()
        repo = factory.get_circuit_breaker_state_repository()
        
        # Check if it's a LayeredRepository
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
        )
        if isinstance(repo, LayeredCircuitBreakerStateRepository):
            return repo
        return None
    except Exception as e:
        logger.warning(f"[L2StorageAPI] Failed to get layered repository: {e}")
        return None


def _get_shadow_logger():
    """Get ShadowLogger instance."""
    try:
        from selfhealing.adapters.memory.circuit_breaker import get_shadow_logger
        return get_shadow_logger()
    except Exception as e:
        logger.warning(f"[L2StorageAPI] Failed to get shadow logger: {e}")
        return None


class L2StorageConfigView(APIView):
    """
    L2 Storage Configuration API.

    GET  /api/self-healing/l2-storage/config/ - Get current config
    PUT  /api/self-healing/l2-storage/config/ - Update config
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get current L2 storage configuration."""
        try:
            config = get_l2_storage_runtime_config()
            
            return Response(
                {
                    "status": "success",
                    "config": config.to_dict(),
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update L2 storage configuration."""
        try:
            serializer = L2StorageConfigSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            config = get_l2_storage_runtime_config()
            changes = serializer.get_config_changes()
            
            if not changes:
                return Response(
                    {"status": "error", "error": "No changes provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            updated_config = config.update(
                **changes,
                updated_by=str(request.user),
            )
            
            logger.info(
                f"[L2StorageAPI] Config updated by {request.user}: {changes}"
            )
            
            return Response(
                {
                    "status": "success",
                    "message": "L2 storage configuration updated",
                    "config": updated_config,
                    "changes": changes,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as e:
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error updating config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageConfigResetView(APIView):
    """
    L2 Storage Configuration Reset API.

    POST /api/self-healing/l2-storage/config/reset - Reset to defaults
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Reset L2 storage configuration to defaults."""
        try:
            config = get_l2_storage_runtime_config()
            config.reset()
            
            logger.info(f"[L2StorageAPI] Config reset to defaults by {request.user}")
            
            return Response(
                {
                    "status": "success",
                    "message": "L2 storage configuration reset to defaults",
                    "config": config.to_dict(),
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error resetting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageStatusView(APIView):
    """
    L2 Storage Status API.

    GET /api/self-healing/l2-storage/status/ - Get storage status
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get L2 storage status including metrics."""
        try:
            repo = _get_layered_repository()
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting status: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageHealthView(APIView):
    """
    L2 Storage Health API.

    GET  /api/self-healing/l2-storage/health/       - Get health status
    POST /api/self-healing/l2-storage/health/reset/ - Reset health status
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get L2 health status."""
        try:
            repo = _get_layered_repository()
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting health: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageHealthResetView(APIView):
    """
    L2 Storage Health Reset API.

    POST /api/self-healing/l2-storage/health/reset/ - Reset health status
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Reset L2 health status (mark as healthy)."""
        try:
            repo = _get_layered_repository()
            
            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error resetting health: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogListView(APIView):
    """
    Shadow Log List API.

    GET /api/self-healing/l2-storage/shadow-log/ - Get shadow log entries
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get shadow log entries."""
        try:
            shadow_logger = _get_shadow_logger()
            
            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting shadow log: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogStatsView(APIView):
    """
    Shadow Log Statistics API.

    GET /api/self-healing/l2-storage/shadow-log/stats/ - Get shadow log stats
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get shadow log statistics."""
        try:
            shadow_logger = _get_shadow_logger()
            
            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            
            stats = shadow_logger.get_stats()
            
            return Response(
                {
                    "status": "success",
                    "stats": stats,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting shadow log stats: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogClearView(APIView):
    """
    Shadow Log Clear API.

    POST /api/self-healing/l2-storage/shadow-log/clear/ - Clear shadow log
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Clear all shadow log entries."""
        try:
            shadow_logger = _get_shadow_logger()
            
            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error clearing shadow log: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageSyncFromL2View(APIView):
    """
    L2 Storage Sync From L2 API.

    POST /api/self-healing/l2-storage/sync/from-l2 - Force sync from L2
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Force sync from L2 to L1."""
        try:
            repo = _get_layered_repository()
            
            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            success = repo.force_sync_from_l2()
            
            if success:
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
            else:
                return Response(
                    {
                        "status": "error",
                        "error": "Sync from L2 failed",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error syncing from L2: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageSyncToL2View(APIView):
    """
    L2 Storage Sync To L2 API.

    POST /api/self-healing/l2-storage/sync/to-l2 - Force sync to L2
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Force sync from L1 to L2."""
        try:
            repo = _get_layered_repository()
            
            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            result = repo.force_sync_to_l2()
            
            logger.info(
                f"[L2StorageAPI] Force sync to L2 by {request.user}: {result}"
            )
            
            return Response(
                {
                    "status": "success" if result["success"] else "partial",
                    "message": "Sync to L2 completed",
                    "result": result,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error syncing to L2: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class L2StorageMetricsView(APIView):
    """
    L2 Storage Metrics API.

    GET /api/self-healing/l2-storage/metrics/ - Get L2 storage metrics
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get L2 storage metrics."""
        try:
            repo = _get_layered_repository()
            
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
        except Exception as e:
            logger.error(f"[L2StorageAPI] Error getting metrics: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogAnalyzeView(APIView):
    """
    Shadow Log Forensic Analysis API.

    GET /api/self-healing/l2-storage/shadow-log/analyze/ - Analyze L2 failures
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """
        Analyze L2 failures for forensic investigation.

        Returns a comprehensive analysis of L2 failures including:
        - Timeline of failures
        - Affected services
        - Adapter-specific statistics
        - Recommendations for recovery
        """
        try:
            shadow_logger = _get_shadow_logger()

            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            analysis = shadow_logger.analyze_l2_failures()

            return Response(
                {
                    "status": "success",
                    "analysis": analysis,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error analyzing shadow log: {e}", exc_info=True
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogReplayView(APIView):
    """
    Shadow Log Replay API.

    POST /api/self-healing/l2-storage/shadow-log/replay/ - Replay unsynced records
    """

    permission_classes = [IsAdminUser]

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
        try:
            shadow_logger = _get_shadow_logger()
            repo = _get_layered_repository()

            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if repo is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Layered storage not configured",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error replaying shadow log: {e}", exc_info=True
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowLogByServiceView(APIView):
    """
    Shadow Log by Service API.

    GET /api/self-healing/l2-storage/shadow-log/service/<service_name>/ - Get logs by service
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request, service_name: str) -> Response:
        """Get shadow log entries for a specific service."""
        try:
            shadow_logger = _get_shadow_logger()

            if shadow_logger is None:
                return Response(
                    {"status": "error", "error": "Shadow logger not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

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
        except Exception as e:
            logger.error(
                f"[L2StorageAPI] Error getting shadow log for service: {e}",
                exc_info=True,
            )
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Drift Reconciliation API Views
# =============================================================================


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
            repo = _get_layered_repository()

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
            repo = _get_layered_repository()

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
            repo = _get_layered_repository()

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
            repo = _get_layered_repository()

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
