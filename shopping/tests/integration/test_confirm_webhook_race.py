"""
Confirm API + Webhook 동시 도착 Race Condition 테스트

Purpose:
    Confirm API와 Webhook이 동시에 도착하는 극단적 race condition 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - Confirm + Webhook 동시 도착 시 1번만 처리
        - 재고/포인트 중복 변경 방지
    B. Sequential Tests:
        - Webhook 먼저 → Confirm 호출 시 에러
        - Confirm 먼저 → Webhook 무시

Concurrency Control:
    - Payment.is_paid 플래그
    - select_for_update

시나리오:
    1. 스레드 1: /api/payments/confirm/ 호출
    2. 스레드 2: TossWebhookService.handle_payment_done 호출
    3. 결과: 딱 1번만 처리되어야 함
"""

import threading
from decimal import Decimal
from unittest.mock import patch

from django.db import connection

import pytest

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentConfirmError, PaymentService
from shopping.services.toss_webhook_service import TossWebhookService
from shopping.tests.factories import (
    ProductFactory,
    TossResponseBuilder,
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

    # 주문 생성 시 재고 차감 시뮬레이션
    product.stock -= 1
    product.save()

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
class TestConfirmWebhookRaceInvariant:
    """
    핵심 불변 조건: Confirm API + Webhook 동시 도착 시 1회만 처리

    Purpose:
        두 경로에서 동시에 결제 완료 요청이 와도 중복 처리 없음
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        Payment.is_paid 플래그 + select_for_update
    """

    def test_confirm_and_webhook_simultaneous_stock_unchanged(self, category):
        """
        Purpose:
            Confirm + Webhook 동시 도착 시 재고 변화 없음 (이미 주문 시 차감됨)
        Scenario:
            stock=10, 주문 생성 시 1 차감 → stock=9
            Confirm + Webhook 동시 도착
        Expected:
            stock = 9 (Confirm/Webhook에서는 sold_count만 증가)
        """
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = create_test_order_with_payment(user, product, "test_race_condition_key")

        initial_stock = product.stock  # 9 (주문 생성 시 1 차감됨)
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
            try:
                TossWebhookService.handle_payment_done(event_data)
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
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()

        assert payment.is_paid is True, "Payment paid 상태"
        assert order.status == "paid", "Order paid 상태"
        assert product.stock == initial_stock, f"재고 변화 없음. 예상: {initial_stock}, 실제: {product.stock}"

    def test_confirm_and_webhook_simultaneous_points_earned_once(self, category):
        """
        Purpose:
            Confirm + Webhook 동시 도착 시 포인트 1번만 적립
        Scenario:
            10000원 결제 (1% = 100P), Confirm + Webhook 동시 도착
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
                TossWebhookService.handle_payment_done(event_data)
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

        expected_points = int(order.total_amount * Decimal("0.01"))
        assert user.points == expected_points, f"포인트 = {expected_points}P. 실제: {user.points}P"

    def test_multiple_confirm_and_webhook_5_threads(self, category):
        """
        Purpose:
            3 Confirm + 2 Webhook 동시 도착 극단적 시나리오
        Scenario:
            5개 스레드 (3 Confirm + 2 Webhook) 동시 호출
        Expected:
            결제 완료, 재고 변화 없음 (주문 시 이미 차감)
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
            quantity=3,
            price=product.price,
        )

        # 주문 생성 시 재고 차감 시뮬레이션
        product.stock -= 3
        product.save()

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
                TossWebhookService.handle_payment_done(event_data)
                with lock:
                    results.append({"source": f"webhook_{thread_id}", "success": True})
            except Exception as e:
                with lock:
                    results.append({"source": f"webhook_{thread_id}", "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
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

        assert payment.is_paid is True, "Payment paid 상태"
        assert order.status == "paid", "Order paid 상태"
        assert product.stock == initial_stock, f"재고 변화 없음. 예상: {initial_stock}, 실제: {product.stock}"


# =============================================================================
# B. 순차 처리 테스트 (Sequential Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestConfirmAfterWebhook:
    """
    Webhook 먼저 처리 → Confirm 호출 시나리오

    Purpose:
        Webhook이 먼저 처리된 후 Confirm 호출 시 에러 반환
    """

    def test_confirm_after_webhook_returns_already_completed_error(self, category):
        """
        Purpose:
            Webhook 먼저 처리 후 Confirm 호출 시 "이미 완료된 결제" 에러
        Scenario:
            Webhook으로 결제 완료 → Confirm API 호출
        Expected:
            PaymentConfirmError("이미 완료된 결제")
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = create_test_order_with_payment(user, product, "test_webhook_first_key")

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
        TossWebhookService.handle_payment_done(event_data)

        payment.refresh_from_db()
        product.refresh_from_db()

        # 중간 검증
        assert payment.is_paid is True
        assert product.stock == initial_stock

        # Act 2: Confirm API 호출
        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

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

        # 재고 변화 없음
        product.refresh_from_db()
        assert product.stock == initial_stock


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestConfirmBeforeWebhook:
    """
    Confirm 먼저 처리 → Webhook 도착 시나리오

    Purpose:
        Confirm이 먼저 처리된 후 Webhook 도착 시 무시
    """

    def test_webhook_after_confirm_is_ignored(self, category):
        """
        Purpose:
            Confirm 먼저 처리 후 Webhook 도착 시 무시됨
        Scenario:
            Confirm으로 결제 완료 → Webhook 도착
        Expected:
            Webhook 무시, 재고 변화 없음
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = create_test_order_with_payment(user, product, "test_confirm_first_key")

        initial_stock = product.stock

        mock_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=payment.toss_order_id,
            amount=int(payment.amount),
        )

        # Act 1: Confirm 먼저 처리
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

        # 중간 검증
        assert payment.is_paid is True
        assert product.stock == initial_stock

        # Act 2: Webhook 도착
        event_data = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "DONE",
            "totalAmount": int(payment.amount),
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        # Webhook은 에러 없이 무시
        TossWebhookService.handle_payment_done(event_data)

        # Assert
        product.refresh_from_db()
        assert product.stock == initial_stock, "Webhook 무시, 재고 변화 없음"
