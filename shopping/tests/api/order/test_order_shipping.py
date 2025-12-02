from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.order import Order


@pytest.mark.django_db
class TestShippingFeeCalculation:
    """배송비 계산 정상 케이스"""

    def test_standard_area_with_fee(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """일반 지역 배송비 부과 (3만원 미만)"""
        # Arrange - 20,000원 상품 장바구니 담기
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("20000")
        assert order.shipping_fee == Decimal("3000")
        assert order.additional_shipping_fee == Decimal("0")
        assert order.is_free_shipping is False
        assert order.final_amount == Decimal("23000")

    def test_standard_area_free_shipping(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """일반 지역 무료배송 (3만원 이상)"""
        # Arrange - 35,000원 상품 장바구니 담기
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("35000")
        assert order.shipping_fee == Decimal("0")
        assert order.additional_shipping_fee == Decimal("0")
        assert order.is_free_shipping is True
        assert order.final_amount == Decimal("35000")

    def test_remote_area_with_fee(self, authenticated_client, user, product, add_to_cart_helper, remote_shipping_data):
        """도서산간 지역 배송비 부과 (3만원 미만 + 제주)"""
        # Arrange - 20,000원 상품 + 제주 배송지
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("20000")
        assert order.shipping_fee == Decimal("3000")
        assert order.additional_shipping_fee == Decimal("3000")
        assert order.is_free_shipping is False
        assert order.final_amount == Decimal("26000")

    def test_remote_area_free_shipping(self, authenticated_client, user, product, add_to_cart_helper, remote_shipping_data):
        """도서산간 지역 무료배송 (3만원 이상 + 제주)"""
        # Arrange - 35,000원 상품 + 제주 배송지
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("35000")
        assert order.shipping_fee == Decimal("0")
        assert order.additional_shipping_fee == Decimal("3000")
        assert order.is_free_shipping is True
        assert order.final_amount == Decimal("38000")

    def test_multiple_products_reaches_threshold(
        self, authenticated_client, user, product_factory, add_to_cart_helper, shipping_data
    ):
        """여러 상품 조합으로 무료배송 달성"""
        # Arrange - 15,000원 상품 2개 = 30,000원
        product1 = product_factory(name="상품1", price=Decimal("15000"), sku="MULTI-001")
        product2 = product_factory(name="상품2", price=Decimal("15000"), sku="MULTI-002")
        add_to_cart_helper(user, product1, quantity=1)
        add_to_cart_helper(user, product2, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True
        assert order.final_amount == Decimal("30000")


@pytest.mark.django_db
class TestShippingFeeBoundary:
    """배송비 경계값 테스트"""

    def test_exact_threshold_amount(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """정확히 무료배송 기준 금액 (30,000원)"""
        # Arrange - 30,000원 상품
        product.price = Decimal("30000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True

    def test_below_threshold_by_one(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """무료배송 바로 아래 (29,999원)"""
        # Arrange - 29,999원 상품
        product.price = Decimal("29999")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("29999")
        assert order.shipping_fee == Decimal("3000")
        assert order.is_free_shipping is False
        assert order.final_amount == Decimal("32999")

    def test_above_threshold_by_one(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """무료배송 바로 위 (30,001원)"""
        # Arrange - 30,001원 상품
        product.price = Decimal("30001")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30001")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True

    def test_jeju_postal_code_detection(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data, postal_codes
    ):
        """제주 우편번호 감지 (63xxx)"""
        # Arrange - 제주 우편번호로 변경
        shipping_data["shipping_postal_code"] = postal_codes["jeju"]
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.additional_shipping_fee == Decimal("3000")

    def test_ulleung_postal_code_detection(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data, postal_codes
    ):
        """울릉도 우편번호 감지 (59xxx)"""
        # Arrange - 울릉도 우편번호로 변경
        shipping_data["shipping_postal_code"] = postal_codes["ulleung"]
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.additional_shipping_fee == Decimal("3000")

    def test_other_remote_postal_code_detection(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data, postal_codes
    ):
        """기타 도서산간 우편번호 감지 (52xxx)"""
        # Arrange - 도서산간 우편번호로 변경
        shipping_data["shipping_postal_code"] = postal_codes["other_remote"]
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.additional_shipping_fee == Decimal("3000")


@pytest.mark.django_db
class TestShippingFeeWithPoints:
    """포인트 사용시 배송비 계산"""

    def test_points_do_not_affect_free_shipping(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """포인트 사용해도 무료배송 판단은 상품 금액 기준"""
        # Arrange - 35,000원 상품 + 5,000포인트 사용
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("35000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True
        assert order.used_points == 5000
        assert order.final_amount == Decimal("30000")

    def test_points_with_shipping_fee(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """배송비 포함 금액에서 포인트 차감"""
        # Arrange - 20,000원 상품 + 배송비 3,000원 + 포인트 3,000원 사용
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 3000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("20000")
        assert order.shipping_fee == Decimal("3000")
        assert order.used_points == 3000
        assert order.final_amount == Decimal("20000")

    def test_points_exceed_total_with_shipping(
        self, authenticated_client, user_with_high_points, product, add_to_cart_helper, shipping_data
    ):
        """배송비 포함 총 금액보다 많은 포인트 사용 시도"""
        # Arrange - 20,000원 상품 + 배송비 3,000원 = 23,000원 / 30,000포인트 사용 시도
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user_with_high_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 30000}
        authenticated_client.force_authenticate(user=user_with_high_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "주문 금액보다 많은 포인트" in str(response.data)

    def test_points_with_remote_area_shipping(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, remote_shipping_data
    ):
        """도서산간 배송비 포함 금액에서 포인트 차감"""
        # Arrange - 20,000원 + 배송비 6,000원 = 26,000원 / 5,000포인트 사용
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**remote_shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("20000")
        assert order.shipping_fee == Decimal("3000")
        assert order.additional_shipping_fee == Decimal("3000")
        assert order.used_points == 5000
        assert order.final_amount == Decimal("21000")

    def test_points_exact_boundary_with_free_shipping(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """무료배송 경계값 + 포인트 사용"""
        # Arrange - 30,000원 상품 + 5,000포인트 사용
        product.price = Decimal("30000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30000")
        assert order.shipping_fee == Decimal("0")
        assert order.used_points == 5000
        assert order.final_amount == Decimal("25000")


@pytest.mark.django_db
class TestShippingFeeEdgeCases:
    """배송비 계산 예외 케이스"""

    def test_empty_cart_cannot_create_order(self, authenticated_client, user, shipping_data):
        """빈 장바구니로 주문 생성 시도"""
        # Arrange - 장바구니에 아무것도 없음
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "장바구니가 비어있습니다" in str(response.data)

    def test_multiple_products_with_mixed_prices(
        self, authenticated_client, user, product_factory, add_to_cart_helper, shipping_data
    ):
        """다양한 가격 조합으로 무료배송 경계 테스트"""
        # Arrange - 10,000원 + 15,000원 + 5,000원 = 30,000원
        product1 = product_factory(name="상품1", price=Decimal("10000"), sku="MIX-001")
        product2 = product_factory(name="상품2", price=Decimal("15000"), sku="MIX-002")
        product3 = product_factory(name="상품3", price=Decimal("5000"), sku="MIX-003")
        add_to_cart_helper(user, product1, quantity=1)
        add_to_cart_helper(user, product2, quantity=1)
        add_to_cart_helper(user, product3, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True

    def test_large_quantity_reaches_free_shipping(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """수량으로 무료배송 달성"""
        # Arrange - 10,000원 상품 3개 = 30,000원
        product.price = Decimal("10000")
        product.save()
        add_to_cart_helper(user, product, quantity=3)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("30000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True

    def test_remote_area_with_multiple_products(
        self, authenticated_client, user, product_factory, add_to_cart_helper, remote_shipping_data
    ):
        """도서산간 + 여러 상품 조합"""
        # Arrange - 20,000원 + 15,000원 = 35,000원 + 제주
        product1 = product_factory(name="상품1", price=Decimal("20000"), sku="REMOTE-001")
        product2 = product_factory(name="상품2", price=Decimal("15000"), sku="REMOTE-002")
        add_to_cart_helper(user, product1, quantity=1)
        add_to_cart_helper(user, product2, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert - 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("35000")
        assert order.shipping_fee == Decimal("0")
        assert order.additional_shipping_fee == Decimal("3000")
        assert order.is_free_shipping is True
        assert order.final_amount == Decimal("38000")
