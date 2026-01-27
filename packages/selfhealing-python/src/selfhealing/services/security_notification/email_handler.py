"""
Email Notification Handler.

Handles Email-specific notification sending and formatting.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import NotificationConfig, NotificationResult

logger = logging.getLogger(__name__)


class EmailHandlerMixin:
    """Mixin providing Email notification capabilities."""

    config: NotificationConfig

    def _send_email(
        self, message: dict[str, Any], recipients: list[str]
    ) -> NotificationResult:
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
                f"[Security Notification] Email notification prepared for {len(recipients)} recipients: "
                f"Subject: {subject}"
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

    def _send_email_alert(
        self, message: dict[str, Any], recipients: list[str]
    ) -> NotificationResult:
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
