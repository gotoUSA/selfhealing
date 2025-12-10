"""
Mock Payment Adapter for Self-Healing System

Mock implementation of PaymentProviderInterface for testing.
Allows configurable responses for testing various scenarios.

Usage:
    >>> adapter = MockPaymentAdapter()
    >>> adapter.set_confirm_result(success=True)
    >>> result = adapter.confirm_payment("pk_123", "order_1", Decimal("1000"))
    >>> assert result.success == True
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from shopping.services.self_healing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)

logger = logging.getLogger(__name__)


@dataclass
class MockPaymentState:
    """Internal state for mock payment tracking."""

    payment_key: str
    order_id: str
    amount: Decimal
    status: str = "READY"
    confirmed_at: Optional[str] = None
    canceled_at: Optional[str] = None


class MockPaymentAdapter(PaymentProviderInterface):
    """
    Mock implementation of PaymentProviderInterface for testing.

    This adapter provides configurable responses for testing
    payment flows without making real API calls.

    Features:
        - Configurable success/failure responses
        - Payment state tracking
        - Call history recording
        - Failure injection for testing error handling

    Example:
        >>> adapter = MockPaymentAdapter()
        >>>
        >>> # Configure for success
        >>> adapter.set_confirm_result(success=True)
        >>> result = adapter.confirm_payment("pk_123", "order_1", Decimal("1000"))
        >>> assert result.success
        >>>
        >>> # Configure for failure
        >>> adapter.set_confirm_result(
        ...     success=False,
        ...     error_code="INSUFFICIENT_FUNDS",
        ...     error_message="Insufficient funds",
        ... )
        >>> result = adapter.confirm_payment("pk_456", "order_2", Decimal("2000"))
        >>> assert not result.success
        >>> assert result.error_code == "INSUFFICIENT_FUNDS"
    """

    def __init__(self) -> None:
        """Initialize mock adapter with default successful responses."""
        self._payments: dict[str, MockPaymentState] = {}
        self._call_history: list[dict[str, Any]] = []

        # Configurable response settings
        self._confirm_result: Optional[PaymentConfirmResult] = None
        self._cancel_result: Optional[PaymentCancelResult] = None
        self._webhook_result: Optional[WebhookVerifyResult] = None
        self._status_result: Optional[PaymentStatusResult] = None
        self._health_status: bool = True

        # Failure injection
        self._confirm_should_fail: bool = False
        self._confirm_fail_times: int = 0
        self._confirm_fail_count: int = 0
        self._confirm_error_code: str = "MOCK_ERROR"
        self._confirm_error_message: str = "Mock failure"

        # Callbacks for custom behavior
        self._on_confirm: Optional[Callable[[str, str, Decimal], PaymentConfirmResult]] = None
        self._on_cancel: Optional[Callable[[str, str], PaymentCancelResult]] = None

    @property
    def provider_name(self) -> str:
        """Return 'mock' as the provider identifier."""
        return "mock"

    # =========================================================================
    # Configuration Methods
    # =========================================================================

    def set_confirm_result(
        self,
        success: bool = True,
        payment_key: Optional[str] = None,
        transaction_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> "MockPaymentAdapter":
        """
        Configure the result for confirm_payment calls.

        Args:
            success: Whether confirmation should succeed
            payment_key: Payment key in response
            transaction_id: Transaction ID in response
            error_code: Error code if failing
            error_message: Error message if failing

        Returns:
            self for method chaining
        """
        self._confirm_result = PaymentConfirmResult(
            success=success,
            payment_key=payment_key,
            transaction_id=transaction_id or f"mock_txn_{datetime.now().timestamp()}",
            approved_at=datetime.now().isoformat() if success else None,
            error_code=error_code,
            error_message=error_message,
        )
        return self

    def set_cancel_result(
        self,
        success: bool = True,
        refund_amount: Optional[Decimal] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> "MockPaymentAdapter":
        """Configure the result for cancel_payment calls."""
        self._cancel_result = PaymentCancelResult(
            success=success,
            cancel_key=f"mock_cancel_{datetime.now().timestamp()}" if success else None,
            refund_amount=refund_amount,
            error_code=error_code,
            error_message=error_message,
        )
        return self

    def set_webhook_result(
        self,
        valid: bool = True,
        event_type: Optional[str] = None,
        payload: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> "MockPaymentAdapter":
        """Configure the result for verify_webhook calls."""
        self._webhook_result = WebhookVerifyResult(
            valid=valid,
            event_type=event_type,
            payload=payload,
            error_message=error_message,
        )
        return self

    def set_health_status(self, healthy: bool) -> "MockPaymentAdapter":
        """Configure the result for health_check calls."""
        self._health_status = healthy
        return self

    def configure_intermittent_failure(
        self,
        fail_times: int,
        error_code: str = "TEMPORARY_FAILURE",
        error_message: str = "Temporary failure",
    ) -> "MockPaymentAdapter":
        """
        Configure intermittent failures for testing retries.

        Args:
            fail_times: Number of times to fail before succeeding
            error_code: Error code to return during failures
            error_message: Error message during failures

        Returns:
            self for method chaining
        """
        self._confirm_should_fail = True
        self._confirm_fail_times = fail_times
        self._confirm_fail_count = 0
        self._confirm_error_code = error_code
        self._confirm_error_message = error_message
        return self

    def set_on_confirm(
        self, callback: Callable[[str, str, Decimal], PaymentConfirmResult]
    ) -> "MockPaymentAdapter":
        """Set a custom callback for confirm_payment."""
        self._on_confirm = callback
        return self

    def reset(self) -> "MockPaymentAdapter":
        """Reset all state and configuration."""
        self._payments.clear()
        self._call_history.clear()
        self._confirm_result = None
        self._cancel_result = None
        self._webhook_result = None
        self._status_result = None
        self._health_status = True
        self._confirm_should_fail = False
        self._confirm_fail_times = 0
        self._confirm_fail_count = 0
        self._on_confirm = None
        self._on_cancel = None
        return self

    # =========================================================================
    # Inspection Methods
    # =========================================================================

    @property
    def call_history(self) -> list[dict[str, Any]]:
        """Get the history of all method calls."""
        return self._call_history.copy()

    @property
    def payments(self) -> dict[str, MockPaymentState]:
        """Get all tracked payments."""
        return self._payments.copy()

    def get_confirm_call_count(self) -> int:
        """Get the number of confirm_payment calls."""
        return sum(1 for call in self._call_history if call["method"] == "confirm_payment")

    def get_cancel_call_count(self) -> int:
        """Get the number of cancel_payment calls."""
        return sum(1 for call in self._call_history if call["method"] == "cancel_payment")

    def was_called_with(self, method: str, **kwargs) -> bool:
        """Check if a method was called with specific arguments."""
        for call in self._call_history:
            if call["method"] != method:
                continue
            if all(call.get(k) == v for k, v in kwargs.items()):
                return True
        return False

    # =========================================================================
    # Interface Implementation
    # =========================================================================

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        """
        Mock payment confirmation.

        Returns configured result or generates default success response.
        """
        # Record call
        self._call_history.append({
            "method": "confirm_payment",
            "payment_key": payment_key,
            "order_id": order_id,
            "amount": amount,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug(f"[MockPayment] confirm_payment called: {payment_key}")

        # Custom callback
        if self._on_confirm:
            return self._on_confirm(payment_key, order_id, amount)

        # Intermittent failure simulation
        if self._confirm_should_fail and self._confirm_fail_count < self._confirm_fail_times:
            self._confirm_fail_count += 1
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code=self._confirm_error_code,
                error_message=self._confirm_error_message,
            )

        # Configured result
        if self._confirm_result is not None:
            return PaymentConfirmResult(
                success=self._confirm_result.success,
                payment_key=payment_key,
                transaction_id=self._confirm_result.transaction_id,
                approved_at=self._confirm_result.approved_at,
                error_code=self._confirm_result.error_code,
                error_message=self._confirm_result.error_message,
            )

        # Default success
        now = datetime.now()
        self._payments[payment_key] = MockPaymentState(
            payment_key=payment_key,
            order_id=order_id,
            amount=amount,
            status="DONE",
            confirmed_at=now.isoformat(),
        )

        return PaymentConfirmResult(
            success=True,
            payment_key=payment_key,
            transaction_id=f"mock_txn_{now.timestamp()}",
            approved_at=now.isoformat(),
        )

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """
        Mock payment cancellation.

        Returns configured result or generates default success response.
        """
        # Record call
        self._call_history.append({
            "method": "cancel_payment",
            "payment_key": payment_key,
            "cancel_reason": cancel_reason,
            "cancel_amount": cancel_amount,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug(f"[MockPayment] cancel_payment called: {payment_key}")

        # Custom callback
        if self._on_cancel:
            return self._on_cancel(payment_key, cancel_reason)

        # Configured result
        if self._cancel_result is not None:
            return self._cancel_result

        # Default success
        now = datetime.now()
        if payment_key in self._payments:
            self._payments[payment_key].status = "CANCELED"
            self._payments[payment_key].canceled_at = now.isoformat()
            refund = cancel_amount or self._payments[payment_key].amount
        else:
            refund = cancel_amount or Decimal("0")

        return PaymentCancelResult(
            success=True,
            cancel_key=f"mock_cancel_{now.timestamp()}",
            refund_amount=refund,
        )

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """
        Mock webhook verification.

        Returns configured result or default valid response.
        """
        self._call_history.append({
            "method": "verify_webhook",
            "payload_size": len(payload),
            "signature": signature,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug("[MockPayment] verify_webhook called")

        if self._webhook_result is not None:
            return self._webhook_result

        # Default: parse payload and return as valid
        try:
            import json
            payload_data = json.loads(payload.decode("utf-8"))
            return WebhookVerifyResult(
                valid=True,
                event_type=payload_data.get("eventType", "payment.confirmed"),
                payload=payload_data,
            )
        except Exception:
            return WebhookVerifyResult(
                valid=True,
                event_type="payment.confirmed",
                payload={},
            )

    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """
        Mock payment status inquiry.

        Returns tracked payment state or configured result.
        """
        self._call_history.append({
            "method": "get_payment_status",
            "payment_key": payment_key,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug(f"[MockPayment] get_payment_status called: {payment_key}")

        if self._status_result is not None:
            return self._status_result

        # Return tracked payment state
        if payment_key in self._payments:
            state = self._payments[payment_key]
            return PaymentStatusResult(
                success=True,
                status=state.status,
                payment_key=state.payment_key,
                order_id=state.order_id,
                amount=state.amount,
                approved_at=state.confirmed_at,
            )

        # Not found
        return PaymentStatusResult(
            success=False,
            payment_key=payment_key,
            error_code="NOT_FOUND",
            error_message="Payment not found",
        )

    def health_check(self) -> bool:
        """
        Mock health check.

        Returns configured health status.
        """
        self._call_history.append({
            "method": "health_check",
            "timestamp": datetime.now().isoformat(),
        })

        return self._health_status
