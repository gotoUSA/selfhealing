"""
PagerDuty Notification Handler.

Handles PagerDuty-specific incident triggering.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .models import (
    NotificationConfig,
    NotificationResult,
    _get_notification_limits,
)

logger = logging.getLogger(__name__)


class PagerDutyHandlerMixin:
    """Mixin providing PagerDuty notification capabilities."""

    config: NotificationConfig

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
        source_ip: str | None,
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
