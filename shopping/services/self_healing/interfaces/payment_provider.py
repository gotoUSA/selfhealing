"""
Payment Provider Interface for Self-Healing System

Abstract interface for payment gateway operations.
Allows switching between different payment providers (Toss, Stripe, Iamport)
without modifying core business logic.

Design Principles:
1. Pure Python - no framework dependencies
2. Dataclasses for immutable DTOs
3. ABC for provider contracts
4. Provider-agnostic error handling

Reference: docs/PLUGGABLE_ARCHITECTURE.md Section 3.1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


# ============================================================================
# Data Transfer Objects (DTOs)
# ============================================================================


@dataclass(frozen=True)
class PaymentConfirmResult:
    """
    Result of payment confirmation attempt.

    Immutable dataclass representing the outcome of a payment
    confirmation/capture operation.

    Attributes:
        success: Whether the confirmation was successful
        payment_key: Provider-specific payment identifier
        transaction_id: Unique transaction ID from provider
        approved_at: ISO 8601 timestamp of approval
        error_code: Provider-specific error code (if failed)
        error_message: Human-readable error message (if failed)
        raw_response: Original response from provider for debugging
    """

    success: bool
    payment_key: Optional[str] = None
    transaction_id: Optional[str] = None
    approved_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None

    def is_retryable(self) -> bool:
        """
        Check if the failed payment can be retried.

        Returns:
            True if the error is transient and retry is recommended
        """
        if self.success:
            return False

        # Common retryable error codes across providers
        retryable_codes = {
            # Network/timeout errors
            "TIMEOUT",
            "CONNECTION_ERROR",
            "GATEWAY_TIMEOUT",
            # Temporary service issues
            "SERVICE_UNAVAILABLE",
            "RATE_LIMIT_EXCEEDED",
            "TEMPORARY_FAILURE",
            "TRY_AGAIN",
            # Toss-specific
            "PROVIDER_ERROR",
            "FAILED_INTERNAL_SYSTEM_PROCESSING",
        }

        return self.error_code in retryable_codes if self.error_code else False


@dataclass(frozen=True)
class PaymentCancelResult:
    """
    Result of payment cancellation/refund operation.

    Immutable dataclass representing the outcome of a payment
    cancellation or refund request.

    Attributes:
        success: Whether the cancellation was successful
        cancel_key: Provider-specific cancellation identifier
        refund_amount: Amount actually refunded
        error_code: Provider-specific error code (if failed)
        error_message: Human-readable error message (if failed)
        raw_response: Original response from provider for debugging
    """

    success: bool
    cancel_key: Optional[str] = None
    refund_amount: Optional[Decimal] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass(frozen=True)
class WebhookVerifyResult:
    """
    Result of webhook signature verification.

    Immutable dataclass representing the outcome of webhook
    signature validation for security.

    Attributes:
        valid: Whether the signature is valid
        event_type: Type of webhook event (e.g., 'payment.confirmed')
        payload: Parsed webhook payload data
        error_message: Reason for validation failure (if invalid)
    """

    valid: bool
    event_type: Optional[str] = None
    payload: Optional[dict] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class PaymentStatusResult:
    """
    Result of payment status inquiry.

    Attributes:
        success: Whether the inquiry was successful
        status: Current payment status
        payment_key: Provider-specific payment identifier
        order_id: Internal order ID
        amount: Payment amount
        approved_at: ISO 8601 timestamp of approval (if approved)
        error_code: Provider-specific error code (if failed)
        error_message: Human-readable error message (if failed)
        raw_response: Original response from provider
    """

    success: bool
    status: Optional[str] = None
    payment_key: Optional[str] = None
    order_id: Optional[str] = None
    amount: Optional[Decimal] = None
    approved_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


# ============================================================================
# Payment Provider Interface
# ============================================================================


class PaymentProviderInterface(ABC):
    """
    Abstract interface for payment providers.

    This interface defines the contract that all payment provider
    adapters must implement. It enables the self-healing system
    to work with different payment gateways interchangeably.

    Implementations:
        - TossPaymentAdapter (current)
        - StripePaymentAdapter (planned)
        - IamportPaymentAdapter (planned)
        - MockPaymentAdapter (for testing)

    Example:
        >>> payment = ProviderRegistry.get_payment()
        >>> result = payment.confirm_payment(
        ...     payment_key="pk_xxx",
        ...     order_id="order_123",
        ...     amount=Decimal("50000"),
        ... )
        >>> if result.success:
        ...     print(f"Payment confirmed: {result.transaction_id}")
        ... else:
        ...     print(f"Payment failed: {result.error_message}")
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Return the provider name.

        Returns:
            Provider identifier (e.g., 'toss', 'stripe', 'iamport')
        """
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

        This method finalizes a payment that was previously authorized
        by the customer. It should be idempotent when idempotency_key
        is provided.

        Args:
            payment_key: Provider-specific payment identifier
            order_id: Internal order ID for reference
            amount: Payment amount to confirm
            idempotency_key: Optional key for idempotent requests

        Returns:
            PaymentConfirmResult with success status and details

        Note:
            Implementations should handle network errors gracefully
            and return appropriate error codes in the result.
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

        This method cancels a confirmed payment or issues a refund.
        Partial refunds are supported when cancel_amount is less than
        the original payment amount.

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

        This method validates the authenticity of incoming webhook
        requests by verifying the cryptographic signature.

        Args:
            payload: Raw request body bytes
            signature: Signature header value from provider
            timestamp: Optional timestamp for replay protection

        Returns:
            WebhookVerifyResult with validation status

        Security:
            Always verify webhooks before processing. Invalid
            signatures may indicate tampering or replay attacks.
        """
        pass

    @abstractmethod
    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """
        Query current payment status from provider.

        This method retrieves the latest payment state directly
        from the payment provider's API.

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

        This method performs a lightweight connectivity check
        to the payment provider's API.

        Returns:
            True if provider is healthy and reachable

        Note:
            Used by circuit breakers and monitoring systems
            to detect provider outages.
        """
        pass

    def supports_partial_refund(self) -> bool:
        """
        Check if provider supports partial refunds.

        Returns:
            True if partial refunds are supported (default: True)
        """
        return True

    def supports_idempotency(self) -> bool:
        """
        Check if provider supports idempotent requests.

        Returns:
            True if idempotency keys are supported (default: True)
        """
        return True

    def get_retry_delay(self, attempt: int, error_code: Optional[str] = None) -> int:
        """
        Get recommended retry delay for a failed request.

        Args:
            attempt: Current retry attempt number (1-based)
            error_code: Provider-specific error code

        Returns:
            Recommended delay in seconds before retry
        """
        # Default exponential backoff: 1s, 2s, 4s, 8s, 16s (max)
        base_delay = min(2 ** (attempt - 1), 16)

        # Some errors may require longer delays
        if error_code == "RATE_LIMIT_EXCEEDED":
            return base_delay * 2

        return base_delay
