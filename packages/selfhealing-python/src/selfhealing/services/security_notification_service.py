"""
Backward Compatibility Wrapper for security_notification module.

DEPRECATED: This module is deprecated and will be removed in a future version.
Please import directly from the security_notification package:

    # Old (deprecated):
    from selfhealing.services.security_notification_service import SecurityNotificationService
    
    # New (recommended):
    from selfhealing.services.security_notification import SecurityNotificationService

This wrapper exists for backward compatibility with code that imports from:
    from selfhealing.services.security_notification_service import (...)
"""

from __future__ import annotations

import warnings

warnings.warn(
    "selfhealing.services.security_notification_service is deprecated. "
    "Use selfhealing.services.security_notification instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all public symbols from the package
from selfhealing.services.security_notification import (
    # Models
    NotificationChannel,
    NotificationConfig,
    NotificationResult,
    SecurityNotificationResult,
    # Constants
    SLACK_BLOCK_TEXT_LIMIT,
    DESCRIPTION_MAX_LENGTH,
    ACTION_TAKEN_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    _get_notification_limits,
    # Handlers (for extension/testing)
    SlackHandlerMixin,
    EmailHandlerMixin,
    SMSHandlerMixin,
    PagerDutyHandlerMixin,
    # Main service
    SecurityNotificationService,
    # Module-level functions
    get_security_notification_service,
    send_alert,
    notify_security_incident,
    notify_security_incident_by_id,
)

__all__ = [
    # Models
    "NotificationChannel",
    "NotificationConfig",
    "NotificationResult",
    "SecurityNotificationResult",
    # Constants
    "SLACK_BLOCK_TEXT_LIMIT",
    "DESCRIPTION_MAX_LENGTH",
    "ACTION_TAKEN_MAX_LENGTH",
    "TITLE_MAX_LENGTH",
    "_get_notification_limits",
    # Handlers
    "SlackHandlerMixin",
    "EmailHandlerMixin",
    "SMSHandlerMixin",
    "PagerDutyHandlerMixin",
    # Main service
    "SecurityNotificationService",
    # Module-level functions
    "get_security_notification_service",
    "send_alert",
    "notify_security_incident",
    "notify_security_incident_by_id",
]
