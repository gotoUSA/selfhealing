"""
Gauge Update Functions, Context Managers, and Decorators.

Functions for periodic gauge updates from repositories,
context managers for instrumentation, and alerting rule definitions.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from typing import TYPE_CHECKING, Callable, Generator

from selfhealing.metrics.safe_gauge import clamp_non_negative, clamp_percentage

from .definitions import (
    dlq_pending_gauge,
    dlq_by_status_gauge,
    circuit_breaker_state,
    retry_success_rate,
    recovery_time_seconds,
    shadow_log_unsynced_count,
)
from .registry import get_registered_domains
from .recorders import record_replay_attempt

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Shadow Log Metrics Update
# =============================================================================


def update_shadow_log_metrics() -> None:
    """Update shadow log metrics from ShadowLogger."""
    try:
        from selfhealing.adapters.memory.circuit_breaker import get_shadow_logger

        shadow_logger = get_shadow_logger()
        stats = shadow_logger.get_stats()
        shadow_log_unsynced_count.labels().set(stats.get("unsynced_count", 0))
    except Exception as e:
        logger.warning(f"[Metrics] Failed to update shadow log metrics: {e}")


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
            safe_count = clamp_non_negative(count, f"dlq_status_count[{status}]")
            dlq_by_status_gauge.labels(status=status).set(safe_count)

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

        success_rates = stats.get("success_rates_by_domain", {})

        for domain in get_registered_domains():
            if domain in success_rates:
                rate = success_rates[domain]
            else:
                rate = 100.0

            safe_rate = clamp_percentage(rate, f"retry_success_rate[{domain}]")
            retry_success_rate.labels(domain=domain).set(safe_rate)
            rates[domain] = safe_rate

        logger.debug(f"[Metrics] Updated retry success rates: {rates}")
        return rates

    except Exception as e:
        logger.error(f"[Metrics] Failed to update retry success rates: {e}")
        return {}


# =============================================================================
# Context Manager and Decorators for Instrumentation
# =============================================================================


@contextmanager
def track_recovery_time(
    domain: str, resolution_type: str
) -> Generator[None, None, None]:
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
        recovery_time_seconds.labels(
            domain=domain, resolution_type=resolution_type
        ).observe(duration)


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
            except Exception:
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
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
