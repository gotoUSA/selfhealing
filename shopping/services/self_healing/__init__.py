"""
L3 Self-Healing Services

This module provides self-healing capabilities for the shopping application.
Includes retry logic, backoff calculation, idempotency checking, DLQ management,
replay functionality, and circuit breaker management.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md (§7, §8)
Reference: docs/L3_SELF_HEALING_OPERATIONS.md (§1, §2, §9)
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
from .dlq_service import (
    DLQService,
    DLQConfig,
    DLQEntryResult,
    get_dlq_service,
    store_to_dlq,
)
from .replay_service import (
    ReplayService,
    ReplayResult,
    BatchReplayResult,
    ReplayHandler,
    get_replay_handler,
    get_replay_service,
    replay_failed_operation,
    batch_replay_by_failure_type,
)
from .circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
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
    # DLQ Service
    "DLQService",
    "DLQConfig",
    "DLQEntryResult",
    "get_dlq_service",
    "store_to_dlq",
    # Replay Service
    "ReplayService",
    "ReplayResult",
    "BatchReplayResult",
    "ReplayHandler",
    "get_replay_handler",
    "get_replay_service",
    "replay_failed_operation",
    "batch_replay_by_failure_type",
    # Circuit Breaker Service
    "CircuitBreakerService",
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
]
