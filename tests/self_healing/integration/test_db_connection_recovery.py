"""
DB 연결 끊김 및 복구 테스트

Note: This module is skipped - DB infrastructure tests.
"""

import pytest

# Skip entire module - DB infrastructure tests
pytestmark = pytest.mark.requires_db
from django.db import OperationalError
from django.db.utils import InterfaceError

from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentService
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)


@pytest.mark.django_db(transaction=True)
class TestDBConnectionRecovery:
    """DB 연결 복구"""

    def test_db_disconnect_during_transaction_triggers_rollback(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="ready")
        initial_stock = product.stock

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=TossResponseBuilder.success_response(),
        )
        mocker.patch.object(
            Payment,
            "mark_as_paid",
            side_effect=OperationalError("server closed the connection unexpectedly"),
        )

        # Act
        with pytest.raises(OperationalError):
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.total_amount),
                user=user,
            )

        # Assert
        product.refresh_from_db()
        payment.refresh_from_db()
        assert product.stock == initial_stock
        assert payment.status == "ready"

    def test_retry_after_interface_error_succeeds(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="ready")
        toss_response = TossResponseBuilder.success_response()
        call_count = {"value": 0}

        def mock_confirm(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise InterfaceError("connection already closed")
            return toss_response

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=mock_confirm,
        )

        # Act
        with pytest.raises(InterfaceError):
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.total_amount),
                user=user,
            )

        payment.refresh_from_db()
        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key",
            order_id=order.id,
            amount=int(order.total_amount),
            user=user,
        )

        # Assert
        assert result["payment"].status == "done"

    def test_db_error_preserves_order_and_payment_state(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="ready")
        original_save = Order.save

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=TossResponseBuilder.success_response(),
        )

        def mock_save(self, *args, **kwargs):
            if self.status == "paid":
                raise OperationalError("database is locked")
            return original_save(self, *args, **kwargs)

        mocker.patch.object(Order, "save", mock_save)

        # Act
        with pytest.raises(OperationalError):
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.total_amount),
                user=user,
            )

        # Assert
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == "confirmed"
        assert payment.status == "ready"
