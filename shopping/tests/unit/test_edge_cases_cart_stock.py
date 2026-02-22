"""
장바구니/재고 경계값 테스트

테스트 범위:
- 수량 = 재고 경계값
- 재고 1개 동시 주문
- 수량 업데이트 극단값
- 비활성 상품 처리
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from shopping.models.cart import Cart, CartItem
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    CategoryFactory,
    ProductFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCartQuantityBoundary:
    """장바구니 수량 경계값 테스트"""

    def test_quantity_equals_stock(self):
        """수량 = 재고 (정확히 max)"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()

        # Act
        item = CartItemFactory(cart=cart, product=product, quantity=10)

        # Assert
        assert item.quantity == 10
        assert item.quantity == product.stock

    def test_quantity_stock_minus_1(self):
        """수량 = 재고 - 1"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()

        # Act
        item = CartItemFactory(cart=cart, product=product, quantity=9)

        # Assert
        assert item.quantity == 9
        assert item.quantity < product.stock

    def test_quantity_exceeds_stock_validation(self):
        """수량 > 재고 시 ValidationError"""
        # Arrange
        product = ProductFactory(stock=5)
        cart = CartFactory()
        item = CartItemFactory.build(cart=cart, product=product, quantity=10)
        item.cart = cart
        item.product = product

        # Act
        with pytest.raises(ValidationError) as exc_info:
            item.clean()

        # Assert
        assert "재고가 부족" in str(exc_info.value)

    def test_quantity_1_with_stock_1(self):
        """재고 1개일 때 수량 1개"""
        # Arrange
        product = ProductFactory.low_stock()  # stock=1
        cart = CartFactory()

        # Act
        item = CartItemFactory(cart=cart, product=product, quantity=1)

        # Assert
        assert item.quantity == 1
        assert product.stock == 1


@pytest.mark.django_db
class TestCartQuantityUpdate:
    """장바구니 수량 업데이트 테스트"""

    def test_increase_quantity_within_stock(self):
        """재고 범위 내 수량 증가"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        item = CartItemFactory(cart=cart, product=product, quantity=5)

        # Act
        item.increase_quantity(3)

        item.refresh_from_db()

        # Assert
        assert item.quantity == 8

    def test_update_quantity_to_stock_max(self):
        """재고 최대값으로 수량 업데이트"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        item = CartItemFactory(cart=cart, product=product, quantity=5)

        # Act
        item.update_quantity(10)

        # Assert
        assert item.quantity == 10

    def test_update_quantity_to_zero_deletes(self):
        """수량 0으로 업데이트 시 아이템 삭제"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        item = CartItemFactory(cart=cart, product=product, quantity=5)
        item_id = item.id

        # Act
        item.update_quantity(0)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()

    def test_update_quantity_negative_deletes(self):
        """음수 수량 업데이트 시 아이템 삭제"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        item = CartItemFactory(cart=cart, product=product, quantity=5)
        item_id = item.id

        # Act
        item.update_quantity(-1)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()

    def test_decrease_below_zero_deletes(self):
        """감소로 0 이하가 되면 아이템 삭제"""
        # Arrange
        product = ProductFactory(stock=10)
        cart = CartFactory()
        item = CartItemFactory(cart=cart, product=product, quantity=3)
        item_id = item.id

        # Act
        item.decrease_quantity(5)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()


@pytest.mark.django_db
class TestStockBoundary:
    """재고 경계값 테스트"""

    def test_stock_0_is_out_of_stock(self):
        """재고 0개는 품절"""
        # Arrange & Act
        product = ProductFactory.out_of_stock()

        # Assert
        assert product.stock == 0
        assert product.is_in_stock is False
        assert product.stock_status == "품절"

    def test_stock_1_is_in_stock(self):
        """재고 1개는 재고 있음"""
        # Arrange & Act
        product = ProductFactory.low_stock()

        # Assert
        assert product.stock == 1
        assert product.is_in_stock is True
        assert product.stock_status == "재고 부족"

    def test_stock_9_is_low_stock(self):
        """재고 9개는 재고 부족"""
        # Arrange & Act
        product = ProductFactory(stock=9)

        # Assert
        assert product.stock == 9
        assert product.is_in_stock is True
        assert product.stock_status == "재고 부족"

    def test_stock_10_is_sufficient(self):
        """재고 10개는 재고 충분"""
        # Arrange & Act
        product = ProductFactory(stock=10)

        # Assert
        assert product.stock == 10
        assert product.is_in_stock is True
        assert product.stock_status == "재고 충분"

    def test_can_purchase_exact_stock(self):
        """정확히 재고 수량만큼 구매 가능"""
        # Arrange
        product = ProductFactory(stock=5)

        # Act & Assert
        assert product.can_purchase(5) is True
        assert product.can_purchase(6) is False

    def test_can_purchase_zero_quantity(self):
        """0개 구매 시도"""
        # Arrange
        product = ProductFactory(stock=5)

        # Act & Assert - 0개는 구매 가능 (로직상)
        assert product.can_purchase(0) is True


@pytest.mark.django_db
class TestInactiveProductCart:
    """비활성 상품 장바구니 처리 테스트"""

    def test_inactive_product_can_purchase_ignores_is_active(self):
        """can_purchase는 is_active를 체크하지 않음 (재고와 is_available만 확인)"""
        # Arrange
        product = ProductFactory.inactive()

        # Act & Assert
        # can_purchase()는 is_active를 확인하지 않음 - 의도된 설계
        # is_active 체크는 View/Serializer 레벨에서 별도로 수행해야 함
        assert product.is_active is False
        assert product.is_in_stock is True  # stock > 0 and is_available
        assert product.can_purchase(1) is True  # is_active와 무관하게 재고만 체크

    def test_cart_with_inactive_product(self):
        """비활성 상품이 담긴 장바구니"""
        # Arrange
        product = ProductFactory(stock=10, is_active=True)
        cart = CartFactory()
        CartItemFactory(cart=cart, product=product, quantity=1)

        # Act
        product.is_active = False
        product.save()

        # Assert - 장바구니 아이템은 남아있지만 상품은 비활성
        assert cart.items.count() == 1
        product.refresh_from_db()
        assert product.is_active is False


@pytest.mark.django_db
class TestCartMergeBoundary:
    """장바구니 병합 경계값 테스트"""

    def test_merge_same_product_quantity_sum(self):
        """동일 상품 병합 시 수량 합산"""
        # Arrange
        user = UserFactory()
        session_key = "test_session_merge_001"
        product = ProductFactory(stock=100)

        # 회원 장바구니: 3개
        user_cart = CartFactory(user=user)
        CartItemFactory(cart=user_cart, product=product, quantity=3)

        # 비회원 장바구니: 5개
        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product, quantity=5)

        # Act
        result = Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert - 합계: 8개
        assert result.items.count() == 1
        assert result.items.first().quantity == 8

    def test_merge_empty_anonymous_cart(self):
        """빈 비회원 장바구니 병합"""
        # Arrange
        user = UserFactory()
        session_key = "nonexistent_session"

        # 회원 장바구니만 존재
        user_cart = CartFactory(user=user)
        product = ProductFactory()
        CartItemFactory(cart=user_cart, product=product, quantity=2)

        # Act
        result = Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert result.items.count() == 1
        assert result.items.first().quantity == 2

    def test_merge_deletes_anonymous_cart(self):
        """병합 후 비회원 장바구니 삭제"""
        # Arrange
        user = UserFactory()
        session_key = "test_session_delete_001"
        product = ProductFactory()

        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product, quantity=1)
        anon_cart_id = anon_cart.id

        # Act
        Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert not Cart.objects.filter(id=anon_cart_id).exists()


@pytest.mark.django_db
class TestCartTotalCalculation:
    """장바구니 총액 계산 테스트"""

    def test_empty_cart_total_zero(self):
        """빈 장바구니 총액 0"""
        # Arrange & Act
        cart = CartFactory()

        # Assert
        assert cart.get_total_quantity() == 0
        assert cart.get_total_amount() == Decimal("0")

    def test_single_item_total(self):
        """단일 아이템 총액"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart = CartFactory()
        CartItemFactory(cart=cart, product=product, quantity=3)

        # Act & Assert
        assert cart.get_total_amount() == Decimal("30000")

    def test_multiple_items_total(self):
        """여러 아이템 총액"""
        # Arrange
        category = CategoryFactory()
        product1 = ProductFactory(price=Decimal("10000"), category=category)
        product2 = ProductFactory(price=Decimal("20000"), category=category)
        cart = CartFactory()
        CartItemFactory(cart=cart, product=product1, quantity=2)  # 20000
        CartItemFactory(cart=cart, product=product2, quantity=1)  # 20000

        # Act & Assert
        assert cart.get_total_amount() == Decimal("40000")
        assert cart.get_total_quantity() == 3
