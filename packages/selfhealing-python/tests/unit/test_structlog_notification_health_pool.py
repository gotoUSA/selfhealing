"""
Notification, ConnectionHealth, PoolMonitor structlog 전환 검증.

대상 모듈:
- selfhealing.interfaces.notification (LoggingNotificationAdapter)
- selfhealing.core.connection_health (DefaultConnectionHealthMonitor)
- selfhealing.core.pool_monitor (ConnectionPoolMonitor)

각 모듈이 stdlib logging 대신 structlog를 올바르게 사용하는지,
이벤트 이름·키워드 규칙이 유지되는지 검증한다.

테스트 분류:
- Contract: structlog BoundLogger API 계약 및 이벤트 이름 규칙 검증
- Behavior: 각 클래스의 로깅 동작 검증
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.interfaces.notification import (
    LoggingNotificationAdapter,
    Notification,
    NotificationSeverity,
)

# ===========================================================================
# LoggingNotificationAdapter Contract Tests
# ===========================================================================


class TestLoggingNotificationAdapterContract:
    """LoggingNotificationAdapter structlog 전환 계약 검증."""

    def test_adapter_uses_structlog_bound_logger(self):
        """adapter._logger가 structlog BoundLogger여야 한다."""
        adapter = LoggingNotificationAdapter()
        logger_type_name = type(adapter._logger).__name__
        assert "BoundLogger" in logger_type_name or "Proxy" in logger_type_name

    def test_severity_to_log_method_mapping_has_five_entries(self):
        """SEVERITY_TO_LOG_METHOD 매핑에 5개 severity 레벨이 모두 정의되어야 한다."""
        mapping = LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD
        assert len(mapping) == 5
        assert set(mapping.keys()) == {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}

    def test_critical_maps_to_critical_method(self):
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD["CRITICAL"] == "critical"

    def test_high_maps_to_error_method(self):
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD["HIGH"] == "error"

    def test_medium_maps_to_warning_method(self):
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD["MEDIUM"] == "warning"

    def test_low_maps_to_info_method(self):
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD["LOW"] == "info"

    def test_info_severity_maps_to_debug_method(self):
        assert LoggingNotificationAdapter._SEVERITY_TO_LOG_METHOD["INFO"] == "debug"


# ===========================================================================
# LoggingNotificationAdapter Behavior Tests
# ===========================================================================


class TestLoggingNotificationAdapterBehavior:
    """LoggingNotificationAdapter 로깅 동작 검증."""

    def _make_notification(self, severity: NotificationSeverity) -> Notification:
        return Notification(
            title="Test Alert",
            message="Something happened",
            severity=severity,
            source="test_component",
        )

    def test_send_critical_notification_calls_critical_method(self):
        """CRITICAL severity 알림은 logger.critical()로 기록되어야 한다."""
        mock_logger = MagicMock()
        adapter = LoggingNotificationAdapter()
        adapter._logger = mock_logger

        notification = self._make_notification(NotificationSeverity.CRITICAL)
        result = adapter.send(notification)

        assert result is True
        mock_logger.critical.assert_called_once()
        call = mock_logger.critical.call_args
        assert call.args[0] == "notification.sent"

    def test_send_high_notification_calls_error_method(self):
        """HIGH severity 알림은 logger.error()로 기록되어야 한다."""
        mock_logger = MagicMock()
        adapter = LoggingNotificationAdapter()
        adapter._logger = mock_logger

        notification = self._make_notification(NotificationSeverity.HIGH)
        adapter.send(notification)

        mock_logger.error.assert_called_once()

    def test_send_medium_notification_calls_warning_method(self):
        """MEDIUM severity 알림은 logger.warning()으로 기록되어야 한다."""
        mock_logger = MagicMock()
        adapter = LoggingNotificationAdapter()
        adapter._logger = mock_logger

        notification = self._make_notification(NotificationSeverity.MEDIUM)
        adapter.send(notification)

        mock_logger.warning.assert_called_once()

    def test_notification_event_name_is_notification_sent(self):
        """structlog 이벤트 이름이 'notification.sent' 이어야 한다."""
        mock_logger = MagicMock()
        adapter = LoggingNotificationAdapter()
        adapter._logger = mock_logger

        notification = self._make_notification(NotificationSeverity.LOW)
        adapter.send(notification)

        mock_logger.info.assert_called_once()
        event_name = mock_logger.info.call_args.args[0]
        assert event_name == "notification.sent"

    def test_notification_kwargs_include_source_and_title(self):
        """structlog 호출 kwargs에 source, title, message가 포함되어야 한다."""
        mock_logger = MagicMock()
        adapter = LoggingNotificationAdapter()
        adapter._logger = mock_logger

        notification = self._make_notification(NotificationSeverity.LOW)
        adapter.send(notification)

        call = mock_logger.info.call_args
        assert call.kwargs.get("source") == "test_component"
        assert call.kwargs.get("title") == "Test Alert"
        assert call.kwargs.get("message") == "Something happened"

    def test_send_returns_true(self):
        """send() 메서드가 True를 반환해야 한다."""
        adapter = LoggingNotificationAdapter()
        # mock으로 실제 stdout 기록 방지
        with patch.object(adapter._logger, "debug"):
            notification = self._make_notification(NotificationSeverity.INFO)
            result = adapter.send(notification)
        assert result is True


# ===========================================================================
# ConnectionHealthMonitor structlog Behavior Tests
# ===========================================================================


class TestConnectionHealthMonitorStructlogBehavior:
    """connection_health.py의 structlog 전환 동작 검증."""

    def test_simulation_override_uses_structlog_logger(self):
        """set_simulation_override 호출 시 structlog logger가 사용되어야 한다."""
        from selfhealing.core.connection_health import (
            ConnectionStatus,
            ConnectionType,
            DefaultConnectionHealthMonitor,
        )

        monitor = DefaultConnectionHealthMonitor()

        with patch("selfhealing.core.connection_health.logger") as mock_logger:
            monitor.set_simulation_override(
                ConnectionType.DATABASE,
                "primary",
                ConnectionStatus.UNHEALTHY,
            )
            mock_logger.info.assert_called_once()
            call = mock_logger.info.call_args
            assert call.args[0] == "connection_health.simulation_override_set"

    def test_clear_overrides_logs_cleared_event(self):
        """clear_all_simulation_overrides 호출 시 cleared 이벤트가 기록되어야 한다."""
        from selfhealing.core.connection_health import DefaultConnectionHealthMonitor

        monitor = DefaultConnectionHealthMonitor()

        with patch("selfhealing.core.connection_health.logger") as mock_logger:
            monitor.clear_all_simulation_overrides()
            mock_logger.info.assert_called_once()
            call = mock_logger.info.call_args
            assert call.args[0] == "connection_health.simulation_overrides_cleared"


# ===========================================================================
# PoolMonitor structlog Behavior Tests
# ===========================================================================


class TestPoolMonitorStructlogBehavior:
    """pool_monitor.py의 structlog 전환 동작 검증."""

    def test_set_simulation_override_logs_set_event(self):
        """set_simulation_override 호출 시 set 이벤트가 기록되어야 한다."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()

        with patch("selfhealing.core.pool_monitor.logger") as mock_logger:
            monitor.set_simulation_override(PoolHealthStatus.CRITICAL, None, "exp-1")
            mock_logger.info.assert_called_once()
            call = mock_logger.info.call_args
            assert call.args[0] == "pool_monitor.simulation_override_set"
            assert call.kwargs.get("pool_health_status") == PoolHealthStatus.CRITICAL.value

    def test_clear_simulation_override_logs_cleared_event(self):
        """clear_simulation_override 호출 시 cleared 이벤트가 기록되어야 한다."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        monitor = ConnectionPoolMonitor()
        monitor.set_simulation_override(PoolHealthStatus.HEALTHY, None, None)

        with patch("selfhealing.core.pool_monitor.logger") as mock_logger:
            monitor.set_simulation_override(None, None, None)
            # health_status가 None이므로 cleared 메시지
            mock_logger.info.assert_called_once()
            call = mock_logger.info.call_args
            assert call.args[0] == "pool_monitor.simulation_override_cleared"
