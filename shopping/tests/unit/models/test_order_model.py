"""
Order 및 OrderItem 모델 테스트

커버리지 대상 라인: 212, 217, 231, 235-268, 326-338
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from shopping.models.order import Order, OrderItem
from shopping.tests.factories import OrderFactory, OrderItemFactory, ProductFactory


@pytest.mark.django_db
class TestOrder:
    """Order 모델 프로퍼티 및 검증 테스트"""

    # ==========================================
    # Happy Path - Properties
    # ==========================================

    def test_full_shipping_address_returns_combined(self):
        order = OrderFactory.build(
            shipping_address="서울시 강남구",
            shipping_address_detail="101동 202호",
        )

        assert order.get_full_shipping_address == "서울시 강남구 101동 202호"

    @pytest.mark.parametrize(
        "status",
        ["paid", "preparing", "shipped", "delivered"],
    )
    def test_is_paid_returns_true_for_paid_statuses(self, status):
        order = OrderFactory.build(status=status)

        assert order.is_paid is True

    @pytest.mark.parametrize(
        "status",
        ["pending", "confirmed", "canceled", "refunded"],
    )
    def test_is_paid_returns_false_for_unpaid_statuses(self, status):
        order = OrderFactory.build(status=status)

        assert order.is_paid is False

    def test_payment_method_display_returns_method_name(self):
        order = OrderFactory.build(payment_method="card")

        assert order.payment_method_display == "신용/체크카드"

    def test_payment_method_display_returns_default(self):
        order = OrderFactory.build(payment_method="")

        assert order.payment_method_display == "결제 전"

    # ==========================================
    # Happy Path - Clean
    # ==========================================

    def test_clean_passes_with_valid_data(self):
        order = OrderFactory.build(
            total_amount=Decimal("10000"),
            used_points=0,
            final_amount=Decimal("10000"),
            is_free_shipping=False,
            shipping_fee=Decimal("3000"),
            status="pending",
        )

        order.clean()  # ValidationError 없이 통과

    # ==========================================
    # Exception - Clean
    # ==========================================

    def test_clean_fails_with_mismatched_final_amount(self):
        order = OrderFactory.build(
            total_amount=Decimal("10000"),
            used_points=0,
            final_amount=Decimal("5000"),  # 불일치
        )

        with pytest.raises(ValidationError) as exc_info:
            order.clean()

        assert "final_amount" in exc_info.value.message_dict

    def test_clean_fails_with_free_shipping_and_fee(self):
        order = OrderFactory.build(
            total_amount=Decimal("10000"),
            used_points=0,
            final_amount=Decimal("10000"),
            is_free_shipping=True,
            shipping_fee=Decimal("3000"),  # 무료배송인데 배송비 존재
        )

        with pytest.raises(ValidationError) as exc_info:
            order.clean()

        assert "is_free_shipping" in exc_info.value.message_dict

    @pytest.mark.parametrize(
        "status",
        ["paid", "preparing", "shipped", "delivered"],
    )
    def test_clean_fails_without_payment_method_for_paid_status(self, status):
        order = OrderFactory.build(
            total_amount=Decimal("10000"),
            used_points=0,
            final_amount=Decimal("10000"),
            status=status,
            payment_method="",  # 결제방법 없음
        )

        with pytest.raises(ValidationError) as exc_info:
            order.clean()

        assert "payment_method" in exc_info.value.message_dict

    def test_clean_fails_with_negative_used_points(self):
        order = Order(
            total_amount=Decimal("10000"),
            used_points=-100,  # 음수 포인트
            final_amount=Decimal("10100"),
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
        )

        with pytest.raises(ValidationError) as exc_info:
            order.clean()

        assert "used_points" in exc_info.value.message_dict

    def test_clean_fails_with_used_points_exceeding_total(self):
        order = OrderFactory.build(
            total_amount=Decimal("10000"),
            used_points=15000,  # 총 금액 초과
            final_amount=Decimal("-5000"),
        )

        with pytest.raises(ValidationError) as exc_info:
            order.clean()

        assert "used_points" in exc_info.value.message_dict


@pytest.mark.django_db
class TestOrderItem:
    """OrderItem 모델 검증 테스트"""

    # ==========================================
    # Happy Path
    # ==========================================

    def test_clean_passes_with_valid_data(self):
        product = ProductFactory()
        order = OrderFactory()
        order_item = OrderItemFactory.build(
            order=order,
            product=product,
            product_name="테스트 상품",
            quantity=2,
            price=Decimal("10000"),
        )

        order_item.clean()  # ValidationError 없이 통과

    # ==========================================
    # Exception
    # ==========================================

    def test_clean_fails_with_zero_quantity(self):
        order_item = OrderItem(
            product_name="테스트 상품",
            quantity=0,  # 0개
            price=Decimal("10000"),
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        assert "quantity" in exc_info.value.message_dict

    def test_clean_fails_with_negative_price(self):
        order_item = OrderItem(
            product_name="테스트 상품",
            quantity=1,
            price=Decimal("-1000"),  # 음수 가격
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        assert "price" in exc_info.value.message_dict

    def test_clean_fails_with_empty_product_name(self):
        order_item = OrderItem(
            product_name="",  # 빈 문자열
            quantity=1,
            price=Decimal("10000"),
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        assert "product_name" in exc_info.value.message_dict

    def test_clean_fails_with_whitespace_product_name(self):
        order_item = OrderItem(
            product_name="   ",  # 공백만
            quantity=1,
            price=Decimal("10000"),
        )

        with pytest.raises(ValidationError) as exc_info:
            order_item.clean()

        assert "product_name" in exc_info.value.message_dict
