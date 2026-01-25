"""
Integrity Health Score.

Contains:
- IntegrityHealthScore: Real-time health monitoring for hash chain integrity
- Prometheus metrics integration
- Dashboard-ready statistics

Purpose:
    Visualize the "self-healing intelligence" of the system:
    - "Current integrity: 100%"
    - "Today: 3 potential chain breaks auto-repaired"
    - "Last 24h: 0 orphaned sequences"
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from selfhealing.settings.audit_integrity import get_audit_integrity_settings

logger = logging.getLogger(__name__)


def _get_healthy_threshold() -> float:
    """Get healthy threshold from settings."""
    return get_audit_integrity_settings().health_healthy_threshold


def _get_warning_threshold() -> float:
    """Get warning threshold from settings."""
    return get_audit_integrity_settings().health_warning_threshold


def _get_critical_threshold() -> float:
    """Get critical threshold from settings."""
    return get_audit_integrity_settings().health_critical_threshold


@dataclass
class RecoveryEvent:
    """Single recovery event record."""
    
    event_type: str  # "reconcile", "startup_sync", "watchdog_cleanup"
    sequences_affected: int
    recovery_time_ms: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrityHealthMetrics:
    """
    Aggregated integrity health metrics.
    
    Designed for Prometheus Gauge export and Grafana dashboard.
    """
    
    # Current state (Gauge)
    is_healthy: bool = True
    health_score: float = 100.0  # 0-100 percentage
    
    # Counters (for rate calculation)
    total_sequences: int = 0
    verified_sequences: int = 0
    degraded_sequences: int = 0
    orphaned_sequences: int = 0
    reconciled_sequences: int = 0
    
    # Recovery statistics (last 24h)
    recoveries_today: int = 0
    avg_recovery_time_ms: float = 0.0
    max_recovery_time_ms: float = 0.0
    
    # Chain state
    chain_length: int = 0
    last_verified_sequence: int = 0
    last_anchor_date: Optional[str] = None
    days_since_last_break: int = 0
    
    # Timestamps
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_recovery_at: Optional[str] = None


class IntegrityHealthScore:
    """
    Real-time integrity health monitoring.
    
    Aggregates data from:
    - HashChainReconciler (degraded entry recovery)
    - StartupHashChainSync (startup sync events)
    - PendingSequenceManager (orphaned sequence cleanup)
    - DailyHashAnchor (anchor verification)
    
    Exposes metrics as:
    - Prometheus Gauges for real-time monitoring
    - JSON API for dashboard integration
    - Self-audit trail for compliance
    
    Example:
        >>> health = IntegrityHealthScore()
        >>> metrics = health.get_current_metrics()
        >>> print(f"Health Score: {metrics.health_score}%")
        >>> print(f"Recoveries today: {metrics.recoveries_today}")
    """
    
    # Prometheus metric names
    GAUGE_HEALTH_SCORE = "selfhealing_integrity_health_score"
    GAUGE_DEGRADED_COUNT = "selfhealing_integrity_degraded_count"
    GAUGE_ORPHANED_COUNT = "selfhealing_integrity_orphaned_count"
    GAUGE_RECOVERIES_TODAY = "selfhealing_integrity_recoveries_today"
    COUNTER_TOTAL_RECOVERIES = "selfhealing_integrity_recoveries_total"
    
    # Legacy constants for backward compatibility
    HEALTHY_THRESHOLD = 95.0
    WARNING_THRESHOLD = 80.0
    CRITICAL_THRESHOLD = 50.0
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        prometheus_registry: Optional[Any] = None,
        healthy_threshold: Optional[float] = None,
        warning_threshold: Optional[float] = None,
        critical_threshold: Optional[float] = None,
    ):
        """
        Initialize IntegrityHealthScore.
        
        Args:
            redis_client: Redis client for fetching chain state
            prometheus_registry: Prometheus registry for metrics
            healthy_threshold: Healthy score threshold (default from AuditIntegritySettings)
            warning_threshold: Warning score threshold (default from AuditIntegritySettings)
            critical_threshold: Critical score threshold (default from AuditIntegritySettings)
        """
        self._redis = redis_client
        self._lock = threading.Lock()
        
        # Health thresholds from settings
        self._healthy_threshold = healthy_threshold if healthy_threshold is not None else _get_healthy_threshold()
        self._warning_threshold = warning_threshold if warning_threshold is not None else _get_warning_threshold()
        self._critical_threshold = critical_threshold if critical_threshold is not None else _get_critical_threshold()
        
        # In-memory event buffer (last 24h)
        self._recovery_events: List[RecoveryEvent] = []
        self._max_events = 1000
        
        # Cached metrics
        self._cached_metrics: Optional[IntegrityHealthMetrics] = None
        self._cache_ttl_seconds = 10
        self._cache_updated_at: Optional[float] = None
        
        # Prometheus gauges (lazy init)
        self._prometheus_gauges: Dict[str, Any] = {}
        self._prometheus_registry = prometheus_registry
        self._prometheus_initialized = False
        
        # Stats tracking
        self._days_since_break = 0
        self._last_break_date: Optional[datetime] = None
    
    def _init_prometheus(self) -> None:
        """Lazy initialize Prometheus metrics."""
        if self._prometheus_initialized:
            return
        
        try:
            from prometheus_client import Gauge, Counter
            
            registry = self._prometheus_registry
            
            self._prometheus_gauges["health_score"] = Gauge(
                self.GAUGE_HEALTH_SCORE,
                "Integrity health score (0-100)",
                registry=registry,
            )
            self._prometheus_gauges["degraded_count"] = Gauge(
                self.GAUGE_DEGRADED_COUNT,
                "Current count of degraded sequences",
                registry=registry,
            )
            self._prometheus_gauges["orphaned_count"] = Gauge(
                self.GAUGE_ORPHANED_COUNT,
                "Current count of orphaned sequences",
                registry=registry,
            )
            self._prometheus_gauges["recoveries_today"] = Gauge(
                self.GAUGE_RECOVERIES_TODAY,
                "Number of auto-recoveries in last 24h",
                registry=registry,
            )
            
            self._prometheus_initialized = True
            logger.debug("[HealthScore] Prometheus metrics initialized")
            
        except ImportError:
            logger.debug("[HealthScore] prometheus_client not available")
            self._prometheus_initialized = True  # Don't retry
    
    def record_recovery(
        self,
        event_type: str,
        sequences_affected: int,
        recovery_time_ms: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a recovery event.
        
        Args:
            event_type: Type of recovery (reconcile, startup_sync, watchdog_cleanup)
            sequences_affected: Number of sequences recovered
            recovery_time_ms: Time taken for recovery in milliseconds
            details: Additional event details
        """
        event = RecoveryEvent(
            event_type=event_type,
            sequences_affected=sequences_affected,
            recovery_time_ms=recovery_time_ms,
            details=details or {},
        )
        
        with self._lock:
            self._recovery_events.append(event)
            
            # Trim old events (keep last 24h or max_events)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            cutoff_str = cutoff.isoformat()
            
            self._recovery_events = [
                e for e in self._recovery_events
                if e.timestamp >= cutoff_str
            ][-self._max_events:]
            
            # Invalidate cache
            self._cached_metrics = None
        
        # Update Prometheus
        self._update_prometheus_metrics()
        
        # Log to self-audit
        self._log_recovery_event(event)
        
        logger.info(
            f"[HealthScore] Recorded recovery: type={event_type}, "
            f"sequences={sequences_affected}, time={recovery_time_ms:.2f}ms"
        )
    
    def record_chain_break(self) -> None:
        """Record that a chain break was detected (resets days counter)."""
        with self._lock:
            self._last_break_date = datetime.now(timezone.utc)
            self._days_since_break = 0
            self._cached_metrics = None
    
    def get_current_metrics(self, force_refresh: bool = False) -> IntegrityHealthMetrics:
        """
        Get current integrity health metrics.
        
        Args:
            force_refresh: Force recalculation even if cache is valid
            
        Returns:
            IntegrityHealthMetrics dataclass
        """
        # Check cache
        if not force_refresh and self._cached_metrics:
            now = time.monotonic()
            if self._cache_updated_at and (now - self._cache_updated_at) < self._cache_ttl_seconds:
                return self._cached_metrics
        
        with self._lock:
            metrics = self._calculate_metrics()
            self._cached_metrics = metrics
            self._cache_updated_at = time.monotonic()
            return metrics
    
    def _calculate_metrics(self) -> IntegrityHealthMetrics:
        """Calculate current metrics from state and events."""
        metrics = IntegrityHealthMetrics()
        
        # Get chain state from Redis
        chain_state = self._get_chain_state()
        metrics.total_sequences = chain_state.get("sequence", 0)
        metrics.chain_length = metrics.total_sequences
        metrics.last_verified_sequence = metrics.total_sequences
        
        # Count degraded/orphaned (would need to scan Redis or local files)
        # For now, estimate from recent events
        metrics.degraded_sequences = chain_state.get("degraded_count", 0)
        metrics.orphaned_sequences = chain_state.get("orphaned_count", 0)
        
        # Calculate from recovery events
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_str = today_start.isoformat()
        
        today_events = [e for e in self._recovery_events if e.timestamp >= today_str]
        metrics.recoveries_today = len(today_events)
        
        if today_events:
            recovery_times = [e.recovery_time_ms for e in today_events]
            metrics.avg_recovery_time_ms = sum(recovery_times) / len(recovery_times)
            metrics.max_recovery_time_ms = max(recovery_times)
            metrics.last_recovery_at = today_events[-1].timestamp
            
            # Sum reconciled sequences
            metrics.reconciled_sequences = sum(e.sequences_affected for e in today_events)
        
        # Calculate verified sequences
        metrics.verified_sequences = metrics.total_sequences - metrics.degraded_sequences - metrics.orphaned_sequences
        
        # Calculate health score
        if metrics.total_sequences > 0:
            verified_ratio = metrics.verified_sequences / metrics.total_sequences
            metrics.health_score = round(verified_ratio * 100, 2)
        else:
            metrics.health_score = 100.0  # No sequences = healthy (no issues)
        
        # Determine if healthy
        metrics.is_healthy = metrics.health_score >= self._healthy_threshold
        
        # Days since last break
        if self._last_break_date:
            delta = now - self._last_break_date
            metrics.days_since_last_break = delta.days
        else:
            metrics.days_since_last_break = -1  # Never had a break
        
        # Anchor info
        metrics.last_anchor_date = chain_state.get("last_anchor_date")
        
        return metrics
    
    def _get_chain_state(self) -> Dict[str, Any]:
        """Get current chain state from Redis."""
        if not self._redis:
            return {"sequence": 0}
        
        try:
            state_key = "selfhealing:audit:hash_chain:state"
            state = self._redis.hgetall(state_key)
            
            if not state:
                return {"sequence": 0}
            
            result = {}
            for key, value in state.items():
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                val_str = value.decode("utf-8") if isinstance(value, bytes) else value
                
                # Convert numeric fields
                if key_str in ("sequence", "degraded_count", "orphaned_count"):
                    result[key_str] = int(val_str) if val_str else 0
                else:
                    result[key_str] = val_str
            
            return result
            
        except Exception as e:
            logger.warning(f"[HealthScore] Failed to get chain state: {e}")
            return {"sequence": 0}
    
    def _update_prometheus_metrics(self) -> None:
        """Update Prometheus gauge values."""
        self._init_prometheus()
        
        if not self._prometheus_gauges:
            return
        
        try:
            metrics = self.get_current_metrics()
            
            if "health_score" in self._prometheus_gauges:
                self._prometheus_gauges["health_score"].set(metrics.health_score)
            if "degraded_count" in self._prometheus_gauges:
                self._prometheus_gauges["degraded_count"].set(metrics.degraded_sequences)
            if "orphaned_count" in self._prometheus_gauges:
                self._prometheus_gauges["orphaned_count"].set(metrics.orphaned_sequences)
            if "recoveries_today" in self._prometheus_gauges:
                self._prometheus_gauges["recoveries_today"].set(metrics.recoveries_today)
                
        except Exception as e:
            logger.warning(f"[HealthScore] Failed to update Prometheus: {e}")
    
    def _log_recovery_event(self, event: RecoveryEvent) -> None:
        """Log recovery event to self-audit trail."""
        try:
            from selfhealing.audit.self_audit import self_audit, SelfAuditEvent
            
            self_audit().log(
                SelfAuditEvent.RECOVERY_COMPLETED,
                f"Integrity recovery: {event.event_type} ({event.sequences_affected} sequences)",
                {
                    "action": "integrity_recovery",
                    "event_type": event.event_type,
                    "sequences_affected": event.sequences_affected,
                    "recovery_time_ms": event.recovery_time_ms,
                    "details": event.details,
                }
            )
        except (ImportError, AttributeError):
            pass  # self_audit not available
    
    def get_health_status(self) -> str:
        """
        Get human-readable health status.
        
        Returns:
            Status string: "HEALTHY", "WARNING", "CRITICAL", or "UNKNOWN"
        """
        metrics = self.get_current_metrics()
        
        if metrics.health_score >= self._healthy_threshold:
            return "HEALTHY"
        elif metrics.health_score >= self._warning_threshold:
            return "WARNING"
        elif metrics.health_score >= self._critical_threshold:
            return "CRITICAL"
        else:
            return "CRITICAL"
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Get dashboard-ready summary.
        
        Returns:
            Dictionary suitable for dashboard display
        """
        metrics = self.get_current_metrics()
        status = self.get_health_status()
        
        return {
            "status": status,
            "health_score": metrics.health_score,
            "is_healthy": metrics.is_healthy,
            "summary": {
                "chain_length": metrics.chain_length,
                "verified_sequences": metrics.verified_sequences,
                "degraded_sequences": metrics.degraded_sequences,
                "orphaned_sequences": metrics.orphaned_sequences,
            },
            "recovery": {
                "recoveries_today": metrics.recoveries_today,
                "sequences_recovered": metrics.reconciled_sequences,
                "avg_recovery_time_ms": metrics.avg_recovery_time_ms,
                "last_recovery_at": metrics.last_recovery_at,
            },
            "streaks": {
                "days_since_last_break": metrics.days_since_last_break,
            },
            "message": self._generate_status_message(metrics),
            "calculated_at": metrics.calculated_at,
        }
    
    def _generate_status_message(self, metrics: IntegrityHealthMetrics) -> str:
        """Generate human-readable status message."""
        if metrics.health_score >= 100:
            base = "Integrity: 100% - All sequences verified"
        elif metrics.health_score >= self._healthy_threshold:
            base = f"Integrity: {metrics.health_score:.1f}% - Healthy"
        elif metrics.health_score >= self._warning_threshold:
            base = f"Integrity: {metrics.health_score:.1f}% - Warning: {metrics.degraded_sequences} degraded"
        else:
            base = f"Integrity: {metrics.health_score:.1f}% - Critical: Immediate attention required"
        
        if metrics.recoveries_today > 0:
            base += f" | Today: {metrics.recoveries_today} auto-recoveries"
        
        return base
    
    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent recovery events.
        
        Args:
            limit: Maximum number of events to return
            
        Returns:
            List of event dictionaries
        """
        with self._lock:
            recent = self._recovery_events[-limit:]
            return [
                {
                    "event_type": e.event_type,
                    "sequences_affected": e.sequences_affected,
                    "recovery_time_ms": e.recovery_time_ms,
                    "timestamp": e.timestamp,
                    "details": e.details,
                }
                for e in reversed(recent)
            ]


# Singleton instance
_health_score_instance: Optional[IntegrityHealthScore] = None
_health_score_lock = threading.Lock()


def get_integrity_health_score(
    redis_client: Optional[Any] = None,
) -> IntegrityHealthScore:
    """
    Get singleton IntegrityHealthScore instance.
    
    Args:
        redis_client: Redis client (only used on first call)
        
    Returns:
        IntegrityHealthScore singleton
    """
    global _health_score_instance
    
    with _health_score_lock:
        if _health_score_instance is None:
            _health_score_instance = IntegrityHealthScore(redis_client=redis_client)
        return _health_score_instance


def reset_integrity_health_score() -> None:
    """Reset singleton (for testing)."""
    global _health_score_instance
    with _health_score_lock:
        _health_score_instance = None


__all__ = [
    "IntegrityHealthScore",
    "IntegrityHealthMetrics",
    "RecoveryEvent",
    "get_integrity_health_score",
    "reset_integrity_health_score",
]
