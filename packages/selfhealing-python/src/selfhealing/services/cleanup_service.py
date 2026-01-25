"""
Cleanup Service

Handles cleanup and archival operations for DLQ, Config, and Approvals.

Thin Task, Fat Service 원칙:
- Task는 단순 위임자 역할
- 모든 비즈니스 로직은 이 서비스에서 처리

Audit:
- archive_old_dlq_entries: log_system_control_audit(action="archive_dlq")
- cleanup_expired_config: log_system_control_audit(action="cleanup_expired_config")
- purge_archived_dlq_entries: log_system_control_audit(action="purge_dlq_permanent|purge_dlq_dry_run")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from selfhealing.services.audit import (
    log_system_control_audit,
)

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Cleanup 작업 결과."""

    success: bool
    operation: str
    count: int = 0
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "success": self.success,
            "operation": self.operation,
            f"{self.operation}_count": self.count,
        }
        if self.error:
            result["error"] = self.error
        if self.details:
            result.update(self.details)
        return result


class CleanupService:
    """
    Cleanup 및 아카이브 작업을 처리하는 서비스.

    모든 cleanup_tasks.py 태스크의 비즈니스 로직을 담당합니다.

    Operations:
    - archive_old_dlq_entries: 30일 이상 된 해결된 DLQ 항목 아카이브
    - cleanup_expired_config: 만료된 Pending Config 항목 정리
    - expire_approval_requests: 72시간 이상 대기 중인 승인 요청 만료
    - purge_archived_dlq_entries: 90일 이상 된 아카이브 항목 영구 삭제
    """

    def archive_old_dlq_entries(
        self,
        older_than_days: int | None = None,
    ) -> CleanupResult:
        """
        30일 이상 된 해결된 DLQ 항목을 아카이브.

        Args:
            older_than_days: 아카이브 기준 일수 (None이면 Settings에서 로드)

        Returns:
            CleanupResult with archived count
        """
        if older_than_days is None:
            from selfhealing.settings.cleanup import get_cleanup_settings
            older_than_days = get_cleanup_settings().archive_older_than_days

        logger.info(
            f"[CleanupService] Archiving DLQ entries older than {older_than_days} days"
        )

        try:
            from selfhealing.services.dlq_service import get_dlq_service

            dlq_service = get_dlq_service()
            count = dlq_service.archive_old_entries(older_than_days=older_than_days)

            logger.info(f"[CleanupService] Archived {count} DLQ entries")

            # === Audit 기록: DLQ 아카이브 ===
            log_system_control_audit(
                action="archive_dlq",
                actor="system",
                old_state={"archived_count": 0},
                new_state={"archived_count": count},
                reason=f"Archive DLQ entries older than {older_than_days} days",
            )

            return CleanupResult(
                success=True,
                operation="archived",
                count=count,
                details={"older_than_days": older_than_days},
            )

        except Exception as e:
            logger.error(f"[CleanupService] Archive failed: {e}", exc_info=True)
            return CleanupResult(
                success=False,
                operation="archived",
                error=str(e),
                details={"older_than_days": older_than_days},
            )

    def cleanup_expired_config(
        self,
        older_than_hours: int | None = None,
    ) -> CleanupResult:
        """
        만료된 Pending Config 항목 정리.

        Args:
            older_than_hours: 만료 기준 시간 (None이면 Settings에서 로드)

        Returns:
            CleanupResult with expired count
        """
        if older_than_hours is None:
            from selfhealing.settings.cleanup import get_cleanup_settings
            older_than_hours = get_cleanup_settings().expired_config_hours

        logger.info(
            f"[CleanupService] Cleaning up configs older than {older_than_hours} hours"
        )

        try:
            from selfhealing.services.pending_config import get_pending_config_service

            pending_service = get_pending_config_service()
            count = pending_service.cleanup_expired(max_age_hours=older_than_hours)

            logger.info(f"[CleanupService] Cleaned up {count} expired configs")

            # === Audit 기록: 만료된 Pending Config 정리 ===
            log_system_control_audit(
                action="cleanup_expired_config",
                actor="system",
                old_state={"expired_count": 0},
                new_state={"expired_count": count},
                reason=f"Cleanup expired pending configs older than {older_than_hours} hours",
            )

            return CleanupResult(
                success=True,
                operation="expired",
                count=count,
                details={"older_than_hours": older_than_hours},
            )

        except Exception as e:
            logger.error(f"[CleanupService] Cleanup failed: {e}", exc_info=True)
            return CleanupResult(
                success=False,
                operation="expired",
                error=str(e),
                details={"older_than_hours": older_than_hours},
            )

    def expire_approval_requests(
        self,
        older_than_hours: int | None = None,
    ) -> CleanupResult:
        """
        72시간 이상 대기 중인 승인 요청 만료 처리.

        Args:
            older_than_hours: 만료 기준 시간 (None이면 Settings에서 로드)

        Returns:
            CleanupResult with expired count
        """
        if older_than_hours is None:
            from selfhealing.settings.cleanup import get_cleanup_settings
            older_than_hours = get_cleanup_settings().approval_expiry_hours

        logger.info(
            f"[CleanupService] Expiring approval requests older than "
            f"{older_than_hours} hours"
        )

        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            count = manager.expire_old_requests()

            logger.info(f"[CleanupService] Expired {count} approval requests")

            return CleanupResult(
                success=True,
                operation="expired",
                count=count,
                details={"older_than_hours": older_than_hours},
            )

        except Exception as e:
            logger.error(
                f"[CleanupService] Approval expiration failed: {e}", exc_info=True
            )
            return CleanupResult(
                success=False,
                operation="expired",
                error=str(e),
                details={"older_than_hours": older_than_hours},
            )

    def purge_archived_dlq_entries(
        self,
        older_than_days: int | None = None,
        dry_run: bool = False,
    ) -> CleanupResult:
        """
        90일 이상 된 아카이브 항목을 영구 삭제.

        ⚠️ 고위험: 사전 승인 필수, 복구 불가

        Args:
            older_than_days: 삭제 기준 일수 (None이면 Settings에서 로드)
            dry_run: True면 실제 삭제하지 않고 대상 수만 반환

        Returns:
            CleanupResult with purged count
        """
        if older_than_days is None:
            from selfhealing.settings.cleanup import get_cleanup_settings
            older_than_days = get_cleanup_settings().purge_older_than_days

        logger.warning(
            f"[CleanupService] ⚠️ Purging archived DLQ entries older than "
            f"{older_than_days} days (dry_run={dry_run})"
        )

        try:
            from selfhealing.services.dlq_service import get_dlq_service

            dlq_service = get_dlq_service()

            if dry_run:
                # dry_run 모드: 대상 수만 확인
                count = dlq_service.count_archived_older_than(
                    older_than_days=older_than_days
                )
                logger.info(
                    f"[CleanupService] DRY RUN: Would purge {count} archived entries"
                )

                # === Audit 기록: DRY RUN 모드 (실제 삭제 없음) ===
                log_system_control_audit(
                    action="purge_dlq_dry_run",
                    actor="system",
                    old_state={"purged_count": 0},
                    new_state={"would_purge_count": count, "dry_run": True},
                    reason=f"DRY RUN: Would purge {count} archived entries older than {older_than_days} days",
                )
            else:
                count = dlq_service.purge_archived(older_than_days=older_than_days)
                logger.warning(
                    f"[CleanupService] ⚠️ PERMANENTLY DELETED {count} entries"
                )

                # === Audit 기록: 영구 삭제 (고위험, 복구 불가) ===
                log_system_control_audit(
                    action="purge_dlq_permanent",
                    actor="system",
                    old_state={"purged_count": 0},
                    new_state={"purged_count": count, "permanent": True, "unrecoverable": True},
                    reason=f"PERMANENT DELETION: Purged {count} archived entries older than {older_than_days} days - UNRECOVERABLE",
                )

            return CleanupResult(
                success=True,
                operation="purged",
                count=count,
                details={
                    "older_than_days": older_than_days,
                    "dry_run": dry_run,
                    "warning": "PERMANENT DELETION - UNRECOVERABLE"
                    if not dry_run
                    else "DRY RUN - No actual deletion",
                },
            )

        except Exception as e:
            logger.error(f"[CleanupService] Purge failed: {e}", exc_info=True)
            return CleanupResult(
                success=False,
                operation="purged",
                error=str(e),
                details={"older_than_days": older_than_days, "dry_run": dry_run},
            )


# =============================================================================
# Singleton
# =============================================================================

_cleanup_service: Optional[CleanupService] = None


def get_cleanup_service() -> CleanupService:
    """Get or create the cleanup service instance."""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = CleanupService()
    return _cleanup_service


def reset_cleanup_service() -> None:
    """Reset the cleanup service instance (for testing)."""
    global _cleanup_service
    _cleanup_service = None


__all__ = [
    "CleanupResult",
    "CleanupService",
    "get_cleanup_service",
    "reset_cleanup_service",
]
