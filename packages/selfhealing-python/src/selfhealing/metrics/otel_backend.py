"""
OTEL Meter-based metrics backend for SelfHealingMetrics.

Replaces direct prometheus_client usage with OpenTelemetry Meter API.
PrometheusMetricReader (initialized in observability/__init__.py) bridges
OTEL instruments to prometheus_client REGISTRY for /metrics exposition.

This resolves Prometheus multiprocess metrics fragmentation (Section 5.4)
by delegating aggregation to the OTEL SDK / OTEL Collector.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import structlog

from selfhealing.metrics.safe_gauge import clamp_non_negative, clamp_percentage

logger = structlog.get_logger()


class _GaugeStore:
    """Thread-safe value store for ObservableGauge callbacks.

    OTEL doesn't have a synchronous Gauge with .set(). Instead, we store
    values and expose them via ObservableGauge callbacks.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._values: dict[tuple, float] = {}

    def set(self, value: float, attributes: dict | None = None) -> None:
        key = tuple(sorted((attributes or {}).items()))
        with self._lock:
            self._values[key] = value

    def callback(self, options):
        from opentelemetry.metrics import Observation

        with self._lock:
            results = []
            for attr_key, value in self._values.items():
                attrs = dict(attr_key)
                results.append(Observation(value, attrs))
            return results


class OTELSelfHealingMetrics:
    """OTEL Meter-based implementation of SelfHealingMetrics.

    Provides the same public API as SelfHealingMetrics (prometheus.py)
    but uses OTEL instruments internally. PrometheusMetricReader converts
    them to Prometheus text format for /metrics endpoint.
    """

    def __init__(self, prefix: str = "selfhealing"):
        self.prefix = prefix
        self._initialized = False
        self._gauge_stores: dict[str, _GaugeStore] = {}

        try:
            from selfhealing.observability import get_meter

            meter = get_meter()
            if meter is None:
                logger.warning("otel_metrics.meter_not_available")
                return

            self._create_instruments(meter, prefix)
            self._initialized = True

        except Exception as e:
            logger.warning("otel_metrics.initialization_failed", error=e)

    def _gauge_store(self, name: str) -> _GaugeStore:
        """Get or create a GaugeStore for an ObservableGauge."""
        if name not in self._gauge_stores:
            self._gauge_stores[name] = _GaugeStore()
        return self._gauge_stores[name]

    def _create_instruments(self, meter, prefix: str) -> None:
        # =====================================================================
        # DLQ Metrics
        # =====================================================================
        self.dlq_items_total = meter.create_counter(
            f"{prefix}_dlq_items_total",
            description="Total DLQ items created",
        )
        self.dlq_created_total = meter.create_counter(
            f"{prefix}_dlq_created_total",
            description="Total DLQ items created (for rate calculation)",
        )

        self._dlq_pending_store = self._gauge_store("dlq_pending")
        meter.create_observable_gauge(
            f"{prefix}_dlq_pending_count",
            callbacks=[self._dlq_pending_store.callback],
            description="Current pending DLQ items",
        )

        self._dlq_status_store = self._gauge_store("dlq_status")
        meter.create_observable_gauge(
            f"{prefix}_dlq_items_by_status",
            callbacks=[self._dlq_status_store.callback],
            description="DLQ items count by status",
        )

        # =====================================================================
        # Retry Metrics
        # =====================================================================
        self.retry_attempts_histogram = meter.create_histogram(
            f"{prefix}_retry_attempts_distribution",
            description="Number of retry attempts before resolution",
        )
        self.retry_outcomes_total = meter.create_counter(
            f"{prefix}_retry_outcomes_total",
            description="Retry outcomes by domain and result",
        )

        self._retry_success_store = self._gauge_store("retry_success")
        meter.create_observable_gauge(
            f"{prefix}_retry_success_rate",
            callbacks=[self._retry_success_store.callback],
            description="Percentage of successful retries (0-100)",
        )

        self.retry_delay_seconds = meter.create_histogram(
            f"{prefix}_retry_delay_seconds",
            description="Retry delay in seconds",
        )

        # =====================================================================
        # Recovery Metrics
        # =====================================================================
        self.recovery_time_seconds = meter.create_histogram(
            f"{prefix}_recovery_time_seconds",
            description="Time from failure to resolution in seconds",
        )
        self.sla_breach_total = meter.create_counter(
            f"{prefix}_sla_breach_total",
            description="Total SLA breaches detected",
        )
        self.human_review_queue_time = meter.create_histogram(
            f"{prefix}_human_review_queue_time_seconds",
            description="Time items wait in queue for human review",
        )

        # =====================================================================
        # Circuit Breaker Metrics
        # =====================================================================
        self._cb_state_store = self._gauge_store("cb_state")
        meter.create_observable_gauge(
            f"{prefix}_circuit_breaker_state",
            callbacks=[self._cb_state_store.callback],
            description="Circuit breaker state (0=closed, 1=open, 2=half_open)",
        )

        self.circuit_breaker_failures = meter.create_counter(
            f"{prefix}_circuit_breaker_failures_total",
            description="Total circuit breaker failures",
        )
        self.circuit_breaker_trips = meter.create_counter(
            f"{prefix}_circuit_breaker_trips_total",
            description="Total times circuit breaker tripped to open",
        )
        self.circuit_breaker_transitions = meter.create_counter(
            f"{prefix}_circuit_breaker_transitions_total",
            description="Total circuit breaker state transitions",
        )
        self.circuit_breaker_open_duration = meter.create_histogram(
            f"{prefix}_circuit_breaker_open_duration_seconds",
            description="Duration in open state before closing",
        )

        # =====================================================================
        # Circuit Mesh Coordinator Metrics
        # =====================================================================
        self._mesh_overrides_store = self._gauge_store("mesh_overrides")
        meter.create_observable_gauge(
            f"{prefix}_mesh_overrides_active",
            callbacks=[self._mesh_overrides_store.callback],
            description="Current active mesh threshold overrides",
        )

        self.mesh_override_applied_total = meter.create_counter(
            f"{prefix}_mesh_override_applied_total",
            description="Total mesh threshold overrides applied",
        )
        self.mesh_override_released_total = meter.create_counter(
            f"{prefix}_mesh_override_released_total",
            description="Total mesh threshold overrides released",
        )
        self.mesh_override_expired_total = meter.create_counter(
            f"{prefix}_mesh_override_expired_total",
            description="Total mesh threshold overrides expired by TTL",
        )
        self.mesh_override_renewed_total = meter.create_counter(
            f"{prefix}_mesh_override_renewed_total",
            description="Total mesh threshold override TTL renewals",
        )
        self.mesh_preemptive_fallback_total = meter.create_counter(
            f"{prefix}_mesh_preemptive_fallback_total",
            description="Total preemptive fallback activations",
        )
        self.mesh_fast_recovery_total = meter.create_counter(
            f"{prefix}_mesh_fast_recovery_total",
            description="Total fast-recovery overrides applied",
        )
        self.mesh_escalation_total = meter.create_counter(
            f"{prefix}_mesh_escalation_total",
            description="Total escalations to EmergencyCoordinator",
        )
        self.mesh_circular_dependency_detected_total = meter.create_counter(
            f"{prefix}_mesh_circular_dependency_detected_total",
            description="Total circular dependency detections in mesh",
        )
        self.mesh_override_store_drift_total = meter.create_counter(
            f"{prefix}_mesh_override_store_drift_total",
            description="Total L1-L2 drift detections in mesh override store",
        )
        self.mesh_recovery_duration_seconds = meter.create_histogram(
            f"{prefix}_mesh_recovery_duration_seconds",
            description="Duration from downstream CB OPEN to CLOSED recovery",
        )

        # =====================================================================
        # DI Fallback Metrics
        # =====================================================================
        self.di_fallback_total = meter.create_counter(
            f"{prefix}_di_fallback_total",
            description="DI fallback to in-memory adapter",
        )

        # =====================================================================
        # Replay Metrics
        # =====================================================================
        self.replay_attempts_total = meter.create_counter(
            f"{prefix}_replay_attempts_total",
            description="Total replay attempts",
        )
        self.replay_outcomes_total = meter.create_counter(
            f"{prefix}_replay_outcomes_total",
            description="Replay outcomes",
        )
        self.replay_duration_seconds = meter.create_histogram(
            f"{prefix}_replay_duration_seconds",
            description="Replay operation duration",
        )

        # =====================================================================
        # Security Metrics
        # =====================================================================
        self.security_incidents = meter.create_counter(
            f"{prefix}_security_incidents_total",
            description="Total security incidents",
        )

        # =====================================================================
        # RED Metrics
        # =====================================================================
        self.http_requests_total = meter.create_counter(
            f"{prefix}_http_requests_total",
            description="Total HTTP requests (Rate)",
        )
        self.http_request_duration_seconds = meter.create_histogram(
            f"{prefix}_http_request_duration_seconds",
            description="HTTP request duration in seconds (Duration)",
        )
        self.http_request_errors_total = meter.create_counter(
            f"{prefix}_http_request_errors_total",
            description="Total HTTP request errors (Errors)",
        )

        # =====================================================================
        # Four Golden Signals — Saturation
        # =====================================================================
        self._queue_depth_store = self._gauge_store("queue_depth")
        meter.create_observable_gauge(
            f"{prefix}_request_queue_depth",
            callbacks=[self._queue_depth_store.callback],
            description="Current request queue depth (Saturation)",
        )

        self._worker_util_store = self._gauge_store("worker_util")
        meter.create_observable_gauge(
            f"{prefix}_worker_utilization_ratio",
            callbacks=[self._worker_util_store.callback],
            description="Worker pool utilization ratio 0.0-1.0 (Saturation)",
        )

        self._active_conn_store = self._gauge_store("active_conn")
        meter.create_observable_gauge(
            f"{prefix}_active_connections",
            callbacks=[self._active_conn_store.callback],
            description="Number of active connections (Saturation)",
        )

        self._latency_pct_store = self._gauge_store("latency_pct")
        meter.create_observable_gauge(
            f"{prefix}_request_latency_percentile_seconds",
            callbacks=[self._latency_pct_store.callback],
            description="Request latency percentiles (Latency)",
        )

        self._error_rate_store = self._gauge_store("error_rate")
        meter.create_observable_gauge(
            f"{prefix}_error_rate_percent",
            callbacks=[self._error_rate_store.callback],
            description="Current error rate percentage (Errors)",
        )

        # =====================================================================
        # Capacity Reservation Metrics
        # =====================================================================
        self.capacity_warmup_total = meter.create_counter(
            f"{prefix}_capacity_warmup_total",
            description="Total warm-up executions",
        )
        self.capacity_warmup_duration_seconds = meter.create_histogram(
            f"{prefix}_capacity_warmup_duration_seconds",
            description="Warm-up execution duration in seconds",
        )
        self.capacity_cooldown_total = meter.create_counter(
            f"{prefix}_capacity_cooldown_total",
            description="Total cool-down executions",
        )

        self._capacity_events_store = self._gauge_store("capacity_events")
        meter.create_observable_gauge(
            f"{prefix}_capacity_active_events",
            callbacks=[self._capacity_events_store.callback],
            description="Currently active scheduled events",
        )

        self._capacity_rate_store = self._gauge_store("capacity_rate")
        meter.create_observable_gauge(
            f"{prefix}_capacity_rate_multiplier",
            callbacks=[self._capacity_rate_store.callback],
            description="Currently applied rate multiplier",
        )

        self._capacity_pool_store = self._gauge_store("capacity_pool")
        meter.create_observable_gauge(
            f"{prefix}_capacity_pool_multiplier",
            callbacks=[self._capacity_pool_store.callback],
            description="Currently applied pool multiplier",
        )

    # =========================================================================
    # DLQ Recording Methods
    # =========================================================================

    def record_dlq_item_created(self, domain: str, failure_type: str) -> None:
        if not self._initialized:
            return
        try:
            self.dlq_items_total.add(
                1, {"domain": domain, "failure_type": failure_type}
            )
            self.dlq_created_total.add(1, {"domain": domain})
        except Exception as e:
            logger.warning("metrics.failed_record_dlq_creation", error=e)

    def set_dlq_pending_count(self, domain: str, count: int) -> None:
        if not self._initialized:
            return
        safe_count = clamp_non_negative(count, f"dlq_pending_count[{domain}]")
        self._dlq_pending_store.set(safe_count, {"domain": domain})

    def set_dlq_status_count(self, status: str, count: int) -> None:
        if not self._initialized:
            return
        safe_count = clamp_non_negative(count, f"dlq_status_count[{status}]")
        self._dlq_status_store.set(safe_count, {"status": status})

    # =========================================================================
    # Retry Recording Methods
    # =========================================================================

    def record_retry_attempt(
        self, domain: str, attempt_count: int, outcome: str
    ) -> None:
        if not self._initialized:
            return
        try:
            self.retry_attempts_histogram.record(attempt_count, {"domain": domain})
            self.retry_outcomes_total.add(1, {"domain": domain, "outcome": outcome})
        except Exception as e:
            logger.warning("metrics.failed_record_retry_metric", error=e)

    def record_retry(
        self, domain: str, success: bool, delay: float | None = None
    ) -> None:
        if not self._initialized:
            return
        try:
            outcome = "success" if success else "failure"
            self.retry_outcomes_total.add(1, {"domain": domain, "outcome": outcome})
            if delay is not None:
                self.retry_delay_seconds.record(delay, {"domain": domain})
        except Exception as e:
            logger.warning("metrics.failed_record_retry_metric", error=e)

    def set_retry_success_rate(self, domain: str, rate: float) -> None:
        if not self._initialized:
            return
        safe_rate = clamp_percentage(rate, f"retry_success_rate[{domain}]")
        self._retry_success_store.set(safe_rate, {"domain": domain})

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
        if not self._initialized:
            return
        try:
            duration = (resolved_at - created_at).total_seconds()
            self.recovery_time_seconds.record(
                duration, {"domain": domain, "resolution_type": resolution_type}
            )
        except Exception as e:
            logger.warning("metrics.failed_record_recovery_time", error=e)

    def record_sla_breach(self, domain: str) -> None:
        if not self._initialized:
            return
        try:
            self.sla_breach_total.add(1, {"domain": domain})
        except Exception as e:
            logger.warning("metrics.failed_record_sla_breach", error=e)

    # =========================================================================
    # Circuit Breaker Recording Methods
    # =========================================================================

    def set_circuit_state(
        self, service_name: str, state: str, cell_id: str = ""
    ) -> None:
        if not self._initialized:
            return
        state_map = {"closed": 0, "open": 1, "half_open": 2}
        value = state_map.get(state, 0)
        self._cb_state_store.set(
            value, {"service_name": service_name, "cell_id": cell_id}
        )

    def record_circuit_failure(self, service_name: str) -> None:
        if not self._initialized:
            return
        try:
            self.circuit_breaker_failures.add(1, {"service_name": service_name})
        except Exception as e:
            logger.warning("metrics.failed_record_circuit_failure", error=e)

    def record_circuit_trip(self, service_name: str) -> None:
        if not self._initialized:
            return
        try:
            self.circuit_breaker_trips.add(1, {"service_name": service_name})
        except Exception as e:
            logger.warning("metrics.failed_record_circuit_trip", error=e)

    def record_circuit_transition(
        self,
        service_name: str,
        from_state: str,
        to_state: str,
        cell_id: str = "",
    ) -> None:
        if not self._initialized:
            return
        try:
            self.circuit_breaker_transitions.add(
                1,
                {
                    "service_name": service_name,
                    "cell_id": cell_id,
                    "from_state": from_state,
                    "to_state": to_state,
                },
            )
        except Exception as e:
            logger.warning("metrics.failed_record_circuit_transition", error=e)

    def record_circuit_open_duration(self, service_name: str, duration: float) -> None:
        if not self._initialized:
            return
        try:
            self.circuit_breaker_open_duration.record(
                duration, {"service_name": service_name}
            )
        except Exception as e:
            logger.warning("metrics.failed_record_circuit_open", error=e)

    # =========================================================================
    # Mesh Coordinator
    # =========================================================================

    def set_mesh_overrides_active(self, count: int) -> None:
        if not self._initialized:
            return
        self._mesh_overrides_store.set(count)

    def record_mesh_override_applied(self) -> None:
        if not self._initialized:
            return
        self.mesh_override_applied_total.add(1)

    def record_mesh_override_released(self) -> None:
        if not self._initialized:
            return
        self.mesh_override_released_total.add(1)

    def record_mesh_override_expired(self) -> None:
        if not self._initialized:
            return
        self.mesh_override_expired_total.add(1)

    def record_mesh_override_renewed(self) -> None:
        if not self._initialized:
            return
        self.mesh_override_renewed_total.add(1)

    # =========================================================================
    # DI Fallback
    # =========================================================================

    def record_di_fallback(self, service: str, adapter: str) -> None:
        if not self._initialized:
            return
        self.di_fallback_total.add(1, {"service": service, "adapter": adapter})

    # =========================================================================
    # Replay
    # =========================================================================

    def record_replay_attempt(self, domain: str, replay_type: str = "manual") -> None:
        if not self._initialized:
            return
        self.replay_attempts_total.add(
            1, {"domain": domain, "replay_type": replay_type}
        )

    def record_replay_outcome(self, domain: str, outcome: str) -> None:
        if not self._initialized:
            return
        self.replay_outcomes_total.add(1, {"domain": domain, "outcome": outcome})

    # =========================================================================
    # Security
    # =========================================================================

    def record_security_incident(self, incident_type: str, severity: str) -> None:
        if not self._initialized:
            return
        self.security_incidents.add(
            1, {"incident_type": incident_type, "severity": severity}
        )

    # =========================================================================
    # RED Metrics
    # =========================================================================

    def record_http_request(self, method: str, endpoint: str, status_code: int) -> None:
        if not self._initialized:
            return
        self.http_requests_total.add(
            1,
            {"method": method, "endpoint": endpoint, "status_code": str(status_code)},
        )

    def record_http_duration(self, method: str, endpoint: str, duration: float) -> None:
        if not self._initialized:
            return
        self.http_request_duration_seconds.record(
            duration, {"method": method, "endpoint": endpoint}
        )

    def record_http_error(self, method: str, endpoint: str, error_type: str) -> None:
        if not self._initialized:
            return
        self.http_request_errors_total.add(
            1, {"method": method, "endpoint": endpoint, "error_type": error_type}
        )

    @contextmanager
    def track_http_request(self, method: str, endpoint: str):
        start_time = datetime.now(timezone.utc)
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
                self.record_http_duration(method, endpoint, duration)
                if error_occurred and error_type:
                    self.record_http_error(method, endpoint, error_type)

    # =========================================================================
    # Saturation / Golden Signals
    # =========================================================================

    def set_queue_depth(self, service: str, depth: int) -> None:
        if not self._initialized:
            return
        self._queue_depth_store.set(depth, {"service": service})

    def set_worker_utilization(self, pool_name: str, ratio: float) -> None:
        if not self._initialized:
            return
        self._worker_util_store.set(ratio, {"pool_name": pool_name})

    def set_active_connections(self, connection_type: str, count: int) -> None:
        if not self._initialized:
            return
        self._active_conn_store.set(count, {"connection_type": connection_type})

    def set_latency_percentile(
        self, percentile: str, endpoint: str, value: float
    ) -> None:
        if not self._initialized:
            return
        self._latency_pct_store.set(
            value, {"percentile": percentile, "endpoint": endpoint}
        )

    def set_error_rate(self, service: str, rate: float) -> None:
        if not self._initialized:
            return
        safe_rate = clamp_percentage(rate, f"error_rate[{service}]")
        self._error_rate_store.set(safe_rate, {"service": service})

    def set_info(self, info_dict: dict[str, str]) -> None:
        pass

    # =========================================================================
    # Capacity
    # =========================================================================

    def record_capacity_warmup(self, event_id: str, outcome: str) -> None:
        if not self._initialized:
            return
        self.capacity_warmup_total.add(1, {"event_id": event_id, "outcome": outcome})

    def record_capacity_cooldown(self, event_id: str, outcome: str) -> None:
        if not self._initialized:
            return
        self.capacity_cooldown_total.add(1, {"event_id": event_id, "outcome": outcome})

    def set_capacity_active_events(self, count: int) -> None:
        if not self._initialized:
            return
        self._capacity_events_store.set(count)

    def set_capacity_rate_multiplier(self, value: float) -> None:
        if not self._initialized:
            return
        self._capacity_rate_store.set(value)

    def set_capacity_pool_multiplier(self, value: float) -> None:
        if not self._initialized:
            return
        self._capacity_pool_store.set(value)

    # =========================================================================
    # Timer
    # =========================================================================

    @contextmanager
    def timer(self, domain: str, metric_type: str = "replay"):
        start_time = datetime.now(timezone.utc)
        try:
            yield
        finally:
            if self._initialized:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                if metric_type == "replay":
                    self.replay_duration_seconds.record(duration, {"domain": domain})
