from decimal import Decimal

from django.conf import settings

import pytest
from rest_framework import status

from shopping.models.payment import Payment, PaymentLog
from shopping.tests.factories import OrderFactory, OrderItemFactory, ProductFactory


@pytest.mark.django_db
class TestPaymentRequestNormalCase:
    """정상 케이스"""

    def test_single_product_payment_request(self, authenticated_client, order):
        """단일 상품 결제 요청 - 주문명: 상품명"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 응답 상태
        assert response.status_code == status.HTTP_201_CREATED

        # Assert - Payment 생성 확인
        assert Payment.objects.filter(order=order).exists()
        payment = Payment.objects.get(order=order)
        assert payment.status == "ready"
        assert payment.amount == order.final_amount

        # Assert - 응답 데이터 구조
        data = response.json()
        assert "payment_id" in data
        assert "order_id" in data
        assert "order_name" in data
        assert "customer_name" in data
        assert "customer_email" in data
        assert "amount" in data
        assert "client_key" in data
        assert "success_url" in data
        assert "fail_url" in data

        # Assert - 주문명 (단일 상품)
        first_item = order.order_items.first()
        assert data["order_name"] == first_item.product_name

        # Assert - 결제 정보
        assert data["payment_id"] == payment.id
        assert data["order_id"] == order.id
        assert data["amount"] == int(order.final_amount)
        assert data["client_key"] == settings.TOSS_CLIENT_KEY

    def test_multiple_products_payment_request(self, authenticated_client, order_with_multiple_items):
        """여러 상품 결제 요청 - 주문명: '상품명 외 N건'"""
        # Arrange
        order = order_with_multiple_items
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()
        first_item = order.order_items.first()
        item_count = order.order_items.count()

        # Assert - 주문명 형식: "상품명 외 N건"
        expected_order_name = f"{first_item.product_name} 외 {item_count - 1}건"
        assert data["order_name"] == expected_order_name

    def test_payment_object_created(self, authenticated_client, order):
        """Payment 객체 생성 확인"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        payment = Payment.objects.get(order=order)
        assert payment.order == order
        assert payment.status == "ready"
        assert payment.amount == order.final_amount
        assert payment.toss_order_id == str(order.id)  # Toss에 전송하는 orderId와 일치
        assert payment.method == "card"  # 기본값

    def test_response_data_structure(self, authenticated_client, order, user):
        """응답 데이터 구조 검증"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()

        # Assert - 필수 필드 존재
        required_fields = [
            "payment_id",
            "order_id",
            "order_name",
            "customer_name",
            "customer_email",
            "amount",
            "client_key",
            "success_url",
            "fail_url",
        ]
        for field in required_fields:
            assert field in data, f"필수 필드 누락: {field}"

        # Assert - 데이터 타입
        assert isinstance(data["payment_id"], int)
        assert isinstance(data["order_id"], int)
        assert isinstance(data["order_name"], str)
        assert isinstance(data["customer_name"], str)
        assert isinstance(data["customer_email"], str)
        assert isinstance(data["amount"], int)
        assert isinstance(data["client_key"], str)
        assert isinstance(data["success_url"], str)
        assert isinstance(data["fail_url"], str)

        # Assert - 고객 정보
        assert data["customer_email"] == user.email
        assert data["customer_name"] == (user.get_full_name() or user.username)

    def test_payment_log_created(self, authenticated_client, order):
        """PaymentLog 생성 확인"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        payment = Payment.objects.get(order=order)
        logs = PaymentLog.objects.filter(payment=payment)

        # Assert - 로그 생성 확인
        assert logs.exists()
        log = logs.first()
        assert log.log_type == "request"
        assert log.message == "결제 요청 생성"
        assert "order_id" in log.data
        assert "total_amount" in log.data
        assert "amount" in log.data


@pytest.mark.django_db
class TestPaymentRequestBoundary:
    """경계값 테스트"""

    def test_existing_payment_deletion_and_recreation(self, authenticated_client, order_with_existing_payment):
        """기존 Payment 재사용 (재시도)"""
        # Arrange
        order = order_with_existing_payment
        old_payment_id = Payment.objects.get(order=order).id
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        # Assert - 기존 Payment 재사용 확인 (삭제되지 않음)
        assert Payment.objects.filter(id=old_payment_id).exists()

        # Assert - 동일한 Payment 객체 반환 확인
        payment = Payment.objects.get(order=order)
        assert payment.id == old_payment_id
        assert payment.status == "ready"

    def test_payment_with_points(self, authenticated_client, order_with_points):
        """포인트 사용 주문 - final_amount로 결제"""
        # Arrange
        order = order_with_points
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()
        payment = Payment.objects.get(order=order)

        # Assert - 포인트 차감 후 금액으로 결제
        assert payment.amount == order.final_amount
        assert data["amount"] == int(order.final_amount)
        assert order.final_amount == order.total_amount - order.used_points

    def test_long_product_name(self, authenticated_client, order_with_long_product_name):
        """긴 상품명 처리"""
        # Arrange
        order = order_with_long_product_name
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()
        first_item = order.order_items.first()

        # Assert - 긴 상품명도 정상 처리
        assert data["order_name"] == first_item.product_name
        assert len(data["order_name"]) > 100


@pytest.mark.django_db
class TestPaymentRequestException:
    """예외 케이스"""

    def test_unverified_user_rejected(self, api_client, unverified_user, order):
        """이메일 미인증 사용자 거부"""
        # Arrange - 미인증 사용자로 인증
        from django.urls import reverse

        response = api_client.post(
            reverse("auth-login"),
            {"username": "unverified", "password": "testpass123"},
        )
        token = response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # 미인증 사용자의 주문 생성
        order.user = unverified_user
        order.save()

        request_data = {"order_id": order.id}

        # Act
        response = api_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "이메일 인증이 필요합니다" in response.json()["error"]

    def test_nonexistent_order(self, authenticated_client):
        """존재하지 않는 주문"""
        # Arrange
        request_data = {"order_id": 99999}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "주문을 찾을 수 없습니다" in str(response.json())

    def test_other_user_order(self, authenticated_client, other_user_order):
        """다른 사용자의 주문"""
        # Arrange
        request_data = {"order_id": other_user_order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "주문을 찾을 수 없습니다" in str(response.json())

    def test_already_paid_order(self, authenticated_client, paid_order_with_payment):
        """이미 결제된 주문"""
        # Arrange
        order = paid_order_with_payment
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미 결제된 주문입니다" in str(response.json())

    def test_non_pending_status_order(self, authenticated_client, canceled_order):
        """confirmed가 아닌 상태 주문"""
        # Arrange
        request_data = {"order_id": canceled_order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_message = str(response.json())
        assert "주문 처리가 완료되지 않았습니다" in error_message

    def test_zero_amount_order(self, authenticated_client, user, category):
        """주문 금액 0원 이하"""
        # Arrange - total_amount가 0원인 주문 생성

        # 0원 상품 생성
        zero_product = ProductFactory(
            name="무료 상품",
            category=category,
            price=Decimal("0"),
        )

        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("0"),  # 0원
        )

        OrderItemFactory(
            order=order,
            product=zero_product,
            quantity=1,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 올바르지 않습니다" in str(response.json())

    def test_unauthenticated_user(self, api_client, order):
        """인증되지 않은 사용자"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = api_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 401 Unauthorized
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_order_id(self, authenticated_client):
        """order_id 누락"""
        # Arrange
        request_data = {}  # order_id 없음

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "order_id" in str(response.json())

    def test_invalid_order_id_type(self, authenticated_client):
        """잘못된 order_id 타입"""
        # Arrange
        request_data = {"order_id": "invalid"}  # 문자열

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_order_without_items(self, authenticated_client, user):
        """주문 항목이 없는 주문"""
        # Arrange - OrderItem 없이 주문만 생성
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        # OrderItem을 생성하지 않음

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "주문 항목이 아직 생성되지 않았습니다" in str(response.json())
