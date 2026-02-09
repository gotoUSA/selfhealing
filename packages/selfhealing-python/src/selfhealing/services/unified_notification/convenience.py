"""
Unified Notification Convenience Functions.

Simple helper functions for common notification patterns.
"""

from __future__ import annotations

from .models import (
    NotificationCategory,
    NotificationPayload,
    NotificationPriority,
    NotificationResult,
)
from .service import get_unified_notification_manager


# =============================================================================
# Convenience Functions
# =============================================================================


def notify(
    title: str,
    message: str,
    priority: str = "medium",
    category: str = "operations",
    source: str = "unknown",
    **kwargs,
) -> NotificationResult:
    """
    Convenience function for sending notifications.

    Args:
        title: Notification title
        message: Notification message
        priority: Priority level (critical, high, medium, low, info)
        category: Category (security, operations, sla, etc.)
        source: Source identifier
        **kwargs: Additional payload fields

    Returns:
        NotificationResult

    Usage:
        from selfhealing.services.unified_notification import notify

        notify(
            title="SLA Drift Warning",
            message="Payment domain exceeded 20% threshold",
            priority="high",
            category="sla",
            source="drift_detection",
            metadata={"domain": "payment", "rate": 25.0},
        )
    """
    try:
        priority_enum = NotificationPriority(priority.lower())
    except ValueError:
        priority_enum = NotificationPriority.MEDIUM

    try:
        category_enum = NotificationCategory(category.lower())
    except ValueError:
        category_enum = NotificationCategory.OPERATIONS

    payload = NotificationPayload(
        title=title,
        message=message,
        priority=priority_enum,
        category=category_enum,
        source=source,
        metadata=kwargs.get("metadata", {}),
        tags=kwargs.get("tags", []),
        channels=kwargs.get("channels"),
        dedup_key=kwargs.get("dedup_key"),
    )

    manager = get_unified_notification_manager()
    return manager.notify(payload)


def notify_security(
    title: str,
    message: str,
    priority: str = "high",
    source: str = "security",
    **kwargs,
) -> NotificationResult:
    """Convenience function for security notifications."""
    return notify(
        title=title,
        message=message,
        priority=priority,
        category="security",
        source=source,
        **kwargs,
    )


def notify_sla(
    title: str,
    message: str,
    domain: str,
    priority: str = "medium",
    source: str = "sla_monitor",
    **kwargs,
) -> NotificationResult:
    """Convenience function for SLA-related notifications."""
    metadata = kwargs.get("metadata", {})
    metadata["domain"] = domain

    return notify(
        title=title,
        message=message,
        priority=priority,
        category="sla",
        source=source,
        metadata=metadata,
        dedup_key=f"sla:{domain}",
        **kwargs,
    )


def notify_error(
    title: str,
    message: str,
    error: Exception,
    source: str = "unknown",
    **kwargs,
) -> NotificationResult:
    """Convenience function for error notifications."""
    metadata = kwargs.get("metadata", {})
    metadata["error_type"] = type(error).__name__
    metadata["error_message"] = str(error)

    return notify(
        title=title,
        message=message,
        priority="high",
        category="error",
        source=source,
        metadata=metadata,
        **kwargs,
    )
