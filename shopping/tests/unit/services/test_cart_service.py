"""CartService 단위 테스트

커버리지 보완:
- get_or_create_cart: 세션 자동 생성, 에러 핸들링
- update_item_quantity: 수량 0 시 삭제
- _validate_quantity: 최대 수량 초과
- _get_product: 상품 미존재 에러
- cleanup_unavailable_items: 비활성/품절/재고부족 처리
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from shopping.models.cart import Cart
from shopping.services.cart_service import CartService, CartServiceError


@pytest.mark.django_db
class TestGetOrCreateCart:
    """get_or_create_cart 메서드 테스트"""

    def test_creates_cart_with_request_auto_session(self, user_factory):
        """request 객체로 세션 자동 생성"""
        # Arrange
        mock_request = MagicMock()
        mock_request.session.session_key = None
        mock_request.session.create = MagicMock()

        def create_session():
            mock_request.session.session_key = "auto_generated_session_key"

        mock_request.session.create.side_effect = create_session

        # Act
        cart = CartService.get_or_create_cart(request=mock_request)

        # Assert
        mock_request.session.create.assert_called_once()
        assert cart is not None
        assert cart.session_key == "auto_generated_session_key"

    def test_raises_error_without_identity(self):
        """user, session_key, request 모두 없으면 에러"""
        # Act & Assert
        with pytest.raises(CartServiceError) as exc_info:
            CartService.get_or_create_cart()

        assert exc_info.value.code == "MISSING_IDENTITY"


@pytest.mark.django_db
class TestUpdateItemQuantity:
    """update_item_quantity 메서드 테스트"""

    def test_quantity_zero_removes_item(self, user, product):
        """수량 0으로 변경 시 아이템 삭제"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        cart_item = CartService.add_item(cart, product.id, quantity=2)
        item_id = cart_item.id

        # Act
        result = CartService.update_item_quantity(cart, item_id, quantity=0)

        # Assert
        assert result is None
        assert not cart.items.filter(pk=item_id).exists()


@pytest.mark.django_db
class TestValidateQuantity:
    """_validate_quantity 메서드 테스트"""

    def test_max_quantity_exceeded_raises_error(self, user, product):
        """최대 수량 초과 시 에러"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)

        # Act & Assert
        with pytest.raises(CartServiceError) as exc_info:
            CartService.add_item(cart, product.id, quantity=1000)

        assert exc_info.value.code == "QUANTITY_EXCEEDED"


@pytest.mark.django_db
class TestGetProduct:
    """_get_product 메서드 테스트"""

    def test_nonexistent_product_raises_error(self, user):
        """존재하지 않는 상품 ID로 추가 시 에러"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)

        # Act & Assert
        with pytest.raises(CartServiceError) as exc_info:
            CartService.add_item(cart, product_id=99999, quantity=1)

        assert exc_info.value.code == "PRODUCT_NOT_FOUND"

    def test_inactive_product_raises_error(self, user, inactive_product):
        """비활성 상품 추가 시 에러"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)

        # Act & Assert
        with pytest.raises(CartServiceError) as exc_info:
            CartService.add_item(cart, product_id=inactive_product.id, quantity=1)

        assert exc_info.value.code == "PRODUCT_NOT_FOUND"


@pytest.mark.django_db
class TestCleanupUnavailableItems:
    """cleanup_unavailable_items 메서드 테스트"""

    def test_removes_inactive_product(self, user, product):
        """비활성 상품 제거"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        product.is_active = False
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 1
        assert result["removed"][0]["reason"] == "판매 중단"
        assert cart.items.count() == 0

    def test_removes_out_of_stock_product(self, user, product):
        """품절 상품 제거"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        product.stock = 0
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 1
        assert result["removed"][0]["reason"] == "품절"
        assert cart.items.count() == 0

    def test_adjusts_insufficient_stock(self, user, product):
        """재고 부족 시 수량 조정"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=5)

        product.stock = 2
        product.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["updated_count"] == 1
        assert result["updated"][0]["old_quantity"] == 5
        assert result["updated"][0]["new_quantity"] == 2
        assert cart.items.first().quantity == 2

    def test_handles_multiple_issues(self, user, product_factory):
        """여러 문제 동시 처리"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)

        inactive = product_factory(sku="INACTIVE-001")
        out_of_stock = product_factory(sku="OOS-001")
        insufficient = product_factory(sku="INSUF-001", stock=10)

        CartService.add_item(cart, inactive.id, quantity=1)
        CartService.add_item(cart, out_of_stock.id, quantity=1)
        CartService.add_item(cart, insufficient.id, quantity=5)

        # 아이템 추가 후 상품 상태 변경
        inactive.is_active = False
        inactive.save()
        out_of_stock.stock = 0
        out_of_stock.save()
        insufficient.stock = 3  # 5개 담았는데 재고가 3개로 줄어듦
        insufficient.save()

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 2
        assert result["updated_count"] == 1
        assert cart.items.count() == 1

    def test_no_changes_when_all_valid(self, user, product):
        """모든 상품 정상 시 변경 없음"""
        # Arrange
        cart, _ = Cart.get_or_create_active_cart(user=user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        result = CartService.cleanup_unavailable_items(cart)

        # Assert
        assert result["removed_count"] == 0
        assert result["updated_count"] == 0
        assert cart.items.count() == 1
