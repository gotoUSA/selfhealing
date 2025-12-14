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
from datetime import datetime
from typing import Any, Optional, Dict, List, Callable

logger = logging.getLogger(__name__)


def _get_current_time_iso() -> str:
    """Get current time in ISO format. Can be overridden by adapters."""
    return datetime.utcnow().isoformat()


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
    """Snapshot of entity states for forensic analysis (domain-neutral).
    
    All state data is stored in a generic `states` dictionary.
    Use `set_state()` and `get_state()` methods for access.
    
    Example:
        snapshot = StateSnapshot()
        snapshot.set_state("order_status", "pending")
        snapshot.set_state("payment_status", "completed")
    """

    states: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def set_state(self, key: str, value: Any) -> None:
        """Set a state value."""
        self.states[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self.states.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        result = dict(self.states)
        result.update(self.extra)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateSnapshot":
        """Create from dictionary."""
        return cls(states=dict(data), extra={})

    # Legacy property accessors for backward compatibility
    @property
    def order_status(self) -> Optional[str]:
        """@deprecated: use get_state('order_status')"""
        return self.states.get("order_status")

    @order_status.setter
    def order_status(self, value: Optional[str]) -> None:
        if value is not None:
            self.states["order_status"] = value

    @property
    def payment_status(self) -> Optional[str]:
        """@deprecated: use get_state('payment_status')"""
        return self.states.get("payment_status")

    @payment_status.setter
    def payment_status(self, value: Optional[str]) -> None:
        if value is not None:
            self.states["payment_status"] = value

    @property
    def user_points(self) -> Optional[int]:
        """@deprecated: use get_state('user_points')"""
        return self.states.get("user_points")

    @user_points.setter
    def user_points(self, value: Optional[int]) -> None:
        if value is not None:
            self.states["user_points"] = value

    @property
    def product_stock(self) -> Optional[Dict[int, int]]:
        """@deprecated: use get_state('product_stock')"""
        return self.states.get("product_stock")

    @product_stock.setter
    def product_stock(self, value: Optional[Dict[int, int]]) -> None:
        if value is not None:
            self.states["product_stock"] = value


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
        order_status: Optional[str] = None,
        payment_status: Optional[str] = None,
        user_points: Optional[int] = None,
        product_stocks: Optional[Dict[int, int]] = None,
        **extra: Any,
    ) -> None:
        """Capture state snapshot before operation.
        
        Note: Named parameters are for backward compatibility.
        Use **extra for domain-neutral state capture.
        """
        states: Dict[str, Any] = {}
        if order_status is not None:
            states["order_status"] = order_status
        if payment_status is not None:
            states["payment_status"] = payment_status
        if user_points is not None:
            states["user_points"] = user_points
        if product_stocks is not None:
            states["product_stock"] = product_stocks
        states.update(extra)
        self.state_before = StateSnapshot(states=states)

    def capture_state_after(
        self,
        order_status: Optional[str] = None,
        payment_status: Optional[str] = None,
        user_points: Optional[int] = None,
        product_stocks: Optional[Dict[int, int]] = None,
        **extra: Any,
    ) -> None:
        """Capture state snapshot after operation (or failure).
        
        Note: Named parameters are for backward compatibility.
        Use **extra for domain-neutral state capture.
        """
        states: Dict[str, Any] = {}
        if order_status is not None:
            states["order_status"] = order_status
        if payment_status is not None:
            states["payment_status"] = payment_status
        if user_points is not None:
            states["user_points"] = user_points
        if product_stocks is not None:
            states["product_stock"] = product_stocks
        states.update(extra)
        self.state_after = StateSnapshot(states=states)

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
        order_status: Optional[str] = None,
        payment_status: Optional[str] = None,
        user_points: Optional[int] = None,
        product_stocks: Optional[Dict[int, int]] = None,
        **extra: Any,
    ) -> "ForensicContextBuilder":
        """Capture state before operation."""
        self._context.capture_state_before(
            order_status=order_status,
            payment_status=payment_status,
            user_points=user_points,
            product_stocks=product_stocks,
            **extra,
        )
        return self

    def with_state_after(
        self,
        order_status: Optional[str] = None,
        payment_status: Optional[str] = None,
        user_points: Optional[int] = None,
        product_stocks: Optional[Dict[int, int]] = None,
        **extra: Any,
    ) -> "ForensicContextBuilder":
        """Capture state after operation."""
        self._context.capture_state_after(
            order_status=order_status,
            payment_status=payment_status,
            user_points=user_points,
            product_stocks=product_stocks,
            **extra,
        )
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
    order_status: Optional[str] = None,
    payment_status: Optional[str] = None,
    user_points: Optional[int] = None,
    **extra: Any,
) -> ForensicContext:
    """
    Convenience function to capture forensic context.

    Args:
        client_ip: Client IP address
        user_agent: User agent string
        session_id: Session identifier
        task_id: Celery task ID
        task_name: Celery task name
        order_status: Order status for state snapshot
        payment_status: Payment status for state snapshot
        user_points: User points for state snapshot
        **extra: Additional context data

    Returns:
        ForensicContext with captured data
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

    if order_status or payment_status or user_points is not None:
        builder.with_state_before(
            order_status=order_status,
            payment_status=payment_status,
            user_points=user_points,
        )

    if extra:
        builder.with_extra(**extra)

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


# Legacy function for backward compatibility
def create_shopping_snapshot_data(
    order_id: Optional[int] = None,
    order_number: Optional[str] = None,
    order_status: Optional[str] = None,
    total_amount: Optional[str] = None,
    used_points: Optional[int] = None,
    final_amount: Optional[str] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    payment_id: Optional[int] = None,
    payment_key: Optional[str] = None,
    toss_order_id: Optional[str] = None,
    payment_amount: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    user_points: Optional[int] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """
    @deprecated: Use create_snapshot_data(**data) instead.
    
    Create snapshot data for DLQ storage (shopping domain specific).
    Kept for backward compatibility.
    """
    snapshot: Dict[str, Any] = {}

    # Order data
    if order_id is not None:
        snapshot["order_id"] = order_id
    if order_number:
        snapshot["order_number"] = order_number
    if order_status:
        snapshot["order_status"] = order_status
    if total_amount:
        snapshot["total_amount"] = total_amount
    if used_points is not None:
        snapshot["used_points"] = used_points
    if final_amount:
        snapshot["final_amount"] = final_amount
    if items:
        snapshot["items"] = items

    # Payment data
    if payment_id is not None:
        snapshot["payment_id"] = payment_id
    if payment_key:
        snapshot["payment_key"] = payment_key
    if toss_order_id:
        snapshot["toss_order_id"] = toss_order_id
    if payment_amount:
        snapshot["amount"] = payment_amount
    if payment_status:
        snapshot["payment_status"] = payment_status
    if payment_method:
        snapshot["payment_method"] = payment_method

    # User data
    if user_id is not None:
        snapshot["user_id"] = user_id
    if user_email:
        snapshot["user_email"] = user_email
    if user_points is not None:
        snapshot["user_points"] = user_points

    # Extra data
    snapshot.update(extra)

    return snapshot
