"""
Metric Recording Functions.

All record_* functions that increment counters, observe histograms,
and set gauge values for specific events.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from .definitions import (
    # DLQ
    dlq_items_total,
    dlq_created_total,
    sla_breach_total,
    # Retry
    retry_attempts_histogram,
    retry_outcomes_total,
    # Recovery
    recovery_time_seconds,
    # Circuit Breaker
    circuit_breaker_state,
    circuit_breaker_transitions,
    circuit_breaker_open_duration,
    # L2 Storage
    l2_timeout_total,
    l2_sync_failure_total,
    l2_latency_seconds,
    l2_connection_status,
    # Replay
    replay_attempts_total,
    replay_outcomes_total,
    # Error Budget
    error_budget_remaining_percent,
    error_budget_remaining_minutes,
    burn_rate_1h,
    burn_rate_6h,
    deployment_freeze_status,
    freeze_decision_total,
    active_override_gauge,
    # Heartbeat
    selfhealing_heartbeat_timestamp,
    selfhealing_heartbeat_count,
    override_escalation_total,
    recovery_alert_total,
    # Fail-Safe
    failsafe_triggered_total,
    failsafe_mode_active,
)

logger = logging.getLogger(__name__)


# =============================================================================
# DLQ Recording Functions
# =============================================================================


def record_dlq_item_created(domain: str, failure_type: str) -> None:
    """
    Record that a new DLQ item was created.

    Args:
        domain: Business domain (payment, point, inventory, etc.)
        failure_type: Specific failure type (PG_TIMEOUT, AMOUNT_MISMATCH, etc.)
    """
    try:
        dlq_items_total.labels(domain=domain, failure_type=failure_type).inc()
        dlq_created_total.labels(domain=domain).inc()
        logger.debug(f"[Metrics] DLQ item created: domain={domain}, type={failure_type}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record DLQ creation metric: {e}")


def record_sla_breach(domain: str) -> None:
    """
    Record an SLA breach event.

    Args:
        domain: Business domain where breach occurred
    """
    try:
        sla_breach_total.labels(domain=domain).inc()
        logger.info(f"[Metrics] SLA breach recorded: domain={domain}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record SLA breach metric: {e}")


# =============================================================================
# Retry Recording Functions
# =============================================================================


def record_retry_attempt(domain: str, attempt_count: int, outcome: str) -> None:
    """
    Record a retry attempt outcome.

    Args:
        domain: Business domain
        attempt_count: Number of attempts made
        outcome: Result (success, failure, exhausted)
    """
    try:
        retry_attempts_histogram.labels(domain=domain).observe(attempt_count)
        retry_outcomes_total.labels(domain=domain, outcome=outcome).inc()
        logger.debug(
            f"[Metrics] Retry recorded: domain={domain}, attempts={attempt_count}, outcome={outcome}"
        )
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record retry metric: {e}")


# =============================================================================
# Recovery Recording Functions
# =============================================================================


def record_recovery_time(
    domain: str,
    resolution_type: str,
    created_at: datetime,
    resolved_at: datetime,
) -> None:
    """
    Record time from failure to resolution.

    Args:
        domain: Business domain
        resolution_type: How it was resolved (auto_replay, manual_fix, etc.)
        created_at: When the failure was created
        resolved_at: When it was resolved
    """
    try:
        duration = (resolved_at - created_at).total_seconds()
        recovery_time_seconds.labels(
            domain=domain, resolution_type=resolution_type
        ).observe(duration)
        logger.debug(
            f"[Metrics] Recovery time recorded: domain={domain}, "
            f"type={resolution_type}, duration={duration}s"
        )
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record recovery time metric: {e}")


# =============================================================================
# Circuit Breaker Recording Functions
# =============================================================================


def record_circuit_breaker_state_change(
    service: str,
    from_state: str,
    to_state: str,
) -> None:
    """
    Record a circuit breaker state transition.

    Args:
        service: Service name (e.g., external_gateway)
        from_state: Previous state
        to_state: New state
    """
    try:
        state_value = {"closed": 0, "open": 1, "half_open": 2}.get(to_state, 0)
        circuit_breaker_state.labels(service=service).set(state_value)
        circuit_breaker_transitions.labels(
            service=service,
            from_state=from_state,
            to_state=to_state,
        ).inc()
        logger.info(
            f"[Metrics] Circuit breaker transition: {service} {from_state} -> {to_state}"
        )
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record circuit breaker metric: {e}")


def record_circuit_breaker_open_duration(service: str, duration_seconds: float) -> None:
    """
    Record how long a circuit breaker was in open state.

    Args:
        service: Service name
        duration_seconds: Time spent in open state
    """
    try:
        circuit_breaker_open_duration.labels(service=service).observe(duration_seconds)
        logger.debug(f"[Metrics] CB open duration recorded: {service}={duration_seconds}s")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record CB duration metric: {e}")


# =============================================================================
# L2 Storage Recording Functions
# =============================================================================


def record_l2_timeout(adapter_type: str, operation: str) -> None:
    """Record L2 timeout occurrence."""
    try:
        l2_timeout_total.labels(adapter_type=adapter_type, operation=operation).inc()
        l2_connection_status.labels(adapter_type=adapter_type).set(0)
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record L2 timeout: {e}")


def record_l2_sync_failure(adapter_type: str, operation: str) -> None:
    """Record L2 sync failure."""
    try:
        l2_sync_failure_total.labels(adapter_type=adapter_type, operation=operation).inc()
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record L2 sync failure: {e}")


def record_l2_latency(adapter_type: str, latency_seconds: float) -> None:
    """Record L2 operation latency."""
    try:
        l2_latency_seconds.labels(adapter_type=adapter_type).observe(latency_seconds)
        l2_connection_status.labels(adapter_type=adapter_type).set(1)
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record L2 latency: {e}")


# =============================================================================
# Replay Recording Functions
# =============================================================================


def record_replay_attempt(domain: str, replay_type: str, success: bool) -> None:
    """
    Record a replay attempt.

    Args:
        domain: Business domain
        replay_type: Type of replay (single, batch, conditional)
        success: Whether replay succeeded
    """
    try:
        replay_attempts_total.labels(domain=domain, replay_type=replay_type).inc()
        outcome = "success" if success else "failure"
        replay_outcomes_total.labels(domain=domain, outcome=outcome).inc()
        logger.debug(
            f"[Metrics] Replay recorded: domain={domain}, type={replay_type}, success={success}"
        )
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record replay metric: {e}")


# =============================================================================
# Error Budget Recording Functions
# =============================================================================


def record_error_budget_status(
    slo_name: str,
    remaining_percent: float,
    remaining_minutes: float,
    burn_rate_1h_value: float,
    burn_rate_6h_value: float,
) -> None:
    """
    Record Error Budget status metrics.

    Args:
        slo_name: SLO name (e.g., "availability")
        remaining_percent: Budget remaining percentage (0-100)
        remaining_minutes: Budget remaining in minutes
        burn_rate_1h_value: 1-hour burn rate
        burn_rate_6h_value: 6-hour burn rate
    """
    try:
        error_budget_remaining_percent.labels(slo_name=slo_name).set(remaining_percent)
        error_budget_remaining_minutes.labels(slo_name=slo_name).set(remaining_minutes)
        burn_rate_1h.labels(slo_name=slo_name).set(burn_rate_1h_value)
        burn_rate_6h.labels(slo_name=slo_name).set(burn_rate_6h_value)
        logger.debug(
            f"[Metrics] Error budget recorded: slo={slo_name}, "
            f"remaining={remaining_percent:.1f}%, burn_1h={burn_rate_1h_value:.2f}"
        )
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record error budget metric: {e}")


def record_deployment_freeze_status(status: str) -> None:
    """
    Record deployment freeze status.

    Args:
        status: Freeze status (proceed, caution, warning, freeze_recommended)
    """
    try:
        status_mapping = {
            "proceed": 0,
            "caution": 1,
            "warning": 2,
            "freeze_recommended": 3,
        }
        status_value = status_mapping.get(status, 0)
        deployment_freeze_status.set(status_value)
        logger.debug(f"[Metrics] Deployment freeze status: {status} ({status_value})")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record freeze status metric: {e}")


def record_freeze_decision(decision_type: str) -> None:
    """
    Record a freeze-related decision.

    Args:
        decision_type: Type of decision (freeze_acknowledged, override_approved, freeze_lifted)
    """
    try:
        freeze_decision_total.labels(decision_type=decision_type).inc()
        logger.info(f"[Metrics] Freeze decision recorded: {decision_type}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record freeze decision metric: {e}")


def record_active_override(has_override: bool) -> None:
    """
    Record whether there is an active deployment override.

    Args:
        has_override: Whether an override is active
    """
    try:
        active_override_gauge.set(1 if has_override else 0)
        logger.debug(f"[Metrics] Active override: {has_override}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record active override metric: {e}")


# =============================================================================
# Fail-Safe Recording Functions
# =============================================================================


def record_failsafe_triggered(component: str) -> None:
    """
    Record that fail-safe mode was triggered.

    This metric is CRITICAL for detecting "silent failures".
    It should trigger alerts in Prometheus/Grafana.

    Args:
        component: The component that triggered fail-safe (e.g., "error_budget")
    """
    try:
        failsafe_triggered_total.labels(component=component).inc()
        failsafe_mode_active.labels(component=component).set(1)
        logger.critical(
            f"[Metrics] FAIL-SAFE TRIGGERED: component={component}. "
            "Alerting rules should fire."
        )
    except Exception as e:
        logger.error(f"[Metrics] Failed to record fail-safe metric: {e}")


def record_failsafe_recovered(component: str) -> None:
    """
    Record that fail-safe mode has been recovered.

    Args:
        component: The component that recovered
    """
    try:
        failsafe_mode_active.labels(component=component).set(0)
        logger.info(f"[Metrics] Fail-safe recovered: component={component}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record fail-safe recovery: {e}")


# =============================================================================
# Heartbeat Functions (Dead Man's Snitch)
# =============================================================================


def emit_heartbeat(component: str = "error_budget") -> None:
    """
    Emit a heartbeat signal indicating the system is alive.

    This should be called periodically (default: every 60 seconds).
    If this metric stops being updated, it indicates the service is dead.

    Args:
        component: The component emitting the heartbeat

    Usage:
        # In a Celery Beat task or background thread
        @app.task
        def heartbeat_task():
            emit_heartbeat("error_budget")

    Prometheus Alert Rule:
        - alert: SelfHealingServiceDead
          expr: time() - selfhealing_heartbeat_timestamp_seconds > 120
          for: 0m
          labels:
            severity: critical
    """
    try:
        current_time = time.time()
        selfhealing_heartbeat_timestamp.labels(component=component).set(current_time)
        selfhealing_heartbeat_count.labels(component=component).inc()
        logger.debug(
            f"[Metrics] Heartbeat emitted: component={component}, time={current_time}"
        )
    except Exception as e:
        logger.error(f"[Metrics] Failed to emit heartbeat: {e}")


def record_override_escalation(override_type: str) -> None:
    """
    Record that an override escalation alert was sent.

    Args:
        override_type: Type of override (hotfix, security_patch, etc.)
    """
    try:
        override_escalation_total.labels(override_type=override_type).inc()
        logger.info(f"[Metrics] Override escalation recorded: type={override_type}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record override escalation: {e}")


def record_recovery_alert(component: str) -> None:
    """
    Record that a recovery alert was sent.

    Args:
        component: The component that recovered
    """
    try:
        recovery_alert_total.labels(component=component).inc()
        logger.info(f"[Metrics] Recovery alert recorded: component={component}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record recovery alert: {e}")
