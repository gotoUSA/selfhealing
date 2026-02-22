"""
DLQ Models and Data Classes - Backward Compatibility Shim

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.dlq.models`` instead.
    This shim will be removed in v3.0.0.

Data classes and configuration for DLQ operations.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from 'selfhealing.services.dlq_models' is deprecated. "
    "Use 'selfhealing.services.dlq.models' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all from canonical location
from selfhealing.services.dlq.models import (  # noqa: F401  # Configuration; Result Data Classes; Backward compatibility alias; Cleanup; Paginated; Replay; Resolve; Throttle-aware
    CleanupStats,
    DLQBatchReplayStats,
    DLQConfig,
    DLQEntryResult,
    DLQPaginatedResult,
    DlqReplayResult,
    DLQThrottleBatchReplayResult,
    DLQThrottleReplayResult,
    ReplayResult,
    ResolveResult,
)

__all__ = [
    "DLQConfig",
    "DLQEntryResult",
    "DLQBatchReplayStats",
    "ReplayResult",
    "CleanupStats",
    "DLQPaginatedResult",
    "DlqReplayResult",
    "ResolveResult",
    "DLQThrottleReplayResult",
    "DLQThrottleBatchReplayResult",
]
