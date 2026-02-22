"""
Slack Notification Handler.

Handles Slack-specific notification sending and formatting.
"""

from __future__ import annotations

from typing import Any

import requests
import structlog

from .models import (
    ChannelDeliveryResult,
    NotificationConfig,
    _get_notification_limits,
)

logger = structlog.get_logger()


class SlackHandlerMixin:
    """Mixin providing Slack notification capabilities."""

    config: NotificationConfig

    def _send_slack(self, message: dict[str, Any], channel: str) -> ChannelDeliveryResult:
        """
        Send a Slack notification.

        Args:
            message: Formatted message dictionary
            channel: Slack channel to send to

        Returns:
            ChannelDeliveryResult
        """
        if not self.config.slack_webhook_url:
            return ChannelDeliveryResult(
                channel="slack",
                success=False,
                error="Slack webhook URL not configured",
            )

        if self.config.dry_run:
            logger.info(
                "dry_run_slack",
                channel=channel,
                message=message['title'],
            )
            return ChannelDeliveryResult(
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
                return ChannelDeliveryResult(
                    channel="slack",
                    success=True,
                    message=f"Sent to {channel}",
                )
            else:
                return ChannelDeliveryResult(
                    channel="slack",
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text[:100]}",
                )

        except Exception as e:
            logger.exception(
                "security_notification_slack_error",
                error=e,
            )
            return ChannelDeliveryResult(
                channel="slack",
                success=False,
                error=str(e),
            )

    def _format_slack_message(
        self, message: dict[str, Any], channel: str
    ) -> dict[str, Any]:
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
                        {
                            "type": "mrkdwn",
                            "text": f"*Severity:*\n{message['severity']}",
                        },
                        {"type": "mrkdwn", "text": f"*Status:*\n{message['status']}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Source IP:*\n{message['source_ip']}",
                        },
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
                            "text": f"Detected: {message['detected_at']} | "
                            f"<{message['admin_url']}|View in Admin>",
                        }
                    ],
                },
            ],
        }

    def _send_slack_alert(
        self, message: dict[str, Any], channel: str
    ) -> ChannelDeliveryResult:
        """
        Send a Slack alert notification (general purpose).

        Args:
            message: Formatted message dictionary
            channel: Slack channel to send to

        Returns:
            ChannelDeliveryResult
        """
        if not self.config.slack_webhook_url:
            return ChannelDeliveryResult(
                channel="slack",
                success=False,
                error="Slack webhook URL not configured",
            )

        if self.config.dry_run:
            logger.info(
                "dry_run_slack_alert",
                channel=channel,
                message=message['title'],
            )
            return ChannelDeliveryResult(
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
                return ChannelDeliveryResult(
                    channel="slack",
                    success=True,
                    message=f"Alert sent to {channel}",
                )
            else:
                return ChannelDeliveryResult(
                    channel="slack",
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text[:100]}",
                )

        except Exception as e:
            logger.exception(
                "security_notification_slack_alert",
                error=e,
            )
            return ChannelDeliveryResult(
                channel="slack",
                success=False,
                error=str(e),
            )

    def _format_slack_alert(
        self, message: dict[str, Any], channel: str
    ) -> dict[str, Any]:
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

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"⏰ {message['detected_at']}"}
                ],
            }
        )

        return {"channel": channel, "blocks": blocks}
