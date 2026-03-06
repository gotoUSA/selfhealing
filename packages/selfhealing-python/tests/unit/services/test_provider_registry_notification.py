"""
ProviderRegistry notification adapter and DCL tests (commit 0b59f932).

Tests for:
- register_notification / get_notification
- Double-Checked Locking singleton creation
- _auto_register_notification_adapters
- reset / clear_instances includes notification state
- list_providers includes notification key

Test Categories:
    A. Contract: Default values, list_providers keys
    B. Behavior: Registration, get, DCL caching, reset, thread-safety
"""

import threading
from unittest.mock import MagicMock

import pytest

from selfhealing.factory import ProviderRegistry

# =============================================================================
# Fixture: isolate ProviderRegistry state
# =============================================================================


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Save and restore ProviderRegistry state for test isolation."""
    original_notifications = ProviderRegistry._notifications.copy()
    original_notification_instances = ProviderRegistry._notification_instances.copy()
    original_instances = ProviderRegistry._instances.copy()
    original_default_notification = ProviderRegistry._default_notification

    ProviderRegistry._notifications = {}
    ProviderRegistry._notification_instances = {}
    ProviderRegistry.clear_instances()

    yield

    ProviderRegistry._notifications = original_notifications
    ProviderRegistry._notification_instances = original_notification_instances
    ProviderRegistry._instances = original_instances
    ProviderRegistry._default_notification = original_default_notification


# =============================================================================
# A. Contract Tests
# =============================================================================


class TestProviderRegistryNotificationContract:
    """Verify default notification settings and list_providers structure."""

    def test_default_notification_name_is_logging(self):
        """Default notification adapter name is 'logging'."""
        assert ProviderRegistry._default_notification == "logging"

    def test_list_providers_contains_notification_key(self):
        """list_providers() includes 'notification' key."""
        providers = ProviderRegistry.list_providers()
        assert "notification" in providers

    def test_list_providers_notification_reflects_registered(self):
        """list_providers()['notification'] reflects registered adapters."""
        mock_factory = MagicMock()
        ProviderRegistry.register_notification("test_channel", mock_factory)

        providers = ProviderRegistry.list_providers()
        assert "test_channel" in providers["notification"]


# =============================================================================
# B. Behavior Tests
# =============================================================================


class TestProviderRegistryNotificationBehavior:
    """Verify registration, retrieval, DCL caching, and reset."""

    def test_register_notification_stores_factory(self):
        """register_notification stores the factory in _notifications."""
        mock_factory = MagicMock()
        ProviderRegistry.register_notification("slack", mock_factory)

        assert "slack" in ProviderRegistry._notifications
        assert ProviderRegistry._notifications["slack"] is mock_factory

    def test_get_notification_returns_instance(self):
        """get_notification creates and returns an adapter instance."""
        mock_adapter = MagicMock()
        mock_factory = MagicMock(return_value=mock_adapter)
        ProviderRegistry.register_notification("test", mock_factory)

        result = ProviderRegistry.get_notification("test")

        assert result is mock_adapter
        mock_factory.assert_called_once()

    def test_get_notification_caches_instance(self):
        """get_notification caches instance (DCL singleton)."""
        mock_adapter = MagicMock()
        mock_factory = MagicMock(return_value=mock_adapter)
        ProviderRegistry.register_notification("test", mock_factory)

        result1 = ProviderRegistry.get_notification("test")
        result2 = ProviderRegistry.get_notification("test")

        assert result1 is result2
        mock_factory.assert_called_once()

    def test_get_notification_uses_default_when_name_is_none(self):
        """get_notification(None) uses _default_notification."""
        from selfhealing.interfaces.notification import LoggingNotificationAdapter

        # Auto-registration should kick in
        result = ProviderRegistry.get_notification(None)
        assert isinstance(result, LoggingNotificationAdapter)

    def test_get_notification_unknown_name_raises_value_error(self):
        """get_notification with unknown name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown notification adapter"):
            ProviderRegistry.get_notification("nonexistent_channel")

    def test_auto_register_notification_adapters_registers_defaults(self):
        """_auto_register_notification_adapters registers logging and stdout."""
        ProviderRegistry._auto_register_notification_adapters()

        assert "logging" in ProviderRegistry._notifications
        assert "stdout" in ProviderRegistry._notifications

    def test_clear_instances_clears_notification_instances(self):
        """clear_instances() clears _notification_instances."""
        mock_adapter = MagicMock()
        ProviderRegistry._notification_instances["test"] = mock_adapter

        ProviderRegistry.clear_instances()

        assert len(ProviderRegistry._notification_instances) == 0

    def test_reset_clears_notifications_and_instances(self):
        """reset() clears both _notifications and _notification_instances."""
        ProviderRegistry.register_notification("x", MagicMock())
        ProviderRegistry._notification_instances["x"] = MagicMock()

        ProviderRegistry.reset()

        assert len(ProviderRegistry._notifications) == 0
        assert len(ProviderRegistry._notification_instances) == 0

    def test_reset_restores_default_notification_to_logging(self):
        """reset() restores _default_notification to 'logging'."""
        ProviderRegistry._default_notification = "custom"
        ProviderRegistry.reset()

        assert ProviderRegistry._default_notification == "logging"


class TestProviderRegistryDCLThreadSafety:
    """Verify DCL thread safety for get_notification."""

    def test_concurrent_get_notification_returns_same_instance(self):
        """Multiple threads calling get_notification get the same instance."""
        from selfhealing.interfaces.notification import LoggingNotificationAdapter

        ProviderRegistry.register_notification("logging", LoggingNotificationAdapter)

        results = []
        errors = []

        def worker():
            try:
                results.append(ProviderRegistry.get_notification("logging"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_concurrent_get_cache_returns_same_singleton(self):
        """DCL for get_cache returns same singleton across threads."""
        from selfhealing.adapters.cache import InMemoryCacheAdapter

        ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)

        results = []

        def worker():
            results.append(ProviderRegistry.get_cache(name="memory", singleton=True))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r is results[0] for r in results)
