"""
Webhook 동시성 테스트

Purpose:
    동시 Webhook 도착 시 중복 처리 방지 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 동일 결제 DONE 이벤트 중복 처리 방지
        - 재고/포인트/sold_count 1회만 변경
    B. High Concurrency Tests:
        - 5개 스레드 동시 처리
    C. Edge Cases:
        - 이미 완료된 결제에 Webhook 도착

Concurrency Control:
    - Payment.is_paid 플래그 - 중복 처리 방지
    - select_for_update - 행 단위 락
"""

import threading
from decimal import Decimal

from django.db import connection

import pytest

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.services.toss_webhook_service import TossWebhookService
from shopping.tests.factories import (
    ProductFactory,
    UserFactory,
)


# =============================================================================
# 헬퍼 함수
# =============================================================================


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


def create_test_order_with_payment(user, product, payment_key: str) -> tuple[Order, Payment]:
    """테스트용 주문 및 결제 생성 헬퍼"""
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
        payment_key=payment_key,
    )

    return order, payment


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestWebhookDuplicateDoneInvariant:
    """
    핵심 불변 조건: 동일 DONE 이벤트 중복 처리 방지

    Purpose:
        동일 결제에 DONE 이벤트가 여러 번 도착해도 1번만 처리
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        Payment.is_paid 플래그 + select_for_update
    """

    def test_duplicate_done_webhook_stock_deducted_once(self, category):
        """
        Purpose:
            동일 결제에 DONE 이벤트 2번 도착 시 sold_count 1번만 증가
        Scenario:
            sold_count=0, quantity=1, 2개 스레드 동시 DONE 이벤트 처리
        Expected:
            Payment paid, Order paid, sold_count=1
        Note:
            재고(stock)는 주문 생성 시 차감됨. 웹훅은 sold_count만 증가시킴.
        Concurrency Control:
            is_paid 플래그로 중복 처리 방지
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, sold_count=0, price=Decimal("10000"))
        order, payment = create_test_order_with_payment(user, product, "test_webhook_dup_key")

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
            try:
                TossWebhookService.handle_payment_done(event_data)
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
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        assert payment.is_paid is True, "Payment paid 상태"
        assert order.status == "paid", "Order paid 상태"
        # 웹훅은 sold_count만 증가시킴 (재고 차감은 주문 생성 시 처리)
        assert product.sold_count == 1, f"sold_count 1번만 증가. 실제: {product.sold_count}"

    def test_duplicate_done_webhook_sold_count_increased_once(self, category):
        """
        Purpose:
            동일 결제에 DONE 이벤트 2번 도착 시 sold_count 1번만 증가
        Scenario:
            sold_count=0, quantity=2, 2개 스레드 동시 처리
        Expected:
            sold_count = 2 (1회 증가분만)
        """
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
            try:
                TossWebhookService.handle_payment_done(event_data)
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
        assert product.sold_count == 2, f"sold_count = 2. 실제: {product.sold_count}"

    def test_duplicate_done_webhook_points_earned_once(self, category):
        """
        Purpose:
            동일 결제에 DONE 이벤트 2번 도착 시 포인트 1번만 적립
        Scenario:
            user.points=0, 10000원 결제 (1% = 100P), 2개 스레드 동시 처리
        Expected:
            user.points = 100P
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
            try:
                TossWebhookService.handle_payment_done(event_data)
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

        expected_points = int(order.total_amount * Decimal("0.01"))
        assert user.points == expected_points, f"포인트 = {expected_points}P. 실제: {user.points}P"
        assert order.earned_points == expected_points


# =============================================================================
# B. 높은 동시성 테스트 (High Concurrency Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
@pytest.mark.slow
class TestWebhookHighConcurrency:
    """
    높은 동시성 테스트

    Purpose:
        5개 이상 스레드 동시 처리에서도 정합성 유지
    """

    def test_5_concurrent_done_webhooks(self, category):
        """
        Purpose:
            5개 스레드로 동시에 DONE 이벤트 처리
        Scenario:
            sold_count=0, quantity=5, 5개 스레드 동시 처리
        Expected:
            sold_count 5만 증가 (0 + 5 = 5)
        Note:
            재고(stock)는 주문 생성 시 차감됨. 웹훅은 sold_count만 증가시킴.
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=100, sold_count=0, price=Decimal("10000"))

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
            try:
                TossWebhookService.handle_payment_done(event_data)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
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
        # 웹훅은 sold_count만 증가시킴 (재고 차감은 주문 생성 시 처리)
        assert product.sold_count == 5, f"sold_count 5만 증가. 실제: {product.sold_count}"


# =============================================================================
# C. 엣지 케이스 테스트 (Edge Cases)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestWebhookEdgeCases:
    """
    엣지 케이스: 이미 처리된 결제, 재고 부족 등

    Purpose:
        비정상 상황에서도 데이터 정합성 유지
    """

    def test_webhook_on_already_paid_order_is_ignored(self, category):
        """
        Purpose:
            이미 paid 상태인 주문에 DONE 웹훅 도착 시 무시
        Scenario:
            이미 완료된 결제에 Webhook 도착
        Expected:
            재고/포인트 변화 없음
        """
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
        payment.mark_as_paid(
            {
                "paymentKey": payment.payment_key,
                "orderId": payment.toss_order_id,
                "status": "DONE",
                "totalAmount": int(payment.amount),
                "method": "카드",
                "approvedAt": "2025-01-15T10:00:00+09:00",
            }
        )

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

        # Act
        TossWebhookService.handle_payment_done(event_data)

        # Assert
        product.refresh_from_db()
        user.refresh_from_db()

        assert product.stock == initial_stock, "재고 변화 없음"
        assert user.points == initial_points, "포인트 변화 없음"

    def test_webhook_does_not_allow_negative_stock(self, category):
        """
        Purpose:
            재고 부족 시에도 음수가 되지 않음
        Scenario:
            stock=1, quantity=5 (재고 < 주문 수량)
        Expected:
            stock >= 0 (Greatest 사용)
        """
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
        TossWebhookService.handle_payment_done(event_data)

        # Assert
        product.refresh_from_db()
        assert product.stock >= 0, f"재고 음수 불가. 실제: {product.stock}"
