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

from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.order import Order
    from shopping.models.payment import Payment
    from shopping.models.user import User

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
    """Snapshot of entity states for forensic analysis."""

    order_status: str | None = None
    payment_status: str | None = None
    user_points: int | None = None
    product_stock: dict[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            "order_status": self.order_status,
            "payment_status": self.payment_status,
            "user_points": self.user_points,
            "product_stock": self.product_stock,
        }


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
        from shopping.services.self_healing.config import get_forensic_settings
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
        from shopping.services.self_healing.config import get_forensic_settings
        max_length = get_forensic_settings().error_message_max_length
        self.retry_history.append(
            RetryAttempt(
                attempt=attempt,
                error_code=error_code,
                error_message=error_message[:max_length],
                attempted_at=timezone.now().isoformat(),
                backoff_seconds=backoff_seconds,
            )
        )

    def capture_state_before(
        self,
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
        product_stocks: dict[int, int] | None = None,
    ) -> None:
        """
        Capture state snapshot before operation.

        Args:
            order: Order instance
            payment: Payment instance
            user: User instance
            product_stocks: Dict of product_id -> stock quantity
        """
        self.state_before = StateSnapshot(
            order_status=order.status if order else None,
            payment_status=payment.status if payment else None,
            user_points=user.points if user else None,
            product_stock=product_stocks,
        )

    def capture_state_after(
        self,
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
        product_stocks: dict[int, int] | None = None,
    ) -> None:
        """
        Capture state snapshot after operation (or failure).

        Args:
            order: Order instance
            payment: Payment instance
            user: User instance
            product_stocks: Dict of product_id -> stock quantity
        """
        self.state_after = StateSnapshot(
            order_status=order.status if order else None,
            payment_status=payment.status if payment else None,
            user_points=user.points if user else None,
            product_stock=product_stocks,
        )


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
        self._context.request_timestamp = timezone.now().isoformat()
        return self

    def end_timing(self) -> "ForensicContextBuilder":
        """End timing and calculate latency."""
        self._context.response_timestamp = timezone.now().isoformat()
        if self._start_time:
            self._context.latency_ms = int((time.time() - self._start_time) * 1000)
        return self

    def with_request(self, request: Any) -> "ForensicContextBuilder":
        """
        Add request context from Django request.

        Args:
            request: Django HttpRequest object
        """
        if hasattr(request, "META"):
            from shopping.services.self_healing.config import get_forensic_settings
            max_length = get_forensic_settings().user_agent_max_length
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
        from shopping.services.self_healing.config import get_forensic_settings
        max_length = get_forensic_settings().response_body_max_length
        self._context.external_request_id = request_id
        self._context.external_response_code = response_code
        self._context.external_response_body = response_body[:max_length]
        return self

    def with_state_before(
        self,
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
        product_stocks: dict[int, int] | None = None,
    ) -> "ForensicContextBuilder":
        """Capture state before operation."""
        self._context.capture_state_before(order, payment, user, product_stocks)
        return self

    def with_state_after(
        self,
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
        product_stocks: dict[int, int] | None = None,
    ) -> "ForensicContextBuilder":
        """Capture state after operation."""
        self._context.capture_state_after(order, payment, user, product_stocks)
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
    order: "Order | None" = None,
    payment: "Payment | None" = None,
    user: "User | None" = None,
    request: Any = None,
    task_id: str = "",
    task_name: str = "",
) -> ForensicContext:
    """
    Convenience function to capture forensic context.

    Args:
        order: Order instance
        payment: Payment instance
        user: User instance
        request: Django request object
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

    if order or payment or user:
        builder.with_state_before(order, payment, user)

    return builder.build()


def create_snapshot_data(
    order: "Order | None" = None,
    payment: "Payment | None" = None,
    user: "User | None" = None,
) -> dict[str, Any]:
    """
    Create snapshot data for DLQ storage.

    This captures the essential data needed to recover the operation
    without accessing the original records.

    Args:
        order: Order instance
        payment: Payment instance
        user: User instance

    Returns:
        Dictionary with snapshot data
    """
    snapshot: dict[str, Any] = {}

    if order:
        snapshot.update(
            {
                "order_id": order.id,
                "order_number": order.order_number,
                "order_status": order.status,
                "total_amount": str(order.total_amount),
                "used_points": order.used_points,
                "final_amount": str(order.final_amount),
                "items": [
                    {
                        "product_id": item.product_id,
                        "quantity": item.quantity,
                        "price": str(item.price),
                    }
                    for item in order.order_items.all()
                ],
            }
        )

    if payment:
        snapshot.update(
            {
                "payment_id": payment.id,
                "payment_key": payment.payment_key or "",
                "toss_order_id": payment.toss_order_id or "",
                "amount": str(payment.amount),
                "payment_status": payment.status,
                "payment_method": payment.method,
            }
        )

    if user:
        snapshot.update(
            {
                "user_id": user.id,
                "user_email": user.email,
                "user_points": user.points,
            }
        )

    return snapshot
