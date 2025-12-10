"""
Payment Provider Interface for the self-healing system.

This module defines the abstract interface for payment gateway operations,
allowing different implementations (Toss, Stripe, Iamport, KakaoPay, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from decimal import Decimal


@dataclass
class PaymentConfirmResult:
    """Result of payment confirmation attempt"""
    success: bool
    payment_key: Optional[str] = None
    transaction_id: Optional[str] = None
    approved_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class PaymentCancelResult:
    """Result of payment cancellation/refund"""
    success: bool
    cancel_key: Optional[str] = None
    refund_amount: Optional[Decimal] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class WebhookVerifyResult:
    """Result of webhook signature verification"""
    valid: bool
    event_type: Optional[str] = None
    payload: Optional[dict] = None
    error_message: Optional[str] = None


@dataclass
class PaymentStatusResult:
    """Result of payment status query"""
    success: bool
    status: Optional[str] = None
    payment_key: Optional[str] = None
    order_id: Optional[str] = None
    amount: Optional[Decimal] = None
    approved_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


class PaymentProviderInterface(ABC):
    """
    Abstract interface for payment providers.

    Implementations:
        - TossPaymentAdapter (current)
        - StripePaymentAdapter (planned)
        - IamportPaymentAdapter (planned)
        - KakaoPayAdapter (planned)
        - MockPaymentAdapter (for testing)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'toss', 'stripe')"""
        pass

    @abstractmethod
    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        """
        Confirm/capture a payment.

        Args:
            payment_key: Provider-specific payment identifier
            order_id: Internal order ID
            amount: Payment amount to confirm
            idempotency_key: Optional key for idempotent requests

        Returns:
            PaymentConfirmResult with success status and details
        """
        pass

    @abstractmethod
    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """
        Cancel or refund a payment.

        Args:
            payment_key: Provider-specific payment identifier
            cancel_reason: Human-readable cancellation reason
            cancel_amount: Partial refund amount (None = full refund)
            idempotency_key: Optional key for idempotent requests

        Returns:
            PaymentCancelResult with refund status and details
        """
        pass

    @abstractmethod
    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """
        Verify webhook signature for security.

        Args:
            payload: Raw request body bytes
            signature: Signature header value
            timestamp: Optional timestamp for replay protection

        Returns:
            WebhookVerifyResult with validation status
        """
        pass

    @abstractmethod
    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """
        Query current payment status from provider.

        Args:
            payment_key: Provider-specific payment identifier

        Returns:
            PaymentStatusResult with current status details
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if payment provider API is reachable.

        Returns:
            True if provider is healthy
        """
        pass
