from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.order import OrderItem
from shopping.models.user import User

from .conftest import TEST_USER_PASSWORD


@pytest.mark.django_db
class TestOrderDetailHappyPath:
    """주문 상세 조회 성공 시나리오"""

    def test_retrieve_own_order_success(self, authenticated_client, user, pending_order):
        """본인의 주문 상세 조회 성공"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": pending_order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == pending_order.id
        assert response.data["user"] == user.id
        assert response.data["user_username"] == user.username
        assert response.data["status"] == "pending"
        assert "order_items" in response.data
        assert "total_amount" in response.data
        assert "created_at" in response.data

    def test_order_items_structure(self, authenticated_client, user, order_with_multiple_items):
        """주문 아이템 구조 검증 - 여러 상품 포함"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": order_with_multiple_items.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["order_items"]) == 3

        # 첫 번째 아이템 구조 검증
        first_item = response.data["order_items"][0]
        assert "id" in first_item
        assert "product" in first_item
        assert "product_info" in first_item
        assert "product_name" in first_item
        assert "quantity" in first_item
        assert "price" in first_item
        assert "subtotal" in first_item

        # product_info 중첩 객체 검증
        product_info = first_item["product_info"]
        assert "id" in product_info
        assert "name" in product_info
        assert "price" in product_info

    def test_amount_calculation(self, authenticated_client, user, product, order_factory):
        """금액 계산 정확성 검증"""
        # Arrange - 포인트 사용한 주문 생성
        order = order_factory(
            user,
            status="pending",
            total_amount=Decimal("50000"),
            used_points=5000,
            final_amount=Decimal("45000"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=5,
            price=Decimal("10000"),
        )

        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data["total_amount"]) == Decimal("50000")
        assert response.data["used_points"] == 5000
        assert Decimal(response.data["final_amount"]) == Decimal("45000")

        # 아이템 subtotal 검증
        item = response.data["order_items"][0]
        assert Decimal(item["subtotal"]) == Decimal("50000")  # 10000 * 5

    def test_shipping_info_included(self, authenticated_client, user, order):
        """배송 정보 포함 확인"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["shipping_name"] == order.shipping_name
        assert response.data["shipping_phone"] == order.shipping_phone
        assert response.data["shipping_postal_code"] == order.shipping_postal_code
        assert response.data["shipping_address"] == order.shipping_address
        assert response.data["shipping_address_detail"] == order.shipping_address_detail

    def test_status_display_included(self, authenticated_client, user, pending_order):
        """주문 상태 display 값 포함 확인"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": pending_order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "status_display" in response.data
        assert response.data["status"] == "pending"
        assert response.data["status_display"] == "결제대기"

    def test_can_cancel_flag(self, authenticated_client, user, pending_order):
        """주문 취소 가능 여부 플래그 확인"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": pending_order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "can_cancel" in response.data
        assert isinstance(response.data["can_cancel"], bool)


@pytest.mark.django_db
class TestOrderDetailBoundary:
    """특수한 상황의 주문 조회"""

    def test_order_with_no_items(self, authenticated_client, user, order_factory):
        """주문 아이템이 0개인 경우"""
        # Arrange - 아이템 없는 주문 생성
        order = order_factory(user, status="pending", total_amount=Decimal("0"))
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["order_items"] == []
        assert Decimal(response.data["total_amount"]) == Decimal("0")

    def test_order_with_many_items(self, authenticated_client, user, product, order_factory):
        """주문 아이템이 많은 경우 (100개)"""
        # Arrange
        order = order_factory(user, status="pending", total_amount=Decimal("1000000"))

        # 100개 아이템 생성
        for i in range(100):
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=f"{product.name} {i}",
                quantity=1,
                price=Decimal("10000"),
            )

        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["order_items"]) == 100

    def test_all_order_statuses(self, authenticated_client, user, product, order_factory, order_statuses):
        """모든 주문 상태별 조회 테스트"""
        # Arrange
        for status_code in order_statuses["all"]:
            order = order_factory(user, status=status_code, total_amount=Decimal("10000"))
            status_display = order_statuses["display"][status_code]
            url = reverse("order-detail", kwargs={"pk": order.id})

            # Act
            response = authenticated_client.get(url)

            # Assert
            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == status_code
            assert response.data["status_display"] == status_display

    def test_order_with_max_points(self, authenticated_client, user, product, order_factory):
        """포인트 최대 사용한 주문"""
        # Arrange
        order = order_factory(
            user,
            status="pending",
            total_amount=Decimal("50000"),
            used_points=50000,  # 전액 포인트 사용
            final_amount=Decimal("0"),
        )
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["used_points"] == 50000
        assert Decimal(response.data["final_amount"]) == Decimal("0")

    def test_order_with_no_points(self, authenticated_client, user, product, order_factory):
        """포인트 사용하지 않은 주문"""
        # Arrange
        order = order_factory(
            user,
            status="pending",
            total_amount=Decimal("50000"),
            used_points=0,
            final_amount=Decimal("50000"),
        )
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["used_points"] == 0
        assert Decimal(response.data["final_amount"]) == Decimal("50000")

    def test_order_with_shipping_fee(self, authenticated_client, user, product, order_factory):
        """배송비가 있는 주문"""
        # Arrange
        order = order_factory(
            user,
            status="pending",
            total_amount=Decimal("50000"),
            shipping_fee=Decimal("3000"),
            final_amount=Decimal("53000"),
        )
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data["shipping_fee"]) == Decimal("3000")

    def test_order_id_boundary_values(self, authenticated_client, user, product, order_factory):
        """주문 ID 경계값 테스트"""
        # Arrange - ID=1 주문 생성
        order = order_factory(user, status="pending", total_amount=Decimal("10000"))

        # 생성된 ID로 조회
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == order.id


@pytest.mark.django_db
class TestOrderDetailException:
    """에러 상황 처리"""

    def test_unauthenticated_access(self, api_client, order):
        """미인증 사용자 조회 시도 (401)"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_existent_order(self, authenticated_client, user):
        """존재하지 않는 주문 조회 (404)"""
        # Arrange
        non_existent_id = 99999
        url = reverse("order-detail", kwargs={"pk": non_existent_id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_other_user_order(self, order, other_user, login_helper):
        """다른 사용자의 주문 조회 시도 (403)"""
        # Arrange
        client, _ = login_helper(other_user)
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = client.get(url)

        # Assert - 권한 없으면 404 반환
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_admin_access_all_orders(self, order, admin_user, login_helper):
        """관리자는 모든 주문 조회 가능 (200)"""
        # Arrange
        client, _ = login_helper(admin_user)
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == order.id

    def test_invalid_order_id_format(self, authenticated_client, user):
        """잘못된 주문 ID 형식 (문자열)"""
        # Arrange
        url = "/api/orders/invalid/"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_negative_order_id(self, authenticated_client, user):
        """음수 주문 ID"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": -1})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_zero_order_id(self, authenticated_client, user):
        """0 주문 ID"""
        # Arrange
        url = reverse("order-detail", kwargs={"pk": 0})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_expired_token(self, api_client, order):
        """만료된 토큰으로 조회 시도"""
        # Arrange - 잘못된 토큰 사용
        api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid_token_123")
        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_deleted_user_order(self, api_client, product, order_factory):
        """탈퇴한 사용자의 주문 조회"""
        # Arrange - 사용자 생성 및 주문 생성
        user = User.objects.create_user(
            username="deleteduser",
            email="deleted@example.com",
            password=TEST_USER_PASSWORD,
            is_email_verified=True,
        )

        order = order_factory(user, status="pending", total_amount=Decimal("10000"))

        # 사용자 비활성화
        user.is_active = False
        user.save()

        # 로그인 시도 (실패 예상)
        login_response = api_client.post(
            reverse("auth-login"),
            {"username": "deleteduser", "password": TEST_USER_PASSWORD},
        )

        # Assert - 로그인 실패로 주문 조회 불가
        assert login_response.status_code == status.HTTP_400_BAD_REQUEST
