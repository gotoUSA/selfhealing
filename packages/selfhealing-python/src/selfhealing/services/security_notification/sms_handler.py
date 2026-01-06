"""
SMS Notification Handler.

Handles SMS-specific notification sending.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import NotificationConfig, NotificationResult

logger = logging.getLogger(__name__)


class SMSHandlerMixin:
    """Mixin providing SMS notification capabilities."""

    config: NotificationConfig

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
