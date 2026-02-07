"""
Common type definitions for the self-healing system.

This module contains enums, dataclasses, and type aliases used across the library.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict


class FailureType(str, Enum):
    """Types of failures that can be tracked and recovered (domain-neutral)."""

    NETWORK = "network"
    DATABASE = "database"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    DATA_INTEGRITY = "data_integrity"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


class OperationStatus(str, Enum):
    """Status of a failed operation in the DLQ."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    MANUAL_REVIEW = "manual_review"


class CircuitState(str, Enum):
    """State of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class FailedOperationData:
    """Data transfer object for failed operations."""

    id: int
    domain: str
    failure_type: str
    status: str
    created_at: datetime
    context: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 3
    last_retry_at: datetime | None = None
    next_retry_at: datetime | None = None
    resolved_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class CircuitBreakerStateData:
    """Data transfer object for circuit breaker state."""

    service_name: str
    state: str  # 'closed', 'open', 'half_open'
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    opened_at: datetime | None = None
    half_opened_at: datetime | None = None
    failure_threshold: int = 5
    recovery_timeout: int = 60
    half_open_max_calls: int = 3
    # Manual control fields
    manually_controlled: bool = False
    controlled_by_id: int | None = None
    control_reason: str = ""
    manual_override_expires_at: datetime | None = None
    half_open_request_count: int = 0
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RetryContext(TypedDict, total=False):
    """Context information for retry operations."""

    attempt: int
    max_attempts: int
    delay: float
    last_error: str
    operation_id: str
    domain: str


@dataclass
class MetricsSnapshot:
    """Snapshot of self-healing metrics."""

    timestamp: datetime
    circuit_breakers_open: int = 0
    circuit_breakers_half_open: int = 0
    dlq_pending_count: int = 0
    dlq_processing_count: int = 0
    dlq_failed_count: int = 0
    replay_success_rate: float = 0.0
    total_retries: int = 0
    successful_retries: int = 0
