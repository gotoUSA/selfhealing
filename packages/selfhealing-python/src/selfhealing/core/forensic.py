"""
Forensic Context Capture

Framework-agnostic forensic context capture for debugging and recovery.
All failures in the self-healing layer should include forensic context.

Context includes:
- Timing information (request/response timestamps, latency)
- Retry history (attempts, errors, backoff times)
- State snapshots (before/after operation states)
- Request/Response data
- Task/Worker context
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Callable

logger = logging.getLogger(__name__)


def _get_current_time_iso() -> str:
    """Get current time in ISO format. Can be overridden by adapters."""
    return datetime.now(timezone.utc).isoformat()


# Allow frameworks to override the time function
_time_provider: Callable[[], str] = _get_current_time_iso


def set_time_provider(provider: Callable[[], str]) -> None:
    """Set a custom time provider (e.g., Django's timezone.now)."""
    global _time_provider
    _time_provider = provider


def get_current_time_iso() -> str:
    """Get current time in ISO format using configured provider."""
    return _time_provider()


@dataclass
class RetryAttempt:
    """Record of a single retry attempt."""

    attempt: int
    error_code: str
    error_message: str
    attempted_at: str
    backoff_seconds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            "attempt": self.attempt,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "attempted_at": self.attempted_at,
            "backoff_seconds": self.backoff_seconds,
        }


@dataclass
class StateSnapshot:
    """
    Snapshot of entity states for forensic analysis (domain-neutral).

    All state data is stored in a generic `state_data` dictionary.
    Use `set_state()` and `get_state()` methods for access, or
    access `state_data` directly.

    Example:
        snapshot = StateSnapshot()
        snapshot.set_state("entity_status", "pending")
        # or
        snapshot = StateSnapshot(state_data={"entity_status": "pending"})
    """

    state_data: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    # Alias for backwards compatibility
    @property
    def states(self) -> Dict[str, Any]:
        return self.state_data

    def set_state(self, key: str, value: Any) -> None:
        """Set a state value."""
        self.state_data[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self.state_data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        result = dict(self.state_data)
        result.update(self.extra)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateSnapshot":
        """Create from dictionary."""
        return cls(state_data=dict(data), extra={})


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
    retry_history: List[RetryAttempt] = field(default_factory=list)

    # State Snapshots
    state_before: Optional[StateSnapshot] = None
    state_after: Optional[StateSnapshot] = None

    # Request Context
    client_ip: str = ""
    user_agent: str = ""
    session_id: str = ""

    # Task Context (for async workers like Celery)
    task_name: str = ""
    task_id: str = ""
    queue_name: str = ""
    worker_id: str = ""

    # External System
    external_request_id: str = ""
    external_response_code: Optional[int] = None
    external_response_body: str = ""

    # Additional data
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_metadata(self, max_response_length: int = 5000) -> Dict[str, Any]:
        """Convert to metadata dictionary for DLQ storage."""
        return {
            # Timing
            "request_timestamp": self.request_timestamp,
            "response_timestamp": self.response_timestamp,
            "latency_ms": self.latency_ms,
            # Retry History
            "retry_history": [r.to_dict() for r in self.retry_history],
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
            "external_response_body": self.external_response_body[:max_response_length] if self.external_response_body else "",
            # Extra
            **self.extra,
        }

    def add_retry_attempt(
        self,
        attempt: int,
        error_code: str,
        error_message: str,
        backoff_seconds: int = 0,
        max_message_length: int = 500,
    ) -> None:
        """
        Add a retry attempt to the history.

        Args:
            attempt: Attempt number
            error_code: Error code from the failure
            error_message: Human-readable error message
            backoff_seconds: Seconds waited before this attempt
            max_message_length: Maximum length for error message
        """
        self.retry_history.append(
            RetryAttempt(
                attempt=attempt,
                error_code=error_code,
                error_message=error_message[:max_message_length],
                attempted_at=get_current_time_iso(),
                backoff_seconds=backoff_seconds,
            )
        )

    def capture_state_before(
        self,
        **states: Any,
    ) -> None:
        """Capture state snapshot before operation.

        Args:
            **states: Key-value pairs of state data to capture

        Example:
            context.capture_state_before(
                entity_status="pending",
                resource_count=100,
            )
        """
        self.state_before = StateSnapshot(state_data=dict(states))

    def capture_state_after(
        self,
        **states: Any,
    ) -> None:
        """Capture state snapshot after operation (or failure).

        Args:
            **states: Key-value pairs of state data to capture

        Example:
            context.capture_state_after(
                entity_status="completed",
                resource_count=99,
            )
        """
        self.state_after = StateSnapshot(state_data=dict(states))

    @classmethod
    def from_metadata(cls, metadata: Dict[str, Any]) -> "ForensicContext":
        """Recreate ForensicContext from stored metadata."""
        context = cls(
            request_timestamp=metadata.get("request_timestamp", ""),
            response_timestamp=metadata.get("response_timestamp", ""),
            latency_ms=metadata.get("latency_ms", 0),
            client_ip=metadata.get("client_ip", ""),
            user_agent=metadata.get("user_agent", ""),
            session_id=metadata.get("session_id", ""),
            task_name=metadata.get("task_name", ""),
            task_id=metadata.get("task_id", ""),
            queue_name=metadata.get("queue_name", ""),
            worker_id=metadata.get("worker_id", ""),
            external_request_id=metadata.get("external_request_id", ""),
            external_response_code=metadata.get("external_response_code"),
            external_response_body=metadata.get("external_response_body", ""),
        )

        # Parse retry history
        retry_history = metadata.get("retry_history", [])
        for r in retry_history:
            context.retry_history.append(
                RetryAttempt(
                    attempt=r.get("attempt", 0),
                    error_code=r.get("error_code", ""),
                    error_message=r.get("error_message", ""),
                    attempted_at=r.get("attempted_at", ""),
                    backoff_seconds=r.get("backoff_seconds", 0),
                )
            )

        # Parse state snapshots
        state_before = metadata.get("state_before", {})
        if state_before:
            context.state_before = StateSnapshot.from_dict(state_before)

        state_after = metadata.get("state_after", {})
        if state_after:
            context.state_after = StateSnapshot.from_dict(state_after)

        return context


class ForensicContextBuilder:
    """
    Builder for creating ForensicContext with fluent API.

    Usage:
        context = (
            ForensicContextBuilder()
            .start_timing()
            .with_task(task_id, task_name)
            .with_state_before(order_status="pending")
            .end_timing()
            .build()
        )
    """

    def __init__(self):
        self._context = ForensicContext()
        self._start_time: Optional[float] = None

    def start_timing(self) -> "ForensicContextBuilder":
        """Start timing the operation."""
        self._start_time = time.time()
        self._context.request_timestamp = get_current_time_iso()
        return self

    def end_timing(self) -> "ForensicContextBuilder":
        """End timing and calculate latency."""
        self._context.response_timestamp = get_current_time_iso()
        if self._start_time:
            self._context.latency_ms = int((time.time() - self._start_time) * 1000)
        return self

    def with_client_info(
        self,
        client_ip: str = "",
        user_agent: str = "",
        session_id: str = "",
        max_user_agent_length: int = 500,
    ) -> "ForensicContextBuilder":
        """
        Add client request context.

        Args:
            client_ip: Client IP address
            user_agent: User agent string
            session_id: Session identifier
            max_user_agent_length: Max length for user agent
        """
        self._context.client_ip = client_ip
        self._context.user_agent = user_agent[:max_user_agent_length] if user_agent else ""
        self._context.session_id = session_id
        return self

    def with_task(
        self,
        task_id: str = "",
        task_name: str = "",
        queue_name: str = "",
        worker_id: str = "",
    ) -> "ForensicContextBuilder":
        """
        Add async task context (e.g., Celery).

        Args:
            task_id: Task ID
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
        response_code: Optional[int] = None,
        response_body: str = "",
        max_body_length: int = 5000,
    ) -> "ForensicContextBuilder":
        """
        Add external system response data.

        Args:
            request_id: External system's request/transaction ID
            response_code: HTTP status code or error code
            response_body: Response body (will be truncated)
            max_body_length: Maximum body length to store
        """
        self._context.external_request_id = request_id
        self._context.external_response_code = response_code
        self._context.external_response_body = response_body[:max_body_length] if response_body else ""
        return self

    def with_state_before(
        self,
        **states: Any,
    ) -> "ForensicContextBuilder":
        """Capture state before operation.

        Args:
            **states: Key-value pairs of state data
        """
        self._context.capture_state_before(**states)
        return self

    def with_state_after(
        self,
        **states: Any,
    ) -> "ForensicContextBuilder":
        """Capture state after operation.

        Args:
            **states: Key-value pairs of state data
        """
        self._context.capture_state_after(**states)
        return self

    def with_extra(self, **kwargs: Any) -> "ForensicContextBuilder":
        """Add extra context data."""
        self._context.extra.update(kwargs)
        return self

    def add_retry_attempt(
        self,
        attempt: int,
        error_code: str,
        error_message: str,
        backoff_seconds: int = 0,
    ) -> "ForensicContextBuilder":
        """Add a retry attempt record."""
        self._context.add_retry_attempt(
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
            backoff_seconds=backoff_seconds,
        )
        return self

    def build(self) -> ForensicContext:
        """Build the ForensicContext."""
        return self._context


def capture_forensic_context(
    client_ip: str = "",
    user_agent: str = "",
    session_id: str = "",
    task_id: str = "",
    task_name: str = "",
    state_before: Optional[Dict[str, Any]] = None,
    state_after: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> ForensicContext:
    """
    Convenience function to capture forensic context (domain-neutral).

    Args:
        client_ip: Client IP address
        user_agent: User agent string
        session_id: Session identifier
        task_id: Async task ID
        task_name: Async task name
        state_before: State snapshot before operation (dict of key-value pairs)
        state_after: State snapshot after operation (dict of key-value pairs)
        **extra: Additional state data (domain-neutral key-value pairs)

    Returns:
        ForensicContext with captured data

    Example:
        context = capture_forensic_context(
            client_ip="192.168.1.1",
            state_before={"entity_status": "pending"},
            state_after={"entity_status": "completed"},
        )
    """
    builder = ForensicContextBuilder()
    builder.start_timing()

    if client_ip or user_agent or session_id:
        builder.with_client_info(
            client_ip=client_ip,
            user_agent=user_agent,
            session_id=session_id,
        )

    if task_id or task_name:
        builder.with_task(task_id=task_id, task_name=task_name)

    # Handle state_before - either explicit dict or extra kwargs
    if state_before:
        builder.with_state_before(**state_before)
    elif extra:
        builder.with_state_before(**extra)

    # Handle state_after if provided
    if state_after:
        builder.with_state_after(**state_after)

    return builder.build()


def create_snapshot_data(**data: Any) -> Dict[str, Any]:
    """
    Create snapshot data for DLQ storage (domain-neutral).

    This captures the essential data needed to recover the operation
    without accessing the original records.

    Args:
        **data: Any key-value pairs to include in the snapshot

    Returns:
        Dictionary with snapshot data (only non-None values included)

    Example:
        snapshot = create_snapshot_data(
            entity_id=123,
            entity_type="order",
            status="pending",
            amount="10000",
        )
    """
    return {k: v for k, v in data.items() if v is not None}
