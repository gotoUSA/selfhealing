"""ShippingService 단위 테스트"""

from decimal import Decimal

import pytest

from shopping.services.shipping_service import ShippingService


class TestShippingServiceCalculateFee:
    """배송비 계산 테스트"""

    def test_calculate_fee_under_threshold_normal_area(self):
        """무료배송 미달 + 일반 지역"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD - Decimal("5000")
        postal_code = "06234"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_over_threshold_normal_area(self):
        """무료배송 달성 + 일반 지역"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD + Decimal("5000")
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == Decimal("0")
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is True

    def test_calculate_fee_under_threshold_remote_area(self):
        """무료배송 미달 + 도서산간 지역"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD - Decimal("10000")
        postal_code = "63000"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == ShippingService.REMOTE_AREA_FEE
        assert result["is_free_shipping"] is False

    def test_calculate_fee_over_threshold_remote_area(self):
        """무료배송 달성 + 도서산간 지역 (기본비 무료, 추가비만 부과)"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD + Decimal("10000")
        postal_code = "59123"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == Decimal("0")
        assert result["additional_fee"] == ShippingService.REMOTE_AREA_FEE
        assert result["is_free_shipping"] is True

    def test_calculate_fee_exactly_threshold(self):
        """정확히 무료배송 기준 금액 (경계값)"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == Decimal("0")
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is True

    def test_calculate_fee_just_below_threshold(self):
        """무료배송 기준 직전 (경계값)"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD - Decimal("1")
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_zero_amount(self):
        """0원 주문 (경계값)"""
        # Arrange
        total_amount = Decimal("0")
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_empty_postal_code(self):
        """우편번호 없음 (경계값)"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD - Decimal("1000")
        postal_code = ""

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_default_postal_code(self):
        """우편번호 기본값 (경계값)"""
        # Arrange
        total_amount = ShippingService.FREE_SHIPPING_THRESHOLD - Decimal("1000")

        # Act
        result = ShippingService.calculate_fee(total_amount)

        # Assert
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_negative_amount(self):
        """음수 금액 (예외 케이스)"""
        # Arrange
        total_amount = Decimal("-1000")
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        # 음수도 기준 미달로 처리 (현재 서비스 로직 기준)
        assert result["shipping_fee"] == ShippingService.DEFAULT_SHIPPING_FEE
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is False

    def test_calculate_fee_very_large_amount(self):
        """매우 큰 금액 (예외 케이스 - Decimal 처리 확인)"""
        # Arrange
        total_amount = Decimal("999999999999.99")
        postal_code = "12345"

        # Act
        result = ShippingService.calculate_fee(total_amount, postal_code)

        # Assert
        assert result["shipping_fee"] == Decimal("0")
        assert result["additional_fee"] == Decimal("0")
        assert result["is_free_shipping"] is True


class TestShippingServiceIsRemoteArea:
    """도서산간 지역 판별 테스트"""

    def test_is_remote_area_jeju_63xxx(self):
        """제주 지역 (63xxx)"""
        # Arrange
        postal_code = "63000"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_jeju_63999(self):
        """제주 지역 경계값 (63999)"""
        # Arrange
        postal_code = "63999"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_ulleung_59xxx(self):
        """울릉도 지역 (59xxx)"""
        # Arrange
        postal_code = "59000"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_prefix_52xxx(self):
        """52xxx 도서산간 지역"""
        # Arrange
        postal_code = "52123"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_normal_seoul(self):
        """서울 일반 지역 (06xxx)"""
        # Arrange
        postal_code = "06234"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is False

    def test_is_remote_area_normal_busan(self):
        """부산 일반 지역 (48xxx)"""
        # Arrange
        postal_code = "48123"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is False

    def test_is_remote_area_empty_string(self):
        """빈 우편번호 (경계값)"""
        # Arrange
        postal_code = ""

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is False

    def test_is_remote_area_short_code(self):
        """짧은 우편번호 (경계값)"""
        # Arrange
        postal_code = "63"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_exact_prefix_59(self):
        """정확히 prefix만 일치 (경계값)"""
        # Arrange
        postal_code = "59"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is True

    def test_is_remote_area_similar_but_not_match(self):
        """유사하지만 불일치 (예외 케이스)"""
        # Arrange
        postal_code = "62999"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is False

    def test_is_remote_area_prefix_in_middle(self):
        """중간에 prefix 포함 (예외 케이스)"""
        # Arrange
        postal_code = "16300"

        # Act
        result = ShippingService.is_remote_area(postal_code)

        # Assert
        assert result is False
