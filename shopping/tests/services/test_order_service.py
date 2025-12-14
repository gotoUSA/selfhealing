"""OrderService 단위 테스트"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.db import transaction

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Category, Product
from shopping.models.user import User
from shopping.services.order_service import OrderService, OrderServiceError
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    CategoryFactory,
    ProductFactory,
    ShippingDataBuilder,
    UserFactory,
)


@pytest.mark.django_db
class TestOrderServiceCreateOrder:
    """주문 생성 테스트"""

    def test_create_order_from_cart_success(self):
        """장바구니에서 주문 생성 성공"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        initial_stock = product.stock

        # Act
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert
        assert order is not None
        assert order.user == user
        assert order.status == "pending"
        assert order.total_amount == Decimal("20000")  # 10000 * 2
        assert order.shipping_name == shipping_info["shipping_name"]
        assert order.order_number is not None

        # 주문 아이템 확인
        assert order.order_items.count() == 1
        order_item = order.order_items.first()
        assert order_item.product == product
        assert order_item.quantity == 2

        # 재고 차감 확인
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

        # 장바구니 비우기 확인
        assert cart.items.count() == 0

    def test_create_order_with_points(self):
        """포인트 사용하여 주문 생성"""
        # Arrange
        use_points = 5000
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        initial_points = user.points

        # Act
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=use_points,
            **shipping_info,
        )

        # Assert
        assert order.used_points == use_points
        # 배송비 포함해서 계산해야 함 (20000 + 배송비 - 5000)
        assert order.final_amount < order.total_amount + order.shipping_fee

        # 포인트 차감 확인
        user.refresh_from_db()
        assert user.points == initial_points - use_points

    def test_create_order_stock_shortage(self):
        """재고 부족 시 주문 생성 실패"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=10)  # 충분한 재고로 시작
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        # CartItem 생성 후 재고를 부족하게 변경 (CartItem validation 우회)
        product.stock = 1  # 주문 수량 2개보다 적게 설정
        product.save()

        # Act & Assert
        with pytest.raises(OrderServiceError) as exc_info:
            OrderService.create_order_from_cart(
                user=user,
                cart=cart,
                use_points=0,
                **shipping_info,
            )

        assert "재고가 부족합니다" in str(exc_info.value)

        # 트랜잭션 롤백 확인 (주문이 생성되지 않아야 함)
        assert Order.objects.filter(user=user).count() == 0

    def test_create_order_multiple_products(self):
        """여러 상품으로 주문 생성"""
        # Arrange
        user = UserFactory.with_points(10000)
        category = CategoryFactory()
        product1 = ProductFactory(
            name="상품1",
            price=Decimal("10000"),
            stock=10,
            category=category,
        )
        product2 = ProductFactory(
            name="상품2",
            price=Decimal("20000"),
            stock=10,
            category=category,
        )
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product1, quantity=1)
        CartItemFactory(cart=cart, product=product2, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        # Act
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert
        assert order.order_items.count() == 2
        assert order.total_amount == Decimal("50000")  # 10000*1 + 20000*2

        # 각 상품의 재고 차감 확인
        product1.refresh_from_db()
        product2.refresh_from_db()
        assert product1.stock == 9
        assert product2.stock == 8

    def test_create_order_with_shipping_fee(self):
        """배송비가 포함된 주문 생성"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        # Act
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert
        # 배송비는 ShippingService에 의해 계산됨
        assert order.shipping_fee >= 0
        assert order.final_amount == order.total_amount + order.shipping_fee + order.additional_shipping_fee

    def test_create_order_logging(self, caplog):
        """주문 생성 시 로깅 확인"""
        import logging

        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        # logger 이름을 명시하여 로그 캡처
        caplog.set_level(logging.INFO, logger="shopping.services.order_service")

        # Act
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert - caplog.records를 사용하여 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("주문 생성 시작" in msg for msg in log_messages)
        assert any("주문 생성 완료" in msg for msg in log_messages)
        assert any("재고 차감" in msg for msg in log_messages)
        assert any("주문 생성 프로세스 완료" in msg for msg in log_messages)
        assert any(f"order_id={order.id}" in msg for msg in log_messages)


@pytest.mark.django_db
class TestOrderServiceCancelOrder:
    """주문 취소 테스트"""

    def test_cancel_pending_order(self):
        """pending 상태 주문 취소"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        initial_stock = product.stock  # 주문 생성 전 재고 저장

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # 주문 생성 후 재고가 차감되었는지 확인
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

        # Act
        OrderService.cancel_order(order)

        # Assert
        order.refresh_from_db()
        assert order.status == "canceled"

        # 재고 복구 확인 (원래 재고로 돌아와야 함)
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_cancel_paid_order(self):
        """paid 상태 주문 취소 (sold_count도 차감)"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # paid 상태로 변경하고 sold_count 증가 시뮬레이션
        order.status = "paid"
        order.save()
        product.sold_count = 2
        product.save()

        initial_stock = product.stock
        initial_sold_count = product.sold_count

        # Act
        OrderService.cancel_order(order)

        # Assert
        order.refresh_from_db()
        assert order.status == "canceled"

        # 재고 복구 및 sold_count 차감 확인
        product.refresh_from_db()
        assert product.stock == initial_stock + 2
        assert product.sold_count == initial_sold_count - 2

    def test_cancel_order_not_allowed(self):
        """취소 불가능한 주문 (shipped 상태)"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)
        shipping_info = ShippingDataBuilder.default()

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        order.status = "shipped"  # 배송 중 상태로 변경
        order.save()

        # Act & Assert
        with pytest.raises(OrderServiceError) as exc_info:
            OrderService.cancel_order(order)

        assert "취소할 수 없는 주문입니다" in str(exc_info.value)

    def test_cancel_order_logging(self, caplog):
        """주문 취소 시 로깅 확인"""
        import logging

        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)
        shipping_info = ShippingDataBuilder.default()

        # logger 이름을 명시하여 로그 캡처
        caplog.set_level(logging.INFO, logger="shopping.services.order_service")

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Act
        OrderService.cancel_order(order)

        # Assert - caplog.records를 사용하여 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("주문 취소 시작" in msg for msg in log_messages)
        assert any("재고 복구" in msg for msg in log_messages)
        assert any("주문 취소 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestOrderServiceConcurrency:
    """동시성 제어 테스트"""

    def test_concurrent_order_creation_stock_management(self):
        """동시 주문 생성 시 재고 관리"""
        # Arrange
        category = CategoryFactory()
        product = ProductFactory(
            name="재고 테스트 상품",
            price=Decimal("10000"),
            stock=5,  # 재고 5개
            category=category,
        )

        # 첫 번째 사용자와 장바구니 (3개 주문)
        user1 = UserFactory.with_points(10000)
        cart1 = CartFactory(user=user1)
        CartItemFactory(cart=cart1, product=product, quantity=3)

        # 두 번째 사용자와 장바구니 (3개 주문)
        user2 = UserFactory(
            username="testuser2",
            email="test2@example.com",
            points=10000,
        )
        cart2 = CartFactory(user=user2)
        CartItemFactory(cart=cart2, product=product, quantity=3)

        shipping_info = ShippingDataBuilder.default()

        # Act & Assert
        # 첫 번째 주문은 성공해야 함
        order1 = OrderService.create_order_from_cart(
            user=user1,
            cart=cart1,
            use_points=0,
            **shipping_info,
        )
        assert order1 is not None

        # 두 번째 주문은 재고 부족으로 실패해야 함
        with pytest.raises(OrderServiceError):
            OrderService.create_order_from_cart(
                user=user2,
                cart=cart2,
                use_points=0,
                **shipping_info,
            )

        # 재고 확인 (첫 번째 주문만 차감됨)
        product.refresh_from_db()
        assert product.stock == 2  # 5 - 3


@pytest.mark.django_db(transaction=True)
class TestOrderServiceHybrid:
    """주문 하이브리드 처리 테스트 (Phase 2)"""

    def test_create_order_hybrid_success(self):
        """하이브리드 주문 생성 성공 - Order 레코드만 생성하고 task_id 반환"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        # Act
        order, task_id = OrderService.create_order_hybrid(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert: Order 생성 확인
        assert order is not None
        assert order.user == user
        assert order.status == "pending"  # 아직 미확정
        assert order.total_amount == Decimal("20000")

        # Assert: task_id 반환 확인
        assert task_id is not None
        assert isinstance(task_id, str)

        # Assert: OrderItem은 아직 생성되지 않음 (비동기 처리)
        assert order.order_items.count() == 0

        # Assert: 재고는 아직 차감되지 않음 (비동기 처리)
        product.refresh_from_db()
        assert product.stock == 100

        # Assert: 장바구니는 아직 비워지지 않음 (비동기 처리)
        assert cart.items.count() == 1

    def test_create_order_hybrid_empty_cart_fails(self):
        """빈 장바구니로 하이브리드 주문 생성 시 실패"""
        # Arrange
        user = UserFactory.with_points(10000)
        cart = CartFactory(user=user)  # 빈 장바구니
        shipping_info = ShippingDataBuilder.default()

        # Act & Assert
        with pytest.raises(OrderServiceError) as exc_info:
            OrderService.create_order_hybrid(
                user=user,
                cart=cart,
                use_points=0,
                **shipping_info,
            )

        assert "장바구니가 비어있습니다" in str(exc_info.value)

    def test_create_order_hybrid_point_validation(self):
        """하이브리드 주문에서 포인트 검증은 동기로 수행"""
        # Arrange
        user = UserFactory.with_points(500)  # 포인트 부족
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)
        shipping_info = ShippingDataBuilder.default()

        # Act & Assert: 포인트 부족으로 실패
        with pytest.raises(OrderServiceError) as exc_info:
            OrderService.create_order_hybrid(
                user=user,
                cart=cart,
                use_points=10000,  # 보유량보다 많음
                **shipping_info,
            )

        assert "포인트가 부족합니다" in str(exc_info.value)

    def test_create_order_hybrid_with_points(self):
        """포인트 사용한 하이브리드 주문 생성"""
        # Arrange
        use_points = 5000
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        initial_points = user.points

        # Act
        order, task_id = OrderService.create_order_hybrid(
            user=user,
            cart=cart,
            use_points=use_points,
            **shipping_info,
        )

        # Assert: Order에 포인트 정보 저장됨
        assert order.used_points == use_points
        assert order.final_amount < order.total_amount + order.shipping_fee

        # Assert: 포인트 실제 차감은 비동기 처리
        user.refresh_from_db()
        assert user.points == initial_points  # 아직 차감 안됨

    def test_create_order_hybrid_logging(self, caplog):
        """하이브리드 주문 생성 시 로깅 확인"""
        import logging

        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        shipping_info = ShippingDataBuilder.default()

        caplog.set_level(logging.INFO, logger="shopping.services.order_service")

        # Act
        order, task_id = OrderService.create_order_hybrid(
            user=user,
            cart=cart,
            use_points=0,
            **shipping_info,
        )

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("하이브리드 주문 생성 시작" in msg for msg in log_messages)
        assert any("Order 레코드 생성 완료" in msg for msg in log_messages)
        assert any("주문 비동기 처리 시작" in msg for msg in log_messages)
        assert any(f"order_id={order.id}" in msg for msg in log_messages)
        assert any(f"task_id={task_id}" in msg for msg in log_messages)

