"""
🧹 청소부 레인 (Cleanup & Expire) Celery Tasks

Thin Task, Fat Service 원칙:
- 이 파일의 함수들은 단순 위임자 역할만 수행
- 모든 비즈니스 로직은 CleanupService에서 처리

Tasks:
1. archive_old_dlq_entries - 30일 이상 된 해결된 DLQ 항목 아카이브
2. cleanup_expired_config - 만료된 Pending Config 항목 정리
3. expire_approval_requests - 72시간 이상 대기 중인 승인 요청 만료
4. purge_archived_dlq_entries - 90일 이상 된 아카이브 항목 영구 삭제 (고위험)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# =============================================================================
# Thin Task Wrappers
# =============================================================================


def archive_old_dlq_entries(older_than_days: int = 30) -> Dict[str, Any]:
    """
    30일 이상 된 해결된 DLQ 항목을 아카이브.

    This function is a thin wrapper that delegates to CleanupService.
    All business logic is handled in the service layer.

    Args:
        older_than_days: 아카이브 기준 일수 (기본 30일)

    Returns:
        dict: {
            "success": bool,
            "archived_count": int,
            "older_than_days": int,
        }
    """
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.archive_old_dlq_entries(older_than_days=older_than_days)
        return result.to_dict()

    except Exception as e:
        logger.error(f"[CleanupTask] archive_old_dlq_entries failed: {e}")
        raise


def cleanup_expired_config(older_than_hours: int = 24) -> Dict[str, Any]:
    """
    만료된 Pending Config 항목 정리.

    This function is a thin wrapper that delegates to CleanupService.

    Args:
        older_than_hours: 만료 기준 시간 (기본 24시간)

    Returns:
        dict: {
            "success": bool,
            "expired_count": int,
            "older_than_hours": int,
        }
    """
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.cleanup_expired_config(older_than_hours=older_than_hours)
        return result.to_dict()

    except Exception as e:
        logger.error(f"[CleanupTask] cleanup_expired_config failed: {e}")
        raise


def expire_approval_requests(older_than_hours: int = 72) -> Dict[str, Any]:
    """
    72시간 이상 대기 중인 승인 요청 만료 처리.

    This function is a thin wrapper that delegates to CleanupService.

    Args:
        older_than_hours: 만료 기준 시간 (기본 72시간)

    Returns:
        dict: {
            "success": bool,
            "expired_count": int,
            "older_than_hours": int,
        }
    """
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.expire_approval_requests(older_than_hours=older_than_hours)
        return result.to_dict()

    except Exception as e:
        logger.error(f"[CleanupTask] expire_approval_requests failed: {e}")
        raise


def purge_archived_dlq_entries(
    older_than_days: int = 90,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    90일 이상 된 아카이브 항목을 영구 삭제.

    ⚠️ 고위험: 사전 승인 필수, 복구 불가

    This function is a thin wrapper that delegates to CleanupService.

    Args:
        older_than_days: 삭제 기준 일수 (기본 90일)
        dry_run: True면 실제 삭제하지 않고 대상 수만 반환

    Returns:
        dict: {
            "success": bool,
            "purged_count": int,
            "older_than_days": int,
            "warning": str,
        }
    """
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.purge_archived_dlq_entries(
            older_than_days=older_than_days,
            dry_run=dry_run,
        )
        return result.to_dict()

    except Exception as e:
        logger.error(f"[CleanupTask] purge_archived_dlq_entries failed: {e}")
        raise


# =============================================================================
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task

    @shared_task(
        name="selfhealing.archive_old_dlq_entries",
        bind=True,
        max_retries=2,
        default_retry_delay=300,
    )
    def archive_old_dlq_entries_task(self, older_than_days: int = 30):
        """Celery task wrapper for archive_old_dlq_entries."""
        return archive_old_dlq_entries(older_than_days)

    @shared_task(
        name="selfhealing.cleanup_expired_config",
        bind=True,
        max_retries=2,
        default_retry_delay=300,
    )
    def cleanup_expired_config_task(self, older_than_hours: int = 24):
        """Celery task wrapper for cleanup_expired_config."""
        return cleanup_expired_config(older_than_hours)

    @shared_task(
        name="selfhealing.expire_approval_requests",
        bind=True,
        max_retries=2,
        default_retry_delay=300,
    )
    def expire_approval_requests_task(self, older_than_hours: int = 72):
        """Celery task wrapper for expire_approval_requests."""
        return expire_approval_requests(older_than_hours)

    @shared_task(
        name="selfhealing.purge_archived_dlq_entries",
        bind=True,
        max_retries=1,  # 고위험 작업은 재시도 제한
        default_retry_delay=600,
    )
    def purge_archived_dlq_entries_task(
        self, older_than_days: int = 90, dry_run: bool = False
    ):
        """Celery task wrapper for purge_archived_dlq_entries."""
        return purge_archived_dlq_entries(older_than_days, dry_run)

    CELERY_TASKS_AVAILABLE = True

except ImportError:
    logger.debug("[CleanupTasks] Celery not available, skipping task registration")
    CELERY_TASKS_AVAILABLE = False


# =============================================================================
# Beat Schedule 정의
# =============================================================================


def get_cleanup_beat_schedule() -> Dict[str, Any]:
    """
    청소부 레인 Beat Schedule 반환.

    Returns:
        dict: Celery Beat Schedule 설정
    """
    try:
        from celery.schedules import crontab

        return {
            # 매일 02:30 - 만료 설정 정리
            "cleanup-expired-config": {
                "task": "selfhealing.cleanup_expired_config",
                "schedule": crontab(hour=2, minute=30),
                "options": {"queue": "maintenance"},
                "kwargs": {"older_than_hours": 24},
            },
            # 매일 03:00 - DLQ 아카이브
            "archive-old-dlq-entries": {
                "task": "selfhealing.archive_old_dlq_entries",
                "schedule": crontab(hour=3, minute=0),
                "options": {"queue": "maintenance"},
                "kwargs": {"older_than_days": 30},
            },
            # 매일 06:00 - 승인 요청 만료
            "expire-approval-requests": {
                "task": "selfhealing.expire_approval_requests",
                "schedule": crontab(hour=6, minute=0),
                "options": {"queue": "maintenance"},
                "kwargs": {"older_than_hours": 72},
            },
            # 매주 일요일 04:00 - DLQ 영구 삭제 (고위험)
            "purge-archived-dlq-entries": {
                "task": "selfhealing.purge_archived_dlq_entries",
                "schedule": crontab(hour=4, minute=0, day_of_week=0),
                "options": {"queue": "critical_maintenance"},
                "kwargs": {"older_than_days": 90},
            },
        }
    except ImportError:
        logger.debug("[CleanupTasks] Celery not available for beat schedule")
        return {}


# =============================================================================
# Backward Compatibility - Legacy Class Aliases
# =============================================================================
# 기존 클래스 기반 태스크를 사용하는 코드를 위한 호환성 레이어


class _LegacyTaskWrapper:
    """Legacy class-based task wrapper for backward compatibility."""

    def __init__(self, name: str, func):
        self.name = name
        self._func = func

    def run(self, *args, **kwargs):
        return self._func(*args, **kwargs)


# Legacy aliases
ArchiveOldDLQEntriesTask = _LegacyTaskWrapper(
    "selfhealing.archive_old_dlq_entries", archive_old_dlq_entries
)
CleanupExpiredConfigTask = _LegacyTaskWrapper(
    "selfhealing.cleanup_expired_config", cleanup_expired_config
)
ExpireApprovalRequestsTask = _LegacyTaskWrapper(
    "selfhealing.expire_approval_requests", expire_approval_requests
)
PurgeArchivedDLQEntriesTask = _LegacyTaskWrapper(
    "selfhealing.purge_archived_dlq_entries", purge_archived_dlq_entries
)

# Legacy task list
CLEANUP_TASKS = [
    ArchiveOldDLQEntriesTask,
    CleanupExpiredConfigTask,
    ExpireApprovalRequestsTask,
    PurgeArchivedDLQEntriesTask,
]


def register_cleanup_tasks_with_celery(app):
    """
    Celery app에 청소부 레인 태스크 등록.

    Legacy compatibility function. With the new shared_task approach,
    tasks are automatically registered when the module is imported.

    Usage:
        from celery import Celery
        from selfhealing.tasks.cleanup_tasks import register_cleanup_tasks_with_celery

        app = Celery('myproject')
        register_cleanup_tasks_with_celery(app)
    """
    logger.info(
        "[CleanupTasks] register_cleanup_tasks_with_celery called - "
        "tasks are now auto-registered via shared_task"
    )


__all__ = [
    # Thin wrapper functions
    "archive_old_dlq_entries",
    "cleanup_expired_config",
    "expire_approval_requests",
    "purge_archived_dlq_entries",
    # Beat schedule
    "get_cleanup_beat_schedule",
    # Legacy compatibility
    "ArchiveOldDLQEntriesTask",
    "CleanupExpiredConfigTask",
    "ExpireApprovalRequestsTask",
    "PurgeArchivedDLQEntriesTask",
    "CLEANUP_TASKS",
    "register_cleanup_tasks_with_celery",
    # Service re-exports (for testing convenience)
    "CleanupResult",
    "CleanupService",
    "get_cleanup_service",
    "reset_cleanup_service",
]


# =============================================================================
# Lazy Service Re-exports
# =============================================================================


def __getattr__(name: str):
    """Lazy import for service types to avoid import chain issues."""
    _lazy_service_imports = {
        "CleanupResult",
        "CleanupService",
        "get_cleanup_service",
        "reset_cleanup_service",
    }
    if name in _lazy_service_imports:
        # Import directly to avoid services/__init__.py
        import importlib
        cs_module = importlib.import_module("selfhealing.services.cleanup_service")
        return getattr(cs_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
