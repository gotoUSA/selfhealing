"""
Configuration Apply Tasks.

Celery tasks for applying scheduled/delayed configuration changes.

Thin Task, Fat Service Architecture:
    - 이 파일의 Celery Task들은 단순 위임자 역할만 수행
    - 모든 비즈니스 로직은 ConfigApplyService에서 처리
    - 거버넌스 체크 (Emergency Mode)도 서비스 레이어에서 수행

Tasks:
- apply_pending_config_changes: Apply all due pending changes
- apply_graceful_config_change: Wait for in-progress ops, then apply

Reference:
- docs/self_healing/17_SYSTEM_ARCHITECTURE_DIAGRAM.md §8
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

    This task is a thin wrapper that delegates to ConfigApplyService.
    All governance checks (Emergency Mode) are performed in the service layer.

    Note:
        - Kill Switch는 체크하지 않음 (복구 퇴로 확보)
        - Emergency Mode LEVEL_2+ 시 차단

    This task should be scheduled to run periodically (e.g., every 5 seconds)
    via Celery Beat.
    """
    from selfhealing.services.execution_services import get_config_apply_service

    try:
        service = get_config_apply_service()
        result = service.apply_pending_changes()

        if result.get("status") == "blocked":
            logger.warning(f"[ConfigTask] Blocked: {result.get('reason')}")

        return result

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

    This task is a thin wrapper that delegates to ConfigApplyService.
    Waits for in-progress operations to complete before applying.

    Args:
        pending_id: ID of the pending configuration change
        max_wait_seconds: Maximum time to wait for in-progress ops
    """
    from selfhealing.services.execution_services import get_config_apply_service

    try:
        service = get_config_apply_service()
        result = service.apply_graceful_change(pending_id, max_wait_seconds)

        if result.get("status") == "blocked":
            # 비상 모드에서는 재시도하여 비상 모드 해제 후 적용
            if self.request.retries < self.max_retries:
                logger.info(f"[ConfigTask] Will retry after emergency mode ends")
                raise self.retry(countdown=30)
            return result

        if result.get("status") == "retry":
            # 진행 중인 작업이 있으면 재시도
            logger.info(
                f"[ConfigTask] Waiting for in-progress ops for {pending_id}, "
                f"retry {self.request.retries + 1}"
            )
            raise self.retry(countdown=min(5 * (self.request.retries + 1), 30))

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

