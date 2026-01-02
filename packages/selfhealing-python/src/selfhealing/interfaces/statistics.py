"""
Statistics Repository Interface for Self-Healing System.

This module defines the interface for statistics/dashboard operations.
Unlike runtime repositories (Redis-based), this interface is designed for
complex aggregate queries that are best handled by SQL/ORM.

Design Principles:
1. Separated from runtime repositories
2. Read-heavy, complex aggregations
3. Graceful degradation via NullStatisticsRepository
4. Framework-agnostic (Django ORM, SQLAlchemy, etc.)

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# =============================================================================
# Data Transfer Objects
# =============================================================================


@dataclass
class StatusCounts:
    """Status-wise count of DLQ entries."""
    
    total: int = 0
    pending: int = 0
    resolved: int = 0
    failed: int = 0
    archived: int = 0
    reviewing: int = 0
    replayed: int = 0
    requires_review: int = 0
    rejected: int = 0
    expired: int = 0


@dataclass
class DomainDistribution:
    """Domain-wise distribution of DLQ entries."""
    
    domain: str
    count: int
    percentage: float


@dataclass
class FailureTypeDistribution:
    """Failure type distribution."""
    
    failure_type: str
    count: int
    percentage: float


@dataclass
class RecentActivity:
    """Recent activity statistics."""
    
    new_in_24h: int = 0
    resolved_in_24h: int = 0
    new_in_7d: int = 0
    resolved_in_7d: int = 0
    trend: str = "stable"  # up, down, stable


@dataclass
class CleanupStats:
    """Cleanup operation statistics."""
    
    total: int = 0
    by_status: Dict[str, int] = field(default_factory=dict)
    resolved_older_than_30_days: int = 0
    archived_older_than_90_days: int = 0


@dataclass
class PaginatedResult:
    """Paginated query result."""
    
    items: List[Any] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    has_next: bool = False
    has_prev: bool = False
    
    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


@dataclass
class CircuitBreakerSummary:
    """Summary of all circuit breakers."""
    
    total: int = 0
    closed: int = 0
    open: int = 0
    half_open: int = 0


@dataclass
class CircuitBreakerInfo:
    """Information about a single circuit breaker."""
    
    service_name: str
    state: str  # closed, open, half_open
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_state_change: Optional[datetime] = None
    

# =============================================================================
# Statistics Repository Interface
# =============================================================================


class StatisticsRepositoryInterface(ABC):
    """
    Statistics/Dashboard Repository Interface.
    
    This interface defines operations for dashboards and analytics.
    Unlike runtime repositories (optimized for speed), this interface
    is designed for complex aggregate queries.
    
    Implementations:
    - DjangoStatisticsAdapter: Uses Django ORM
    - SQLAlchemyStatisticsAdapter: Uses SQLAlchemy
    - NullStatisticsRepository: Returns empty results (default)
    
    Usage:
        from selfhealing.factory import ProviderRegistry
        
        stats_repo = ProviderRegistry.get_statistics_repo()
        counts = stats_repo.get_status_counts()
    """
    
    # =========================================================================
    # DLQ Statistics
    # =========================================================================
    
    @abstractmethod
    def get_status_counts(self) -> StatusCounts:
        """
        Get count of DLQ entries by status.
        
        Returns:
            StatusCounts with counts for each status
        """
        pass
    
    @abstractmethod
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        """
        Get distribution of DLQ entries by domain.
        
        Args:
            limit: Maximum number of domains to return (top N)
            
        Returns:
            List of DomainDistribution sorted by count descending
        """
        pass
    
    @abstractmethod
    def get_failure_type_distribution(self, limit: int = 10) -> List[FailureTypeDistribution]:
        """
        Get distribution of DLQ entries by failure type.
        
        Args:
            limit: Maximum number of failure types to return (top N)
            
        Returns:
            List of FailureTypeDistribution sorted by count descending
        """
        pass
    
    @abstractmethod
    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """
        Get recent activity statistics.
        
        Args:
            hours: Hours to look back for hourly stats
            days: Days to look back for daily stats
            
        Returns:
            RecentActivity with new/resolved counts and trend
        """
        pass
    
    @abstractmethod
    def get_resolution_rate(self, days: int = 30) -> float:
        """
        Calculate resolution success rate.
        
        Args:
            days: Number of days to look back
            
        Returns:
            Resolution rate as a float (0.0 to 1.0)
        """
        pass
    
    @abstractmethod
    def get_avg_retry_count(self) -> float:
        """
        Get average retry count across all DLQ entries.
        
        Returns:
            Average retry count as a float
        """
        pass
    
    # =========================================================================
    # DLQ List Operations (Paginated)
    # =========================================================================
    
    @abstractmethod
    def list_entries(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        order_by: str = "-created_at",
    ) -> PaginatedResult:
        """
        List DLQ entries with pagination and filtering.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of items per page
            status: Filter by status (optional)
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            order_by: Sort order (prefix with - for descending)
            
        Returns:
            PaginatedResult containing DLQ entries
        """
        pass
    
    @abstractmethod
    def get_entry_detail(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific DLQ entry.
        
        Args:
            entry_id: Unique identifier of the entry
            
        Returns:
            Dict with entry details or None if not found
        """
        pass
    
    # =========================================================================
    # Cleanup Operations
    # =========================================================================
    
    @abstractmethod
    def get_cleanup_stats(self) -> CleanupStats:
        """
        Get statistics for cleanup operations.
        
        Returns:
            CleanupStats with counts of entries eligible for cleanup
        """
        pass
    
    @abstractmethod
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """
        Archive old resolved entries.
        
        Args:
            older_than_days: Archive entries resolved more than N days ago
            
        Returns:
            Number of entries archived
        """
        pass
    
    @abstractmethod
    def purge_archived(
        self,
        ids: Optional[List[str]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """
        Permanently delete archived entries.
        
        Args:
            ids: Specific entry IDs to purge (optional)
            older_than_days: Purge archived entries older than N days (optional)
            
        Returns:
            Number of entries purged
        """
        pass
    
    # =========================================================================
    # Circuit Breaker Statistics
    # =========================================================================
    
    @abstractmethod
    def get_circuit_breaker_summary(self) -> CircuitBreakerSummary:
        """
        Get summary of all circuit breakers.
        
        Returns:
            CircuitBreakerSummary with counts by state
        """
        pass
    
    @abstractmethod
    def list_circuit_breakers(self) -> List[CircuitBreakerInfo]:
        """
        List all circuit breakers with their current state.
        
        Returns:
            List of CircuitBreakerInfo for all registered circuit breakers
        """
        pass
    
    # =========================================================================
    # Persistence (for hybrid storage)
    # =========================================================================
    
    @abstractmethod
    def persist_entry(self, entry_data: Dict[str, Any]) -> Optional[str]:
        """
        Persist a DLQ entry to the statistics store.
        
        Called by runtime layer to sync data to ORM for statistics.
        Can be called asynchronously.
        
        Args:
            entry_data: Entry data from runtime repository
            
        Returns:
            Entry ID if persisted, None otherwise
        """
        pass
    
    @abstractmethod
    def sync_from_runtime(self, entries: List[Dict[str, Any]]) -> int:
        """
        Bulk sync entries from runtime repository.
        
        Used for periodic synchronization.
        
        Args:
            entries: List of entry data from runtime repository
            
        Returns:
            Number of entries synced
        """
        pass
