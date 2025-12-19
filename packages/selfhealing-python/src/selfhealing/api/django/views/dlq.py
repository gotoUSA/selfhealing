"""
Self-Healing DLQ (Dead Letter Queue) Views.

REST API endpoints for DLQ management.

Endpoints:
- POST /api/self-healing/dlq/replay/ - Trigger DLQ replay
- GET  /api/self-healing/dlq/cleanup/stats/ - Get cleanup statistics
- POST /api/self-healing/dlq/cleanup/archive/ - Archive old resolved entries
- POST /api/self-healing/dlq/cleanup/purge/ - Permanently delete archived entries
"""

import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers import DLQReplayRequestSerializer

logger = logging.getLogger(__name__)


def _get_failed_operation_model():
    """Lazy import FailedOperation model."""
    from selfhealing.adapters.django.models import FailedOperation

    return FailedOperation


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
            FailedOperation = _get_failed_operation_model()
            # Get pending DLQ entries
            queryset = FailedOperation.objects.filter(status=FailedOperation.Status.PENDING)
            if domain:
                queryset = queryset.filter(domain=domain)

            entries = queryset[:batch_size]
            total = len(entries)
            success_count = 0
            failed_count = 0
            skipped_count = 0

            for entry in entries:
                # Here you would implement actual replay logic
                # For now, just log
                logger.info(f"[DLQ] Would replay entry {entry.id}: {entry.domain}/{entry.failure_type}")
                # In real implementation:
                # result = replay_operation(entry)
                # if result.success:
                #     entry.mark_as_resolved("auto_replay")
                #     success_count += 1
                # else:
                #     entry.increment_retry()
                #     failed_count += 1

            logger.info(
                f"[DLQ] Replay triggered via API: domain={domain}, "
                f"batch_size={batch_size}, success={success_count}, failed={failed_count}"
            )

            return Response(
                {
                    "status": "success",
                    "total": total,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "skipped_count": skipped_count,
                }
            )

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
            from django.db.models import Count

            FailedOperation = _get_failed_operation_model()
            now = timezone.now()
            day_30_ago = now - timedelta(days=30)
            day_90_ago = now - timedelta(days=90)

            # Count by status
            status_counts = dict(
                FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count")
            )

            # Count resolved older than 30 days
            resolved_older_than_30_days = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED,
                resolved_at__lt=day_30_ago,
            ).count()

            # Count archived older than 90 days
            archived_older_than_90_days = FailedOperation.objects.filter(
                status=FailedOperation.Status.ARCHIVED,
                updated_at__lt=day_90_ago,
            ).count()

            return Response(
                {
                    "total": FailedOperation.objects.count(),
                    "by_status": status_counts,
                    "resolved_older_than_30_days": resolved_older_than_30_days,
                    "archived_older_than_90_days": archived_older_than_90_days,
                    "recommendations": {
                        "can_archive": resolved_older_than_30_days,
                        "can_purge": archived_older_than_90_days,
                    },
                }
            )

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
            FailedOperation = _get_failed_operation_model()
            older_than_days = int(request.data.get("older_than_days", 30))

            if older_than_days < 1:
                return Response(
                    {"status": "error", "error": "older_than_days must be at least 1"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            cutoff = timezone.now() - timedelta(days=older_than_days)

            count = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED,
                resolved_at__lt=cutoff,
            ).update(
                status=FailedOperation.Status.ARCHIVED,
                updated_at=timezone.now(),
            )

            logger.info(
                f"[DLQ] Archived {count} entries via API " f"(resolved > {older_than_days} days ago) by user {request.user}"
            )

            return Response(
                {
                    "status": "success",
                    "archived_count": count,
                    "older_than_days": older_than_days,
                }
            )

        except ValueError:
            return Response(
                {"status": "error", "error": "older_than_days must be an integer"},
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

            FailedOperation = _get_failed_operation_model()

            if ids is not None and older_than_days is not None:
                return Response(
                    {"status": "error", "error": "Specify either ids or older_than_days, not both"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            archived_status = FailedOperation.Status.ARCHIVED

            if ids is not None:
                # Verify all are archived
                non_archived = (
                    FailedOperation.objects.filter(id__in=ids).exclude(status=archived_status).values_list("id", "status")
                )

                if non_archived:
                    first_bad = list(non_archived)[0]
                    return Response(
                        {
                            "status": "error",
                            "error": f"Entry {first_bad[0]} is not archived (status: {first_bad[1]}). "
                            "Only archived entries can be purged.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                result = FailedOperation.objects.filter(
                    id__in=ids,
                    status=archived_status,
                ).delete()
                count = result[0] if result else 0

            elif older_than_days is not None:
                older_than_days = int(older_than_days)
                if older_than_days < 1:
                    return Response(
                        {"status": "error", "error": "older_than_days must be at least 1"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                cutoff = timezone.now() - timedelta(days=older_than_days)
                result = FailedOperation.objects.filter(
                    status=archived_status,
                    updated_at__lt=cutoff,
                ).delete()
                count = result[0] if result else 0

            else:
                # Purge all archived
                result = FailedOperation.objects.filter(
                    status=archived_status,
                ).delete()
                count = result[0] if result else 0

            logger.warning(f"[DLQ] PURGED {count} archived entries via API by user {request.user}")

            return Response(
                {
                    "status": "success",
                    "purged_count": count,
                    "warning": "This action is irreversible",
                }
            )

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
            from django.core.paginator import Paginator

            FailedOperation = _get_failed_operation_model()

            # Filters
            status_filter = request.query_params.get("status")
            domain_filter = request.query_params.get("domain")

            # Pagination
            page = int(request.query_params.get("page", 1))
            page_size = min(int(request.query_params.get("page_size", 20)), 100)

            queryset = FailedOperation.objects.all().order_by("-created_at")

            if status_filter:
                queryset = queryset.filter(status=status_filter)
            if domain_filter:
                queryset = queryset.filter(domain=domain_filter)

            paginator = Paginator(queryset, page_size)
            page_obj = paginator.get_page(page)

            entries = []
            for entry in page_obj:
                entries.append(
                    {
                        "id": entry.id,
                        "domain": entry.domain,
                        "failure_type": entry.failure_type,
                        "status": entry.status,
                        "retry_count": entry.retry_count,
                        "created_at": entry.created_at.isoformat() if entry.created_at else None,
                        "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
                    }
                )

            return Response(
                {
                    "results": entries,
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total_pages": paginator.num_pages,
                        "total_count": paginator.count,
                        "has_next": page_obj.has_next(),
                        "has_previous": page_obj.has_previous(),
                    },
                }
            )

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
            FailedOperation = _get_failed_operation_model()
            entry = FailedOperation.objects.get(pk=pk)

            return Response(
                {
                    "id": entry.id,
                    "domain": entry.domain,
                    "failure_type": entry.failure_type,
                    "status": entry.status,
                    "retry_count": entry.retry_count,
                    "max_retries": entry.max_retries,
                    "context": entry.context,
                    "error_message": entry.error_message,
                    "stack_trace": entry.stack_trace,
                    "resolution_notes": entry.resolution_notes,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
                    "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
                }
            )

        except FailedOperation.DoesNotExist:
            return Response(
                {"status": "error", "error": f"DLQ entry {pk} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
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
            FailedOperation = _get_failed_operation_model()
            entry = FailedOperation.objects.get(pk=pk)

            if entry.status == FailedOperation.Status.RESOLVED:
                return Response(
                    {"status": "error", "error": "Cannot retry an already resolved entry"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if entry.status == FailedOperation.Status.ARCHIVED:
                return Response(
                    {"status": "error", "error": "Cannot retry an archived entry"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Here you would implement actual retry logic
            # For now, just increment retry count and log
            old_count = entry.retry_count
            entry.retry_count += 1
            entry.updated_at = timezone.now()
            entry.save()

            logger.info(
                f"[DLQ] Retry triggered for entry {pk} " f"({entry.domain}/{entry.failure_type}) by user {request.user}"
            )

            # In real implementation:
            # result = execute_retry_logic(entry)
            # return success/failure based on result

            return Response(
                {
                    "status": "success",
                    "id": entry.id,
                    "retry_count": entry.retry_count,
                    "previous_retry_count": old_count,
                    "message": f"Retry triggered for entry {pk}",
                }
            )

        except FailedOperation.DoesNotExist:
            return Response(
                {"status": "error", "error": f"DLQ entry {pk} not found"},
                status=status.HTTP_404_NOT_FOUND,
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
            FailedOperation = _get_failed_operation_model()
            entry = FailedOperation.objects.get(pk=pk)

            if entry.status == FailedOperation.Status.RESOLVED:
                return Response(
                    {"status": "error", "error": "Entry is already resolved"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if entry.status == FailedOperation.Status.ARCHIVED:
                return Response(
                    {"status": "error", "error": "Cannot resolve an archived entry"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            notes = request.data.get("notes", f"Manually resolved by {request.user}")

            old_status = entry.status
            entry.status = FailedOperation.Status.RESOLVED
            entry.resolved_at = timezone.now()
            entry.updated_at = timezone.now()
            entry.resolution_notes = notes
            entry.save()

            logger.info(f"[DLQ] Entry {pk} manually resolved by user {request.user}: {notes}")

            return Response(
                {
                    "status": "success",
                    "id": entry.id,
                    "previous_status": old_status,
                    "current_status": entry.status,
                    "resolved_at": entry.resolved_at.isoformat(),
                    "notes": notes,
                }
            )

        except FailedOperation.DoesNotExist:
            return Response(
                {"status": "error", "error": f"DLQ entry {pk} not found"},
                status=status.HTTP_404_NOT_FOUND,
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
        """Create a test DLQ entry."""
        from django.conf import settings

        # Only allow in non-production environments
        if not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False):
            return Response(
                {"error": "DLQ test entries can only be created in DEBUG/TEST mode"},
                status=status.HTTP_403_FORBIDDEN,
            )

        domain = request.data.get("domain")
        failure_type = request.data.get("failure_type")

        if not domain or not failure_type:
            return Response(
                {"error": "domain and failure_type are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            FailedOperation = _get_failed_operation_model()

            # Create test DLQ entry using correct field names
            entry = FailedOperation.objects.create(
                domain=domain,
                failure_type=failure_type,
                order_id=request.data.get("order_id"),
                payment_id=request.data.get("payment_id"),
                user_id=request.user.id if request.user else None,
                error_code="TEST_ERROR",
                error_message=request.data.get("error_message", "Test failure for load testing"),
                snapshot_data=request.data.get("snapshot_data", {}),
                request_data=request.data.get("request_data", {}),
                response_data=request.data.get("response_data", {}),
                metadata={
                    "test": True,
                    "created_by": str(request.user),
                    "source": "DLQTestCreateView",
                    "entity_type": request.data.get("entity_type", "test"),
                    "entity_id": request.data.get("entity_id", ""),
                    **(request.data.get("metadata", {})),
                },
                recommended_action=FailedOperation.RecommendedAction.REPLAY,
                status=FailedOperation.Status.PENDING,
            )

            logger.info(
                f"[DLQ] Test entry created: id={entry.id}, "
                f"domain={domain}, failure_type={failure_type}, "
                f"user={request.user}"
            )

            return Response(
                {
                    "status": "created",
                    "dlq_id": entry.id,
                    "domain": domain,
                    "failure_type": failure_type,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.error(f"[DLQ] Test entry creation failed: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

