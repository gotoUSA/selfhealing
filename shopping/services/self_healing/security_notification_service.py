"""
Security Notification Service

Handles security-related notifications across multiple channels:
- Slack alerts
- Email notifications
- SMS alerts (critical only)
- PagerDuty integration (critical only)

Notifications are routed based on incident severity:
- CRITICAL: Slack + Email + SMS + PagerDuty
- HIGH: Slack + Email
- MEDIUM: Slack only

Message Truncation:
- Slack API has a 3000 character limit per block
- Description is truncated to 500 characters
- Action taken is truncated to 200 characters

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4 (Escalation & Notifications)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import requests
from django.conf import settings

if TYPE_CHECKING:
    from shopping.models.security_incident import SecurityIncident

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - loaded from config
# =============================================================================


def _get_notification_limits():
    """Lazy-load notification limits from config."""
    from shopping.services.self_healing.config import get_notification_limits

    return get_notification_limits()


# For backward compatibility, expose as module-level but load lazily
# Use _get_notification_limits() for actual values
SLACK_BLOCK_TEXT_LIMIT = 3000  # Deprecated: use _get_notification_limits()
DESCRIPTION_MAX_LENGTH = 500  # Deprecated: use _get_notification_limits()
ACTION_TAKEN_MAX_LENGTH = 200  # Deprecated: use _get_notification_limits()
TITLE_MAX_LENGTH = 150  # Deprecated: use _get_notification_limits()


# =============================================================================
# Configuration
# =============================================================================


class NotificationChannel(str, Enum):
    """Available notification channels."""

    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"
    PAGERDUTY = "pagerduty"


@dataclass
class NotificationConfig:
    """Configuration for security notifications."""

    # Slack configuration
    slack_webhook_url: str = ""
    slack_critical_channel: str = "#critical-alerts"
    slack_high_channel: str = "#ops-alerts"
    slack_medium_channel: str = "#dev-alerts"

    # Email configuration
    email_critical_recipients: list[str] = field(default_factory=list)
    email_high_recipients: list[str] = field(default_factory=list)

    # SMS configuration
    sms_critical_recipients: list[str] = field(default_factory=list)

    # PagerDuty configuration
    pagerduty_service_key: str = ""
    pagerduty_enabled: bool = False

    # General settings
    enabled: bool = True
    dry_run: bool = False  # For testing - log instead of send

    @classmethod
    def from_settings(cls) -> "NotificationConfig":
        """Load configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        notifications = self_healing.get("NOTIFICATIONS", {})

        email_recipients = notifications.get("EMAIL_RECIPIENTS", {})
        sms_recipients = notifications.get("SMS_RECIPIENTS", {})

        return cls(
            slack_webhook_url=notifications.get("SLACK_WEBHOOK_URL", ""),
            slack_critical_channel=notifications.get("CRITICAL_CHANNEL", "#critical-alerts"),
            slack_high_channel=notifications.get("HIGH_CHANNEL", "#ops-alerts"),
            slack_medium_channel=notifications.get("MEDIUM_CHANNEL", "#dev-alerts"),
            email_critical_recipients=email_recipients.get("critical", []),
            email_high_recipients=email_recipients.get("high", []),
            sms_critical_recipients=sms_recipients.get("critical", []),
            pagerduty_service_key=notifications.get("PAGERDUTY_SERVICE_KEY", ""),
            pagerduty_enabled=notifications.get("PAGERDUTY_ENABLED", False),
            enabled=notifications.get("ENABLED", True),
            dry_run=notifications.get("DRY_RUN", False),
        )


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class NotificationResult:
    """Result of a notification attempt."""

    channel: str
    success: bool
    message: str = ""
    error: str | None = None


@dataclass
class SecurityNotificationResult:
    """Aggregate result of all notification attempts."""

    incident_id: int
    results: list[NotificationResult] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        """Check if all notifications were successful."""
        return all(r.success for r in self.results)

    @property
    def any_success(self) -> bool:
        """Check if any notification was successful."""
        return any(r.success for r in self.results)

    def add_result(self, result: NotificationResult) -> None:
        """Add a notification result."""
        self.results.append(result)


# =============================================================================
# Security Notification Service
# =============================================================================


class SecurityNotificationService:
    """
    Service for sending security-related notifications.

    Routes notifications to appropriate channels based on severity:
    - CRITICAL: All channels (Slack, Email, SMS, PagerDuty)
    - HIGH: Slack + Email
    - MEDIUM: Slack only

    Usage:
        service = SecurityNotificationService()
        result = service.notify_security_incident(incident)
    """

    def __init__(self, config: NotificationConfig | None = None):
        """
        Initialize the notification service.

        Args:
            config: Optional configuration, loads from settings if None
        """
        self.config = config or NotificationConfig.from_settings()

    def notify_security_incident(self, incident: "SecurityIncident") -> SecurityNotificationResult:
        """
        Send notifications for a security incident.

        Routes to appropriate channels based on incident severity.

        Args:
            incident: The security incident to notify about

        Returns:
            SecurityNotificationResult with results from all channels
        """
        if not self.config.enabled:
            logger.debug("[Security Notification] Notifications disabled")
            return SecurityNotificationResult(incident_id=incident.id)

        result = SecurityNotificationResult(incident_id=incident.id)
        severity = incident.severity

        # Format the message
        message = self._format_incident_message(incident)

        # Route based on severity
        if severity == "critical":
            # All channels for critical
            result.add_result(self._send_slack(message, self.config.slack_critical_channel))
            result.add_result(self._send_email(message, self.config.email_critical_recipients))
            result.add_result(self._send_sms(message, self.config.sms_critical_recipients))
            if self.config.pagerduty_enabled:
                result.add_result(self._trigger_pagerduty(incident))

        elif severity == "high":
            # Slack + Email for high
            result.add_result(self._send_slack(message, self.config.slack_high_channel))
            result.add_result(self._send_email(message, self.config.email_high_recipients))

        else:  # medium and below
            # Slack only for medium
            result.add_result(self._send_slack(message, self.config.slack_medium_channel))

        # Log results
        success_count = sum(1 for r in result.results if r.success)
        total_count = len(result.results)
        logger.info(f"[Security Notification] Incident {incident.id}: " f"{success_count}/{total_count} notifications sent")

        return result

    def _format_incident_message(self, incident: "SecurityIncident") -> dict[str, Any]:
        """
        Format incident into a structured message.

        Applies truncation to prevent exceeding API limits:
        - Description: 500 chars max
        - Action taken: 200 chars max
        - Title: 150 chars max

        Args:
            incident: The security incident

        Returns:
            Formatted message dictionary
        """
        admin_url = self._get_admin_url(incident)

        # Truncate fields to prevent API limit issues
        description = self._truncate_with_ellipsis(incident.description, DESCRIPTION_MAX_LENGTH)
        action_taken = (
            self._truncate_with_ellipsis(incident.action_taken, ACTION_TAKEN_MAX_LENGTH) if incident.action_taken else "N/A"
        )

        return {
            "title": f"🚨 Security Incident: {incident.incident_type}"[:TITLE_MAX_LENGTH],
            "severity": incident.severity.upper(),
            "incident_id": incident.id,
            "type": incident.incident_type,
            "status": incident.status,
            "description": description,
            "source_ip": incident.source_ip or "N/A",
            "user_id": incident.user_id if incident.user else "N/A",
            "detected_at": incident.detected_at.isoformat(),
            "action_taken": action_taken,
            "admin_url": admin_url,
        }

    def _truncate_with_ellipsis(self, text: str, max_length: int) -> str:
        """
        Truncate text with ellipsis if it exceeds max length.

        Args:
            text: Text to truncate
            max_length: Maximum allowed length

        Returns:
            Truncated text with ellipsis if needed
        """
        if not text:
            return ""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    def _get_admin_url(self, incident: "SecurityIncident") -> str:
        """
        Generate admin URL for the incident.

        Args:
            incident: The security incident

        Returns:
            Admin URL string
        """
        base_url = getattr(settings, "SITE_URL", "http://localhost:8000")
        return f"{base_url}/admin/shopping/securityincident/{incident.id}/change/"

    def _send_slack(self, message: dict[str, Any], channel: str) -> NotificationResult:
        """
        Send a Slack notification.

        Args:
            message: Formatted message dictionary
            channel: Slack channel to send to

        Returns:
            NotificationResult
        """
        if not self.config.slack_webhook_url:
            return NotificationResult(
                channel="slack",
                success=False,
                error="Slack webhook URL not configured",
            )

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Slack to {channel}: {message['title']}")
            return NotificationResult(
                channel="slack",
                success=True,
                message=f"[DRY RUN] Would send to {channel}",
            )

        try:
            # Format for Slack
            slack_message = self._format_slack_message(message, channel)
            limits = _get_notification_limits()

            response = requests.post(
                self.config.slack_webhook_url,
                json=slack_message,
                timeout=limits.notification_timeout_seconds,
            )

            if response.status_code == 200:
                return NotificationResult(
                    channel="slack",
                    success=True,
                    message=f"Sent to {channel}",
                )
            else:
                return NotificationResult(
                    channel="slack",
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text[:100]}",
                )

        except Exception as e:
            logger.error(f"[Security Notification] Slack error: {e}")
            return NotificationResult(
                channel="slack",
                success=False,
                error=str(e),
            )

    def _format_slack_message(self, message: dict[str, Any], channel: str) -> dict[str, Any]:
        """
        Format message for Slack Block Kit.

        Args:
            message: Formatted message dictionary
            channel: Target channel

        Returns:
            Slack-formatted message
        """
        severity_emoji = {
            "CRITICAL": "🔴",
            "HIGH": "🟠",
            "MEDIUM": "🟡",
        }.get(message["severity"], "⚪")

        return {
            "channel": channel,
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{severity_emoji} {message['title']}",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Severity:*\n{message['severity']}"},
                        {"type": "mrkdwn", "text": f"*Status:*\n{message['status']}"},
                        {"type": "mrkdwn", "text": f"*Source IP:*\n{message['source_ip']}"},
                        {"type": "mrkdwn", "text": f"*User ID:*\n{message['user_id']}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Description:*\n{message['description']}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Action Taken:*\n{message['action_taken']}",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Detected: {message['detected_at']} | " f"<{message['admin_url']}|View in Admin>",
                        }
                    ],
                },
            ],
        }

    def _send_email(self, message: dict[str, Any], recipients: list[str]) -> NotificationResult:
        """
        Send an email notification.

        Args:
            message: Formatted message dictionary
            recipients: List of email addresses

        Returns:
            NotificationResult
        """
        if not recipients:
            return NotificationResult(
                channel="email",
                success=False,
                error="No email recipients configured",
            )

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Email to {recipients}: {message['title']}")
            return NotificationResult(
                channel="email",
                success=True,
                message=f"[DRY RUN] Would send to {len(recipients)} recipients",
            )

        try:
            from django.core.mail import send_mail

            subject = f"[{message['severity']}] Security Incident: {message['type']}"
            body = self._format_email_body(message)

            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                fail_silently=False,
            )

            return NotificationResult(
                channel="email",
                success=True,
                message=f"Sent to {len(recipients)} recipients",
            )

        except Exception as e:
            logger.error(f"[Security Notification] Email error: {e}")
            return NotificationResult(
                channel="email",
                success=False,
                error=str(e),
            )

    def _format_email_body(self, message: dict[str, Any]) -> str:
        """
        Format message for email body.

        Args:
            message: Formatted message dictionary

        Returns:
            Plain text email body
        """
        return f"""
SECURITY INCIDENT ALERT
=======================

Severity: {message['severity']}
Type: {message['type']}
Status: {message['status']}

Description:
{message['description']}

Source IP: {message['source_ip']}
User ID: {message['user_id']}
Detected At: {message['detected_at']}

Action Taken:
{message['action_taken']}

View in Admin: {message['admin_url']}

---
This is an automated security alert. Do not reply to this email.
        """.strip()

    def _send_sms(self, message: dict[str, Any], recipients: list[str]) -> NotificationResult:
        """
        Send SMS notification (critical incidents only).

        Args:
            message: Formatted message dictionary
            recipients: List of phone numbers

        Returns:
            NotificationResult
        """
        if not recipients:
            return NotificationResult(
                channel="sms",
                success=False,
                error="No SMS recipients configured",
            )

        if self.config.dry_run:
            logger.info(f"[DRY RUN] SMS to {recipients}: {message['title']}")
            return NotificationResult(
                channel="sms",
                success=True,
                message=f"[DRY RUN] Would send to {len(recipients)} recipients",
            )

        try:
            # SMS content must be short
            sms_body = f"[SECURITY] {message['type']}: {message['description'][:100]}. " f"IP: {message['source_ip']}"

            # Here you would integrate with your SMS provider (Twilio, AWS SNS, etc.)
            # For now, we log it
            logger.info(f"[Security Notification] SMS would send to {recipients}: {sms_body}")

            # Placeholder for actual SMS integration
            # Example with Twilio:
            # from twilio.rest import Client
            # client = Client(account_sid, auth_token)
            # for recipient in recipients:
            #     client.messages.create(body=sms_body, from_=from_number, to=recipient)

            return NotificationResult(
                channel="sms",
                success=True,
                message=f"SMS logged for {len(recipients)} recipients (integration pending)",
            )

        except Exception as e:
            logger.error(f"[Security Notification] SMS error: {e}")
            return NotificationResult(
                channel="sms",
                success=False,
                error=str(e),
            )

    def _trigger_pagerduty(self, incident: "SecurityIncident") -> NotificationResult:
        """
        Trigger a PagerDuty incident.

        Args:
            incident: The security incident

        Returns:
            NotificationResult
        """
        if not self.config.pagerduty_service_key:
            return NotificationResult(
                channel="pagerduty",
                success=False,
                error="PagerDuty service key not configured",
            )

        if self.config.dry_run:
            logger.info(f"[DRY RUN] PagerDuty: {incident.incident_type}")
            return NotificationResult(
                channel="pagerduty",
                success=True,
                message="[DRY RUN] Would trigger PagerDuty incident",
            )

        try:
            payload = {
                "routing_key": self.config.pagerduty_service_key,
                "event_action": "trigger",
                "dedup_key": f"security-incident-{incident.id}",
                "payload": {
                    "summary": f"Security Incident: {incident.incident_type}",
                    "severity": "critical",
                    "source": "self-healing-security",
                    "custom_details": {
                        "incident_id": incident.id,
                        "type": incident.incident_type,
                        "source_ip": incident.source_ip,
                        "description": incident.description[:500],
                    },
                },
            }
            limits = _get_notification_limits()

            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=limits.notification_timeout_seconds,
            )

            if response.status_code in (200, 202):
                return NotificationResult(
                    channel="pagerduty",
                    success=True,
                    message="PagerDuty incident triggered",
                )
            else:
                return NotificationResult(
                    channel="pagerduty",
                    success=False,
                    error=f"HTTP {response.status_code}",
                )

        except Exception as e:
            logger.error(f"[Security Notification] PagerDuty error: {e}")
            return NotificationResult(
                channel="pagerduty",
                success=False,
                error=str(e),
            )


# =============================================================================
# Module-level Helper Functions
# =============================================================================


_notification_service: SecurityNotificationService | None = None


def get_security_notification_service() -> SecurityNotificationService:
    """Get or create the singleton security notification service."""
    global _notification_service
    if _notification_service is None:
        _notification_service = SecurityNotificationService()
    return _notification_service


def notify_security_incident(incident: "SecurityIncident") -> SecurityNotificationResult:
    """
    Convenience function to notify about a security incident.

    Args:
        incident: The security incident

    Returns:
        SecurityNotificationResult
    """
    service = get_security_notification_service()
    return service.notify_security_incident(incident)
