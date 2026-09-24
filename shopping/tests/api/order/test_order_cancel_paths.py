"""
주문 취소 입구별 계약 — 어느 입구로 취소해도 돈·재고·포인트가 한 번씩만 움직인다

취소 입구는 네 개다: 주문 취소 버튼, 결제 취소 버튼, 토스 취소 웹훅, 미결제 만료 배치.
2026-09-24 실제 스택 검증에서 입구마다 빠진 연결이 나왔고, 이 파일이 그 계약을 고정한다:
- 결제 완료 주문을 주문 취소 버튼으로 취소하면 토스 환불 없이 주문만 취소됐다
- 이어서 결제 취소를 누르면 재고·포인트가 두 번 복구됐다
- 토스 승인 대기 중에 취소하면 결제 마감이 취소된 주문을 결제 완료로 되살렸다
- 결제 취소가 토스 환불을 먼저 하고 적립 포인트 회수 검사를 뒤에 해서, 거절될 때 돈만 나갔다
- 토스 취소 웹훅은 사용한 포인트를 돌려주지 않았다
- 처리 전(pending) 주문을 취소하면 아직 차감하지 않은 포인트까지 환불했다
"""

import pytest
from django.db.models import F
from django.urls import reverse
from rest_framework import status
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment, PaymentLog
from shopping.models.point import PointHistory
from shopping.models.product import Product
from shopping.services.order_service import OrderService
from shopping.services.payment_service import (
    PaymentCancelError,
    PaymentConfirmError,
    PaymentService,
)
from shopping.services.point_service import PointService
from shopping.services.toss_webhook_service import TossWebhookService
from shopping.tasks.payment_tasks import finalize_payment_confirm
from shopping.tests.factories import TossResponseBuilder

TOSS_CANCEL = "shopping.utils.toss_payment.TossPaymentClient.cancel_payment"
NOTIFY_DELAY = "shopping.tasks.payment_tasks.notify_payment_failure.delay"

SEED_POINTS = 5000
QTY = 2
USED = 1000
EARNED = 200


@pytest.fixture
def toss_cancel(mocker):
    """토스 취소(환불) API — 호출 횟수가 곧 환불 횟수다"""
    return mocker.patch(
        TOSS_CANCEL,
        side_effect=lambda payment_key, cancel_reason, **kw: (
            TossResponseBuilder.cancel_response(payment_key=payment_key, cancel_reason=cancel_reason)
        ),
    )


def _seed_points(user, amount=SEED_POINTS):
    """이전 구매로 적립된 포인트 (FIFO 로 쓰고 회수되는 적립 건)"""
    PointService.add_points(user=user, amount=amount, type="earn", description="이전 구매 적립")
    user.refresh_from_db()


def _processed_order(user, product, status="confirmed", used_points=USED):
    """주문 처리 태스크가 끝난 상태: 아이템 생성 + 재고 차감 + 포인트 차감(FIFO, 주문에 'use' 이력)"""
    order = Order.objects.create(
        user=user,
        status=status,
        total_amount=product.price * QTY,
        used_points=used_points,
        final_amount=product.price * QTY - used_points,
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
        quantity=QTY,
        price=product.price,
    )
    Product.objects.filter(pk=product.pk).update(stock=F("stock") - QTY)
    if used_points:
        result = PointService().use_points_fifo(user=user, amount=used_points, type="use", order=order)
        assert result["success"]
    return order


def _paid_order(user, product):
    """결제 마감 + 적립까지 끝난 상태: 판매량 증가, 결제 done, 주문 earned_points + 'earn' 이력"""
    order = _processed_order(user, product, status="paid")
    Product.objects.filter(pk=product.pk).update(sold_count=F("sold_count") + QTY)
    payment = Payment.objects.create(
        order=order,
        amount=order.final_amount,
        status="done",
        toss_order_id=str(order.id),
        payment_key=f"test_key_paid_{order.id}",
    )
    PointService.add_points(user=user, amount=EARNED, type="earn", order=order, description="결제 완료 적립")
    Order.objects.filter(pk=order.pk).update(earned_points=EARNED)
    order.refresh_from_db()
    return order, payment


def _snapshot(user, product):
    user.refresh_from_db()
    product.refresh_from_db()
    return {"points": user.points, "stock": product.stock, "sold": product.sold_count}


@pytest.mark.django_db
class TestPaidOrderThroughOrderCancelButton:
    """결제 완료 주문 — 주문 취소 버튼은 결제 취소(환불)로 간다"""

    def test_order_cancel_button_refunds_a_paid_order_once(self, authenticated_client, user, product, toss_cancel):
        _seed_points(user)
        before = _snapshot(user, product)
        order, payment = _paid_order(user, product)

        response = authenticated_client.post(reverse("order-cancel", kwargs={"pk": order.id}))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["refund_amount"] == int(payment.amount)
        toss_cancel.assert_called_once()
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == "canceled"
        assert order.status == "canceled"
        assert _snapshot(user, product) == before

    def test_payment_cancel_on_an_order_already_canceled_refunds_money_only(self, user, product, toss_cancel):
        """환불 없이 주문만 취소된 예전 데이터(재고·포인트는 이미 복구) — 돈만 돌려주고 두 번 복구하지 않는다"""
        _seed_points(user)
        before = _snapshot(user, product)
        order, payment = _paid_order(user, product)
        # 예전 주문 취소 버튼이 남긴 상태를 그대로 만든다
        Product.objects.filter(pk=product.pk).update(stock=F("stock") + QTY, sold_count=F("sold_count") - QTY)
        PointService.add_points(user=user, amount=USED, type="cancel_refund", order=order)
        PointService().use_points_fifo(user=user, amount=EARNED, type="cancel_deduct", order=order)
        Order.objects.filter(pk=order.pk).update(status="canceled")
        assert _snapshot(user, product) == before

        result = PaymentService.cancel_payment(payment_id=payment.id, user=user, cancel_reason="환불 누락 정정")

        toss_cancel.assert_called_once()
        assert result["points_refunded"] == 0 and result["points_deducted"] == 0
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert _snapshot(user, product) == before
        assert PointHistory.objects.filter(order=order, type="cancel_refund").count() == 1


@pytest.mark.django_db
class TestCancelWhileApprovalInFlight:
    """승인 중 취소 — 결제 울타리는 고객 버튼에도 있다"""

    def test_order_cancel_is_refused_while_payment_is_in_progress(self, authenticated_client, user, product):
        _seed_points(user)
        order = _processed_order(user, product)
        Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="in_progress",
            toss_order_id=str(order.id),
            payment_key="k_ip",
        )
        before = _snapshot(user, product)

        response = authenticated_client.post(reverse("order-cancel", kwargs={"pk": order.id}))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 승인이 진행 중" in response.data["error"]
        order.refresh_from_db()
        assert order.status == "confirmed"
        assert Payment.objects.get(order=order).status == "in_progress"
        assert _snapshot(user, product) == before

    def test_order_cancel_closes_an_open_payment_so_a_later_confirm_is_refused(self, authenticated_client, user, product):
        _seed_points(user)
        order = _processed_order(user, product)
        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="ready",
            toss_order_id=str(order.id),
            payment_key="",
        )

        response = authenticated_client.post(reverse("order-cancel", kwargs={"pk": order.id}))

        assert response.status_code == status.HTTP_200_OK
        payment.refresh_from_db()
        assert payment.status == "canceled"
        with pytest.raises(PaymentConfirmError):
            PaymentService.confirm_payment_async(payment, "late_key", order.id, int(payment.amount), user)

    def test_finalize_does_not_resurrect_a_canceled_order(self, user, product, mocker, django_capture_on_commit_callbacks):
        """울타리 밖의 경합으로 취소된 주문에 승인이 도착해도 결제 완료로 덮지 않는다 — 결제는 사실대로 done + 알림"""
        order = _processed_order(user, product, used_points=0)
        payment = Payment.objects.create(
            order=order,
            amount=order.final_amount,
            status="in_progress",
            toss_order_id=str(order.id),
            payment_key="k_race",
        )
        Product.objects.filter(pk=product.pk).update(stock=F("stock") + QTY)
        Order.objects.filter(pk=order.pk).update(status="canceled")
        before = _snapshot(user, product)
        notify = mocker.patch(NOTIFY_DELAY)

        toss_response = TossResponseBuilder.success_response(
            payment_key="k_race", order_id=str(order.id), amount=int(payment.amount)
        )
        with django_capture_on_commit_callbacks(execute=True):
            result = finalize_payment_confirm(toss_response, payment.id, user.id)

        assert result["status"] == "order_canceled"
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == "canceled"
        assert payment.status == "done"
        assert _snapshot(user, product) == before  # 판매량·적립 없음
        assert PaymentLog.objects.filter(payment=payment, log_type="error").exists()
        notify.assert_called_once()
        assert notify.call_args.args[1] == "canceled_order_charged"
        assert notify.call_args.kwargs["severity"] == "critical"


@pytest.mark.django_db
class TestValidateBeforeRefund:
    """되돌릴 수 없는 토스 환불 전에 검증을 끝낸다"""

    def test_payment_cancel_is_refused_before_the_refund_when_earned_points_are_spent(self, user, product, toss_cancel):
        _seed_points(user)
        order, payment = _paid_order(user, product)
        user.refresh_from_db()
        # 적립받은 포인트까지 전부 다른 주문에 씀
        assert PointService().use_points_fifo(user=user, amount=user.points, type="use")["success"]

        with pytest.raises(PaymentCancelError, match="포인트가 부족"):
            PaymentService.cancel_payment(payment_id=payment.id, user=user, cancel_reason="단순 변심")

        toss_cancel.assert_not_called()
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == "done"
        assert order.status == "paid"


@pytest.mark.django_db
class TestTossCancelWebhook:
    """토스 쪽 취소(대시보드·우리 롤백 뒤 도착) — 결제 취소 버튼과 같은 결과"""

    def test_cancel_webhook_refunds_used_points_once(self, user, product):
        _seed_points(user)
        before = _snapshot(user, product)
        order, payment = _paid_order(user, product)
        event = {
            "paymentKey": payment.payment_key,
            "orderId": payment.toss_order_id,
            "status": "CANCELED",
            "totalAmount": int(payment.amount),
            "cancels": [
                {
                    "cancelAmount": int(payment.amount),
                    "cancelReason": "관리자 취소",
                    "canceledAt": "2026-09-24T11:00:00+09:00",
                }
            ],
        }

        TossWebhookService.handle_payment_canceled(event)
        TossWebhookService.handle_payment_canceled(event)  # 재전송

        assert _snapshot(user, product) == before
        assert PointHistory.objects.filter(order=order, type="cancel_refund").count() == 1


@pytest.mark.django_db
class TestUnprocessedPendingOrder:
    """처리 전 주문 — 포인트는 주문 처리 태스크가 차감한다"""

    def test_cancel_before_processing_does_not_refund_points_never_deducted(self, user, product):
        _seed_points(user)
        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price,
            used_points=USED,
            final_amount=product.price - USED,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )
        before = _snapshot(user, product)

        OrderService.cancel_order(order)

        order.refresh_from_db()
        assert order.status == "canceled"
        assert _snapshot(user, product) == before
        assert not PointHistory.objects.filter(order=order, type="cancel_refund").exists()
