"""
Unified Notification Routing Policy.

Defines channel routing based on priority and category.

Reference:
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [15] NotificationChannelSettings 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selfhealing.settings import NotificationChannelSettings, get_layered_settings

from .models import NotificationCategory, NotificationPriority


def _get_notification_channel_settings() -> NotificationChannelSettings:
    """LayeredSettings에서 알림 채널 설정 가져오기."""
    return get_layered_settings(NotificationChannelSettings, "notification_channel")


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
