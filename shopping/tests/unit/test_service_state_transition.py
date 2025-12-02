"""서비스 레이어 상태 전이 테스트

결제 취소 파이프라인:
- 모든 side-effect 정상 작동
- 중복 취소 방지
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from shopping.models.point import PointHistory
from shopping.services.order_service import OrderService, OrderServiceError
from shopping.services.payment_service import PaymentCancelError, PaymentService
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    CategoryFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)


@pytest.mark.django_db(transaction=True)
class TestOrderCancelPipeline:
    """주문 취소 파이프라인 테스트"""

    def test_cancel_pending_order_restores_stock_only(self):
        """pending 주문 취소 시 재고만 복구 (sold_count 유지)"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = OrderFactory.pending(user=user, total_amount=Decimal("10000"))
        OrderItemFactory(order=order, product=product, quantity=2, price=product.price)

        # 주문 생성 시 재고 차감 시뮬레이션
        product.stock = 8
        product.sold_count = 0  # pending이므로 sold_count는 아직 0
        product.save()

        # Act
        OrderService.cancel_order(order)

        # Assert
        product.refresh_from_db()
        order.refresh_from_db()

        assert order.status == "canceled"
        assert product.stock == 10  # 재고 복구
        assert product.sold_count == 0  # sold_count는 유지

    def test_cancel_paid_order_restores_stock_and_sold_count(self):
        """paid 주문 취소 시 재고 + sold_count 모두 복구"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        # earned_points=0으로 설정하여 포인트 회수 로직 스킵
        order = OrderFactory.paid(user=user, total_amount=Decimal("10000"), earned_points=0)
        OrderItemFactory(order=order, product=product, quantity=2, price=product.price)

        # 결제 완료 시 상태 시뮬레이션
        product.stock = 8
        product.sold_count = 2
        product.save()

        # Act
        OrderService.cancel_order(order)

        # Assert
        product.refresh_from_db()
        order.refresh_from_db()

        assert order.status == "canceled"
        assert product.stock == 10  # 재고 복구
        assert product.sold_count == 0  # sold_count 차감

    def test_cancel_order_with_used_points_refunds_points(self):
        """포인트 사용 주문 취소 시 포인트 환불"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        # 포인트 적립 (환불 테스트용)
        PointService.add_points(user=user, amount=2000, type="earn", description="테스트 적립")

        order = OrderFactory.pending(
            user=user,
            total_amount=Decimal("10000"),
            used_points=2000,
            final_amount=Decimal("11000"),  # 10000 + 3000 배송비 - 2000 포인트
        )
        OrderItemFactory(order=order, product=product, quantity=1, price=product.price)

        # 포인트 사용 시뮬레이션
        PointService().use_points_fifo(user=user, amount=2000, type="use", order=order)
        user.refresh_from_db()
        assert user.points == 0

        # Act
        OrderService.cancel_order(order)

        # Assert
        user.refresh_from_db()
        order.refresh_from_db()

        assert order.status == "canceled"
        assert user.points == 2000  # 포인트 환불

        # 환불 이력 확인
        refund_history = PointHistory.objects.filter(user=user, type="cancel_refund", order=order).first()
        assert refund_history is not None
        assert refund_history.points == 2000

    def test_cancel_order_with_earned_points_deducts_points(self):
        """적립 포인트 있는 주문 취소 시 적립 포인트 회수"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("50000"))

        # 실제 포인트 적립 (FIFO 회수를 위해 PointHistory 필요)
        PointService.add_points(user=user, amount=500, type="earn", description="주문 적립")
        user.refresh_from_db()

        # 적립 포인트가 있는 주문
        order = OrderFactory.paid(
            user=user,
            total_amount=Decimal("50000"),
            earned_points=500,  # 1% 적립
        )
        OrderItemFactory(order=order, product=product, quantity=1, price=product.price)
        product.stock = 9
        product.sold_count = 1
        product.save()

        initial_points = user.points

        # Act
        OrderService.cancel_order(order)

        # Assert
        user.refresh_from_db()
        order.refresh_from_db()

        assert order.status == "canceled"
        assert user.points == initial_points - 500  # 적립 포인트 회수

        # 회수 이력 확인
        deduct_history = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).first()
        assert deduct_history is not None
        assert deduct_history.points == -500

    def test_cancel_already_canceled_order_fails(self):
        """이미 취소된 주문 재취소 시 실패"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10)

        order = OrderFactory.canceled(user=user)
        OrderItemFactory(order=order, product=product, quantity=1)

        # Act & Assert
        with pytest.raises(OrderServiceError, match="취소할 수 없는 주문"):
            OrderService.cancel_order(order)

    def test_cancel_shipped_order_fails(self):
        """배송중 주문 취소 시 실패"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        order = OrderFactory.shipped(user=user)

        # Act & Assert
        with pytest.raises(OrderServiceError, match="취소할 수 없는 주문"):
            OrderService.cancel_order(order)


@pytest.mark.django_db(transaction=True)
class TestPaymentCancelPipeline:
    """결제 취소 파이프라인 테스트"""

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_updates_all_states(self, mock_toss_client):
        """결제 취소 시 모든 상태 정확히 업데이트"""
        # Arrange
        mock_instance = mock_toss_client.return_value
        mock_instance.cancel_payment.return_value = {
            "status": "CANCELED",
            "cancelReason": "테스트 취소",
            "canceledAt": "2025-01-15T10:00:00+09:00",
            "totalAmount": 13000,
        }

        user = UserFactory(is_email_verified=True, points=0)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        # 실제 포인트 적립 (FIFO 회수를 위해 PointHistory 필요)
        PointService.add_points(user=user, amount=100, type="earn", description="주문 적립")
        user.refresh_from_db()

        order = OrderFactory.paid(
            user=user,
            total_amount=Decimal("10000"),
            earned_points=100,
        )
        OrderItemFactory(order=order, product=product, quantity=1, price=product.price)
        payment = PaymentFactory.done(order=order, payment_key="test_key_123")

        # 결제 완료 상태 시뮬레이션
        product.stock = 9
        product.sold_count = 1
        product.save()

        # Act
        result = PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="테스트 취소",
        )

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()
        user.refresh_from_db()

        # Payment 상태
        assert payment.status == "canceled"
        assert payment.is_canceled is True
        assert payment.cancel_reason == "테스트 취소"

        # Order 상태
        assert order.status == "canceled"

        # 재고/판매량 복구
        assert product.stock == 10
        assert product.sold_count == 0

        # 포인트 회수
        assert user.points == 0  # 100 - 100 (적립 회수)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_already_canceled_payment_fails(self, mock_toss_client):
        """이미 취소된 결제 재취소 시 실패"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        order = OrderFactory.canceled(user=user)
        payment = PaymentFactory.canceled(order=order)

        # Act & Assert
        with pytest.raises(PaymentCancelError, match="이미 취소된 결제"):
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="중복 취소 시도",
            )

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_with_used_points_refunds(self, mock_toss_client):
        """포인트 사용 결제 취소 시 포인트 환불"""
        # Arrange
        mock_instance = mock_toss_client.return_value
        mock_instance.cancel_payment.return_value = {
            "status": "CANCELED",
            "cancelReason": "변심",
            "canceledAt": "2025-01-15T10:00:00+09:00",
            "totalAmount": 11000,
        }

        user = UserFactory(is_email_verified=True, points=0)
        category = CategoryFactory()
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = OrderFactory.paid(
            user=user,
            total_amount=Decimal("10000"),
            used_points=2000,
            final_amount=Decimal("11000"),
            earned_points=0,
        )
        OrderItemFactory(order=order, product=product, quantity=1, price=product.price)
        payment = PaymentFactory.done(order=order, payment_key="test_key_456")
        product.stock = 9
        product.sold_count = 1
        product.save()

        # Act
        result = PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="변심",
        )

        # Assert
        user.refresh_from_db()
        assert user.points == 2000  # 사용 포인트 환불
        assert result["points_refunded"] == 2000
