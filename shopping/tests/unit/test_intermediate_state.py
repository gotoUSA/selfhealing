"""
장바구니/주문 중간 상태 처리 테스트

테스트 범위:
- 가격 스냅샷 (담은 시점 가격 저장 및 변경 감지)
- 재고/품절 검증 (주문 전 실시간 검증)
- 적립률/등급 스냅샷 (주문 시점 등급 저장)
- 장바구니 자동 정리 (품절/비활성 상품 처리)
"""

from decimal import Decimal

import pytest

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order
from shopping.services.cart_service import CartService
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    OrderFactory,
    OrderItemFactory,
    ProductFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCartPriceSnapshot:
    """장바구니 가격 스냅샷 테스트"""

    def test_price_at_add_saved_on_add_item(self):
        """장바구니 추가 시 가격 스냅샷 저장"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()

        # Act
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Assert
        assert cart_item.price_at_add == Decimal("10000")

    def test_price_at_add_preserved_on_quantity_update(self):
        """수량 변경 시 가격 스냅샷 유지"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=1)
        original_price = cart_item.price_at_add

        # Act
        product.price = Decimal("15000")
        product.save()
        CartService.update_item_quantity(cart, cart_item.id, quantity=3)
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.price_at_add == original_price
        assert cart_item.quantity == 3

    def test_is_price_changed_detects_increase(self):
        """가격 인상 감지"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Act
        product.price = Decimal("15000")
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.is_price_changed is True
        assert cart_item.price_difference == Decimal("5000")

    def test_is_price_changed_detects_decrease(self):
        """가격 인하 감지"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Act
        product.price = Decimal("8000")
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.is_price_changed is True
        assert cart_item.price_difference == Decimal("-2000")

    def test_is_price_changed_false_when_same(self):
        """가격 미변경 시 False"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Act & Assert
        assert cart_item.is_price_changed is False
        assert cart_item.price_difference == Decimal("0")

    def test_check_price_changes_returns_changed_items(self):
        """가격 변경된 상품 목록 반환"""
        # Arrange
        product1 = ProductFactory(price=Decimal("10000"))
        product2 = ProductFactory(price=Decimal("20000"))
        cart = CartFactory()
        CartService.add_item(cart, product1.id, quantity=1)
        CartService.add_item(cart, product2.id, quantity=1)

        # Act
        product1.price = Decimal("12000")
        product1.save()

        changes = CartService.check_price_changes(cart)

        # Assert
        assert len(changes) == 1
        assert changes[0].product_id == product1.id
        assert changes[0].original_price == Decimal("10000")
        assert changes[0].current_price == Decimal("12000")
        assert changes[0].change_type == "increased"

    def test_update_item_prices_updates_snapshots(self):
        """가격 업데이트 시 스냅샷 갱신"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        # Act
        updated_count = CartService.update_item_prices(cart)
        cart_item.refresh_from_db()

        # Assert
        assert updated_count == 1
        assert cart_item.price_at_add == Decimal("15000")
        assert cart_item.is_price_changed is False


@pytest.mark.django_db
class TestCartStockValidation:
    """장바구니 재고/품절 검증 테스트"""

    def test_check_stock_detects_inactive_product(self):
        """비활성 상품 감지"""
        # Arrange
        product = ProductFactory(is_active=True)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=1)
        product.is_active = False
        product.save()

        # Act
        issues = CartService.check_stock(cart)

        # Assert
        assert len(issues) == 1
        assert issues[0].issue_type == "inactive"

    def test_check_stock_detects_out_of_stock(self):
        """품절 상품 감지"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=1)
        product.stock = 0
        product.save()

        # Act
        issues = CartService.check_stock(cart)

        # Assert
        assert len(issues) == 1
        assert issues[0].issue_type == "out_of_stock"

    def test_check_stock_detects_insufficient_stock(self):
        """재고 부족 감지"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=8)
        product.stock = 5
        product.save()

        # Act
        issues = CartService.check_stock(cart)

        # Assert
        assert len(issues) == 1
        assert issues[0].issue_type == "insufficient"
        assert issues[0].requested == 8
        assert issues[0].available == 5

    def test_check_stock_returns_empty_when_no_issues(self):
        """문제 없을 시 빈 리스트 반환"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=5)

        # Act
        issues = CartService.check_stock(cart)

        # Assert
        assert len(issues) == 0

    def test_cleanup_removes_inactive_products(self):
        """비활성 상품 자동 제거"""
        # Arrange
        product = ProductFactory(is_active=True)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=1)
        product.is_active = False
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 1
        assert result["removed"][0]["reason"] == "판매 중단"
        assert cart.items.count() == 0

    def test_cleanup_removes_out_of_stock_products(self):
        """품절 상품 자동 제거"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=1)
        product.stock = 0
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 1
        assert result["removed"][0]["reason"] == "품절"
        assert cart.items.count() == 0

    def test_cleanup_adjusts_quantity_for_insufficient_stock(self):
        """재고 부족 시 수량 자동 조정"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        cart_item = CartService.add_item(cart, product.id, quantity=8)
        product.stock = 5
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)
        cart_item.refresh_from_db()

        # Assert
        assert result["updated_count"] == 1
        assert result["updated"][0]["old_quantity"] == 8
        assert result["updated"][0]["new_quantity"] == 5
        assert cart_item.quantity == 5


@pytest.mark.django_db
class TestMembershipSnapshot:
    """적립률/등급 스냅샷 테스트"""

    def test_earn_rate_snapshot_saved_on_order_creation(self):
        """주문 생성 시 적립률 스냅샷 저장"""
        # Arrange
        user = UserFactory(membership_level="gold")

        # Act
        order = OrderFactory(user=user)

        # Assert
        assert order.earn_rate_at_order == user.get_earn_rate()
        assert order.membership_at_order == "gold"

    def test_membership_snapshot_saved_on_order_creation(self):
        """주문 생성 시 등급 스냅샷 저장"""
        # Arrange
        user = UserFactory(membership_level="vip")

        # Act
        order = OrderFactory(user=user)

        # Assert
        assert order.membership_at_order == "vip"

    @pytest.mark.parametrize(
        "membership,expected_rate",
        [
            ("bronze", 1),
            ("silver", 2),
            ("gold", 3),
            ("vip", 5),
        ],
    )
    def test_earn_rate_matches_membership_level(self, membership, expected_rate):
        """등급별 적립률 일치 확인"""
        # Arrange
        user = UserFactory(membership_level=membership)

        # Act
        order = OrderFactory(user=user)

        # Assert
        assert order.earn_rate_at_order == expected_rate

    def test_order_uses_snapshot_not_current_membership(self):
        """주문은 스냅샷된 등급 사용 (현재 등급 아님)"""
        # Arrange
        user = UserFactory(membership_level="vip")
        order = OrderFactory(user=user, earn_rate_at_order=5, membership_at_order="vip")

        # Act - 등급 강등
        user.membership_level = "bronze"
        user.save()

        # Assert - 주문의 스냅샷은 변경되지 않음
        order.refresh_from_db()
        assert order.membership_at_order == "vip"
        assert order.earn_rate_at_order == 5


@pytest.mark.django_db
class TestIntermediateStateFlow:
    """중간 상태 처리 통합 플로우 테스트"""

    def test_price_change_detected_on_cart_retrieve(self):
        """장바구니 조회 시 가격 변경 감지"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        # Act
        price_changes = CartService.check_price_changes(cart)

        # Assert
        assert len(price_changes) == 1
        assert price_changes[0].difference == Decimal("5000")

    def test_full_cleanup_flow(self):
        """전체 정리 플로우 (비활성 + 품절 + 재고부족)"""
        # Arrange
        product_inactive = ProductFactory(is_active=True)
        product_out_of_stock = ProductFactory(stock=10)
        product_insufficient = ProductFactory(stock=10)
        product_ok = ProductFactory(stock=100)

        cart = CartFactory()
        CartService.add_item(cart, product_inactive.id, quantity=1)
        CartService.add_item(cart, product_out_of_stock.id, quantity=1)
        CartService.add_item(cart, product_insufficient.id, quantity=8)
        CartService.add_item(cart, product_ok.id, quantity=5)

        # 상품 상태 변경
        product_inactive.is_active = False
        product_inactive.save()
        product_out_of_stock.stock = 0
        product_out_of_stock.save()
        product_insufficient.stock = 3
        product_insufficient.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 2  # 비활성 + 품절
        assert result["updated_count"] == 1  # 재고부족
        assert cart.items.count() == 2  # 정상 + 수량조정된 상품

    def test_stock_issue_and_price_change_together(self):
        """재고 문제와 가격 변경 동시 발생"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), stock=10)
        cart = CartFactory()
        CartService.add_item(cart, product.id, quantity=8)

        # 상품 상태 변경
        product.price = Decimal("12000")
        product.stock = 5
        product.save()

        # Act
        stock_issues = CartService.check_stock(cart)
        price_changes = CartService.check_price_changes(cart)

        # Assert
        assert len(stock_issues) == 1
        assert stock_issues[0].issue_type == "insufficient"
        assert len(price_changes) == 1
        assert price_changes[0].difference == Decimal("2000")
