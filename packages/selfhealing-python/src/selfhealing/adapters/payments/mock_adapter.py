"""
Mock Payment Provider Adapter for the self-healing system.

Implements PaymentProviderInterface for testing purposes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)

logger = logging.getLogger(__name__)


class MockPaymentAdapter(PaymentProviderInterface):
    """
    Mock implementation of PaymentProviderInterface.

    Provides configurable responses for testing scenarios.

    Usage:
        adapter = MockPaymentAdapter()

        # Configure success response
        adapter.set_confirm_response(success=True)

        # Configure failure response
        adapter.set_confirm_response(
            success=False,
            error_code="PAYMENT_FAILED",
            error_message="Insufficient funds"
        )

        # Configure to raise exception
        adapter.set_confirm_exception(ConnectionError("Network timeout"))
    """

    def __init__(self):
        """Initialize the mock payment adapter."""
        self._confirm_response: Optional[PaymentConfirmResult] = None
        self._confirm_exception: Optional[Exception] = None
        self._cancel_response: Optional[PaymentCancelResult] = None
        self._cancel_exception: Optional[Exception] = None
        self._webhook_response: Optional[WebhookVerifyResult] = None
        self._status_response: Optional[PaymentStatusResult] = None
        self._health: bool = True

        # Tracking
        self._confirm_calls: list[dict] = []
        self._cancel_calls: list[dict] = []
        self._webhook_calls: list[dict] = []
        self._status_calls: list[dict] = []

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "mock"

    # =========================================================================
    # Configuration Methods
    # =========================================================================

    def set_confirm_response(
        self,
        success: bool = True,
        payment_key: Optional[str] = None,
        transaction_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Configure the response for confirm_payment calls."""
        self._confirm_exception = None
        self._confirm_response = PaymentConfirmResult(
            success=success,
            payment_key=payment_key or f"mock_pk_{uuid.uuid4().hex[:8]}",
            transaction_id=transaction_id or f"mock_tx_{uuid.uuid4().hex[:8]}",
            approved_at=datetime.now().isoformat() if success else None,
            error_code=error_code,
            error_message=error_message,
        )

    def set_confirm_exception(self, exception: Exception) -> None:
        """Configure an exception to raise for confirm_payment calls."""
        self._confirm_response = None
        self._confirm_exception = exception

    def set_cancel_response(
        self,
        success: bool = True,
        refund_amount: Optional[Decimal] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Configure the response for cancel_payment calls."""
        self._cancel_exception = None
        self._cancel_response = PaymentCancelResult(
            success=success,
            cancel_key=f"mock_cancel_{uuid.uuid4().hex[:8]}",
            refund_amount=refund_amount,
            error_code=error_code,
            error_message=error_message,
        )

    def set_cancel_exception(self, exception: Exception) -> None:
        """Configure an exception to raise for cancel_payment calls."""
        self._cancel_response = None
        self._cancel_exception = exception

    def set_webhook_response(
        self,
        valid: bool = True,
        event_type: Optional[str] = None,
        payload: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Configure the response for verify_webhook calls."""
        self._webhook_response = WebhookVerifyResult(
            valid=valid,
            event_type=event_type or "PAYMENT_CONFIRMED",
            payload=payload,
            error_message=error_message,
        )

    def set_status_response(
        self,
        success: bool = True,
        status: str = "DONE",
        amount: Optional[Decimal] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Configure the response for get_payment_status calls."""
        self._status_response = PaymentStatusResult(
            success=success,
            status=status,
            amount=amount,
            error_code=error_code,
            error_message=error_message,
        )

    def set_health(self, healthy: bool) -> None:
        """Configure the health check response."""
        self._health = healthy

    def reset(self) -> None:
        """Reset all configurations and tracking."""
        self._confirm_response = None
        self._confirm_exception = None
        self._cancel_response = None
        self._cancel_exception = None
        self._webhook_response = None
        self._status_response = None
        self._health = True
        self._confirm_calls.clear()
        self._cancel_calls.clear()
        self._webhook_calls.clear()
        self._status_calls.clear()

    # =========================================================================
    # Tracking Methods
    # =========================================================================

    @property
    def confirm_call_count(self) -> int:
        """Get number of confirm_payment calls."""
        return len(self._confirm_calls)

    @property
    def cancel_call_count(self) -> int:
        """Get number of cancel_payment calls."""
        return len(self._cancel_calls)

    def get_confirm_calls(self) -> list[dict]:
        """Get all confirm_payment call records."""
        return self._confirm_calls.copy()

    def get_cancel_calls(self) -> list[dict]:
        """Get all cancel_payment call records."""
        return self._cancel_calls.copy()

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
        """Confirm a payment."""
        # Track the call
        self._confirm_calls.append(
            {
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": amount,
                "idempotency_key": idempotency_key,
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.debug(
            f"[MockPayment] confirm_payment called: " f"payment_key={payment_key}, order_id={order_id}, amount={amount}"
        )

        # Raise exception if configured
        if self._confirm_exception is not None:
            raise self._confirm_exception

        # Return configured response or default success
        if self._confirm_response is not None:
            return self._confirm_response

        return PaymentConfirmResult(
            success=True,
            payment_key=payment_key,
            transaction_id=f"mock_tx_{uuid.uuid4().hex[:8]}",
            approved_at=datetime.now().isoformat(),
        )

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """Cancel a payment."""
        # Track the call
        self._cancel_calls.append(
            {
                "payment_key": payment_key,
                "cancel_reason": cancel_reason,
                "cancel_amount": cancel_amount,
                "idempotency_key": idempotency_key,
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.debug(f"[MockPayment] cancel_payment called: " f"payment_key={payment_key}, reason={cancel_reason}")

        # Raise exception if configured
        if self._cancel_exception is not None:
            raise self._cancel_exception

        # Return configured response or default success
        if self._cancel_response is not None:
            return self._cancel_response

        return PaymentCancelResult(
            success=True,
            cancel_key=f"mock_cancel_{uuid.uuid4().hex[:8]}",
            refund_amount=cancel_amount,
        )

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """Verify webhook signature."""
        # Track the call
        self._webhook_calls.append(
            {
                "payload_size": len(payload),
                "signature": signature,
                "timestamp": timestamp,
                "call_timestamp": datetime.now().isoformat(),
            }
        )

        logger.debug(f"[MockPayment] verify_webhook called: " f"signature={signature[:20]}...")

        # Return configured response or default valid
        if self._webhook_response is not None:
            return self._webhook_response

        return WebhookVerifyResult(
            valid=True,
            event_type="PAYMENT_CONFIRMED",
            payload={"mock": True},
        )

    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """Get payment status."""
        # Track the call
        self._status_calls.append(
            {
                "payment_key": payment_key,
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.debug(f"[MockPayment] get_payment_status called: payment_key={payment_key}")

        # Return configured response or default
        if self._status_response is not None:
            return self._status_response

        return PaymentStatusResult(
            success=True,
            status="DONE",
            payment_key=payment_key,
            amount=Decimal("10000"),
        )

    def health_check(self) -> bool:
        """Check if payment provider is healthy."""
        return self._health
