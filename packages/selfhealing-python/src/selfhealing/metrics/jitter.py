"""
Jitter Utilities for Thundering Herd Prevention.

DEPRECATED: This module has been moved to selfhealing.utils.jitter.
This file is kept for backward compatibility.

Provides random delay mechanisms to prevent all instances from
hitting the database simultaneously during startup.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import warnings

# Re-export from new location for backward compatibility
from selfhealing.utils.jitter import (
    with_jitter,
    calculate_jitter,
    sleep_with_jitter,
    async_sleep_with_jitter,
    JitterConfig,
)

# Emit deprecation warning on import
warnings.warn(
    "selfhealing.metrics.jitter is deprecated. "
    "Use selfhealing.utils.jitter instead.",
    DeprecationWarning,
    stacklevel=2
)

__all__ = [
    "with_jitter",
    "calculate_jitter",
    "sleep_with_jitter",
    "async_sleep_with_jitter",
    "JitterConfig",
]
