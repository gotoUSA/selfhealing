"""Toss Confirm API Timeout 및 Retry 테스트"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)
from shopping.utils.toss_payment import TossPaymentClient, TossPaymentError


@pytest.mark.django_db
class TestTossConfirmTimeout:
    """Toss Confirm API Timeout 처리"""

    def test_timeout_raises_network_error(self, mocker):
        """Timeout 발생 시 NETWORK_ERROR 코드로 TossPaymentError 발생"""
        # Arrange
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.Timeout("Connection timed out"),
        )
        client = TossPaymentClient()

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            client.confirm_payment(payment_key="test", order_id="ORDER_001", amount=10000)

        assert exc_info.value.code == "NETWORK_ERROR"
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_connect_timeout_raises_network_error(self, mocker):
        """ConnectTimeout도 NETWORK_ERROR로 처리"""
        # Arrange
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectTimeout("Connect timeout"),
        )
        client = TossPaymentClient()

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            client.confirm_payment(payment_key="test", order_id="ORDER_001", amount=10000)

        assert exc_info.value.code == "NETWORK_ERROR"

    def test_read_timeout_raises_network_error(self, mocker):
        """ReadTimeout도 NETWORK_ERROR로 처리"""
        # Arrange
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.ReadTimeout("Read timeout"),
        )
        client = TossPaymentClient()

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            client.confirm_payment(payment_key="test", order_id="ORDER_001", amount=10000)

        assert exc_info.value.code == "NETWORK_ERROR"


@pytest.mark.django_db
class TestTossConfirmRetry:
    """Toss Confirm 재시도 로직"""

    def test_retry_after_timeout_succeeds(self, mocker, category):
        """첫 번째 Timeout 후 재시도 성공"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")

        mock_responses = [
            requests.exceptions.Timeout("Timeout"),
            Mock(
                status_code=200,
                json=Mock(
                    return_value=TossResponseBuilder.success_response(
                        payment_key=payment.payment_key,
                        order_id=str(order.id),
                        amount=int(payment.amount),
                    )
                ),
            ),
        ]
        mock_post = mocker.patch("requests.post", side_effect=mock_responses)
        client = TossPaymentClient()

        # Act
        with pytest.raises(TossPaymentError):
            client.confirm_payment(
                payment_key=payment.payment_key,
                order_id=str(order.id),
                amount=int(payment.amount),
            )

        result = client.confirm_payment(
            payment_key=payment.payment_key,
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Assert
        assert result["status"] == "DONE"
        assert mock_post.call_count == 2

    def test_idempotency_key_allows_duplicate_calls(self, mocker, category):
        """동일 결제키로 중복 호출해도 정상 응답"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")

        success_response = Mock(
            status_code=200,
            json=Mock(
                return_value=TossResponseBuilder.success_response(
                    payment_key=payment.payment_key,
                    order_id=str(order.id),
                    amount=int(payment.amount),
                )
            ),
        )
        mocker.patch("requests.post", return_value=success_response)
        client = TossPaymentClient()

        # Act
        result1 = client.confirm_payment(
            payment_key=payment.payment_key,
            order_id=str(order.id),
            amount=int(payment.amount),
        )
        result2 = client.confirm_payment(
            payment_key=payment.payment_key,
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Assert
        assert result1["status"] == "DONE"
        assert result2["status"] == "DONE"


@pytest.mark.django_db
class TestTossTimeoutCeleryTask:
    """Celery 태스크에서 Timeout 처리"""

    def test_network_error_sets_payment_aborted(self, mocker, category, api_client):
        """네트워크 에러 시 payment 상태가 aborted"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")

        api_client.force_authenticate(user=user)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500),
        )

        # Act
        response = api_client.post(
            "/api/payments/confirm/",
            {"order_id": order.id, "payment_key": payment.payment_key, "amount": int(payment.amount)},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        payment.refresh_from_db()
        assert payment.status == "aborted"

    def test_timeout_error_logged_in_payment_log(self, mocker, category, api_client):
        """Timeout 에러가 PaymentLog에 기록됨"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")

        api_client.force_authenticate(user=user)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError("NETWORK_ERROR", "Request timeout", 500),
        )

        # Act
        api_client.post(
            "/api/payments/confirm/",
            {"order_id": order.id, "payment_key": payment.payment_key, "amount": int(payment.amount)},
            format="json",
        )

        # Assert
        error_log = PaymentLog.objects.filter(payment=payment, log_type="error").first()
        assert error_log is not None


@pytest.mark.django_db
class TestTossTimeoutRollback:
    """Timeout 발생 시 롤백 검증"""

    def test_timeout_keeps_original_payment_status(self, mocker, category):
        """Timeout 시 Payment 상태 변경 없음"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")
        initial_status = payment.status

        mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout"))
        client = TossPaymentClient()

        # Act
        with pytest.raises(TossPaymentError):
            client.confirm_payment(
                payment_key=payment.payment_key,
                order_id=str(order.id),
                amount=int(payment.amount),
            )

        # Assert
        payment.refresh_from_db()
        assert payment.status == initial_status

    def test_timeout_keeps_original_stock(self, mocker, category):
        """Timeout 시 재고 변경 없음"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")
        initial_stock = product.stock

        mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout"))
        client = TossPaymentClient()

        # Act
        with pytest.raises(TossPaymentError):
            client.confirm_payment(
                payment_key=payment.payment_key,
                order_id=str(order.id),
                amount=int(payment.amount),
            )

        # Assert
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_timeout_keeps_original_points(self, mocker, category):
        """Timeout 시 포인트 변경 없음"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=1000)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="ready")
        initial_points = user.points

        mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout"))
        client = TossPaymentClient()

        # Act
        with pytest.raises(TossPaymentError):
            client.confirm_payment(
                payment_key=payment.payment_key,
                order_id=str(order.id),
                amount=int(payment.amount),
            )

        # Assert
        user.refresh_from_db()
        assert user.points == initial_points
