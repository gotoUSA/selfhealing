"""
Self-Healing DLQ (Dead Letter Queue) Views.

REST API endpoints for DLQ management.
Business logic is delegated to DLQService.

Endpoints:
- POST /api/self-healing/dlq/replay/ - Trigger DLQ replay
- GET  /api/self-healing/dlq/cleanup/stats/ - Get cleanup statistics
- POST /api/self-healing/dlq/cleanup/archive/ - Archive old resolved entries
- POST /api/self-healing/dlq/cleanup/purge/ - Permanently delete archived entries
- GET  /api/self-healing/dlq/list/ - List DLQ entries with pagination
- GET  /api/self-healing/dlq/<pk>/ - Get single DLQ entry details
- POST /api/self-healing/dlq/<pk>/retry/ - Retry a single entry
- POST /api/self-healing/dlq/<pk>/resolve/ - Manually resolve an entry
- POST /api/self-healing/dlq/test/create/ - Create test entry (DEBUG only)
"""

import structlog
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import (
    IsOperator,
    IsSelfHealingAdmin,
    IsSelfHealingAuthenticated,
    IsViewer,
)
from selfhealing.api.django.serializers import DLQReplayRequestSerializer
from selfhealing.services.dlq import get_dlq_service

logger = structlog.get_logger()


class DLQReplayView(APIView):
    """
    DLQ Replay API.

    POST /api/self-healing/dlq/replay/

    Note: Operator-level endpoint - requires selfhealing_operator role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsOperator]

    def post(self, request):
        """Trigger DLQ replay."""
        serializer = DLQReplayRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        domain = serializer.validated_data.get("domain")
        batch_size = serializer.validated_data.get("batch_size", 50)

        service = get_dlq_service()
        result = service.replay(domain=domain, batch_size=batch_size)

        logger.info(
            "dlq.replay_triggered_via_api",
            domain=domain,
            batch_size=batch_size,
            result=result.processed,
            result_3=result.success,
            result_4=result.failed,
        )

        return Response(
            {
                "status": "success",
                "total": result.processed,
                "success_count": result.success,
                "failed_count": result.failed,
                "skipped_count": result.skipped,
            }
        )


class DLQCleanupStatsView(APIView):
    """
    DLQ Cleanup Statistics API.

    GET /api/self-healing/dlq/cleanup/stats/

    Note: Read-only endpoint - Viewer role or higher can access.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsViewer]

    def get(self, request):
        """Get cleanup statistics."""
        service = get_dlq_service()
        stats = service.get_cleanup_stats()

        return Response(
            {
                "total": stats.total,
                "by_status": stats.by_status,
                "resolved_older_than_30_days": stats.resolved_older_than_30_days,
                "archived_older_than_90_days": stats.archived_older_than_90_days,
                "recommendations": {
                    "can_archive": stats.can_archive,
                    "can_purge": stats.can_purge,
                },
            }
        )


class DLQArchiveView(APIView):
    """
    DLQ Archive API.

    POST /api/self-healing/dlq/cleanup/archive/

    Body:
    {
        "older_than_days": 30  (optional, default 30)
    }

    Note: Operator-level endpoint - requires selfhealing_operator role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsOperator]

    def post(self, request):
        """Archive old resolved entries."""
        older_than_days = int(request.data.get("older_than_days", 30))

        service = get_dlq_service()
        count = service.archive_old_entries(older_than_days=older_than_days)

        logger.info(
            "dlq.archived_entries_via_api",
            count=count,
            older_than_days=older_than_days,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "archived_count": count,
                "older_than_days": older_than_days,
            }
        )


class DLQPurgeView(APIView):
    """
    DLQ Purge API (DESTRUCTIVE).

    POST /api/self-healing/dlq/cleanup/purge/

    Body:
    {
        "ids": [1, 2, 3]  (optional, specific IDs to purge)
        "older_than_days": 90  (optional, purge archived older than N days)
        "confirm": true  (required for safety)
    }

    If neither ids nor older_than_days specified, purges ALL archived.

    Note: Admin-only endpoint - requires selfhealing_admin role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsSelfHealingAdmin]

    def post(self, request):
        """Permanently delete archived entries."""
        confirm = request.data.get("confirm", False)
        if not confirm:
            raise ValueError("Safety check: set confirm=true to proceed with deletion")

        ids = request.data.get("ids")
        older_than_days = request.data.get("older_than_days")

        if older_than_days is not None:
            older_than_days = int(older_than_days)

        service = get_dlq_service()
        count = service.purge_archived(ids=ids, older_than_days=older_than_days)

        logger.warning(
            "dlq.purged_archived_entries_via",
            count=count,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "purged_count": count,
                "warning": "This action is irreversible",
            }
        )


class DLQListView(APIView):
    """
    DLQ List API.

    GET /api/self-healing/dlq/list/

    Query Parameters:
    - status: Filter by status (pending, resolved, archived, failed)
    - domain: Filter by domain
    - page: Page number (default 1)
    - page_size: Items per page (default 20, max 100)

    Note: Read-only endpoint - Viewer role or higher can access.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsViewer]

    def get(self, request):
        """Get paginated list of DLQ entries."""
        # Build filters from query params
        filters = {}
        status_filter = request.query_params.get("status")
        domain_filter = request.query_params.get("domain")

        if status_filter:
            filters["status"] = status_filter
        if domain_filter:
            filters["domain"] = domain_filter

        # Pagination
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        service = get_dlq_service()
        result = service.list_entries(filters=filters, page=page, page_size=page_size)

        return Response(
            {
                "results": result.results,
                "pagination": {
                    "page": result.page,
                    "page_size": result.page_size,
                    "total_pages": result.total_pages,
                    "total_count": result.total_count,
                    "has_next": result.has_next,
                    "has_previous": result.has_previous,
                },
            }
        )


class DLQDetailView(APIView):
    """
    DLQ Detail API.

    GET /api/self-healing/dlq/<pk>/

    Note: Read-only endpoint - Viewer role or higher can access.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsViewer]

    def get(self, request, pk):
        """Get detailed info for a single DLQ entry."""
        service = get_dlq_service()
        entry = service.get_entry(pk)

        if entry is None:
            from django.http import Http404

            raise Http404(f"DLQ entry {pk} not found")

        return Response(entry)


class DLQRetryView(APIView):
    """
    DLQ Retry API.

    POST /api/self-healing/dlq/<pk>/retry/

    Retries a single DLQ entry.

    Note: Operator-level endpoint - requires selfhealing_operator role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsOperator]

    def post(self, request, pk):
        """Retry a single DLQ entry."""
        service = get_dlq_service()
        result = service.retry_entry(pk)

        logger.info(
            "dlq.retry_triggered_entry_user",
            pk=pk,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "id": result.id,
                "retry_count": result.retry_count,
                "previous_retry_count": result.previous_retry_count,
                "message": result.message,
            }
        )


class DLQResolveView(APIView):
    """
    DLQ Manual Resolve API.

    POST /api/self-healing/dlq/<pk>/resolve/

    Body:
    {
        "notes": "Reason for manual resolution"  (optional)
    }

    Note: Operator-level endpoint - requires selfhealing_operator role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsOperator]

    def post(self, request, pk):
        """Manually resolve a DLQ entry."""
        notes = request.data.get("notes", f"Manually resolved by {request.user}")

        service = get_dlq_service()
        result = service.resolve_entry(pk, notes=notes)

        logger.info(
            "dlq.entry_manually_resolved_user",
            pk=pk,
            request=request.user,
            notes=notes,
        )

        return Response(
            {
                "status": "success",
                "id": result.id,
                "previous_status": result.previous_status,
                "current_status": result.current_status,
                "resolved_at": result.resolved_at,
                "notes": result.notes,
            }
        )


class DLQTestCreateView(APIView):
    """
    DLQ Test Entry Creation API.

    Create test DLQ entries for load testing and verification.
    Only available in non-production environments (DEBUG=True).

    POST /api/self-healing/dlq/test/create/

    Use Cases:
    - Load test verification of DLQ functionality
    - Integration testing of replay mechanism
    - CI/CD pipeline testing

    Note: Admin-only endpoint - requires selfhealing_admin role.
    """

    permission_classes = [IsSelfHealingAuthenticated, IsSelfHealingAdmin]

    def post(self, request):
        """Create a test DLQ entry (domain-neutral)."""
        domain = request.data.get("domain")
        failure_type = request.data.get("failure_type")

        service = get_dlq_service()
        result = service.create_test_entry(
            domain=domain,
            failure_type=failure_type,
            user_id=request.user.id if request.user else None,
            entity_type=request.data.get("entity_type", "test"),
            entity_id=request.data.get("entity_id", ""),
            error_message=request.data.get("error_message", "Test failure for load testing"),
            snapshot_data=request.data.get("snapshot_data"),
            request_data=request.data.get("request_data"),
            response_data=request.data.get("response_data"),
            metadata=request.data.get("metadata"),
            created_by=str(request.user),
        )

        logger.info(
            "dlq.test_entry_created",
            result=result['dlq_id'],
            domain=domain,
            failure_type=failure_type,
            request=request.user,
        )

        return Response(result, status=status.HTTP_201_CREATED)
