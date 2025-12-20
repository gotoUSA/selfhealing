"""
Self-Healing Observability Metrics

Prometheus metrics for monitoring the L3 Self-Healing layer.
Provides comprehensive visibility into DLQ, retry, recovery, and circuit breaker operations.

Metric Categories:
- DLQ Metrics: Track DLQ item creation and pending counts
- Retry Metrics: Monitor retry attempts and success rates
- Recovery Metrics: Measure time to resolution
- Circuit Breaker Metrics: Track circuit state changes and duration

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Callable, Generator

from prometheus_client import Counter, Gauge, Histogram, REGISTRY

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Safe Metric Registration Helpers
# =============================================================================


def _get_or_create_counter(name: str, description: str, labels: list[str]) -> Counter:
    """Get existing counter or create new one to avoid duplicate registration."""
    # Check if already registered
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Counter(name, description, labels)
    except ValueError:
        # Metric already registered, retrieve it from registry
        return REGISTRY._names_to_collectors[name]


def _get_or_create_gauge(name: str, description: str, labels: list[str]) -> Gauge:
    """Get existing gauge or create new one to avoid duplicate registration."""
    # Check if already registered
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Gauge(name, description, labels)
    except ValueError:
        # Metric already registered, retrieve it from registry
        return REGISTRY._names_to_collectors[name]


def _get_or_create_histogram(name: str, description: str, labels: list[str], buckets: tuple = None) -> Histogram:
    """Get existing histogram or create new one to avoid duplicate registration."""
    # Check if already registered
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        if buckets:
            return Histogram(name, description, labels, buckets=buckets)
        return Histogram(name, description, labels)
    except ValueError:
        # Metric already registered, retrieve it from registry
        return REGISTRY._names_to_collectors[name]


# =============================================================================
# Domain Registry (Dynamic Domain Registration)
# =============================================================================

# Registered domains - populated dynamically by adapters at initialization
_registered_domains: set[str] = set()

# Default domains (domain-neutral fallbacks)
_DEFAULT_DOMAINS: list[str] = [
    "external_service",
    "internal_process",
    "async_task",
    "notification",
    "data_sync",
]


def register_domain(domain: str) -> None:
    """
    Register a domain for metrics collection.

    Call this from adapters to register application-specific domains.
    Example: register_domain("payment"), register_domain("order")
    """
    _registered_domains.add(domain.lower())


def get_registered_domains() -> list[str]:
    """Get all registered domains, including defaults."""
    all_domains = _registered_domains | set(_DEFAULT_DOMAINS)
    return sorted(all_domains)


# Legacy compatibility: DOMAINS now returns registered domains
@property
def DOMAINS() -> list[str]:
    """@deprecated: use get_registered_domains()"""
    return get_registered_domains()


# For backward compatibility, pre-register common domains
# These can be overridden by adapter configuration
for _domain in _DEFAULT_DOMAINS:
    register_domain(_domain)


# =============================================================================
# DLQ Metrics
# =============================================================================

# Total DLQ items created (labeled by domain and failure_type)
dlq_items_total = _get_or_create_counter(
    "dlq_items_total",
    "Total DLQ items created",
    ["domain", "failure_type"],
)

# Current pending DLQ items gauge (labeled by domain)
dlq_pending_gauge = _get_or_create_gauge(
    "dlq_pending_count",
    "Current pending DLQ items",
    ["domain"],
)

# DLQ items by status gauge
dlq_by_status_gauge = _get_or_create_gauge(
    "dlq_items_by_status",
    "DLQ items count by status",
    ["status"],
)

# DLQ growth rate counter (for alerting on rapid growth)
dlq_created_total = _get_or_create_counter(
    "dlq_created_total",
    "Total DLQ items created (for rate calculation)",
    ["domain"],
)


# =============================================================================
# Retry Metrics
# =============================================================================

# Retry attempts histogram (distribution of attempts before resolution)
retry_attempts_histogram = _get_or_create_histogram(
    "retry_attempts_total",
    "Number of retry attempts before resolution",
    ["domain"],
    buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
)

# Retry success/failure counter
retry_outcomes_total = _get_or_create_counter(
    "retry_outcomes_total",
    "Retry outcomes by domain and result",
    ["domain", "outcome"],  # outcome: success, failure, exhausted
)

# Per-domain retry success rate gauge (updated periodically)
retry_success_rate = _get_or_create_gauge(
    "retry_success_rate",
    "Percentage of successful retries (0-100)",
    ["domain"],
)


# =============================================================================
# Recovery Metrics
# =============================================================================

# Time from failure to resolution (histogram)
recovery_time_seconds = _get_or_create_histogram(
    "recovery_time_seconds",
    "Time from failure to resolution in seconds",
    ["domain", "resolution_type"],
    buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400),  # 1m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 24h
)

# SLA breach counter
sla_breach_total = _get_or_create_counter(
    "sla_breach_total",
    "Total SLA breaches detected",
    ["domain"],
)

# Human review queue time (time waiting for human intervention)
human_review_queue_time = _get_or_create_histogram(
    "human_review_queue_time_seconds",
    "Time items wait in queue for human review",
    ["domain"],
    buckets=(300, 900, 1800, 3600, 7200, 14400, 28800),  # 5m, 15m, 30m, 1h, 2h, 4h, 8h
)


# =============================================================================
# Circuit Breaker Metrics
# =============================================================================

# Circuit breaker state gauge (0=closed, 1=open, 2=half-open)
circuit_breaker_state = _get_or_create_gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["service"],
)

# Circuit breaker state change counter
circuit_breaker_transitions = _get_or_create_counter(
    "circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    ["service", "from_state", "to_state"],
)

# Time spent in open state
circuit_breaker_open_duration = _get_or_create_histogram(
    "circuit_breaker_open_duration_seconds",
    "Duration in open state before closing",
    ["service"],
    buckets=(60, 300, 600, 1800, 3600, 7200),  # 1m, 5m, 10m, 30m, 1h, 2h
)


# =============================================================================
# Replay Metrics
# =============================================================================

# Replay attempts counter
replay_attempts_total = _get_or_create_counter(
    "replay_attempts_total",
    "Total replay attempts",
    ["domain", "replay_type"],  # replay_type: single, batch, conditional
)

# Replay outcomes counter
replay_outcomes_total = _get_or_create_counter(
    "replay_outcomes_total",
    "Replay outcomes",
    ["domain", "outcome"],  # outcome: success, failure, rejected
)


# =============================================================================
# Error Budget Metrics
# =============================================================================

# Error budget remaining gauge (percentage)
error_budget_remaining_percent = _get_or_create_gauge(
    "error_budget_remaining_percent",
    "Error budget remaining as percentage (0-100)",
    ["slo_name"],
)

# Error budget remaining gauge (minutes)
error_budget_remaining_minutes = _get_or_create_gauge(
    "error_budget_remaining_minutes",
    "Error budget remaining in minutes",
    ["slo_name"],
)

# Burn rate gauges
burn_rate_1h = _get_or_create_gauge(
    "error_budget_burn_rate_1h",
    "Error budget burn rate over 1 hour window",
    ["slo_name"],
)

burn_rate_6h = _get_or_create_gauge(
    "error_budget_burn_rate_6h",
    "Error budget burn rate over 6 hour window",
    ["slo_name"],
)

# Deployment freeze status gauge (0=proceed, 1=caution, 2=warning, 3=freeze_recommended)
deployment_freeze_status = _get_or_create_gauge(
    "deployment_freeze_status",
    "Deployment freeze status (0=proceed, 1=caution, 2=warning, 3=freeze_recommended)",
    [],  # Global status, no labels
)

# Freeze decision counter
freeze_decision_total = _get_or_create_counter(
    "freeze_decision_total",
    "Total freeze-related decisions",
    ["decision_type"],  # freeze_acknowledged, override_approved, freeze_lifted
)

# Active override gauge (0=no override, 1=has active override)
active_override_gauge = _get_or_create_gauge(
    "deployment_active_override",
    "Whether there is an active deployment override (0=no, 1=yes)",
    [],
)


# =============================================================================
# Heartbeat Metrics (Dead Man's Snitch)
# =============================================================================
#
# Heartbeat 메트릭은 시스템이 "살아있음"을 증명합니다.
# 이 메트릭이 일정 시간 업데이트되지 않으면 시스템이 완전히 죽은 것입니다.
#
# Prometheus 알림 규칙 예시:
#   expr: time() - selfhealing_heartbeat_timestamp_seconds > 120
#   for: 0m
#   severity: critical
#

selfhealing_heartbeat_timestamp = _get_or_create_gauge(
    "selfhealing_heartbeat_timestamp_seconds",
    "Last heartbeat timestamp in seconds since epoch",
    ["component"],
)

selfhealing_heartbeat_count = _get_or_create_counter(
    "selfhealing_heartbeat_total",
    "Total heartbeat emissions",
    ["component"],
)

# Override 에스컬레이션 카운터
override_escalation_total = _get_or_create_counter(
    "selfhealing_override_escalation_total",
    "Total override escalation alerts sent",
    ["override_type"],
)

# 복구 알림 카운터
recovery_alert_total = _get_or_create_counter(
    "selfhealing_recovery_alert_total",
    "Total recovery alerts sent",
    ["component"],
)


# =============================================================================
# Helper Functions - Recording Metrics
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
        logger.debug(f"[Metrics] Retry recorded: domain={domain}, attempts={attempt_count}, outcome={outcome}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record retry metric: {e}")


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
        logger.debug(f"[Metrics] Recovery time recorded: domain={domain}, type={resolution_type}, duration={duration}s")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record recovery time metric: {e}")


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
        # Update state gauge
        state_value = {"closed": 0, "open": 1, "half_open": 2}.get(to_state, 0)
        circuit_breaker_state.labels(service=service).set(state_value)

        # Record transition
        circuit_breaker_transitions.labels(
            service=service,
            from_state=from_state,
            to_state=to_state,
        ).inc()

        logger.info(f"[Metrics] Circuit breaker transition: {service} {from_state} -> {to_state}")
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
        logger.debug(f"[Metrics] Replay recorded: domain={domain}, type={replay_type}, success={success}")
    except Exception as e:
        logger.warning(f"[Metrics] Failed to record replay metric: {e}")


# =============================================================================
# Error Budget Metrics Recording
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
# Fail-Safe Metrics
# =============================================================================

# Fail-Safe 발동 카운터 (침묵하는 장애 방지)
failsafe_triggered_total = _get_or_create_counter(
    "selfhealing_failsafe_triggered_total",
    "Number of times fail-safe mode was activated",
    ["component"],
)

# Fail-Safe 현재 상태 (1=degraded, 0=normal)
failsafe_mode_active = _get_or_create_gauge(
    "selfhealing_failsafe_mode_active",
    "Whether fail-safe mode is currently active (1=yes, 0=no)",
    ["component"],
)


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
        logger.critical(f"[Metrics] FAIL-SAFE TRIGGERED: component={component}. " "Alerting rules should fire.")
    except Exception as e:
        # 메트릭 기록 실패해도 시스템은 계속 동작
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
    import time

    try:
        current_time = time.time()
        selfhealing_heartbeat_timestamp.labels(component=component).set(current_time)
        selfhealing_heartbeat_count.labels(component=component).inc()
        logger.debug(f"[Metrics] Heartbeat emitted: component={component}, time={current_time}")
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


# =============================================================================
# Gauge Update Functions (for periodic collection tasks)
# =============================================================================


def update_dlq_pending_gauges(
    repository: "FailedOperationRepository | None" = None,
) -> dict[str, int]:
    """
    Update DLQ pending gauges from database.

    Should be called periodically by a scheduled task.

    Args:
        repository: Optional repository instance (uses factory if not provided)

    Returns:
        Dictionary of domain -> pending count
    """
    try:
        if repository is None:
            from selfhealing.factory import ProviderRegistry

            repository = ProviderRegistry.get_failed_operation_repo()

        stats = repository.get_statistics()
        pending_by_domain = stats.get("pending_by_domain", {})

        # Update gauges for all registered domains
        for domain in get_registered_domains():
            count = pending_by_domain.get(domain, 0)
            dlq_pending_gauge.labels(domain=domain).set(count)

        logger.debug(f"[Metrics] Updated DLQ pending gauges: {pending_by_domain}")
        return pending_by_domain

    except Exception as e:
        logger.error(f"[Metrics] Failed to update DLQ pending gauges: {e}")
        return {}


def update_dlq_status_gauges(
    repository: "FailedOperationRepository | None" = None,
) -> dict[str, int]:
    """
    Update DLQ status distribution gauges.

    Args:
        repository: Optional repository instance (uses factory if not provided)

    Returns:
        Dictionary of status -> count
    """
    try:
        if repository is None:
            from selfhealing.factory import ProviderRegistry

            repository = ProviderRegistry.get_failed_operation_repo()

        stats = repository.get_statistics()
        by_status = {
            "pending": stats.get("pending_count", 0),
            "reviewing": stats.get("reviewing_count", 0),
            "resolved": stats.get("resolved_count", 0),
            "rejected": stats.get("rejected_count", 0),
        }

        for status, count in by_status.items():
            dlq_by_status_gauge.labels(status=status).set(count)

        logger.debug(f"[Metrics] Updated DLQ status gauges: {by_status}")
        return by_status

    except Exception as e:
        logger.error(f"[Metrics] Failed to update DLQ status gauges: {e}")
        return {}


def update_circuit_breaker_gauges(
    repository: "CircuitBreakerStateRepository | None" = None,
) -> dict[str, str]:
    """
    Update circuit breaker state gauges from database.

    Args:
        repository: Optional repository instance (uses factory if not provided)

    Returns:
        Dictionary of service -> state
    """
    try:
        if repository is None:
            from selfhealing.factory import ProviderRegistry

            repository = ProviderRegistry.get_circuit_breaker_repo()

        all_states = repository.get_all()
        states = {}
        for cb in all_states:
            state_value = {"closed": 0, "open": 1, "half_open": 2}.get(cb.state, 0)
            circuit_breaker_state.labels(service=cb.service_name).set(state_value)
            states[cb.service_name] = cb.state

        logger.debug(f"[Metrics] Updated circuit breaker gauges: {states}")
        return states

    except Exception as e:
        logger.error(f"[Metrics] Failed to update circuit breaker gauges: {e}")
        return {}


def update_retry_success_rates(
    repository: "FailedOperationRepository | None" = None,
) -> dict[str, float]:
    """
    Calculate and update retry success rate gauges.

    Args:
        repository: Optional repository instance (uses factory if not provided)

    Returns:
        Dictionary of domain -> success_rate_percentage
    """
    try:
        if repository is None:
            from selfhealing.factory import ProviderRegistry

            repository = ProviderRegistry.get_failed_operation_repo()

        stats = repository.get_statistics()
        rates = {}

        # Get success rates from repository statistics if available
        success_rates = stats.get("success_rates_by_domain", {})

        for domain in get_registered_domains():
            if domain in success_rates:
                rate = success_rates[domain]
            else:
                # Default to 100% if no data
                rate = 100.0

            retry_success_rate.labels(domain=domain).set(rate)
            rates[domain] = rate

        logger.debug(f"[Metrics] Updated retry success rates: {rates}")
        return rates

    except Exception as e:
        logger.error(f"[Metrics] Failed to update retry success rates: {e}")
        return {}


# =============================================================================
# Context Manager and Decorators for Instrumentation
# =============================================================================


@contextmanager
def track_recovery_time(domain: str, resolution_type: str) -> Generator[None, None, None]:
    """
    Context manager to track recovery time.

    Usage:
        with track_recovery_time("payment", "auto_replay"):
            # perform recovery operation
            pass
    """
    from selfhealing.core.timezone import now

    start = now()
    try:
        yield
    finally:
        end = now()
        duration = (end - start).total_seconds()
        recovery_time_seconds.labels(domain=domain, resolution_type=resolution_type).observe(duration)


def track_replay(replay_type: str = "single"):
    """
    Decorator to track replay attempts.

    Usage:
        @track_replay("batch")
        def batch_replay(...)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            domain = kwargs.get("domain", "unknown")
            try:
                result = func(*args, **kwargs)
                success = getattr(result, "success", True) if result else False
                record_replay_attempt(domain, replay_type, success)
                return result
            except Exception as e:
                record_replay_attempt(domain, replay_type, success=False)
                raise

        return wrapper

    return decorator


# =============================================================================
# Metric Collection Task Helper
# =============================================================================


def collect_all_metrics() -> dict:
    """
    Collect all self-healing metrics.

    This should be called by a periodic Celery task.

    Returns:
        Dictionary with all current metric values
    """
    pending = update_dlq_pending_gauges()
    status = update_dlq_status_gauges()
    cb_states = update_circuit_breaker_gauges()
    success_rates = update_retry_success_rates()

    return {
        "dlq_pending_by_domain": pending,
        "dlq_by_status": status,
        "circuit_breaker_states": cb_states,
        "retry_success_rates": success_rates,
        "collected_at": datetime.now().isoformat(),
    }


# =============================================================================
# Alerting Rule Definitions (Single Source of Truth for Prometheus alerts)
# =============================================================================

# These rules are used to generate scripts/prometheus/self_healing_alerts.yml
# Run: python manage.py generate_self_healing_alerts

ALERTING_RULES: dict = {
    "DLQPendingHigh": {
        "expr": "dlq_pending_count > 10",
        "for": "5m",
        "severity": "warning",
        "team": "ops",
        "summary": "DLQ pending count is high",
        "description": "More than 10 items pending in DLQ for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-pending-high",
    },
    "DLQPendingCritical": {
        "expr": "dlq_pending_count > 50",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "DLQ pending count is critical",
        "description": "More than 50 items pending in DLQ for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-pending-critical",
    },
    "DLQGrowthRateHigh": {
        "expr": "rate(dlq_created_total[5m]) > 5",
        "for": "5m",
        "severity": "warning",
        "team": "ops",
        "summary": "DLQ growth rate is high",
        "description": "More than 5 new DLQ items per minute for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-growth-high",
    },
    "RetrySuccessRateLow": {
        "expr": "retry_success_rate < 70",
        "for": "15m",
        "severity": "warning",
        "team": "dev",
        "summary": "Retry success rate is low",
        "description": "Retry success rate below 70% for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/retry-success-low",
    },
    "CircuitBreakerOpen": {
        "expr": "circuit_breaker_state == 1",
        "for": "1m",
        "severity": "critical",
        "team": "ops",
        "summary": "Circuit breaker is open",
        "description": "Circuit breaker for {{ $labels.service }} is in OPEN state",
        "runbook_url": "https://docs.internal/runbooks/circuit-breaker-open",
    },
    "CircuitBreakerOpenLong": {
        "expr": "circuit_breaker_state == 1",
        "for": "10m",
        "severity": "critical",
        "team": "ops",
        "summary": "Circuit breaker open for extended period",
        "description": "Circuit breaker for {{ $labels.service }} has been open for more than 10 minutes",
        "runbook_url": "https://docs.internal/runbooks/circuit-breaker-extended",
    },
    "SLABreachDetected": {
        "expr": "increase(sla_breach_total[1h]) > 0",
        "for": "0m",
        "severity": "warning",
        "team": "ops",
        "summary": "SLA breach detected",
        "description": "SLA breach detected for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/sla-breach",
    },
    "RecoveryTimeSlow": {
        "expr": "histogram_quantile(0.95, rate(recovery_time_seconds_bucket[1h])) > 1800",
        "for": "15m",
        "severity": "warning",
        "team": "ops",
        "summary": "Recovery time P95 is slow",
        "description": "95th percentile recovery time exceeds 30 minutes",
        "runbook_url": "https://docs.internal/runbooks/recovery-slow",
    },
    "HumanReviewQueueLong": {
        "expr": "histogram_quantile(0.95, rate(human_review_queue_time_seconds_bucket[1h])) > 3600",
        "for": "30m",
        "severity": "warning",
        "team": "ops",
        "summary": "Human review queue time is high",
        "description": "Items waiting more than 1 hour for human review",
        "runbook_url": "https://docs.internal/runbooks/review-queue-long",
    },
    "ReplayFailureRateHigh": {
        "expr": "sum(rate(replay_outcomes_total{outcome='failure'}[1h])) / sum(rate(replay_outcomes_total[1h])) > 0.5",
        "for": "15m",
        "severity": "warning",
        "team": "dev",
        "summary": "Replay failure rate is high",
        "description": "More than 50% of replay attempts are failing",
        "runbook_url": "https://docs.internal/runbooks/replay-failure-high",
    },
    # =========================================================================
    # Error Budget Alerting Rules
    # =========================================================================
    "ErrorBudgetCritical": {
        "expr": "error_budget_remaining_percent < 20",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "Error budget critical - deployment freeze recommended",
        "description": "Error budget remaining is {{ $value }}%. Deployment freeze is recommended.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-critical",
    },
    "ErrorBudgetWarning": {
        "expr": "error_budget_remaining_percent < 50",
        "for": "10m",
        "severity": "warning",
        "team": "ops",
        "summary": "Error budget warning",
        "description": "Error budget remaining is {{ $value }}%. Consider reducing deployments.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-warning",
    },
    "ErrorBudgetFastBurn": {
        "expr": "error_budget_burn_rate_1h > 14.4",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "Fast error budget burn detected",
        "description": "1-hour burn rate is {{ $value }}x. Consuming 2%+ budget per hour.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-fast-burn",
    },
    "ErrorBudgetSlowBurn": {
        "expr": "error_budget_burn_rate_6h > 3",
        "for": "30m",
        "severity": "warning",
        "team": "ops",
        "summary": "Slow error budget burn detected",
        "description": "6-hour burn rate is {{ $value }}x. Sustained elevated error rate.",
        "runbook_url": "https://docs.internal/runbooks/error-budget-slow-burn",
    },
    "DeploymentFreezeActive": {
        "expr": "deployment_freeze_status >= 3",
        "for": "0m",
        "severity": "info",
        "team": "ops",
        "summary": "Deployment freeze is active",
        "description": "Deployment freeze is recommended or in effect.",
        "runbook_url": "https://docs.internal/runbooks/deployment-freeze",
    },
    # =========================================================================
    # Fail-Safe Alerting Rules (침묵하는 장애 방지)
    # =========================================================================
    "FailSafeTriggered": {
        "expr": "increase(selfhealing_failsafe_triggered_total[5m]) > 0",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Self-Healing Fail-Safe mode activated",
        "description": (
            "Self-Healing system component '{{ $labels.component }}' has failed and "
            "Fail-Safe mode is active. Deployments are proceeding but system needs "
            "immediate attention."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-failsafe",
    },
    "FailSafeModeActive": {
        "expr": "selfhealing_failsafe_mode_active == 1",
        "for": "2m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Self-Healing in degraded mode",
        "description": (
            "Self-Healing '{{ $labels.component }}' is operating in Fail-Safe mode. "
            "Error Budget recommendations are not available. "
            "Investigate and restore normal operation immediately."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-failsafe",
    },
    # =========================================================================
    # Dead Man's Snitch (Heartbeat Monitoring)
    # =========================================================================
    "SelfHealingServiceDead": {
        "expr": "time() - selfhealing_heartbeat_timestamp_seconds > 120",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🔴 Self-Healing service is DEAD",
        "description": (
            "No heartbeat received from Self-Healing '{{ $labels.component }}' "
            "for more than 2 minutes. The service may have crashed or is unresponsive. "
            "This is a critical infrastructure failure."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-dead",
    },
    "SelfHealingHeartbeatMissing": {
        "expr": "absent(selfhealing_heartbeat_timestamp_seconds) == 1",
        "for": "5m",
        "severity": "critical",
        "team": "ops",
        "summary": "🔴 Self-Healing heartbeat metric missing",
        "description": (
            "The Self-Healing heartbeat metric is completely absent. "
            "The service may never have started or is not properly initialized."
        ),
        "runbook_url": "https://docs.internal/runbooks/selfhealing-missing",
    },
    # =========================================================================
    # Override Escalation Alerting Rules
    # =========================================================================
    "OverrideEscalation": {
        "expr": "increase(selfhealing_override_escalation_total[1h]) > 0",
        "for": "0m",
        "severity": "warning",
        "team": "ops",
        "summary": "⚠️ Deployment override escalation",
        "description": (
            "A deployment override of type '{{ $labels.override_type }}' was approved "
            "despite insufficient error budget. This action requires governance review."
        ),
        "runbook_url": "https://docs.internal/runbooks/override-escalation",
    },
    "OverrideEscalationHigh": {
        "expr": "increase(selfhealing_override_escalation_total[24h]) > 5",
        "for": "0m",
        "severity": "critical",
        "team": "ops",
        "summary": "🚨 Excessive deployment overrides",
        "description": (
            "More than 5 deployment overrides in the last 24 hours. "
            "This may indicate process issues or sustained reliability problems."
        ),
        "runbook_url": "https://docs.internal/runbooks/override-escalation-high",
    },
}
