"""결제 상태 조회 테스트 (PaymentStatusView)"""

import pytest
from rest_framework import status

from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    CompletedPaymentFactory,
    ProductFactory,
)


@pytest.mark.django_db
class TestPaymentStatusNormalCase:
    """정상 케이스"""

    def test_get_own_payment_status_ready(self, authenticated_client, user, order, payment):
        """ready 상태 결제 조회"""
        # Arrange
        assert payment.status == "ready"

        # Act
        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["payment_id"] == payment.id
        assert response.data["status"] == "ready"
        assert response.data["is_paid"] is False

    def test_get_own_payment_status_done(self, authenticated_client, user, product):
        """done 상태 결제 조회"""
        # Arrange
        order = OrderFactory(user=user, status="paid")
        OrderItemFactory(order=order, product=product)
        payment = CompletedPaymentFactory(order=order)

        # Act
        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "done"
        assert response.data["is_paid"] is True
        assert response.data["order_status"] == "paid"

    def test_response_data_structure(self, authenticated_client, order, payment):
        """응답 데이터 구조 검증"""
        # Act
        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.data

        required_fields = ["payment_id", "status", "is_paid", "order_status", "order_id"]
        for field in required_fields:
            assert field in data, f"필수 필드 누락: {field}"

        # Assert - 타입 검증
        assert isinstance(data["payment_id"], int)
        assert isinstance(data["status"], str)
        assert isinstance(data["is_paid"], bool)
        assert isinstance(data["order_id"], int)

    def test_various_payment_statuses(self, authenticated_client, user, product):
        """다양한 결제 상태 조회"""
        # Arrange
        test_cases = [
            ("ready", False, "confirmed"),
            ("done", True, "paid"),
            ("canceled", False, "canceled"),
            ("aborted", False, "confirmed"),
        ]

        for payment_status, expected_is_paid, order_status in test_cases:
            order = OrderFactory(user=user, status=order_status)
            OrderItemFactory(order=order, product=product)

            if payment_status == "done":
                payment = CompletedPaymentFactory(order=order)
            else:
                payment = PaymentFactory(order=order, status=payment_status)

            # Act
            response = authenticated_client.get(f"/api/payments/{payment.id}/status/")

            # Assert
            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == payment_status
            assert response.data["is_paid"] == expected_is_paid


@pytest.mark.django_db
class TestPaymentStatusException:
    """예외 케이스"""

    def test_nonexistent_payment(self, authenticated_client):
        """존재하지 않는 결제"""
        # Arrange
        nonexistent_id = 999999

        # Act
        response = authenticated_client.get(f"/api/payments/{nonexistent_id}/status/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "결제 정보를 찾을 수 없습니다" in str(response.data)

    def test_other_user_payment(self, authenticated_client, other_user, product):
        """다른 사용자의 결제 조회 불가"""
        # Arrange
        order = OrderFactory(user=other_user)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order)

        # Act
        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "결제 정보를 찾을 수 없습니다" in str(response.data)

    def test_unauthenticated_access(self, api_client, payment):
        """비인증 사용자 접근 거부"""
        # Act
        response = api_client.get(f"/api/payments/{payment.id}/status/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
