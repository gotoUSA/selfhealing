"""
Security Notification Service.

Core service class that combines all notification handlers.

Handles security-related notifications across multiple channels:
- Slack alerts
- Email notifications
- SMS alerts (critical only)
- PagerDuty integration (critical only)

심각도에 따라 적절한 채널로 알림을 라우팅합니다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from selfhealing.settings import get_config

from .models import (
    NotificationConfig,
    NotificationResult,
    SecurityNotificationResult,
    _get_notification_limits,
)
from .slack_handler import SlackHandlerMixin
from .email_handler import EmailHandlerMixin
from .sms_handler import SMSHandlerMixin
from .pagerduty_handler import PagerDutyHandlerMixin

logger = logging.getLogger(__name__)


class SecurityNotificationService(
    SlackHandlerMixin,
    EmailHandlerMixin,
    SMSHandlerMixin,
    PagerDutyHandlerMixin,
):
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

        from selfhealing.core.timezone import now

        # Get notification limits from config (not deprecated constants)
        limits = _get_notification_limits()
        title_max = limits.title_max_length
        description_max = limits.description_max_length

        # Format message for Slack
        formatted_message = {
            "title": self._truncate_with_ellipsis(title, title_max),
            "severity": severity.upper(),
            "description": self._truncate_with_ellipsis(message, description_max),
            "detected_at": now().isoformat(),
            "metadata": metadata,
        }

        # Determine channel based on severity
        if "slack" in channels:
            if severity == "critical":
                channel = self.config.slack_critical_channel
            elif severity in ("warning", "high"):
                channel = self.config.slack_high_channel
            else:
                channel = self.config.slack_medium_channel
            result.add_result(self._send_slack_alert(formatted_message, channel))

        if "email" in channels:
            if severity == "critical":
                recipients = self.config.email_critical_recipients
            else:
                recipients = self.config.email_high_recipients
            if recipients:
                result.add_result(self._send_email_alert(formatted_message, recipients))

        if "sms" in channels and severity == "critical":
            if self.config.sms_critical_recipients:
                result.add_result(self._send_sms_alert(formatted_message, self.config.sms_critical_recipients))

        if "pagerduty" in channels and severity == "critical":
            if self.config.pagerduty_enabled:
                result.add_result(self._send_pagerduty_alert(formatted_message))

        # Log results
        success_count = sum(1 for r in result.results if r.success)
        total_count = len(result.results)
        logger.info(f"[Security Notification] Alert '{title}': {success_count}/{total_count} notifications sent")

        return result

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
        from .models import _get_notification_limits
        
        # Get notification limits from config (not deprecated constants)
        limits = _get_notification_limits()
        description_max = limits.description_max_length
        action_taken_max = limits.action_taken_max_length
        title_max = limits.title_max_length
        
        config = get_config()
        admin_url = f"{config.site_url}/admin/security-incident/{incident_id}/"

        # Truncate fields to prevent API limit issues
        desc = self._truncate_with_ellipsis(description, description_max)
        action = self._truncate_with_ellipsis(action_taken, action_taken_max) if action_taken else "N/A"

        return {
            "title": f"🚨 Security Incident: {incident_type}"[:title_max],
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
