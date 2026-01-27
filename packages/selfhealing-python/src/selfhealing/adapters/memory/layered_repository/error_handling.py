"""
L2 Error Handling Mixin.

Provides methods for handling L2 errors and success states.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ErrorHandlingMixin:
    """Mixin providing L2 error handling operations."""

    def _handle_l2_timeout(self, operation: str, service_name: str | None) -> None:
        """L2 타임아웃 처리."""
        self._metrics["l2_timeout_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)

        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True

            # Audit 기록: L2 장애 발생
            self._log_l2_failure_audit(
                operation=operation,
                service_name=service_name,
                error_type="timeout",
                error_message=f"L2 timeout after {self._l2_consecutive_failures} consecutive failures",
            )

            # 알림 발송: 연속 실패 시
            self._send_l2_failure_notification(
                failure_type="timeout",
                consecutive_failures=self._l2_consecutive_failures,
            )

        try:
            from selfhealing.services.metrics.recorders import record_l2_timeout

            record_l2_timeout(self._adapter_type, operation)
        except ImportError:
            pass

    def _handle_l2_error(
        self,
        operation: str,
        service_name: str | None,
        error: Exception,
        intended_state: str = "",
    ) -> None:
        """L2 오류 처리 및 Shadow Log 기록."""
        self._metrics["l2_sync_failure_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)

        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True

            # Audit 기록: L2 장애 발생
            self._log_l2_failure_audit(
                operation=operation,
                service_name=service_name,
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )

            # 알림 발송: 연속 실패 시
            self._send_l2_failure_notification(
                failure_type="error",
                consecutive_failures=self._l2_consecutive_failures,
                error_message=str(error)[:200],
            )

        if service_name and intended_state:
            self._shadow_logger.record_sync_failure(
                service_name=service_name,
                intended_state=intended_state,
                error=error,
                adapter_type=self._adapter_type,
                operation=operation,
            )

        try:
            from selfhealing.services.metrics.recorders import record_l2_sync_failure

            record_l2_sync_failure(self._adapter_type, operation)
        except ImportError:
            pass

    def _handle_l2_success(self, elapsed_ms: float) -> None:
        """L2 성공 처리 및 복구 감지."""
        was_unhealthy = not self._l2_healthy or self._l2_was_unhealthy

        self._metrics["l2_sync_success_count"] += 1
        self._metrics["l2_latency_total_ms"] += elapsed_ms
        self._metrics["l2_latency_count"] += 1
        self._l2_consecutive_failures = 0
        self._l2_healthy = True

        if was_unhealthy:
            self._l2_was_unhealthy = False
            logger.info(
                f"[LayeredRepo] L2 recovery detected after "
                f"{self._metrics.get('l2_sync_failure_count', 0)} failures. "
                f"Initiating drift reconciliation."
            )

            # Audit 기록: L2 복구
            self._log_l2_recovery_audit()

            # 알림 발송: L2 복구 완료
            self._send_l2_recovery_notification()

            self._schedule_drift_reconciliation()

        try:
            from selfhealing.services.metrics.recorders import record_l2_latency

            record_l2_latency(self._adapter_type, elapsed_ms / 1000.0)
        except ImportError:
            pass
