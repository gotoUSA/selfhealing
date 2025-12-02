"""
Cart/CartItem 모델 단위 테스트

커버리지 미달 라인 커버:
- Cart.clean() 유효성 검증 (line 91, 96)
- Cart.get_total_quantity() (line 118-122)
- Cart.deactivate() (line 130-131)
- Cart.get_or_create_active_cart() ValueError (line 153)
- Cart.merge_anonymous_cart() (line 181-209)
- CartItem.increase_quantity() (line 255-256)
- CartItem.decrease_quantity() (line 260-265)
- CartItem.update_quantity() (line 269-274)
- CartItem.clean() (line 282-285)
"""

import pytest
from django.core.exceptions import ValidationError

from shopping.models.cart import Cart, CartItem
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    ProductFactory,
    UserFactory,
)


# ==========================================
# Cart.clean() 유효성 검증 테스트
# ==========================================


@pytest.mark.django_db
class TestCartClean:
    """Cart.clean() 유효성 검증"""

    def test_raises_error_when_both_user_and_session_key_missing(self):
        """user와 session_key 모두 없으면 ValidationError 발생"""
        # Arrange
        cart = Cart(user=None, session_key=None)

        # Act
        with pytest.raises(ValidationError) as exc_info:
            cart.full_clean()

        # Assert
        assert "회원 또는 세션 키 중 하나는 필수입니다" in str(exc_info.value)

    def test_allows_both_user_and_session_key_present(self):
        """user와 session_key 둘 다 있는 경우 허용 (병합 시나리오)"""
        # Arrange
        user = UserFactory()
        cart = Cart(user=user, session_key="test_session_key_12345")

        # Act - ValidationError 발생하지 않아야 함
        cart.full_clean()

        # Assert
        assert cart.user == user
        assert cart.session_key == "test_session_key_12345"


# ==========================================
# Cart.get_total_quantity() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartGetTotalQuantity:
    """Cart.get_total_quantity() 총 수량 계산"""

    def test_returns_zero_for_empty_cart(self):
        """빈 장바구니는 0 반환"""
        # Arrange
        cart = CartFactory()

        # Act
        result = cart.get_total_quantity()

        # Assert
        assert result == 0

    def test_returns_correct_sum_for_multiple_items(self):
        """여러 아이템의 수량 합계 정확히 반환"""
        # Arrange
        cart = CartFactory()
        CartItemFactory(cart=cart, quantity=3)
        CartItemFactory(cart=cart, quantity=5)
        CartItemFactory(cart=cart, quantity=2)

        # Act
        result = cart.get_total_quantity()

        # Assert
        assert result == 10

    def test_returns_single_item_quantity(self):
        """단일 아이템 수량 정확히 반환"""
        # Arrange
        cart = CartFactory()
        CartItemFactory(cart=cart, quantity=7)

        # Act
        result = cart.get_total_quantity()

        # Assert
        assert result == 7


# ==========================================
# Cart.deactivate() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartDeactivate:
    """Cart.deactivate() 비활성화 처리"""

    def test_sets_is_active_to_false(self):
        """is_active를 False로 변경"""
        # Arrange
        cart = CartFactory(is_active=True)
        assert cart.is_active is True

        # Act
        cart.deactivate()

        # Assert
        cart.refresh_from_db()
        assert cart.is_active is False

    def test_persists_change_to_database(self):
        """변경사항이 DB에 저장됨"""
        # Arrange
        cart = CartFactory(is_active=True)
        cart_id = cart.id

        # Act
        cart.deactivate()

        # Assert
        db_cart = Cart.objects.get(id=cart_id)
        assert db_cart.is_active is False


# ==========================================
# Cart.get_or_create_active_cart() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartGetOrCreateActiveCart:
    """Cart.get_or_create_active_cart() 장바구니 생성/조회"""

    def test_raises_error_when_neither_user_nor_session_key(self):
        """user와 session_key 모두 None이면 ValueError 발생"""
        # Act
        with pytest.raises(ValueError) as exc_info:
            Cart.get_or_create_active_cart(user=None, session_key=None)

        # Assert
        assert "user 또는 session_key 중 하나는 필수입니다" in str(exc_info.value)


# ==========================================
# Cart.merge_anonymous_cart() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartMergeAnonymousCart:
    """Cart.merge_anonymous_cart() 비회원 장바구니 병합"""

    def test_merges_anonymous_items_to_user_cart(self):
        """비회원 장바구니 아이템을 회원 장바구니로 병합"""
        # Arrange
        user = UserFactory()
        session_key = "anon_session_key_12345"
        product = ProductFactory()

        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product, quantity=3)

        # Act
        result_cart = Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert result_cart.user == user
        assert result_cart.items.count() == 1
        assert result_cart.items.first().quantity == 3

    def test_adds_quantity_when_same_product_exists(self):
        """동일 상품이 있으면 수량 합산"""
        # Arrange
        user = UserFactory()
        session_key = "anon_session_key_merge"
        product = ProductFactory()

        user_cart = CartFactory(user=user)
        CartItemFactory(cart=user_cart, product=product, quantity=2)

        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product, quantity=5)

        # Act
        result_cart = Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert result_cart.items.count() == 1
        assert result_cart.items.first().quantity == 7

    def test_returns_user_cart_when_no_anonymous_cart(self):
        """비회원 장바구니가 없으면 회원 장바구니만 반환"""
        # Arrange
        user = UserFactory()
        nonexistent_session = "nonexistent_session_key"

        # Act
        result_cart = Cart.merge_anonymous_cart(user=user, session_key=nonexistent_session)

        # Assert
        assert result_cart.user == user
        assert result_cart.is_active is True

    def test_deletes_anonymous_cart_after_merge(self):
        """병합 후 비회원 장바구니 삭제"""
        # Arrange
        user = UserFactory()
        session_key = "anon_session_to_delete"
        product = ProductFactory()

        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product, quantity=1)
        anon_cart_id = anon_cart.id

        # Act
        Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert not Cart.objects.filter(id=anon_cart_id).exists()

    def test_merges_multiple_different_products(self):
        """여러 다른 상품을 올바르게 병합"""
        # Arrange
        user = UserFactory()
        session_key = "anon_session_multi"
        product1 = ProductFactory()
        product2 = ProductFactory()

        anon_cart = CartFactory(user=None, session_key=session_key)
        CartItemFactory(cart=anon_cart, product=product1, quantity=2)
        CartItemFactory(cart=anon_cart, product=product2, quantity=4)

        # Act
        result_cart = Cart.merge_anonymous_cart(user=user, session_key=session_key)

        # Assert
        assert result_cart.items.count() == 2
        assert result_cart.get_total_quantity() == 6


# ==========================================
# CartItem.increase_quantity() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartItemIncreaseQuantity:
    """CartItem.increase_quantity() 수량 증가"""

    def test_increases_quantity_by_default_one(self):
        """기본값 1만큼 수량 증가"""
        # Arrange
        cart_item = CartItemFactory(quantity=5)

        # Act
        cart_item.increase_quantity()

        # Assert
        cart_item.refresh_from_db()
        assert cart_item.quantity == 6

    def test_increases_quantity_by_specified_amount(self):
        """지정된 수량만큼 증가"""
        # Arrange
        cart_item = CartItemFactory(quantity=3)

        # Act
        cart_item.increase_quantity(quantity=4)

        # Assert
        cart_item.refresh_from_db()
        assert cart_item.quantity == 7


# ==========================================
# CartItem.decrease_quantity() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartItemDecreaseQuantity:
    """CartItem.decrease_quantity() 수량 감소"""

    def test_decreases_quantity_when_remaining_positive(self):
        """감소 후 양수가 남으면 수량 감소"""
        # Arrange
        cart_item = CartItemFactory(quantity=10)

        # Act
        cart_item.decrease_quantity(quantity=3)

        # Assert
        cart_item.refresh_from_db()
        assert cart_item.quantity == 7

    def test_deletes_item_when_quantity_becomes_zero(self):
        """수량이 0이 되면 아이템 삭제"""
        # Arrange
        cart_item = CartItemFactory(quantity=5)
        item_id = cart_item.id

        # Act
        cart_item.decrease_quantity(quantity=5)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()

    def test_deletes_item_when_decrease_exceeds_current(self):
        """감소량이 현재 수량보다 크면 아이템 삭제"""
        # Arrange
        cart_item = CartItemFactory(quantity=3)
        item_id = cart_item.id

        # Act
        cart_item.decrease_quantity(quantity=10)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()


# ==========================================
# CartItem.update_quantity() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartItemUpdateQuantity:
    """CartItem.update_quantity() 수량 직접 설정"""

    def test_updates_to_positive_quantity(self):
        """양수 수량으로 업데이트"""
        # Arrange
        cart_item = CartItemFactory(quantity=5)

        # Act
        cart_item.update_quantity(quantity=15)

        # Assert
        assert cart_item.quantity == 15

    def test_deletes_item_when_quantity_zero(self):
        """수량 0으로 설정 시 아이템 삭제"""
        # Arrange
        cart_item = CartItemFactory(quantity=5)
        item_id = cart_item.id

        # Act
        cart_item.update_quantity(quantity=0)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()

    def test_deletes_item_when_quantity_negative(self):
        """음수 수량 설정 시 아이템 삭제"""
        # Arrange
        cart_item = CartItemFactory(quantity=5)
        item_id = cart_item.id

        # Act
        cart_item.update_quantity(quantity=-1)

        # Assert
        assert not CartItem.objects.filter(id=item_id).exists()


# ==========================================
# CartItem.clean() 테스트
# ==========================================


@pytest.mark.django_db
class TestCartItemClean:
    """CartItem.clean() 유효성 검증"""

    def test_raises_error_when_quantity_exceeds_stock(self):
        """수량이 재고를 초과하면 ValidationError 발생"""
        # Arrange
        product = ProductFactory(stock=5)
        cart_item = CartItemFactory.build(product=product, quantity=10)
        cart_item.cart = CartFactory()
        cart_item.product = product

        # Act
        with pytest.raises(ValidationError) as exc_info:
            cart_item.clean()

        # Assert
        assert "재고가 부족합니다" in str(exc_info.value)
        assert "5개" in str(exc_info.value)
