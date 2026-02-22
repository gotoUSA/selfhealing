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

import structlog
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# Thin Task Wrappers
# =============================================================================


def archive_old_dlq_entries(older_than_days: int = 30) -> dict[str, Any]:
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
        logger.error(
            f"[CleanupTask] archive_old_dlq_entries failed: {e}",
            exc_info=True,  # 스택트레이스 포함
        )
        raise


def cleanup_expired_config(older_than_hours: int = 24) -> dict[str, Any]:
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
        logger.error(
            f"[CleanupTask] cleanup_expired_config failed: {e}",
            exc_info=True,  # 스택트레이스 포함
        )
        raise


def expire_approval_requests(older_than_hours: int = 72) -> dict[str, Any]:
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
        logger.error(
            f"[CleanupTask] expire_approval_requests failed: {e}",
            exc_info=True,  # 스택트레이스 포함
        )
        raise


def purge_archived_dlq_entries(
    older_than_days: int = 90,
    dry_run: bool = False,
) -> dict[str, Any]:
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
        logger.error(
            f"[CleanupTask] purge_archived_dlq_entries failed: {e}",
            exc_info=True,  # 스택트레이스 포함
        )
        raise


def flush_expired_jwt_tokens() -> dict[str, Any]:
    """
    만료된 JWT OutstandingToken 정리.

    rest_framework_simplejwt의 flushexpiredtokens 관리 명령을 실행하여
    만료된 OutstandingToken 레코드를 DB에서 제거합니다.

    JWT 블랙리스트 연동(#217) 이후 OutstandingToken이 누적되므로
    주기적 정리가 필요합니다.

    Precondition:
        - rest_framework_simplejwt.token_blacklist가 INSTALLED_APPS에 포함

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "skipped": bool (optional, token_blacklist 미설치 시만),
        }

    Reference:
        docs/self_healing/middleware_system/217_JWT_BLACKLIST_AND_SECRETS_VALIDATION.md §7.3
    """
    try:
        from django.apps import apps

        if not apps.is_installed("rest_framework_simplejwt.token_blacklist"):
            msg = "token_blacklist 앱이 설치되지 않아 건너뜁니다."
            logger.info(
                "cleanup_task.skipped",
                msg=msg,
            )
            return {"success": True, "message": msg, "skipped": True}

        from django.core.management import call_command

        call_command("flushexpiredtokens")

        msg = "만료된 JWT OutstandingToken 정리 완료"
        logger.info(
            "cleanup_task.event",
            msg=msg,
        )
        return {"success": True, "message": msg}

    except Exception as e:
        logger.error(
            f"[CleanupTask] flush_expired_jwt_tokens failed: {e}",
            exc_info=True,
        )
        raise


# =============================================================================
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task

    from selfhealing.settings.cleanup import get_cleanup_settings

    # 모듈 로드 시점에 설정값 캐싱
    _cleanup_settings = get_cleanup_settings()

    @shared_task(
        name="selfhealing.archive_old_dlq_entries",
        bind=True,
        max_retries=_cleanup_settings.archive_dlq_max_retries,
        default_retry_delay=_cleanup_settings.archive_dlq_retry_delay,
    )
    def archive_old_dlq_entries_task(self, older_than_days: int = 30):
        """Celery task wrapper for archive_old_dlq_entries."""
        return archive_old_dlq_entries(older_than_days)

    @shared_task(
        name="selfhealing.cleanup_expired_config",
        bind=True,
        max_retries=_cleanup_settings.expired_config_max_retries,
        default_retry_delay=_cleanup_settings.expired_config_retry_delay,
    )
    def cleanup_expired_config_task(self, older_than_hours: int = 24):
        """Celery task wrapper for cleanup_expired_config."""
        return cleanup_expired_config(older_than_hours)

    @shared_task(
        name="selfhealing.expire_approval_requests",
        bind=True,
        max_retries=_cleanup_settings.approval_max_retries,
        default_retry_delay=_cleanup_settings.approval_retry_delay,
    )
    def expire_approval_requests_task(self, older_than_hours: int = 72):
        """Celery task wrapper for expire_approval_requests."""
        return expire_approval_requests(older_than_hours)

    @shared_task(
        name="selfhealing.purge_archived_dlq_entries",
        bind=True,
        max_retries=_cleanup_settings.purge_dlq_max_retries,
        default_retry_delay=_cleanup_settings.purge_dlq_retry_delay,
    )
    def purge_archived_dlq_entries_task(self, older_than_days: int = 90, dry_run: bool = False):
        """Celery task wrapper for purge_archived_dlq_entries."""
        return purge_archived_dlq_entries(older_than_days, dry_run)

    @shared_task(
        name="selfhealing.flush_expired_jwt_tokens",
        bind=True,
        max_retries=1,
        default_retry_delay=300,
    )
    def flush_expired_jwt_tokens_task(self):
        """Celery task wrapper for flush_expired_jwt_tokens."""
        return flush_expired_jwt_tokens()

    CELERY_TASKS_AVAILABLE = True

except ImportError:
    logger.debug("cleanup_tasks.celery_available_skipping_task")
    CELERY_TASKS_AVAILABLE = False


# =============================================================================
# Beat Schedule 정의
# =============================================================================


def get_cleanup_beat_schedule() -> dict[str, Any]:
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
            # 매일 02:30 - 만료된 JWT OutstandingToken 정리 (#217)
            "flush-expired-jwt-tokens": {
                "task": "selfhealing.flush_expired_jwt_tokens",
                "schedule": crontab(hour=2, minute=30),
                "options": {"queue": "maintenance"},
            },
        }
    except ImportError:
        logger.debug("cleanup_tasks.celery_available_beat_schedule")
        return {}


__all__ = [
    # Thin wrapper functions
    "archive_old_dlq_entries",
    "cleanup_expired_config",
    "expire_approval_requests",
    "purge_archived_dlq_entries",
    "flush_expired_jwt_tokens",
    # Beat schedule
    "get_cleanup_beat_schedule",
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
