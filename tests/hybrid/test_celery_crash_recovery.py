"""Celery Worker 크래시 복구 테스트"""

import pytest
from celery.exceptions import Retry

from shopping.models.payment import Payment
from shopping.tasks.payment_tasks import call_toss_confirm_api, finalize_payment_confirm
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)
from shopping.utils.toss_payment import TossPaymentError


@pytest.mark.django_db
class TestCeleryCrashRecovery:
    """Celery 크래시 복구"""

    def test_network_error_aborts_payment_and_triggers_retry(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="in_progress")

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError(code="NETWORK_ERROR", message="Connection failed"),
        )

        # retry 호출 시 Retry 예외 발생
        mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=Retry("Retrying due to network error"),
        )

        # Act
        with pytest.raises(Retry):
            call_toss_confirm_api("test_payment_key", order.id, int(order.total_amount))

        # Assert - 네트워크 에러 시 payment가 aborted로 변경되고 retry 시도
        payment.refresh_from_db()
        assert payment.status == "aborted"

    def test_max_retry_exceeded_aborts_payment(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="in_progress")

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError(code="TIMEOUT", message="Request timed out"),
        )
        mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=TossPaymentError(code="TIMEOUT", message="Request timed out"),
        )

        # Act
        with pytest.raises(TossPaymentError):
            call_toss_confirm_api("test_payment_key", order.id, int(order.total_amount))

        # Assert
        payment.refresh_from_db()
        assert payment.status == "aborted"

    def test_finalize_payment_retry_returns_already_processed(self):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="in_progress")
        toss_response = TossResponseBuilder.success_response()

        # Act
        result1 = finalize_payment_confirm(toss_response, payment.id, user.id)
        result2 = finalize_payment_confirm(toss_response, payment.id, user.id)

        # Assert
        assert result1["status"] == "success"
        assert result2["status"] == "already_processed"

    def test_worker_crash_preserves_payment_state(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="in_progress")
        payment_id = payment.id

        # mark_as_paid 시점에서 크래시 시뮬레이션
        call_count = {"value": 0}

        def mock_mark_as_paid(self, data):
            call_count["value"] += 1
            raise Exception("Worker crashed during mark_as_paid")

        mocker.patch.object(Payment, "mark_as_paid", mock_mark_as_paid)

        # Act
        with pytest.raises(Exception, match="Worker crashed"):
            finalize_payment_confirm(TossResponseBuilder.success_response(), payment_id, user.id)

        # Assert - 트랜잭션 롤백으로 상태 유지
        payment.refresh_from_db()
        assert payment.status == "in_progress"

    def test_duplicate_message_does_not_increment_sold_count(self):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="in_progress")
        toss_response = TossResponseBuilder.success_response()

        # Act
        finalize_payment_confirm(toss_response, payment.id, user.id)
        product.refresh_from_db()
        sold_after_first = product.sold_count

        finalize_payment_confirm(toss_response, payment.id, user.id)
        product.refresh_from_db()

        # Assert
        assert product.sold_count == sold_after_first
