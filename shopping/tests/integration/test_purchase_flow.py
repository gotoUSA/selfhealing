"""
사용자 통합 테스트

Pytest + Fixture 패턴 사용

회원가입 → 이메일 인증 → 상품 담기 → 주문 → 결제 플로우 테스트
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.models.product import Category, Product
from shopping.models.user import User


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def purchase_category(db):
    """구매 플로우 테스트용 카테고리"""
    return Category.objects.create(name="전자제품", slug="electronics")


@pytest.fixture
def purchase_product(db, purchase_category):
    """구매 플로우 테스트용 상품"""
    return Product.objects.create(
        category=purchase_category,
        name="테스트 노트북",
        price=Decimal("1000000"),
        stock=10,
        description="테스트용 노트북",
    )


@pytest.fixture
def purchase_urls():
    """구매 플로우 관련 URL들"""
    return {
        "register": reverse("auth-register"),
        "login": reverse("auth-login"),
        "cart": reverse("cart-detail"),
        "order_list": reverse("order-list"),
        "payment_request": reverse("payment-request"),
    }


# ==========================================
# 정상 플로우 테스트
# ==========================================


class TestCompletePurchaseFlow:
    """완전한 구매 플로우 테스트"""

    def test_complete_purchase_flow_with_verification(
        self, api_client, purchase_product, purchase_urls
    ):
        """
        ✅ 정상 플로우: 회원가입 → 이메일 인증 → 상품 담기 → 주문 → 결제
        """
        # Arrange
        register_data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "TestPass123!@",
            "password2": "TestPass123!@",
        }

        # Act & Assert - Step 1: 회원가입
        response = api_client.post(
            purchase_urls["register"], register_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert "token" in response.data
        assert "access" in response.data["token"]

        user = User.objects.get(username="testuser")
        assert user.is_email_verified is False  # 아직 미인증 상태

        # Act & Assert - Step 2: 이메일 인증
        EmailVerificationToken.objects.create(user=user)
        user.is_email_verified = True
        user.save()

        # Act & Assert - Step 3: 로그인
        login_data = {"username": "testuser", "password": "TestPass123!@"}
        response = api_client.post(purchase_urls["login"], login_data, format="json")
        assert response.status_code == status.HTTP_200_OK

        access_token = response.data["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # Act & Assert - Step 4: 장바구니에 상품 추가
        cart_data = {"product_id": purchase_product.id, "quantity": 1}
        response = api_client.post(
            f"{purchase_urls['cart']}add_item/", cart_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Act & Assert - Step 5: 주문 생성
        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }

        response = api_client.post(
            purchase_urls["order_list"], order_data, format="json"
        )
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(user=user)
        # Celery EAGER 모드에서 비동기 태스크가 즉시 완료되어 confirmed 상태가 됨
        assert order.status == "confirmed"
        assert order.total_amount == Decimal("1000000")

        # Act & Assert - Step 6: 결제 요청 (성공)
        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = api_client.post(
            purchase_urls["payment_request"], payment_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert "payment_id" in response.data

        # Assert - Payment 객체 생성 확인
        payment = Payment.objects.get(order=order)
        assert payment.status == "ready"
        assert payment.amount == order.final_amount

    def test_verified_user_full_flow(
        self, api_client, purchase_product, purchase_urls
    ):
        """
        ✅ 인증된 사용자는 전체 플로우 정상 작동
        """
        # Arrange - 인증된 사용자 생성
        user = User.objects.create_user(
            username="verified_user",
            email="verified@example.com",
            password="TestPass123!@",
            is_email_verified=True,  # 인증 완료
        )

        api_client.force_authenticate(user=user)

        # Act & Assert - Step 1: 장바구니 추가
        cart_data = {"product_id": purchase_product.id, "quantity": 1}
        response = api_client.post(
            f"{purchase_urls['cart']}add_item/", cart_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Act & Assert - Step 2: 주문 생성
        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }
        response = api_client.post(
            purchase_urls["order_list"], order_data, format="json"
        )
        assert response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.get(user=user)

        # Act & Assert - Step 3: 결제 요청
        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = api_client.post(
            purchase_urls["payment_request"], payment_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED


# ==========================================
# 미인증 사용자 테스트
# ==========================================


class TestUnverifiedUserRestrictions:
    """미인증 사용자 제한 테스트"""

    def test_unverified_user_cannot_create_order(
        self, api_client, purchase_product, purchase_urls
    ):
        """
        ❌ 미인증 사용자는 주문 생성 불가
        """
        # Arrange - 회원가입 (미인증 상태)
        register_data = {
            "username": "unverified",
            "email": "unverified@example.com",
            "password": "TestPass123!@",
            "password2": "TestPass123!@",
        }

        response = api_client.post(
            purchase_urls["register"], register_data, format="json"
        )
        access_token = response.data["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        user = User.objects.get(username="unverified")
        assert user.is_email_verified is False

        # Act & Assert - Step 1: 장바구니에 상품 추가 (허용)
        cart_data = {"product_id": purchase_product.id, "quantity": 1}
        response = api_client.post(
            f"{purchase_urls['cart']}add_item/", cart_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Act - Step 2: 주문 생성 시도
        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }

        response = api_client.post(
            purchase_urls["order_list"], order_data, format="json"
        )

        # Assert - 주문 생성 차단 확인
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증" in response.data["error"]
        assert response.data["verification_required"] is True
        assert Order.objects.filter(user=user).count() == 0

    def test_unverified_user_cannot_request_payment(
        self, db, api_client, purchase_urls
    ):
        """
        ❌ 미인증 사용자는 결제 요청 불가
        """
        # Arrange - 관리자로 강제로 주문 생성 (테스트용)
        user = User.objects.create_user(
            username="testuser2",
            email="test2@example.com",
            password="TestPass123!@",
            is_email_verified=False,  # 미인증
        )

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=Decimal("1000000"),
            final_amount=Decimal("1000000"),
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동 202호",
        )

        api_client.force_authenticate(user=user)

        # Act - 결제 요청 시도
        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = api_client.post(
            purchase_urls["payment_request"], payment_data, format="json"
        )

        # Assert - 결제 요청 차단 확인
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증" in response.data["error"]
        assert Payment.objects.filter(order=order).count() == 0
