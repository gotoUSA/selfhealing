"""
Metrics & Monitoring Celery Tasks.

These tasks collect metrics, check SLA breaches, and provide health monitoring.

Usage in CELERY_BEAT_SCHEDULE:
    'collect-self-healing-metrics': {
        'task': 'selfhealing.adapters.celery.tasks.collect_self_healing_metrics',
        'schedule': 60.0,  # Every minute
    },
    'check-sla-breaches': {
        'task': 'selfhealing.adapters.celery.tasks.check_and_report_sla_breaches',
        'schedule': 300.0,  # Every 5 minutes
    },
    'emit-selfhealing-heartbeat': {
        'task': 'selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat',
        'schedule': 60.0,  # Should match heartbeat_interval_seconds config
    },
"""

from datetime import datetime, timedelta
from datetime import timezone as tz

import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.collect_self_healing_metrics",
    queue="monitoring",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def collect_self_healing_metrics(self) -> dict:
    """
    Periodic task to collect and update self-healing metrics.

    Collects:
    - DLQ pending counts by domain
    - DLQ items by status
    - Circuit breaker states
    - Retry success rates

    Uses ProviderRegistry for statistics repository access.

    Returns:
        Dictionary with collected metric values
    """
    logger.debug("metrics")

    try:
        from selfhealing.factory import ProviderRegistry

        stats_repo = ProviderRegistry.get_statistics_repo()

        # DLQ stats
        status_counts = stats_repo.get_status_counts()
        domain_dist = stats_repo.get_domain_distribution(limit=20)

        dlq_by_domain = {d.domain: d.count for d in domain_dist}
        dlq_by_status = {
            "pending": status_counts.pending,
            "resolved": status_counts.resolved,
            "failed": status_counts.failed,
            "archived": status_counts.archived,
        }

        # Circuit breaker stats from Redis
        cb_summary = stats_repo.get_circuit_breaker_summary()

        metrics = {
            "dlq_pending_by_domain": dlq_by_domain,
            "dlq_by_status": dlq_by_status,
            "circuit_breakers_open": cb_summary.open,
            "circuit_breakers_half_open": cb_summary.half_open,
        }

        logger.debug(
            "metrics.collection_complete",
            status_counts=status_counts.pending,
        )

        return {
            "success": True,
            **metrics,
        }

    except Exception as e:
        logger.exception(
            "metrics.failed_collect_metrics",
            error=e,
        )
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.check_and_report_sla_breaches",
    queue="monitoring",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_and_report_sla_breaches(self) -> dict:
    """
    Periodic task to check for SLA breaches.

    Checks pending DLQ entries against SLA thresholds and records breaches.
    Uses ProviderRegistry for statistics repository access.

    Returns:
        Dictionary with SLA breach information
    """
    logger.debug("sla_check.breach_check_started")

    try:
        from selfhealing.factory import ProviderRegistry

        stats_repo = ProviderRegistry.get_statistics_repo()

        # Default SLA: 4 hours for resolution
        sla_threshold = timedelta(hours=4)
        cutoff = datetime.now(tz.utc) - sla_threshold

        # Get SLA breaches from statistics repository
        breaches_by_domain = stats_repo.get_sla_breaches(
            sla_threshold_hours=4,
            statuses=["pending", "reviewing", "requires_review"],
        )

        total_breaches = sum(breaches_by_domain.values())

        if total_breaches > 0:
            logger.warning(
                "sla_check.breaches_found",
                total=total_breaches,
                by_domain=breaches_by_domain,
            )
        else:
            logger.debug("sla_check.no_breaches_found")

        return {
            "success": True,
            "total_breaches": total_breaches,
            "breaches_by_domain": breaches_by_domain,
        }

    except Exception as e:
        logger.exception(
            "sla_check_failed",
            error=e,
        )
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat",
    queue="monitoring",
    max_retries=0,
    time_limit=10,
    soft_time_limit=8,
)
def emit_selfhealing_heartbeat(self, component: str = "error_budget") -> dict:
    """
    Periodic heartbeat task for Dead Man's Snitch.

    This task should be scheduled at a regular interval (default: 60 seconds).
    If the heartbeat metric stops being updated, Prometheus will fire an alert.

    Args:
        component: The component emitting the heartbeat (default: 'error_budget')

    Returns:
        Dictionary with heartbeat status

    Prometheus Alert Rules:
        - SelfHealingServiceDead: time() - selfhealing_heartbeat_timestamp_seconds > 120
        - SelfHealingHeartbeatMissing: absent(selfhealing_heartbeat_timestamp_seconds) == 1
    """
    import time

    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager.get_error_budget_config()

        if not config.get("heartbeat_enabled", True):
            logger.debug(
                "heartbeat.disabled",
                component=component,
            )
            return {
                "success": True,
                "component": component,
                "status": "disabled",
                "timestamp": time.time(),
            }

        from selfhealing.services.metrics.recorders import emit_heartbeat

        emit_heartbeat(component=component)

        current_time = time.time()
        logger.debug(
            "heartbeat.emitted",
            component=component,
            current_time=current_time,
        )

        return {
            "success": True,
            "component": component,
            "status": "alive",
            "timestamp": current_time,
            "interval_seconds": config.get("heartbeat_interval_seconds", 60),
            "timeout_seconds": config.get("heartbeat_timeout_seconds", 120),
        }

    except Exception as e:
        logger.exception(
            "heartbeat.failed_emit_heartbeat",
            error=e,
        )
        try:
            from selfhealing.services.metrics.recorders import emit_heartbeat

            emit_heartbeat(component=f"{component}_degraded")
        except Exception:
            pass

        return {
            "success": False,
            "component": component,
            "status": "error",
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.notify_failsafe_recovery",
    queue="monitoring",
    max_retries=1,
    time_limit=30,
    soft_time_limit=25,
)
def notify_failsafe_recovery(
    self,
    component: str,
    downtime_seconds: float,
    recovery_reason: str = "System recovered automatically",
) -> dict:
    """
    Send recovery notification when fail-safe mode is deactivated.

    This task should be called when the system transitions from fail-safe
    mode back to normal operation.

    Args:
        component: The component that recovered
        downtime_seconds: How long the component was in fail-safe mode
        recovery_reason: Why the system recovered

    Returns:
        Dictionary with notification status
    """
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager.get_error_budget_config()

        if not config.get("recovery_alert_enabled", True):
            logger.info(
                "recovery.recovery_alert_disabled_skipping",
                component=component,
            )
            return {
                "success": True,
                "component": component,
                "status": "disabled",
            }

        from selfhealing.services.metrics.recorders import (
            record_failsafe_recovered,
            record_recovery_alert,
        )

        record_recovery_alert(component=component)
        record_failsafe_recovered(component=component)

        try:
            from selfhealing.factory import ProviderRegistry

            alert_adapter = ProviderRegistry.get_alert_adapter()

            if hasattr(alert_adapter, "alert_failsafe_recovered"):
                alert_adapter.alert_failsafe_recovered(
                    component=component,
                    downtime_seconds=downtime_seconds,
                    recovery_reason=recovery_reason,
                )
                logger.info(
                    "recovery.sent_recovery_alert",
                    component=component,
                    downtime_seconds=downtime_seconds,
                )
            else:
                logger.warning("recovery.alert_adapter_support_recovery")
        except Exception as adapter_error:
            logger.warning(
                "recovery.send_alert_via_adapter",
                adapter_error=adapter_error,
            )

        return {
            "success": True,
            "component": component,
            "downtime_seconds": downtime_seconds,
            "recovery_reason": recovery_reason,
            "alert_sent": True,
        }

    except Exception as e:
        logger.exception(
            "recovery.failed_send_recovery_notification",
            error=e,
        )
        return {
            "success": False,
            "component": component,
            "error": str(e),
        }
