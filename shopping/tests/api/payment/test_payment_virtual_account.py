"""
가상계좌 승인 테스트 — 승인 응답 WAITING_FOR_DEPOSIT 은 "발급"이지 "입금"이 아니다

- 비동기 승인(/api/payments/confirm/ → finalize_payment_confirm): 입금 대기로만 기록, 판매량·주문·포인트 무변경
- 동기 승인(confirm_payment_sync): 같은 규칙
- 입금 대기 중 재승인 요청 차단
- 상태 폴링에 계좌 안내(virtual_account) 포함
- 대사(_reconcile_confirmed_payment)는 WAITING_FOR_DEPOSIT 도 "승인이 닿은" 상태로 인정
"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.cart import Cart
from shopping.models.payment import PaymentLog
from shopping.services.payment_service import PaymentConfirmError, PaymentService
from shopping.tasks.payment_tasks import _reconcile_confirmed_payment
from shopping.utils.toss_payment import TossPaymentError

VA_SECRET = "ps_test_secret_0001"


def _va_issue_response(order_id, amount, payment_key="va_key_001"):
    """토스 가상계좌 승인 응답 (status=WAITING_FOR_DEPOSIT)"""
    return {
        "status": "WAITING_FOR_DEPOSIT",
        "paymentKey": payment_key,
        "orderId": str(order_id),
        "totalAmount": amount,
        "method": "가상계좌",
        "approvedAt": None,
        "secret": VA_SECRET,
        "virtualAccount": {
            "accountType": "일반",
            "accountNumber": "X6505636518308",
            "bankCode": "20",
            "customerName": "홍길동",
            "dueDate": "2025-01-22T23:59:59+09:00",
            "refundStatus": "NONE",
            "expired": False,
            "settlementStatus": "INCOMPLETED",
        },
    }


@pytest.mark.django_db
class TestVirtualAccountConfirm:
    """승인 응답이 입금 대기이면 결제 완료 처리하지 않는다"""

    def test_async_confirm_records_waiting_only(
        self, authenticated_client, user, order, payment, product, mocker
    ):
        """비동기 승인: payment=waiting_for_deposit, 계좌·secret 저장, 주문·판매량·포인트·장바구니 무변경"""
        # Arrange
        cart = Cart.objects.create(user=user, is_active=True)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=_va_issue_response(order.id, int(payment.amount)),
        )

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            {
                "order_id": order.id,
                "payment_key": "va_key_001",
                "amount": int(payment.amount),
            },
            format="json",
        )

        # Assert — 응답 계약은 그대로 202
        assert response.status_code == status.HTTP_202_ACCEPTED

        payment.refresh_from_db()
        assert payment.status == "waiting_for_deposit"
        assert payment.is_paid is False
        assert payment.is_waiting_for_deposit is True
        assert payment.payment_key == "va_key_001"
        assert payment.method == "가상계좌"
        assert payment.toss_secret == VA_SECRET
        assert payment.virtual_account_bank_code == "20"
        assert payment.virtual_account_number == "X6505636518308"
        assert payment.virtual_account_due_date is not None

        order.refresh_from_db()
        product.refresh_from_db()
        user.refresh_from_db()
        cart.refresh_from_db()
        assert order.status == "confirmed"  # 입금 전
        assert order.earned_points == 0
        assert product.sold_count == 0
        assert user.points == 5000
        assert cart.is_active is True  # 장바구니도 아직 그대로

        log = PaymentLog.objects.get(payment=payment, log_type="approve")
        assert "가상계좌 발급" in log.message

    def test_sync_confirm_records_waiting_only(
        self, user, order, payment, product, mocker
    ):
        """동기 승인(테스트 페이지 경로)도 같은 규칙"""
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=_va_issue_response(order.id, int(payment.amount)),
        )

        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="va_key_001",
            order_id=order.id,
            amount=int(payment.amount),
            user=user,
        )

        assert result["waiting_for_deposit"] is True
        assert result["points_earned"] == 0
        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()
        assert payment.status == "waiting_for_deposit"
        assert order.status == "confirmed"
        assert product.sold_count == 0

    def test_reconfirm_while_waiting_is_rejected(
        self, authenticated_client, user, order, payment, mocker
    ):
        """입금 대기 중 승인 API 재호출 → 400, 토스 승인 API 호출 없음"""
        payment.mark_as_waiting_for_deposit(
            _va_issue_response(order.id, int(payment.amount))
        )
        confirm = mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment"
        )

        response = authenticated_client.post(
            "/api/payments/confirm/",
            {
                "order_id": order.id,
                "payment_key": "va_key_001",
                "amount": int(payment.amount),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "입금 대기" in response.json()["error"]
        confirm.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == "waiting_for_deposit"

        with pytest.raises(PaymentConfirmError):
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="va_key_001",
                order_id=order.id,
                amount=int(payment.amount),
                user=user,
            )

    def test_status_endpoint_exposes_virtual_account(
        self, authenticated_client, order, payment
    ):
        """폴링 응답: 입금 대기면 virtual_account 블록, 아니면 없음"""
        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")
        assert response.status_code == status.HTTP_200_OK
        assert "virtual_account" not in response.data

        payment.mark_as_waiting_for_deposit(
            _va_issue_response(order.id, int(payment.amount))
        )

        response = authenticated_client.get(f"/api/payments/{payment.id}/status/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "waiting_for_deposit"
        assert response.data["is_paid"] is False
        assert response.data["virtual_account"]["bank_code"] == "20"
        assert response.data["virtual_account"]["account_number"] == "X6505636518308"
        assert response.data["virtual_account"]["due_date"] is not None

    def test_reconcile_accepts_waiting_for_deposit(self, order, payment, mocker):
        """ALREADY_PROCESSED 뒤 조회가 WAITING_FOR_DEPOSIT 이면 롤백이 아니라 발급으로 마감한다"""
        lookup = _va_issue_response(order.id, int(payment.amount))
        toss_client = mocker.Mock()
        toss_client.get_payment.return_value = lookup
        task = mocker.Mock()
        error = TossPaymentError(
            code="ALREADY_PROCESSED_PAYMENT",
            message="이미 처리된 결제",
            status_code=400,
        )

        result = _reconcile_confirmed_payment(
            task, toss_client, "va_key_001", order.id, int(payment.amount), error
        )

        assert result is lookup
        task.retry.assert_not_called()

    def test_expiry_batch_skips_waiting_for_deposit(self, order, payment, product):
        """미결제 주문 만료 배치는 입금 대기 주문을 건너뛴다 (입금 기한은 토스가 관리)"""
        from datetime import timedelta

        from django.utils import timezone

        from shopping.models.order import Order
        from shopping.tasks.order_tasks import expire_unpaid_orders

        payment.mark_as_waiting_for_deposit(
            _va_issue_response(order.id, int(payment.amount))
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )

        expire_unpaid_orders(timeout_minutes=30)

        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == "confirmed"
        assert product.stock == 9
        assert Decimal(product.stock) == Decimal(9)
