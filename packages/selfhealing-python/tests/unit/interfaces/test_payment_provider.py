"""
Unit tests for PaymentProviderInterface.

Tests the abstract interface contract and mock implementation.
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)
from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter


class TestPaymentConfirmResult:
    """Tests for PaymentConfirmResult dataclass."""

    def test_success_result(self):
        """Test creating a successful payment result."""
        result = PaymentConfirmResult(
            success=True,
            payment_key="pk_test_123",
            transaction_id="tx_456",
            approved_at="2025-12-10T10:00:00",
        )
        assert result.success is True
        assert result.payment_key == "pk_test_123"
        assert result.transaction_id == "tx_456"
        assert result.approved_at == "2025-12-10T10:00:00"
        assert result.error_code is None
        assert result.error_message is None

    def test_failure_result(self):
        """Test creating a failed payment result."""
        result = PaymentConfirmResult(
            success=False,
            error_code="INSUFFICIENT_FUNDS",
            error_message="카드 잔액이 부족합니다",
        )
        assert result.success is False
        assert result.error_code == "INSUFFICIENT_FUNDS"
        assert result.error_message == "카드 잔액이 부족합니다"
        assert result.payment_key is None

    def test_with_raw_response(self):
        """Test result with raw response data."""
        raw = {"status": "DONE", "requestedAt": "2025-12-10"}
        result = PaymentConfirmResult(success=True, raw_response=raw)
        assert result.raw_response == raw


class TestPaymentCancelResult:
    """Tests for PaymentCancelResult dataclass."""

    def test_full_refund(self):
        """Test full refund result."""
        result = PaymentCancelResult(
            success=True,
            cancel_key="cancel_123",
            refund_amount=Decimal("50000"),
        )
        assert result.success is True
        assert result.refund_amount == Decimal("50000")

    def test_partial_refund(self):
        """Test partial refund result."""
        result = PaymentCancelResult(
            success=True,
            cancel_key="cancel_456",
            refund_amount=Decimal("25000"),
        )
        assert result.refund_amount == Decimal("25000")

    def test_cancel_failure(self):
        """Test failed cancellation."""
        result = PaymentCancelResult(
            success=False,
            error_code="ALREADY_CANCELED",
            error_message="이미 취소된 결제입니다",
        )
        assert result.success is False
        assert result.error_code == "ALREADY_CANCELED"


class TestWebhookVerifyResult:
    """Tests for WebhookVerifyResult dataclass."""

    def test_valid_webhook(self):
        """Test valid webhook verification."""
        result = WebhookVerifyResult(
            valid=True,
            event_type="PAYMENT_CONFIRMED",
            payload={"paymentKey": "pk_123"},
        )
        assert result.valid is True
        assert result.event_type == "PAYMENT_CONFIRMED"
        assert result.payload == {"paymentKey": "pk_123"}

    def test_invalid_webhook(self):
        """Test invalid webhook verification."""
        result = WebhookVerifyResult(
            valid=False,
            error_message="Invalid signature",
        )
        assert result.valid is False
        assert result.error_message == "Invalid signature"


class TestPaymentStatusResult:
    """Tests for PaymentStatusResult dataclass."""

    def test_successful_status(self):
        """Test successful payment status query."""
        result = PaymentStatusResult(
            success=True,
            status="DONE",
            payment_key="pk_123",
            order_id="order_456",
            amount=Decimal("10000"),
            approved_at="2025-12-10T12:00:00",
        )
        assert result.success is True
        assert result.status == "DONE"
        assert result.amount == Decimal("10000")


class TestMockPaymentAdapter:
    """Tests for MockPaymentAdapter implementation."""

    @pytest.fixture
    def adapter(self):
        """Create a mock payment adapter."""
        return MockPaymentAdapter()

    def test_provider_name(self, adapter: MockPaymentAdapter):
        """Test provider name."""
        assert adapter.provider_name == "mock"

    def test_implements_interface(self, adapter: MockPaymentAdapter):
        """Test that adapter implements PaymentProviderInterface."""
        assert isinstance(adapter, PaymentProviderInterface)

    def test_default_confirm_payment_success(self, adapter: MockPaymentAdapter):
        """Test default confirm payment behavior."""
        adapter.set_confirm_response(success=True)
        result = adapter.confirm_payment(
            payment_key="pk_test",
            order_id="order_123",
            amount=Decimal("10000"),
        )
        assert result.success is True
        assert result.payment_key is not None

    def test_confirm_payment_failure(self, adapter: MockPaymentAdapter):
        """Test configured confirm payment failure."""
        adapter.set_confirm_response(
            success=False,
            error_code="PAYMENT_FAILED",
            error_message="결제 실패",
        )
        result = adapter.confirm_payment(
            payment_key="pk_test",
            order_id="order_123",
            amount=Decimal("10000"),
        )
        assert result.success is False
        assert result.error_code == "PAYMENT_FAILED"

    def test_confirm_payment_exception(self, adapter: MockPaymentAdapter):
        """Test confirm payment raises configured exception."""
        adapter.set_confirm_exception(ConnectionError("Network timeout"))
        with pytest.raises(ConnectionError, match="Network timeout"):
            adapter.confirm_payment(
                payment_key="pk_test",
                order_id="order_123",
                amount=Decimal("10000"),
            )

    def test_cancel_payment_success(self, adapter: MockPaymentAdapter):
        """Test cancel payment success."""
        adapter.set_cancel_response(success=True, refund_amount=Decimal("50000"))
        result = adapter.cancel_payment(
            payment_key="pk_test",
            cancel_reason="고객 요청",
        )
        assert result.success is True
        assert result.refund_amount == Decimal("50000")

    def test_cancel_payment_partial_refund(self, adapter: MockPaymentAdapter):
        """Test partial refund."""
        adapter.set_cancel_response(success=True, refund_amount=Decimal("25000"))
        result = adapter.cancel_payment(
            payment_key="pk_test",
            cancel_reason="부분 환불",
            cancel_amount=Decimal("25000"),
        )
        assert result.success is True
        assert result.refund_amount == Decimal("25000")

    def test_verify_webhook_valid(self, adapter: MockPaymentAdapter):
        """Test webhook verification success."""
        adapter.set_webhook_response(
            valid=True,
            event_type="PAYMENT_CONFIRMED",
            payload={"paymentKey": "pk_123"},
        )
        result = adapter.verify_webhook(
            payload=b'{"paymentKey": "pk_123"}',
            signature="valid_sig",
        )
        assert result.valid is True
        assert result.event_type == "PAYMENT_CONFIRMED"

    def test_verify_webhook_invalid(self, adapter: MockPaymentAdapter):
        """Test webhook verification failure."""
        adapter.set_webhook_response(
            valid=False,
            error_message="Invalid signature",
        )
        result = adapter.verify_webhook(
            payload=b'malformed',
            signature="bad_sig",
        )
        assert result.valid is False
        assert result.error_message == "Invalid signature"

    def test_get_payment_status(self, adapter: MockPaymentAdapter):
        """Test get payment status."""
        adapter.set_status_response(
            success=True,
            status="DONE",
            amount=Decimal("10000"),
        )
        result = adapter.get_payment_status(payment_key="pk_test")
        assert result.success is True
        assert result.status == "DONE"

    def test_health_check_default_healthy(self, adapter: MockPaymentAdapter):
        """Test default health check returns healthy."""
        assert adapter.health_check() is True

    def test_health_check_unhealthy(self, adapter: MockPaymentAdapter):
        """Test configurable unhealthy state."""
        adapter.set_health(False)
        assert adapter.health_check() is False

    def test_call_tracking_confirm(self, adapter: MockPaymentAdapter):
        """Test that confirm calls are tracked."""
        adapter.set_confirm_response(success=True)
        adapter.confirm_payment(
            payment_key="pk_1",
            order_id="order_1",
            amount=Decimal("1000"),
        )
        adapter.confirm_payment(
            payment_key="pk_2",
            order_id="order_2",
            amount=Decimal("2000"),
        )
        calls = adapter.get_confirm_calls()
        assert len(calls) == 2
        assert calls[0]["payment_key"] == "pk_1"
        assert calls[1]["payment_key"] == "pk_2"

    def test_call_tracking_cancel(self, adapter: MockPaymentAdapter):
        """Test that cancel calls are tracked."""
        adapter.set_cancel_response(success=True)
        adapter.cancel_payment(payment_key="pk_1", cancel_reason="테스트")
        calls = adapter.get_cancel_calls()
        assert len(calls) == 1
        assert calls[0]["payment_key"] == "pk_1"

    def test_reset_calls(self, adapter: MockPaymentAdapter):
        """Test resetting call history."""
        adapter.set_confirm_response(success=True)
        adapter.confirm_payment(
            payment_key="pk_1",
            order_id="order_1",
            amount=Decimal("1000"),
        )
        adapter.reset()
        assert len(adapter.get_confirm_calls()) == 0

    def test_idempotency_key_passed(self, adapter: MockPaymentAdapter):
        """Test idempotency key is tracked."""
        adapter.set_confirm_response(success=True)
        adapter.confirm_payment(
            payment_key="pk_1",
            order_id="order_1",
            amount=Decimal("1000"),
            idempotency_key="idem_123",
        )
        calls = adapter.get_confirm_calls()
        assert calls[0]["idempotency_key"] == "idem_123"


class TestPaymentProviderInterfaceContract:
    """Tests to verify interface contract compliance."""

    def test_abstract_methods_required(self):
        """Test that all abstract methods must be implemented."""
        with pytest.raises(TypeError):
            # Cannot instantiate abstract class
            PaymentProviderInterface()

    def test_interface_has_required_methods(self):
        """Test that interface defines all required methods."""
        required_methods = [
            "provider_name",
            "confirm_payment",
            "cancel_payment",
            "verify_webhook",
            "get_payment_status",
            "health_check",
        ]
        for method in required_methods:
            assert hasattr(PaymentProviderInterface, method)
