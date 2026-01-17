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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

import requests
from selfhealing.settings import get_config

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - loaded from config
# =============================================================================


def _get_notification_limits():
    """Lazy-load notification limits from config."""
    from selfhealing.config import get_notification_limits

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
        """Load configuration from settings."""
        config = get_config()
        notification = config.notification  # Use singular form

        return cls(
            slack_webhook_url=getattr(notification, "slack_webhook_url", ""),
            slack_critical_channel=getattr(notification, "critical_channel", "#critical-alerts"),
            slack_high_channel=getattr(notification, "high_channel", "#ops-alerts"),
            slack_medium_channel=getattr(notification, "medium_channel", "#dev-alerts"),
            email_critical_recipients=getattr(notification, "email_critical_recipients", []),
            email_high_recipients=getattr(notification, "email_high_recipients", []),
            sms_critical_recipients=getattr(notification, "sms_critical_recipients", []),
            pagerduty_service_key=getattr(notification, "pagerduty_service_key", ""),
            pagerduty_enabled=getattr(notification, "pagerduty_enabled", False),
            enabled=notification.enabled,
            dry_run=getattr(notification, "dry_run", False),
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
        result = service.notify_security_incident_by_id(incident_id, "signature_invalid", "critical")
    """

    def __init__(self, config: NotificationConfig | None = None):
        """
        Initialize the notification service.

        Args:
            config: Optional configuration, loads from settings if None
        """
        self.config = config or NotificationConfig.from_settings()

    def notify_security_incident_by_id(
        self,
        incident_id: int,
        incident_type: str,
        severity: str,
        description: str = "",
        source_ip: Optional[str] = None,
        user_id: Optional[int] = None,
        action_taken: str = "",
    ) -> SecurityNotificationResult:
        """
        Send notifications for a security incident by ID.

        Routes to appropriate channels based on incident severity.

        Args:
            incident_id: The security incident ID
            incident_type: Type of incident (e.g., 'signature_invalid')
            severity: Severity level ('critical', 'high', 'medium')
            description: Description of the incident
            source_ip: Source IP address
            user_id: Associated user ID
            action_taken: Action taken in response

        Returns:
            SecurityNotificationResult with results from all channels
        """
        if not self.config.enabled:
            logger.debug("[Security Notification] Notifications disabled")
            return SecurityNotificationResult(incident_id=incident_id)

        result = SecurityNotificationResult(incident_id=incident_id)

        # Format the message
        from selfhealing.core.timezone import now

        message = self._format_incident_message_data(
            incident_id=incident_id,
            incident_type=incident_type,
            severity=severity,
            description=description,
            source_ip=source_ip,
            user_id=user_id,
            action_taken=action_taken,
            detected_at=now(),
        )

        # Route based on severity
        if severity == "critical":
            # All channels for critical
            result.add_result(self._send_slack(message, self.config.slack_critical_channel))
            result.add_result(self._send_email(message, self.config.email_critical_recipients))
            result.add_result(self._send_sms(message, self.config.sms_critical_recipients))
            if self.config.pagerduty_enabled:
                result.add_result(self._trigger_pagerduty_by_data(incident_id, incident_type, description, source_ip))

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
        logger.info(f"[Security Notification] Incident {incident_id}: " f"{success_count}/{total_count} notifications sent")

        return result

    def notify_security_incident(self, incident: Any) -> SecurityNotificationResult:
        """
        Send notifications for a security incident object (legacy API).

        This is a convenience method that extracts data from an incident object
        (e.g., Django model instance) and delegates to notify_security_incident_by_id.

        Args:
            incident: Incident object with id, incident_type, severity, description, etc.

        Returns:
            SecurityNotificationResult with results from all channels
        """
        return self.notify_security_incident_by_id(
            incident_id=incident.id,
            incident_type=getattr(incident, "incident_type", "unknown"),
            severity=getattr(incident, "severity", "medium"),
            description=getattr(incident, "description", ""),
            source_ip=getattr(incident, "source_ip", None),
            user_id=getattr(incident, "user_id", None),
            action_taken=getattr(incident, "action_taken", ""),
        )

    def _format_incident_message(self, incident: Any) -> dict[str, Any]:
        """
        Format an incident object into a structured message (legacy API).

        This is a convenience method that extracts data from an incident object
        and delegates to _format_incident_message_data.

        Args:
            incident: Incident object with id, incident_type, severity, description, etc.

        Returns:
            Formatted message dictionary
        """
        from selfhealing.core.timezone import now

        detected_at = getattr(incident, "detected_at", None) or now()
        return self._format_incident_message_data(
            incident_id=incident.id,
            incident_type=getattr(incident, "incident_type", "unknown"),
            severity=getattr(incident, "severity", "medium"),
            description=getattr(incident, "description", ""),
            source_ip=getattr(incident, "source_ip", None),
            user_id=getattr(incident, "user_id", None),
            action_taken=getattr(incident, "action_taken", ""),
            detected_at=detected_at,
        )

    def _format_incident_message_data(
        self,
        incident_id: int,
        incident_type: str,
        severity: str,
        description: str,
        source_ip: Optional[str],
        user_id: Optional[int],
        action_taken: str,
        detected_at: Any,
    ) -> dict[str, Any]:
        """
        Format incident data into a structured message.

        Applies truncation to prevent exceeding API limits:
        - Description: 500 chars max
        - Action taken: 200 chars max
        - Title: 150 chars max

        Args:
            incident_id: The security incident ID
            incident_type: Type of incident
            severity: Severity level
            description: Description of the incident
            source_ip: Source IP address
            user_id: Associated user ID
            action_taken: Action taken in response
            detected_at: Detection timestamp

        Returns:
            Formatted message dictionary
        """
        config = get_config()
        admin_url = f"{config.site_url}/admin/security-incident/{incident_id}/"

        # Truncate fields to prevent API limit issues
        desc = self._truncate_with_ellipsis(description, DESCRIPTION_MAX_LENGTH)
        action = self._truncate_with_ellipsis(action_taken, ACTION_TAKEN_MAX_LENGTH) if action_taken else "N/A"

        return {
            "title": f"🚨 Security Incident: {incident_type}"[:TITLE_MAX_LENGTH],
            "severity": severity.upper(),
            "incident_id": incident_id,
            "type": incident_type,
            "status": "open",
            "description": desc,
            "source_ip": source_ip or "N/A",
            "user_id": user_id if user_id else "N/A",
            "detected_at": detected_at.isoformat() if hasattr(detected_at, "isoformat") else str(detected_at),
            "action_taken": action,
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

        Note: Email sending requires an email provider to be configured.
        This is a placeholder that logs the email content.

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
            subject = f"[{message['severity']}] Security Incident: {message['type']}"
            body = self._format_email_body(message)

            # Email sending is delegated to the application's email infrastructure
            # The selfhealing package logs the intent but actual sending
            # should be handled by the application's email service
            logger.info(
                f"[Security Notification] Email notification prepared for {len(recipients)} recipients: " f"Subject: {subject}"
            )
            logger.debug(f"[Security Notification] Email body: {body[:200]}...")

            # Return success - actual email sending should be handled by application
            return NotificationResult(
                channel="email",
                success=True,
                message=f"Email prepared for {len(recipients)} recipients (actual sending delegated to app)",
            )

        except Exception as e:
            logger.error(f"[Security Notification] Email error: {e}")
            return NotificationResult(
                channel="email",
                success=False,
                error=str(e),
            )

    def send_alert(
        self,
        title: str,
        message: str,
        severity: str = "info",
        channels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SecurityNotificationResult:
        """
        Send a general-purpose alert notification.

        This method is for alerts that are not tied to a specific security incident,
        such as SLA drift warnings, system health alerts, or operational notifications.

        Args:
            title: Alert title (short summary)
            message: Alert message (detailed description)
            severity: Severity level ('info', 'warning', 'critical')
            channels: List of channels to send to (default: ['slack'])
            metadata: Additional context data

        Returns:
            SecurityNotificationResult with results from all channels

        Example:
            service = get_security_notification_service()
            service.send_alert(
                title="[SLA Drift] payment",
                message="SLA 위반율이 25%입니다.",
                severity="warning",
                metadata={"domain": "payment", "breach_rate": 25.0}
            )
        """
        if not self.config.enabled:
            logger.debug("[Security Notification] Notifications disabled")
            return SecurityNotificationResult(incident_id=0)

        result = SecurityNotificationResult(incident_id=0)
        channels = channels or ["slack"]
        metadata = metadata or {}

        formatted_message = self._format_alert_message(title, message, severity, metadata)

        # 채널별 알림 전송
        self._send_to_slack_if_enabled(channels, severity, formatted_message, result)
        self._send_to_email_if_enabled(channels, severity, formatted_message, result)
        self._send_to_sms_if_enabled(channels, severity, formatted_message, result)
        self._send_to_pagerduty_if_enabled(channels, severity, formatted_message, result)

        # Log results
        success_count = sum(1 for r in result.results if r.success)
        total_count = len(result.results)
        logger.info(f"[Security Notification] Alert '{title}': {success_count}/{total_count} notifications sent")

        return result
    
    def _format_alert_message(
        self,
        title: str,
        message: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """알림 메시지 포맷팅."""
        from selfhealing.core.timezone import now
        return {
            "title": self._truncate_with_ellipsis(title, TITLE_MAX_LENGTH),
            "severity": severity.upper(),
            "description": self._truncate_with_ellipsis(message, DESCRIPTION_MAX_LENGTH),
            "detected_at": now().isoformat(),
            "metadata": metadata,
        }
    
    def _send_to_slack_if_enabled(
        self,
        channels: list[str],
        severity: str,
        formatted_message: dict[str, Any],
        result: SecurityNotificationResult,
    ) -> None:
        """Slack 채널로 알림 전송."""
        if "slack" not in channels:
            return
        if severity == "critical":
            channel = self.config.slack_critical_channel
        elif severity in ("warning", "high"):
            channel = self.config.slack_high_channel
        else:
            channel = self.config.slack_medium_channel
        result.add_result(self._send_slack_alert(formatted_message, channel))
    
    def _send_to_email_if_enabled(
        self,
        channels: list[str],
        severity: str,
        formatted_message: dict[str, Any],
        result: SecurityNotificationResult,
    ) -> None:
        """Email로 알림 전송."""
        if "email" not in channels:
            return
        if severity == "critical":
            recipients = self.config.email_critical_recipients
        else:
            recipients = self.config.email_high_recipients
        if recipients:
            result.add_result(self._send_email_alert(formatted_message, recipients))
    
    def _send_to_sms_if_enabled(
        self,
        channels: list[str],
        severity: str,
        formatted_message: dict[str, Any],
        result: SecurityNotificationResult,
    ) -> None:
        """SMS로 알림 전송 (critical만)."""
        if "sms" not in channels or severity != "critical":
            return
        if self.config.sms_critical_recipients:
            result.add_result(self._send_sms_alert(formatted_message, self.config.sms_critical_recipients))
    
    def _send_to_pagerduty_if_enabled(
        self,
        channels: list[str],
        severity: str,
        formatted_message: dict[str, Any],
        result: SecurityNotificationResult,
    ) -> None:
        """PagerDuty로 알림 전송 (critical만)."""
        if "pagerduty" not in channels or severity != "critical":
            return
        if self.config.pagerduty_enabled:
            result.add_result(self._send_pagerduty_alert(formatted_message))

    def _send_slack_alert(self, message: dict[str, Any], channel: str) -> NotificationResult:
        """
        Send a Slack alert notification (general purpose).

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
            logger.info(f"[DRY RUN] Slack alert to {channel}: {message['title']}")
            return NotificationResult(
                channel="slack",
                success=True,
                message=f"[DRY RUN] Would send alert to {channel}",
            )

        try:
            slack_message = self._format_slack_alert(message, channel)
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
                    message=f"Alert sent to {channel}",
                )
            else:
                return NotificationResult(
                    channel="slack",
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text[:100]}",
                )

        except Exception as e:
            logger.error(f"[Security Notification] Slack alert error: {e}")
            return NotificationResult(
                channel="slack",
                success=False,
                error=str(e),
            )

    def _format_slack_alert(self, message: dict[str, Any], channel: str) -> dict[str, Any]:
        """
        Format alert message for Slack Block Kit.

        Args:
            message: Formatted message dictionary
            channel: Target channel

        Returns:
            Slack-formatted message
        """
        severity_emoji = {
            "CRITICAL": "🔴",
            "WARNING": "🟠",
            "HIGH": "🟠",
            "INFO": "🔵",
            "MEDIUM": "🟡",
        }.get(message["severity"], "⚪")

        blocks = [
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
                "text": {
                    "type": "mrkdwn",
                    "text": message["description"],
                },
            },
        ]

        # Add metadata if present
        if message.get("metadata"):
            fields = []
            for key, value in list(message["metadata"].items())[:4]:  # Max 4 fields
                fields.append({"type": "mrkdwn", "text": f"*{key}:*\n{value}"})
            if fields:
                blocks.append({"type": "section", "fields": fields})

        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"⏰ {message['detected_at']}"}
            ],
        })

        return {"channel": channel, "blocks": blocks}

    def _send_email_alert(self, message: dict[str, Any], recipients: list[str]) -> NotificationResult:
        """Send email alert notification."""
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Email alert to {recipients}: {message['title']}")
            return NotificationResult(
                channel="email",
                success=True,
                message=f"[DRY RUN] Would send alert to {len(recipients)} recipients",
            )

        try:
            subject = f"[{message['severity']}] {message['title']}"
            logger.info(f"[Security Notification] Email alert prepared: {subject}")
            return NotificationResult(
                channel="email",
                success=True,
                message=f"Email alert prepared for {len(recipients)} recipients",
            )
        except Exception as e:
            logger.error(f"[Security Notification] Email alert error: {e}")
            return NotificationResult(channel="email", success=False, error=str(e))

    def _send_sms_alert(self, message: dict[str, Any], recipients: list[str]) -> NotificationResult:
        """Send SMS alert notification."""
        if self.config.dry_run:
            logger.info(f"[DRY RUN] SMS alert to {recipients}: {message['title']}")
            return NotificationResult(
                channel="sms",
                success=True,
                message=f"[DRY RUN] Would send alert to {len(recipients)} recipients",
            )

        sms_body = f"[{message['severity']}] {message['title'][:50]}: {message['description'][:100]}"
        logger.info(f"[Security Notification] SMS alert would send: {sms_body}")
        return NotificationResult(
            channel="sms",
            success=True,
            message=f"SMS alert logged for {len(recipients)} recipients",
        )

    def _send_pagerduty_alert(self, message: dict[str, Any]) -> NotificationResult:
        """Send PagerDuty alert notification."""
        if not self.config.pagerduty_service_key:
            return NotificationResult(
                channel="pagerduty",
                success=False,
                error="PagerDuty service key not configured",
            )

        if self.config.dry_run:
            logger.info(f"[DRY RUN] PagerDuty alert: {message['title']}")
            return NotificationResult(
                channel="pagerduty",
                success=True,
                message="[DRY RUN] Would trigger PagerDuty alert",
            )

        try:
            import hashlib
            dedup_key = hashlib.md5(f"{message['title']}".encode()).hexdigest()[:16]

            payload = {
                "routing_key": self.config.pagerduty_service_key,
                "event_action": "trigger",
                "dedup_key": f"selfhealing-alert-{dedup_key}",
                "payload": {
                    "summary": message["title"],
                    "severity": "critical",
                    "source": "self-healing",
                    "custom_details": {
                        "description": message["description"],
                        "metadata": message.get("metadata", {}),
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
                    message="PagerDuty alert triggered",
                )
            else:
                return NotificationResult(
                    channel="pagerduty",
                    success=False,
                    error=f"HTTP {response.status_code}",
                )

        except Exception as e:
            logger.error(f"[Security Notification] PagerDuty alert error: {e}")
            return NotificationResult(channel="pagerduty", success=False, error=str(e))

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

    def _trigger_pagerduty(self, incident: Any) -> NotificationResult:
        """
        Trigger a PagerDuty incident from an incident object.

        This is a convenience method that extracts data from an incident object
        and delegates to _trigger_pagerduty_by_data.

        Args:
            incident: Incident object with id, incident_type, description, source_ip

        Returns:
            NotificationResult
        """
        return self._trigger_pagerduty_by_data(
            incident_id=incident.id,
            incident_type=getattr(incident, "incident_type", "unknown"),
            description=getattr(incident, "description", ""),
            source_ip=getattr(incident, "source_ip", None),
        )

    def _trigger_pagerduty_by_data(
        self,
        incident_id: int,
        incident_type: str,
        description: str,
        source_ip: Optional[str],
    ) -> NotificationResult:
        """
        Trigger a PagerDuty incident.

        Args:
            incident_id: The security incident ID
            incident_type: Type of incident
            description: Description of the incident
            source_ip: Source IP address

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
            logger.info(f"[DRY RUN] PagerDuty: {incident_type}")
            return NotificationResult(
                channel="pagerduty",
                success=True,
                message="[DRY RUN] Would trigger PagerDuty incident",
            )

        try:
            payload = {
                "routing_key": self.config.pagerduty_service_key,
                "event_action": "trigger",
                "dedup_key": f"security-incident-{incident_id}",
                "payload": {
                    "summary": f"Security Incident: {incident_type}",
                    "severity": "critical",
                    "source": "self-healing-security",
                    "custom_details": {
                        "incident_id": incident_id,
                        "type": incident_type,
                        "source_ip": source_ip,
                        "description": description[:500] if description else "",
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


def send_alert(
    title: str,
    message: str,
    severity: str = "info",
    channels: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SecurityNotificationResult:
    """
    Send a general-purpose alert notification.

    This is a convenience function for sending alerts that are not
    tied to a specific security incident (e.g., SLA drift, system events).

    Args:
        title: Alert title (short summary)
        message: Alert message (detailed description)
        severity: Severity level ('info', 'warning', 'critical')
        channels: List of channels to send to (default: ['slack'])
        metadata: Additional context data

    Returns:
        SecurityNotificationResult with results from all channels
    """
    service = get_security_notification_service()
    return service.send_alert(
        title=title,
        message=message,
        severity=severity,
        channels=channels,
        metadata=metadata,
    )


def notify_security_incident_by_id(
    incident_id: int,
    incident_type: str,
    severity: str,
    **kwargs: Any,
) -> SecurityNotificationResult:
    """
    Convenience function to notify about a security incident by ID.

    Args:
        incident_id: The security incident ID
        incident_type: Type of incident
        severity: Severity level
        **kwargs: Additional arguments passed to notify_security_incident_by_id

    Returns:
        SecurityNotificationResult
    """
    service = get_security_notification_service()
    return service.notify_security_incident_by_id(
        incident_id=incident_id,
        incident_type=incident_type,
        severity=severity,
        **kwargs,
    )


def notify_security_incident(incident: Any) -> SecurityNotificationResult:
    """
    Convenience function to notify about a security incident object (legacy API).

    Args:
        incident: Incident object with id, incident_type, severity, etc.

    Returns:
        SecurityNotificationResult
    """
    service = get_security_notification_service()
    return service.notify_security_incident(incident)
