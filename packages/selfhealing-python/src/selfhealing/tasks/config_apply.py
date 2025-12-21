"""
Configuration Apply Tasks.

Celery tasks for applying scheduled/delayed configuration changes.

Tasks:
- apply_pending_config_changes: Apply all due pending changes
- apply_graceful_config_change: Wait for in-progress ops, then apply
"""

import logging
from datetime import datetime, timezone

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="selfhealing.apply_pending_config_changes",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def apply_pending_config_changes(self):
    """
    Apply all pending configuration changes that are due.

    This task should be scheduled to run periodically (e.g., every 5 seconds)
    via Celery Beat.

    Example Celery Beat config:
        CELERY_BEAT_SCHEDULE = {
            'apply-pending-configs': {
                'task': 'selfhealing.apply_pending_config_changes',
                'schedule': 5.0,  # Every 5 seconds
            },
        }
    """
    from selfhealing.services.pending_config import get_pending_config_service
    from selfhealing.services.runtime_config import get_runtime_config_manager

    try:
        pending_service = get_pending_config_service()
        config_manager = get_runtime_config_manager()

        # Get all due changes
        due_changes = pending_service.get_due_changes()

        if not due_changes:
            return {"status": "success", "applied": 0, "message": "No pending changes due"}

        applied_count = 0
        failed_count = 0
        results = []

        for change in due_changes:
            try:
                # Apply the change
                result = config_manager.apply_pending_change(change.id)

                if result.get("status") == "applied":
                    applied_count += 1
                    logger.info(f"[ConfigTask] Applied pending change {change.id}")
                else:
                    failed_count += 1
                    logger.error(f"[ConfigTask] Failed to apply {change.id}: {result.get('error')}")

                results.append(
                    {
                        "id": change.id,
                        "config_type": change.config_type,
                        "status": result.get("status"),
                    }
                )
            except Exception as e:
                failed_count += 1
                pending_service.mark_failed(change.id, str(e))
                logger.error(f"[ConfigTask] Exception applying {change.id}: {e}", exc_info=True)
                results.append(
                    {
                        "id": change.id,
                        "config_type": change.config_type,
                        "status": "error",
                        "error": str(e),
                    }
                )

        return {
            "status": "success",
            "applied": applied_count,
            "failed": failed_count,
            "results": results,
        }

    except Exception as e:
        logger.error(f"[ConfigTask] Error in apply_pending_config_changes: {e}", exc_info=True)
        raise self.retry(exc=e)


@shared_task(
    name="selfhealing.apply_graceful_config_change",
    bind=True,
    max_retries=10,
    default_retry_delay=5,
)
def apply_graceful_config_change(self, pending_id: str, max_wait_seconds: int = 60):
    """
    Apply a configuration change gracefully.

    Waits for in-progress operations to complete before applying.
    Uses exponential backoff with max retries.

    Args:
        pending_id: ID of the pending configuration change
        max_wait_seconds: Maximum time to wait for in-progress ops
    """
    from selfhealing.services.pending_config import get_pending_config_service
    from selfhealing.services.runtime_config import get_runtime_config_manager

    try:
        pending_service = get_pending_config_service()
        config_manager = get_runtime_config_manager()

        # Get the pending change
        change = pending_service.get_pending_change(pending_id)
        if not change:
            return {"status": "error", "error": f"Pending change {pending_id} not found"}

        if change.status != "pending":
            return {"status": "error", "error": f"Change {pending_id} is not pending"}

        # Check if we've exceeded max wait time
        created = datetime.fromisoformat(change.created_at)
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()

        if elapsed > max_wait_seconds:
            # Waited long enough, apply anyway
            logger.warning(f"[ConfigTask] Graceful wait exceeded {max_wait_seconds}s for {pending_id}, " f"applying anyway")
            result = config_manager.apply_pending_change(pending_id)
            return result

        # Check if there are in-progress operations
        # For now, we use a simple heuristic - check if there are active requests
        # In production, this could check:
        # - Active circuit breaker half-open tests
        # - In-flight retry operations
        # - Pending DLQ replays
        has_in_progress = _check_in_progress_operations(change.config_type)

        if has_in_progress:
            logger.info(f"[ConfigTask] Waiting for in-progress ops for {pending_id}, " f"retry {self.request.retries + 1}")
            raise self.retry(countdown=min(5 * (self.request.retries + 1), 30))

        # No in-progress operations, apply the change
        result = config_manager.apply_pending_change(pending_id)
        return result

    except Exception as e:
        if self.request.retries >= self.max_retries:
            # Max retries reached, apply anyway
            logger.warning(f"[ConfigTask] Max retries reached for {pending_id}, applying anyway")
            try:
                from selfhealing.services.runtime_config import get_runtime_config_manager

                config_manager = get_runtime_config_manager()
                return config_manager.apply_pending_change(pending_id)
            except Exception as apply_error:
                from selfhealing.services.pending_config import get_pending_config_service

                pending_service = get_pending_config_service()
                pending_service.mark_failed(pending_id, str(apply_error))
                raise

        raise self.retry(exc=e)


def _check_in_progress_operations(config_type: str) -> bool:
    """
    Check if there are in-progress operations for a config type.

    This is a simplified implementation. In production, you might:
    - Check active circuit breaker half-open tests
    - Check running retry operations
    - Check in-flight DLQ replays
    - Use metrics/gauges to determine activity

    Args:
        config_type: The configuration type

    Returns:
        True if there are in-progress operations
    """
    # For now, always return False (no blocking)
    # This can be enhanced to actually check for in-progress operations

    if config_type == "circuit_breaker":
        # Could check: are there any half-open circuit breakers being tested?
        pass
    elif config_type == "dlq":
        # Could check: is there an active DLQ replay in progress?
        pass
    elif config_type == "retry":
        # Could check: are there active retry loops?
        pass

    return False


@shared_task(name="selfhealing.cleanup_expired_config_changes")
def cleanup_expired_config_changes(max_age_hours: int = 24):
    """
    Cleanup old pending changes that were never applied.

    Should be scheduled to run periodically (e.g., daily).
    """
    from selfhealing.services.pending_config import get_pending_config_service

    try:
        pending_service = get_pending_config_service()
        count = pending_service.cleanup_expired(max_age_hours)

        return {
            "status": "success",
            "expired_count": count,
        }
    except Exception as e:
        logger.error(f"[ConfigTask] Error cleaning up expired changes: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
        }
