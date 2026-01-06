"""
DLQ Replay Celery Tasks.

These tasks handle replay of failed operations from the Dead Letter Queue.

Usage in CELERY_BEAT_SCHEDULE:
    'cleanup-dlq-entries': {
        'task': 'selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries',
        'schedule': 86400.0,  # Daily
    },
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.replay_single_dlq_entry",
    queue="dlq_processing",
    max_retries=0,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def replay_single_dlq_entry(self, dlq_id: int) -> dict:
    """
    Replay a single DLQ entry.

    This task delegates to ReplayService which handles all safety checks:
    - Kill Switch
    - Emergency Level (LEVEL_2+)
    - ErrorBudgetGate

    Args:
        dlq_id: ID of the FailedOperation to replay

    Returns:
        Dictionary with replay result
    """
    logger.info(f"[DLQ Replay Task] Starting replay for DLQ entry: {dlq_id}")

    try:
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService()
        result = service.replay_single(dlq_id)

        return {
            "success": result.success,
            "dlq_id": dlq_id,
            "message": result.message if result.success else "",
            "error": result.error,
            "data": result.data,
        }

    except Exception as e:
        logger.error(f"[DLQ Replay Task] Unexpected error replaying DLQ entry {dlq_id}: {e}")
        return {
            "success": False,
            "dlq_id": dlq_id,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.replay_batch_by_domain",
    queue="dlq_processing",
    max_retries=0,
    time_limit=600,
    soft_time_limit=580,
    acks_late=True,
)
def replay_batch_by_domain(
    self,
    domain: str,
    max_items: int = 100,
) -> dict:
    """
    Replay all pending DLQ entries for a specific domain.

    This task delegates to ReplayService which handles all safety checks:
    - Kill Switch
    - Emergency Level (LEVEL_2+)
    - ErrorBudgetGate

    Args:
        domain: The domain to filter by (payment, point, inventory, etc.)
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with batch replay summary
    """
    logger.info(
        f"[DLQ Batch Replay Task] Starting batch replay for domain={domain}, max_items={max_items}"
    )

    try:
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService()
        result = service.replay_batch(domain=domain, max_items=max_items)

        return {
            "success": result.success_count > 0 or result.total == 0,
            "domain": domain,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "skipped_count": result.skipped_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Batch Replay Task] Unexpected error: {e}")
        return {
            "success": False,
            "domain": domain,
            "error": str(e),
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries",
    queue="maintenance",
    max_retries=1,
    time_limit=300,
    soft_time_limit=290,
)
def cleanup_resolved_dlq_entries(self, days_old: int = 30) -> dict:
    """
    Archive old resolved DLQ entries.

    This task runs periodically to clean up old entries.
    Entries are marked as ARCHIVED (soft-delete) for audit trail.

    Uses ProviderRegistry for statistics repository access.

    Args:
        days_old: Archive entries older than this many days

    Returns:
        Dictionary with cleanup summary
    """
    logger.info(f"[DLQ Cleanup] Starting cleanup of entries older than {days_old} days")

    try:
        from selfhealing.factory import ProviderRegistry

        if not ProviderRegistry.has_statistics_adapter():
            logger.info("[DLQ Cleanup] No statistics adapter, skipping cleanup")
            return {
                "success": True,
                "skipped": True,
                "reason": "no_statistics_adapter",
            }

        stats_repo = ProviderRegistry.get_statistics_repo()

        archived_count = stats_repo.archive_old_entries(older_than_days=days_old)

        logger.info(f"[DLQ Cleanup] Completed: archived={archived_count}")

        return {
            "success": True,
            "archived_count": archived_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Cleanup] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }
