"""
Phase 4: Chaos Notification Integration Tests

Tests for Phase 4 implementation - Notification integration:
- Notification system integration (4 tests)

Reference: docs/self_healing/middleware_system/24_CHAOS_INTEGRATION_PLAN.md §8.5

Total: 4 tests
"""

from unittest.mock import MagicMock, patch

# =============================================================================
# Notification Integration Tests (4 tests)
# =============================================================================


class TestChaosNotificationIntegration:
    """Integration tests for Chaos Notification - 4 tests."""

    def test_notification_sends_on_experiment_start(self):
        """Test notification is sent when experiment starts."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            result = send_chaos_experiment_alert(
                experiment_id="test-exp-001",
                experiment_type="latency_injection",
                target_service="payment-api",
                event_type="started",
                details={"duration_seconds": 60},
            )

            assert result is True
            mock_instance.notify.assert_called_once()

            # Verify payload
            call_args = mock_instance.notify.call_args
            payload = call_args[0][0]
            assert "payment-api" in payload.title
            assert payload.source == "chaos_experiment"
            assert "chaos:" in payload.dedup_key

    def test_notification_sends_on_experiment_failure(self):
        """Test notification is sent with HIGH priority on failure."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert
        from selfhealing.services.unified_notification import NotificationPriority

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            result = send_chaos_experiment_alert(
                experiment_id="test-exp-002",
                experiment_type="failure_injection",
                target_service="order-api",
                event_type="failed",
                details={"error": "Timeout exceeded"},
            )

            assert result is True

            call_args = mock_instance.notify.call_args
            payload = call_args[0][0]
            assert payload.priority == NotificationPriority.HIGH
            assert "❌" in payload.title

    def test_notification_includes_admin_urls(self):
        """Test notification includes Admin Deep Link URLs."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            send_chaos_experiment_alert(
                experiment_id="test-exp-003",
                experiment_type="latency_injection",
                target_service="payment-api",
                event_type="started",
                details={},
            )

            call_args = mock_instance.notify.call_args
            payload = call_args[0][0]
            metadata = payload.metadata

            # Admin URLs should be present
            assert "admin_url" in metadata
            assert "dashboard_url" in metadata or metadata.get("dashboard_url") is None
            # runbook may be None if not configured

    def test_notification_uses_chaos_category(self):
        """Test notification uses NotificationCategory.CHAOS for dedup."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert
        from selfhealing.services.unified_notification import NotificationCategory

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            send_chaos_experiment_alert(
                experiment_id="test-exp-004",
                experiment_type="latency_injection",
                target_service="user-api",
                event_type="stopped",
                details={},
            )

            call_args = mock_instance.notify.call_args
            payload = call_args[0][0]

            # Must use CHAOS category
            assert payload.category == NotificationCategory.CHAOS

