"""
Self-Healing Observability Metrics.

Prometheus metrics for monitoring the L3 Self-Healing layer.
Provides comprehensive visibility into DLQ, retry, recovery, and circuit breaker operations.

This package has been refactored from a single 1,247-line file into:
- registry.py: Metric registration helpers and domain registry
- definitions.py: All metric definitions (Counter, Gauge, Histogram)
- recorders.py: record_* functions for event recording
- updaters.py: update_* functions, context managers, decorators
- alerting_rules.py: Prometheus alerting rule definitions

All exports are maintained for backward compatibility.

DLQ, 재시도, 복구, Circuit Breaker 운영에 대한 포괄적인 관측성을 제공합니다.
"""

from __future__ import annotations

# Alerting Rules
from .alerting_rules import ALERTING_RULES

# Metric Definitions
from .definitions import (  # DLQ; Retry; Recovery; Circuit Breaker; L2 Storage; Replay; Error Budget; Heartbeat; Fail-Safe
    active_override_gauge,
    burn_rate_1h,
    burn_rate_6h,
    circuit_breaker_open_duration,
    circuit_breaker_state,
    circuit_breaker_transitions,
    deployment_freeze_status,
    dlq_by_status_gauge,
    dlq_created_total,
    dlq_items_total,
    dlq_pending_gauge,
    drift_reconciliation_total,
    error_budget_remaining_minutes,
    error_budget_remaining_percent,
    failsafe_mode_active,
    failsafe_triggered_total,
    freeze_decision_total,
    human_review_queue_time,
    l2_connection_status,
    l2_latency_seconds,
    l2_sync_failure_total,
    l2_timeout_total,
    override_escalation_total,
    recovery_alert_total,
    recovery_time_seconds,
    replay_attempts_total,
    replay_outcomes_total,
    retry_attempts_histogram,
    retry_outcomes_total,
    retry_success_rate,
    selfhealing_heartbeat_count,
    selfhealing_heartbeat_timestamp,
    shadow_log_unsynced_count,
    sla_breach_total,
)

# Recorders
from .recorders import (
    emit_heartbeat,
    record_active_override,
    record_circuit_breaker_open_duration,
    record_circuit_breaker_state_change,
    record_deployment_freeze_status,
    record_dlq_item_created,
    record_error_budget_status,
    record_failsafe_recovered,
    record_failsafe_triggered,
    record_freeze_decision,
    record_l2_latency,
    record_l2_sync_failure,
    record_l2_timeout,
    record_override_escalation,
    record_recovery_alert,
    record_recovery_time,
    record_replay_attempt,
    record_retry_attempt,
    record_sla_breach,
)

# Registry
from .registry import (
    DEFAULT_DOMAINS,
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
    get_registered_domains,
    register_domain,
)

# Updaters
from .updaters import (
    collect_all_metrics,
    track_recovery_time,
    update_circuit_breaker_gauges,
    update_dlq_pending_gauges,
    update_dlq_status_gauges,
    update_retry_success_rates,
    update_shadow_log_metrics,
)

__all__ = [
    # Registry
    "get_or_create_counter",
    "get_or_create_gauge",
    "get_or_create_histogram",
    "register_domain",
    "get_registered_domains",
    "DEFAULT_DOMAINS",
    # DLQ Metrics
    "dlq_items_total",
    "dlq_pending_gauge",
    "dlq_by_status_gauge",
    "dlq_created_total",
    # Retry Metrics
    "retry_attempts_histogram",
    "retry_outcomes_total",
    "retry_success_rate",
    # Recovery Metrics
    "recovery_time_seconds",
    "sla_breach_total",
    "human_review_queue_time",
    # Circuit Breaker Metrics
    "circuit_breaker_state",
    "circuit_breaker_transitions",
    "circuit_breaker_open_duration",
    # L2 Storage Metrics
    "l2_timeout_total",
    "l2_sync_failure_total",
    "l2_latency_seconds",
    "l2_connection_status",
    "shadow_log_unsynced_count",
    "drift_reconciliation_total",
    # Replay Metrics
    "replay_attempts_total",
    "replay_outcomes_total",
    # Error Budget Metrics
    "error_budget_remaining_percent",
    "error_budget_remaining_minutes",
    "burn_rate_1h",
    "burn_rate_6h",
    "deployment_freeze_status",
    "freeze_decision_total",
    "active_override_gauge",
    # Heartbeat Metrics
    "selfhealing_heartbeat_timestamp",
    "selfhealing_heartbeat_count",
    "override_escalation_total",
    "recovery_alert_total",
    # Fail-Safe Metrics
    "failsafe_triggered_total",
    "failsafe_mode_active",
    # Recording Functions
    "record_dlq_item_created",
    "record_sla_breach",
    "record_retry_attempt",
    "record_recovery_time",
    "record_circuit_breaker_state_change",
    "record_circuit_breaker_open_duration",
    "record_l2_timeout",
    "record_l2_sync_failure",
    "record_l2_latency",
    "record_replay_attempt",
    "record_error_budget_status",
    "record_deployment_freeze_status",
    "record_freeze_decision",
    "record_active_override",
    "record_failsafe_triggered",
    "record_failsafe_recovered",
    "emit_heartbeat",
    "record_override_escalation",
    "record_recovery_alert",
    # Update Functions
    "update_shadow_log_metrics",
    "update_dlq_pending_gauges",
    "update_dlq_status_gauges",
    "update_circuit_breaker_gauges",
    "update_retry_success_rates",
    "track_recovery_time",
    "collect_all_metrics",
    # Alerting Rules
    "ALERTING_RULES",
]
