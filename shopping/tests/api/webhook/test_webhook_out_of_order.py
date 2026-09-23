"""Webhook 도착 순서 뒤바뀜 테스트"""

from decimal import Decimal

import pytest

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment, PaymentLog
from shopping.services.toss_webhook_service import TossWebhookService
from shopping.tests.factories import ProductFactory, UserFactory


def _create_order_with_payment(user, product, order_status="confirmed", payment_status="ready"):
    """테스트용 주문 및 결제 생성"""
    order = Order.objects.create(
        user=user,
        status=order_status,
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
        status=payment_status,
        toss_order_id=str(order.id),
        payment_key=f"test_key_{order.id}",
    )
    return order, payment


def _make_done_event(payment):
    """DONE Payment 객체 생성"""
    return {
        "paymentKey": payment.payment_key,
        "orderId": payment.toss_order_id,
        "status": "DONE",
        "totalAmount": int(payment.amount),
        "method": "카드",
        "approvedAt": "2025-01-15T10:00:00+09:00",
    }


def _make_canceled_event(payment, reason="사용자 취소"):
    """CANCELED Payment 객체 생성 (토스는 취소 이력을 cancels[] 에 담는다)"""
    return {
        "paymentKey": payment.payment_key,
        "orderId": payment.toss_order_id,
        "status": "CANCELED",
        "totalAmount": int(payment.amount),
        "cancels": [
            {
                "cancelAmount": int(payment.amount),
                "cancelReason": reason,
                "canceledAt": "2025-01-15T11:00:00+09:00",
            }
        ],
    }


@pytest.mark.django_db(transaction=True)
class TestWebhookOutOfOrder:
    """Webhook 순서 뒤바뀜 처리"""

    def test_canceled_then_done_ignores_done(self, category):
        """CANCELED 먼저 도착 후 DONE 도착 시 DONE 무시"""
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "confirmed", "done")
        # is_paid는 status=="done"일 때 자동으로 True (property)

        # CANCELED 먼저
        TossWebhookService.handle_payment_canceled(_make_canceled_event(payment))
        payment.refresh_from_db()
        assert payment.status == "canceled"

        # DONE 나중에 (무시됨)
        TossWebhookService.handle_payment_done(_make_done_event(payment))
        payment.refresh_from_db()
        assert payment.status == "canceled"

    def test_done_canceled_done_final_state_canceled(self, category):
        """DONE → CANCELED → DONE 순서: 최종 상태 canceled"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product)

        # Act
        TossWebhookService.handle_payment_done(_make_done_event(payment))
        payment.refresh_from_db()
        assert payment.status == "done"

        TossWebhookService.handle_payment_canceled(_make_canceled_event(payment))
        payment.refresh_from_db()
        assert payment.status == "canceled"

        TossWebhookService.handle_payment_done(_make_done_event(payment))

        # Assert
        payment.refresh_from_db()
        assert payment.status == "canceled"


@pytest.mark.django_db(transaction=True)
class TestWebhookEarlyArrival:
    """Webhook이 결제 승인 전 도착"""

    def test_pending_payment_receives_done(self, category):
        """pending 상태에서 DONE 웹훅 수신 → done 처리"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "confirmed", "pending")

        # Act
        TossWebhookService.handle_payment_done(_make_done_event(payment))

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == "done"
        assert payment.is_paid is True
        assert order.status == "paid"

    def test_ready_payment_receives_done(self, category):
        """ready 상태에서 DONE 웹훅 수신 → done 처리"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "confirmed", "ready")

        # Act
        TossWebhookService.handle_payment_done(_make_done_event(payment))

        # Assert
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == "done"
        assert order.status == "paid"


@pytest.mark.django_db(transaction=True)
class TestWebhookDuplicate:
    """중복 Webhook 처리"""

    def test_multiple_canceled_webhooks_safe(self, category):
        """여러 CANCELED 웹훅 도착해도 재고 1번만 복구"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=5100)
        product = ProductFactory(category=category, stock=9, sold_count=1, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "paid", "done")
        # is_paid는 status=="done"일 때 자동으로 True (property)
        order.earned_points = 100
        order.save()
        initial_stock = product.stock

        # Act
        for i in range(3):
            TossWebhookService.handle_payment_canceled(_make_canceled_event(payment, f"취소 {i+1}"))

        # Assert
        payment.refresh_from_db()
        product.refresh_from_db()
        assert payment.status == "canceled"
        assert product.stock == initial_stock + 1
        assert product.sold_count == 0

    def test_already_canceled_ignores_canceled(self, category):
        """이미 canceled 상태에서 CANCELED 웹훅 도착 시 재고 중복 복구 안 함"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "canceled", "canceled")
        payment.is_canceled = True
        payment.save()
        initial_stock = product.stock

        # Act
        TossWebhookService.handle_payment_canceled(_make_canceled_event(payment))

        # Assert
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_aborted_then_done_alerts_instead_of_ignoring(self, category, mocker):
        """우리는 aborted 로 끝냈는데 토스 현재 상태가 DONE = 청구됐는데 실패 처리

        예전에는 최종 상태로 보고 INFO 한 줄로 버렸다 — 고아 결제를 알려 주는 유일한 신호였다.
        되살리지는 않는다(롤백이 재고를 이미 돌려놨다). 기록하고 운영자에게 알린다.
        """
        user = UserFactory(is_email_verified=True, points=0)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "payment_failed", "aborted")
        notify = mocker.patch("shopping.tasks.payment_tasks.notify_payment_failure.delay")
        stock_before = product.stock

        TossWebhookService.handle_payment_done(_make_done_event(payment))

        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()
        assert payment.status == "aborted"
        assert order.status == "payment_failed"
        assert product.stock == stock_before
        assert PaymentLog.objects.filter(
            payment=payment, log_type="error", message__contains="수동 대사"
        ).exists()
        notify.assert_called_once()
        assert notify.call_args.args[:2] == (order.id, "charged_but_failed")
        assert notify.call_args.kwargs["severity"] == "critical"

    def test_done_payment_ignores_failed(self, category):
        """done 상태에서 FAILED 웹훅 도착 시 done 유지"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))
        order, payment = _create_order_with_payment(user, product, "paid", "done")
        # is_paid는 status=="done"일 때 자동으로 True (property)

        # Act
        TossWebhookService.handle_payment_failed(
            {
                "paymentKey": payment.payment_key,
                "orderId": payment.toss_order_id,
                "status": "ABORTED",
                "failure": {"code": "REJECT_CARD_COMPANY", "message": "카드 한도 초과"},
            }
        )

        # Assert
        payment.refresh_from_db()
