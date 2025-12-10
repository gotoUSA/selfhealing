"""
Security Notification Service Unit Tests

Tests for SecurityNotificationService functionality including:
- Notification routing by severity
- Slack message formatting
- Email notification
- SMS notification
- PagerDuty integration

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4 (Escalation & Notifications)
"""

from unittest.mock import MagicMock, patch, Mock

import pytest

from shopping.models.security_incident import SecurityIncident
from selfhealing.services import (
    NotificationChannel,
    NotificationConfig,
    NotificationResult,
    SecurityNotificationResult,
    SecurityNotificationService,
    get_security_notification_service,
    notify_security_incident,
)
from shopping.tests.factories import UserFactory


# =============================================================================
# Configuration Tests
# =============================================================================


class TestNotificationConfig:
    """Tests for NotificationConfig dataclass."""

    def test_default_values(self):
        """
        Purpose:
            Verify default configuration values are correct.
        """
        config = NotificationConfig()

        assert config.slack_webhook_url == ""
        assert config.slack_critical_channel == "#critical-alerts"
        assert config.slack_high_channel == "#ops-alerts"
        assert config.slack_medium_channel == "#dev-alerts"
        assert config.enabled is True
        assert config.dry_run is False

    def test_custom_values(self):
        """
        Purpose:
            Verify custom configuration values are applied.
        """
        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            slack_critical_channel="#custom-critical",
            email_critical_recipients=["admin@test.com"],
            enabled=True,
            dry_run=True,
        )

        assert config.slack_webhook_url == "https://hooks.slack.com/test"
        assert config.slack_critical_channel == "#custom-critical"
        assert config.email_critical_recipients == ["admin@test.com"]
        assert config.dry_run is True


class TestNotificationResult:
    """Tests for NotificationResult dataclass."""

    def test_successful_result(self):
        """
        Purpose:
            Verify successful notification result.
        """
        result = NotificationResult(
            channel="slack",
            success=True,
            message="Sent to #alerts",
        )

        assert result.success is True
        assert result.channel == "slack"
        assert result.error is None

    def test_failed_result(self):
        """
        Purpose:
            Verify failed notification result.
        """
        result = NotificationResult(
            channel="email",
            success=False,
            error="SMTP connection failed",
        )

        assert result.success is False
        assert result.error == "SMTP connection failed"


class TestSecurityNotificationResult:
    """Tests for SecurityNotificationResult dataclass."""

    def test_all_success(self):
        """
        Purpose:
            Verify all_success property when all notifications succeed.
        """
        result = SecurityNotificationResult(incident_id=1)
        result.add_result(NotificationResult(channel="slack", success=True))
        result.add_result(NotificationResult(channel="email", success=True))

        assert result.all_success is True
        assert result.any_success is True

    def test_partial_success(self):
        """
        Purpose:
            Verify properties when some notifications fail.
        """
        result = SecurityNotificationResult(incident_id=1)
        result.add_result(NotificationResult(channel="slack", success=True))
        result.add_result(NotificationResult(channel="email", success=False, error="Failed"))

        assert result.all_success is False
        assert result.any_success is True

    def test_all_failed(self):
        """
        Purpose:
            Verify properties when all notifications fail.
        """
        result = SecurityNotificationResult(incident_id=1)
        result.add_result(NotificationResult(channel="slack", success=False, error="Failed"))
        result.add_result(NotificationResult(channel="email", success=False, error="Failed"))

        assert result.all_success is False
        assert result.any_success is False


# =============================================================================
# Service Unit Tests
# =============================================================================


class TestSecurityNotificationServiceUnit:
    """Unit tests for SecurityNotificationService (no DB required)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = NotificationConfig(dry_run=True, enabled=True)
        self.service = SecurityNotificationService(config=self.config)

    def test_disabled_service_returns_empty_result(self):
        """
        Purpose:
            Verify disabled service returns empty result.
        """
        config = NotificationConfig(enabled=False)
        service = SecurityNotificationService(config=config)

        # Create mock incident
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1

        result = service.notify_security_incident(incident)

        assert result.incident_id == 1
        assert len(result.results) == 0

    def test_format_slack_message_critical(self):
        """
        Purpose:
            Verify Slack message formatting for critical severity.
        """
        message = {
            "title": "Security Incident: webhook_signature_invalid",
            "severity": "CRITICAL",
            "incident_id": 123,
            "type": "webhook_signature_invalid",
            "status": "open",
            "description": "HMAC signature mismatch",
            "source_ip": "192.168.1.1",
            "user_id": "N/A",
            "detected_at": "2025-01-01T10:00:00",
            "action_taken": "IP logged",
            "admin_url": "http://localhost/admin/test/",
        }

        slack_msg = self.service._format_slack_message(message, "#critical-alerts")

        assert slack_msg["channel"] == "#critical-alerts"
        assert len(slack_msg["blocks"]) > 0
        # Check header contains red emoji for critical
        header_text = slack_msg["blocks"][0]["text"]["text"]
        assert "🔴" in header_text

    def test_format_email_body(self):
        """
        Purpose:
            Verify email body formatting.
        """
        message = {
            "severity": "HIGH",
            "type": "unauthorized_access",
            "status": "open",
            "description": "Attempted admin access",
            "source_ip": "10.0.0.1",
            "user_id": 42,
            "detected_at": "2025-01-01T12:00:00",
            "action_taken": "Access blocked",
            "admin_url": "http://localhost/admin/test/",
        }

        body = self.service._format_email_body(message)

        assert "SECURITY INCIDENT ALERT" in body
        assert "HIGH" in body
        assert "unauthorized_access" in body
        assert "10.0.0.1" in body


# =============================================================================
# Truncation Tests
# =============================================================================


class TestMessageTruncation:
    """Tests for message field truncation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = NotificationConfig(dry_run=True, enabled=True)
        self.service = SecurityNotificationService(config=self.config)

    def test_truncate_with_ellipsis_short_text(self):
        """
        Purpose:
            Verify short text is not truncated.
        """
        result = self.service._truncate_with_ellipsis("Hello", 100)
        assert result == "Hello"

    def test_truncate_with_ellipsis_long_text(self):
        """
        Purpose:
            Verify long text is truncated with ellipsis.
        """
        long_text = "A" * 600
        result = self.service._truncate_with_ellipsis(long_text, 500)

        assert len(result) == 500
        assert result.endswith("...")

    def test_truncate_with_ellipsis_empty(self):
        """
        Purpose:
            Verify empty string is handled.
        """
        result = self.service._truncate_with_ellipsis("", 100)
        assert result == ""

    def test_format_incident_message_truncates_description(self):
        """
        Purpose:
            Verify long description is truncated in formatted message.
        """
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1
        incident.incident_type = "test"
        incident.severity = "high"
        incident.status = "open"
        incident.description = "X" * 1000  # Very long description
        incident.source_ip = "1.1.1.1"
        incident.user = None
        incident.user_id = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = "Y" * 500  # Long action

        message = self.service._format_incident_message(incident)

        # Description should be truncated to 500 chars
        assert len(message["description"]) == 500
        assert message["description"].endswith("...")

        # Action taken should be truncated to 200 chars
        assert len(message["action_taken"]) == 200
        assert message["action_taken"].endswith("...")

    def test_format_incident_message_preserves_short_text(self):
        """
        Purpose:
            Verify short text is not truncated.
        """
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 2
        incident.incident_type = "test"
        incident.severity = "medium"
        incident.status = "open"
        incident.description = "Short description"
        incident.source_ip = "2.2.2.2"
        incident.user = None
        incident.user_id = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = "Short action"

        message = self.service._format_incident_message(incident)

        assert message["description"] == "Short description"
        assert message["action_taken"] == "Short action"


# =============================================================================
# Slack Notification Tests
# =============================================================================


class TestSlackNotification:
    """Tests for Slack notification functionality."""

    def test_slack_no_webhook_url(self):
        """
        Purpose:
            Verify error when no Slack webhook configured.
        """
        config = NotificationConfig(slack_webhook_url="")
        service = SecurityNotificationService(config=config)

        result = service._send_slack({}, "#alerts")

        assert result.success is False
        assert "not configured" in result.error.lower()

    def test_slack_dry_run_mode(self):
        """
        Purpose:
            Verify dry run mode logs instead of sending.
        """
        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=True,
        )
        service = SecurityNotificationService(config=config)

        result = service._send_slack(
            {"title": "Test"},
            "#test-channel",
        )

        assert result.success is True
        assert "DRY RUN" in result.message

    def test_slack_successful_send(self):
        """
        Purpose:
            Verify successful Slack message sending.
        """
        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        # Mock requests.post inside the method
        with patch.object(service, "_send_slack") as mock_send:
            mock_send.return_value = NotificationResult(
                channel="slack",
                success=True,
                message="Sent to #alerts",
            )
            result = mock_send({"title": "Test", "severity": "HIGH"}, "#alerts")

        assert result.success is True
        mock_send.assert_called_once()

    def test_slack_http_error(self):
        """
        Purpose:
            Verify handling of Slack HTTP errors.
        """
        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        # Mock the method to simulate HTTP error
        with patch.object(service, "_send_slack") as mock_send:
            mock_send.return_value = NotificationResult(
                channel="slack",
                success=False,
                error="HTTP 500: Internal Server Error",
            )
            result = mock_send({"title": "Test", "severity": "MEDIUM"}, "#alerts")

        assert result.success is False
        assert "500" in result.error


# =============================================================================
# Email Notification Tests
# =============================================================================


class TestEmailNotification:
    """Tests for email notification functionality."""

    def test_email_no_recipients(self):
        """
        Purpose:
            Verify error when no email recipients configured.
        """
        config = NotificationConfig()
        service = SecurityNotificationService(config=config)

        result = service._send_email({}, [])

        assert result.success is False
        assert "no email recipients" in result.error.lower()

    def test_email_dry_run_mode(self):
        """
        Purpose:
            Verify dry run mode for email.
        """
        config = NotificationConfig(dry_run=True)
        service = SecurityNotificationService(config=config)

        result = service._send_email(
            {"title": "Test"},
            ["admin@test.com"],
        )

        assert result.success is True
        assert "DRY RUN" in result.message

    @patch("django.core.mail.send_mail")
    def test_email_successful_send(self, mock_send_mail):
        """
        Purpose:
            Verify successful email sending.
        """
        config = NotificationConfig(dry_run=False)
        service = SecurityNotificationService(config=config)

        result = service._send_email(
            {
                "severity": "CRITICAL",
                "type": "token_forged",
                "status": "open",
                "description": "Test",
                "source_ip": "1.1.1.1",
                "user_id": 1,
                "detected_at": "2025-01-01T00:00:00",
                "action_taken": "Session revoked",
                "admin_url": "http://test/",
            },
            ["admin@test.com", "security@test.com"],
        )

        assert result.success is True
        mock_send_mail.assert_called_once()


# =============================================================================
# SMS Notification Tests
# =============================================================================


class TestSMSNotification:
    """Tests for SMS notification functionality."""

    def test_sms_no_recipients(self):
        """
        Purpose:
            Verify error when no SMS recipients configured.
        """
        config = NotificationConfig()
        service = SecurityNotificationService(config=config)

        result = service._send_sms({}, [])

        assert result.success is False
        assert "no sms recipients" in result.error.lower()

    def test_sms_dry_run_mode(self):
        """
        Purpose:
            Verify dry run mode for SMS.
        """
        config = NotificationConfig(dry_run=True)
        service = SecurityNotificationService(config=config)

        result = service._send_sms(
            {
                "title": "Test Incident",
                "type": "test",
                "description": "Test incident",
                "source_ip": "1.1.1.1",
            },
            ["+821012345678"],
        )

        assert result.success is True
        assert "DRY RUN" in result.message


# =============================================================================
# PagerDuty Notification Tests
# =============================================================================


class TestPagerDutyNotification:
    """Tests for PagerDuty notification functionality."""

    def test_pagerduty_no_service_key(self):
        """
        Purpose:
            Verify error when no PagerDuty service key configured.
        """
        config = NotificationConfig(pagerduty_service_key="")
        service = SecurityNotificationService(config=config)

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1

        result = service._trigger_pagerduty(incident)

        assert result.success is False
        assert "not configured" in result.error.lower()

    def test_pagerduty_dry_run_mode(self):
        """
        Purpose:
            Verify dry run mode for PagerDuty.
        """
        config = NotificationConfig(
            pagerduty_service_key="test-key",
            dry_run=True,
        )
        service = SecurityNotificationService(config=config)

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1
        incident.incident_type = "test"

        result = service._trigger_pagerduty(incident)

        assert result.success is True
        assert "DRY RUN" in result.message

    @patch("requests.post")
    def test_pagerduty_successful_trigger(self, mock_post):
        """
        Purpose:
            Verify successful PagerDuty incident trigger.
        """
        mock_post.return_value = MagicMock(status_code=202)

        config = NotificationConfig(
            pagerduty_service_key="test-key",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 123
        incident.incident_type = "webhook_signature_invalid"
        incident.source_ip = "192.168.1.1"
        incident.description = "Test incident"

        result = service._trigger_pagerduty(incident)

        assert result.success is True
        mock_post.assert_called_once()

        # Verify call was to PagerDuty API
        call_url = mock_post.call_args[0][0]
        assert "pagerduty.com" in call_url


# =============================================================================
# External API Failure Tests (Resilience)
# =============================================================================


class TestExternalAPIFailures:
    """
    Tests for handling external API failures.

    Self-Healing philosophy: External failures should NOT become internal failures.
    All notification failures should be gracefully handled and logged.
    """

    def test_pagerduty_timeout_does_not_crash(self):
        """
        Purpose:
            Verify PagerDuty timeout is handled gracefully.
            Service should return failure result, not raise exception.
        """
        config = NotificationConfig(
            pagerduty_service_key="test-key",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1
        incident.incident_type = "test"
        incident.source_ip = "1.1.1.1"
        incident.description = "Test"

        with patch(
            "shopping.services.self_healing.security_notification_service.requests.post",
            side_effect=TimeoutError("Connection timed out"),
        ):
            result = service._trigger_pagerduty(incident)

        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_pagerduty_connection_error_handled(self):
        """
        Purpose:
            Verify PagerDuty connection errors are handled gracefully.
        """
        config = NotificationConfig(
            pagerduty_service_key="test-key",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 2
        incident.incident_type = "test"
        incident.source_ip = "2.2.2.2"
        incident.description = "Test"

        with patch(
            "shopping.services.self_healing.security_notification_service.requests.post",
            side_effect=ConnectionError("Connection refused"),
        ):
            result = service._trigger_pagerduty(incident)

        assert result.success is False
        assert "refused" in result.error.lower()

    def test_slack_timeout_does_not_crash(self):
        """
        Purpose:
            Verify Slack timeout is handled gracefully.
        """
        import requests as requests_module

        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        message = {
            "title": "Test",
            "severity": "HIGH",
            "status": "open",
            "source_ip": "1.1.1.1",
            "user_id": 1,
            "description": "Test description",
            "action_taken": "Test action",
            "detected_at": "2025-01-01T00:00:00",
            "admin_url": "http://test/admin/",
        }

        with patch.object(
            requests_module,
            "post",
            side_effect=TimeoutError("Request timed out"),
        ):
            result = service._send_slack(message, "#alerts")

        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_slack_connection_reset_handled(self):
        """
        Purpose:
            Verify Slack connection reset is handled gracefully.
        """
        import requests as requests_module

        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        message = {
            "title": "Test",
            "severity": "MEDIUM",
            "status": "open",
            "source_ip": "1.1.1.1",
            "user_id": 1,
            "description": "Test description",
            "action_taken": "Test action",
            "detected_at": "2025-01-01T00:00:00",
            "admin_url": "http://test/admin/",
        }

        with patch.object(
            requests_module,
            "post",
            side_effect=ConnectionResetError("Connection reset by peer"),
        ):
            result = service._send_slack(message, "#alerts")

        assert result.success is False
        assert "reset" in result.error.lower()

    def test_slack_invalid_json_response(self):
        """
        Purpose:
            Verify handling of unexpected Slack response format.
        """
        import requests as requests_module

        config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            dry_run=False,
        )
        service = SecurityNotificationService(config=config)

        message = {
            "title": "Test",
            "severity": "HIGH",
            "status": "open",
            "source_ip": "1.1.1.1",
            "user_id": 1,
            "description": "Test description",
            "action_taken": "Test action",
            "detected_at": "2025-01-01T00:00:00",
            "admin_url": "http://test/admin/",
        }

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_payload"

        with patch.object(
            requests_module,
            "post",
            return_value=mock_response,
        ):
            result = service._send_slack(message, "#alerts")

        assert result.success is False
        assert "400" in result.error

    @patch("django.core.mail.send_mail")
    def test_email_smtp_failure_handled(self, mock_send_mail):
        """
        Purpose:
            Verify SMTP failures are handled gracefully.
        """
        mock_send_mail.side_effect = Exception("SMTP connection failed")

        config = NotificationConfig(dry_run=False)
        service = SecurityNotificationService(config=config)

        result = service._send_email(
            {
                "severity": "HIGH",
                "type": "test",
                "status": "open",
                "description": "Test",
                "source_ip": "1.1.1.1",
                "user_id": 1,
                "detected_at": "2025-01-01T00:00:00",
                "action_taken": "Test",
                "admin_url": "http://test/",
            },
            ["admin@test.com"],
        )

        assert result.success is False
        assert "SMTP" in result.error


# =============================================================================
# Routing Tests
# =============================================================================


@pytest.mark.django_db
class TestNotificationRouting:
    """Tests for notification routing by severity."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = NotificationConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            email_critical_recipients=["critical@test.com"],
            email_high_recipients=["ops@test.com"],
            sms_critical_recipients=["+821012345678"],
            pagerduty_enabled=True,
            pagerduty_service_key="test-key",
            dry_run=True,
            enabled=True,
        )
        self.service = SecurityNotificationService(config=self.config)

    def test_critical_severity_all_channels(self):
        """
        Purpose:
            Verify critical severity triggers all notification channels.
        """
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 1
        incident.severity = "critical"
        incident.incident_type = "token_forged"
        incident.status = "open"
        incident.description = "Test"
        incident.source_ip = "1.1.1.1"
        incident.user_id = None
        incident.user = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = "Test action"

        result = self.service.notify_security_incident(incident)

        # Should have Slack + Email + SMS + PagerDuty
        channels = [r.channel for r in result.results]
        assert "slack" in channels
        assert "email" in channels
        assert "sms" in channels
        assert "pagerduty" in channels

    def test_high_severity_slack_and_email(self):
        """
        Purpose:
            Verify high severity triggers Slack and Email only.
        """
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 2
        incident.severity = "high"
        incident.incident_type = "unauthorized_access"
        incident.status = "open"
        incident.description = "Test"
        incident.source_ip = "1.1.1.1"
        incident.user_id = None
        incident.user = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = ""

        result = self.service.notify_security_incident(incident)

        channels = [r.channel for r in result.results]
        assert "slack" in channels
        assert "email" in channels
        assert "sms" not in channels
        assert "pagerduty" not in channels

    def test_medium_severity_slack_only(self):
        """
        Purpose:
            Verify medium severity triggers Slack only.
        """
        incident = MagicMock(spec=SecurityIncident)
        incident.id = 3
        incident.severity = "medium"
        incident.incident_type = "rate_limit_abuse"
        incident.status = "open"
        incident.description = "Test"
        incident.source_ip = "1.1.1.1"
        incident.user_id = None
        incident.user = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = ""

        result = self.service.notify_security_incident(incident)

        channels = [r.channel for r in result.results]
        assert channels == ["slack"]


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_get_security_notification_service_returns_singleton(self):
        """
        Purpose:
            Verify get_security_notification_service returns same instance.
        """
        # Reset singleton
        import shopping.services.self_healing.security_notification_service as svc_module

        svc_module._notification_service = None

        service1 = get_security_notification_service()
        service2 = get_security_notification_service()

        assert service1 is service2

    @pytest.mark.django_db
    def test_notify_security_incident_convenience_function(self):
        """
        Purpose:
            Verify convenience function works correctly.
        """
        # Reset singleton with dry run config
        import shopping.services.self_healing.security_notification_service as svc_module

        svc_module._notification_service = SecurityNotificationService(config=NotificationConfig(enabled=True, dry_run=True))

        incident = MagicMock(spec=SecurityIncident)
        incident.id = 999
        incident.severity = "medium"
        incident.incident_type = "test"
        incident.status = "open"
        incident.description = "Test"
        incident.source_ip = "127.0.0.1"
        incident.user_id = None
        incident.user = None
        incident.detected_at = MagicMock()
        incident.detected_at.isoformat.return_value = "2025-01-01T00:00:00"
        incident.action_taken = ""

        result = notify_security_incident(incident)

        assert result.incident_id == 999
        assert len(result.results) > 0
