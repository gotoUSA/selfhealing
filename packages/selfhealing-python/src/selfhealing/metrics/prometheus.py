"""
Prometheus metrics for the self-healing system.

This module provides Prometheus metric definitions and collection utilities.
"""

from typing import Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager
from functools import wraps
import logging

from selfhealing.metrics.safe_gauge import clamp_non_negative, clamp_percentage

logger = logging.getLogger(__name__)

# Try to import prometheus_client, but don't fail if not installed
try:
    from prometheus_client import Counter, Gauge, Histogram, Info

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None
    Gauge = None
    Histogram = None
    Info = None


# =============================================================================
# Domain Constants (Configurable)
# =============================================================================

# Default domains - can be extended via register_domain()
_registered_domains: list = [
    "external_service",
    "internal_process",
    "async_task",
    "notification",
    "data_sync",
]


def get_domains() -> list:
    """Get all registered domains."""
    return _registered_domains.copy()


def register_domain(domain: str) -> None:
    """Register a new domain for metrics tracking."""
    if domain not in _registered_domains:
        _registered_domains.append(domain)


# Note: DOMAINS constant removed in Phase 2 neutralization.
# Use get_domains() or register_domain() instead.


class SelfHealingMetrics:
    """
    Prometheus metrics for the self-healing system.

    This class provides metric collection with graceful degradation
    when prometheus_client is not installed.
    """

    def __init__(self, prefix: str = "selfhealing"):
        """
        Initialize metrics with the given prefix.

        Args:
            prefix: Metric name prefix
        """
        self.prefix = prefix
        self._initialized = False

        if not PROMETHEUS_AVAILABLE:
            logger.warning("prometheus_client not installed. Metrics will be no-ops.")
            return

        # =============================================================================
        # DLQ Metrics
        # =============================================================================

        self.dlq_items_total = Counter(
            f"{prefix}_dlq_items_total",
            "Total DLQ items created",
            ["domain", "failure_type"],
        )

        self.dlq_pending_gauge = Gauge(
            f"{prefix}_dlq_pending_count",
            "Current pending DLQ items",
            ["domain"],
        )

        self.dlq_by_status_gauge = Gauge(
            f"{prefix}_dlq_items_by_status",
            "DLQ items count by status",
            ["status"],
        )

        self.dlq_created_total = Counter(
            f"{prefix}_dlq_created_total",
            "Total DLQ items created (for rate calculation)",
            ["domain"],
        )

        # =============================================================================
        # Retry Metrics
        # =============================================================================

        self.retry_attempts_histogram = Histogram(
            f"{prefix}_retry_attempts_distribution",
            "Number of retry attempts before resolution",
            ["domain"],
            buckets=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        )

        self.retry_outcomes_total = Counter(
            f"{prefix}_retry_outcomes_total",
            "Retry outcomes by domain and result",
            ["domain", "outcome"],
        )

        self.retry_success_rate = Gauge(
            f"{prefix}_retry_success_rate",
            "Percentage of successful retries (0-100)",
            ["domain"],
        )

        self.retry_delay_seconds = Histogram(
            f"{prefix}_retry_delay_seconds",
            "Retry delay in seconds",
            ["domain"],
            buckets=[1, 5, 10, 30, 60, 120, 300, 600],
        )

        # =============================================================================
        # Recovery Metrics
        # =============================================================================

        self.recovery_time_seconds = Histogram(
            f"{prefix}_recovery_time_seconds",
            "Time from failure to resolution in seconds",
            ["domain", "resolution_type"],
            buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400],
        )

        self.sla_breach_total = Counter(
            f"{prefix}_sla_breach_total",
            "Total SLA breaches detected",
            ["domain"],
        )

        self.human_review_queue_time = Histogram(
            f"{prefix}_human_review_queue_time_seconds",
            "Time items wait in queue for human review",
            ["domain"],
            buckets=[300, 900, 1800, 3600, 7200, 14400, 28800],
        )

        # =============================================================================
        # Circuit Breaker Metrics
        # =============================================================================

        self.circuit_breaker_state = Gauge(
            f"{prefix}_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=open, 2=half_open)",
            ["service_name"],
        )

        self.circuit_breaker_failures = Counter(
            f"{prefix}_circuit_breaker_failures_total",
            "Total circuit breaker failures",
            ["service_name"],
        )

        self.circuit_breaker_trips = Counter(
            f"{prefix}_circuit_breaker_trips_total",
            "Total times circuit breaker tripped to open",
            ["service_name"],
        )

        self.circuit_breaker_transitions = Counter(
            f"{prefix}_circuit_breaker_transitions_total",
            "Total circuit breaker state transitions",
            ["service_name", "from_state", "to_state"],
        )

        self.circuit_breaker_open_duration = Histogram(
            f"{prefix}_circuit_breaker_open_duration_seconds",
            "Duration in open state before closing",
            ["service_name"],
            buckets=[60, 300, 600, 1800, 3600, 7200],
        )

        # =============================================================================
        # Replay Metrics
        # =============================================================================

        self.replay_attempts_total = Counter(
            f"{prefix}_replay_attempts_total",
            "Total replay attempts",
            ["domain", "replay_type"],
        )

        self.replay_outcomes_total = Counter(
            f"{prefix}_replay_outcomes_total",
            "Replay outcomes",
            ["domain", "outcome"],
        )

        self.replay_duration_seconds = Histogram(
            f"{prefix}_replay_duration_seconds",
            "Replay operation duration",
            ["domain"],
            buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
        )

        # =============================================================================
        # Security Metrics
        # =============================================================================

        self.security_incidents = Counter(
            f"{prefix}_security_incidents_total",
            "Total security incidents",
            ["incident_type", "severity"],
        )

        # Info metric
        self.info = Info(
            f"{prefix}_info",
            "Self-healing system information",
        )

        self._initialized = True

    # =========================================================================
    # DLQ Recording Methods
    # =========================================================================

    def record_dlq_item_created(self, domain: str, failure_type: str) -> None:
        """Record that a new DLQ item was created."""
        if not self._initialized:
            return
        try:
            self.dlq_items_total.labels(domain=domain, failure_type=failure_type).inc()
            self.dlq_created_total.labels(domain=domain).inc()
            logger.debug(f"[Metrics] DLQ item created: domain={domain}, type={failure_type}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record DLQ creation metric: {e}")

    def set_dlq_pending_count(self, domain: str, count: int) -> None:
        """Set the pending DLQ item count for a domain."""
        if not self._initialized:
            return
        try:
            # 음수 방어: clamp_non_negative 유틸리티 사용
            safe_count = clamp_non_negative(count, f"dlq_pending_count[{domain}]")
            self.dlq_pending_gauge.labels(domain=domain).set(safe_count)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set DLQ pending count: {e}")

    def set_dlq_status_count(self, status: str, count: int) -> None:
        """Set the DLQ item count for a status."""
        if not self._initialized:
            return
        try:
            # 음수 방어: clamp_non_negative 유틸리티 사용
            safe_count = clamp_non_negative(count, f"dlq_status_count[{status}]")
            self.dlq_by_status_gauge.labels(status=status).set(safe_count)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set DLQ status count: {e}")

    # Legacy method name for backward compatibility
    def set_dlq_count(self, status: str, domain: str, count: int) -> None:
        """Set the DLQ operation count (legacy interface)."""
        self.set_dlq_pending_count(domain, count)

    def record_dlq_created(self, domain: str, failure_type: str) -> None:
        """Record a new operation added to DLQ (alias)."""
        self.record_dlq_item_created(domain, failure_type)

    # =========================================================================
    # Retry Recording Methods
    # =========================================================================

    def record_retry_attempt(self, domain: str, attempt_count: int, outcome: str) -> None:
        """Record a retry attempt outcome."""
        if not self._initialized:
            return
        try:
            self.retry_attempts_histogram.labels(domain=domain).observe(attempt_count)
            self.retry_outcomes_total.labels(domain=domain, outcome=outcome).inc()
            logger.debug(f"[Metrics] Retry recorded: domain={domain}, attempts={attempt_count}, outcome={outcome}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record retry metric: {e}")

    def record_retry(self, domain: str, success: bool, delay: Optional[float] = None) -> None:
        """Record a retry attempt with optional delay."""
        if not self._initialized:
            return
        try:
            outcome = "success" if success else "failure"
            self.retry_outcomes_total.labels(domain=domain, outcome=outcome).inc()

            if delay is not None:
                self.retry_delay_seconds.labels(domain=domain).observe(delay)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record retry metric: {e}")

    def set_retry_success_rate(self, domain: str, rate: float) -> None:
        """Set the retry success rate for a domain (0-100)."""
        if not self._initialized:
            return
        try:
            # 0-100 범위 클램핑: clamp_percentage 유틸리티 사용
            safe_rate = clamp_percentage(rate, f"retry_success_rate[{domain}]")
            self.retry_success_rate.labels(domain=domain).set(safe_rate)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set retry success rate: {e}")

    # =========================================================================
    # Recovery Recording Methods
    # =========================================================================

    def record_recovery_time(
        self,
        domain: str,
        resolution_type: str,
        created_at: datetime,
        resolved_at: datetime,
    ) -> None:
        """Record time from failure to resolution."""
        if not self._initialized:
            return
        try:
            duration = (resolved_at - created_at).total_seconds()
            self.recovery_time_seconds.labels(domain=domain, resolution_type=resolution_type).observe(duration)
            logger.debug(f"[Metrics] Recovery time recorded: domain={domain}, type={resolution_type}, duration={duration}s")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record recovery time metric: {e}")

    def record_sla_breach(self, domain: str) -> None:
        """Record an SLA breach event."""
        if not self._initialized:
            return
        try:
            self.sla_breach_total.labels(domain=domain).inc()
            logger.info(f"[Metrics] SLA breach recorded: domain={domain}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record SLA breach metric: {e}")

    # =========================================================================
    # Circuit Breaker Recording Methods
    # =========================================================================

    def set_circuit_state(self, service_name: str, state: str) -> None:
        """Set the circuit breaker state metric."""
        if not self._initialized:
            return
        try:
            state_map = {"closed": 0, "open": 1, "half_open": 2}
            value = state_map.get(state, 0)
            self.circuit_breaker_state.labels(service_name=service_name).set(value)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set circuit breaker state: {e}")

    def record_circuit_failure(self, service_name: str) -> None:
        """Record a circuit breaker failure."""
        if not self._initialized:
            return
        try:
            self.circuit_breaker_failures.labels(service_name=service_name).inc()
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record circuit failure: {e}")

    def record_circuit_trip(self, service_name: str) -> None:
        """Record a circuit breaker trip to open state."""
        if not self._initialized:
            return
        try:
            self.circuit_breaker_trips.labels(service_name=service_name).inc()
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record circuit trip: {e}")

    def record_circuit_breaker_state_change(
        self,
        service_name: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """Record a circuit breaker state transition."""
        if not self._initialized:
            return
        try:
            # Update state gauge
            self.set_circuit_state(service_name, to_state)

            # Record transition
            self.circuit_breaker_transitions.labels(
                service_name=service_name,
                from_state=from_state,
                to_state=to_state,
            ).inc()

            logger.info(f"[Metrics] Circuit breaker transition: {service_name} {from_state} -> {to_state}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record circuit breaker metric: {e}")

    def record_circuit_breaker_open_duration(self, service_name: str, duration_seconds: float) -> None:
        """Record how long a circuit breaker was in open state."""
        if not self._initialized:
            return
        try:
            self.circuit_breaker_open_duration.labels(service_name=service_name).observe(duration_seconds)
            logger.debug(f"[Metrics] CB open duration recorded: {service_name}={duration_seconds}s")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record CB duration metric: {e}")

    # =========================================================================
    # Replay Recording Methods
    # =========================================================================

    def record_replay_attempt(self, domain: str, replay_type: str, success: bool) -> None:
        """Record a replay attempt."""
        if not self._initialized:
            return
        try:
            self.replay_attempts_total.labels(domain=domain, replay_type=replay_type).inc()
            outcome = "success" if success else "failure"
            self.replay_outcomes_total.labels(domain=domain, outcome=outcome).inc()
            logger.debug(f"[Metrics] Replay recorded: domain={domain}, type={replay_type}, success={success}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record replay metric: {e}")

    def record_replay(self, domain: str, result: str, duration: Optional[float] = None) -> None:
        """Record a replay operation."""
        if not self._initialized:
            return
        try:
            self.replay_outcomes_total.labels(domain=domain, outcome=result).inc()

            if duration is not None:
                self.replay_duration_seconds.labels(domain=domain).observe(duration)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record replay metric: {e}")

    # =========================================================================
    # Security Recording Methods
    # =========================================================================

    def record_security_incident(self, incident_type: str, severity: str) -> None:
        """Record a security incident."""
        if not self._initialized:
            return
        try:
            self.security_incidents.labels(incident_type=incident_type, severity=severity).inc()
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record security incident: {e}")

    # =========================================================================
    # Info and Utility Methods
    # =========================================================================

    def set_info(self, info_dict: Dict[str, str]) -> None:
        """Set the info metric."""
        if not self._initialized:
            return
        try:
            self.info.info(info_dict)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set info: {e}")

    @contextmanager
    def timer(self, domain: str, metric_type: str = "replay"):
        """Context manager for timing operations."""
        start_time = datetime.now()
        try:
            yield
        finally:
            if self._initialized:
                duration = (datetime.now() - start_time).total_seconds()
                if metric_type == "replay":
                    self.replay_duration_seconds.labels(domain=domain).observe(duration)


# Global metrics instance
_metrics: Optional[SelfHealingMetrics] = None


def get_metrics(prefix: str = "selfhealing") -> SelfHealingMetrics:
    """Get the global metrics instance, creating if necessary."""
    global _metrics
    if _metrics is None:
        _metrics = SelfHealingMetrics(prefix=prefix)
    return _metrics


def reset_metrics() -> None:
    """Reset the global metrics instance (mainly for testing)."""
    global _metrics
    _metrics = None


# =============================================================================
# Convenience Functions (for backward compatibility)
# =============================================================================


def record_dlq_item_created(domain: str, failure_type: str) -> None:
    """Record that a new DLQ item was created."""
    get_metrics().record_dlq_item_created(domain, failure_type)


def record_retry_attempt(domain: str, attempt_count: int, outcome: str) -> None:
    """Record a retry attempt outcome."""
    get_metrics().record_retry_attempt(domain, attempt_count, outcome)


def record_recovery_time(
    domain: str,
    resolution_type: str,
    created_at: datetime,
    resolved_at: datetime,
) -> None:
    """Record time from failure to resolution."""
    get_metrics().record_recovery_time(domain, resolution_type, created_at, resolved_at)


def record_sla_breach(domain: str) -> None:
    """Record an SLA breach event."""
    get_metrics().record_sla_breach(domain)


def record_circuit_breaker_state_change(
    service_name: str,
    from_state: str,
    to_state: str,
) -> None:
    """Record a circuit breaker state transition."""
    get_metrics().record_circuit_breaker_state_change(service_name, from_state, to_state)


def record_circuit_breaker_open_duration(service_name: str, duration_seconds: float) -> None:
    """Record how long a circuit breaker was in open state."""
    get_metrics().record_circuit_breaker_open_duration(service_name, duration_seconds)


def record_replay_attempt(domain: str, replay_type: str, success: bool) -> None:
    """Record a replay attempt."""
    get_metrics().record_replay_attempt(domain, replay_type, success)
