"""
Metrics collection and export for the self-healing system.

This module provides Prometheus metrics, event handlers, and other
observability tools.
"""

from selfhealing.metrics.prometheus import (
    SelfHealingMetrics,
    get_metrics,
)
from selfhealing.metrics.event_handlers import (
    DLQMetricEventHandler,
    CircuitBreakerEventHandler,
    ReplayEventHandler,
    reset_event_handler_cache,
)
# Import from the refactored safe_gauge package
from selfhealing.metrics.safe_gauge import (
    SafeGauge,
    SafeGaugeChild,
    SyncStatus,
    SyncInfo,
    clamp_non_negative,
    clamp_percentage,
    safe_set_gauge,
    NoOpGaugeChild,
)
from selfhealing.metrics.decorators import (
    track_dlq_creation,
    track_dlq_resolution,
    track_replay,
    track_execution_time,
    track_counter,
)
# Import from new location to avoid DeprecationWarning internally
from selfhealing.utils.jitter import (
    with_jitter,
    calculate_jitter,
    sleep_with_jitter,
    JitterConfig,
)
from selfhealing.metrics.reconciler import (
    MetricReconciler,
    DriftSeverity,
    DriftResult,
    SyncResult,
    get_reconciler,
)
from selfhealing.metrics.reliability import (
    MetricReliability,
    get_metric_reliability,
)

__all__ = [
    # Prometheus metrics
    "SelfHealingMetrics",
    "get_metrics",
    # Event handlers
    "DLQMetricEventHandler",
    "CircuitBreakerEventHandler",
    "ReplayEventHandler",
    "reset_event_handler_cache",
    # Safe Gauge (core)
    "SafeGauge",
    "SafeGaugeChild",
    # Safe Gauge (sync)
    "SyncStatus",
    "SyncInfo",
    # Safe Gauge (clamping)
    "clamp_non_negative",
    "clamp_percentage",
    "safe_set_gauge",
    # Safe Gauge (noop)
    "NoOpGaugeChild",
    # Decorators
    "track_dlq_creation",
    "track_dlq_resolution",
    "track_replay",
    "track_execution_time",
    "track_counter",
    # Jitter
    "with_jitter",
    "calculate_jitter",
    "sleep_with_jitter",
    "JitterConfig",
    # Reconciler
    "MetricReconciler",
    "DriftSeverity",
    "DriftResult",
    "SyncResult",
    "get_reconciler",
    # Reliability
    "MetricReliability",
    "get_metric_reliability",
]
