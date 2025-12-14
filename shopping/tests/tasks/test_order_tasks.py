"""
주문 처리 태스크 테스트

Phase 2: Task 2-1에서 구현한 process_order_heavy_tasks 테스트
"""

import pytest
from decimal import Decimal
from unittest.mock import patch

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Product
from shopping.tasks.order_tasks import process_order_heavy_tasks


@pytest.mark.django_db(transaction=True)
class TestOrderTasksHappyPath:
    """주문 태스크 정상 케이스"""

    def test_process_order_heavy_tasks_success(
        self, user, product, category, seller_user
    ):
        """무거운 작업 처리가 성공적으로 완료됨

        - 재고 차감
        - OrderItem 생성
        - 장바구니 비우기
        - Order 상태를 confirmed로 변경
        """
        # Arrange: 장바구니와 주문 생성
        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            final_amount=product.price * 2,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act: 태스크 실행
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 결과 검증
        assert result["status"] == "success"
        assert result["order_id"] == order.id

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "confirmed"

        # OrderItem 생성 확인
        assert order.order_items.count() == 1
        order_item = order.order_items.first()
        assert order_item.product == product
        assert order_item.quantity == 2

        # 재고 차감 확인
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

        # 장바구니 비우기 확인
        cart.refresh_from_db()
        assert cart.items.count() == 0

    def test_process_order_heavy_tasks_with_points(
        self, user, product
    ):
        """포인트 사용이 포함된 주문 처리가 성공함"""
        # Arrange
        user.points = 5000
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price,
            used_points=1000,
            final_amount=product.price - 1000,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=1000
        )

        # Assert
        assert result["status"] == "success"

        order.refresh_from_db()
        assert order.status == "confirmed"

        # 포인트 차감 확인
        user.refresh_from_db()
        assert user.points == 4000


@pytest.mark.django_db(transaction=True)
class TestOrderTasksBoundary:
    """주문 태스크 경계 케이스"""

    def test_already_processed_order_ignored(self, user, product):
        """이미 처리된 주문은 무시됨 (멱등성)"""
        # Arrange: 이미 confirmed 상태인 주문
        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="confirmed",  # 이미 처리됨
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 이미 처리됨 응답
        assert result["status"] == "already_processed"
        assert result["order_id"] == order.id

        # 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_multiple_products_in_cart(
        self, user, product_factory
    ):
        """여러 상품이 담긴 장바구니 처리 성공"""
        # Arrange: 3개 상품
        products = [
            product_factory(name=f"상품{i}", sku=f"SKU-{i}", stock=10)
            for i in range(1, 4)
        ]

        cart = Cart.objects.create(user=user, is_active=True)
        for p in products:
            CartItem.objects.create(cart=cart, product=p, quantity=2)

        total = sum(p.price * 2 for p in products)
        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=total,
            final_amount=total,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert
        assert result["status"] == "success"
        assert order.order_items.count() == 3

        # 모든 상품의 재고 차감 확인
        for p in products:
            p.refresh_from_db()
            assert p.stock == 8  # 10 - 2


@pytest.mark.django_db(transaction=True)
class TestOrderTasksException:
    """주문 태스크 예외 케이스"""

    def test_insufficient_stock_fails_order(
        self, user, product
    ):
        """재고 부족 시 주문 실패 처리"""
        # Arrange: 재고가 1개인데 2개 주문
        product.stock = 1
        product.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            final_amount=product.price * 2,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_stock"
        assert product.name in result["product"]

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "재고 부족" in order.failure_reason

        # 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == 1

    def test_point_deduction_failure_rollback_stock(
        self, user, product
    ):
        """포인트 차감 실패 시 재고 롤백"""
        # Arrange: 포인트 부족
        user.points = 500
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            used_points=1000,  # 보유량보다 많음
            final_amount=product.price * 2 - 1000,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=1000
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "포인트 사용 실패" in order.failure_reason

        # 재고가 롤백됨 (차감됐다가 다시 복구됨)
        product.refresh_from_db()
        assert product.stock == initial_stock

        # 포인트는 차감되지 않음
        user.refresh_from_db()
        assert user.points == 500

    def test_point_deduction_with_minimum_amount(
        self, user, product
    ):
        """최소 포인트 사용 금액(100) 미만은 실패"""
        # Arrange
        user.points = 50
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price,
            used_points=50,  # 최소 금액 미만
            final_amount=product.price - 50,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=50
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # 재고가 롤백됨
        product.refresh_from_db()
        assert product.stock == initial_stock
