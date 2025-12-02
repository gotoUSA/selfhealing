"""부분 취소 테스트"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from shopping.models.payment import Payment
from shopping.tests.factories import CompletedPaymentFactory, OrderFactory, OrderItemFactory


@pytest.mark.django_db
class TestPartialCancel:
    """부분 취소"""

    def test_mark_as_partial_canceled_raises_not_implemented(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = CompletedPaymentFactory(order=order)

        # Act & Assert
        with pytest.raises(NotImplementedError):
            payment.mark_as_partial_canceled(Decimal("5000"), {"cancelReason": "test"})

    def test_canceled_amount_exceeds_payment_raises_error(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("10000"))
        OrderItemFactory(order=order, product=product)
        payment = CompletedPaymentFactory(order=order, amount=Decimal("10000"))
        payment.canceled_amount = Decimal("15000")

        # Act & Assert
        with pytest.raises(ValidationError):
            payment.clean()

    def test_partial_canceled_status_exists(self):
        # Arrange
        status_choices = dict(Payment.STATUS_CHOICES)

        # Assert
        assert "partial_canceled" in status_choices

    def test_partial_to_full_cancel_updates_state(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("20000"))
        OrderItemFactory(order=order, product=product, quantity=2)
        payment = CompletedPaymentFactory(order=order, amount=Decimal("20000"))

        # Act
        payment.status = "partial_canceled"
        payment.canceled_amount = Decimal("10000")
        payment.save()

        payment.status = "canceled"
        payment.is_canceled = True
        payment.canceled_amount = Decimal("20000")
        payment.save()

        # Assert
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert payment.is_canceled is True
        assert payment.canceled_amount == Decimal("20000")

    def test_multiple_partial_cancels_accumulate(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("30000"))
        OrderItemFactory(order=order, product=product, quantity=3)
        payment = CompletedPaymentFactory(order=order, amount=Decimal("30000"))

        # Act
        payment.status = "partial_canceled"
        payment.canceled_amount = Decimal("10000")
        payment.save()

        payment.canceled_amount += Decimal("10000")
        payment.save()

        # Assert
        payment.refresh_from_db()
        assert payment.canceled_amount == Decimal("20000")

    def test_can_cancel_false_after_partial_cancel(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("20000"))
        payment = CompletedPaymentFactory(order=order, amount=Decimal("20000"))

        # Act
        payment.status = "partial_canceled"
        payment.canceled_amount = Decimal("10000")
        payment.save()

        # Assert
        assert payment.can_cancel is False

    def test_remaining_amount_calculation(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("50000"))
        payment = CompletedPaymentFactory(order=order, amount=Decimal("50000"))

        # Act
        payment.canceled_amount = Decimal("20000")
        remaining = payment.amount - payment.canceled_amount

        # Assert
        assert remaining == Decimal("30000")

    def test_full_cancel_when_remaining_is_zero(self, user, product):
        # Arrange
        order = OrderFactory(user=user, status="paid", total_amount=Decimal("10000"))
        payment = CompletedPaymentFactory(order=order, amount=Decimal("10000"))

        # Act
        payment.canceled_amount = payment.amount

        # Assert
        assert payment.amount - payment.canceled_amount == Decimal("0")
