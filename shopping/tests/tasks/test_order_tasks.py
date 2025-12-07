"""
주문 처리 태스크 테스트

Phase 2: Task 2-1에서 구현한 process_order_heavy_tasks 테스트
"""

from decimal import Decimal

import pytest

from shopping.models.cart import Cart, CartItem
from shopping.tasks.order_tasks import process_order_heavy_tasks
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    OrderFactory,
    ProductFactory,
    UserFactory,
)


# ==========================================
# 주문 태스크 정상 케이스
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestOrderTasksHappyPath:
    """주문 태스크 정상 케이스"""

    def test_process_order_heavy_tasks_success(self):
        """무거운 작업 처리가 성공적으로 완료됨

        - 재고 차감
        - OrderItem 생성
        - 장바구니 비우기
        - Order 상태를 confirmed로 변경
        """
        # Arrange - Factory 사용
        user = UserFactory()
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price * 2,
            final_amount=product.price * 2,
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=0)

        # Assert - 결과 검증
        assert result["status"] == "success"
        assert result["order_id"] == order.id

        # Assert - Order 상태 확인
        order.refresh_from_db()
        assert order.status == "confirmed"

        # Assert - OrderItem 생성 확인
        assert order.order_items.count() == 1
        order_item = order.order_items.first()
        assert order_item.product == product
        assert order_item.quantity == 2

        # Assert - 재고 차감 확인
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

        # Assert - 장바구니 비우기 확인
        cart.refresh_from_db()
        assert cart.items.count() == 0

    def test_process_order_heavy_tasks_with_points(self):
        """포인트 사용이 포함된 주문 처리가 성공함"""
        # Arrange - Factory 사용
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price,
            used_points=1000,
            final_amount=product.price - 1000,
        )

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=1000)

        # Assert
        assert result["status"] == "success"

        order.refresh_from_db()
        assert order.status == "confirmed"

        # Assert - 포인트 차감 확인
        user.refresh_from_db()
        assert user.points == 4000


# ==========================================
# 주문 태스크 경계 케이스
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestOrderTasksBoundary:
    """주문 태스크 경계 케이스"""

    def test_already_processed_order_ignored(self):
        """이미 처리된 주문은 무시됨 (멱등성)"""
        # Arrange - 이미 confirmed 상태인 주문
        user = UserFactory()
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        order = OrderFactory(
            user=user,
            status="confirmed",  # 이미 처리됨
            total_amount=product.price,
            final_amount=product.price,
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=0)

        # Assert - 이미 처리됨 응답
        assert result["status"] == "already_processed"
        assert result["order_id"] == order.id

        # Assert - 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_multiple_products_in_cart(self):
        """여러 상품이 담긴 장바구니 처리 성공"""
        # Arrange - 3개 상품
        user = UserFactory()
        products = [ProductFactory(stock=10) for _ in range(3)]

        cart = CartFactory(user=user)
        for p in products:
            CartItemFactory(cart=cart, product=p, quantity=2)

        total = sum(p.price * 2 for p in products)
        order = OrderFactory.pending(
            user=user,
            total_amount=total,
            final_amount=total,
        )

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=0)

        # Assert
        assert result["status"] == "success"
        assert order.order_items.count() == 3

        # Assert - 모든 상품의 재고 차감 확인
        for p in products:
            p.refresh_from_db()
            assert p.stock == 8  # 10 - 2


# ==========================================
# 주문 태스크 예외 케이스
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestOrderTasksException:
    """주문 태스크 예외 케이스"""

    def test_insufficient_stock_fails_order(self):
        """재고 부족 시 주문 실패 처리"""
        # Arrange - 재고가 1개인데 2개 주문
        user = UserFactory()
        product = ProductFactory(stock=1)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price * 2,
            final_amount=product.price * 2,
        )

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=0)

        # Assert - 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_stock"
        assert product.name in result["product"]

        # Assert - Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "재고 부족" in order.failure_reason

        # Assert - 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == 1

    def test_point_deduction_failure_rollback_stock(self):
        """포인트 차감 실패 시 재고 롤백"""
        # Arrange - 포인트 부족
        user = UserFactory.with_points(500)
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price * 2,
            used_points=1000,  # 보유량보다 많음
            final_amount=product.price * 2 - 1000,
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=1000)

        # Assert - 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # Assert - Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "포인트 사용 실패" in order.failure_reason

        # Assert - 재고가 롤백됨 (차감됐다가 다시 복구됨)
        product.refresh_from_db()
        assert product.stock == initial_stock

        # Assert - 포인트는 차감되지 않음
        user.refresh_from_db()
        assert user.points == 500

    def test_point_deduction_with_minimum_amount(self):
        """최소 포인트 사용 금액(100) 미만은 실패"""
        # Arrange
        user = UserFactory.with_points(50)
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price,
            used_points=50,  # 최소 금액 미만
            final_amount=product.price - 50,
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=50)

        # Assert - 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # Assert - 재고가 롤백됨
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_insufficient_stock_restores_cart(self):
        """재고 부족 시 장바구니가 복구됨"""
        # Arrange - 재고가 1개인데 2개 주문
        user = UserFactory()
        product = ProductFactory(stock=1)
        cart = CartFactory(user=user, is_active=False)  # 주문 생성시 비활성화됨
        CartItemFactory(cart=cart, product=product, quantity=2)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price * 2,
            final_amount=product.price * 2,
        )

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=0)

        # Assert - 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_stock"

        # Assert - 장바구니가 복구됨
        cart.refresh_from_db()
        assert cart.is_active is True

    def test_point_deduction_failure_restores_cart(self):
        """포인트 차감 실패 시 장바구니가 복구됨"""
        # Arrange - 포인트 부족
        user = UserFactory.with_points(500)
        product = ProductFactory(stock=10)
        cart = CartFactory(user=user, is_active=False)  # 주문 생성시 비활성화됨
        CartItemFactory(cart=cart, product=product, quantity=2)

        order = OrderFactory.pending(
            user=user,
            total_amount=product.price * 2,
            used_points=1000,  # 보유량보다 많음
            final_amount=product.price * 2 - 1000,
        )

        # Act
        result = process_order_heavy_tasks(order_id=order.id, cart_id=cart.id, use_points=1000)

        # Assert - 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # Assert - 장바구니가 복구됨
        cart.refresh_from_db()
        assert cart.is_active is True
