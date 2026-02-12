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
    ["slo_name", "is_synthetic", "region", "tier"],
)

error_budget_remaining_minutes = get_or_create_gauge(
    "error_budget_remaining_minutes",
    "Error budget remaining in minutes",
    ["slo_name", "is_synthetic", "region", "tier"],
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

# =============================================================================
# Adaptive Throttle Extended Metrics
# =============================================================================

# Request Metrics
throttle_requests_total = get_or_create_counter(
    "selfhealing_throttle_requests_total",
    "Total requests processed by throttle",
    ["service", "result"],
)

throttle_allowed_total = get_or_create_counter(
    "selfhealing_throttle_allowed_total",
    "Total requests allowed by throttle",
    ["service"],
)

# SLA Metrics
throttle_sla_warnings_total = get_or_create_counter(
    "selfhealing_throttle_sla_warnings_total",
    "Total SLA warning threshold breaches",
    ["service"],
)

throttle_sla_criticals_total = get_or_create_counter(
    "selfhealing_throttle_sla_criticals_total",
    "Total SLA critical threshold breaches",
    ["service"],
)

throttle_sla_breach_duration_seconds = get_or_create_histogram(
    "selfhealing_throttle_sla_breach_duration_seconds",
    "Duration of SLA breach periods",
    ["service", "severity"],
    buckets=(60, 300, 600, 1800, 3600),
)

# Emergency Metrics
throttle_emergency_level = get_or_create_gauge(
    "selfhealing_throttle_emergency_level",
    "Current emergency level (0-3)",
    ["service"],
)

throttle_gradient_frozen = get_or_create_gauge(
    "selfhealing_throttle_gradient_frozen",
    "Whether gradient adjustment is frozen (1=yes, 0=no)",
    ["service"],
)

# Recovery Metrics
throttle_recovery_dampening_active = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_active",
    "Whether recovery dampening is active (1=yes, 0=no)",
    ["service"],
)

throttle_recovery_dampening_step = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_step",
    "Current recovery dampening step (0=80%, 1=90%, 2=100%)",
    ["service"],
)

throttle_recovery_completed_total = get_or_create_counter(
    "selfhealing_throttle_recovery_completed_total",
    "Total recovery dampening completions",
    ["service"],
)

# Full Stop Metrics
throttle_full_stop_active = get_or_create_gauge(
    "selfhealing_throttle_full_stop_active",
    "Whether full stop is active (1=yes, 0=no)",
    ["service"],
)

throttle_full_stop_activations_total = get_or_create_counter(
    "selfhealing_throttle_full_stop_activations_total",
    "Total full stop activations",
    ["service", "reason"],
)

# Limit Change Metrics
throttle_limit_changes_total = get_or_create_counter(
    "selfhealing_throttle_limit_changes_total",
    "Total throttle limit changes",
    ["service", "direction", "trigger"],
)

throttle_limit_change_magnitude = get_or_create_histogram(
    "selfhealing_throttle_limit_change_magnitude",
    "Magnitude of limit changes (percentage)",
    ["service", "direction"],
    buckets=(5, 10, 20, 30, 50, 70, 100),
)

# Saturation Metrics
throttle_saturation_ratio = get_or_create_gauge(
    "selfhealing_throttle_saturation_ratio",
    "Throttle limit saturation (current_limit / max_limit), 0.0-1.0. " "Lower values mean more throttling is applied",
    ["service"],
)

throttle_max_limit = get_or_create_gauge(
    "selfhealing_throttle_max_limit",
    "Configured maximum throttle limit",
    ["service"],
)


# =============================================================================
# X-Test Regional Boundary Metrics
# =============================================================================

xtest_cross_region_denied_total = get_or_create_counter(
    "selfhealing_xtest_cross_region_denied_total",
    "Total cross-region X-Test requests denied (region mismatch)",
    ["current_region", "target_region"],
)

xtest_global_scope_requests_total = get_or_create_counter(
    "selfhealing_xtest_global_scope_requests_total",
    "Total GLOBAL scope X-Test API requests",
    ["endpoint_pattern", "region", "result"],
)


# =============================================================================
# Canary Governance Metrics
# =============================================================================

canary_governance_blocked_total = get_or_create_counter(
    "selfhealing_canary_governance_blocked_total",
    "Total canary promotions blocked by governance",
    ["block_reason", "region", "tier"],  # kill_switch, emergency_mode, error_budget
)

canary_pending_promotion_gauge = get_or_create_gauge(
    "selfhealing_canary_pending_promotion",
    "Number of canary rollouts pending promotion due to governance",
    ["reason"],  # error_budget, emergency, etc.
)

canary_governance_bypass_total = get_or_create_counter(
    "selfhealing_canary_governance_bypass_total",
    "Total governance bypasses (Break Glass usage)",
    ["requested_by"],
)


# =============================================================================
# Rate Limit Coordinator Metrics (429 통합 대응)
# =============================================================================

rate_limit_429_total = get_or_create_counter(
    "selfhealing_rate_limit_429_total",
    "Total 429 responses received from external APIs",
    ["key", "status_code"],
)

rate_limit_cooldown_seconds = get_or_create_histogram(
    "selfhealing_rate_limit_cooldown_seconds",
    "Cooldown duration after 429 response",
    ["key"],
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

rate_limit_consecutive_429s = get_or_create_gauge(
    "selfhealing_rate_limit_consecutive_429s",
    "Current consecutive 429 count per key",
    ["key"],
)

rate_limit_throttle_adjustments_total = get_or_create_counter(
    "selfhealing_rate_limit_throttle_adjustments_total",
    "Total throttle limit adjustments triggered by 429",
    ["key", "reduction_percent"],
)


# =============================================================================
# Error Budget - Throttle Integration Metrics
# =============================================================================

throttle_error_budget_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_error_budget_adjustments_total",
    "Total throttle limit adjustments triggered by error budget status changes",
    ["service", "budget_status"],
)

throttle_error_budget_multiplier = get_or_create_gauge(
    "selfhealing_throttle_error_budget_multiplier",
    "Current error budget multiplier applied to throttle limit (0.0-1.0)",
    ["service"],
)

throttle_error_budget_reduction_active = get_or_create_gauge(
    "selfhealing_throttle_error_budget_reduction_active",
    "Whether error budget limit reduction is active (1=yes, 0=no)",
    ["service"],
)

throttle_error_budget_preemptive_total = get_or_create_counter(
    "selfhealing_throttle_error_budget_preemptive_total",
    "Total preemptive throttle reductions based on budget depletion forecast",
    ["service", "risk_level"],
)


# =============================================================================
# Retry Backoff Throttle Integration Metrics
# =============================================================================

retry_backoff_multiplier = get_or_create_histogram(
    "selfhealing_retry_backoff_multiplier",
    "Backoff multiplier applied due to throttle state",
    ["domain", "reason"],
    buckets=(1.0, 1.5, 2.0, 2.5, 3.0, 4.0),
)

retry_throttle_full_stop_skips_total = get_or_create_counter(
    "selfhealing_retry_throttle_full_stop_skips_total",
    "Total retries skipped due to throttle full stop",
    ["domain"],
)

retry_backoff_original_seconds = get_or_create_histogram(
    "selfhealing_retry_backoff_original_seconds",
    "Original backoff delay before throttle multiplier",
    ["domain"],
    buckets=(1, 4, 16, 64, 180),
)

retry_backoff_adjusted_seconds = get_or_create_histogram(
    "selfhealing_retry_backoff_adjusted_seconds",
    "Adjusted backoff delay after throttle multiplier",
    ["domain"],
    buckets=(1, 4, 16, 64, 180, 360, 720),
)

retry_critical_tier_grace_retries_total = get_or_create_counter(
    "selfhealing_retry_critical_tier_grace_total",
    "Total CRITICAL tier grace retries during FULL_STOP",
    ["domain"],
)


# =============================================================================
# Throttle DLQ Replay Integration Metrics (거부 요청 DLQ 저장/Replay 연동)
# =============================================================================

throttle_rejection_dlq_stored_total = get_or_create_counter(
    "selfhealing_throttle_rejection_dlq_stored_total",
    "Total throttle rejections stored to DLQ",
    ["reason", "domain"],
)

throttle_recovery_replay_total = get_or_create_counter(
    "selfhealing_throttle_recovery_replay_total",
    "Total entries replayed on throttle recovery",
    ["domain", "result"],
)

throttle_replay_delay_seconds = get_or_create_histogram(
    "selfhealing_throttle_replay_delay_seconds",
    "Time between rejection and successful replay",
    ["domain"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

throttle_rejection_sampled_out_total = get_or_create_counter(
    "selfhealing_throttle_rejection_sampled_out_total",
    "Total throttle rejections filtered by sampling",
    ["tier_id", "reason"],
)

throttle_rejection_hedged_skipped_total = get_or_create_counter(
    "selfhealing_throttle_rejection_hedged_skipped_total",
    "Total hedged requests skipped from DLQ storage",
    ["domain"],
)

throttle_replay_ttl_expired_total = get_or_create_counter(
    "selfhealing_throttle_replay_ttl_expired_total",
    "Total entries skipped due to TTL expiry during replay",
    ["domain"],
)

throttle_replay_permanently_failed_total = get_or_create_counter(
    "selfhealing_throttle_replay_permanently_failed_total",
    "Total entries marked permanently_failed (max_retries exhausted)",
    ["domain"],
)

throttle_replay_adaptive_interval_ms = get_or_create_gauge(
    "selfhealing_throttle_replay_adaptive_interval_ms",
    "Current adaptive replay interval based on capacity ratio",
    ["service"],
)

throttle_dlq_fallback_total = get_or_create_counter(
    "selfhealing_throttle_dlq_fallback_total",
    "Total DLQ fallback writes by channel",
    ["channel"],
)
