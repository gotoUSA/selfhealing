from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.user import User

from .conftest import TEST_ADMIN_PASSWORD, TEST_USER_PASSWORD


@pytest.mark.django_db
class TestOrderAuthentication:
    """주문 API 인증 테스트 - 인증 여부 확인"""

    def test_authenticated_user_can_create_order(
        self, authenticated_client: pytest.fixture, user: User, product: pytest.fixture, shipping_data: dict
    ) -> None:
        """인증된 사용자는 주문 생성 가능"""
        # Arrange - 장바구니에 상품 추가
        cart, _ = Cart.get_or_create_active_cart(user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data
        assert response.data["status"] == "pending"

    def test_authenticated_user_can_view_order_list(
        self, authenticated_client: pytest.fixture, user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """인증된 사용자는 주문 목록 조회 가능"""
        # Arrange - 주문 생성
        order = order_factory(user, status="pending", total_amount=product.price)

        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "count" in response.data
        assert "results" in response.data
        assert response.data["count"] >= 1

    def test_authenticated_user_can_view_order_detail(
        self, authenticated_client: pytest.fixture, user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """인증된 사용자는 본인 주문 상세 조회 가능"""
        # Arrange
        order = order_factory(user, status="pending", total_amount=product.price)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == order.id
        assert response.data["user"] == user.id

    def test_unauthenticated_cannot_access_order_list(self, api_client: pytest.fixture) -> None:
        """인증되지 않은 사용자는 주문 목록 조회 불가"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_cannot_create_order(self, api_client: pytest.fixture, shipping_data: dict) -> None:
        """인증되지 않은 사용자는 주문 생성 불가"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = api_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_cannot_view_order_detail(
        self, api_client: pytest.fixture, user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """인증되지 않은 사용자는 주문 상세 조회 불가"""
        # Arrange
        order = order_factory(user, status="pending", total_amount=product.price)

        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestOrderEmailVerification:
    """주문 이메일 인증 테스트 - 이메일 인증 여부 확인"""

    def test_verified_user_can_create_order(
        self, authenticated_client: pytest.fixture, user: User, product: pytest.fixture, shipping_data: dict
    ) -> None:
        """이메일 인증된 사용자는 주문 생성 가능"""
        # Arrange
        assert user.is_email_verified is True

        cart, _ = Cart.get_or_create_active_cart(user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_unverified_user_cannot_create_order(
        self, unverified_user: User, product: pytest.fixture, shipping_data: dict, login_helper
    ) -> None:
        """이메일 미인증 사용자는 주문 생성 불가"""
        # Arrange - 이메일 미인증 사용자 생성 및 로그인
        client, _ = login_helper(unverified_user)

        cart, _ = Cart.get_or_create_active_cart(unverified_user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        url = reverse("order-list")

        # Act
        response = client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증" in str(response.data)

    def test_unverified_user_gets_proper_error_message(
        self, api_client: pytest.fixture, unverified_user: User, product: pytest.fixture, shipping_data: dict
    ) -> None:
        """이메일 미인증 사용자는 명확한 에러 메시지 받음"""
        # Arrange
        # unverified_user2 is needed for the test to work correctly with a different username
        unverified = User.objects.create_user(
            username="unverified2",
            email="unverified2@example.com",
            password=TEST_USER_PASSWORD,
            is_email_verified=False,
        )

        login_response = api_client.post(reverse("auth-login"), {"username": "unverified2", "password": TEST_USER_PASSWORD})
        token = login_response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        cart, _ = Cart.get_or_create_active_cart(unverified)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        url = reverse("order-list")

        # Act
        response = api_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        error_message = str(response.data).lower()
        assert "이메일" in error_message and "인증" in error_message


@pytest.mark.django_db
class TestOrderOwnership:
    """주문 본인 확인 테스트 - 본인 주문만 접근 가능"""

    def test_user_can_view_own_orders_only(
        self,
        authenticated_client: pytest.fixture,
        user: User,
        seller_user: User,
        product: pytest.fixture,
        order_factory: pytest.fixture,
    ) -> None:
        """사용자는 본인 주문만 조회"""
        # Arrange - 본인 주문 1개, 다른 사용자 주문 2개 생성
        own_order = order_factory(
            user, status="pending", total_amount=product.price, shipping_name="본인", shipping_phone="010-1111-1111"
        )

        order_factory(
            seller_user,
            status="pending",
            total_amount=product.price,
            shipping_name="타인1",
            shipping_phone="010-2222-2222",
            shipping_address_detail="202호",
        )

        order_factory(
            seller_user,
            status="pending",
            total_amount=product.price,
            shipping_name="타인2",
            shipping_phone="010-3333-3333",
            shipping_address_detail="303호",
        )

        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == own_order.id
        assert response.data["results"][0]["user_username"] == user.username

    def test_user_can_view_own_order_detail(
        self, authenticated_client: pytest.fixture, user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """사용자는 본인 주문 상세 조회 가능"""
        # Arrange
        order = order_factory(user, status="pending", total_amount=product.price)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        url = reverse("order-detail", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == order.id
        assert response.data["shipping_name"] == "홍길동"

    def test_empty_order_list_returns_zero_count(self, authenticated_client: pytest.fixture) -> None:
        """주문이 없는 경우 빈 목록 반환"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0
        assert response.data["results"] == []

    def test_user_cannot_view_others_order_detail(
        self, authenticated_client: pytest.fixture, seller_user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """사용자는 다른 사용자 주문 상세 조회 불가"""
        # Arrange - 다른 사용자의 주문 생성
        other_order = order_factory(
            seller_user,
            status="pending",
            total_amount=product.price,
            shipping_name="다른사람",
            shipping_phone="010-9999-9999",
            shipping_address_detail="999호",
        )

        url = reverse("order-detail", kwargs={"pk": other_order.id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_user_gets_404_for_nonexistent_order(self, authenticated_client: pytest.fixture) -> None:
        """존재하지 않는 주문 조회 시 404"""
        # Arrange
        nonexistent_id = 99999
        url = reverse("order-detail", kwargs={"pk": nonexistent_id})

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestAdminOrderPermissions:
    """관리자 주문 권한 테스트 - 관리자는 모든 주문 접근 가능"""

    def test_admin_can_view_all_orders(
        self,
        user: User,
        seller_user: User,
        product: pytest.fixture,
        admin_user: User,
        order_factory: pytest.fixture,
        login_helper,
    ) -> None:
        """관리자는 모든 주문 조회 가능"""
        # Arrange - 관리자 생성
        # 여러 사용자의 주문 생성
        order_factory(
            user, status="pending", total_amount=Decimal("10000"), shipping_name="일반사용자", shipping_phone="010-1111-1111"
        )

        order_factory(
            seller_user,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="판매자",
            shipping_phone="010-2222-2222",
            shipping_address_detail="202호",
        )

        order_factory(
            admin_user,
            status="pending",
            total_amount=Decimal("30000"),
            shipping_name="관리자",
            shipping_phone="010-3333-3333",
            shipping_address_detail="303호",
        )

        # 관리자 로그인
        client, _ = login_helper(admin_user)
        url = reverse("order-list")

        # Act
        response = client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 3

    def test_admin_can_view_any_order_detail(
        self, api_client: pytest.fixture, user: User, product: pytest.fixture, order_factory: pytest.fixture
    ) -> None:
        """관리자는 다른 사용자 주문 상세 조회 가능"""
        # Arrange - 관리자 생성
        admin = User.objects.create_user(
            username="admin2",
            email="admin2@example.com",
            password=TEST_ADMIN_PASSWORD,
            is_staff=True,
            is_superuser=True,
            is_email_verified=True,
        )

        # 일반 사용자 주문 생성
        user_order = order_factory(user, status="pending", total_amount=product.price, shipping_name="일반사용자")

        # 관리자 로그인
        login_response = api_client.post(reverse("auth-login"), {"username": "admin2", "password": TEST_ADMIN_PASSWORD})
        token = login_response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        url = reverse("order-detail", kwargs={"pk": user_order.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == user_order.id
        assert response.data["shipping_name"] == "일반사용자"

    def test_admin_sees_correct_order_count(
        self, api_client: pytest.fixture, user: User, seller_user: User, order_factory: pytest.fixture
    ) -> None:
        """관리자는 정확한 주문 개수를 확인"""
        # Arrange - 관리자 생성
        admin = User.objects.create_user(
            username="admin3",
            email="admin3@example.com",
            password=TEST_ADMIN_PASSWORD,
            is_staff=True,
            is_superuser=True,
            is_email_verified=True,
        )

        # 주문 5개 생성
        for i in range(3):
            order_factory(
                user, status="pending", total_amount=Decimal("10000"), shipping_name=f"주문{i}", shipping_phone="010-0000-0000"
            )

        for i in range(2):
            order_factory(
                seller_user,
                status="pending",
                total_amount=Decimal("20000"),
                shipping_name=f"주문{i+3}",
                shipping_phone="010-0000-0000",
                shipping_address_detail="202호",
            )

        # 관리자 로그인
        login_response = api_client.post(reverse("auth-login"), {"username": "admin3", "password": TEST_ADMIN_PASSWORD})
        token = login_response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        url = reverse("order-list")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 5

    def test_admin_sees_orders_from_multiple_users(
        self, api_client: pytest.fixture, user: User, seller_user: User, order_factory: pytest.fixture
    ) -> None:
        """관리자는 여러 사용자의 주문을 모두 볼 수 있음"""
        # Arrange - 관리자 생성
        admin = User.objects.create_user(
            username="admin4",
            email="admin4@example.com",
            password=TEST_ADMIN_PASSWORD,
            is_staff=True,
            is_superuser=True,
            is_email_verified=True,
        )

        # 서로 다른 사용자의 주문 생성
        user_order = order_factory(
            user, status="pending", total_amount=Decimal("10000"), shipping_name="일반사용자", shipping_phone="010-1111-1111"
        )

        seller_order = order_factory(
            seller_user,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="판매자",
            shipping_phone="010-2222-2222",
            shipping_address_detail="202호",
        )

        # 관리자 로그인
        login_response = api_client.post(reverse("auth-login"), {"username": "admin4", "password": TEST_ADMIN_PASSWORD})
        token = login_response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        url = reverse("order-list")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        order_ids = [order["id"] for order in response.data["results"]]
        assert user_order.id in order_ids
        assert seller_order.id in order_ids
