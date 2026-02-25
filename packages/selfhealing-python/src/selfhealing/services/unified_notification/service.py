"""
Unified Notification Manager Service.

Central notification management for the Self-Healing system.
Consolidates all notification logic into a single point of control,
solving the problem of fragmented notification sources.

Architecture Problem Solved:
- Before: 4 scattered notification sources (AlertAdapter, SecurityNotificationService,
          GateAlertManager, GovernanceService._send_notification)
- After: Single UnifiedNotificationManager that routes all notifications

Key Features:
- Centralized notification routing
- Policy-based channel selection
- Rate limiting and cooldown
- Audit trail integration
- Emergency level escalation

중앙화된 알림 라우팅 및 관리를 제공합니다.

Reference:
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [15] NotificationChannelSettings 참조.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from .models import (
    NotificationCategory,
    NotificationPayload,
    NotificationPriority,
    NotificationResult,
)
from .routing import RoutingPolicy

logger = structlog.get_logger()


# =============================================================================
# Unified Notification Manager
# =============================================================================


class UnifiedNotificationManager:
    """
    Central manager for all self-healing notifications.

    Provides a single point of control for:
    - Channel routing based on priority/category
    - Rate limiting and cooldown
    - Emergency level escalation
    - Audit trail integration

    Usage:
        manager = get_unified_notification_manager()

        result = manager.notify(NotificationPayload(
            title="SLA Drift Warning",
            message="Payment domain exceeded threshold",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            source="drift_detection",
        ))
    """

    def __init__(self, policy: RoutingPolicy | None = None):
        self._policy = policy or RoutingPolicy()
        self._cooldown_cache: dict[str, datetime] = {}
        self._notification_counts: dict[str, int] = {}

    def notify(self, payload: NotificationPayload) -> NotificationResult:
        """
        Send a notification through the unified manager.

        Handles routing, cooldown, escalation, and audit.
        """
        # 1. Check cooldown
        if self._is_suppressed_by_cooldown(payload):
            return NotificationResult(
                success=True,
                suppressed=True,
                suppression_reason="cooldown",
            )

        # 2. Check emergency level escalation
        effective_priority = self._get_effective_priority(payload)

        # 3. Determine channels
        channels = payload.channels or self._policy.get_channels(effective_priority, payload.category)

        if not channels:
            # Log only
            logger.info(
                "unified_notification.event",
                category=payload.category.value,
                payload=payload.title,
                message=payload.message,
            )
            return NotificationResult(success=True, suppressed=True, suppression_reason="log_only")

        # 4. Send to each channel
        result = self._send_to_channels(payload, channels, effective_priority)

        # 5. Record cooldown
        if result.success:
            self._record_cooldown(payload)

        # 6. Record to audit trail
        self._record_audit(payload, result)

        # 7. Add to daily report if applicable
        if payload.category in (
            NotificationCategory.OPERATIONS,
            NotificationCategory.SLA,
            NotificationCategory.CIRCUIT_BREAKER,
        ):
            self._add_to_daily_report(payload)

        return result

    def _is_suppressed_by_cooldown(self, payload: NotificationPayload) -> bool:
        """Check if notification should be suppressed by cooldown."""
        cooldown_seconds = self._policy.get_cooldown(payload.category)

        if cooldown_seconds <= 0:
            return False

        # Generate dedup key
        dedup_key = payload.dedup_key or f"{payload.source}:{payload.category.value}"

        last_sent = self._cooldown_cache.get(dedup_key)
        if last_sent is None:
            return False

        elapsed = (datetime.now(timezone.utc) - last_sent).total_seconds()
        return elapsed < cooldown_seconds

    def _record_cooldown(self, payload: NotificationPayload) -> None:
        """Record that notification was sent for cooldown tracking."""
        dedup_key = payload.dedup_key or f"{payload.source}:{payload.category.value}"
        self._cooldown_cache[dedup_key] = datetime.now(timezone.utc)

    def _get_effective_priority(self, payload: NotificationPayload) -> NotificationPriority:
        """
        Get effective priority considering emergency level.

        Dynamic escalation rules:
        - Level 2+: LOW/INFO → MEDIUM
        - Level 3+: LOW/INFO/MEDIUM → HIGH
        - CRITICAL은 최고 우선순위이므로 에스컬레이션 불필요
        """
        priority = payload.priority

        # 상한선 명시적 체크: 이미 CRITICAL이면 조기 반환 (방어적 코딩)
        if priority == NotificationPriority.CRITICAL:
            return priority

        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            level = manager.get_current_level()

            # Emergency Level 2+: Escalate LOW/INFO to MEDIUM minimum
            if level >= 2:
                if priority in (
                    NotificationPriority.LOW,
                    NotificationPriority.INFO,
                ):
                    priority = NotificationPriority.MEDIUM

            # Emergency Level 3+: Escalate all to HIGH minimum
            if level >= 3:
                if priority in (
                    NotificationPriority.LOW,
                    NotificationPriority.INFO,
                    NotificationPriority.MEDIUM,
                ):
                    priority = NotificationPriority.HIGH

        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "unified_notification.emergency_level_check_failed",
                error=e,
            )

        return priority

    def _send_to_channels(
        self,
        payload: NotificationPayload,
        channels: list[str],
        priority: NotificationPriority,
    ) -> NotificationResult:
        """Send notification to specified channels."""
        result = NotificationResult(success=True)

        try:
            from selfhealing.services.security_notification import (
                get_security_notification_service,
            )

            service = get_security_notification_service()

            # Use SecurityNotificationService.send_alert for actual sending
            send_result = service.send_alert(
                title=payload.title,
                message=payload.message,
                severity=priority.value,
                channels=channels,
                metadata={
                    **payload.metadata,
                    "category": payload.category.value,
                    "source": payload.source,
                    "tags": payload.tags,
                },
            )

            # Process results
            for channel_result in send_result.results:
                if channel_result.success:
                    result.channels_sent.append(channel_result.channel)
                else:
                    result.channels_failed.append(channel_result.channel)

            result.success = len(result.channels_sent) > 0

        except Exception as e:
            logger.exception(
                "unified_notification.send_failed",
                error=e,
            )
            result.success = False
            result.error = str(e)

        return result

    def _record_audit(self, payload: NotificationPayload, result: NotificationResult) -> None:
        """Record notification in audit trail."""
        try:
            from selfhealing.audit import get_audit_logger

            audit_logger = get_audit_logger()
            audit_logger.log_event(
                event_type="notification_sent",
                entity_type="notification",
                entity_id=payload.dedup_key or f"{payload.source}:{payload.timestamp.timestamp()}",
                action="send",
                details={
                    "title": payload.title,
                    "category": payload.category.value,
                    "priority": payload.priority.value,
                    "source": payload.source,
                    "channels_sent": result.channels_sent,
                    "suppressed": result.suppressed,
                },
            )

        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "unified_notification.audit_record_failed",
                error=e,
            )

    def _add_to_daily_report(self, payload: NotificationPayload) -> None:
        """Add notification to daily aggregated report."""
        try:
            from selfhealing.tasks.daily_report import get_daily_report_collector

            collector = get_daily_report_collector()
            collector.add_result(
                task_name=payload.source,
                result=payload.metadata,
                severity=payload.priority.value,
            )

        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "unified_notification.daily_report_add_failed",
                error=e,
            )

    def reset_cooldowns(self) -> None:
        """Reset all cooldowns (for testing)."""
        self._cooldown_cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get notification statistics."""
        return {
            "cooldown_entries": len(self._cooldown_cache),
            "notification_counts": dict(self._notification_counts),
        }


# =============================================================================
# Module-level Singleton
# =============================================================================

_manager: UnifiedNotificationManager | None = None


def get_unified_notification_manager() -> UnifiedNotificationManager:
    """Get the singleton UnifiedNotificationManager instance."""
    global _manager
    if _manager is None:
        _manager = UnifiedNotificationManager()
    return _manager


def reset_notification_manager() -> None:
    """Reset the notification manager singleton (for testing)."""
    global _manager
    _manager = None


# Alias for backward compatibility
get_notification_service = get_unified_notification_manager
