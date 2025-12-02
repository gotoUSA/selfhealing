from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.order import Order
from shopping.tests.conftest import (
    DEFAULT_PRODUCT_PRICE,
    DEFAULT_PRODUCT_STOCK,
    DEFAULT_SHIPPING_FEE,
    DEFAULT_USER_POINTS,
    FREE_SHIPPING_THRESHOLD,
    MIN_POINTS,
    REMOTE_AREA_FEE,
)


@pytest.mark.django_db
class TestOrderValidationHappyPath:
    """정상적인 주문 생성 시나리오"""

    def test_create_order_with_single_product(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """단일 상품 주문 생성"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert: 비동기 응답 (HTTP 202)
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data
        assert "task_id" in response.data
        assert "status" in response.data
        assert response.data["status"] == "pending"

    def test_create_order_with_multiple_products(
        self, authenticated_client, user, multiple_products, shipping_data, add_to_cart_helper
    ):
        """여러 상품 주문 생성"""
        # Arrange
        for product in multiple_products:
            add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data
        assert "task_id" in response.data

    def test_create_order_with_points(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """포인트 사용 주문 (부분 결제)"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        use_points = 2000
        order_data = {**shipping_data, "use_points": use_points}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data
        # Order는 생성됨 (포인트 차감은 비동기)
        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == use_points

    def test_create_order_with_full_points_payment(
        self, authenticate_as, user_with_high_points, product, add_to_cart_helper, shipping_data
    ):
        """전액 포인트 결제"""
        # Arrange
        add_to_cart_helper(user_with_high_points, product, quantity=1)

        # 로그인
        authenticated_client = authenticate_as(user_with_high_points)

        url = reverse("order-list")
        total_payment = DEFAULT_PRODUCT_PRICE + DEFAULT_SHIPPING_FEE
        order_data = {
            **shipping_data,
            "use_points": total_payment,
        }

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == total_payment
        assert order.final_amount == Decimal("0")

    def test_create_order_with_multiple_quantities(
        self, authenticated_client, user, product, shipping_data, add_to_cart_helper
    ):
        """동일 상품 여러 개 주문"""
        # Arrange
        add_to_cart_helper(user, product, quantity=3)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == product.price * 3


@pytest.mark.django_db
class TestOrderValidationBoundary:
    """경계값 테스트"""

    def test_minimum_points_usage(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """최소 포인트 사용"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        order_data = {**shipping_data, "use_points": MIN_POINTS}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == MIN_POINTS

    def test_maximum_points_usage(self, authenticate_as, user_with_high_points, product, add_to_cart_helper, shipping_data):
        """최대 포인트 사용 (주문 금액 전액)"""
        # Arrange
        add_to_cart_helper(user_with_high_points, product, quantity=1)

        # 로그인
        authenticated_client = authenticate_as(user_with_high_points)

        url = reverse("order-list")
        total_payment = DEFAULT_PRODUCT_PRICE + DEFAULT_SHIPPING_FEE
        order_data = {
            **shipping_data,
            "use_points": total_payment,
        }

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == total_payment
        assert order.final_amount == Decimal("0")

    def test_free_shipping_threshold(self, authenticated_client, user, product_factory, shipping_data, add_to_cart_helper):
        """무료 배송 기준 금액"""
        # Arrange
        expensive_product = product_factory(price=FREE_SHIPPING_THRESHOLD)
        add_to_cart_helper(user, expensive_product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True

    def test_just_below_free_shipping(self, authenticated_client, user, product_factory, shipping_data, add_to_cart_helper):
        """무료 배송 기준 직전"""
        # Arrange
        product = product_factory(price=FREE_SHIPPING_THRESHOLD - 1)
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.shipping_fee == DEFAULT_SHIPPING_FEE
        assert order.is_free_shipping is False

    def test_remote_area_shipping(self, authenticated_client, user, product, remote_shipping_data, add_to_cart_helper):
        """도서산간 지역 배송 (제주)"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.additional_shipping_fee == REMOTE_AREA_FEE

    def test_remote_area_with_free_shipping(
        self, authenticated_client, user, product_factory, remote_shipping_data, add_to_cart_helper
    ):
        """도서산간 + 무료배송"""
        # Arrange
        expensive_product = product_factory(price=FREE_SHIPPING_THRESHOLD + 5000)
        add_to_cart_helper(user, expensive_product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.shipping_fee == Decimal("0")  # 무료배송
        assert order.additional_shipping_fee == REMOTE_AREA_FEE  # 도서산간비는 별도
        assert order.is_free_shipping is True

    def test_zero_points_usage(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """포인트 미사용 (0포인트)"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        order_data = {**shipping_data, "use_points": 0}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert: 비동기 응답
        assert response.status_code == status.HTTP_202_ACCEPTED
        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == 0


@pytest.mark.django_db
class TestOrderValidationException:
    """예외 상황 테스트"""

    def test_unverified_email_user(self, authenticate_as, unverified_user, product, shipping_data, add_to_cart_helper):
        """이메일 미인증 사용자 주문 시도"""
        # Arrange
        add_to_cart_helper(unverified_user, product, quantity=1)

        # 미인증 사용자로 로그인
        api_client = authenticate_as(unverified_user)

        url = reverse("order-list")

        # Act
        response = api_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증" in str(response.data)

    def test_empty_cart(self, authenticated_client, user, shipping_data):
        """빈 장바구니로 주문 시도"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "장바구니가 비어있습니다" in str(response.data)

    def test_inactive_product_in_cart(self, authenticated_client, user, inactive_product, shipping_data, add_to_cart_helper):
        """비활성 상품 주문 시도"""
        # Arrange
        add_to_cart_helper(user, inactive_product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "판매하지 않는 상품" in str(response.data)

    def test_out_of_stock_product(self, authenticated_client, user, out_of_stock_product, shipping_data, add_to_cart_helper):
        """품절 상품 주문 시도"""
        # Arrange
        add_to_cart_helper(user, out_of_stock_product, quantity=1)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "품절" in str(response.data)

    def test_insufficient_stock(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """재고 부족 상품 주문 시도"""
        # Arrange - 재고보다 많이 주문
        add_to_cart_helper(user, product, quantity=DEFAULT_PRODUCT_STOCK + 5)
        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "재고가 부족" in str(response.data)

    def test_insufficient_points(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """보유 포인트 초과 사용 시도"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        order_data = {**shipping_data, "use_points": DEFAULT_USER_POINTS * 2}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "보유 포인트가 부족" in str(response.data)

    def test_points_below_minimum(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """최소 포인트 미달"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        order_data = {**shipping_data, "use_points": MIN_POINTS - 50}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"최소 {MIN_POINTS}포인트" in str(response.data)

    def test_points_exceed_order_amount(self, authenticate_as, user_with_high_points, product, add_to_cart_helper, shipping_data):
        """주문 금액 초과 포인트 사용 시도"""
        # Arrange
        add_to_cart_helper(user_with_high_points, product, quantity=1)

        # 로그인
        authenticated_client = authenticate_as(user_with_high_points)

        url = reverse("order-list")
        total_payment = DEFAULT_PRODUCT_PRICE + DEFAULT_SHIPPING_FEE
        order_data = {
            **shipping_data,
            "use_points": total_payment + 2000,  # 주문 금액 초과
        }

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "주문 금액보다 많은 포인트" in str(response.data)

    def test_missing_shipping_name(self, authenticated_client, user, product, add_to_cart_helper, invalid_shipping_field_factory):
        """배송자 이름 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        invalid_data = invalid_shipping_field_factory("shipping_name")

        # Act
        response = authenticated_client.post(url, invalid_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_name" in response.data

    def test_missing_shipping_phone(self, authenticated_client, user, product, add_to_cart_helper, invalid_shipping_field_factory):
        """배송자 연락처 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        invalid_data = invalid_shipping_field_factory("shipping_phone")

        # Act
        response = authenticated_client.post(url, invalid_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_phone" in response.data

    def test_missing_shipping_postal_code(self, authenticated_client, user, product, add_to_cart_helper, invalid_shipping_field_factory):
        """우편번호 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        invalid_data = invalid_shipping_field_factory("shipping_postal_code")

        # Act
        response = authenticated_client.post(url, invalid_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_postal_code" in response.data

    def test_missing_shipping_address(self, authenticated_client, user, product, add_to_cart_helper, invalid_shipping_field_factory):
        """배송 주소 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        invalid_data = invalid_shipping_field_factory("shipping_address")

        # Act
        response = authenticated_client.post(url, invalid_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_address" in response.data


    def test_all_fields_missing(self, authenticated_client, user, product, add_to_cart_helper):

        """모든 배송지 정보 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        invalid_data = {}

        # Act
        response = authenticated_client.post(url, invalid_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_name" in response.data
        assert "shipping_phone" in response.data
        assert "shipping_postal_code" in response.data
        assert "shipping_address" in response.data

    def test_negative_points_usage(self, authenticated_client, user, product, shipping_data, add_to_cart_helper):
        """음수 포인트 사용 시도"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = reverse("order-list")
        order_data = {**shipping_data, "use_points": -1000}

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unverified_email_user_blocks_order_before_other_validations(
        self, authenticate_as, unverified_user, out_of_stock_product, add_to_cart_helper, shipping_data
    ):
        """복합 검증 오류 (미인증 + 품절 상품)"""
        # Arrange
        add_to_cart_helper(unverified_user, out_of_stock_product, quantity=1)

        # 미인증 사용자로 로그인
        api_client = authenticate_as(unverified_user)

        url = reverse("order-list")

        # Act
        response = api_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        # 첫 번째 검증(이메일 인증)에서 걸림
        assert "이메일 인증" in str(response.data)
