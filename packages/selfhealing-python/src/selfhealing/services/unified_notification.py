"""
Unified Notification Manager

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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.settings import NotificationChannelSettings, get_layered_settings

logger = logging.getLogger(__name__)


def _get_notification_channel_settings() -> NotificationChannelSettings:
    """LayeredSettings에서 알림 채널 설정 가져오기."""
    return get_layered_settings(NotificationChannelSettings, "notification_channel")


# =============================================================================
# Enums and Data Classes
# =============================================================================


class NotificationPriority(str, Enum):
    """Notification priority levels."""

    CRITICAL = "critical"  # Immediate: all channels
    HIGH = "high"  # Urgent: Slack + Email
    MEDIUM = "medium"  # Normal: Slack only
    LOW = "low"  # Can be batched
    INFO = "info"  # Log only unless configured


class NotificationCategory(str, Enum):
    """Notification categories for routing and filtering."""

    SECURITY = "security"  # Security incidents
    OPERATIONS = "operations"  # Self-healing operations
    SLA = "sla"  # SLA drift and violations
    CIRCUIT_BREAKER = "circuit_breaker"  # Circuit breaker state changes
    GOVERNANCE = "governance"  # Governance checks
    APPROVAL = "approval"  # Approval requests
    REPORT = "report"  # Daily reports
    ERROR = "error"  # Task failures
    CHAOS = "chaos"  # Chaos experiment notifications


@dataclass
class NotificationPayload:
    """
    Unified notification payload.

    All notification sources should construct this payload
    for consistent handling.
    """

    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    category: NotificationCategory = NotificationCategory.OPERATIONS

    # Source information
    source: str = "unknown"  # e.g., "drift_detection", "circuit_breaker"
    task_name: str | None = None
    task_id: str | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Routing hints (can be overridden by manager)
    channels: list[str] | None = None

    # Deduplication
    dedup_key: str | None = None  # If set, used for cooldown dedup

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "message": self.message,
            "priority": self.priority.value,
            "category": self.category.value,
            "source": self.source,
            "task_name": self.task_name,
            "task_id": self.task_id,
            "metadata": self.metadata,
            "tags": self.tags,
            "timestamp": self.timestamp.isoformat(),
            "channels": self.channels,
            "dedup_key": self.dedup_key,
        }


@dataclass
class NotificationResult:
    """Result of notification attempt."""

    success: bool
    channels_sent: list[str] = field(default_factory=list)
    channels_failed: list[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "channels_sent": self.channels_sent,
            "channels_failed": self.channels_failed,
            "suppressed": self.suppressed,
            "suppression_reason": self.suppression_reason,
            "error": self.error,
        }


# =============================================================================
# Routing Policy
# =============================================================================


@dataclass
class RoutingPolicy:
    """
    Notification routing policy.

    Defines which channels to use based on priority and category.

    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [15] NotificationChannelSettings 참조.
    """

    # Channel mapping by priority
    priority_channels: dict[NotificationPriority, list[str]] = field(
        default_factory=lambda: {
            NotificationPriority.CRITICAL: ["slack", "email", "sms", "pagerduty"],
            NotificationPriority.HIGH: ["slack", "email"],
            NotificationPriority.MEDIUM: ["slack"],
            NotificationPriority.LOW: ["slack"],
            NotificationPriority.INFO: [],  # Log only
        }
    )

    # Category-specific channel overrides
    category_channels: dict[NotificationCategory, list[str]] = field(
        default_factory=lambda: {
            NotificationCategory.SECURITY: ["slack", "email"],
            NotificationCategory.APPROVAL: ["slack", "email"],
            NotificationCategory.REPORT: ["slack", "email"],
        }
    )

    # Cooldown settings (seconds) by category
    cooldown_seconds: dict[NotificationCategory, int] = field(
        default_factory=lambda: {
            NotificationCategory.SECURITY: 60,  # 1 min
            NotificationCategory.OPERATIONS: _get_notification_channel_settings().cooldown_seconds,
            NotificationCategory.SLA: 1800,  # 30 min
            NotificationCategory.CIRCUIT_BREAKER: _get_notification_channel_settings().cooldown_seconds,
            NotificationCategory.GOVERNANCE: 900,  # 15 min
            NotificationCategory.APPROVAL: 0,  # No cooldown
            NotificationCategory.REPORT: 0,  # No cooldown
            NotificationCategory.ERROR: 60,  # 1 min
            NotificationCategory.CHAOS: _get_notification_channel_settings().cooldown_seconds,
        }
    )

    @classmethod
    def from_settings(cls) -> RoutingPolicy:
        """
        LayeredSettings에서 라우팅 정책 생성.

        Returns:
            Settings 기반 RoutingPolicy
        """
        settings = _get_notification_channel_settings()

        return cls(
            cooldown_seconds={
                NotificationCategory.SECURITY: 60,
                NotificationCategory.OPERATIONS: settings.cooldown_seconds,
                NotificationCategory.SLA: 1800,
                NotificationCategory.CIRCUIT_BREAKER: settings.cooldown_seconds,
                NotificationCategory.GOVERNANCE: 900,
                NotificationCategory.APPROVAL: 0,
                NotificationCategory.REPORT: 0,
                NotificationCategory.ERROR: 60,
                NotificationCategory.CHAOS: settings.cooldown_seconds,
            }
        )

    def get_channels(
        self,
        priority: NotificationPriority,
        category: NotificationCategory,
    ) -> list[str]:
        """Get channels for a notification based on priority and category."""
        # Category override takes precedence for specific categories
        if category in self.category_channels:
            category_channels = self.category_channels[category]
            priority_channels = self.priority_channels.get(priority, [])

            # Merge: category channels + additional from priority
            channels = list(category_channels)
            for ch in priority_channels:
                if ch not in channels:
                    channels.append(ch)
            return channels

        return self.priority_channels.get(priority, [])

    def get_cooldown(self, category: NotificationCategory) -> int:
        """Get cooldown seconds for a category."""
        return self.cooldown_seconds.get(category, 300)


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
            logger.info(f"[UnifiedNotification] {payload.category.value}: " f"{payload.title} - {payload.message}")
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
            logger.debug(f"[UnifiedNotification] Emergency level check failed: {e}")

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
            logger.error(f"[UnifiedNotification] Send failed: {e}")
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
            logger.debug(f"[UnifiedNotification] Audit record failed: {e}")

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
            logger.debug(f"[UnifiedNotification] Daily report add failed: {e}")

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


# =============================================================================
# Convenience Functions
# =============================================================================


def notify(
    title: str,
    message: str,
    priority: str = "medium",
    category: str = "operations",
    source: str = "unknown",
    **kwargs,
) -> NotificationResult:
    """
    Convenience function for sending notifications.

    Args:
        title: Notification title
        message: Notification message
        priority: Priority level (critical, high, medium, low, info)
        category: Category (security, operations, sla, etc.)
        source: Source identifier
        **kwargs: Additional payload fields

    Returns:
        NotificationResult

    Usage:
        from selfhealing.services.unified_notification import notify

        notify(
            title="SLA Drift Warning",
            message="Payment domain exceeded 20% threshold",
            priority="high",
            category="sla",
            source="drift_detection",
            metadata={"domain": "payment", "rate": 25.0},
        )
    """
    try:
        priority_enum = NotificationPriority(priority.lower())
    except ValueError:
        priority_enum = NotificationPriority.MEDIUM

    try:
        category_enum = NotificationCategory(category.lower())
    except ValueError:
        category_enum = NotificationCategory.OPERATIONS

    payload = NotificationPayload(
        title=title,
        message=message,
        priority=priority_enum,
        category=category_enum,
        source=source,
        metadata=kwargs.get("metadata", {}),
        tags=kwargs.get("tags", []),
        channels=kwargs.get("channels"),
        dedup_key=kwargs.get("dedup_key"),
    )

    manager = get_unified_notification_manager()
    return manager.notify(payload)


def notify_security(
    title: str,
    message: str,
    priority: str = "high",
    source: str = "security",
    **kwargs,
) -> NotificationResult:
    """Convenience function for security notifications."""
    return notify(
        title=title,
        message=message,
        priority=priority,
        category="security",
        source=source,
        **kwargs,
    )


def notify_sla(
    title: str,
    message: str,
    domain: str,
    priority: str = "medium",
    source: str = "sla_monitor",
    **kwargs,
) -> NotificationResult:
    """Convenience function for SLA-related notifications."""
    metadata = kwargs.get("metadata", {})
    metadata["domain"] = domain

    return notify(
        title=title,
        message=message,
        priority=priority,
        category="sla",
        source=source,
        metadata=metadata,
        dedup_key=f"sla:{domain}",
        **kwargs,
    )


def notify_error(
    title: str,
    message: str,
    error: Exception,
    source: str = "unknown",
    **kwargs,
) -> NotificationResult:
    """Convenience function for error notifications."""
    metadata = kwargs.get("metadata", {})
    metadata["error_type"] = type(error).__name__
    metadata["error_message"] = str(error)

    return notify(
        title=title,
        message=message,
        priority="high",
        category="error",
        source=source,
        metadata=metadata,
        **kwargs,
    )


# =============================================================================
# Slack Block Kit Formatters - Actionable Alert
# =============================================================================


def format_cb_slack_blocks(
    payload: NotificationPayload,
    priority: NotificationPriority,
) -> dict[str, Any]:
    """
        Circuit Breaker 알림용 Slack Block Kit 메시지 포맷.

    Actionable Alert 설계 원칙:
        - 거버넌스 유지: 원클릭 해제 대신 Admin 제어판으로 이동
        - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
        - 안전성: 운영자가 상태 확인 후 판단 가능

        Args:
            payload: 알림 페이로드
            priority: 효과적 우선순위 (에스컬레이션 적용 후)

        Returns:
            Slack Block Kit 형식의 메시지 딕셔너리
    """
    severity_emoji = {
        NotificationPriority.CRITICAL: "🔴",
        NotificationPriority.HIGH: "🟠",
        NotificationPriority.MEDIUM: "🟡",
        NotificationPriority.LOW: "🔵",
        NotificationPriority.INFO: "⚪",
    }.get(priority, "⚪")

    metadata = payload.metadata or {}
    service_name = metadata.get("service_name", "unknown")
    trace_url = metadata.get("trace_url")
    trigger_time = metadata.get("trigger_time", "")

    # Actionable URLs
    dashboard_url = metadata.get("dashboard_url")
    admin_url = metadata.get("admin_url")
    runbook_url = metadata.get("runbook_url")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {payload.title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n{service_name}"},
                {"type": "mrkdwn", "text": f"*Priority:*\n{priority.value.upper()}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Message:*\n{payload.message}",
            },
        },
    ]

    # Trace URL 섹션 (있는 경우)
    if trace_url:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Trace:*\n<{trace_url}|View in Jaeger>",
                },
            }
        )

    # Actionable 버튼 섹션
    action_elements = []

    if dashboard_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📊 Dashboard",
                    "emoji": True,
                },
                "url": dashboard_url,
                "action_id": "view_dashboard",
            }
        )

    if admin_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "⚙️ Admin Panel",
                    "emoji": True,
                },
                "url": admin_url,
                "action_id": "view_admin",
                "style": "primary",
            }
        )

    if runbook_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📖 Runbook",
                    "emoji": True,
                },
                "url": runbook_url,
                "action_id": "view_runbook",
            }
        )

    if action_elements:
        blocks.append(
            {
                "type": "actions",
                "elements": action_elements,
            }
        )

    # 컨텍스트 섹션 (타임스탬프)
    context_text = f"Event: {payload.category.value}"
    if trigger_time:
        context_text += f" | Time: {trigger_time}"

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": context_text,
                },
            ],
        }
    )

    return {"blocks": blocks}


def format_cb_notification_with_actions(payload: NotificationPayload) -> dict[str, Any]:
    """
    Circuit Breaker 알림을 Actionable Alert 형식으로 포맷.

    이 함수는 SecurityNotificationService에서 호출되어
    Slack으로 전송될 메시지를 Actionable 버튼이 포함된 Block Kit 형식으로 변환합니다.

    Args:
        payload: 알림 페이로드

    Returns:
        Actionable 버튼이 포함된 Slack Block Kit 메시지
    """
    try:
        from selfhealing.services.emergency_mode import get_emergency_manager

        manager = get_emergency_manager()
        level = manager.get_current_level()

        # Emergency Level에 따른 우선순위 조정
        priority = payload.priority
        if level >= 3 and priority in (
            NotificationPriority.LOW,
            NotificationPriority.INFO,
            NotificationPriority.MEDIUM,
        ):
            priority = NotificationPriority.HIGH
        elif level >= 2 and priority in (
            NotificationPriority.LOW,
            NotificationPriority.INFO,
        ):
            priority = NotificationPriority.MEDIUM

    except ImportError:
        priority = payload.priority
    except Exception:
        priority = payload.priority

    return format_cb_slack_blocks(payload, priority)
