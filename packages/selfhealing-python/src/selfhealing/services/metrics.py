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
# Domain Constants (Single Source of Truth)
# =============================================================================

# All self-healing domains - update this list when adding new domains
DOMAINS: list[str] = [
    "payment",
    "point",
    "inventory",
    "webhook",
    "notification",
]


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
        service: Service name (e.g., toss_payment)
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

        # Update gauges for all domains
        for domain in DOMAINS:
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

        for domain in DOMAINS:
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
}
