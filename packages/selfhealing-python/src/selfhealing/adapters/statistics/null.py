"""
Null Statistics Repository.

This is a Null Object implementation of StatisticsRepositoryInterface.
Used when no statistics adapter is registered.

Features:
- Returns empty/default values for all methods
- No errors thrown
- Runtime functionality continues to work
- Dashboards show "Statistics not available" message

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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
    AuditTrailEntry,
    EntityAuditTrail,
)

logger = logging.getLogger(__name__)


class NullStatisticsRepository(StatisticsRepositoryInterface):
    """
    Null Object implementation of StatisticsRepositoryInterface.
    
    Returns empty/default values for all methods.
    This allows the system to function without a statistics adapter
    while gracefully degrading dashboard functionality.
    
    Usage:
        # This is the default when no adapter is registered
        stats_repo = ProviderRegistry.get_statistics_repo()
        
        # All methods return empty results
        counts = stats_repo.get_status_counts()  # StatusCounts(all zeros)
    """
    
    _warned: bool = False
    
    def __init__(self):
        """Initialize NullStatisticsRepository."""
        if not NullStatisticsRepository._warned:
            logger.warning(
                "[NullStatisticsRepository] No statistics adapter registered. "
                "Dashboard statistics will not be available. "
                "Register an adapter in your app's ready() method. "
                "See docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md"
            )
            NullStatisticsRepository._warned = True
    
    # =========================================================================
    # DLQ Statistics
    # =========================================================================
    
    def get_status_counts(self) -> StatusCounts:
        """Return empty status counts."""
        return StatusCounts()
    
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        """Return empty domain distribution."""
        return []
    
    def get_failure_type_distribution(self, limit: int = 10) -> List[FailureTypeDistribution]:
        """Return empty failure type distribution."""
        return []
    
    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """Return empty recent activity."""
        return RecentActivity()
    
    def get_resolution_rate(self, days: int = 30) -> float:
        """Return zero resolution rate."""
        return 0.0
    
    def get_avg_retry_count(self) -> float:
        """Return zero average retry count."""
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
        """Return empty paginated result."""
        return PaginatedResult(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            has_next=False,
            has_prev=False,
        )
    
    def get_entry_detail(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Return None for entry detail."""
        return None
    
    # =========================================================================
    # SLA Monitoring
    # =========================================================================
    
    def get_sla_breaches(
        self,
        sla_threshold_hours: int = 4,
        statuses: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Return empty SLA breaches (no-op)."""
        logger.debug("[NullStatisticsRepository] get_sla_breaches called (no-op)")
        return {}
    
    # =========================================================================
    # Cleanup Operations
    # =========================================================================
    
    def get_cleanup_stats(self) -> CleanupStats:
        """Return empty cleanup stats."""
        return CleanupStats()
    
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """Return zero archived entries (no-op)."""
        logger.debug("[NullStatisticsRepository] archive_old_entries called (no-op)")
        return 0
    
    def purge_archived(
        self,
        ids: Optional[List[str]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """Return zero purged entries (no-op)."""
        logger.debug("[NullStatisticsRepository] purge_archived called (no-op)")
        return 0
    
    # =========================================================================
    # Circuit Breaker Statistics
    # =========================================================================
    
    def get_circuit_breaker_summary(self) -> CircuitBreakerSummary:
        """Return empty circuit breaker summary."""
        return CircuitBreakerSummary()
    
    def list_circuit_breakers(self) -> List[CircuitBreakerInfo]:
        """Return empty circuit breaker list."""
        return []
    
    # =========================================================================
    # Persistence (no-op for null adapter)
    # =========================================================================
    
    def persist_entry(self, entry_data: Dict[str, Any]) -> Optional[str]:
        """No-op persist (returns None)."""
        logger.debug("[NullStatisticsRepository] persist_entry called (no-op)")
        return None
    
    def sync_from_runtime(self, entries: List[Dict[str, Any]]) -> int:
        """No-op sync (returns 0)."""
        logger.debug("[NullStatisticsRepository] sync_from_runtime called (no-op)")
        return 0
    
    # =========================================================================
    # Audit Trail Integration (no-op)
    # =========================================================================
    
    def get_audit_trail_by_entity(
        self,
        entity_id: str,
        entity_type: str = "dlq_entry",
    ) -> EntityAuditTrail:
        """Return empty audit trail."""
        return EntityAuditTrail(
            entity_id=entity_id,
            entity_type=entity_type,
            domain="unknown",
            entries=[],
        )
    
    def link_audit_entry(
        self,
        entity_id: str,
        entity_type: str,
        action: str,
        actor_id: Optional[str] = None,
        status: Optional[str] = None,
        details: Optional[str] = None,
        audit_record_hash: Optional[str] = None,
    ) -> bool:
        """No-op link (returns False)."""
        logger.debug("[NullStatisticsRepository] link_audit_entry called (no-op)")
        return False
