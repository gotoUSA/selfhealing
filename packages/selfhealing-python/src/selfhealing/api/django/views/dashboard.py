"""
Self-Healing Dashboard Views.

REST API endpoints for monitoring dashboard.

Endpoints:
- GET /api/self-healing/dashboard/summary/ - Get system summary statistics
"""

import logging
from datetime import timedelta

from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shopping.models.failed_operation import FailedOperation

logger = logging.getLogger(__name__)


class DashboardSummaryView(APIView):
    """
    Dashboard Summary API.

    GET /api/self-healing/dashboard/summary/

    Returns a comprehensive summary of the self-healing system status.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """Get dashboard summary statistics."""
        try:
            now = timezone.now()
            last_24h = now - timedelta(hours=24)
            last_7d = now - timedelta(days=7)
            last_30d = now - timedelta(days=30)

            # Overall counts by status
            status_counts = dict(
                FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count")
            )

            total = sum(status_counts.values())
            pending = status_counts.get(FailedOperation.Status.PENDING, 0)
            resolved = status_counts.get(FailedOperation.Status.RESOLVED, 0)
            failed = status_counts.get(FailedOperation.Status.FAILED, 0)
            archived = status_counts.get(FailedOperation.Status.ARCHIVED, 0)

            # Recent activity (last 24h)
            new_failures_24h = FailedOperation.objects.filter(created_at__gte=last_24h).count()

            resolved_24h = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED, resolved_at__gte=last_24h
            ).count()

            # Weekly trends
            new_failures_7d = FailedOperation.objects.filter(created_at__gte=last_7d).count()

            resolved_7d = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED, resolved_at__gte=last_7d
            ).count()

            # By domain distribution
            domain_distribution = list(
                FailedOperation.objects.filter(
                    status__in=[
                        FailedOperation.Status.PENDING,
                        FailedOperation.Status.FAILED,
                    ]
                )
                .values("domain")
                .annotate(count=Count("id"))
                .order_by("-count")[:10]
            )

            # By failure type distribution
            failure_type_distribution = list(
                FailedOperation.objects.filter(
                    status__in=[
                        FailedOperation.Status.PENDING,
                        FailedOperation.Status.FAILED,
                    ]
                )
                .values("failure_type")
                .annotate(count=Count("id"))
                .order_by("-count")[:10]
            )

            # Average retry count for pending/failed
            avg_retries = (
                FailedOperation.objects.filter(
                    status__in=[
                        FailedOperation.Status.PENDING,
                        FailedOperation.Status.FAILED,
                    ]
                ).aggregate(avg=Avg("retry_count"))["avg"]
                or 0
            )

            # High retry count items (potentially stuck)
            high_retry_count = FailedOperation.objects.filter(
                status=FailedOperation.Status.PENDING, retry_count__gte=5
            ).count()

            # Resolution rate
            resolution_rate = 0
            if total > 0:
                resolution_rate = round((resolved / (total - archived)) * 100, 2) if (total - archived) > 0 else 0

            # Health status
            if pending == 0 and failed == 0:
                health_status = "healthy"
            elif pending <= 10 and failed == 0:
                health_status = "good"
            elif pending <= 50 or failed <= 5:
                health_status = "warning"
            else:
                health_status = "critical"

            return Response(
                {
                    "timestamp": now.isoformat(),
                    "health_status": health_status,
                    "overview": {
                        "total": total,
                        "pending": pending,
                        "resolved": resolved,
                        "failed": failed,
                        "archived": archived,
                        "resolution_rate_percent": resolution_rate,
                    },
                    "recent_activity": {
                        "new_failures_24h": new_failures_24h,
                        "resolved_24h": resolved_24h,
                        "new_failures_7d": new_failures_7d,
                        "resolved_7d": resolved_7d,
                    },
                    "distribution": {
                        "by_domain": domain_distribution,
                        "by_failure_type": failure_type_distribution,
                    },
                    "alerts": {
                        "high_retry_count": high_retry_count,
                        "avg_retry_count": round(avg_retries, 2),
                    },
                    "recommendations": [],  # Future: add AI recommendations
                }
            )

        except Exception as e:
            logger.error(f"[Dashboard] Summary failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
