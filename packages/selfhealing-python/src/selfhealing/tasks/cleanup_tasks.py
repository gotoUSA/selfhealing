"""
🧹 청소부 레인 (Cleanup & Expire) Celery Tasks

Phase 2 구현: 자율 운영 청소부 레인 태스크들

Tasks:
1. ArchiveOldDLQEntriesTask - 30일 이상 된 해결된 DLQ 항목 아카이브
2. CleanupExpiredConfigTask - 만료된 Pending Config 항목 정리
3. ExpireApprovalRequestsTask - 72시간 이상 대기 중인 승인 요청 만료
4. PurgeArchivedDLQEntriesTask - 90일 이상 된 아카이브 항목 영구 삭제 (고위험)

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §3, §4
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from selfhealing.tasks.base import BaseNotifyingTask
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Task 1: Archive Old DLQ Entries (P1)
# =============================================================================


class ArchiveOldDLQEntriesTask(BaseNotifyingTask):
    """
    30일 이상 된 해결된 DLQ 항목을 아카이브.
    
    스케줄: 매일 03:00
    큐: maintenance
    알림: 일일 요약에 포함 (AGGREGATED)
    
    Args:
        older_than_days: 아카이브 기준 일수 (기본 30일)
    
    Returns:
        dict: {
            "success": bool,
            "archived_count": int,
            "older_than_days": int,
        }
    """

    name = "selfhealing.archive_old_dlq_entries"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
        cooldown_seconds=86400,  # 24시간
    )

    def run(self, older_than_days: int = 30) -> Dict[str, Any]:
        """아카이브 태스크 실행."""
        logger.info(
            f"[ArchiveOldDLQEntries] Starting archive for entries "
            f"older than {older_than_days} days"
        )
        
        try:
            from selfhealing.services.dlq_service import get_dlq_service
            
            service = get_dlq_service()
            count = service.archive_old_entries(older_than_days=older_than_days)
            
            logger.info(f"[ArchiveOldDLQEntries] Archived {count} entries")
            
            return {
                "success": True,
                "archived_count": count,
                "older_than_days": older_than_days,
            }
            
        except Exception as e:
            logger.error(f"[ArchiveOldDLQEntries] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "older_than_days": older_than_days,
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ DLQ 아카이브 실패: {result['error']}"
        return (
            f"📦 DLQ 아카이브: {result['archived_count']}건 "
            f"({result['older_than_days']}일 경과)"
        )


# =============================================================================
# Task 2: Cleanup Expired Config (P2)
# =============================================================================


class CleanupExpiredConfigTask(BaseNotifyingTask):
    """
    만료된 Pending Config 항목 정리.
    
    스케줄: 매일 02:30
    큐: maintenance
    알림: 일일 요약에 포함 (AGGREGATED)
    
    Args:
        older_than_hours: 만료 기준 시간 (기본 24시간)
    
    Returns:
        dict: {
            "success": bool,
            "expired_count": int,
            "older_than_hours": int,
        }
    """

    name = "selfhealing.cleanup_expired_config"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
    )

    def run(self, older_than_hours: int = 24) -> Dict[str, Any]:
        """만료 설정 정리 태스크 실행."""
        logger.info(
            f"[CleanupExpiredConfig] Starting cleanup for configs "
            f"older than {older_than_hours} hours"
        )
        
        try:
            from selfhealing.services.pending_config import get_pending_config_service
            
            service = get_pending_config_service()
            count = service.cleanup_expired(max_age_hours=older_than_hours)
            
            logger.info(f"[CleanupExpiredConfig] Cleaned up {count} expired configs")
            
            return {
                "success": True,
                "expired_count": count,
                "older_than_hours": older_than_hours,
            }
            
        except Exception as e:
            logger.error(f"[CleanupExpiredConfig] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "older_than_hours": older_than_hours,
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 만료 설정 정리 실패: {result['error']}"
        return f"🧹 만료 설정 정리: {result['expired_count']}건"


# =============================================================================
# Task 3: Expire Approval Requests (P2)
# =============================================================================


class ExpireApprovalRequestsTask(BaseNotifyingTask):
    """
    72시간 이상 대기 중인 승인 요청 만료 처리.
    
    스케줄: 매일 06:00
    큐: maintenance
    알림: 5건 이상일 때만 (임계값 기반)
    
    Args:
        older_than_hours: 만료 기준 시간 (기본 72시간)
    
    Returns:
        dict: {
            "success": bool,
            "expired_count": int,
            "older_than_hours": int,
        }
    """

    name = "selfhealing.expire_approval_requests"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        threshold=5,  # 5건 이상일 때만 알림
        threshold_field="expired_count",
        default_severity="warning",
    )

    def run(self, older_than_hours: int = 72) -> Dict[str, Any]:
        """승인 요청 만료 태스크 실행."""
        logger.info(
            f"[ExpireApprovalRequests] Starting expiration for requests "
            f"older than {older_than_hours} hours"
        )
        
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            
            manager = get_runtime_config_manager()
            count = manager.expire_old_requests()
            
            logger.info(f"[ExpireApprovalRequests] Expired {count} approval requests")
            
            return {
                "success": True,
                "expired_count": count,
                "older_than_hours": older_than_hours,
            }
            
        except Exception as e:
            logger.error(f"[ExpireApprovalRequests] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "older_than_hours": older_than_hours,
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 승인 요청 만료 처리 실패: {result['error']}"
        return (
            f"⏰ 승인 요청 만료: {result['expired_count']}건 "
            f"({result['older_than_hours']}시간 경과)"
        )


# =============================================================================
# Task 4: Purge Archived DLQ Entries (P1) ⚠️ 고위험
# =============================================================================


class PurgeArchivedDLQEntriesTask(BaseNotifyingTask):
    """
    90일 이상 된 아카이브 항목을 영구 삭제.
    
    ⚠️ 고위험: 사전 승인 필수, 복구 불가
    
    스케줄: 매주 일요일 04:00
    큐: critical_maintenance
    알림: 사전 승인 필수 (BEFORE)
    특이사항: Emergency Level 3에서도 승인 필요
    
    Args:
        older_than_days: 삭제 기준 일수 (기본 90일)
    
    Returns:
        dict: {
            "success": bool,
            "purged_count": int,
            "older_than_days": int,
            "warning": str,
        }
    """

    name = "selfhealing.purge_archived_dlq_entries"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.BEFORE,
        requires_approval=True,
        default_severity="critical",
        cooldown_seconds=3600,  # 1시간
        escalate_on_emergency=False,  # Emergency Level 3에서도 승인 필요
        channels=["slack", "email"],
    )

    def run(self, older_than_days: int = 90) -> Dict[str, Any]:
        """영구 삭제 태스크 실행."""
        logger.warning(
            f"[PurgeArchivedDLQEntries] ⚠️ Starting PERMANENT DELETE for entries "
            f"older than {older_than_days} days"
        )
        
        try:
            from selfhealing.services.dlq_service import get_dlq_service
            
            service = get_dlq_service()
            count = service.purge_archived(older_than_days=older_than_days)
            
            logger.warning(
                f"[PurgeArchivedDLQEntries] ⚠️ PERMANENTLY DELETED {count} entries"
            )
            
            return {
                "success": True,
                "purged_count": count,
                "older_than_days": older_than_days,
                "warning": "PERMANENT DELETION - UNRECOVERABLE",
            }
            
        except Exception as e:
            logger.error(f"[PurgeArchivedDLQEntries] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "older_than_days": older_than_days,
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ DLQ 영구 삭제 실패: {result['error']}"
        return (
            f"⚠️ DLQ 영구 삭제: {result['purged_count']}건 (복구 불가!)"
        )

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """항상 critical 반환."""
        return "critical"


# =============================================================================
# Task Registry (Celery 등록용)
# =============================================================================


# 태스크 클래스 목록 (Celery 등록 시 사용)
CLEANUP_TASKS = [
    ArchiveOldDLQEntriesTask,
    CleanupExpiredConfigTask,
    ExpireApprovalRequestsTask,
    PurgeArchivedDLQEntriesTask,
]


# =============================================================================
# Celery shared_task 래퍼 (Django 프로젝트 연동용)
# =============================================================================


def register_cleanup_tasks_with_celery(app):
    """
    Celery app에 청소부 레인 태스크 등록.
    
    Usage:
        from celery import Celery
        from selfhealing.tasks.cleanup_tasks import register_cleanup_tasks_with_celery
        
        app = Celery('myproject')
        register_cleanup_tasks_with_celery(app)
    """
    for task_class in CLEANUP_TASKS:
        # Celery Task 클래스로 래핑
        wrapped = type(
            task_class.__name__,
            (task_class, app.Task),
            {
                "name": task_class.name,
                "bind": True,
            },
        )
        app.register_task(wrapped())
        logger.info(f"[CleanupTasks] Registered: {task_class.name}")


# =============================================================================
# Beat Schedule 정의
# =============================================================================


def get_cleanup_beat_schedule() -> Dict[str, Any]:
    """
    청소부 레인 Beat Schedule 반환.
    
    Returns:
        dict: Celery Beat Schedule 설정
    """
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


__all__ = [
    # Task Classes
    "ArchiveOldDLQEntriesTask",
    "CleanupExpiredConfigTask",
    "ExpireApprovalRequestsTask",
    "PurgeArchivedDLQEntriesTask",
    # Registry
    "CLEANUP_TASKS",
    "register_cleanup_tasks_with_celery",
    "get_cleanup_beat_schedule",
]
