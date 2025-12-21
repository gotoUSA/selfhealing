"""
Forensic Context Capture

Captures detailed context for debugging and recovery.
All failures in the self-healing layer should include forensic context.

Context includes:
- Timing information (request/response timestamps, latency)
- Retry history (attempts, errors, backoff times)
- State snapshots (before/after operation states)
- Request/Response data
- Task/Worker context

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §6
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from selfhealing.core.timezone import now

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class RetryAttempt:
    """Record of a single retry attempt."""

    attempt: int
    error_code: str
    error_message: str
    attempted_at: str
    backoff_seconds: int


@dataclass
class StateSnapshot:
    """
    Generic snapshot of entity states for forensic analysis.

    Domain-neutral design: stores arbitrary key-value state data
    instead of hardcoded order/payment/user fields.
    """

    state_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return self.state_data.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateSnapshot":
        """Create StateSnapshot from dictionary."""
        return cls(state_data=data)


@dataclass
class ForensicContext:
    """
    Complete forensic context for a failed operation.

    This captures all information needed to:
    1. Understand what happened
    2. Reproduce the issue if needed
    3. Recover without accessing original records
    4. Audit for compliance
    """

    # Timing
    request_timestamp: str = ""
    response_timestamp: str = ""
    latency_ms: int = 0

    # Retry History
    retry_history: list[RetryAttempt] = field(default_factory=list)

    # State Snapshots
    state_before: StateSnapshot | None = None
    state_after: StateSnapshot | None = None

    # Request Context
    client_ip: str = ""
    user_agent: str = ""
    session_id: str = ""

    # Task Context
    task_name: str = ""
    task_id: str = ""
    queue_name: str = ""
    worker_id: str = ""

    # External System
    external_request_id: str = ""
    external_response_code: int | None = None
    external_response_body: str = ""

    # Additional data
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Convert to metadata dictionary for DLQ storage."""
        return {
            # Timing
            "request_timestamp": self.request_timestamp,
            "response_timestamp": self.response_timestamp,
            "latency_ms": self.latency_ms,
            # Retry History
            "retry_history": [
                {
                    "attempt": r.attempt,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                    "attempted_at": r.attempted_at,
                    "backoff_seconds": r.backoff_seconds,
                }
                for r in self.retry_history
            ],
            # State Snapshots
            "state_before": self.state_before.to_dict() if self.state_before else {},
            "state_after": self.state_after.to_dict() if self.state_after else {},
            # Request Context
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            # Task Context
            "task_name": self.task_name,
            "task_id": self.task_id,
            "queue_name": self.queue_name,
            "worker_id": self.worker_id,
            # External System
            "external_request_id": self.external_request_id,
            "external_response_code": self.external_response_code,
            "external_response_body": self._truncate_response_body(self.external_response_body),
            # Extra
            **self.extra,
        }

    @staticmethod
    def _truncate_response_body(body: str) -> str:
        """Truncate response body to configured max length."""
        if not body:
            return ""
        from selfhealing.config import get_forensic_settings

        max_length = get_forensic_settings().response_body_max_length
        return body[:max_length]

    def add_retry_attempt(
        self,
        attempt: int,
        error_code: str,
        error_message: str,
        backoff_seconds: int = 0,
    ) -> None:
        """
        Add a retry attempt to the history.

        Args:
            attempt: Attempt number
            error_code: Error code from the failure
            error_message: Human-readable error message
            backoff_seconds: Seconds waited before this attempt
        """
        from selfhealing.config import get_forensic_settings

        max_length = get_forensic_settings().error_message_max_length
        self.retry_history.append(
            RetryAttempt(
                attempt=attempt,
                error_code=error_code,
                error_message=error_message[:max_length],
                attempted_at=now().isoformat(),
                backoff_seconds=backoff_seconds,
            )
        )

    def capture_state_before(
        self,
        state_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Capture state snapshot before operation.

        Args:
            state_data: Dictionary of state key-value pairs
            **kwargs: Additional state fields as keyword arguments

        Example:
            ctx.capture_state_before(
                entity_status="pending",
                user_id=123,
                amount=50000,
            )
        """
        data = state_data.copy() if state_data else {}
        data.update(kwargs)
        self.state_before = StateSnapshot(state_data=data)

    def capture_state_after(
        self,
        state_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Capture state snapshot after operation (or failure).

        Args:
            state_data: Dictionary of state key-value pairs
            **kwargs: Additional state fields as keyword arguments

        Example:
            ctx.capture_state_after(
                entity_status="failed",
                error_code="TIMEOUT",
            )
        """
        data = state_data.copy() if state_data else {}
        data.update(kwargs)
        self.state_after = StateSnapshot(state_data=data)


class ForensicContextBuilder:
    """
    Builder for creating ForensicContext with fluent API.

    Usage:
        context = (
            ForensicContextBuilder()
            .with_request(request)
            .with_task(task_id, task_name)
            .with_state_before(order, payment, user)
            .build()
        )
    """

    def __init__(self):
        self._context = ForensicContext()
        self._start_time: float | None = None

    def start_timing(self) -> "ForensicContextBuilder":
        """Start timing the operation."""
        self._start_time = time.time()
        self._context.request_timestamp = now().isoformat()
        return self

    def end_timing(self) -> "ForensicContextBuilder":
        """End timing and calculate latency."""
        self._context.response_timestamp = now().isoformat()
        if self._start_time:
            self._context.latency_ms = int((time.time() - self._start_time) * 1000)
        return self

    def with_request(self, request: Any) -> "ForensicContextBuilder":
        """
        Add request context from HTTP request (framework-agnostic).

        Args:
            request: HTTP request object (Django, Flask, etc.)
        """
        if hasattr(request, "META"):
            from selfhealing.core.config import get_config

            max_length = get_config().forensic.user_agent_max_length
            self._context.client_ip = self._get_client_ip(request)
            self._context.user_agent = request.META.get("HTTP_USER_AGENT", "")[:max_length]
            self._context.session_id = request.session.session_key if hasattr(request, "session") else ""
        return self

    def with_task(
        self,
        task_id: str = "",
        task_name: str = "",
        queue_name: str = "",
        worker_id: str = "",
    ) -> "ForensicContextBuilder":
        """
        Add Celery task context.

        Args:
            task_id: Celery task ID
            task_name: Task function name
            queue_name: Queue the task was in
            worker_id: Worker that processed the task
        """
        self._context.task_id = task_id
        self._context.task_name = task_name
        self._context.queue_name = queue_name
        self._context.worker_id = worker_id
        return self

    def with_external_response(
        self,
        request_id: str = "",
        response_code: int | None = None,
        response_body: str = "",
    ) -> "ForensicContextBuilder":
        """
        Add external system response data.

        Args:
            request_id: External system's request/transaction ID
            response_code: HTTP status code or error code
            response_body: Response body (will be truncated)
        """
        from selfhealing.config import get_forensic_settings

        max_length = get_forensic_settings().response_body_max_length
        self._context.external_request_id = request_id
        self._context.external_response_code = response_code
        self._context.external_response_body = response_body[:max_length]
        return self

    def with_state_before(
        self,
        state_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> "ForensicContextBuilder":
        """
        Capture state before operation.

        Args:
            state_data: Dictionary of state key-value pairs
            **kwargs: Additional state fields
        """
        self._context.capture_state_before(state_data, **kwargs)
        return self

    def with_state_after(
        self,
        state_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> "ForensicContextBuilder":
        """
        Capture state after operation.

        Args:
            state_data: Dictionary of state key-value pairs
            **kwargs: Additional state fields
        """
        self._context.capture_state_after(state_data, **kwargs)
        return self

    def with_extra(self, **kwargs: Any) -> "ForensicContextBuilder":
        """Add extra context data."""
        self._context.extra.update(kwargs)
        return self

    def build(self) -> ForensicContext:
        """Build the ForensicContext."""
        return self._context

    @staticmethod
    def _get_client_ip(request: Any) -> str:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")


def capture_forensic_context(
    state_before: dict[str, Any] | None = None,
    request: Any = None,
    task_id: str = "",
    task_name: str = "",
) -> ForensicContext:
    """
    Convenience function to capture forensic context (domain-neutral).

    Args:
        state_before: Initial state as dictionary
        request: HTTP request object (Django, Flask, etc.)
        task_id: Celery task ID
        task_name: Celery task name

    Returns:
        ForensicContext with captured data
    """
    builder = ForensicContextBuilder()

    if request:
        builder.with_request(request)

    if task_id or task_name:
        builder.with_task(task_id=task_id, task_name=task_name)

    if state_before:
        builder.with_state_before(state_before)

    return builder.build()


def create_snapshot_data(
    entity_data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Create snapshot data for DLQ storage (domain-neutral).

    This captures the essential data needed to recover the operation
    without accessing the original records.

    Args:
        entity_data: Dictionary of entity data to include
        **kwargs: Additional key-value pairs to include

    Returns:
        Dictionary with snapshot data

    Example:
        snapshot = create_snapshot_data(
            entity_type="order",
            entity_id="12345",
            status="pending",
            amount=50000,
            user_id=123,
        )
    """
    snapshot: dict[str, Any] = {}

    if entity_data:
        snapshot.update(entity_data)

    snapshot.update(kwargs)

    return snapshot


def create_snapshot_from_entity(
    entity: Any,
    fields: list[str] | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create snapshot data from any entity with specified fields.

    Domain-neutral helper that extracts specified fields from an entity.

    Args:
        entity: Any object with attributes to snapshot
        fields: List of field names to extract (defaults to ['id', 'status'])
        extra_data: Additional data to include in snapshot

    Returns:
        Dictionary with snapshot data

    Example:
        snapshot = create_snapshot_from_entity(
            order,
            fields=['id', 'status', 'total_amount', 'user_id'],
            extra_data={'context': 'payment_flow'}
        )
    """
    if fields is None:
        fields = ['id', 'status']

    snapshot: dict[str, Any] = {}

    for field_name in fields:
        if hasattr(entity, field_name):
            value = getattr(entity, field_name)
            # Convert Decimal to string for JSON serialization
            if isinstance(value, Decimal):
                value = str(value)
            snapshot[field_name] = value

    if extra_data:
        snapshot.update(extra_data)

    return snapshot
