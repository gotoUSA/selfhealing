"""
Dashboard Service

Provides centralized dashboard statistics and monitoring operations.
Extracts business logic from views/dashboard.py for better separation of concerns.

Features:
- System status overview (pending, resolved, failed, archived counts)
- Recent activity tracking (24h, 7d periods)
- Distribution analysis by domain and failure type
- Health status determination
- Resolution rate and retry count statistics

Reference: docs/SERVICE_LAYER_EXTRACTION_PLAN.md Phase 2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from selfhealing.core.timezone import now

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class StatusCounts:
    """Status counts for DLQ entries."""

    total: int = 0
    pending: int = 0
    resolved: int = 0
    failed: int = 0
    archived: int = 0


@dataclass
class RecentActivity:
    """Recent activity statistics."""

    new_failures_24h: int = 0
    resolved_24h: int = 0
    new_failures_7d: int = 0
    resolved_7d: int = 0


@dataclass
class Distribution:
    """Distribution data by domain and failure type."""

    by_domain: List[Dict[str, Any]] = field(default_factory=list)
    by_failure_type: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AlertInfo:
    """Alert information."""

    high_retry_count: int = 0
    avg_retry_count: float = 0.0


@dataclass
class DashboardSummary:
    """Complete dashboard summary data."""

    timestamp: str
    health_status: str
    status_counts: StatusCounts
    recent_activity: RecentActivity
    distribution: Distribution
    alerts: AlertInfo
    resolution_rate_percent: float = 0.0
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "timestamp": self.timestamp,
            "health_status": self.health_status,
            "overview": {
                "total": self.status_counts.total,
                "pending": self.status_counts.pending,
                "resolved": self.status_counts.resolved,
                "failed": self.status_counts.failed,
                "archived": self.status_counts.archived,
                "resolution_rate_percent": self.resolution_rate_percent,
            },
            "recent_activity": {
                "new_failures_24h": self.recent_activity.new_failures_24h,
                "resolved_24h": self.recent_activity.resolved_24h,
                "new_failures_7d": self.recent_activity.new_failures_7d,
                "resolved_7d": self.recent_activity.resolved_7d,
            },
            "distribution": {
                "by_domain": self.distribution.by_domain,
                "by_failure_type": self.distribution.by_failure_type,
            },
            "alerts": {
                "high_retry_count": self.alerts.high_retry_count,
                "avg_retry_count": self.alerts.avg_retry_count,
            },
            "recommendations": self.recommendations,
        }


# =============================================================================
# Dashboard Service
# =============================================================================


def _get_failed_operation_model():
    """Lazy import FailedOperation model."""
    from selfhealing.adapters.django.models import FailedOperation

    return FailedOperation


class DashboardService:
    """
    Dashboard statistics service.

    Provides centralized access to system monitoring data and statistics.
    """

    def __init__(self):
        """Initialize DashboardService."""
        self._model = None

    @property
    def model(self):
        """Lazy load the FailedOperation model."""
        if self._model is None:
            self._model = _get_failed_operation_model()
        return self._model

    def get_summary(self) -> DashboardSummary:
        """
        Get complete dashboard summary.

        Returns:
            DashboardSummary: Complete dashboard data
        """
        from django.db.models import Avg, Count

        current_time = now()

        # Get all component data
        status_counts = self.get_status_counts()
        recent_activity = self.get_recent_activity()
        distribution = self.get_distribution()
        alerts = self.get_alerts()

        # Calculate resolution rate
        resolution_rate = self.calculate_resolution_rate(
            resolved=status_counts.resolved,
            total=status_counts.total,
            archived=status_counts.archived,
        )

        # Determine health status
        health_status = self.determine_health_status(
            pending=status_counts.pending,
            failed=status_counts.failed,
        )

        return DashboardSummary(
            timestamp=current_time.isoformat(),
            health_status=health_status,
            status_counts=status_counts,
            recent_activity=recent_activity,
            distribution=distribution,
            alerts=alerts,
            resolution_rate_percent=resolution_rate,
            recommendations=[],  # Future: add AI recommendations
        )

    def get_status_counts(self) -> StatusCounts:
        """
        Get counts by status.

        Returns:
            StatusCounts: Counts for each status
        """
        from django.db.models import Count

        FailedOperation = self.model

        status_dict = dict(
            FailedOperation.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        total = sum(status_dict.values())

        return StatusCounts(
            total=total,
            pending=status_dict.get(FailedOperation.Status.PENDING, 0),
            resolved=status_dict.get(FailedOperation.Status.RESOLVED, 0),
            failed=status_dict.get(FailedOperation.Status.FAILED, 0),
            archived=status_dict.get(FailedOperation.Status.ARCHIVED, 0),
        )

    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """
        Get recent activity statistics.

        Args:
            hours: Hours for short-term activity (default: 24)
            days: Days for longer-term activity (default: 7)

        Returns:
            RecentActivity: Recent activity data
        """
        FailedOperation = self.model
        current_time = now()
        last_hours = current_time - timedelta(hours=hours)
        last_days = current_time - timedelta(days=days)

        # Short-term activity
        new_failures_short = FailedOperation.objects.filter(
            created_at__gte=last_hours
        ).count()

        resolved_short = FailedOperation.objects.filter(
            status=FailedOperation.Status.RESOLVED,
            resolved_at__gte=last_hours,
        ).count()

        # Longer-term activity
        new_failures_long = FailedOperation.objects.filter(
            created_at__gte=last_days
        ).count()

        resolved_long = FailedOperation.objects.filter(
            status=FailedOperation.Status.RESOLVED,
            resolved_at__gte=last_days,
        ).count()

        return RecentActivity(
            new_failures_24h=new_failures_short,
            resolved_24h=resolved_short,
            new_failures_7d=new_failures_long,
            resolved_7d=resolved_long,
        )

    def get_distribution(self, limit: int = 10) -> Distribution:
        """
        Get distribution by domain and failure type.

        Args:
            limit: Maximum number of items per category (default: 10)

        Returns:
            Distribution: Distribution data
        """
        from django.db.models import Count

        FailedOperation = self.model

        # Active statuses filter
        active_statuses = [
            FailedOperation.Status.PENDING,
            FailedOperation.Status.FAILED,
        ]

        # By domain
        by_domain = list(
            FailedOperation.objects.filter(status__in=active_statuses)
            .values("domain")
            .annotate(count=Count("id"))
            .order_by("-count")[:limit]
        )

        # By failure type
        by_failure_type = list(
            FailedOperation.objects.filter(status__in=active_statuses)
            .values("failure_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:limit]
        )

        return Distribution(
            by_domain=by_domain,
            by_failure_type=by_failure_type,
        )

    def get_alerts(self, high_retry_threshold: int = 5) -> AlertInfo:
        """
        Get alert information.

        Args:
            high_retry_threshold: Threshold for high retry count (default: 5)

        Returns:
            AlertInfo: Alert data
        """
        from django.db.models import Avg

        FailedOperation = self.model

        # High retry count items
        high_retry_count = FailedOperation.objects.filter(
            status=FailedOperation.Status.PENDING,
            retry_count__gte=high_retry_threshold,
        ).count()

        # Average retry count
        avg_retries = (
            FailedOperation.objects.filter(
                status__in=[
                    FailedOperation.Status.PENDING,
                    FailedOperation.Status.FAILED,
                ]
            ).aggregate(avg=Avg("retry_count"))["avg"]
            or 0
        )

        return AlertInfo(
            high_retry_count=high_retry_count,
            avg_retry_count=round(avg_retries, 2),
        )

    def calculate_resolution_rate(
        self,
        resolved: int,
        total: int,
        archived: int,
    ) -> float:
        """
        Calculate resolution rate.

        Args:
            resolved: Number of resolved entries
            total: Total number of entries
            archived: Number of archived entries

        Returns:
            float: Resolution rate as percentage (0.0 - 100.0)
        """
        active_total = total - archived
        if active_total <= 0:
            return 0.0
        return round((resolved / active_total) * 100, 2)

    def determine_health_status(self, pending: int, failed: int) -> str:
        """
        Determine system health status based on pending and failed counts.

        Args:
            pending: Number of pending entries
            failed: Number of failed entries

        Returns:
            str: Health status ('healthy', 'good', 'warning', 'critical')
        """
        if pending == 0 and failed == 0:
            return "healthy"
        elif pending <= 10 and failed == 0:
            return "good"
        elif pending <= 50 or failed <= 5:
            return "warning"
        else:
            return "critical"


# =============================================================================
# Singleton Instance
# =============================================================================

_dashboard_service: Optional[DashboardService] = None


def get_dashboard_service() -> DashboardService:
    """
    Get the singleton DashboardService instance.

    Returns:
        DashboardService: The singleton instance
    """
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service
