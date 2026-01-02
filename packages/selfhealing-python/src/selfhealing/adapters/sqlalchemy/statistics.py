"""
SQLAlchemy Statistics Adapter for Self-Healing System.

This adapter implements StatisticsRepositoryInterface using SQLAlchemy.
Designed for FastAPI, Flask, and other non-Django projects.

Usage:
    # In your FastAPI app
    from selfhealing.factory import ProviderRegistry
    from selfhealing.adapters.sqlalchemy import SQLAlchemyStatisticsAdapter
    
    from database import SessionLocal
    
    @app.on_event("startup")
    async def startup():
        ProviderRegistry.register_statistics_adapter(
            SQLAlchemyStatisticsAdapter(session_factory=SessionLocal)
        )

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Type, TYPE_CHECKING

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


class SQLAlchemyStatisticsAdapter(StatisticsRepositoryInterface):
    """
    SQLAlchemy implementation of StatisticsRepositoryInterface.
    
    This adapter is domain-free - tables are provided by the application.
    It uses SQLAlchemy's powerful ORM for complex aggregate queries.
    
    Attributes:
        session_factory: Callable that returns a SQLAlchemy Session
        failed_operation_table: SQLAlchemy Table/Model for DLQ entries
        circuit_breaker_table: SQLAlchemy Table/Model for CB state (optional)
    """
    
    def __init__(
        self,
        session_factory: Callable,
        failed_operation_table: Optional[Type] = None,
        circuit_breaker_table: Optional[Type] = None,
        async_mode: bool = False,
    ):
        """
        Initialize SQLAlchemy Statistics Adapter.
        
        Args:
            session_factory: Callable that returns a SQLAlchemy Session
            failed_operation_table: SQLAlchemy Model class for FailedOperation
            circuit_breaker_table: SQLAlchemy Model class for CircuitBreakerState
            async_mode: Whether to use async session (for async SQLAlchemy)
        """
        self._session_factory = session_factory
        self._failed_operation_table = failed_operation_table
        self._circuit_breaker_table = circuit_breaker_table
        self._async_mode = async_mode
        
        if failed_operation_table:
            logger.info(
                f"[SQLAlchemyStatisticsAdapter] Initialized with table: "
                f"{failed_operation_table.__name__}"
            )
        else:
            logger.warning(
                "[SQLAlchemyStatisticsAdapter] No failed_operation_table provided. "
                "Some statistics will not be available."
            )
    
    def _get_session(self):
        """Get a new session from the factory."""
        return self._session_factory()
    
    def _check_table(self) -> bool:
        """Check if table is available."""
        if self._failed_operation_table is None:
            logger.warning("[SQLAlchemyStatisticsAdapter] Table not configured")
            return False
        return True
    
    # =========================================================================
    # DLQ Statistics
    # =========================================================================
    
    def get_status_counts(self) -> StatusCounts:
        """Get count of DLQ entries by status using SQLAlchemy."""
        if not self._check_table():
            return StatusCounts()
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                results = (
                    session.query(table.status, func.count(table.id))
                    .group_by(table.status)
                    .all()
                )
                
                counts = StatusCounts()
                for status, count in results:
                    counts.total += count
                    if hasattr(counts, status):
                        setattr(counts, status, count)
                
                return counts
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_status_counts error: {e}")
            return StatusCounts()
    
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        """Get distribution of DLQ entries by domain."""
        if not self._check_table():
            return []
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                total = session.query(func.count(table.id)).scalar() or 0
                if total == 0:
                    return []
                
                results = (
                    session.query(table.domain, func.count(table.id).label('count'))
                    .group_by(table.domain)
                    .order_by(func.count(table.id).desc())
                    .limit(limit)
                    .all()
                )
                
                return [
                    DomainDistribution(
                        domain=row.domain or "unknown",
                        count=row.count,
                        percentage=round(row.count / total * 100, 2),
                    )
                    for row in results
                ]
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_domain_distribution error: {e}")
            return []
    
    def get_failure_type_distribution(self, limit: int = 10) -> List[FailureTypeDistribution]:
        """Get distribution of DLQ entries by failure type."""
        if not self._check_table():
            return []
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                total = session.query(func.count(table.id)).scalar() or 0
                if total == 0:
                    return []
                
                results = (
                    session.query(table.failure_type, func.count(table.id).label('count'))
                    .group_by(table.failure_type)
                    .order_by(func.count(table.id).desc())
                    .limit(limit)
                    .all()
                )
                
                return [
                    FailureTypeDistribution(
                        failure_type=row.failure_type or "unknown",
                        count=row.count,
                        percentage=round(row.count / total * 100, 2),
                    )
                    for row in results
                ]
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_failure_type_distribution error: {e}")
            return []
    
    def get_recent_activity(self, hours: int = 24, days: int = 7) -> RecentActivity:
        """Get recent activity statistics."""
        if not self._check_table():
            return RecentActivity()
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                now = datetime.utcnow()
                hours_ago = now - timedelta(hours=hours)
                days_ago = now - timedelta(days=days)
                
                # New entries
                new_in_24h = (
                    session.query(func.count(table.id))
                    .filter(table.created_at >= hours_ago)
                    .scalar() or 0
                )
                new_in_7d = (
                    session.query(func.count(table.id))
                    .filter(table.created_at >= days_ago)
                    .scalar() or 0
                )
                
                # Resolved entries
                resolved_in_24h = (
                    session.query(func.count(table.id))
                    .filter(table.resolved_at >= hours_ago)
                    .scalar() or 0
                )
                resolved_in_7d = (
                    session.query(func.count(table.id))
                    .filter(table.resolved_at >= days_ago)
                    .scalar() or 0
                )
                
                # Calculate trend
                prev_week_count = (
                    session.query(func.count(table.id))
                    .filter(
                        table.created_at >= days_ago - timedelta(days=7),
                        table.created_at < days_ago,
                    )
                    .scalar() or 0
                )
                
                if new_in_7d > prev_week_count * 1.1:
                    trend = "up"
                elif new_in_7d < prev_week_count * 0.9:
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
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_recent_activity error: {e}")
            return RecentActivity()
    
    def get_resolution_rate(self, days: int = 30) -> float:
        """Calculate resolution success rate."""
        if not self._check_table():
            return 0.0
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                since = datetime.utcnow() - timedelta(days=days)
                
                total = (
                    session.query(func.count(table.id))
                    .filter(table.created_at >= since)
                    .scalar() or 0
                )
                
                if total == 0:
                    return 0.0
                
                resolved = (
                    session.query(func.count(table.id))
                    .filter(
                        table.created_at >= since,
                        table.status == "resolved",
                    )
                    .scalar() or 0
                )
                
                return round(resolved / total, 4)
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_resolution_rate error: {e}")
            return 0.0
    
    def get_avg_retry_count(self) -> float:
        """Get average retry count across all DLQ entries."""
        if not self._check_table():
            return 0.0
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                result = session.query(func.avg(table.retry_count)).scalar()
                return round(result or 0.0, 2)
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_avg_retry_count error: {e}")
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
        if not self._check_table():
            return PaginatedResult(page=page, page_size=page_size)
        
        try:
            from sqlalchemy import desc, asc
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                query = session.query(table)
                
                # Apply filters
                if status:
                    query = query.filter(table.status == status)
                if domain:
                    query = query.filter(table.domain == domain)
                if failure_type:
                    query = query.filter(table.failure_type == failure_type)
                
                # Order
                if order_by.startswith("-"):
                    order_col = getattr(table, order_by[1:], table.created_at)
                    query = query.order_by(desc(order_col))
                else:
                    order_col = getattr(table, order_by, table.created_at)
                    query = query.order_by(asc(order_col))
                
                # Count total
                total = query.count()
                
                # Paginate
                offset = (page - 1) * page_size
                items = query.offset(offset).limit(page_size).all()
                
                # Convert to dicts
                item_dicts = []
                for item in items:
                    item_dict = {c.name: getattr(item, c.name) for c in item.__table__.columns}
                    item_dicts.append(item_dict)
                
                return PaginatedResult(
                    items=item_dicts,
                    total=total,
                    page=page,
                    page_size=page_size,
                    has_next=offset + page_size < total,
                    has_prev=page > 1,
                )
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] list_entries error: {e}")
            return PaginatedResult(page=page, page_size=page_size)
    
    def get_entry_detail(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific DLQ entry."""
        if not self._check_table():
            return None
        
        try:
            session = self._get_session()
            try:
                table = self._failed_operation_table
                entry = session.query(table).filter(table.id == entry_id).first()
                
                if entry:
                    return {c.name: getattr(entry, c.name) for c in entry.__table__.columns}
                return None
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_entry_detail error: {e}")
            return None
    
    # =========================================================================
    # SLA Monitoring
    # =========================================================================
    
    def get_sla_breaches(
        self,
        sla_threshold_hours: int = 4,
        statuses: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """
        Get count of SLA breaches by domain.
        
        Finds DLQ entries that have exceeded the SLA threshold for resolution.
        """
        if not self._check_table():
            return {}
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                if statuses is None:
                    statuses = ["pending", "reviewing", "requires_review"]
                
                cutoff = datetime.utcnow() - timedelta(hours=sla_threshold_hours)
                
                # Find entries that have exceeded SLA
                breach_query = (
                    session.query(table.domain, func.count(table.id))
                    .filter(
                        table.status.in_(statuses),
                        table.created_at < cutoff,
                    )
                    .group_by(table.domain)
                    .all()
                )
                
                return dict(breach_query)
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_sla_breaches error: {e}")
            return {}
    
    # =========================================================================
    # Cleanup Operations
    # =========================================================================
    
    def get_cleanup_stats(self) -> CleanupStats:
        """Get statistics for cleanup operations."""
        if not self._check_table():
            return CleanupStats()
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                # Count by status
                status_query = (
                    session.query(table.status, func.count(table.id))
                    .group_by(table.status)
                    .all()
                )
                status_counts = dict(status_query)
                
                # Resolved older than 30 days
                thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                resolved_old = (
                    session.query(func.count(table.id))
                    .filter(
                        table.status == "resolved",
                        table.resolved_at < thirty_days_ago,
                    )
                    .scalar() or 0
                )
                
                # Archived older than 90 days
                ninety_days_ago = datetime.utcnow() - timedelta(days=90)
                archived_old = (
                    session.query(func.count(table.id))
                    .filter(
                        table.status == "archived",
                        table.updated_at < ninety_days_ago,
                    )
                    .scalar() or 0
                )
                
                total = session.query(func.count(table.id)).scalar() or 0
                
                return CleanupStats(
                    total=total,
                    by_status=status_counts,
                    resolved_older_than_30_days=resolved_old,
                    archived_older_than_90_days=archived_old,
                )
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_cleanup_stats error: {e}")
            return CleanupStats()
    
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """Archive old resolved entries."""
        if not self._check_table():
            return 0
        
        try:
            session = self._get_session()
            try:
                table = self._failed_operation_table
                cutoff = datetime.utcnow() - timedelta(days=older_than_days)
                
                count = (
                    session.query(table)
                    .filter(
                        table.status == "resolved",
                        table.resolved_at < cutoff,
                    )
                    .update({"status": "archived"})
                )
                
                session.commit()
                logger.info(f"[SQLAlchemyStatisticsAdapter] Archived {count} entries")
                return count
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] archive_old_entries error: {e}")
            return 0
    
    def purge_archived(
        self,
        ids: Optional[List[str]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """Permanently delete archived entries."""
        if not self._check_table():
            return 0
        
        try:
            session = self._get_session()
            try:
                table = self._failed_operation_table
                query = session.query(table).filter(table.status == "archived")
                
                if ids:
                    query = query.filter(table.id.in_(ids))
                
                if older_than_days:
                    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
                    query = query.filter(table.updated_at < cutoff)
                
                count = query.delete(synchronize_session=False)
                session.commit()
                
                logger.info(f"[SQLAlchemyStatisticsAdapter] Purged {count} entries")
                return count
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] purge_archived error: {e}")
            return 0
    
    # =========================================================================
    # Circuit Breaker Statistics
    # =========================================================================
    
    def get_circuit_breaker_summary(self) -> CircuitBreakerSummary:
        """Get summary of all circuit breakers."""
        if not self._circuit_breaker_table:
            # Fallback to Redis-based stats
            return self._get_cb_summary_from_redis()
        
        try:
            from sqlalchemy import func
            
            session = self._get_session()
            try:
                table = self._circuit_breaker_table
                
                results = (
                    session.query(table.state, func.count(table.id))
                    .group_by(table.state)
                    .all()
                )
                
                summary = CircuitBreakerSummary()
                for state, count in results:
                    summary.total += count
                    if state == "closed":
                        summary.closed = count
                    elif state == "open":
                        summary.open = count
                    elif state == "half_open":
                        summary.half_open = count
                
                return summary
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] get_circuit_breaker_summary error: {e}")
            return CircuitBreakerSummary()
    
    def _get_cb_summary_from_redis(self) -> CircuitBreakerSummary:
        """Get CB summary from Redis runtime repository."""
        try:
            from selfhealing.factory import ProviderRegistry
            
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            states = cb_repo.get_all_states()
            
            summary = CircuitBreakerSummary(total=len(states))
            for state in states.values():
                state_val = state.state.value if hasattr(state.state, 'value') else str(state.state)
                if state_val == "closed":
                    summary.closed += 1
                elif state_val == "open":
                    summary.open += 1
                elif state_val == "half_open":
                    summary.half_open += 1
            
            return summary
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] _get_cb_summary_from_redis error: {e}")
            return CircuitBreakerSummary()
    
    def list_circuit_breakers(self) -> List[CircuitBreakerInfo]:
        """List all circuit breakers with their current state."""
        if not self._circuit_breaker_table:
            # Fallback to Redis
            return self._list_cbs_from_redis()
        
        try:
            session = self._get_session()
            try:
                table = self._circuit_breaker_table
                entries = session.query(table).all()
                
                return [
                    CircuitBreakerInfo(
                        service_name=getattr(entry, "service_name", "unknown"),
                        state=getattr(entry, "state", "closed"),
                        failure_count=getattr(entry, "failure_count", 0),
                        success_count=getattr(entry, "success_count", 0),
                        last_failure_time=getattr(entry, "last_failure_time", None),
                        last_state_change=getattr(entry, "last_state_change", None),
                    )
                    for entry in entries
                ]
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] list_circuit_breakers error: {e}")
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
            logger.error(f"[SQLAlchemyStatisticsAdapter] _list_cbs_from_redis error: {e}")
            return []
    
    # =========================================================================
    # Persistence (for hybrid storage)
    # =========================================================================
    
    def persist_entry(self, entry_data: Dict[str, Any]) -> Optional[str]:
        """Persist a DLQ entry to the statistics store."""
        if not self._check_table():
            return None
        
        try:
            session = self._get_session()
            try:
                table = self._failed_operation_table
                
                entry_id = entry_data.get("id")
                if entry_id:
                    # Update existing
                    existing = session.query(table).filter(table.id == entry_id).first()
                    if existing:
                        for key, value in entry_data.items():
                            if hasattr(existing, key):
                                setattr(existing, key, value)
                        session.commit()
                        return str(existing.id)
                
                # Create new
                obj = table(**entry_data)
                session.add(obj)
                session.commit()
                session.refresh(obj)
                return str(obj.id)
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] persist_entry error: {e}")
            return None
    
    def sync_from_runtime(self, entries: List[Dict[str, Any]]) -> int:
        """Bulk sync entries from runtime repository."""
        if not self._check_table():
            return 0
        
        synced = 0
        for entry_data in entries:
            if self.persist_entry(entry_data):
                synced += 1
        
        logger.info(f"[SQLAlchemyStatisticsAdapter] Synced {synced}/{len(entries)} entries")
        return synced
    
    # =========================================================================
    # Audit Trail Integration (The Master Trail)
    # =========================================================================
    
    def get_audit_trail_by_entity(
        self,
        entity_id: str,
        entity_type: str = "dlq_entry",
    ) -> EntityAuditTrail:
        """Get complete audit trail for a specific entity."""
        trail = EntityAuditTrail(
            entity_id=entity_id,
            entity_type=entity_type,
            domain="unknown",
            entries=[],
        )
        
        # Get DLQ entry details if available
        if self._check_table() and entity_type == "dlq_entry":
            try:
                session = self._get_session()
                try:
                    table = self._failed_operation_table
                    entry = session.query(table).filter(table.id == entity_id).first()
                    if entry:
                        trail.domain = getattr(entry, "domain", "unknown")
                        trail.created_at = getattr(entry, "created_at", None)
                        trail.resolved_at = getattr(entry, "resolved_at", None)
                        trail.current_status = getattr(entry, "status", "unknown")
                finally:
                    session.close()
            except Exception as e:
                logger.warning(f"[SQLAlchemyStatisticsAdapter] Failed to get DLQ entry: {e}")
        
        # Get audit entries from audit adapter
        try:
            from selfhealing.factory import ProviderRegistry
            
            audit_adapter = ProviderRegistry.get_audit_adapter()
            if hasattr(audit_adapter, "get_entries_by_entity"):
                audit_entries = audit_adapter.get_entries_by_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                )
                
                for audit_entry in audit_entries:
                    trail.entries.append(
                        AuditTrailEntry(
                            timestamp=audit_entry.timestamp,
                            action=audit_entry.action.value if hasattr(audit_entry.action, 'value') else str(audit_entry.action),
                            actor_id=audit_entry.actor_id,
                            status=getattr(audit_entry, 'new_value', None),
                            details=getattr(audit_entry, 'details', None),
                            hash_chain=getattr(audit_entry, 'hash', None),
                            previous_hash=getattr(audit_entry, 'previous_hash', None),
                        )
                    )
        except Exception as e:
            logger.debug(f"[SQLAlchemyStatisticsAdapter] Audit trail lookup skipped: {e}")
        
        return trail
    
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
        """Link an audit record to an entity."""
        if not self._check_table():
            return False
        
        try:
            session = self._get_session()
            try:
                table = self._failed_operation_table
                entry = session.query(table).filter(table.id == entity_id).first()
                if not entry:
                    return False
                
                # If the model has a metadata field, store audit references
                if hasattr(entry, "metadata"):
                    import json
                    metadata = entry.metadata or {}
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    
                    audit_refs = metadata.get("audit_references", [])
                    audit_refs.append({
                        "action": action,
                        "actor_id": actor_id,
                        "status": status,
                        "hash": audit_record_hash,
                    })
                    metadata["audit_references"] = audit_refs
                    
                    entry.metadata = json.dumps(metadata) if isinstance(entry.metadata, str) else metadata
                    session.commit()
                
                return True
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"[SQLAlchemyStatisticsAdapter] link_audit_entry error: {e}")
            return False
    
    # =========================================================================
    # Async Persistence Configuration
    # =========================================================================
    
    def should_persist_async(self) -> bool:
        """Check if async persistence is configured."""
        return self._async_mode
    
    def get_async_persist_task_name(self) -> Optional[str]:
        """Get the Celery task name for async persistence."""
        if self.should_persist_async():
            return "selfhealing.adapters.celery.tasks.async_persist_dlq_entry"
        return None
