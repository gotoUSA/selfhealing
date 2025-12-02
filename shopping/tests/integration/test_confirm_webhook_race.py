"""
Confirm API + Webhook 동시 도착 Race Condition 테스트

- Confirm + Webhook 동시 도착 테스트
- Race Condition 방지 검증

시나리오:
1. 스레드 1: /api/payments/confirm/ 호출
2. 스레드 2: handle_payment_done 호출 (웹훅 시뮬레이션)
3. 결과: 딱 1번만 처리되어야 함
"""

import threading
from decimal import Decimal
from unittest.mock import patch

from django.db import connection

import pytest

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentService
from shopping.tests.factories import (
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)
from shopping.webhooks.toss_webhook_view import handle_payment_done


def close_db_connection():
    """스레드별 DB 연결 정리"""
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestConfirmAndWebhookRaceCondition:
    """Confirm API와 Webhook 동시 도착 Race Condition 테스트"""

    def test_confirm_and_webhook_simultaneous_only_one_succeeds(self, category):
        """
        Confirm API와 Webhook이 동시에 도착하면 1번만 처리되어야 함

        시나리오:
        1. 결제 준비 상태 생성
        2. 2개 스레드로 동시에 Confirm API와 Webhook 호출
        3. 결제는 1번만 완료, 재고도 1번만 차감
        """
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_race_condition_key",
        )

        initial_stock = product.stock
        results = []
        lock = threading.Lock()

        # Toss API Mock 응답
        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_confirm_api():
            """Confirm API 호출 시뮬레이션"""
            try:
                with patch(
                    "shopping.services.payment_service.TossPaymentClient.confirm_payment",
                    return_value=mock_response,
                ):
                    result = PaymentService.confirm_payment_sync(
                        payment=Payment.objects.get(pk=payment.pk),
                        payment_key=payment.payment_key,
                        order_id=int(payment.toss_order_id),
                        amount=int(payment.amount),
                        user=user,
                    )
                    with lock:
                        results.append({"source": "confirm", "success": True, "result": result})
            except Exception as e:
                with lock:
                    results.append({"source": "confirm", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        def call_webhook():
            """Webhook 호출"""
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"source": "webhook", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": "webhook", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act - 2개 스레드로 동시 호출
        threads = [
            threading.Thread(target=call_confirm_api),
            threading.Thread(target=call_webhook),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        # 결제는 완료되어야 함
        assert payment.is_paid is True, "Payment가 paid 상태여야 함"
        assert order.status == "paid", "Order가 paid 상태여야 함"

        # 재고는 1번만 차감되어야 함 (10 - 1 = 9)
        assert (
            product.stock == initial_stock - 1
        ), f"재고가 1번만 차감되어야 함. 예상: {initial_stock - 1}, 실제: {product.stock}"

    def test_confirm_and_webhook_simultaneous_points_earned_once(self, category):
        """
        Confirm API와 Webhook 동시 도착 시 포인트도 1번만 적립
        """
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            earned_points=0,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_race_points_key",
        )

        results = []
        lock = threading.Lock()

        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_confirm_api():
            try:
                with patch(
                    "shopping.services.payment_service.TossPaymentClient.confirm_payment",
                    return_value=mock_response,
                ):
                    PaymentService.confirm_payment_sync(
                        payment=Payment.objects.get(pk=payment.pk),
                        payment_key=payment.payment_key,
                        order_id=int(payment.toss_order_id),
                        amount=int(payment.amount),
                        user=user,
                    )
                    with lock:
                        results.append({"source": "confirm", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": "confirm", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        def call_webhook():
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"source": "webhook", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": "webhook", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [
            threading.Thread(target=call_confirm_api),
            threading.Thread(target=call_webhook),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        user.refresh_from_db()
        order.refresh_from_db()

        # 포인트는 1번만 적립 (1% 기준 = 100P)
        expected_points = int(order.total_amount * Decimal("0.01"))
        assert user.points == expected_points, f"포인트가 {expected_points}P여야 함. 실제: {user.points}P"

    def test_multiple_confirm_and_webhook_calls(self, category):
        """
        여러 개의 Confirm API와 Webhook이 동시에 도착하는 극단적 시나리오

        5개 스레드 (3 Confirm + 2 Webhook) 동시 호출
        """
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=100, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price * 3,
            final_amount=product.price * 3,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=3,  # 3개 주문
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_multi_race_key",
        )

        initial_stock = product.stock
        results = []
        lock = threading.Lock()

        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_confirm_api(thread_id):
            try:
                with patch(
                    "shopping.services.payment_service.TossPaymentClient.confirm_payment",
                    return_value=mock_response,
                ):
                    PaymentService.confirm_payment_sync(
                        payment=Payment.objects.get(pk=payment.pk),
                        payment_key=payment.payment_key,
                        order_id=int(payment.toss_order_id),
                        amount=int(payment.amount),
                        user=user,
                    )
                    with lock:
                        results.append({"source": f"confirm_{thread_id}", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": f"confirm_{thread_id}", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        def call_webhook(thread_id):
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"source": f"webhook_{thread_id}", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": f"webhook_{thread_id}", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act - 5개 스레드 (3 Confirm + 2 Webhook)
        threads = [
            threading.Thread(target=call_confirm_api, args=(1,)),
            threading.Thread(target=call_confirm_api, args=(2,)),
            threading.Thread(target=call_confirm_api, args=(3,)),
            threading.Thread(target=call_webhook, args=(1,)),
            threading.Thread(target=call_webhook, args=(2,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        # 결제는 완료되어야 함
        assert payment.is_paid is True, "Payment가 paid 상태여야 함"
        assert order.status == "paid", "Order가 paid 상태여야 함"

        # 재고는 1번만 차감 (3개)
        assert (
            product.stock == initial_stock - 3
        ), f"재고가 3개만 차감되어야 함. 예상: {initial_stock - 3}, 실제: {product.stock}"


@pytest.mark.django_db(transaction=True)
class TestConfirmAfterWebhook:
    """Webhook이 먼저 처리된 후 Confirm API 호출 테스트"""

    def test_confirm_after_webhook_already_processed_returns_error(self, category):
        """
        Webhook이 먼저 처리된 후 Confirm API 호출 시 '이미 완료된 결제' 에러
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_webhook_first_key",
        )

        initial_stock = product.stock

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        # Act 1: Webhook 먼저 처리
        handle_payment_done(event_data)

        payment.refresh_from_db()
        product.refresh_from_db()

        # 중간 검증: Webhook으로 결제 완료
        assert payment.is_paid is True
        assert product.stock == initial_stock - 1

        # Act 2: Confirm API 호출
        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        from shopping.services.payment_service import PaymentConfirmError

        with pytest.raises(PaymentConfirmError) as exc_info:
            with patch(
                "shopping.services.payment_service.TossPaymentClient.confirm_payment",
                return_value=mock_response,
            ):
                PaymentService.confirm_payment_sync(
                    payment=Payment.objects.get(pk=payment.pk),
                    payment_key=payment.payment_key,
                    order_id=int(payment.toss_order_id),
                    amount=int(payment.amount),
                    user=user,
                )

        # Assert
        assert "이미 완료된 결제" in str(exc_info.value)

        # 재고는 변하지 않아야 함 (이미 1번 차감됨)
        product.refresh_from_db()
        assert product.stock == initial_stock - 1


@pytest.mark.django_db(transaction=True)
class TestConfirmBeforeWebhook:
    """Confirm API가 먼저 처리된 후 Webhook 도착 테스트"""

    def test_webhook_after_confirm_is_ignored(self, category):
        """
        Confirm API가 먼저 처리된 후 Webhook 도착 시 무시됨
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_confirm_first_key",
        )

        initial_stock = product.stock

        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        # Act 1: Confirm API 먼저 처리
        with patch(
            "shopping.services.payment_service.TossPaymentClient.confirm_payment",
            return_value=mock_response,
        ):
            PaymentService.confirm_payment_sync(
                payment=Payment.objects.get(pk=payment.pk),
                payment_key=payment.payment_key,
                order_id=int(payment.toss_order_id),
                amount=int(payment.amount),
                user=user,
            )

        payment.refresh_from_db()
        product.refresh_from_db()

        # 중간 검증: Confirm으로 결제 완료
        assert payment.is_paid is True
        assert product.stock == initial_stock - 1

        # Act 2: Webhook 도착
        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        # Webhook은 에러 없이 무시되어야 함
        handle_payment_done(event_data)

        # Assert: 재고는 변하지 않아야 함
        product.refresh_from_db()
        assert (
            product.stock == initial_stock - 1
        ), f"Webhook이 무시되어 재고 변화 없어야 함. 예상: {initial_stock - 1}, 실제: {product.stock}"
