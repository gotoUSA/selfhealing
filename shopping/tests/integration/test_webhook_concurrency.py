"""
Webhook 동시성 테스트

동시 Webhook 도착 시 중복 처리 방지 검증
- 동일 결제에 DONE 이벤트 2번 동시 도착
- Confirm API와 Webhook 동시 도착 (Race Condition)
"""

import threading
from decimal import Decimal

from django.db import connection
from django.db.models import F

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.product import Product
from shopping.tests.factories import (
    OrderFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)
from shopping.webhooks.toss_webhook_view import handle_payment_done


def close_db_connection():
    """스레드별 DB 연결 정리"""
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestWebhookDuplicateDone:
    """동일 결제에 DONE 이벤트 2번 동시 도착 테스트"""

    def test_duplicate_payment_done_webhook_only_one_succeeds(self, category):
        """동일 결제에 DONE 이벤트 2번 동시 도착 시 1번만 처리"""
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
            payment_key="test_webhook_dup_key",
        )

        initial_stock = product.stock
        results = []
        lock = threading.Lock()

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_webhook():
            """Webhook 호출"""
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act - 2개 스레드로 동시에 handle_payment_done 호출
        threads = [threading.Thread(target=call_webhook) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        assert payment.is_paid is True, "Payment가 paid 상태여야 함"
        assert order.status == "paid", "Order가 paid 상태여야 함"

        # 재고는 1번만 차감되어야 함 (10 - 1 = 9)
        assert product.stock == initial_stock - 1, f"재고가 1번만 차감되어야 함. 실제: {product.stock}"

    def test_duplicate_payment_done_webhook_sold_count_once(self, category):
        """동일 결제에 DONE 이벤트 2번 도착 시 sold_count도 1번만 증가"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, sold_count=0, price=Decimal("10000"))

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
            quantity=2,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_webhook_sold_key",
        )

        results = []
        lock = threading.Lock()

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_webhook():
            """Webhook 호출"""
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=call_webhook) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        product.refresh_from_db()

        # sold_count는 quantity(2)만큼만 1번 증가해야 함
        assert product.sold_count == 2, f"sold_count가 2여야 함. 실제: {product.sold_count}"

    def test_duplicate_payment_done_webhook_points_earned_once(self, category):
        """동일 결제에 DONE 이벤트 2번 도착 시 포인트도 1번만 적립"""
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
            payment_key="test_webhook_points_key",
        )

        results = []
        lock = threading.Lock()

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_webhook():
            """Webhook 호출"""
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=call_webhook) for _ in range(2)]
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
        assert order.earned_points == expected_points, f"earned_points가 {expected_points}P여야 함"


@pytest.mark.django_db(transaction=True)
class TestWebhookHighConcurrency:
    """Webhook 높은 동시성 테스트"""

    def test_many_concurrent_done_webhooks(self, category):
        """5개 스레드로 동시에 DONE 이벤트 처리"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=100, price=Decimal("10000"))

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
            quantity=5,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_webhook_high_conc_key",
        )

        initial_stock = product.stock
        results = []
        lock = threading.Lock()

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        def call_webhook():
            """Webhook 호출"""
            try:
                handle_payment_done(event_data)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act - 5개 스레드로 동시 호출
        threads = [threading.Thread(target=call_webhook) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        assert payment.is_paid is True
        assert order.status == "paid"

        # 재고는 1번만 차감 (5개)
        assert product.stock == initial_stock - 5, f"재고가 5개만 차감되어야 함. 실제: {product.stock}"


@pytest.mark.django_db(transaction=True)
class TestWebhookAndAlreadyPaid:
    """이미 결제 완료된 주문에 대한 Webhook 테스트"""

    def test_webhook_on_already_paid_order_is_ignored(self, category):
        """이미 paid 상태인 주문에 DONE 웹훅 도착 시 무시"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=100)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="paid",
            total_amount=product.price,
            final_amount=product.price,
            earned_points=100,
            payment_method="card",
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
            status="done",
            toss_order_id=str(order.id),
            payment_key="test_already_paid_key",
        )
        payment.mark_as_paid({
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        })

        initial_stock = product.stock
        initial_points = user.points

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        # Act - 이미 paid인 상태에서 다시 webhook 호출
        handle_payment_done(event_data)

        # Assert
        product.refresh_from_db()
        user.refresh_from_db()

        # 재고/포인트 변화 없어야 함
        assert product.stock == initial_stock, "이미 처리된 주문이므로 재고 변화 없어야 함"
        assert user.points == initial_points, "이미 처리된 주문이므로 포인트 변화 없어야 함"


@pytest.mark.django_db(transaction=True)
class TestWebhookStockValidation:
    """Webhook 재고 검증 테스트"""

    def test_webhook_does_not_allow_negative_stock(self, category):
        """재고가 부족해도 음수가 되지 않아야 함"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=1, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price * 5,
            final_amount=product.price * 5,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        # 재고(1) < 주문 수량(5)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=5,
            price=product.price,
        )

        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="test_negative_stock_key",
        )

        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        # Act
        handle_payment_done(event_data)

        # Assert
        product.refresh_from_db()

        # 재고가 음수가 되어서는 안 됨 (조건부 업데이트 적용 시)
        # 현재 코드는 Greatest(F("stock") - quantity, 0)를 사용하므로 최소 0
        assert product.stock >= 0, f"재고는 음수가 될 수 없음. 실제: {product.stock}"
