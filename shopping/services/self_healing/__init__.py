"""
L3 Self-Healing Services

This module provides self-healing capabilities for the shopping application.
Includes retry logic, backoff calculation, idempotency checking, and DLQ management.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md (§7, §8)
"""

from .retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryResult,
    MaxRetriesExceededError,
)
from .backoff_calculator import (
    BackoffCalculator,
    calculate_backoff,
)
from .idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyResult,
)
from .forensic_context import (
    ForensicContext,
    capture_forensic_context,
)

__all__ = [
    # Retry
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "MaxRetriesExceededError",
    # Backoff
    "BackoffCalculator",
    "calculate_backoff",
    # Idempotency
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyResult",
    # Forensic
    "ForensicContext",
    "capture_forensic_context",
]
