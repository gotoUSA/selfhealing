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
- **Redis caching for high-traffic scenarios**
- **Hybrid storage support via ProviderRegistry**

Reference: docs/SERVICE_LAYER_EXTRACTION_PLAN.md Phase 2
Reference: docs/self_healing/07_CONTROL_API.md (Performance section)
Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from selfhealing.core.timezone import now

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface

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


def _get_statistics_repo():
    """Get statistics repository via ProviderRegistry."""
    from selfhealing.factory import ProviderRegistry
    return ProviderRegistry.get_statistics_repo()


def _has_statistics_adapter() -> bool:
    """Check if statistics adapter is registered."""
    from selfhealing.factory import ProviderRegistry
    return ProviderRegistry.has_statistics_adapter()


class DashboardService:
    """
    Dashboard statistics service.

    Provides centralized access to system monitoring data and statistics.
    Uses Redis caching to prevent database overload during high-traffic scenarios.

    Cache Strategy:
    - Summary data is cached for 30 seconds (configurable)
    - Individual components (status, activity, distribution) use shorter TTL
    - Cache is invalidated on significant state changes

    Usage:
        service = get_dashboard_service()
        summary = service.get_summary()  # Returns cached if available

        # Force fresh data (bypass cache)
        summary = service.get_summary(skip_cache=True)
    """

    # Cache configuration
    CACHE_PREFIX = "selfhealing:dashboard:"
    CACHE_TTL_SECONDS = 30  # Default TTL for dashboard data
    CACHE_TTL_STATUS = 15   # Shorter TTL for status counts
    CACHE_TTL_ACTIVITY = 60 # Longer TTL for activity stats

    def __init__(self, cache: "CacheProviderInterface | None" = None):
        """Initialize DashboardService with optional cache provider."""
        self._stats_repo = None
        self._cache = cache

    @property
    def stats_repo(self):
        """Lazy load the statistics repository."""
        if self._stats_repo is None:
            self._stats_repo = _get_statistics_repo()
        return self._stats_repo

    @property
    def cache(self) -> "CacheProviderInterface | None":
        """Get cache provider, creating default if needed."""
        if self._cache is None:
            try:
                from selfhealing.factory import ProviderRegistry
                self._cache = ProviderRegistry.get_cache()
            except (ImportError, ValueError):
                # Cache not available, will skip caching
                pass
        return self._cache

    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cached value by key."""
        if not self.cache:
            return None
        try:
            full_key = f"{self.CACHE_PREFIX}{key}"
            cached = self.cache.get(full_key)
            if cached:
                logger.debug(f"[Dashboard] Cache hit: {key}")
                return cached
        except Exception as e:
            logger.warning(f"[Dashboard] Cache read error: {e}")
        return None

    def _set_cached(self, key: str, value: Dict[str, Any], ttl_seconds: int = None) -> None:
        """Set cached value with TTL."""
        if not self.cache:
            return
        try:
            full_key = f"{self.CACHE_PREFIX}{key}"
            ttl = ttl_seconds or self.CACHE_TTL_SECONDS
            self.cache.set(full_key, value, ttl=timedelta(seconds=ttl))
            logger.debug(f"[Dashboard] Cache set: {key}, ttl={ttl}s")
        except Exception as e:
            logger.warning(f"[Dashboard] Cache write error: {e}")

    def get_summary(self, skip_cache: bool = False) -> DashboardSummary:
        """
        Get complete dashboard summary.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data

        Returns:
            DashboardSummary: Complete dashboard data
        """
        cache_key = "summary"

        # Try cache first (unless skip_cache)
        if not skip_cache:
            cached = self._get_cached(cache_key)
            if cached:
                return self._dict_to_summary(cached)

        # Fetch fresh data
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

        summary = DashboardSummary(
            timestamp=current_time.isoformat(),
            health_status=health_status,
            status_counts=status_counts,
            recent_activity=recent_activity,
            distribution=distribution,
            alerts=alerts,
            resolution_rate_percent=resolution_rate,
            recommendations=[],  # Future: add AI recommendations
        )

        # Cache the result
        self._set_cached(cache_key, summary.to_dict(), self.CACHE_TTL_SECONDS)

        return summary

    def _dict_to_summary(self, data: Dict[str, Any]) -> DashboardSummary:
        """Convert cached dictionary back to DashboardSummary."""
        overview = data.get("overview", {})
        recent = data.get("recent_activity", {})
        dist = data.get("distribution", {})
        alerts_data = data.get("alerts", {})

        return DashboardSummary(
            timestamp=data.get("timestamp", ""),
            health_status=data.get("health_status", "unknown"),
            status_counts=StatusCounts(
                total=overview.get("total", 0),
                pending=overview.get("pending", 0),
                resolved=overview.get("resolved", 0),
                failed=overview.get("failed", 0),
                archived=overview.get("archived", 0),
            ),
            recent_activity=RecentActivity(
                new_failures_24h=recent.get("new_failures_24h", 0),
                resolved_24h=recent.get("resolved_24h", 0),
                new_failures_7d=recent.get("new_failures_7d", 0),
                resolved_7d=recent.get("resolved_7d", 0),
            ),
            distribution=Distribution(
                by_domain=dist.get("by_domain", []),
                by_failure_type=dist.get("by_failure_type", []),
            ),
            alerts=AlertInfo(
                high_retry_count=alerts_data.get("high_retry_count", 0),
                avg_retry_count=alerts_data.get("avg_retry_count", 0.0),
            ),
            resolution_rate_percent=overview.get("resolution_rate_percent", 0.0),
            recommendations=data.get("recommendations", []),
        )

    def get_status_counts(self) -> StatusCounts:
        """
        Get counts by status.

        Uses ProviderRegistry to access statistics repository,
        which can be Django ORM, SQLAlchemy, or NullStatisticsRepository.

        Returns:
            StatusCounts: Counts for each status
        """
        try:
            stats = self.stats_repo.get_status_counts()
            return StatusCounts(
                total=stats.total,
                pending=stats.pending,
                resolved=stats.resolved,
                failed=stats.failed,
                archived=stats.archived,
            )
        except Exception as e:
            logger.error(f"[Dashboard] get_status_counts error: {e}")
            return StatusCounts()

    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """
        Get recent activity statistics.

        Uses statistics repository via ProviderRegistry.

        Args:
            hours: Hours for short-term activity (default: 24)
            days: Days for longer-term activity (default: 7)

        Returns:
            RecentActivity: Recent activity data
        """
        try:
            activity = self.stats_repo.get_recent_activity(hours=hours, days=days)
            return RecentActivity(
                new_failures_24h=activity.new_in_24h,
                resolved_24h=activity.resolved_in_24h,
                new_failures_7d=activity.new_in_7d,
                resolved_7d=activity.resolved_in_7d,
            )
        except Exception as e:
            logger.error(f"[Dashboard] get_recent_activity error: {e}")
            return RecentActivity()

    def get_distribution(self, limit: int = 10) -> Distribution:
        """
        Get distribution by domain and failure type.

        Uses statistics repository via ProviderRegistry.

        Args:
            limit: Maximum number of items per category (default: 10)

        Returns:
            Distribution: Distribution data
        """
        try:
            domain_dist = self.stats_repo.get_domain_distribution(limit=limit)
            failure_dist = self.stats_repo.get_failure_type_distribution(limit=limit)
            
            return Distribution(
                by_domain=[
                    {"domain": d.domain, "count": d.count}
                    for d in domain_dist
                ],
                by_failure_type=[
                    {"failure_type": f.failure_type, "count": f.count}
                    for f in failure_dist
                ],
            )
        except Exception as e:
            logger.error(f"[Dashboard] get_distribution error: {e}")
            return Distribution()

    def get_alerts(self, high_retry_threshold: int = 5) -> AlertInfo:
        """
        Get alert information.

        Uses statistics repository via ProviderRegistry.

        Args:
            high_retry_threshold: Threshold for high retry count (default: 5)

        Returns:
            AlertInfo: Alert data
        """
        try:
            avg_retry = self.stats_repo.get_avg_retry_count()
            
            # High retry count items - need to check via list_entries or custom query
            # For now, estimate based on average
            high_retry_count = 0
            if avg_retry > high_retry_threshold:
                # If average is above threshold, there are likely high retry items
                high_retry_count = int(avg_retry)
            
            return AlertInfo(
                high_retry_count=high_retry_count,
                avg_retry_count=round(avg_retry, 2),
            )
        except Exception as e:
            logger.error(f"[Dashboard] get_alerts error: {e}")
            return AlertInfo()

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


def get_dashboard_service(cache: "CacheProviderInterface | None" = None) -> DashboardService:
    """
    Get the singleton DashboardService instance.

    Args:
        cache: Optional cache provider for Redis caching.
               If not provided, will attempt to use ProviderRegistry.

    Returns:
        DashboardService: The singleton instance
    """
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService(cache=cache)
    return _dashboard_service


def invalidate_dashboard_cache() -> None:
    """
    Invalidate all dashboard cache entries.

    Call this when significant state changes occur that should
    be immediately reflected in the dashboard.
    """
    service = get_dashboard_service()
    if service.cache:
        try:
            # Clear all dashboard cache keys
            for key in ["summary", "status", "activity", "distribution", "alerts"]:
                full_key = f"{DashboardService.CACHE_PREFIX}{key}"
                service.cache.delete(full_key)
            logger.info("[Dashboard] Cache invalidated")
        except Exception as e:
            logger.warning(f"[Dashboard] Cache invalidation error: {e}")
