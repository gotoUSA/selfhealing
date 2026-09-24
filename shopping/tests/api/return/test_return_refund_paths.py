"""
배송 뒤 돈을 돌려주는 경로(반품 환불) — 낸 방식대로, 한 번씩만

2026-09-24 실제 스택 검증에서 나온 것:
- 반품 환불이 상품값 전체를 현금으로 돌려주면서 사용 포인트도 따로 전액 환불 — 포인트로 낸 몫이 두 번 환불됐다
- 부분 반품인데 사용 포인트 전액 환불·적립 포인트 전액 회수·주문 전체 refunded — 나머지 상품은 반품 불가
- 토스 환불을 먼저 하고 적립 포인트 회수 검사를 뒤에 해서, 거절될 때마다 돈만 나갔다(판매자가 재시도할 때마다 또)
- 부분 반품 뒤 결제 취소 API가 남은 금액까지 환불하고 재고·포인트를 한 번 더 돌려줬다
- 배송 완료 주문에 결제 취소 API가 통해서 상품을 가진 채 전액 환불됐다
"""

from decimal import Decimal

import pytest
from django.db.models import F
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.point import PointHistory
from shopping.models.product import Product
from shopping.models.return_request import Return, ReturnItem
from shopping.services.payment_service import PaymentCancelError, PaymentService
from shopping.services.point_service import PointService
from shopping.services.return_service import ReturnService
from shopping.tests.factories import TossResponseBuilder

TOSS_CANCEL = "shopping.utils.toss_payment.TossPaymentClient.cancel_payment"
PRICE = Decimal("10000")
QTY = 2
USED = 1000
EARNED = 200
SHIPPING = Decimal("3000")


@pytest.fixture
def toss_cancel(mocker):
    return mocker.patch(
        TOSS_CANCEL,
        side_effect=lambda payment_key, cancel_reason, **kw: (
            TossResponseBuilder.cancel_response(payment_key=payment_key, cancel_reason=cancel_reason)
        ),
    )


def _delivered_order(user, product, method="카드"):
    """결제 마감·적립·배송까지 끝난 주문: 상품 20,000 + 배송비 3,000 - 포인트 1,000 = 현금 22,000"""
    PointService.add_points(user=user, amount=5000, type="earn", description="이전 구매 적립")
    order = Order.objects.create(
        user=user,
        status="delivered",
        total_amount=PRICE * QTY,
        shipping_fee=SHIPPING,
        used_points=USED,
        final_amount=PRICE * QTY + SHIPPING - USED,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구",
        shipping_address_detail="101동",
    )
    item = OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=QTY,
        price=PRICE,
    )
    Product.objects.filter(pk=product.pk).update(stock=F("stock") - QTY, sold_count=F("sold_count") + QTY)
    assert PointService().use_points_fifo(user=user, amount=USED, type="use", order=order)["success"]
    payment = Payment.objects.create(
        order=order,
        amount=order.final_amount,
        status="done",
        method=method,
        toss_order_id=str(order.id),
        payment_key=f"k_ret_{order.id}",
    )
    PointService.add_points(user=user, amount=EARNED, type="earn", order=order, description="결제 완료 적립")
    Order.objects.filter(pk=order.pk).update(earned_points=EARNED)
    order.refresh_from_db()
    user.refresh_from_db()
    product.refresh_from_db()
    return order, item, payment


def _received_return(order, user, item, qty):
    """고객 신청 → 판매자 승인 → 반품 도착까지 (금액 계산은 서비스 그대로)"""
    ret = Return.objects.create(
        order=order,
        user=user,
        type="refund",
        reason="change_of_mind",
        reason_detail="S6",
        status="received",
        return_number=f"RET-S6-{order.id}-{Return.objects.count()}",
        refund_account_bank="004",
        refund_account_number="12345678901234",
        refund_account_holder="홍길동",
    )
    ReturnItem.objects.create(
        return_request=ret,
        order_item=item,
        quantity=qty,
        product_name=item.product_name,
        product_price=item.price,
    )
    ret.refund_amount = ReturnService.calculate_refund_amount(ret.return_items.all())
    ret.save()
    return ret


def _points(order, type_):
    return sum(PointHistory.objects.filter(order=order, type=type_).values_list("points", flat=True))


@pytest.mark.django_db
class TestReturnRefundAmounts:
    def test_full_return_refunds_what_was_paid_for_the_goods(self, user, product, toss_cancel):
        """상품 20,000 = 현금 19,000 + 포인트 1,000 → 현금 19,000·포인트 1,000 (예전: 현금 20,000 + 포인트 1,000)"""
        order, item, payment = _delivered_order(user, product)
        stock, sold = product.stock, product.sold_count

        ReturnService.complete_refund(_received_return(order, user, item, QTY))

        assert toss_cancel.call_args.kwargs["cancel_amount"] == 19000
        assert _points(order, "cancel_refund") == USED
        assert _points(order, "cancel_deduct") == -EARNED
        product.refresh_from_db()
        order.refresh_from_db()
        payment.refresh_from_db()
        assert (product.stock, product.sold_count) == (stock + QTY, sold - QTY)
        assert order.status == "refunded"
        assert payment.status == "partial_canceled"  # 배송비 3,000 은 남는다
        assert payment.canceled_amount == 19000

    def test_card_refund_does_not_send_the_customer_account_to_toss(self, user, product, toss_cancel):
        """토스: 환불 계좌는 가상계좌 결제에만 — 카드 반품에 고객 계좌번호를 보내지 않는다"""
        order, item, _ = _delivered_order(user, product)
        ReturnService.complete_refund(_received_return(order, user, item, 1))
        assert toss_cancel.call_args.kwargs["refund_account"] is None

    def test_refund_carries_an_idempotency_key_per_return(self, user, product, toss_cancel):
        """환불 뒤 우리 쪽이 실패해 판매자가 다시 눌러도 토스는 같은 키면 두 번 환불하지 않는다"""
        order, item, _ = _delivered_order(user, product)
        ret = _received_return(order, user, item, 1)
        ReturnService.complete_refund(ret)
        assert toss_cancel.call_args.kwargs["idempotency_key"] == f"return-refund-{ret.id}"


@pytest.mark.django_db
class TestValidateBeforeReturnRefund:
    def test_spent_earned_points_are_refused_before_any_refund(self, user, product, toss_cancel):
        order, item, payment = _delivered_order(user, product)
        user.refresh_from_db()
        assert PointService().use_points_fifo(user=user, amount=user.points, type="use")["success"]
        ret = _received_return(order, user, item, 1)

        with pytest.raises(ValueError, match="유효한 포인트가 부족"):
            ReturnService.complete_refund(ret)
        with pytest.raises(ValueError, match="유효한 포인트가 부족"):
            ReturnService.complete_refund(ret)  # 판매자 재시도

        toss_cancel.assert_not_called()
        payment.refresh_from_db()
        ret.refresh_from_db()
        assert payment.status == "done"
        assert ret.status == "received"


@pytest.mark.django_db
class TestPaymentCancelAfterDelivery:
    def test_payment_cancel_is_refused_for_a_delivered_order(self, user, product, toss_cancel):
        """상품이 고객에게 있는 주문의 돈은 반품으로만 돌려준다"""
        order, _, payment = _delivered_order(user, product)
        with pytest.raises(PaymentCancelError, match="반품"):
            PaymentService.cancel_payment(payment_id=payment.id, user=user, cancel_reason="변심")
        toss_cancel.assert_not_called()

    def test_payment_cancel_after_a_partial_return_is_refused(self, user, product, toss_cancel):
        order, item, payment = _delivered_order(user, product)
        ReturnService.complete_refund(_received_return(order, user, item, 1))
        product.refresh_from_db()
        stock = product.stock
        toss_cancel.reset_mock()

        with pytest.raises(PaymentCancelError):
            PaymentService.cancel_payment(payment_id=payment.id, user=user, cancel_reason="변심")

        toss_cancel.assert_not_called()
        product.refresh_from_db()
        assert product.stock == stock
