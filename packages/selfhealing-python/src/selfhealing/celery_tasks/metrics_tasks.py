"""
Metrics Celery Tasks

Tasks for observability and SLA monitoring.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.collect_self_healing_metrics",
    queue="monitoring",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def collect_self_healing_metrics(self) -> dict:
    """
    Periodic task to collect and update self-healing Prometheus metrics.

    Updates gauge metrics that require database queries:
    - DLQ pending counts by domain
    - DLQ items by status
    - Circuit breaker states
    - Retry success rates

    This task should be scheduled to run every minute.

    Returns:
        Dictionary with collected metric values
    """
    from selfhealing.services import collect_all_metrics

    logger.debug("metrics")

    try:
        metrics = collect_all_metrics()

        logger.debug(
            "metrics.collection_complete_values",
            value=sum(metrics.get('dlq_pending_by_domain', {}).values()),
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
    name="selfhealing.celery_tasks.check_and_report_sla_breaches",
    queue="monitoring",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_and_report_sla_breaches(self) -> dict:
    """
    Periodic task to check for SLA breaches and record metrics.

    SLA thresholds are configured in services/self_healing/config.py.
    See SLAThresholds class for default values and customization.

    This task should be scheduled to run every 5 minutes.

    Returns:
        Dictionary with SLA breach information
    """
    from selfhealing.services import get_dlq_service, record_sla_breach

    logger.debug("sla_check.breach_check_started")

    try:
        dlq_service = get_dlq_service()
        breached_entries = dlq_service.get_sla_breached_entries()

        breaches_by_domain: dict[str, int] = {}

        for entry in breached_entries:
            domain = entry.domain
            breaches_by_domain[domain] = breaches_by_domain.get(domain, 0) + 1
            record_sla_breach(domain)

        total_breaches = sum(breaches_by_domain.values())

        if total_breaches > 0:
            logger.warning(
                "sla_check_found_sla",
                total_breaches=total_breaches,
                breaches_by_domain=breaches_by_domain,
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
