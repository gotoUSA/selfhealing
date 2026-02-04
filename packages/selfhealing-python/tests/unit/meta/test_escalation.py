"""
EscalationManager 테스트.

PagerDuty, Slack 에스컬레이션 테스트.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest

from selfhealing.meta.config import MetaWatchdogSettings
from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
    EscalationManager,
    EscalationResult,
)


class TestEscalationLevel:
    """EscalationLevel 열거형 테스트."""

    def test_values(self):
        """값 확인."""
        assert EscalationLevel.INFO.value == "info"
        assert EscalationLevel.WARNING.value == "warning"
        assert EscalationLevel.ERROR.value == "error"
        assert EscalationLevel.CRITICAL.value == "critical"


class TestEscalationEvent:
    """EscalationEvent 데이터클래스 테스트."""

    def test_creation(self):
        """생성 테스트."""
        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test Alert",
            description="Test description",
            component="test",
        )

        assert event.level == EscalationLevel.CRITICAL
        assert event.title == "Test Alert"
        assert event.description == "Test description"
        assert event.component == "test"
        assert event.details == {}
        assert isinstance(event.timestamp, datetime)


class TestEscalationResult:
    """EscalationResult 데이터클래스 테스트."""

    def test_success_result(self):
        """성공 결과 테스트."""
        result = EscalationResult(
            success=True,
            channels_sent=["pagerduty", "slack"],
            channels_failed=[],
        )

        assert result.success is True
        assert "pagerduty" in result.channels_sent
        assert result.error_message is None

    def test_failure_result(self):
        """실패 결과 테스트."""
        result = EscalationResult(
            success=False,
            channels_sent=[],
            channels_failed=["pagerduty"],
            error_message="Network error",
        )

        assert result.success is False
        assert "pagerduty" in result.channels_failed


class TestEscalationManager:
    """EscalationManager 테스트."""

    def test_escalation_disabled(self):
        """에스컬레이션 비활성화 테스트."""
        settings = MetaWatchdogSettings(escalation_enabled=False)
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        result = manager.escalate(event)

        assert result.success is False
        assert result.error_message == "Escalation disabled"

    def test_dry_run_mode(self):
        """Dry-run 모드 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            dry_run_mode=True,
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        result = manager.escalate(event)

        assert result.success is True
        assert "dry_run" in result.channels_sent

    def test_maintenance_component_suppressed(self):
        """유지보수 컴포넌트 억제 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            maintenance_components=["redis"],
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="redis",  # 유지보수 중
        )

        result = manager.escalate(event)

        assert result.success is False
        assert result.error_message == "Component in maintenance"

    def test_cooldown_prevents_duplicate(self):
        """쿨다운이 중복 에스컬레이션 방지."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            escalation_cooldown_seconds=3600.0,
            pagerduty_routing_key="test-key",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        # 첫 번째: 성공 (Mock PagerDuty)
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 202
            mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
            mock_response.__exit__ = mock.MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result1 = manager.escalate(event)
            assert result1.success is True

        # 두 번째: 쿨다운으로 실패
        result2 = manager.escalate(event)
        assert result2.success is False
        assert result2.error_message == "Cooldown active"

    def test_reset_cooldown(self):
        """쿨다운 리셋 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            escalation_cooldown_seconds=3600.0,
            dry_run_mode=True,
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        manager.escalate(event)
        manager.reset_cooldown("test")

        # 쿨다운 리셋 후 성공
        result = manager.escalate(event)
        assert result.success is True

    @mock.patch("urllib.request.urlopen")
    def test_get_last_escalation_time(self, mock_urlopen):
        """마지막 에스컬레이션 시각 조회 테스트."""
        # urllib 응답 Mock (성공해야 _record_escalation 호출됨)
        mock_response = mock.MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"ok"
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            dry_run_mode=False,
            slack_webhook_url="https://hooks.slack.com/test",
        )
        manager = EscalationManager(settings=settings)

        # 에스컬레이션 전
        assert manager.get_last_escalation_time("test") is None

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )
        manager.escalate(event)

        # 에스컬레이션 후 (채널이 성공해야 기록됨)
        last_time = manager.get_last_escalation_time("test")
        assert last_time is not None
        assert last_time > 0

    def test_no_channels_configured(self):
        """채널 미설정 시 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            pagerduty_routing_key=None,
            slack_webhook_url=None,
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        result = manager.escalate(event)

        # 둘 다 미설정이면 실패 (채널 없음)
        assert result.success is False

    def test_warning_level_skips_pagerduty(self):
        """WARNING 레벨은 PagerDuty 스킵."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            pagerduty_routing_key="test-key",
            slack_webhook_url="https://hooks.slack.com/test",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.WARNING,  # WARNING
            title="Test",
            description="Test",
            component="test",
        )

        # Mock Slack only (PagerDuty는 호출되지 않음)
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
            mock_response.__exit__ = mock.MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = manager.escalate(event)

            assert "slack" in result.channels_sent
            assert "pagerduty" not in result.channels_sent


class TestPagerDutyIntegration:
    """PagerDuty 통합 테스트."""

    def test_pagerduty_send_success(self):
        """PagerDuty 전송 성공 테스트 (Mock)."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            pagerduty_routing_key="test-routing-key",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        # urllib.request.urlopen Mock
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 202
            mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
            mock_response.__exit__ = mock.MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = manager.escalate(event)

            assert result.success is True
            assert "pagerduty" in result.channels_sent

    def test_pagerduty_network_error(self):
        """PagerDuty 네트워크 오류 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            pagerduty_routing_key="test-routing-key",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="Test",
            description="Test",
            component="test",
        )

        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = manager.escalate(event)

            assert "pagerduty" in result.channels_failed


class TestSlackIntegration:
    """Slack 통합 테스트."""

    def test_slack_send_success(self):
        """Slack 전송 성공 테스트 (Mock)."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            slack_webhook_url="https://hooks.slack.com/test",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.WARNING,
            title="Test",
            description="Test",
            component="test",
        )

        # urllib.request.urlopen Mock
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
            mock_response.__exit__ = mock.MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = manager.escalate(event)

            assert result.success is True
            assert "slack" in result.channels_sent

    def test_slack_network_error(self):
        """Slack 네트워크 오류 테스트."""
        settings = MetaWatchdogSettings(
            escalation_enabled=True,
            slack_webhook_url="https://hooks.slack.com/test",
        )
        manager = EscalationManager(settings=settings)

        event = EscalationEvent(
            level=EscalationLevel.WARNING,
            title="Test",
            description="Test",
            component="test",
        )

        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = manager.escalate(event)

            assert "slack" in result.channels_failed
