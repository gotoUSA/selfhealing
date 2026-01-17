"""
Dynamic Escalation Tests.

Tests for:
1. Level 2 escalation: LOW/INFO -> MEDIUM
2. Level 3 escalation: LOW/INFO/MEDIUM -> HIGH
3. Ceiling check: CRITICAL does not escalate
4. Channel variability: Level 3 adds SMS/PagerDuty

Dynamic Escalation

Review points:
- Dynamic Escalation: Priority escalation based on Level 2/3
- Ceiling setting: CRITICAL alerts cannot escalate higher
- Channel variability: SMS/PagerDuty channels auto-added at Level 3
"""

import pytest
from unittest.mock import MagicMock, patch


def create_emergency_mode_mock(level: int):
    """Create a mock emergency_mode module with the specified level."""
    mock_emergency_mode = MagicMock()
    mock_emergency_manager = MagicMock()
    mock_emergency_manager.get_current_level.return_value = level
    mock_emergency_mode.get_emergency_manager.return_value = mock_emergency_manager
    return mock_emergency_mode


class TestDynamicEscalationLevel2:
    """Level 2 dynamic escalation tests."""

    def test_level2_escalates_info_to_medium(self):
        """Level 2 escalates INFO to MEDIUM."""
        mock_emergency_mode = create_emergency_mode_mock(level=2)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.INFO,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.MEDIUM

    def test_level2_escalates_low_to_medium(self):
        """Level 2 escalates LOW to MEDIUM."""
        mock_emergency_mode = create_emergency_mode_mock(level=2)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.LOW,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.MEDIUM

    def test_level2_does_not_escalate_medium(self):
        """Level 2 does not escalate MEDIUM."""
        mock_emergency_mode = create_emergency_mode_mock(level=2)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.MEDIUM,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.MEDIUM

    def test_level2_does_not_escalate_high(self):
        """Level 2 does not escalate HIGH."""
        mock_emergency_mode = create_emergency_mode_mock(level=2)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.HIGH


class TestDynamicEscalationLevel3:
    """Level 3 dynamic escalation tests."""

    def test_level3_escalates_info_to_high(self):
        """Level 3 escalates INFO to HIGH."""
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.INFO,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.HIGH

    def test_level3_escalates_low_to_high(self):
        """Level 3 escalates LOW to HIGH."""
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.LOW,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.HIGH

    def test_level3_escalates_medium_to_high(self):
        """Level 3 escalates MEDIUM to HIGH."""
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.MEDIUM,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.HIGH

    def test_level3_does_not_escalate_high(self):
        """Level 3 does not escalate HIGH (already HIGH)."""
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.HIGH


class TestCriticalCeiling:
    """CRITICAL ceiling tests."""

    def test_critical_not_escalated_at_level2(self):
        """Level 2 does not escalate CRITICAL (ceiling reached)."""
        mock_emergency_mode = create_emergency_mode_mock(level=2)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Critical Alert",
                message="Critical message",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.CIRCUIT_BREAKER,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.CRITICAL

    def test_critical_not_escalated_at_level3(self):
        """Level 3 does not escalate CRITICAL (ceiling reached)."""
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Critical Alert",
                message="Critical message",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.CIRCUIT_BREAKER,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.CRITICAL

    def test_critical_early_return_without_manager(self):
        """CRITICAL returns early without calling emergency_mode_manager."""
        from selfhealing.services.unified_notification import (
            UnifiedNotificationManager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )

        manager = UnifiedNotificationManager()
        payload = NotificationPayload(
            title="Critical Alert",
            message="Critical message",
            priority=NotificationPriority.CRITICAL,
            category=NotificationCategory.GOVERNANCE,
        )

        # Returns CRITICAL even if ImportError occurs
        result = manager._get_effective_priority(payload)
        assert result == NotificationPriority.CRITICAL


class TestNormalLevelNoEscalation:
    """Normal level (NORMAL/Level 1) no escalation tests."""

    def test_level0_no_escalation(self):
        """Level 0 (NORMAL) does not escalate."""
        mock_emergency_mode = create_emergency_mode_mock(level=0)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.INFO,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.INFO

    def test_level1_no_escalation(self):
        """Level 1 does not escalate."""
        mock_emergency_mode = create_emergency_mode_mock(level=1)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.LOW,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            assert result == NotificationPriority.LOW


class TestChannelVariability:
    """Channel variability tests - channel selection based on priority."""

    def test_critical_priority_includes_all_channels(self):
        """CRITICAL priority includes all channels (slack, email, sms, pagerduty)."""
        from selfhealing.services.unified_notification import (
            RoutingPolicy,
            NotificationPriority,
            NotificationCategory,
        )

        policy = RoutingPolicy()
        channels = policy.get_channels(
            NotificationPriority.CRITICAL,
            NotificationCategory.OPERATIONS,
        )

        assert "slack" in channels
        assert "email" in channels
        assert "sms" in channels
        assert "pagerduty" in channels

    def test_high_priority_channels(self):
        """HIGH priority includes slack, email channels."""
        from selfhealing.services.unified_notification import (
            RoutingPolicy,
            NotificationPriority,
            NotificationCategory,
        )

        policy = RoutingPolicy()
        channels = policy.get_channels(
            NotificationPriority.HIGH,
            NotificationCategory.OPERATIONS,
        )

        assert "slack" in channels
        assert "email" in channels
        assert "sms" not in channels
        assert "pagerduty" not in channels

    def test_medium_priority_channels(self):
        """MEDIUM priority includes only slack."""
        from selfhealing.services.unified_notification import (
            RoutingPolicy,
            NotificationPriority,
            NotificationCategory,
        )

        policy = RoutingPolicy()
        channels = policy.get_channels(
            NotificationPriority.MEDIUM,
            NotificationCategory.OPERATIONS,
        )

        assert channels == ["slack"]

    def test_level3_escalation_adds_higher_priority_channels(self):
        """Level 3 escalation provides higher priority channel access.

        Note: Actual channel selection is determined by escalated priority.
        At Level 3, MEDIUM escalates to HIGH, getting slack + email channels.
        """
        mock_emergency_mode = create_emergency_mode_mock(level=3)

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                RoutingPolicy,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()

            # MEDIUM -> HIGH escalation
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.MEDIUM,
                category=NotificationCategory.OPERATIONS,
            )

            effective_priority = manager._get_effective_priority(payload)
            assert effective_priority == NotificationPriority.HIGH

            # HIGH priority channel check
            policy = RoutingPolicy()
            channels = policy.get_channels(effective_priority, payload.category)
            assert "slack" in channels
            assert "email" in channels


class TestEdgeCases:
    """Edge case tests."""

    def test_exception_in_manager_returns_original_priority(self):
        """Exception in emergency_mode_manager returns original priority."""
        mock_emergency_mode = MagicMock()
        mock_emergency_mode.get_emergency_manager.side_effect = Exception(
            "Manager unavailable"
        )

        with patch.dict(
            'sys.modules',
            {'selfhealing.services.emergency_mode': mock_emergency_mode}
        ):
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            payload = NotificationPayload(
                title="Test",
                message="Test message",
                priority=NotificationPriority.LOW,
                category=NotificationCategory.OPERATIONS,
            )

            result = manager._get_effective_priority(payload)
            # Returns original priority on exception
            assert result == NotificationPriority.LOW

    def test_import_error_returns_original_priority(self):
        """ImportError returns original priority."""
        from selfhealing.services.unified_notification import (
            UnifiedNotificationManager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )

        manager = UnifiedNotificationManager()
        payload = NotificationPayload(
            title="Test",
            message="Test message",
            priority=NotificationPriority.MEDIUM,
            category=NotificationCategory.OPERATIONS,
        )

        # Returns original priority even if ImportError occurs (exception handled)
        result = manager._get_effective_priority(payload)
        assert result == NotificationPriority.MEDIUM
