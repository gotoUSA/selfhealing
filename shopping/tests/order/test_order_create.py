from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.cart import Cart
from shopping.models.order import Order
from shopping.models.point import PointHistory


@pytest.mark.django_db
class TestOrderCreateHappyPath:
    """주문 생성 정상 케이스"""

    def test_create_order_single_product(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """단일 상품 주문 생성"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order is not None
        assert order.user == user
        assert order.status == "confirmed"
        assert order.total_amount == product.price
        assert order.used_points == 0
        assert order.order_items.count() == 1

    def test_create_order_multiple_products(
        self, authenticated_client, user, multiple_products, add_to_cart_helper, shipping_data
    ):
        """여러 상품 주문 생성"""
        # Arrange
        for product in multiple_products:
            add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.order_items.count() == 3
        assert order.total_amount == sum(p.price for p in multiple_products)

    def test_create_order_with_quantity(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """수량 여러개 주문 생성"""
        # Arrange
        add_to_cart_helper(user, product, quantity=3)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        order_item = order.order_items.first()
        assert order_item.quantity == 3
        assert order.total_amount == product.price * 3

    def test_create_order_with_points_partial(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """포인트 일부 사용 주문"""
        # Arrange
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 2000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == 2000

        user_with_points.refresh_from_db()
        assert user_with_points.points == 3000

        point_history = PointHistory.objects.filter(user=user_with_points, type="use", order=order).first()
        assert point_history is not None
        assert point_history.points == -2000

    def test_create_order_with_points_full(
        self, authenticated_client, user_with_high_points, product, add_to_cart_helper, shipping_data
    ):
        """포인트 전액 사용 주문"""
        # Arrange
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user_with_high_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 23000}
        authenticated_client.force_authenticate(user=user_with_high_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == 23000
        assert order.final_amount == Decimal("0")

        user_with_high_points.refresh_from_db()
        assert user_with_high_points.points == 27000

    def test_order_number_generation(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """주문 번호 자동 생성 확인 (YYYYMMDD + 6자리)"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.order_number is not None
        assert len(order.order_number) == 14
        assert order.order_number.startswith(timezone.now().strftime("%Y%m%d"))

    def test_order_with_shipping_fee_applied(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """배송비 자동 적용 확인 (3만원 미만 = 3000원)"""
        # Arrange
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("20000")
        assert order.shipping_fee == Decimal("3000")
        assert order.final_amount == Decimal("23000")

    def test_order_with_free_shipping(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """무료배송 적용 확인 (3만원 이상)"""
        # Arrange
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("35000")
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True
        assert order.final_amount == Decimal("35000")

    def test_order_with_remote_area_shipping_fee(
        self, authenticated_client, user, product, add_to_cart_helper, remote_shipping_data
    ):
        """도서산간 추가 배송비 적용 확인 (제주/도서산간 = +3000원)"""
        # Arrange
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, remote_shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.is_free_shipping is True
        assert order.shipping_fee == Decimal("0")
        assert order.additional_shipping_fee == Decimal("3000")
        assert order.final_amount == Decimal("38000")

    def test_cart_cleared_after_order(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """주문 생성 후 장바구니 비활성화 확인"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        cart = Cart.objects.get(user=user, is_active=True)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        # 주문 후 장바구니 상태 확인
        # 구현에 따라 비활성화 또는 아이템 삭제될 수 있음
        cart.refresh_from_db()
        # 비활성화되거나 아이템이 비었어야 함
        if cart.is_active:
            assert cart.items.count() == 0

    def test_order_items_snapshot_saved(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """OrderItem에 상품 정보 스냅샷 저장 확인"""
        # Arrange
        original_price = product.price
        original_name = product.name
        add_to_cart_helper(user, product, quantity=2)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        order_item = order.order_items.first()
        assert order_item.product_name == original_name
        assert order_item.price == original_price
        assert order_item.quantity == 2


@pytest.mark.django_db
class TestOrderCreateBoundary:
    """주문 생성 경계값 테스트"""

    def test_create_order_with_zero_points(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """포인트 0개 사용 주문"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        order_data = {**shipping_data, "use_points": 0}
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == 0

    def test_create_order_use_all_points(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """보유 포인트 전부 사용"""
        # Arrange
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        user_with_points.refresh_from_db()
        assert user_with_points.points == 0

    def test_create_order_last_stock_item(
        self, authenticated_client, user, low_stock_product, add_to_cart_helper, shipping_data
    ):
        """재고 마지막 1개 주문"""
        # Arrange
        add_to_cart_helper(user, low_stock_product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.order_items.count() == 1

    def test_create_order_minimum_amount(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """최소 주문 금액 주문 (1원)"""
        # Arrange
        product.price = Decimal("1")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.total_amount == Decimal("1")

    def test_create_order_exact_free_shipping_threshold(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """정확히 무료배송 기준 금액 (30,000원)"""
        # Arrange
        product.price = Decimal("30000")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.is_free_shipping is True
        assert order.shipping_fee == Decimal("0")

    def test_create_order_one_won_below_free_shipping(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """무료배송 기준 1원 미만 (29,999원)"""
        # Arrange
        product.price = Decimal("29999")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.is_free_shipping is False
        assert order.shipping_fee == Decimal("3000")

    def test_create_order_with_maximum_quantity(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """최대 수량 주문 (재고 전부)"""
        # Arrange
        product.stock = 10
        product.save()
        add_to_cart_helper(user, product, quantity=10)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        order_item = order.order_items.first()
        assert order_item.quantity == 10

    def test_create_order_points_equal_to_total(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """포인트가 주문금액과 정확히 동일"""
        # Arrange
        product.price = Decimal("5000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(id=response.data["order_id"])
        assert order.used_points == 5000


@pytest.mark.django_db
class TestOrderCreateException:
    """주문 생성 예외 케이스"""

    def test_create_order_without_authentication(self, api_client, shipping_data):
        """인증 없이 주문 시도"""
        # Arrange
        url = "/api/orders/"

        # Act
        response = api_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    def test_create_order_with_unverified_email(
        self, authenticated_client, unverified_user, product, add_to_cart_helper, shipping_data
    ):
        """이메일 미인증 사용자 주문 시도"""
        # Arrange
        add_to_cart_helper(unverified_user, product, quantity=1)
        authenticated_client.force_authenticate(user=unverified_user)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증" in str(response.data)

    def test_create_order_with_empty_cart(self, authenticated_client, user, shipping_data):
        """빈 장바구니로 주문 시도"""
        # Arrange
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_out_of_stock_product(
        self, authenticated_client, user, out_of_stock_product, add_to_cart_helper, shipping_data
    ):
        """품절 상품 주문 시도 (장바구니에는 담김)"""
        # Arrange
        add_to_cart_helper(user, out_of_stock_product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_exceeds_stock(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """재고 초과 수량 주문 시도"""
        # Arrange
        product.stock = 5
        product.save()
        add_to_cart_helper(user, product, quantity=10)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비동기 처리로 인해 202 응답
        assert response.status_code == status.HTTP_202_ACCEPTED

        # 비동기 태스크에서 재고 부족으로 실패 확인
        order = Order.objects.get(id=response.data["order_id"])
        assert order.status == "failed"

    def test_create_order_exceeds_points(
        self, authenticated_client, user_with_points, product, add_to_cart_helper, shipping_data
    ):
        """보유 포인트 초과 사용 시도"""
        # Arrange
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 10000}
        authenticated_client.force_authenticate(user=user_with_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_negative_points(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """음수 포인트 사용 시도"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        order_data = {**shipping_data, "use_points": -1000}
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_inactive_product(
        self, authenticated_client, user, inactive_product, add_to_cart_helper, shipping_data
    ):
        """비활성화된 상품 주문 시도 (주문 가능한지 확인)"""
        # Arrange
        add_to_cart_helper(user, inactive_product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert - 비활성 상품은 주문할 수 없어야함
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # 에러 메세지 확인 (cart_items 키에 에러 리스트가 있어야 함)
        assert "cart_items" in response.data
        assert any("판매하지 않는 상품" in str(error) for error in response.data["cart_items"])

    def test_create_order_with_invalid_shipping_info(
        self, authenticated_client, user, product, add_to_cart_helper, invalid_shipping_data
    ):
        """잘못된 배송지 정보로 주문 시도"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, invalid_shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_missing_shipping_name(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """배송지 이름 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        incomplete_data = {**shipping_data}
        del incomplete_data["shipping_name"]
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, incomplete_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_missing_shipping_address(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """배송지 주소 누락"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        incomplete_data = {**shipping_data}
        del incomplete_data["shipping_address"]
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, incomplete_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_points_more_than_order_total(
        self, authenticated_client, user_with_high_points, product, add_to_cart_helper, shipping_data
    ):
        """주문 금액보다 많은 포인트 사용 시도"""
        # Arrange
        product.price = Decimal("5000")
        product.save()
        add_to_cart_helper(user_with_high_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 10000}
        authenticated_client.force_authenticate(user=user_with_high_points)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, order_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_with_zero_price_product(
        self, authenticated_client, user, product, add_to_cart_helper, shipping_data
    ):
        """가격이 0원인 상품 주문 시도"""
        # Arrange
        product.price = Decimal("0")
        product.save()
        add_to_cart_helper(user, product, quantity=1)
        url = "/api/orders/"

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        # 0원 상품 허용 여부는 비즈니스 로직에 따라 다름
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_202_ACCEPTED]
