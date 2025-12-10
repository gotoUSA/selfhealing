"""
Stripe Payment Provider Adapter for the self-healing system.

Implements PaymentProviderInterface using Stripe as the payment gateway.
Supports international payments with full feature set.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
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


class StripePaymentAdapter(PaymentProviderInterface):
    """
    Stripe implementation of PaymentProviderInterface.

    Uses the Stripe Python SDK for all payment operations.
    Supports PaymentIntents, Refunds, and Webhook verification.

    Configuration:
        - STRIPE_SECRET_KEY: API secret key
        - STRIPE_WEBHOOK_SECRET: Webhook endpoint signing secret
        - STRIPE_API_VERSION: Optional API version override

    Usage:
        adapter = StripePaymentAdapter(
            secret_key="sk_test_...",
            webhook_secret="whsec_..."
        )
        result = adapter.confirm_payment(
            payment_key="pi_xxx",
            order_id="order_123",
            amount=Decimal("50.00")
        )
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        """
        Initialize the Stripe payment adapter.

        Args:
            secret_key: Stripe secret API key. If None, reads from settings.
            webhook_secret: Webhook signing secret. If None, reads from settings.
            api_version: Optional API version override.
        """
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._api_version = api_version
        self._stripe = None

    @property
    def stripe(self):
        """Get Stripe module, configured with credentials."""
        if self._stripe is None:
            try:
                import stripe
            except ImportError:
                raise ImportError(
                    "stripe is required for StripePaymentAdapter. "
                    "Install it with: pip install stripe"
                )

            # Get credentials from settings if not provided
            if self._secret_key is None:
                try:
                    from django.conf import settings
                    self._secret_key = getattr(settings, "STRIPE_SECRET_KEY", None)
                    self._webhook_secret = getattr(
                        settings, "STRIPE_WEBHOOK_SECRET", None
                    )
                except ImportError:
                    import os
                    self._secret_key = os.environ.get("STRIPE_SECRET_KEY")
                    self._webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

            if not self._secret_key:
                raise ValueError("Stripe secret key is required")

            stripe.api_key = self._secret_key
            if self._api_version:
                stripe.api_version = self._api_version

            self._stripe = stripe

        return self._stripe

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "stripe"

    # =========================================================================
    # Payment Operations
    # =========================================================================

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        """
        Confirm/capture a Stripe PaymentIntent.

        For Stripe, payment_key is the PaymentIntent ID (pi_xxx).
        This confirms the payment intent and captures funds.

        Args:
            payment_key: Stripe PaymentIntent ID (pi_xxx)
            order_id: Internal order ID (stored in metadata)
            amount: Expected amount (verified against intent)
            idempotency_key: Optional idempotency key

        Returns:
            PaymentConfirmResult with confirmation details
        """
        try:
            logger.info(
                f"[StripePayment] Confirming payment: {payment_key}, "
                f"order={order_id}, amount={amount}"
            )

            # Build request options
            request_options = {}
            if idempotency_key:
                request_options["idempotency_key"] = idempotency_key

            # Retrieve the PaymentIntent to verify amount
            intent = self.stripe.PaymentIntent.retrieve(payment_key)

            # Convert amount to cents for comparison (Stripe uses smallest unit)
            expected_amount_cents = int(amount * 100)
            if intent.amount != expected_amount_cents:
                logger.warning(
                    f"[StripePayment] Amount mismatch: expected {expected_amount_cents}, "
                    f"got {intent.amount}"
                )
                return PaymentConfirmResult(
                    success=False,
                    payment_key=payment_key,
                    error_code="AMOUNT_MISMATCH",
                    error_message=f"Amount mismatch: expected {amount}, got {intent.amount / 100}",
                    raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
                )

            # Check current status
            if intent.status == "succeeded":
                # Already confirmed
                logger.info(f"[StripePayment] Payment already succeeded: {payment_key}")
                return PaymentConfirmResult(
                    success=True,
                    payment_key=payment_key,
                    transaction_id=intent.latest_charge,
                    approved_at=self._format_timestamp(intent.created),
                    raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
                )

            if intent.status == "requires_capture":
                # Capture the payment
                intent = self.stripe.PaymentIntent.capture(
                    payment_key,
                    **request_options,
                )
            elif intent.status == "requires_confirmation":
                # Confirm the payment
                intent = self.stripe.PaymentIntent.confirm(
                    payment_key,
                    **request_options,
                )
            elif intent.status in ("canceled", "requires_payment_method"):
                return PaymentConfirmResult(
                    success=False,
                    payment_key=payment_key,
                    error_code="INVALID_STATUS",
                    error_message=f"Cannot confirm payment in status: {intent.status}",
                    raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
                )

            # Update metadata with order_id
            self.stripe.PaymentIntent.modify(
                payment_key,
                metadata={"order_id": order_id},
            )

            logger.info(
                f"[StripePayment] Payment confirmed: {payment_key}, "
                f"status={intent.status}"
            )

            return PaymentConfirmResult(
                success=intent.status == "succeeded",
                payment_key=payment_key,
                transaction_id=intent.latest_charge,
                approved_at=self._format_timestamp(intent.created),
                raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
            )

        except self.stripe.error.CardError as e:
            logger.warning(f"[StripePayment] Card error: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code=e.code,
                error_message=str(e.user_message),
            )
        except self.stripe.error.InvalidRequestError as e:
            logger.error(f"[StripePayment] Invalid request: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code="INVALID_REQUEST",
                error_message=str(e),
            )
        except self.stripe.error.StripeError as e:
            logger.error(f"[StripePayment] Stripe error: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code=getattr(e, "code", "STRIPE_ERROR"),
                error_message=str(e),
            )
        except Exception as e:
            logger.exception(f"[StripePayment] Unexpected error: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code="INTERNAL_ERROR",
                error_message=str(e),
            )

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """
        Cancel or refund a Stripe payment.

        For pending payments, cancels the PaymentIntent.
        For completed payments, creates a Refund.

        Args:
            payment_key: Stripe PaymentIntent ID
            cancel_reason: Reason for cancellation/refund
            cancel_amount: Partial refund amount (None = full refund)
            idempotency_key: Optional idempotency key

        Returns:
            PaymentCancelResult with refund details
        """
        try:
            logger.info(
                f"[StripePayment] Canceling payment: {payment_key}, "
                f"reason={cancel_reason}, amount={cancel_amount}"
            )

            # Build request options
            request_options = {}
            if idempotency_key:
                request_options["idempotency_key"] = idempotency_key

            # Retrieve PaymentIntent to check status
            intent = self.stripe.PaymentIntent.retrieve(payment_key)

            if intent.status in ("requires_payment_method", "requires_confirmation",
                                 "requires_action", "processing"):
                # Cancel the intent (not yet captured)
                intent = self.stripe.PaymentIntent.cancel(
                    payment_key,
                    cancellation_reason="requested_by_customer",
                    **request_options,
                )
                logger.info(f"[StripePayment] PaymentIntent canceled: {payment_key}")
                return PaymentCancelResult(
                    success=True,
                    cancel_key=payment_key,
                    refund_amount=Decimal(str(intent.amount / 100)),
                    raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
                )

            if intent.status == "succeeded":
                # Create a refund
                refund_params = {
                    "payment_intent": payment_key,
                    "reason": "requested_by_customer",
                    "metadata": {"cancel_reason": cancel_reason},
                }

                if cancel_amount is not None:
                    refund_params["amount"] = int(cancel_amount * 100)

                refund = self.stripe.Refund.create(
                    **refund_params,
                    **request_options,
                )

                logger.info(
                    f"[StripePayment] Refund created: {refund.id}, "
                    f"amount={refund.amount}"
                )

                return PaymentCancelResult(
                    success=refund.status == "succeeded",
                    cancel_key=refund.id,
                    refund_amount=Decimal(str(refund.amount / 100)),
                    raw_response=refund.to_dict() if hasattr(refund, 'to_dict') else None,
                )

            if intent.status == "canceled":
                return PaymentCancelResult(
                    success=True,
                    cancel_key=payment_key,
                    error_message="Payment already canceled",
                )

            return PaymentCancelResult(
                success=False,
                error_code="INVALID_STATUS",
                error_message=f"Cannot cancel payment in status: {intent.status}",
            )

        except self.stripe.error.InvalidRequestError as e:
            logger.error(f"[StripePayment] Invalid refund request: {e}")
            return PaymentCancelResult(
                success=False,
                error_code="INVALID_REQUEST",
                error_message=str(e),
            )
        except self.stripe.error.StripeError as e:
            logger.error(f"[StripePayment] Stripe error during cancel: {e}")
            return PaymentCancelResult(
                success=False,
                error_code=getattr(e, "code", "STRIPE_ERROR"),
                error_message=str(e),
            )
        except Exception as e:
            logger.exception(f"[StripePayment] Unexpected error during cancel: {e}")
            return PaymentCancelResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=str(e),
            )

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """
        Verify Stripe webhook signature.

        Uses Stripe's signature verification with webhook secret.

        Args:
            payload: Raw request body bytes
            signature: Stripe-Signature header value
            timestamp: Not used (extracted from signature)

        Returns:
            WebhookVerifyResult with event details
        """
        try:
            if not self._webhook_secret:
                logger.error("[StripePayment] Webhook secret not configured")
                return WebhookVerifyResult(
                    valid=False,
                    error_message="Webhook secret not configured",
                )

            # Construct and verify event
            event = self.stripe.Webhook.construct_event(
                payload=payload,
                sig_header=signature,
                secret=self._webhook_secret,
            )

            logger.info(f"[StripePayment] Webhook verified: {event.type}")

            return WebhookVerifyResult(
                valid=True,
                event_type=event.type,
                payload=event.data.object.to_dict() if hasattr(event.data.object, 'to_dict') else event.data.object,
            )

        except self.stripe.error.SignatureVerificationError as e:
            logger.warning(f"[StripePayment] Webhook signature invalid: {e}")
            return WebhookVerifyResult(
                valid=False,
                error_message="Invalid signature",
            )
        except Exception as e:
            logger.exception(f"[StripePayment] Webhook verification error: {e}")
            return WebhookVerifyResult(
                valid=False,
                error_message=str(e),
            )

    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """
        Query current payment status from Stripe.

        Args:
            payment_key: Stripe PaymentIntent ID

        Returns:
            PaymentStatusResult with current status
        """
        try:
            intent = self.stripe.PaymentIntent.retrieve(payment_key)

            # Map Stripe status to standard status
            status_map = {
                "requires_payment_method": "PENDING",
                "requires_confirmation": "PENDING",
                "requires_action": "PENDING",
                "processing": "PROCESSING",
                "requires_capture": "AUTHORIZED",
                "succeeded": "DONE",
                "canceled": "CANCELED",
            }

            return PaymentStatusResult(
                success=True,
                status=status_map.get(intent.status, intent.status.upper()),
                payment_key=payment_key,
                order_id=intent.metadata.get("order_id"),
                amount=Decimal(str(intent.amount / 100)),
                approved_at=self._format_timestamp(intent.created) if intent.status == "succeeded" else None,
                raw_response=intent.to_dict() if hasattr(intent, 'to_dict') else None,
            )

        except self.stripe.error.InvalidRequestError as e:
            logger.error(f"[StripePayment] Payment not found: {payment_key}")
            return PaymentStatusResult(
                success=False,
                payment_key=payment_key,
                error_code="NOT_FOUND",
                error_message=str(e),
            )
        except self.stripe.error.StripeError as e:
            logger.error(f"[StripePayment] Status query error: {e}")
            return PaymentStatusResult(
                success=False,
                payment_key=payment_key,
                error_code=getattr(e, "code", "STRIPE_ERROR"),
                error_message=str(e),
            )
        except Exception as e:
            logger.exception(f"[StripePayment] Unexpected error: {e}")
            return PaymentStatusResult(
                success=False,
                payment_key=payment_key,
                error_code="INTERNAL_ERROR",
                error_message=str(e),
            )

    def health_check(self) -> bool:
        """
        Check if Stripe API is reachable.

        Returns:
            True if healthy
        """
        try:
            # Simple API call to verify connectivity
            self.stripe.Balance.retrieve()
            return True
        except Exception as e:
            logger.error(f"[StripePayment] Health check failed: {e}")
            return False

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _format_timestamp(self, timestamp: int) -> str:
        """Format Unix timestamp to ISO format."""
        from datetime import datetime, timezone
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
