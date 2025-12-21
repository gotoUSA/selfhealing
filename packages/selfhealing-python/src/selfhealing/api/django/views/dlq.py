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

import logging

from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers import DLQReplayRequestSerializer
from selfhealing.services.dlq_service import get_dlq_service

logger = logging.getLogger(__name__)


class DLQReplayView(APIView):
    """
    DLQ Replay API.

    POST /api/self-healing/dlq/replay/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Trigger DLQ replay."""
        serializer = DLQReplayRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        domain = serializer.validated_data.get("domain")
        batch_size = serializer.validated_data.get("batch_size", 50)

        try:
            service = get_dlq_service()
            result = service.replay(domain=domain, batch_size=batch_size)

            logger.info(
                f"[DLQ] Replay triggered via API: domain={domain}, "
                f"batch_size={batch_size}, processed={result.processed}, "
                f"success={result.success}, failed={result.failed}"
            )

            return Response({
                "status": "success",
                "total": result.processed,
                "success_count": result.success,
                "failed_count": result.failed,
                "skipped_count": result.skipped,
            })

        except Exception as e:
            logger.error(f"[DLQ] Replay failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DLQCleanupStatsView(APIView):
    """
    DLQ Cleanup Statistics API.

    GET /api/self-healing/dlq/cleanup/stats/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """Get cleanup statistics."""
        try:
            service = get_dlq_service()
            stats = service.get_cleanup_stats()

            return Response({
                "total": stats.total,
                "by_status": stats.by_status,
                "resolved_older_than_30_days": stats.resolved_older_than_30_days,
                "archived_older_than_90_days": stats.archived_older_than_90_days,
                "recommendations": {
                    "can_archive": stats.can_archive,
                    "can_purge": stats.can_purge,
                },
            })

        except Exception as e:
            logger.error(f"[DLQ] Cleanup stats failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DLQArchiveView(APIView):
    """
    DLQ Archive API.

    POST /api/self-healing/dlq/cleanup/archive/

    Body:
    {
        "older_than_days": 30  (optional, default 30)
    }
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Archive old resolved entries."""
        try:
            older_than_days = int(request.data.get("older_than_days", 30))

            service = get_dlq_service()
            count = service.archive_old_entries(older_than_days=older_than_days)

            logger.info(
                f"[DLQ] Archived {count} entries via API "
                f"(resolved > {older_than_days} days ago) by user {request.user}"
            )

            return Response({
                "status": "success",
                "archived_count": count,
                "older_than_days": older_than_days,
            })

        except ValueError as e:
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] Archive failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Permanently delete archived entries."""
        try:
            confirm = request.data.get("confirm", False)
            if not confirm:
                return Response(
                    {
                        "status": "error",
                        "error": "Safety check: set confirm=true to proceed with deletion",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            ids = request.data.get("ids")
            older_than_days = request.data.get("older_than_days")

            if older_than_days is not None:
                older_than_days = int(older_than_days)

            service = get_dlq_service()
            count = service.purge_archived(ids=ids, older_than_days=older_than_days)

            logger.warning(f"[DLQ] PURGED {count} archived entries via API by user {request.user}")

            return Response({
                "status": "success",
                "purged_count": count,
                "warning": "This action is irreversible",
            })

        except ValueError as e:
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] Purge failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """Get paginated list of DLQ entries."""
        try:
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

            return Response({
                "results": result.results,
                "pagination": {
                    "page": result.page,
                    "page_size": result.page_size,
                    "total_pages": result.total_pages,
                    "total_count": result.total_count,
                    "has_next": result.has_next,
                    "has_previous": result.has_previous,
                },
            })

        except ValueError as e:
            return Response(
                {"status": "error", "error": f"Invalid parameter: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] List failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DLQDetailView(APIView):
    """
    DLQ Detail API.

    GET /api/self-healing/dlq/<pk>/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request, pk):
        """Get detailed info for a single DLQ entry."""
        try:
            service = get_dlq_service()
            entry = service.get_entry(pk)

            if entry is None:
                return Response(
                    {"status": "error", "error": f"DLQ entry {pk} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(entry)

        except Exception as e:
            logger.error(f"[DLQ] Detail failed for {pk}: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DLQRetryView(APIView):
    """
    DLQ Retry API.

    POST /api/self-healing/dlq/<pk>/retry/

    Retries a single DLQ entry.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, pk):
        """Retry a single DLQ entry."""
        try:
            service = get_dlq_service()
            result = service.retry_entry(pk)

            logger.info(
                f"[DLQ] Retry triggered for entry {pk} by user {request.user}"
            )

            return Response({
                "status": "success",
                "id": result.id,
                "retry_count": result.retry_count,
                "previous_retry_count": result.previous_retry_count,
                "message": result.message,
            })

        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg:
                return Response(
                    {"status": "error", "error": error_msg},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"status": "error", "error": error_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] Retry failed for {pk}: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DLQResolveView(APIView):
    """
    DLQ Manual Resolve API.

    POST /api/self-healing/dlq/<pk>/resolve/

    Body:
    {
        "notes": "Reason for manual resolution"  (optional)
    }
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, pk):
        """Manually resolve a DLQ entry."""
        try:
            notes = request.data.get("notes", f"Manually resolved by {request.user}")

            service = get_dlq_service()
            result = service.resolve_entry(pk, notes=notes)

            logger.info(f"[DLQ] Entry {pk} manually resolved by user {request.user}: {notes}")

            return Response({
                "status": "success",
                "id": result.id,
                "previous_status": result.previous_status,
                "current_status": result.current_status,
                "resolved_at": result.resolved_at,
                "notes": result.notes,
            })

        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg:
                return Response(
                    {"status": "error", "error": error_msg},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"status": "error", "error": error_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] Manual resolve failed for {pk}: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Create a test DLQ entry (domain-neutral)."""
        domain = request.data.get("domain")
        failure_type = request.data.get("failure_type")

        # Build entity_refs from request data
        entity_refs = request.data.get("entity_refs", {})

        try:
            service = get_dlq_service()
            result = service.create_test_entry(
                domain=domain,
                failure_type=failure_type,
                user_id=request.user.id if request.user else None,
                entity_type=request.data.get("entity_type", "test"),
                entity_id=request.data.get("entity_id", ""),
                entity_refs=entity_refs,
                error_message=request.data.get("error_message", "Test failure for load testing"),
                snapshot_data=request.data.get("snapshot_data"),
                request_data=request.data.get("request_data"),
                response_data=request.data.get("response_data"),
                metadata=request.data.get("metadata"),
                created_by=str(request.user),
            )

            logger.info(
                f"[DLQ] Test entry created: id={result['dlq_id']}, "
                f"domain={domain}, failure_type={failure_type}, "
                f"user={request.user}"
            )

            return Response(result, status=status.HTTP_201_CREATED)

        except PermissionError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"[DLQ] Test entry creation failed: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

