"""
Self-Healing Utilities.

Provides utility functions for the self-healing system.
"""

from selfhealing.utils.time import (
    utc_now,
    ensure_aware,
    to_iso_string,
    from_iso_string,
    elapsed_seconds,
    is_expired,
    add_seconds,
    format_duration,
)
from selfhealing.utils.async_logger import (
    AsyncHealingLogger,
    EventSeverity,
)

__all__ = [
    "utc_now",
    "ensure_aware",
    "to_iso_string",
    "from_iso_string",
    "elapsed_seconds",
    "is_expired",
    "add_seconds",
    "format_duration",
    # Platinum SLA Optimization
    "AsyncHealingLogger",
    "EventSeverity",
]
