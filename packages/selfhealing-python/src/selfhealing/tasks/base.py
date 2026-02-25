"""
BaseNotifyingTask - Celery Task Base Class with Built-in Notifications

This module provides a base class for Celery tasks that automatically handles
notifications based on configurable policies. It implements risk-based notification
timing, alert fatigue prevention, and Emergency Level integration.

Key Features:
- Risk-based notification timing (BEFORE/AFTER/REALTIME/AGGREGATED)
- Alert fatigue prevention (cooldown, threshold, aggregation)
- Emergency Level integration (dynamic policy escalation)
- Audit Trail integration (notification event logging)

Usage:
    from selfhealing.tasks.base import BaseNotifyingTask
    from selfhealing.tasks.notification_policy import (
        NotificationPolicy,
        NotificationTiming,
    )

    @shared_task(bind=True, base=BaseNotifyingTask)
    class MyTask(BaseNotifyingTask):
        notification_policy = NotificationPolicy(
            timing=NotificationTiming.AFTER,
            default_severity="info",
        )

        def run(self, *args, **kwargs):
            # Task implementation
            return {"count": 42}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog

from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationThreshold,
    NotificationTiming,
)

logger = structlog.get_logger()


class BaseNotifyingTask:
    """
    Base class for Celery tasks with built-in notification support.

    This class can be used as a base for Celery tasks to automatically
    handle notifications based on configurable policies. It integrates
    with SecurityNotificationService and supports:

    - Risk-based notification timing
    - Alert fatigue prevention (cooldown, threshold)
    - Emergency Level integration
    - Audit Trail logging

    Note: This is designed to work with Celery's Task class, but can also
    be used standalone for testing or non-Celery environments.

    Usage:
        @shared_task(bind=True, base=BaseNotifyingTask)
        class ArchiveTask(BaseNotifyingTask):
            notification_policy = NotificationPolicy(
                timing=NotificationTiming.AGGREGATED,
                aggregate=True,
            )

            def run(self, days=30):
                count = archive_old_entries(days)
                return {"archived_count": count}
    """

    # Class-level policy (override in subclass)
    notification_policy: NotificationPolicy = NotificationPolicy()

    # Class-level cooldown state (shared across instances)
    _last_alert_times: ClassVar[dict[str, datetime]] = {}

    # Task metadata (set by Celery if used as base)
    name: str = "unknown"
    request: Any = None

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """
        Execute task with notification handling.

        This wraps the actual task execution with pre/post notification hooks.
        """
        # 1. Pre-execution hook (high-risk tasks)
        if not self._on_pre_execute(*args, **kwargs):
            return {
                "success": False,
                "blocked": True,
                "reason": "approval_required",
            }

        # 2. Execute the actual task
        try:
            # Call the subclass's run method
            result = self.run(*args, **kwargs)
            if not isinstance(result, dict):
                result = {"result": result}
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
            logger.exception(
                "celery_task.task_failed",
                task_name=self.name,
                error=e,
            )

        # 3. Post-execution hook
        self._on_post_execute(result, *args, **kwargs)

        return result

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """
        Override this method in subclass to implement task logic.

        Returns:
            Dictionary with task results
        """
        raise NotImplementedError("Subclasses must implement run()")

    def _on_pre_execute(self, *args: Any, **kwargs: Any) -> bool:
        """
        Pre-execution hook for high-risk tasks.

        Returns:
            True to proceed with execution, False to block
        """
        policy = self.notification_policy

        if policy.timing != NotificationTiming.BEFORE:
            return True

        if policy.requires_approval:
            # Check if approval is needed
            if self._should_skip_approval():
                # Emergency Level 3+: auto-approve but notify
                self._send_pre_notification(*args, **kwargs)
                return True
            else:
                # Request approval and block execution
                return self._request_approval(*args, **kwargs)

        # Just send pre-notification
        self._send_pre_notification(*args, **kwargs)
        return True

    def _on_post_execute(self, result: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        """Post-execution hook for result notifications."""
        policy = self.notification_policy

        # Check if notification should be sent
        if not self._should_notify(result):
            return

        # Get effective timing (may be escalated by Emergency Level)
        effective_timing = self._get_effective_timing()

        if policy.aggregate and effective_timing != NotificationTiming.REALTIME:
            # Add to daily report
            self._add_to_daily_report(result)
        else:
            # Send immediate notification
            self._send_notification(result)

        # Record to Audit Trail
        self._record_audit_trail(result)

    def _should_notify(self, result: dict[str, Any]) -> bool:
        """
        Determine if notification should be sent.

        Checks cooldown, threshold, and result content.
        """
        policy = self.notification_policy

        # 1. Cooldown check
        alert_key = f"{self.name}:{self._get_alert_key(result)}"
        if not self._can_send_alert(alert_key):
            logger.debug(
                "celery_task.alert_suppressed_cooldown",
                alert_key=alert_key,
            )
            return False

        # 2. Threshold check
        if policy.threshold is not None and policy.threshold_field:
            value = result.get(policy.threshold_field, 0)
            if isinstance(value, (int, float)) and value < policy.threshold:
                logger.debug(
                    "celery_task.alert_suppressed_threshold",
                    policy=policy.threshold_field,
                    threshold_value=value,
                    threshold=policy.threshold,
                )
                return False

        # 3. Meaningful result check
        return self._has_meaningful_result(result)

    def _get_effective_timing(self) -> NotificationTiming:
        """
        Get effective notification timing considering Emergency Level.

        Emergency Level 3+: All notifications become REALTIME.
        """
        policy = self.notification_policy

        if not policy.escalate_on_emergency:
            return policy.timing

        try:
            # Try to get Emergency Level from EmergencyModeManager
            from selfhealing.core.emergency_mode import get_emergency_mode_manager

            manager = get_emergency_mode_manager()
            current_level = manager.get_current_level()

            # LEVEL_3+: Escalate to REALTIME
            if current_level >= 3:
                return NotificationTiming.REALTIME

        except ImportError:
            logger.debug("celery_task.emergencymodemanager_available")
        except Exception as e:
            logger.warning(
                "celery_task.error_checking_emergency_level",
                error=e,
            )

        return policy.timing

    def _should_skip_approval(self) -> bool:
        """
        Check if approval should be skipped (Emergency Level 3+).

        In Emergency Level 3+, high-risk tasks auto-execute with notification.
        """
        try:
            from selfhealing.core.emergency_mode import get_emergency_mode_manager

            manager = get_emergency_mode_manager()
            current_level = manager.get_current_level()
            return current_level >= 3

        except ImportError:
            return False
        except Exception:
            return False

    def _can_send_alert(self, alert_key: str) -> bool:
        """Check if alert can be sent based on cooldown."""
        last_time = self._last_alert_times.get(alert_key)
        if last_time is None:
            return True

        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed >= self.notification_policy.cooldown_seconds

    def _record_alert_sent(self, alert_key: str) -> None:
        """Record that an alert was sent for cooldown tracking."""
        self._last_alert_times[alert_key] = datetime.now(timezone.utc)

    def _send_notification(self, result: dict[str, Any]) -> None:
        """Send notification via SecurityNotificationService."""
        try:
            from selfhealing.services.security_notification import (
                get_security_notification_service,
            )

            service = get_security_notification_service()

            message = self._get_summary_message(result)
            severity = self._get_severity(result)

            service.send_alert(
                title=f"[Self-Healing] {self.name}",
                message=message,
                severity=severity,
                channels=self.notification_policy.channels,
                metadata={
                    "task_name": self.name,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            # Record for cooldown
            alert_key = f"{self.name}:{self._get_alert_key(result)}"
            self._record_alert_sent(alert_key)

            logger.info(
                "celery_task.notification_sent",
                task_name=self.name,
                severity=severity,
            )

        except Exception as e:
            logger.exception(
                "celery_task.failed_send_notification",
                error=e,
            )

    def _send_pre_notification(self, *args: Any, **kwargs: Any) -> None:
        """Send pre-execution notification for high-risk tasks."""
        try:
            from selfhealing.services.security_notification import (
                get_security_notification_service,
            )

            service = get_security_notification_service()

            service.send_alert(
                title=f"[Self-Healing] {self.name} - 실행 예정",
                message=f"고위험 작업이 실행됩니다: {self.name}",
                severity="warning",
                channels=self.notification_policy.channels,
                metadata={
                    "task_name": self.name,
                    "args": str(args),
                    "kwargs": str(kwargs),
                    "timing": "before",
                },
            )

        except Exception as e:
            logger.exception(
                "celery_task.failed_send_pre_notification",
                error=e,
            )

    def _request_approval(self, *args: Any, **kwargs: Any) -> bool:
        """
        Request approval for high-risk task execution.

        Returns False to block execution until approved.
        """
        logger.warning(
            "celery_task.task_requires_approval_execution",
            task_name=self.name,
        )

        # Send approval request notification
        try:
            from selfhealing.services.security_notification import (
                get_security_notification_service,
            )

            service = get_security_notification_service()

            service.send_alert(
                title=f"[Self-Healing] 승인 필요: {self.name}",
                message=(f"고위험 작업 실행 승인이 필요합니다.\n" f"작업: {self.name}\n" f"인자: {args}, {kwargs}"),
                severity="critical",
                channels=self.notification_policy.channels + ["email"],
                metadata={
                    "task_name": self.name,
                    "requires_approval": True,
                    "args": str(args),
                    "kwargs": str(kwargs),
                },
            )

        except Exception as e:
            logger.exception(
                "celery_task.failed_send_approval_request",
                error=e,
            )

        # Block execution
        return False

    def _add_to_daily_report(self, result: dict[str, Any]) -> None:
        """
        Add result to daily aggregated report.

        This stores the result for later aggregation in the daily report task.
        """
        try:
            # Store in cache/Redis for later aggregation
            # For now, just log
            logger.info(
                "celery_task.adding_daily_report",
                task_name=self.name,
                daily_report_result=result,
            )

            # TODO: Implement actual storage (Redis or Django cache)
            # Example:
            # from django.core.cache import cache
            # key = f"daily_report:{datetime.now().date()}"
            # current = cache.get(key, [])
            # current.append({"task": self.name, "result": result, "timestamp": now()})
            # cache.set(key, current, timeout=86400)

        except Exception as e:
            logger.exception(
                "celery_task.failed_add_daily_report",
                error=e,
            )

    def _record_audit_trail(self, result: dict[str, Any]) -> None:
        """Record notification event in Audit Trail."""
        try:
            from selfhealing.audit import get_audit_logger

            audit_logger = get_audit_logger()

            task_id = getattr(self.request, "id", None) if self.request else None

            audit_logger.log_event(
                event_type="notification_sent",
                entity_type="celery_task",
                entity_id=task_id or "unknown",
                action=self.name,
                details={
                    "result_summary": self._get_summary_message(result),
                    "severity": self._get_severity(result),
                    "notification_policy": {
                        "timing": self.notification_policy.timing.value,
                        "aggregate": self.notification_policy.aggregate,
                        "requires_approval": self.notification_policy.requires_approval,
                    },
                },
            )

        except ImportError:
            logger.debug("celery_task.audit_logger_available")
        except Exception as e:
            logger.warning(
                "celery_task.failed_record_audit_trail",
                error=e,
            )

    # ==========================================================================
    # Override these methods in subclass for custom behavior
    # ==========================================================================

    def _get_summary_message(self, result: dict[str, Any]) -> str:
        """
        Generate notification message from result.

        Override in subclass for custom formatting.
        """
        if result.get("error"):
            return f"작업 실패: {result.get('error')}"

        # Try common count fields
        count_fields = [
            "count",
            "total",
            "processed",
            "archived_count",
            "expired_count",
            "purged_count",
            "recovered_count",
        ]
        for field_name in count_fields:
            if field_name in result:
                return f"처리 완료: {result[field_name]}건"

        return f"작업 완료: {result}"

    def _get_severity(self, result: dict[str, Any]) -> str:
        """
        Determine notification severity from result.

        Override in subclass for custom logic.
        """
        # Failed tasks are always critical
        if result.get("error") or result.get("success") is False:
            return "critical"

        # Check threshold field for severity
        policy = self.notification_policy
        if policy.threshold is not None and policy.threshold_field:
            value = result.get(policy.threshold_field, 0)
            if isinstance(value, (int, float)):
                threshold = NotificationThreshold(
                    log_only=policy.threshold * 0.25,
                    warning=policy.threshold,
                    critical=policy.threshold * 2.5,
                )
                calculated = threshold.get_severity(value)
                if calculated:
                    return calculated

        return policy.default_severity

    def _get_alert_key(self, result: dict[str, Any]) -> str:
        """
        Generate unique key for alert deduplication.

        Override in subclass for domain-specific keys.
        """
        # Try to extract domain or similar identifier
        for key in ["domain", "service", "type", "category"]:
            if key in result:
                return str(result[key])
        return "default"

    def _has_meaningful_result(self, result: dict[str, Any]) -> bool:
        """
        Check if result is meaningful enough to notify.

        Override in subclass for custom logic.
        """
        if not isinstance(result, dict):
            return True

        # Failures are always meaningful
        if result.get("error") or result.get("success") is False:
            return True

        # Check for count fields with non-zero values
        count_fields = [
            "count",
            "total",
            "processed",
            "archived_count",
            "expired_count",
            "purged_count",
            "recovered_count",
            "warnings",
        ]
        for field_name in count_fields:
            value = result.get(field_name)
            if isinstance(value, (int, float)) and value > 0:
                return True
            if isinstance(value, list) and len(value) > 0:
                return True

        return False


# =============================================================================
# Module-level Helper Functions
# =============================================================================


def reset_cooldowns() -> None:
    """Reset all alert cooldowns (for testing)."""
    BaseNotifyingTask._last_alert_times.clear()


def get_cooldown_status() -> dict[str, str]:
    """Get current cooldown status (for debugging)."""
    return {key: value.isoformat() for key, value in BaseNotifyingTask._last_alert_times.items()}
