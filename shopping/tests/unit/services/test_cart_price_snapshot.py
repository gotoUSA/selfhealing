"""장바구니 가격 스냅샷 테스트

Phase 1: 장바구니에 담는 시점의 가격을 저장하고, 변경 시 감지
"""

from decimal import Decimal

import pytest

from shopping.models.cart import Cart, CartItem
from shopping.services.cart_service import CartService


class TestCartPriceSnapshot:
    """장바구니 가격 스냅샷 기능 테스트"""

    @pytest.mark.django_db
    def test_price_at_add_saved_on_add_item(self, user, product):
        """아이템 추가 시 price_at_add가 저장된다"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        original_price = product.price

        # Act
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Assert
        assert cart_item.price_at_add == original_price

    @pytest.mark.django_db
    def test_price_at_add_preserved_on_quantity_update(self, user, product):
        """수량 증가 시 price_at_add가 유지된다"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)
        original_snapshot = cart_item.price_at_add

        # Act
        CartService.add_item(cart, product.id, quantity=2)
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.quantity == 3
        assert cart_item.price_at_add == original_snapshot

    @pytest.mark.django_db
    def test_is_price_changed_false_when_no_change(self, user, product):
        """가격 변경 없으면 is_price_changed는 False"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)

        # Act
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Assert
        assert cart_item.is_price_changed is False

    @pytest.mark.django_db
    def test_is_price_changed_detects_increase(self, user, product):
        """가격 인상 시 is_price_changed가 True"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Act
        product.price = product.price + Decimal("5000")
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.is_price_changed is True

    @pytest.mark.django_db
    def test_is_price_changed_detects_decrease(self, user, product):
        """가격 인하 시 is_price_changed가 True"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        # Act
        product.price = product.price - Decimal("3000")
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.is_price_changed is True

    @pytest.mark.django_db
    def test_price_difference_positive_on_increase(self, user, product):
        """가격 인상 시 price_difference는 양수"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)
        increase_amount = Decimal("5000")

        # Act
        product.price = product.price + increase_amount
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.price_difference == increase_amount

    @pytest.mark.django_db
    def test_price_difference_negative_on_decrease(self, user, product):
        """가격 인하 시 price_difference는 음수"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)
        decrease_amount = Decimal("3000")

        # Act
        product.price = product.price - decrease_amount
        product.save()
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.price_difference == -decrease_amount

    @pytest.mark.django_db
    def test_price_difference_zero_when_null_snapshot(self, user, product, cart):
        """price_at_add가 None이면 price_difference는 0"""
        # Arrange - 직접 생성하여 price_at_add=None 상태
        cart_item = CartItem.objects.create(
            cart=cart,
            product=product,
            quantity=1,
            price_at_add=None,
        )

        # Assert
        assert cart_item.price_difference == Decimal("0")
        assert cart_item.is_price_changed is False


class TestCheckPriceChanges:
    """CartService.check_price_changes 테스트"""

    @pytest.mark.django_db
    def test_returns_empty_list_when_no_changes(self, user, product):
        """가격 변경 없으면 빈 리스트 반환"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        changes = CartService.check_price_changes(cart)

        # Assert
        assert changes == []

    @pytest.mark.django_db
    def test_detects_price_increase(self, user, product):
        """가격 인상 감지"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        original_price = product.price
        CartService.add_item(cart, product.id, quantity=1)

        product.price = original_price + Decimal("5000")
        product.save()

        # Act
        changes = CartService.check_price_changes(cart)

        # Assert
        assert len(changes) == 1
        assert changes[0].change_type == "increased"
        assert changes[0].original_price == original_price
        assert changes[0].current_price == product.price

    @pytest.mark.django_db
    def test_detects_price_decrease(self, user, product):
        """가격 인하 감지"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        original_price = product.price
        CartService.add_item(cart, product.id, quantity=1)

        product.price = original_price - Decimal("3000")
        product.save()

        # Act
        changes = CartService.check_price_changes(cart)

        # Assert
        assert len(changes) == 1
        assert changes[0].change_type == "decreased"

    @pytest.mark.django_db
    def test_detects_multiple_changes(self, user, product_factory):
        """여러 상품 가격 변경 감지"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        product1 = product_factory(price=Decimal("10000"), sku="P1")
        product2 = product_factory(price=Decimal("20000"), sku="P2")

        CartService.add_item(cart, product1.id, quantity=1)
        CartService.add_item(cart, product2.id, quantity=1)

        product1.price = Decimal("15000")
        product1.save()
        product2.price = Decimal("18000")
        product2.save()

        # Act
        changes = CartService.check_price_changes(cart)

        # Assert
        assert len(changes) == 2


class TestUpdateItemPrices:
    """CartService.update_item_prices 테스트"""

    @pytest.mark.django_db
    def test_updates_changed_prices(self, user, product):
        """변경된 가격을 현재 가격으로 업데이트"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        new_price = product.price + Decimal("5000")
        product.price = new_price
        product.save()

        # Act
        updated_count = CartService.update_item_prices(cart)

        # Assert
        assert updated_count == 1
        cart_item = cart.items.first()
        cart_item.refresh_from_db()
        assert cart_item.price_at_add == new_price
        assert cart_item.is_price_changed is False

    @pytest.mark.django_db
    def test_returns_zero_when_no_changes(self, user, product):
        """변경 없으면 0 반환"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        updated_count = CartService.update_item_prices(cart)

        # Assert
        assert updated_count == 0

    @pytest.mark.django_db
    def test_clears_price_changed_flag_after_update(self, user, product):
        """업데이트 후 is_price_changed가 False가 된다"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=1)

        product.price = product.price + Decimal("5000")
        product.save()
        cart_item.refresh_from_db()

        # 변경 확인
        assert cart_item.is_price_changed is True

        # Act
        CartService.update_item_prices(cart)
        cart_item.refresh_from_db()

        # Assert
        assert cart_item.is_price_changed is False
