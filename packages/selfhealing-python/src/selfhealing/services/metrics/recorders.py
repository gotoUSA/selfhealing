"""
Metric Recording Functions.

All record_* functions that increment counters, observe histograms,
and set gauge values for specific events.

합성 요청(X-Test-Mode, Chaos 실험) 시 자동으로 is_synthetic 레이블 설정.
"""

from __future__ import annotations

import structlog
import time
from datetime import datetime

from selfhealing.core.test_mode_context import TestModeContext

from .definitions import (  # DLQ; Retry; Recovery; Circuit Breaker; L2 Storage; Replay; Error Budget; Heartbeat; Fail-Safe
    active_override_gauge,
    burn_rate_1h,
    burn_rate_6h,
    circuit_breaker_open_duration,
    circuit_breaker_state,
    circuit_breaker_transitions,
    deployment_freeze_status,
    dlq_created_total,
    dlq_items_total,
    error_budget_remaining_minutes,
    error_budget_remaining_percent,
    failsafe_mode_active,
    failsafe_triggered_total,
    freeze_decision_total,
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
    selfhealing_heartbeat_count,
    selfhealing_heartbeat_timestamp,
    sla_breach_total,
)

logger = structlog.get_logger()


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
        is_synthetic = TestModeContext.get_synthetic_label_value()
        dlq_items_total.labels(
            domain=domain,
            failure_type=failure_type,
            is_synthetic=is_synthetic,
        ).inc()
        dlq_created_total.labels(domain=domain).inc()
        logger.debug(
            "metrics.dlq_item_created",
            domain=domain,
            failure_type=failure_type,
            is_synthetic=is_synthetic,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_dlq_creation",
            error=e,
        )


def record_sla_breach(domain: str) -> None:
    """
    Record an SLA breach event.

    Args:
        domain: Business domain where breach occurred
    """
    try:
        sla_breach_total.labels(domain=domain).inc()
        logger.info(
            "metrics.sla_breach_recorded",
            domain=domain,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_sla_breach",
            error=e,
        )


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
        is_synthetic = TestModeContext.get_synthetic_label_value()
        retry_attempts_histogram.labels(
            domain=domain,
            is_synthetic=is_synthetic,
        ).observe(attempt_count)
        retry_outcomes_total.labels(
            domain=domain,
            outcome=outcome,
            is_synthetic=is_synthetic,
        ).inc()
        logger.debug(
            f"[Metrics] Retry recorded: domain={domain}, attempts={attempt_count}, "
            f"outcome={outcome}, is_synthetic={is_synthetic}"
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_retry_metric",
            error=e,
        )


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
        recovery_time_seconds.labels(domain=domain, resolution_type=resolution_type).observe(duration)
        logger.debug(
            "metrics.recovery_time_recorded",
            domain=domain,
            resolution_type=resolution_type,
            duration=duration,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_recovery_time",
            error=e,
        )


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
        from selfhealing.services.cell_topology.cb_namespace import parse_composite_cb_name

        base_service, cell_id = parse_composite_cb_name(service)
        is_synthetic = TestModeContext.get_synthetic_label_value()
        state_value = {"closed": 0, "open": 1, "half_open": 2}.get(to_state, 0)
        circuit_breaker_state.labels(service=base_service, cell_id=cell_id).set(state_value)
        circuit_breaker_transitions.labels(
            service=base_service,
            cell_id=cell_id,
            from_state=from_state,
            to_state=to_state,
            is_synthetic=is_synthetic,
        ).inc()
        logger.info(
            f"[Metrics] Circuit breaker transition: {service} {from_state} -> {to_state}, " f"is_synthetic={is_synthetic}"
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_circuit_breaker",
            error=e,
        )


def record_circuit_breaker_open_duration(service: str, duration_seconds: float) -> None:
    """
    Record how long a circuit breaker was in open state.

    Args:
        service: Service name
        duration_seconds: Time spent in open state
    """
    try:
        circuit_breaker_open_duration.labels(service=service).observe(duration_seconds)
        logger.debug(
            "metrics.cb_open_duration_recorded",
            service=service,
            duration_seconds=duration_seconds,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_cb_duration",
            error=e,
        )


# =============================================================================
# L2 Storage Recording Functions
# =============================================================================


def record_l2_timeout(adapter_type: str, operation: str) -> None:
    """Record L2 timeout occurrence."""
    try:
        l2_timeout_total.labels(adapter_type=adapter_type, operation=operation).inc()
        l2_connection_status.labels(adapter_type=adapter_type).set(0)
    except Exception as e:
        logger.warning(
            "metrics.failed_record_timeout",
            error=e,
        )


def record_l2_sync_failure(adapter_type: str, operation: str) -> None:
    """Record L2 sync failure."""
    try:
        l2_sync_failure_total.labels(adapter_type=adapter_type, operation=operation).inc()
    except Exception as e:
        logger.warning(
            "metrics.failed_record_sync_failure",
            error=e,
        )


def record_l2_latency(adapter_type: str, latency_seconds: float) -> None:
    """Record L2 operation latency."""
    try:
        l2_latency_seconds.labels(adapter_type=adapter_type).observe(latency_seconds)
        l2_connection_status.labels(adapter_type=adapter_type).set(1)
    except Exception as e:
        logger.warning(
            "metrics.failed_record_latency",
            error=e,
        )


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
        is_synthetic = TestModeContext.get_synthetic_label_value()
        replay_attempts_total.labels(
            domain=domain,
            replay_type=replay_type,
            is_synthetic=is_synthetic,
        ).inc()
        outcome = "success" if success else "failure"
        replay_outcomes_total.labels(
            domain=domain,
            outcome=outcome,
            is_synthetic=is_synthetic,
        ).inc()
        logger.debug(
            f"[Metrics] Replay recorded: domain={domain}, type={replay_type}, "
            f"success={success}, is_synthetic={is_synthetic}"
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_replay_metric",
            error=e,
        )


# =============================================================================
# Error Budget Recording Functions
# =============================================================================


def record_error_budget_status(
    slo_name: str,
    remaining_percent: float,
    remaining_minutes: float,
    burn_rate_1h_value: float,
    burn_rate_6h_value: float,
    region: str = "",
    tier: str = "",
) -> None:
    """
    Record Error Budget status metrics.

    Args:
        slo_name: SLO name (e.g., "availability")
        remaining_percent: Budget remaining percentage (0-100)
        remaining_minutes: Budget remaining in minutes
        burn_rate_1h_value: 1-hour burn rate
        burn_rate_6h_value: 6-hour burn rate
        region: 리전 식별자 (빈 문자열이면 글로벌)
        tier: 티어 식별자 (빈 문자열이면 미지정)
    """
    try:
        is_synthetic = TestModeContext.get_synthetic_label_value()
        error_budget_remaining_percent.labels(
            slo_name=slo_name,
            is_synthetic=is_synthetic,
            region=region,
            tier=tier,
        ).set(remaining_percent)
        error_budget_remaining_minutes.labels(
            slo_name=slo_name,
            is_synthetic=is_synthetic,
            region=region,
            tier=tier,
        ).set(remaining_minutes)
        burn_rate_1h.labels(slo_name=slo_name).set(burn_rate_1h_value)
        burn_rate_6h.labels(slo_name=slo_name).set(burn_rate_6h_value)
        logger.debug(
            f"[Metrics] Error budget recorded: slo={slo_name}, "
            f"remaining={remaining_percent:.1f}%, burn_1h={burn_rate_1h_value:.2f}, "
            f"is_synthetic={is_synthetic}, region={region}, tier={tier}"
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_error_budget",
            error=e,
        )


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
        logger.debug(
            "metrics.deployment_freeze_status",
            status=status,
            status_value=status_value,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_freeze_status",
            error=e,
        )


def record_freeze_decision(decision_type: str) -> None:
    """
    Record a freeze-related decision.

    Args:
        decision_type: Type of decision (freeze_acknowledged, override_approved, freeze_lifted)
    """
    try:
        freeze_decision_total.labels(decision_type=decision_type).inc()
        logger.info(
            "metrics.freeze_decision_recorded",
            decision_type=decision_type,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_freeze_decision",
            error=e,
        )


def record_active_override(has_override: bool) -> None:
    """
    Record whether there is an active deployment override.

    Args:
        has_override: Whether an override is active
    """
    try:
        active_override_gauge.set(1 if has_override else 0)
        logger.debug(
            "metrics.active_override",
            has_override=has_override,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_active_override",
            error=e,
        )


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
            "metrics.fail_safe_triggered_alerting",
            component=component,
        )
    except Exception as e:
        logger.error(
            "metrics.failed_record_fail_safe",
            error=e,
        )


def record_failsafe_recovered(component: str) -> None:
    """
    Record that fail-safe mode has been recovered.

    Args:
        component: The component that recovered
    """
    try:
        failsafe_mode_active.labels(component=component).set(0)
        logger.info(
            "metrics.fail_safe_recovered",
            component=component,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_fail_safe",
            error=e,
        )


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
            "metrics.heartbeat_emitted",
            component=component,
            current_time=current_time,
        )
    except Exception as e:
        logger.error(
            "metrics.failed_emit_heartbeat",
            error=e,
        )


def record_override_escalation(override_type: str) -> None:
    """
    Record that an override escalation alert was sent.

    Args:
        override_type: Type of override (hotfix, security_patch, etc.)
    """
    try:
        override_escalation_total.labels(override_type=override_type).inc()
        logger.info(
            "metrics.override_escalation_recorded",
            override_type=override_type,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_override_escalation",
            error=e,
        )


def record_recovery_alert(component: str) -> None:
    """
    Record that a recovery alert was sent.

    Args:
        component: The component that recovered
    """
    try:
        recovery_alert_total.labels(component=component).inc()
        logger.info(
            "metrics.recovery_alert_recorded",
            component=component,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_recovery_alert",
            error=e,
        )


# =============================================================================
# X-Test Regional Boundary Recording Functions
# =============================================================================


def record_xtest_cross_region_denied(
    current_region: str,
    target_region: str,
) -> None:
    """
    Record a cross-region X-Test request denial.

    Called when X-Region header does not match current cluster region.

    Args:
        current_region: Current cluster region (e.g., 'seoul')
        target_region: Requested target region from X-Region header
    """
    try:
        from .definitions import xtest_cross_region_denied_total

        xtest_cross_region_denied_total.labels(
            current_region=current_region,
            target_region=target_region,
        ).inc()
        logger.warning(
            "metrics.cross_region_test_denied",
            current_region=current_region,
            target_region=target_region,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_cross_region",
            error=e,
        )


def record_xtest_global_scope_request(
    endpoint_pattern: str,
    region: str,
    result: str,
) -> None:
    """
    Record a GLOBAL scope X-Test API request.

    Args:
        endpoint_pattern: Matched GLOBAL scope pattern (e.g., 'emergency', 'isolation')
        region: Current or target region
        result: Request result ('allowed', 'denied_no_header', 'denied_mismatch')
    """
    try:
        from .definitions import xtest_global_scope_requests_total

        xtest_global_scope_requests_total.labels(
            endpoint_pattern=endpoint_pattern,
            region=region,
            result=result,
        ).inc()
        logger.debug(
            "metrics.global_scope_request",
            endpoint_pattern=endpoint_pattern,
            region=region,
            result=result,
        )
    except Exception as e:
        logger.warning(
            "metrics.failed_record_global_scope",
            error=e,
        )
