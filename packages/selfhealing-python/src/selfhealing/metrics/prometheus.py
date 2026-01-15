"""
Prometheus metrics for the self-healing system.

This module provides Prometheus metric definitions and collection utilities.
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone
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

        # =============================================================================
        # RED Metrics (Rate, Errors, Duration)
        # =============================================================================
        # Reference: https://www.weave.works/blog/the-red-method-key-metrics-for-microservices/

        self.http_requests_total = Counter(
            f"{prefix}_http_requests_total",
            "Total HTTP requests (Rate)",
            ["method", "endpoint", "status_code"],
        )

        self.http_request_duration_seconds = Histogram(
            f"{prefix}_http_request_duration_seconds",
            "HTTP request duration in seconds (Duration)",
            ["method", "endpoint"],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )

        self.http_request_errors_total = Counter(
            f"{prefix}_http_request_errors_total",
            "Total HTTP request errors (Errors)",
            ["method", "endpoint", "error_type"],
        )

        # =============================================================================
        # Four Golden Signals
        # =============================================================================
        # Reference: https://sre.google/sre-book/monitoring-distributed-systems/
        # 1. Latency - covered by http_request_duration_seconds (with percentiles)
        # 2. Traffic - covered by http_requests_total
        # 3. Errors - covered by http_request_errors_total
        # 4. Saturation - queue depth, resource utilization

        # Saturation Metrics
        self.request_queue_depth = Gauge(
            f"{prefix}_request_queue_depth",
            "Current request queue depth (Saturation)",
            ["service"],
        )

        self.worker_utilization_ratio = Gauge(
            f"{prefix}_worker_utilization_ratio",
            "Worker pool utilization ratio 0.0-1.0 (Saturation)",
            ["pool_name"],
        )

        self.active_connections = Gauge(
            f"{prefix}_active_connections",
            "Number of active connections (Saturation)",
            ["connection_type"],
        )

        # Latency Percentile Support (Summary for p50/p90/p99)
        # Note: Histogram also provides percentiles via histogram_quantile() in PromQL
        # This is an additional explicit percentile gauge for real-time dashboards
        self.request_latency_percentiles = Gauge(
            f"{prefix}_request_latency_percentile_seconds",
            "Request latency percentiles (Latency)",
            ["percentile", "endpoint"],
        )

        # Error Rate Gauge (calculated metric for alerting)
        self.error_rate_percent = Gauge(
            f"{prefix}_error_rate_percent",
            "Current error rate percentage (Errors)",
            ["service"],
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
    # RED Metrics Recording Methods
    # =========================================================================

    def record_http_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """
        Record an HTTP request (RED metrics: Rate + Duration).

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: Request endpoint path
            status_code: HTTP response status code
            duration_seconds: Request duration in seconds
        """
        if not self._initialized:
            return
        try:
            # Rate: increment request counter
            self.http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()

            # Duration: observe request latency
            self.http_request_duration_seconds.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration_seconds)

            logger.debug(
                f"[Metrics] HTTP request: {method} {endpoint} "
                f"status={status_code} duration={duration_seconds:.3f}s"
            )
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record HTTP request: {e}")

    def record_http_error(
        self,
        method: str,
        endpoint: str,
        error_type: str,
    ) -> None:
        """
        Record an HTTP request error (RED metrics: Errors).

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: Request endpoint path
            error_type: Type of error (e.g., "timeout", "connection_error", "500")
        """
        if not self._initialized:
            return
        try:
            self.http_request_errors_total.labels(
                method=method,
                endpoint=endpoint,
                error_type=error_type,
            ).inc()
            logger.debug(f"[Metrics] HTTP error: {method} {endpoint} error={error_type}")
        except Exception as e:
            logger.warning(f"[Metrics] Failed to record HTTP error: {e}")

    # =========================================================================
    # Four Golden Signals Recording Methods
    # =========================================================================

    def set_request_queue_depth(self, service: str, depth: int) -> None:
        """
        Set current request queue depth (Saturation signal).

        Args:
            service: Service name
            depth: Current queue depth
        """
        if not self._initialized:
            return
        try:
            safe_depth = clamp_non_negative(depth, f"request_queue_depth[{service}]")
            self.request_queue_depth.labels(service=service).set(safe_depth)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set queue depth: {e}")

    def set_worker_utilization(self, pool_name: str, ratio: float) -> None:
        """
        Set worker pool utilization ratio (Saturation signal).

        Args:
            pool_name: Worker pool name
            ratio: Utilization ratio (0.0 to 1.0)
        """
        if not self._initialized:
            return
        try:
            # Clamp to 0.0-1.0 range
            safe_ratio = max(0.0, min(1.0, ratio))
            if ratio < 0.0 or ratio > 1.0:
                logger.warning(
                    f"[Metrics] worker_utilization_ratio[{pool_name}] clamped: "
                    f"{ratio} -> {safe_ratio}"
                )
            self.worker_utilization_ratio.labels(pool_name=pool_name).set(safe_ratio)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set worker utilization: {e}")

    def set_active_connections(self, connection_type: str, count: int) -> None:
        """
        Set number of active connections (Saturation signal).

        Args:
            connection_type: Type of connection (e.g., "db", "redis", "http")
            count: Number of active connections
        """
        if not self._initialized:
            return
        try:
            safe_count = clamp_non_negative(count, f"active_connections[{connection_type}]")
            self.active_connections.labels(connection_type=connection_type).set(safe_count)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set active connections: {e}")

    def set_latency_percentile(
        self,
        endpoint: str,
        percentile: str,
        value_seconds: float,
    ) -> None:
        """
        Set request latency percentile (Latency signal).

        Args:
            endpoint: Endpoint path
            percentile: Percentile label (e.g., "p50", "p90", "p99")
            value_seconds: Latency value in seconds
        """
        if not self._initialized:
            return
        try:
            safe_value = max(0.0, value_seconds)
            self.request_latency_percentiles.labels(
                percentile=percentile,
                endpoint=endpoint,
            ).set(safe_value)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set latency percentile: {e}")

    def set_error_rate(self, service: str, rate_percent: float) -> None:
        """
        Set current error rate percentage (Errors signal).

        Args:
            service: Service name
            rate_percent: Error rate as percentage (0-100)
        """
        if not self._initialized:
            return
        try:
            safe_rate = clamp_percentage(rate_percent, f"error_rate_percent[{service}]")
            self.error_rate_percent.labels(service=service).set(safe_rate)
        except Exception as e:
            logger.warning(f"[Metrics] Failed to set error rate: {e}")

    @contextmanager
    def http_request_timer(self, method: str, endpoint: str):
        """
        Context manager for timing HTTP requests.

        Usage:
            with metrics.http_request_timer("GET", "/api/users"):
                response = make_request()
            # duration automatically recorded

        Args:
            method: HTTP method
            endpoint: Request endpoint
        """
        start_time = datetime.now(timezone.utc)
        status_code = 200
        error_occurred = False
        error_type = None

        try:
            yield
        except Exception as e:
            error_occurred = True
            error_type = type(e).__name__
            raise
        finally:
            if self._initialized:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                # Record duration
                self.http_request_duration_seconds.labels(
                    method=method,
                    endpoint=endpoint,
                ).observe(duration)

                if error_occurred and error_type:
                    self.record_http_error(method, endpoint, error_type)

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
        start_time = datetime.now(timezone.utc)
        try:
            yield
        finally:
            if self._initialized:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
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


# =============================================================================
# RED Metrics Convenience Functions
# =============================================================================


def record_http_request(
    method: str,
    endpoint: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record an HTTP request (Rate + Duration)."""
    get_metrics().record_http_request(method, endpoint, status_code, duration_seconds)


def record_http_error(method: str, endpoint: str, error_type: str) -> None:
    """Record an HTTP request error (Errors)."""
    get_metrics().record_http_error(method, endpoint, error_type)


# =============================================================================
# Four Golden Signals Convenience Functions
# =============================================================================


def set_request_queue_depth(service: str, depth: int) -> None:
    """Set current request queue depth (Saturation)."""
    get_metrics().set_request_queue_depth(service, depth)


def set_worker_utilization(pool_name: str, ratio: float) -> None:
    """Set worker pool utilization ratio (Saturation)."""
    get_metrics().set_worker_utilization(pool_name, ratio)


def set_active_connections(connection_type: str, count: int) -> None:
    """Set number of active connections (Saturation)."""
    get_metrics().set_active_connections(connection_type, count)


def set_latency_percentile(endpoint: str, percentile: str, value_seconds: float) -> None:
    """Set request latency percentile (Latency)."""
    get_metrics().set_latency_percentile(endpoint, percentile, value_seconds)


def set_error_rate(service: str, rate_percent: float) -> None:
    """Set current error rate percentage (Errors)."""
    get_metrics().set_error_rate(service, rate_percent)
