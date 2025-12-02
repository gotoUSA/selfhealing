from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.models.product import Category, Product
from shopping.models.user import User


class UserIntegrationTest(TestCase):
    """사용자 통합 테스트"""

    def setUp(self):
        """테스트 데이터 준비"""
        self.client = APIClient()

        # 카테고리 생성
        self.category = Category.objects.create(name="전자제품", slug="electronics")

        # 상품 생성
        self.product = Product.objects.create(
            category=self.category,
            name="테스트 노트북",
            price=Decimal("1000000"),
            stock=10,
            description="테스트용 노트북",
        )

        # URL
        self.register_url = reverse("auth-register")
        self.login_url = reverse("auth-login")
        self.cart_url = reverse("cart-detail")
        self.order_list_url = reverse("order-list")
        self.payment_request_url = reverse("payment-request")

    def test_complete_purchase_flow_with_verification(self):
        """
        ✅ 정상 플로우: 회원가입 → 이메일 인증 → 상품 담기 → 주문 → 결제
        """
        # 1. 회원가입
        register_data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "TestPass123!@",
            "password2": "TestPass123!@",
        }

        response = self.client.post(self.register_url, register_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("token", response.data)
        self.assertIn("access", response.data["token"])

        user = User.objects.get(username="testuser")
        self.assertFalse(user.is_email_verified)  # 아직 미인증 상태

        # 2. 이메일 인증
        EmailVerificationToken.objects.create(user=user)
        user.is_email_verified = True
        user.save()

        # 3. 로그인
        login_data = {"username": "testuser", "password": "TestPass123!@"}
        response = self.client.post(self.login_url, login_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        access_token = response.data["token"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # 4. 장바구니에 상품 추가
        cart_data = {"product_id": self.product.id, "quantity": 1}
        response = self.client.post(f"{self.cart_url}add_item/", cart_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 5. 주문 생성
        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }

        response = self.client.post(self.order_list_url, order_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        order = Order.objects.get(user=user)
        # Celery EAGER 모드에서 비동기 태스크가 즉시 완료되어 confirmed 상태가 됨
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.total_amount, Decimal("1000000"))

        # 6. 결제 요청 (성공)
        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = self.client.post(self.payment_request_url, payment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("payment_id", response.data)

        # Payment 객체 생성 확인
        payment = Payment.objects.get(order=order)
        self.assertEqual(payment.status, "ready")
        self.assertEqual(payment.amount, order.final_amount)

    def test_unverified_user_cannot_create_order(self):
        """
        ❌ 미인증 사용자는 주문 생성 불가
        """
        # 1. 회원가입 (미인증 상태)
        register_data = {
            "username": "unverified",
            "email": "unverified@example.com",
            "password": "TestPass123!@",
            "password2": "TestPass123!@",
        }

        response = self.client.post(self.register_url, register_data, format="json")
        access_token = response.data["token"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        user = User.objects.get(username="unverified")
        self.assertFalse(user.is_email_verified)

        # 2. 장바구니에 상품 추가 (허용)
        cart_data = {"product_id": self.product.id, "quantity": 1}
        response = self.client.post(f"{self.cart_url}add_item/", cart_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 3. 주문 생성 시도 (차단되어야함)
        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }

        response = self.client.post(self.order_list_url, order_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("이메일 인증", response.data["error"])
        self.assertTrue(response.data["verification_required"])

        # 주문이 생성되지 않았는지 확인
        self.assertEqual(Order.objects.filter(user=user).count(), 0)

    def test_unverified_user_cannot_request_payment(self):
        """
        ❌ 미인증 사용자는 결제 요청 불가
        """
        # 1. 관리자로 강제로 주문 생성 (테스트용)
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

        # 2. 로그인
        self.client.force_authenticate(user=user)

        # 3. 결제 요청 시도 (차단되어야 함)
        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = self.client.post(self.payment_request_url, payment_data, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("이메일 인증", response.data["error"])

        # Payment 객체가 생성되지 않았는지 확인
        self.assertEqual(Payment.objects.filter(order=order).count(), 0)

    def test_verified_user_full_flow(self):
        """
        ✅ 인증된 사용자는 전체 플로우 정상 작동
        """
        # 인증된 사용자 생성
        user = User.objects.create_user(
            username="verified_user",
            email="verified@example.com",
            password="TestPass123!@",
            is_email_verified=True,  # 인증 완료
        )

        self.client.force_authenticate(user=user)

        # 장바구니 → 주문 → 결제 요청까지 모두 성공해야 함
        cart_data = {"product_id": self.product.id, "quantity": 1}
        response = self.client.post(f"{self.cart_url}add_item/", cart_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        order_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101동 202호",
        }
        response = self.client.post(self.order_list_url, order_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        order = Order.objects.get(user=user)

        payment_data = {"order_id": order.id, "payment_method": "card"}
        response = self.client.post(self.payment_request_url, payment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
