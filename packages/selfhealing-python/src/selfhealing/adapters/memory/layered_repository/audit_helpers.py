"""
Audit and Notification Helpers Mixin.

Provides methods for audit logging and notifications with Fail-Open principle.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class AuditHelpersMixin:
    """Mixin providing audit logging and notification helpers."""

    def _log_l2_failure_audit(
        self,
        operation: str,
        service_name: str | None,
        error_type: str,
        error_message: str,
    ) -> None:
        """L2 장애 발생 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import log_storage_failure_audit

            log_storage_failure_audit(
                storage_type="l2",
                adapter_type=self._adapter_type,
                operation=operation,
                service_name=service_name,
                error_type=error_type,
                error_message=error_message,
                consecutive_failures=self._l2_consecutive_failures,
            )
        except Exception as e:
            # Fail-Open: Audit 실패가 시스템을 중단시키지 않음
            logger.debug(
                "layered_repo.audit_logging_failed_ignored",
                error=e,
            )

    def _log_l2_recovery_audit(self) -> None:
        """L2 복구 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import log_storage_recovery_audit

            log_storage_recovery_audit(
                storage_type="l2",
                adapter_type=self._adapter_type,
                total_failures=self._metrics.get("l2_sync_failure_count", 0),
            )
        except Exception as e:
            logger.debug(
                "layered_repo.audit_logging_failed_ignored",
                error=e,
            )

    def _log_drift_reconciliation_audit(
        self,
        total_checked: int,
        reconciled: int,
        l1_wins: int,
        l2_wins: int,
        errors: list[dict[str, Any]],
    ) -> None:
        """드리프트 복구 완료 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import (
                log_drift_reconciliation_audit,
            )

            log_drift_reconciliation_audit(
                adapter_type=self._adapter_type,
                total_checked=total_checked,
                reconciled=reconciled,
                l1_wins=l1_wins,
                l2_wins=l2_wins,
                error_count=len(errors),
            )
        except Exception as e:
            logger.debug(
                "layered_repo.audit_logging_failed_ignored",
                error=e,
            )

    def _send_l2_failure_notification(
        self,
        failure_type: str,
        consecutive_failures: int,
        error_message: str = "",
    ) -> None:
        """L2 연속 장애 시 알림 발송. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.unified_notification import (
                get_notification_service,
            )

            notification_service = get_notification_service()
            notification_service.send(
                level="warning",
                category="STORAGE_FAILURE",
                title=f"L2 Storage Failure ({self._adapter_type})",
                message=(
                    f"L2 storage has failed {consecutive_failures} consecutive times. "
                    f"Type: {failure_type}. System operating in L1-only mode. "
                    f"{error_message}"
                ),
                metadata={
                    "adapter_type": self._adapter_type,
                    "failure_type": failure_type,
                    "consecutive_failures": consecutive_failures,
                },
            )
        except Exception as e:
            logger.debug(
                "layered_repo.notification_failed_ignored",
                error=e,
            )

    def _send_l2_recovery_notification(self) -> None:
        """L2 복구 완료 시 알림 발송. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.unified_notification import (
                get_notification_service,
            )

            notification_service = get_notification_service()
            notification_service.send(
                level="info",
                category="STORAGE_RECOVERY",
                title=f"L2 Storage Recovered ({self._adapter_type})",
                message=(
                    f"L2 storage has recovered after "
                    f"{self._metrics.get('l2_sync_failure_count', 0)} failures. "
                    f"Drift reconciliation initiated."
                ),
                metadata={
                    "adapter_type": self._adapter_type,
                    "total_failures": self._metrics.get("l2_sync_failure_count", 0),
                },
            )
        except Exception as e:
            logger.debug(
                "layered_repo.notification_failed_ignored",
                error=e,
            )
