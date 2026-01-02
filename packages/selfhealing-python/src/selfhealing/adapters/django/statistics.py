"""
Django Statistics Adapter for Self-Healing System.

This adapter implements StatisticsRepositoryInterface using Django ORM.
It is designed to be domain-free - the actual models are provided by
the application, not by selfhealing package.

Usage:
    # In your Django app's apps.py
    from selfhealing.factory import ProviderRegistry
    from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
    
    class ShoppingConfig(AppConfig):
        def ready(self):
            from shopping.models import FailedOperation
            
            ProviderRegistry.register_statistics_adapter(
                DjangoStatisticsAdapter(
                    failed_operation_model=FailedOperation,
                )
            )

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Type, TYPE_CHECKING

from selfhealing.interfaces.statistics import (
    StatisticsRepositoryInterface,
    StatusCounts,
    DomainDistribution,
    FailureTypeDistribution,
    RecentActivity,
    CleanupStats,
    PaginatedResult,
    CircuitBreakerSummary,
    CircuitBreakerInfo,
)

if TYPE_CHECKING:
    from django.db.models import Model

logger = logging.getLogger(__name__)


class DjangoStatisticsAdapter(StatisticsRepositoryInterface):
    """
    Django ORM implementation of StatisticsRepositoryInterface.
    
    This adapter is domain-free - models are provided by the application.
    It uses Django's powerful ORM for complex aggregate queries.
    
    Attributes:
        failed_operation_model: Django model for DLQ entries
        circuit_breaker_model: Django model for CB state (optional)
    """
    
    def __init__(
        self,
        failed_operation_model: Optional[Type["Model"]] = None,
        circuit_breaker_model: Optional[Type["Model"]] = None,
    ):
        """
        Initialize Django Statistics Adapter.
        
        Args:
            failed_operation_model: Django model class for FailedOperation
            circuit_breaker_model: Django model class for CircuitBreakerState (optional)
        """
        self._failed_operation_model = failed_operation_model
        self._circuit_breaker_model = circuit_breaker_model
        
        if failed_operation_model:
            logger.info(
                f"[DjangoStatisticsAdapter] Initialized with model: "
                f"{failed_operation_model.__name__}"
            )
        else:
            logger.warning(
                "[DjangoStatisticsAdapter] No failed_operation_model provided. "
                "Some statistics will not be available."
            )
    
    def _get_model(self) -> Optional[Type["Model"]]:
        """Get the FailedOperation model."""
        return self._failed_operation_model
    
    def _check_model(self) -> bool:
        """Check if model is available."""
        if self._failed_operation_model is None:
            logger.warning("[DjangoStatisticsAdapter] Model not configured")
            return False
        return True
    
    # =========================================================================
    # DLQ Statistics
    # =========================================================================
    
    def get_status_counts(self) -> StatusCounts:
        """Get count of DLQ entries by status using Django aggregation."""
        if not self._check_model():
            return StatusCounts()
        
        try:
            from django.db.models import Count
            
            model = self._get_model()
            queryset = model.objects.values("status").annotate(count=Count("id"))
            
            counts = StatusCounts()
            for row in queryset:
                status = row["status"]
                count = row["count"]
                counts.total += count
                
                if hasattr(counts, status):
                    setattr(counts, status, count)
            
            return counts
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_status_counts error: {e}")
            return StatusCounts()
    
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        """Get distribution of DLQ entries by domain."""
        if not self._check_model():
            return []
        
        try:
            from django.db.models import Count
            
            model = self._get_model()
            total = model.objects.count()
            
            if total == 0:
                return []
            
            queryset = (
                model.objects
                .values("domain")
                .annotate(count=Count("id"))
                .order_by("-count")[:limit]
            )
            
            return [
                DomainDistribution(
                    domain=row["domain"] or "unknown",
                    count=row["count"],
                    percentage=round(row["count"] / total * 100, 2),
                )
                for row in queryset
            ]
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_domain_distribution error: {e}")
            return []
    
    def get_failure_type_distribution(self, limit: int = 10) -> List[FailureTypeDistribution]:
        """Get distribution of DLQ entries by failure type."""
        if not self._check_model():
            return []
        
        try:
            from django.db.models import Count
            
            model = self._get_model()
            total = model.objects.count()
            
            if total == 0:
                return []
            
            queryset = (
                model.objects
                .values("failure_type")
                .annotate(count=Count("id"))
                .order_by("-count")[:limit]
            )
            
            return [
                FailureTypeDistribution(
                    failure_type=row["failure_type"] or "unknown",
                    count=row["count"],
                    percentage=round(row["count"] / total * 100, 2),
                )
                for row in queryset
            ]
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_failure_type_distribution error: {e}")
            return []
    
    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """Get recent activity statistics."""
        if not self._check_model():
            return RecentActivity()
        
        try:
            from django.utils import timezone
            
            model = self._get_model()
            now = timezone.now()
            hours_ago = now - timedelta(hours=hours)
            days_ago = now - timedelta(days=days)
            
            # New entries
            new_in_24h = model.objects.filter(created_at__gte=hours_ago).count()
            new_in_7d = model.objects.filter(created_at__gte=days_ago).count()
            
            # Resolved entries
            resolved_in_24h = model.objects.filter(
                resolved_at__gte=hours_ago
            ).count()
            resolved_in_7d = model.objects.filter(
                resolved_at__gte=days_ago
            ).count()
            
            # Calculate trend
            prev_week = model.objects.filter(
                created_at__gte=days_ago - timedelta(days=7),
                created_at__lt=days_ago,
            ).count()
            
            if new_in_7d > prev_week * 1.1:
                trend = "up"
            elif new_in_7d < prev_week * 0.9:
                trend = "down"
            else:
                trend = "stable"
            
            return RecentActivity(
                new_in_24h=new_in_24h,
                resolved_in_24h=resolved_in_24h,
                new_in_7d=new_in_7d,
                resolved_in_7d=resolved_in_7d,
                trend=trend,
            )
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_recent_activity error: {e}")
            return RecentActivity()
    
    def get_resolution_rate(self, days: int = 30) -> float:
        """Calculate resolution success rate."""
        if not self._check_model():
            return 0.0
        
        try:
            from django.utils import timezone
            
            model = self._get_model()
            since = timezone.now() - timedelta(days=days)
            
            total = model.objects.filter(created_at__gte=since).count()
            if total == 0:
                return 0.0
            
            resolved = model.objects.filter(
                created_at__gte=since,
                status="resolved",
            ).count()
            
            return round(resolved / total, 4)
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_resolution_rate error: {e}")
            return 0.0
    
    def get_avg_retry_count(self) -> float:
        """Get average retry count across all DLQ entries."""
        if not self._check_model():
            return 0.0
        
        try:
            from django.db.models import Avg
            
            model = self._get_model()
            result = model.objects.aggregate(avg_retry=Avg("retry_count"))
            return round(result["avg_retry"] or 0.0, 2)
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_avg_retry_count error: {e}")
            return 0.0
    
    # =========================================================================
    # DLQ List Operations (Paginated)
    # =========================================================================
    
    def list_entries(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        order_by: str = "-created_at",
    ) -> PaginatedResult:
        """List DLQ entries with pagination and filtering."""
        if not self._check_model():
            return PaginatedResult(page=page, page_size=page_size)
        
        try:
            model = self._get_model()
            queryset = model.objects.all()
            
            # Apply filters
            if status:
                queryset = queryset.filter(status=status)
            if domain:
                queryset = queryset.filter(domain=domain)
            if failure_type:
                queryset = queryset.filter(failure_type=failure_type)
            
            # Order
            queryset = queryset.order_by(order_by)
            
            # Count
            total = queryset.count()
            
            # Paginate
            offset = (page - 1) * page_size
            items = list(queryset[offset:offset + page_size].values())
            
            return PaginatedResult(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
                has_next=offset + page_size < total,
                has_prev=page > 1,
            )
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] list_entries error: {e}")
            return PaginatedResult(page=page, page_size=page_size)
    
    def get_entry_detail(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific DLQ entry."""
        if not self._check_model():
            return None
        
        try:
            model = self._get_model()
            entry = model.objects.filter(pk=entry_id).values().first()
            return dict(entry) if entry else None
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_entry_detail error: {e}")
            return None
    
    # =========================================================================
    # Cleanup Operations
    # =========================================================================
    
    def get_cleanup_stats(self) -> CleanupStats:
        """Get statistics for cleanup operations."""
        if not self._check_model():
            return CleanupStats()
        
        try:
            from django.db.models import Count
            from django.utils import timezone
            
            model = self._get_model()
            
            # Count by status
            status_counts = dict(
                model.objects
                .values("status")
                .annotate(count=Count("id"))
                .values_list("status", "count")
            )
            
            # Resolved older than 30 days
            thirty_days_ago = timezone.now() - timedelta(days=30)
            resolved_old = model.objects.filter(
                status="resolved",
                resolved_at__lt=thirty_days_ago,
            ).count()
            
            # Archived older than 90 days
            ninety_days_ago = timezone.now() - timedelta(days=90)
            archived_old = model.objects.filter(
                status="archived",
                updated_at__lt=ninety_days_ago,
            ).count()
            
            return CleanupStats(
                total=model.objects.count(),
                by_status=status_counts,
                resolved_older_than_30_days=resolved_old,
                archived_older_than_90_days=archived_old,
            )
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_cleanup_stats error: {e}")
            return CleanupStats()
    
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """Archive old resolved entries."""
        if not self._check_model():
            return 0
        
        try:
            from django.utils import timezone
            
            model = self._get_model()
            cutoff = timezone.now() - timedelta(days=older_than_days)
            
            count = model.objects.filter(
                status="resolved",
                resolved_at__lt=cutoff,
            ).update(status="archived")
            
            logger.info(f"[DjangoStatisticsAdapter] Archived {count} entries")
            return count
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] archive_old_entries error: {e}")
            return 0
    
    def purge_archived(
        self,
        ids: Optional[List[str]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """Permanently delete archived entries."""
        if not self._check_model():
            return 0
        
        try:
            from django.utils import timezone
            
            model = self._get_model()
            queryset = model.objects.filter(status="archived")
            
            if ids:
                queryset = queryset.filter(pk__in=ids)
            
            if older_than_days:
                cutoff = timezone.now() - timedelta(days=older_than_days)
                queryset = queryset.filter(updated_at__lt=cutoff)
            
            count, _ = queryset.delete()
            
            logger.info(f"[DjangoStatisticsAdapter] Purged {count} entries")
            return count
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] purge_archived error: {e}")
            return 0
    
    # =========================================================================
    # Circuit Breaker Statistics
    # =========================================================================
    
    def get_circuit_breaker_summary(self) -> CircuitBreakerSummary:
        """Get summary of all circuit breakers."""
        if not self._circuit_breaker_model:
            # Fallback to Redis-based stats if available
            return self._get_cb_summary_from_redis()
        
        try:
            from django.db.models import Count
            
            model = self._circuit_breaker_model
            queryset = model.objects.values("state").annotate(count=Count("id"))
            
            summary = CircuitBreakerSummary()
            for row in queryset:
                state = row["state"]
                count = row["count"]
                summary.total += count
                
                if state == "closed":
                    summary.closed = count
                elif state == "open":
                    summary.open = count
                elif state == "half_open":
                    summary.half_open = count
            
            return summary
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] get_circuit_breaker_summary error: {e}")
            return CircuitBreakerSummary()
    
    def _get_cb_summary_from_redis(self) -> CircuitBreakerSummary:
        """Get CB summary from Redis runtime repository."""
        try:
            from selfhealing.factory import ProviderRegistry
            
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            states = cb_repo.get_all_states()
            
            summary = CircuitBreakerSummary(total=len(states))
            for state in states.values():
                if state.state == "closed":
                    summary.closed += 1
                elif state.state == "open":
                    summary.open += 1
                elif state.state == "half_open":
                    summary.half_open += 1
            
            return summary
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] _get_cb_summary_from_redis error: {e}")
            return CircuitBreakerSummary()
    
    def list_circuit_breakers(self) -> List[CircuitBreakerInfo]:
        """List all circuit breakers with their current state."""
        if not self._circuit_breaker_model:
            # Fallback to Redis-based list if available
            return self._list_cbs_from_redis()
        
        try:
            model = self._circuit_breaker_model
            entries = model.objects.all().values()
            
            return [
                CircuitBreakerInfo(
                    service_name=entry.get("service_name", "unknown"),
                    state=entry.get("state", "closed"),
                    failure_count=entry.get("failure_count", 0),
                    success_count=entry.get("success_count", 0),
                    last_failure_time=entry.get("last_failure_time"),
                    last_state_change=entry.get("last_state_change"),
                )
                for entry in entries
            ]
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] list_circuit_breakers error: {e}")
            return []
    
    def _list_cbs_from_redis(self) -> List[CircuitBreakerInfo]:
        """List CBs from Redis runtime repository."""
        try:
            from selfhealing.factory import ProviderRegistry
            
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            states = cb_repo.get_all_states()
            
            return [
                CircuitBreakerInfo(
                    service_name=name,
                    state=state.state.value if hasattr(state.state, 'value') else str(state.state),
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    last_failure_time=state.last_failure_time,
                    last_state_change=state.last_state_change,
                )
                for name, state in states.items()
            ]
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] _list_cbs_from_redis error: {e}")
            return []
    
    # =========================================================================
    # Persistence (for hybrid storage)
    # =========================================================================
    
    def persist_entry(self, entry_data: Dict[str, Any]) -> Optional[str]:
        """Persist a DLQ entry to the statistics store."""
        if not self._check_model():
            return None
        
        try:
            model = self._get_model()
            
            # Create or update
            entry_id = entry_data.get("id")
            if entry_id:
                obj, created = model.objects.update_or_create(
                    pk=entry_id,
                    defaults=entry_data,
                )
            else:
                obj = model.objects.create(**entry_data)
            
            return str(obj.pk)
        except Exception as e:
            logger.error(f"[DjangoStatisticsAdapter] persist_entry error: {e}")
            return None
    
    def sync_from_runtime(self, entries: List[Dict[str, Any]]) -> int:
        """Bulk sync entries from runtime repository."""
        if not self._check_model():
            return 0
        
        synced = 0
        for entry_data in entries:
            if self.persist_entry(entry_data):
                synced += 1
        
        logger.info(f"[DjangoStatisticsAdapter] Synced {synced}/{len(entries)} entries")
        return synced
