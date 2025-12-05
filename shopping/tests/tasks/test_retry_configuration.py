"""
Celery 재시도 설정 테스트

- Non-Retryable 오류가 재시도되지 않는지 확인
- Retryable 오류(5xx)가 재시도되는지 확인
- Backoff, Jitter 설정이 적용되었는지 확인
"""

import pytest
from celery.exceptions import Retry
from unittest.mock import MagicMock, patch

from shopping.constants import TOSS_NON_RETRYABLE_ERRORS, TOSS_RETRYABLE_ERRORS
from shopping.tasks.payment_tasks import call_toss_confirm_api, finalize_payment_confirm
from shopping.utils.toss_payment import TossPaymentError


class TestRetryConfiguration:
    """Celery 재시도 설정 검증"""

    def test_backoff_enabled_on_call_toss_confirm_api(self):
        """call_toss_confirm_api에 지수 백오프가 활성화되어 있는지"""
        assert call_toss_confirm_api.retry_backoff is True

    def test_jitter_enabled_on_call_toss_confirm_api(self):
        """call_toss_confirm_api에 Jitter가 활성화되어 있는지"""
        assert call_toss_confirm_api.retry_jitter is True

    def test_max_backoff_limit_on_call_toss_confirm_api(self):
        """call_toss_confirm_api에 최대 백오프 시간이 설정되어 있는지"""
        assert call_toss_confirm_api.retry_backoff_max == 180

    def test_acks_late_enabled_on_call_toss_confirm_api(self):
        """call_toss_confirm_api에 acks_late가 활성화되어 있는지"""
        assert call_toss_confirm_api.acks_late is True

    def test_time_limit_on_call_toss_confirm_api(self):
        """call_toss_confirm_api에 타임아웃이 설정되어 있는지"""
        assert call_toss_confirm_api.time_limit == 30
        assert call_toss_confirm_api.soft_time_limit == 25

    def test_backoff_enabled_on_finalize_payment_confirm(self):
        """finalize_payment_confirm에 지수 백오프가 활성화되어 있는지"""
        assert finalize_payment_confirm.retry_backoff is True

    def test_jitter_enabled_on_finalize_payment_confirm(self):
        """finalize_payment_confirm에 Jitter가 활성화되어 있는지"""
        assert finalize_payment_confirm.retry_jitter is True


class TestNonRetryableErrorConstants:
    """Non-Retryable 오류 상수 검증"""

    def test_already_processed_payment_is_non_retryable(self):
        """ALREADY_PROCESSED_PAYMENT은 재시도 불가"""
        assert "ALREADY_PROCESSED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS

    def test_invalid_amount_is_non_retryable(self):
        """INVALID_AMOUNT은 재시도 불가"""
        assert "INVALID_AMOUNT" in TOSS_NON_RETRYABLE_ERRORS

    def test_reject_card_payment_is_non_retryable(self):
        """REJECT_CARD_PAYMENT은 재시도 불가"""
        assert "REJECT_CARD_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS

    def test_network_error_is_retryable(self):
        """NETWORK_ERROR는 재시도 가능"""
        assert "NETWORK_ERROR" in TOSS_RETRYABLE_ERRORS

    def test_timeout_is_retryable(self):
        """TIMEOUT은 재시도 가능"""
        assert "TIMEOUT" in TOSS_RETRYABLE_ERRORS

    def test_server_error_is_retryable(self):
        """서버 오류(500)는 재시도 가능"""
        assert "FAILED_INTERNAL_SYSTEM_PROCESSING" in TOSS_RETRYABLE_ERRORS


@pytest.mark.django_db
class TestNonRetryableErrors:
    """재시도하면 안 되는 오류 테스트"""

    @pytest.mark.parametrize("error_code", [
        "ALREADY_PROCESSED_PAYMENT",
        "INVALID_AMOUNT",
        "INVALID_CARD_NUMBER",
        "REJECT_CARD_PAYMENT",
        "PAY_PROCESS_CANCELED",
    ])
    def test_non_retryable_error_does_not_retry(self, mocker, error_code):
        """비즈니스 로직 오류는 재시도하지 않음"""
        from shopping.tests.factories import (
            OrderFactory,
            PaymentFactory,
            UserFactory,
            ProductFactory,
            OrderItemFactory,
        )

        # Arrange
        user = UserFactory()
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        PaymentFactory(order=order, status="in_progress")

        error = TossPaymentError(code=error_code, message="Test error")
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=error,
        )

        # retry가 호출되지 않아야 함
        mock_retry = mocker.patch.object(call_toss_confirm_api, "retry")

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            call_toss_confirm_api("test_key", order.id, int(order.total_amount))

        assert exc_info.value.code == error_code
        mock_retry.assert_not_called()

    def test_5xx_error_triggers_retry(self, mocker):
        """HTTP 5xx 오류는 재시도"""
        from shopping.tests.factories import (
            OrderFactory,
            PaymentFactory,
            UserFactory,
            ProductFactory,
            OrderItemFactory,
        )

        # Arrange
        user = UserFactory()
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        PaymentFactory(order=order, status="in_progress")

        error = TossPaymentError(code="UNKNOWN_ERROR", message="Server error")
        error.status_code = 500
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=error,
        )

        # retry 호출 시 Retry 예외 발생
        mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=Retry("Retrying"),
        )

        # Act & Assert
        with pytest.raises(Retry):
            call_toss_confirm_api("test_key", order.id, int(order.total_amount))
