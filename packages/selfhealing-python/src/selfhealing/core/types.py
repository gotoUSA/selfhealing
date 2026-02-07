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
