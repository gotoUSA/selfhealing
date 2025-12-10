"""
Toss Payments Adapter for Self-Healing System

Concrete implementation of PaymentProviderInterface for Toss Payments.
Wraps the existing TossPaymentClient to conform to the pluggable interface.

Toss Payments: https://docs.tosspayments.com/reference

Related:
    - interfaces/payment_provider.py: Interface definition
    - shopping/utils/toss_payment.py: Original client
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import requests

from shopping.services.self_healing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)

logger = logging.getLogger(__name__)


class TossPaymentAdapter(PaymentProviderInterface):
    """
    Toss Payments implementation of PaymentProviderInterface.

    This adapter wraps Toss Payments API operations and provides
    a standardized interface for the self-healing system.

    Configuration:
        Requires the following Django settings:
        - TOSS_SECRET_KEY: API secret key
        - TOSS_CLIENT_KEY: Client key
        - TOSS_BASE_URL: API base URL
        - TOSS_WEBHOOK_SECRET: Webhook verification secret

    Example:
        >>> from shopping.services.self_healing.factory import ProviderRegistry
        >>> payment = ProviderRegistry.get_payment("toss")
        >>> result = payment.confirm_payment(
        ...     payment_key="pk_xxx",
        ...     order_id="order_123",
        ...     amount=Decimal("50000"),
        ... )
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        client_key: Optional[str] = None,
        base_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        debug_mode: Optional[bool] = None,
        timeout: int = 30,
    ) -> None:
        """
        Initialize Toss Payment Adapter.

        Args:
            secret_key: API secret key (defaults to settings.TOSS_SECRET_KEY)
            client_key: Client key (defaults to settings.TOSS_CLIENT_KEY)
            base_url: API base URL (defaults to settings.TOSS_BASE_URL)
            webhook_secret: Webhook secret (defaults to settings.TOSS_WEBHOOK_SECRET)
            debug_mode: Enable mock responses (defaults to settings.DEBUG)
            timeout: Request timeout in seconds
        """
        from django.conf import settings

        self.secret_key = secret_key or getattr(settings, "TOSS_SECRET_KEY", "")
        self.client_key = client_key or getattr(settings, "TOSS_CLIENT_KEY", "")
        self.base_url = base_url or getattr(
            settings, "TOSS_BASE_URL", "https://api.tosspayments.com"
        )
        self.webhook_secret = webhook_secret or getattr(
            settings, "TOSS_WEBHOOK_SECRET", ""
        )
        self.debug_mode = debug_mode if debug_mode is not None else settings.DEBUG
        self.timeout = timeout

        # Build authorization header
        credentials = f"{self.secret_key}:"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
        }

    @property
    def provider_name(self) -> str:
        """Return 'toss' as the provider identifier."""
        return "toss"

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        """
        Confirm a Toss payment.

        Calls Toss POST /v1/payments/confirm endpoint.

        Args:
            payment_key: Toss payment key from checkout
            order_id: Internal order ID
            amount: Amount to confirm
            idempotency_key: Optional idempotency key (via header)

        Returns:
            PaymentConfirmResult with confirmation status
        """
        # Debug mode returns mock response
        if self.debug_mode:
            logger.debug(f"[TossAdapter] Debug mode: mock confirm for {payment_key}")
            return PaymentConfirmResult(
                success=True,
                payment_key=payment_key,
                transaction_id=f"mock_txn_{order_id}",
                approved_at=datetime.now().isoformat(),
                raw_response={
                    "orderId": order_id,
                    "status": "DONE",
                    "approvedAt": datetime.now().isoformat(),
                    "paymentKey": payment_key,
                    "totalAmount": int(amount),
                },
            )

        url = f"{self.base_url}/v1/payments/confirm"
        data = {
            "paymentKey": payment_key,
            "orderId": order_id,
            "amount": int(amount),
        }

        headers = self.headers.copy()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = requests.post(
                url,
                json=data,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                resp_data = response.json()
                return PaymentConfirmResult(
                    success=True,
                    payment_key=resp_data.get("paymentKey"),
                    transaction_id=resp_data.get("transactionKey"),
                    approved_at=resp_data.get("approvedAt"),
                    raw_response=resp_data,
                )

            # Handle error response
            error_data = response.json()
            logger.warning(
                f"[TossAdapter] Payment confirm failed: {error_data.get('code')}"
            )
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code=error_data.get("code", "UNKNOWN"),
                error_message=error_data.get("message", "결제 승인 실패"),
                raw_response=error_data,
            )

        except requests.exceptions.Timeout:
            logger.error(f"[TossAdapter] Timeout confirming payment {payment_key}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code="TIMEOUT",
                error_message="결제 승인 요청 시간 초과",
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[TossAdapter] Connection error: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code="CONNECTION_ERROR",
                error_message=f"네트워크 연결 오류: {str(e)}",
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"[TossAdapter] Request error: {e}")
            return PaymentConfirmResult(
                success=False,
                payment_key=payment_key,
                error_code="NETWORK_ERROR",
                error_message=f"네트워크 오류: {str(e)}",
            )

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """
        Cancel/refund a Toss payment.

        Calls Toss POST /v1/payments/{paymentKey}/cancel endpoint.

        Args:
            payment_key: Toss payment key
            cancel_reason: Reason for cancellation
            cancel_amount: Partial cancel amount (None = full)
            idempotency_key: Optional idempotency key

        Returns:
            PaymentCancelResult with cancellation status
        """
        # Debug mode returns mock response
        if self.debug_mode:
            logger.debug(f"[TossAdapter] Debug mode: mock cancel for {payment_key}")
            return PaymentCancelResult(
                success=True,
                cancel_key=f"cancel_{payment_key}",
                refund_amount=cancel_amount,
                raw_response={
                    "status": "CANCELED",
                    "canceledAt": datetime.now().isoformat(),
                    "cancelReason": cancel_reason,
                },
            )

        url = f"{self.base_url}/v1/payments/{payment_key}/cancel"
        data: dict[str, Any] = {
            "cancelReason": cancel_reason,
        }

        if cancel_amount is not None:
            data["cancelAmount"] = int(cancel_amount)

        headers = self.headers.copy()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = requests.post(
                url,
                json=data,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                resp_data = response.json()
                # Get refund amount from cancels array
                refund_amt = None
                if resp_data.get("cancels"):
                    refund_amt = Decimal(str(resp_data["cancels"][-1].get("cancelAmount", 0)))
                
                return PaymentCancelResult(
                    success=True,
                    cancel_key=resp_data.get("transactionKey"),
                    refund_amount=refund_amt,
                    raw_response=resp_data,
                )

            error_data = response.json()
            logger.warning(
                f"[TossAdapter] Payment cancel failed: {error_data.get('code')}"
            )
            return PaymentCancelResult(
                success=False,
                error_code=error_data.get("code", "UNKNOWN"),
                error_message=error_data.get("message", "결제 취소 실패"),
                raw_response=error_data,
            )

        except requests.exceptions.Timeout:
            logger.error(f"[TossAdapter] Timeout canceling payment {payment_key}")
            return PaymentCancelResult(
                success=False,
                error_code="TIMEOUT",
                error_message="결제 취소 요청 시간 초과",
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"[TossAdapter] Request error: {e}")
            return PaymentCancelResult(
                success=False,
                error_code="NETWORK_ERROR",
                error_message=f"네트워크 오류: {str(e)}",
            )

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """
        Verify Toss webhook signature.

        Uses HMAC-SHA256 to verify the webhook signature.

        Args:
            payload: Raw webhook request body
            signature: Signature from webhook header
            timestamp: Not used by Toss (for interface compatibility)

        Returns:
            WebhookVerifyResult with verification status
        """
        if not self.webhook_secret:
            logger.error("[TossAdapter] Webhook secret not configured")
            return WebhookVerifyResult(
                valid=False,
                error_message="Webhook secret not configured",
            )

        try:
            # Compute expected signature
            expected_signature = hmac.new(
                self.webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()

            # Constant-time comparison
            is_valid = hmac.compare_digest(signature, expected_signature)

            if is_valid:
                payload_data = json.loads(payload.decode("utf-8"))
                return WebhookVerifyResult(
                    valid=True,
                    event_type=payload_data.get("eventType"),
                    payload=payload_data,
                )
            else:
                logger.warning("[TossAdapter] Invalid webhook signature")
                return WebhookVerifyResult(
                    valid=False,
                    error_message="Invalid signature",
                )

        except json.JSONDecodeError as e:
            logger.error(f"[TossAdapter] Failed to parse webhook payload: {e}")
            return WebhookVerifyResult(
                valid=False,
                error_message=f"Invalid JSON payload: {str(e)}",
            )
        except Exception as e:
            logger.error(f"[TossAdapter] Webhook verification error: {e}")
            return WebhookVerifyResult(
                valid=False,
                error_message=str(e),
            )

    def get_payment_status(
        self,
        payment_key: str,
    ) -> PaymentStatusResult:
        """
        Get payment status from Toss.

        Calls Toss GET /v1/payments/{paymentKey} endpoint.

        Args:
            payment_key: Toss payment key

        Returns:
            PaymentStatusResult with current status
        """
        # Debug mode returns mock response
        if self.debug_mode:
            return PaymentStatusResult(
                success=True,
                status="DONE",
                payment_key=payment_key,
                raw_response={"status": "DONE", "paymentKey": payment_key},
            )

        url = f"{self.base_url}/v1/payments/{payment_key}"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                resp_data = response.json()
                return PaymentStatusResult(
                    success=True,
                    status=resp_data.get("status"),
                    payment_key=resp_data.get("paymentKey"),
                    order_id=resp_data.get("orderId"),
                    amount=Decimal(str(resp_data.get("totalAmount", 0))),
                    approved_at=resp_data.get("approvedAt"),
                    raw_response=resp_data,
                )

            error_data = response.json()
            return PaymentStatusResult(
                success=False,
                payment_key=payment_key,
                error_code=error_data.get("code", "UNKNOWN"),
                error_message=error_data.get("message", "결제 조회 실패"),
                raw_response=error_data,
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"[TossAdapter] Failed to get payment status: {e}")
            return PaymentStatusResult(
                success=False,
                payment_key=payment_key,
                error_code="NETWORK_ERROR",
                error_message=str(e),
            )

    def health_check(self) -> bool:
        """
        Check if Toss API is reachable.

        Makes a lightweight request to verify connectivity.

        Returns:
            True if Toss API is responding
        """
        # In debug mode, always return healthy
        if self.debug_mode:
            return True

        try:
            # Use a simple endpoint to check connectivity
            # Toss doesn't have a dedicated health endpoint,
            # so we just check if the base URL is reachable
            response = requests.get(
                f"{self.base_url}/v1/payments/test",
                headers=self.headers,
                timeout=5,
            )
            # 404 is expected, we just want to know the server responds
            return response.status_code in (200, 400, 404)
        except requests.exceptions.RequestException:
            return False

    def supports_partial_refund(self) -> bool:
        """Toss supports partial refunds."""
        return True

    def supports_idempotency(self) -> bool:
        """Toss supports idempotency keys."""
        return True

    def get_retry_delay(self, attempt: int, error_code: Optional[str] = None) -> int:
        """
        Get recommended retry delay for Toss errors.

        Args:
            attempt: Current retry attempt
            error_code: Toss error code

        Returns:
            Recommended delay in seconds
        """
        # Base exponential backoff
        base_delay = min(2 ** (attempt - 1), 16)

        # Toss-specific adjustments
        if error_code == "PROVIDER_ERROR":
            # Provider errors may need longer delays
            return base_delay * 2
        elif error_code == "FAILED_INTERNAL_SYSTEM_PROCESSING":
            # Internal errors should wait longer
            return base_delay * 3
        elif error_code in ("RATE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"):
            # Rate limiting needs significant backoff
            return max(base_delay * 4, 30)

        return base_delay
