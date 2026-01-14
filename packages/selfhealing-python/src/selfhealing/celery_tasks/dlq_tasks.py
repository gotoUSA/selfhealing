"""
DLQ Celery Tasks

Tasks for replaying failed operations from the Dead Letter Queue.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.conditional_replay_on_circuit_close",
    queue="dlq_processing",
    max_retries=0,
    time_limit=300,
    soft_time_limit=290,
    acks_late=True,
)
def conditional_replay_on_circuit_close(self, service_name: str, max_items: int = 50) -> dict:
    """
    Trigger conditional replay when a circuit breaker closes.

    This task is called by CircuitBreakerService.force_close() when
    trigger_replay=True is specified.

    Replays DLQ entries that failed due to the recovered service.

    Args:
        service_name: Name of the service that recovered
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with replay result summary
    """
    from selfhealing.services import get_replay_service

    logger.info(f"[Circuit Recovery] Starting conditional replay for '{service_name}', max_items={max_items}")

    try:
        service = get_replay_service()
        result = service.replay_on_circuit_close(
            service_name=service_name,
            max_items=max_items,
        )

        logger.info(
            f"[Circuit Recovery] Completed for '{service_name}': "
            f"total={result.total}, success={result.success_count}, "
            f"failed={result.failed_count}"
        )

        return {
            "success": True,
            "service_name": service_name,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
        }

    except Exception as e:
        logger.error(
            f"[Circuit Recovery] Failed for '{service_name}': {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.replay_single_dlq_entry",
    queue="dlq_processing",
    max_retries=0,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def replay_single_dlq_entry(self, dlq_id: int) -> dict:
    """
    Replay a single DLQ entry.

    This task is triggered by operators via admin UI or API.

    Args:
        dlq_id: ID of the FailedOperation to replay

    Returns:
        Dictionary with replay result
    """
    from selfhealing.services import get_replay_service

    logger.info(f"[DLQ Replay] Starting replay for DLQ entry: {dlq_id}")

    try:
        service = get_replay_service()
        result = service.replay_single(dlq_id)

        if result.success:
            logger.info(f"[DLQ Replay] Successfully replayed DLQ entry {dlq_id}")
            return {
                "success": True,
                "dlq_id": dlq_id,
                "message": result.message,
                "data": result.data,
            }
        else:
            logger.warning(f"[DLQ Replay] Failed to replay DLQ entry {dlq_id}: {result.error}")
            return {
                "success": False,
                "dlq_id": dlq_id,
                "error": result.error,
            }

    except Exception as e:
        logger.error(f"[DLQ Replay] Unexpected error replaying DLQ entry {dlq_id}: {e}")
        return {
            "success": False,
            "dlq_id": dlq_id,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.replay_batch_by_failure_type",
    queue="dlq_processing",
    max_retries=0,
    time_limit=600,
    soft_time_limit=580,
    acks_late=True,
)
def replay_batch_by_failure_type(
    self,
    failure_type: str,
    max_items: int = 100,
) -> dict:
    """
    Replay all pending DLQ entries of a specific failure type.

    This task is used for batch recovery after system issues are resolved.

    Args:
        failure_type: The failure type to filter by
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with batch replay summary
    """
    from selfhealing.services import get_replay_service

    logger.info(f"[DLQ Batch Replay] Starting batch replay for failure_type={failure_type}, max_items={max_items}")

    try:
        service = get_replay_service()
        result = service.replay_batch(
            failure_type=failure_type,
            max_items=max_items,
        )

        logger.info(
            f"[DLQ Batch Replay] Completed: total={result.total}, "
            f"success={result.success_count}, failed={result.failed_count}"
        )

        return {
            "success": True,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "skipped_count": result.skipped_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Batch Replay] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.replay_batch_by_domain",
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

    This task is used for domain-wide recovery operations.

    Args:
        domain: The domain to filter by (payment, point, inventory, webhook, notification)
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with batch replay summary
    """
    from selfhealing.services import get_replay_service

    logger.info(f"[DLQ Batch Replay] Starting batch replay for domain={domain}, max_items={max_items}")

    try:
        service = get_replay_service()
        result = service.replay_batch(
            domain=domain,
            max_items=max_items,
        )

        logger.info(
            f"[DLQ Batch Replay] Completed: total={result.total}, "
            f"success={result.success_count}, failed={result.failed_count}"
        )

        return {
            "success": True,
            "domain": domain,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Batch Replay] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.cleanup_resolved_dlq_entries",
    queue="maintenance",
    max_retries=1,
    time_limit=300,
    soft_time_limit=290,
)
def cleanup_resolved_dlq_entries(self, days_old: int = 30) -> dict:
    """
    Archive old resolved DLQ entries (soft-delete, NOT hard delete).

    This task runs periodically to archive old DLQ entries.
    Entries are marked as ARCHIVED instead of deleted for audit trail.

    Retention Policy:
    - Expired entries: mark as EXPIRED
    - Old resolved/rejected: mark as ARCHIVED (soft-delete)
    - Never hard delete for compliance (payment/point records)

    Args:
        days_old: Archive entries older than this many days

    Returns:
        Dictionary with cleanup summary
    """
    from selfhealing.services import get_dlq_service

    logger.info(f"[DLQ Cleanup] Starting cleanup of entries older than {days_old} days")

    try:
        dlq_service = get_dlq_service()
        result = dlq_service.cleanup_old_entries(days_old=days_old)

        logger.info(
            f"[DLQ Cleanup] Completed: expired={result.get('expired_count', 0)}, "
            f"archived={result.get('archived_count', 0)}"
        )

        return {
            "success": True,
            **result,
        }

    except Exception as e:
        logger.error(f"[DLQ Cleanup] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }
