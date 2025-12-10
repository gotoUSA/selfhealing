"""
DLQ Replay Tasks

Celery tasks for replaying failed operations from the Dead Letter Queue.
Supports single replay, batch replay, and conditional replay on circuit breaker recovery.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §2
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="shopping.tasks.dlq_replay_tasks.replay_single_dlq_entry",
    queue="dlq_processing",
    max_retries=0,  # DLQ replay should not auto-retry
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
    name="shopping.tasks.dlq_replay_tasks.replay_batch_by_failure_type",
    queue="dlq_processing",
    max_retries=0,
    time_limit=600,  # 10 minutes for batch operations
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

    logger.info(f"[DLQ Batch Replay] Starting batch replay for failure_type={failure_type}, " f"max_items={max_items}")

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
    name="shopping.tasks.dlq_replay_tasks.replay_batch_by_domain",
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

    logger.info(f"[DLQ Batch Replay] Starting batch replay for domain={domain}, " f"max_items={max_items}")

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
    name="shopping.tasks.dlq_replay_tasks.replay_on_circuit_breaker_close",
    queue="dlq_processing",
    max_retries=0,
    time_limit=300,
    soft_time_limit=290,
    acks_late=True,
)
def replay_on_circuit_breaker_close(
    self,
    service_name: str,
    max_items: int = 50,
) -> dict:
    """
    Replay DLQ entries when a circuit breaker closes (service recovers).

    This task is triggered by the circuit breaker recovery mechanism.

    Args:
        service_name: Name of the service that recovered
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with replay summary
    """
    from selfhealing.services import get_replay_service

    logger.info(
        f"[DLQ Circuit Recovery] Circuit breaker closed for {service_name}, " f"attempting replay of up to {max_items} items"
    )

    try:
        service = get_replay_service()
        result = service.replay_on_circuit_close(
            service_name=service_name,
            max_items=max_items,
        )

        logger.info(
            f"[DLQ Circuit Recovery] Completed for {service_name}: " f"total={result.total}, success={result.success_count}"
        )

        return {
            "success": True,
            "service_name": service_name,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Circuit Recovery] Unexpected error: {e}")
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.dlq_replay_tasks.cleanup_resolved_dlq_entries",
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
    from datetime import timedelta

    from django.utils import timezone

    from shopping.models.failed_operation import FailedOperation

    logger.info(f"[DLQ Cleanup] Starting cleanup of entries older than {days_old} days")

    try:
        cutoff_date = timezone.now() - timedelta(days=days_old)

        # Get expired entries (past retention period but still pending/reviewing)
        expired_entries = FailedOperation.objects.filter(
            expires_at__lt=timezone.now(),
            status__in=[
                FailedOperation.Status.PENDING,
                FailedOperation.Status.REVIEWING,
                FailedOperation.Status.REQUIRES_REVIEW,
            ],
        )

        # Mark as expired
        expired_count = expired_entries.count()
        for entry in expired_entries:
            entry.mark_as_expired()

        # Archive old resolved/rejected entries (SOFT DELETE - not hard delete)
        old_entries = FailedOperation.objects.filter(
            created_at__lt=cutoff_date,
            status__in=[
                FailedOperation.Status.RESOLVED,
                FailedOperation.Status.REJECTED,
                FailedOperation.Status.EXPIRED,
            ],
        )
        archived_count = old_entries.count()
        for entry in old_entries:
            entry.mark_as_archived(note=f"Auto-archived after {days_old} days retention")

        logger.info(f"[DLQ Cleanup] Completed: expired={expired_count}, archived={archived_count}")

        return {
            "success": True,
            "expired_count": expired_count,
            "archived_count": archived_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Cleanup] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }
