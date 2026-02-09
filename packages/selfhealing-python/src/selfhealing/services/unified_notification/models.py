"""
Unified Notification Models.

Enums and data classes for the notification system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# =============================================================================
# Enums and Data Classes
# =============================================================================


class NotificationPriority(str, Enum):
    """Notification priority levels."""

    CRITICAL = "critical"  # Immediate: all channels
    HIGH = "high"  # Urgent: Slack + Email
    MEDIUM = "medium"  # Normal: Slack only
    LOW = "low"  # Can be batched
    INFO = "info"  # Log only unless configured


class NotificationCategory(str, Enum):
    """Notification categories for routing and filtering."""

    SECURITY = "security"  # Security incidents
    OPERATIONS = "operations"  # Self-healing operations
    SLA = "sla"  # SLA drift and violations
    CIRCUIT_BREAKER = "circuit_breaker"  # Circuit breaker state changes
    GOVERNANCE = "governance"  # Governance checks
    APPROVAL = "approval"  # Approval requests
    REPORT = "report"  # Daily reports
    ERROR = "error"  # Task failures
    CHAOS = "chaos"  # Chaos experiment notifications


@dataclass
class NotificationPayload:
    """
    Unified notification payload.

    All notification sources should construct this payload
    for consistent handling.
    """

    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    category: NotificationCategory = NotificationCategory.OPERATIONS

    # Source information
    source: str = "unknown"  # e.g., "drift_detection", "circuit_breaker"
    task_name: str | None = None
    task_id: str | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Routing hints (can be overridden by manager)
    channels: list[str] | None = None

    # Deduplication
    dedup_key: str | None = None  # If set, used for cooldown dedup

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "message": self.message,
            "priority": self.priority.value,
            "category": self.category.value,
            "source": self.source,
            "task_name": self.task_name,
            "task_id": self.task_id,
            "metadata": self.metadata,
            "tags": self.tags,
            "timestamp": self.timestamp.isoformat(),
            "channels": self.channels,
            "dedup_key": self.dedup_key,
        }


@dataclass
class NotificationResult:
    """Result of notification attempt."""

    success: bool
    channels_sent: list[str] = field(default_factory=list)
    channels_failed: list[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "channels_sent": self.channels_sent,
            "channels_failed": self.channels_failed,
            "suppressed": self.suppressed,
            "suppression_reason": self.suppression_reason,
            "error": self.error,
        }
