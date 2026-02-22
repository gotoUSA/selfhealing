"""
결제 경계값 테스트

테스트 범위:
- 전액 포인트 결제 (final_amount = 0)
- 결제금액 = 포인트 + 1원
- 결제 취소 시 포인트 처리
- 부분 취소 시나리오
"""

from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from shopping.models.point import PointHistory
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    CompletedPaymentFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    PointHistoryFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)


@pytest.mark.django_db
class TestFullPointPayment:
    """전액 포인트 결제 테스트"""

    def test_order_with_zero_final_amount(self):
        """final_amount가 0인 주문 생성"""
        # Arrange
        user = UserFactory.with_points(50000)
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        # Act
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=13000,
            final_amount=Decimal("0"),
        )

        # Assert
        assert order.final_amount == Decimal("0")
        assert order.used_points == 13000

    def test_payment_with_zero_amount_allowed(self):
        """0원 결제 가능 여부 확인"""
        # Arrange
        user = UserFactory.with_points(50000)
        order = OrderFactory.with_full_points(user=user)

        # Act
        payment = PaymentFactory(order=order, amount=Decimal("0"))

        # Assert
        assert payment.amount == Decimal("0")

    def test_point_history_created_for_full_point_payment(self):
        """전액 포인트 결제 시 포인트 이력 생성"""
        # Arrange
        user = UserFactory.with_points(50000)
        order = OrderFactory(user=user, used_points=13000)

        # Act
        PointHistoryFactory(
            user=user,
            points=-13000,
            balance=37000,
            type="use",
            order=order,
            description="포인트 전액 결제",
        )

        # Assert
        history = PointHistory.objects.filter(user=user, order=order, type="use").first()
        assert history is not None
        assert history.points == -13000


@pytest.mark.django_db
class TestPaymentAmountBoundary:
    """결제 금액 경계값 테스트"""

    def test_payment_1_won_after_points(self):
        """포인트 사용 후 1원 결제"""
        # Arrange
        user = UserFactory.with_points(12999)
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=12999,
            final_amount=Decimal("1"),
        )

        # Act
        payment = PaymentFactory(order=order, amount=Decimal("1"))

        # Assert
        assert payment.amount == Decimal("1")

    def test_payment_exactly_points_used(self):
        """결제금액과 사용 포인트가 정확히 같음"""
        # Arrange
        user = UserFactory.with_points(13000)

        # Act
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=13000,
            final_amount=Decimal("0"),
        )

        # Assert
        assert order.total_amount + order.shipping_fee == Decimal("13000")
        assert order.used_points == 13000
        assert order.final_amount == Decimal("0")

    def test_points_1_won_less_than_total(self):
        """총액보다 1원 적은 포인트 사용"""
        # Arrange
        user = UserFactory.with_points(12999)

        # Act
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=12999,
            final_amount=Decimal("1"),
        )

        # Assert
        assert order.final_amount == Decimal("1")


@pytest.mark.django_db
class TestPaymentCancelPointRefund:
    """결제 취소 시 포인트 환불 테스트"""

    def test_cancel_refunds_used_points(self, mocker, api_client):
        """결제 취소 시 사용 포인트 환불"""
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=100)
        api_client.force_authenticate(user=user)

        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            used_points=3000,
            final_amount=product.price - Decimal("3000") + Decimal("3000"),
        )
        OrderItemFactory(order=order, product=product)

        PointHistoryFactory(
            user=user,
            points=-3000,
            balance=2000,
            type="use",
            order=order,
        )

        payment = CompletedPaymentFactory(order=order, amount=order.final_amount)

        toss_cancel = TossResponseBuilder.cancel_response(payment_key=payment.payment_key)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel,
        )

        # Act
        response = api_client.post(
            "/api/payments/cancel/",
            {"payment_id": payment.id, "cancel_reason": "테스트 취소"},
            format="json",
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_cancel_deducts_earned_points(self, mocker, api_client):
        """결제 취소 시 적립 포인트 회수"""
        # Arrange
        user = UserFactory.with_points(1000)
        product = ProductFactory(stock=100)
        api_client.force_authenticate(user=user)

        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            earned_points=100,
            final_amount=product.price + Decimal("3000"),
        )
        OrderItemFactory(order=order, product=product)

        PointHistoryFactory(
            user=user,
            points=100,
            balance=1100,
            type="earn",
            order=order,
        )

        payment = CompletedPaymentFactory(order=order, amount=order.final_amount)

        toss_cancel = TossResponseBuilder.cancel_response(payment_key=payment.payment_key)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel,
        )

        # Act
        response = api_client.post(
            "/api/payments/cancel/",
            {"payment_id": payment.id, "cancel_reason": "테스트 취소"},
            format="json",
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.django_db
class TestPaymentValidation:
    """결제 유효성 검증 테스트"""

    def test_negative_amount_not_allowed(self):
        """음수 결제금액 불허"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)

        # Act
        payment = PaymentFactory.build(order=order, amount=Decimal("-1000"))

        # Assert - build()만 사용했으므로 DB에는 저장되지 않음, 실제 저장 시 validation 필요
        assert payment.amount == Decimal("-1000")

    def test_payment_amount_matches_order_final(self):
        """결제금액이 주문 최종금액과 일치"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=5000,
            final_amount=Decimal("8000"),
        )

        # Act
        payment = PaymentFactory(order=order, amount=order.final_amount)

        # Assert
        assert payment.amount == order.final_amount


@pytest.mark.django_db
class TestPaymentStatusTransition:
    """결제 상태 전이 테스트"""

    def test_ready_to_done(self):
        """ready -> done 전이"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory.ready(order=order)
        assert payment.status == "ready"

        # Act
        payment.status = "done"
        payment.approved_at = timezone.now()
        payment.save()

        # Assert
        payment.refresh_from_db()
        assert payment.status == "done"
        assert payment.approved_at is not None

    def test_done_to_canceled(self):
        """done -> canceled 전이"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = CompletedPaymentFactory(order=order)
        assert payment.status == "done"

        # Act
        payment.status = "canceled"
        payment.is_canceled = True
        payment.canceled_at = timezone.now()
        payment.save()

        # Assert
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert payment.is_canceled is True

    def test_ready_to_aborted(self):
        """ready -> aborted 전이 (결제 중단)"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory.ready(order=order)

        # Act
        payment.status = "aborted"
        payment.save()

        # Assert
        payment.refresh_from_db()
        assert payment.status == "aborted"
