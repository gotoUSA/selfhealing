"""
Prometheus Metric Definitions.

All metric definitions (Counter, Gauge, Histogram) for the self-healing system.
Metrics are organized by category for clarity.
"""

from __future__ import annotations

from .registry import (
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
)

# =============================================================================
# DLQ Metrics
# =============================================================================

dlq_items_total = get_or_create_counter(
    "dlq_items_total",
    "Total DLQ items created",
    ["domain", "failure_type", "is_synthetic"],
)

dlq_pending_gauge = get_or_create_gauge(
    "dlq_pending_count",
    "Current pending DLQ items",
    ["domain"],
)

dlq_by_status_gauge = get_or_create_gauge(
    "dlq_items_by_status",
    "DLQ items count by status",
    ["status"],
)

dlq_created_total = get_or_create_counter(
    "dlq_created_total",
    "Total DLQ items created (for rate calculation)",
    ["domain"],
)


# =============================================================================
# Retry Metrics
# =============================================================================

retry_attempts_histogram = get_or_create_histogram(
    "retry_attempts_total",
    "Number of retry attempts before resolution",
    ["domain", "is_synthetic"],
    buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
)

retry_outcomes_total = get_or_create_counter(
    "retry_outcomes_total",
    "Retry outcomes by domain and result",
    ["domain", "outcome", "is_synthetic"],
)

retry_success_rate = get_or_create_gauge(
    "retry_success_rate",
    "Percentage of successful retries (0-100)",
    ["domain"],
)


# =============================================================================
# Recovery Metrics
# =============================================================================

recovery_time_seconds = get_or_create_histogram(
    "recovery_time_seconds",
    "Time from failure to resolution in seconds",
    ["domain", "resolution_type"],
    buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400),
)

sla_breach_total = get_or_create_counter(
    "sla_breach_total",
    "Total SLA breaches detected",
    ["domain"],
)

human_review_queue_time = get_or_create_histogram(
    "human_review_queue_time_seconds",
    "Time items wait in queue for human review",
    ["domain"],
    buckets=(300, 900, 1800, 3600, 7200, 14400, 28800),
)


# =============================================================================
# Circuit Breaker Metrics
# =============================================================================

circuit_breaker_state = get_or_create_gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["service"],
)

circuit_breaker_transitions = get_or_create_counter(
    "circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    ["service", "from_state", "to_state", "is_synthetic"],
)

circuit_breaker_open_duration = get_or_create_histogram(
    "circuit_breaker_open_duration_seconds",
    "Duration in open state before closing",
    ["service"],
    buckets=(60, 300, 600, 1800, 3600, 7200),
)


# =============================================================================
# L2 Storage Resilience Metrics
# =============================================================================

l2_timeout_total = get_or_create_counter(
    "selfhealing_l2_timeout_total",
    "Total L2 storage timeout occurrences",
    ["adapter_type", "operation"],
)

l2_sync_failure_total = get_or_create_counter(
    "selfhealing_l2_sync_failure_total",
    "Total L2 storage sync failures",
    ["adapter_type", "operation"],
)

l2_latency_seconds = get_or_create_histogram(
    "selfhealing_l2_latency_seconds",
    "L2 storage operation latency in seconds",
    ["adapter_type"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

l2_connection_status = get_or_create_gauge(
    "selfhealing_l2_connection_status",
    "L2 storage connection status (1=healthy, 0=unhealthy)",
    ["adapter_type"],
)

shadow_log_unsynced_count = get_or_create_gauge(
    "selfhealing_shadow_log_unsynced_count",
    "Number of unsynced shadow log entries",
    [],
)

drift_reconciliation_total = get_or_create_counter(
    "selfhealing_drift_reconciliation_total",
    "Total drift reconciliation operations",
    ["result"],
)


# =============================================================================
# Replay Metrics
# =============================================================================

replay_attempts_total = get_or_create_counter(
    "replay_attempts_total",
    "Total replay attempts",
    ["domain", "replay_type", "is_synthetic"],
)

replay_outcomes_total = get_or_create_counter(
    "replay_outcomes_total",
    "Replay outcomes",
    ["domain", "outcome", "is_synthetic"],
)


# =============================================================================
# Error Budget Metrics
# =============================================================================

error_budget_remaining_percent = get_or_create_gauge(
    "error_budget_remaining_percent",
    "Error budget remaining as percentage (0-100)",
    ["slo_name", "is_synthetic"],
)

error_budget_remaining_minutes = get_or_create_gauge(
    "error_budget_remaining_minutes",
    "Error budget remaining in minutes",
    ["slo_name", "is_synthetic"],
)

burn_rate_1h = get_or_create_gauge(
    "error_budget_burn_rate_1h",
    "Error budget burn rate over 1 hour window",
    ["slo_name"],
)

burn_rate_6h = get_or_create_gauge(
    "error_budget_burn_rate_6h",
    "Error budget burn rate over 6 hour window",
    ["slo_name"],
)

deployment_freeze_status = get_or_create_gauge(
    "deployment_freeze_status",
    "Deployment freeze status (0=proceed, 1=caution, 2=warning, 3=freeze_recommended)",
    [],
)

freeze_decision_total = get_or_create_counter(
    "freeze_decision_total",
    "Total freeze-related decisions",
    ["decision_type"],
)

active_override_gauge = get_or_create_gauge(
    "deployment_active_override",
    "Whether there is an active deployment override (0=no, 1=yes)",
    [],
)


# =============================================================================
# Heartbeat Metrics (Dead Man's Snitch)
# =============================================================================

selfhealing_heartbeat_timestamp = get_or_create_gauge(
    "selfhealing_heartbeat_timestamp_seconds",
    "Last heartbeat timestamp in seconds since epoch",
    ["component"],
)

selfhealing_heartbeat_count = get_or_create_counter(
    "selfhealing_heartbeat_total",
    "Total heartbeat emissions",
    ["component"],
)

override_escalation_total = get_or_create_counter(
    "selfhealing_override_escalation_total",
    "Total override escalation alerts sent",
    ["override_type"],
)

recovery_alert_total = get_or_create_counter(
    "selfhealing_recovery_alert_total",
    "Total recovery alerts sent",
    ["component"],
)


# =============================================================================
# Fail-Safe Metrics
# =============================================================================

failsafe_triggered_total = get_or_create_counter(
    "selfhealing_failsafe_triggered_total",
    "Number of times fail-safe mode was activated",
    ["component"],
)

failsafe_mode_active = get_or_create_gauge(
    "selfhealing_failsafe_mode_active",
    "Whether fail-safe mode is currently active (1=yes, 0=no)",
    ["component"],
)

# =============================================================================
# Adaptive Throttle Metrics
# =============================================================================

throttle_current_limit = get_or_create_gauge(
    "selfhealing_throttle_limit",
    "Current throttle limit value",
    ["service"],
)

throttle_rtt_ms = get_or_create_histogram(
    "selfhealing_throttle_rtt_ms",
    "Response time (RTT) in milliseconds",
    ["service"],
    buckets=(10, 25, 50, 100, 200, 500, 1000, 2000, 5000),
)

throttle_gradient = get_or_create_gauge(
    "selfhealing_throttle_gradient",
    "Current RTT gradient (positive=slowing, negative=improving)",
    ["service"],
)

throttle_denied_total = get_or_create_counter(
    "selfhealing_throttle_denied_total",
    "Total requests denied by throttle",
    ["service", "reason"],
)

throttle_emergency_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_emergency_adjustments_total",
    "Total throttle limit adjustments due to emergency mode",
    ["level"],
)

throttle_cb_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_cb_adjustments_total",
    "Total throttle limit adjustments due to circuit breaker state",
    ["service", "cb_state"],
)
