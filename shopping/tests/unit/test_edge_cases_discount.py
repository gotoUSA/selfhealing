"""
할인율 계산 경계값 테스트

테스트 범위:
- Product.discount_percentage 계산
- WishlistProductSerializer.get_discount_rate 계산
- 0%, 100% 경계값 및 비정상 입력 처리
"""

from decimal import Decimal

import pytest

from shopping.serializers.wishlist_serializers import WishlistProductSerializer
from shopping.tests.factories import ProductFactory


@pytest.mark.django_db
class TestProductDiscountPercentage:
    """Product.discount_percentage 계산 테스트"""

    def test_discount_0_percent_when_no_compare_price(self):
        """compare_price가 None이면 할인율 0%"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), compare_price=None)

        # Act & Assert
        # is_on_sale은 compare_price가 None일 때 falsy 값 반환
        assert product.discount_percentage == 0
        assert not product.is_on_sale

    def test_discount_0_percent_when_prices_equal(self):
        """price와 compare_price가 같으면 할인율 0%"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), compare_price=Decimal("10000"))

        # Act & Assert
        assert product.discount_percentage == 0
        assert not product.is_on_sale

    def test_discount_10_percent(self):
        """10% 할인 (10000원 -> 9000원)"""
        # Arrange
        product = ProductFactory(price=Decimal("9000"), compare_price=Decimal("10000"))

        # Act & Assert
        assert product.discount_percentage == 10
        assert product.is_on_sale

    def test_discount_50_percent(self):
        """50% 할인"""
        # Arrange
        product = ProductFactory(price=Decimal("5000"), compare_price=Decimal("10000"))

        # Act & Assert
        assert product.discount_percentage == 50
        assert product.is_on_sale

    def test_discount_99_percent(self):
        """99% 할인 (1원 상품)"""
        # Arrange
        product = ProductFactory(price=Decimal("1"), compare_price=Decimal("100"))

        # Act & Assert
        assert product.discount_percentage == 99
        assert product.is_on_sale

    def test_discount_almost_100_percent(self):
        """거의 100% 할인 (1원 -> 10000원 원가)"""
        # Arrange
        product = ProductFactory(price=Decimal("1"), compare_price=Decimal("10000"))

        # Act & Assert
        # 99.99% -> int로 변환 시 99
        assert product.discount_percentage == 99

    def test_no_discount_when_price_higher_than_compare(self):
        """price가 compare_price보다 높으면 할인 아님"""
        # Arrange
        product = ProductFactory(price=Decimal("15000"), compare_price=Decimal("10000"))

        # Act & Assert
        assert not product.is_on_sale
        assert product.discount_percentage == 0

    def test_small_discount_rounds_down(self):
        """소수점 할인율은 내림 처리 (5.5% -> 5%)"""
        # Arrange - 10000 -> 9450 = 5.5% 할인
        product = ProductFactory(price=Decimal("9450"), compare_price=Decimal("10000"))

        # Act & Assert
        assert product.discount_percentage == 5


@pytest.mark.django_db
class TestWishlistSerializerDiscountRate:
    """WishlistProductSerializer.get_discount_rate 테스트"""

    def test_discount_rate_0_when_no_compare_price(self):
        """compare_price가 None이면 할인율 0"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), compare_price=None)

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert
        assert serializer.data["discount_rate"] == 0

    def test_discount_rate_0_when_prices_equal(self):
        """price와 compare_price가 같으면 할인율 0"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), compare_price=Decimal("10000"))

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert
        assert serializer.data["discount_rate"] == 0

    def test_discount_rate_10_percent(self):
        """10% 할인율 계산"""
        # Arrange
        product = ProductFactory(price=Decimal("9000"), compare_price=Decimal("10000"))

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert
        assert serializer.data["discount_rate"] == 10.0

    def test_discount_rate_50_percent(self):
        """50% 할인율 계산"""
        # Arrange
        product = ProductFactory(price=Decimal("5000"), compare_price=Decimal("10000"))

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert
        assert serializer.data["discount_rate"] == 50.0

    def test_discount_rate_no_discount_when_price_higher(self):
        """price가 더 높으면 할인 아님"""
        # Arrange
        product = ProductFactory(price=Decimal("15000"), compare_price=Decimal("10000"))

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert
        assert serializer.data["discount_rate"] == 0

    def test_discount_rate_decimal_precision(self):
        """소수점 첫째자리 반올림 정확성"""
        # Arrange - 10000 -> 8765 = 12.35% -> 12.4%
        product = ProductFactory(price=Decimal("8765"), compare_price=Decimal("10000"))

        # Act
        serializer = WishlistProductSerializer(product)

        # Assert - float 비교를 위해 float로 변환
        assert float(serializer.data["discount_rate"]) == 12.4


@pytest.mark.django_db
class TestDiscountEdgeCasesException:
    """할인 계산 예외 케이스"""

    def test_compare_price_zero_no_crash(self):
        """compare_price가 0이어도 에러 없음 (비정상 데이터 방어)"""
        # Arrange - DB에 직접 비정상 데이터가 있을 경우를 대비
        product = ProductFactory.build(price=Decimal("10000"), compare_price=Decimal("0"))

        # Act & Assert
        # ZeroDivisionError 발생하지 않아야 함
        # is_on_sale 조건에서 compare_price > price 체크로 방어됨
        # is_on_sale은 Decimal('0')과 같은 falsy 값 반환 가능
        assert not product.is_on_sale
        assert product.discount_percentage == 0

    def test_negative_price_no_crash(self):
        """음수 가격이어도 에러 없음 (비정상 데이터 방어)"""
        # Arrange
        product = ProductFactory.build(price=Decimal("-1000"), compare_price=Decimal("10000"))

        # Act & Assert
        # 비정상 데이터지만 크래시는 발생하지 않아야 함
        try:
            _ = product.discount_percentage
        except Exception:
            pytest.fail("음수 가격 처리 시 예외 발생")
