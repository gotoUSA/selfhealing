"""
Notification ABC and registry delegation tests (commit 0b59f932).

Tests for:
- NotificationAdapter is ABC (not Protocol)
- StdoutNotificationAdapter / LoggingNotificationAdapter inherit from ABC
- register_notification_adapter duck-typing validation
- get_notification_adapter delegation to ProviderRegistry
- send_notification convenience function

Test Categories:
    A. Contract: ABC interface, adapter channels, severity mapping
    B. Behavior: Registration, duck-typing, get_adapter delegation, send
"""

from abc import ABC
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.interfaces.notification import (
    LoggingNotificationAdapter,
    Notification,
    NotificationAdapter,
    NotificationChannel,
    NotificationSeverity,
    StdoutNotificationAdapter,
    get_notification_adapter,
    register_notification_adapter,
    send_notification,
)

# =============================================================================
# Fixture: isolate ProviderRegistry notification state
# =============================================================================


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Isolate ProviderRegistry notification state."""
    from selfhealing.factory import ProviderRegistry

    original_notifications = ProviderRegistry._notifications.copy()
    original_instances = ProviderRegistry._notification_instances.copy()

    yield

    ProviderRegistry._notifications = original_notifications
    ProviderRegistry._notification_instances = original_instances


# =============================================================================
# A. Contract Tests
# =============================================================================


class TestNotificationAdapterABCContract:
    """Verify NotificationAdapter is ABC with required abstract methods."""

    def test_notification_adapter_is_abc(self):
        """NotificationAdapter is an ABC, not a Protocol."""
        assert issubclass(NotificationAdapter, ABC)

    def test_notification_adapter_cannot_be_instantiated(self):
        """Direct instantiation of NotificationAdapter raises TypeError."""
        with pytest.raises(TypeError):
            NotificationAdapter()

    def test_stdout_adapter_inherits_notification_adapter(self):
        """StdoutNotificationAdapter is a subclass of NotificationAdapter."""
        assert issubclass(StdoutNotificationAdapter, NotificationAdapter)

    def test_logging_adapter_inherits_notification_adapter(self):
        """LoggingNotificationAdapter is a subclass of NotificationAdapter."""
        assert issubclass(LoggingNotificationAdapter, NotificationAdapter)

    def test_stdout_adapter_channel_is_stdout(self):
        """StdoutNotificationAdapter.channel is STDOUT."""
        adapter = StdoutNotificationAdapter()
        assert adapter.channel == NotificationChannel.STDOUT

    def test_logging_adapter_channel_is_file(self):
        """LoggingNotificationAdapter.channel is FILE."""
        adapter = LoggingNotificationAdapter()
        assert adapter.channel == NotificationChannel.FILE

    def test_logging_adapter_severity_mapping_contract(self):
        """LoggingNotificationAdapter severity mapping has 5 entries."""
        expected = {
            "CRITICAL": "critical",
            "HIGH": "error",
            "MEDIUM": "warning",
            "LOW": "info",
            "INFO": "debug",
        }
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD == expected


# =============================================================================
# B. Behavior Tests
# =============================================================================


class TestNotificationAdapterBehavior:
    """Verify adapter send behavior."""

    def test_stdout_adapter_send_returns_true(self, capsys):
        """StdoutNotificationAdapter.send returns True and prints."""
        adapter = StdoutNotificationAdapter()
        notification = Notification(
            title="Test",
            message="Hello",
            severity=NotificationSeverity.HIGH,
        )

        result = adapter.send(notification)

        assert result is True
        captured = capsys.readouterr()
        assert "[HIGH]" in captured.out
        assert "Test: Hello" in captured.out

    def test_stdout_adapter_send_batch_returns_count(self, capsys):
        """StdoutNotificationAdapter.send_batch returns count of sent."""
        adapter = StdoutNotificationAdapter()
        notifications = [
            Notification(title="A", message="msg1"),
            Notification(title="B", message="msg2"),
        ]

        count = adapter.send_batch(notifications)
        assert count == 2

    def test_logging_adapter_send_returns_true(self):
        """LoggingNotificationAdapter.send returns True."""
        adapter = LoggingNotificationAdapter()
        notification = Notification(title="Test", message="Hello")

        result = adapter.send(notification)
        assert result is True


class TestRegisterNotificationAdapterBehavior:
    """Verify register_notification_adapter with ABC and duck-typed adapters."""

    def test_register_abc_adapter_delegates_to_provider_registry(self):
        """Registering an ABC adapter delegates to ProviderRegistry."""
        adapter = StdoutNotificationAdapter()

        register_notification_adapter(adapter)

        from selfhealing.factory import ProviderRegistry

        assert "stdout" in ProviderRegistry._notifications

    def test_register_duck_typed_adapter_with_all_methods_succeeds(self):
        """Duck-typed adapter with send/send_batch/channel is accepted."""

        class DuckAdapter:
            def send(self, notification):
                return True

            def send_batch(self, notifications):
                return len(notifications)

            @property
            def channel(self):
                return NotificationChannel.WEBHOOK

        adapter = DuckAdapter()
        register_notification_adapter(adapter)

        # Should be registered as virtual subclass
        assert isinstance(adapter, NotificationAdapter)

    def test_register_duck_typed_adapter_missing_method_raises_type_error(self):
        """Duck-typed adapter missing required method raises TypeError."""

        class IncompleteAdapter:
            def send(self, notification):
                return True

            # Missing send_batch and channel

        with pytest.raises(TypeError, match="missing"):
            register_notification_adapter(IncompleteAdapter())


class TestGetNotificationAdapterBehavior:
    """Verify get_notification_adapter delegation to ProviderRegistry."""

    def test_get_adapter_with_none_returns_default(self):
        """get_notification_adapter(None) returns default adapter."""
        adapter = get_notification_adapter(None)
        assert isinstance(adapter, (LoggingNotificationAdapter, NotificationAdapter))

    def test_get_adapter_with_unknown_channel_returns_default(self):
        """get_notification_adapter with unknown channel returns default."""
        adapter = get_notification_adapter(NotificationChannel.PAGERDUTY)
        # Should fallback to _default_adapter
        assert isinstance(adapter, LoggingNotificationAdapter)

    def test_get_adapter_after_registration_returns_registered(self):
        """get_notification_adapter returns the registered adapter."""
        custom_adapter = StdoutNotificationAdapter()
        register_notification_adapter(custom_adapter)

        result = get_notification_adapter(NotificationChannel.STDOUT)
        assert isinstance(result, StdoutNotificationAdapter)


class TestSendNotificationBehavior:
    """Verify send_notification convenience function."""

    def test_send_notification_calls_adapter_send(self):
        """send_notification creates Notification and calls adapter.send."""
        mock_adapter = MagicMock(spec=NotificationAdapter)
        mock_adapter.send.return_value = True

        with patch(
            "selfhealing.interfaces.notification.get_notification_adapter",
            return_value=mock_adapter,
        ):
            result = send_notification(
                title="Alert",
                message="Something happened",
                severity=NotificationSeverity.HIGH,
            )

        assert result is True
        mock_adapter.send.assert_called_once()
        sent_notification = mock_adapter.send.call_args[0][0]
        assert sent_notification.title == "Alert"
        assert sent_notification.severity == NotificationSeverity.HIGH
