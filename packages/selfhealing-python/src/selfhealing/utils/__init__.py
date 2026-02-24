"""
Self-Healing Utilities.

Provides utility functions for the self-healing system.
"""

from selfhealing.utils.async_logger import (
    AsyncHealingLogger,
    EventSeverity,
)
from selfhealing.utils.jitter import (
    JitterConfig,
    async_sleep_with_jitter,
    calculate_jitter,
    sleep_with_jitter,
    with_jitter,
)
from selfhealing.utils.network import extract_client_ip
from selfhealing.utils.template import SafeFormatDict
from selfhealing.utils.time import (
    add_seconds,
    elapsed_seconds,
    ensure_aware,
    format_duration,
    from_iso_string,
    is_expired,
    to_iso_string,
    utc_now,
)

__all__ = [
    # Network utilities (canonical IP extraction)
    "extract_client_ip",
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
    # Jitter utilities (Thundering Herd prevention)
    "with_jitter",
    "calculate_jitter",
    "sleep_with_jitter",
    "async_sleep_with_jitter",
    "JitterConfig",
    # Template utilities (safe format_map)
    "SafeFormatDict",
]
