"""
웹훅 경로의 적립 포인트 회수 — 잔액과 적립 건별 사용량이 같이 움직여야 한다

토스 웹훅의 결제 취소(CANCELED)·입금 되돌림(DONE → WAITING_FOR_DEPOSIT)은 주문으로 적립한 포인트를 회수한다.
회수가 잔액(User.points)만 줄이고 적립 건의 used_amount 를 그대로 두면, 나중에 만료 배치가 그 적립의
'남은 양'을 또 만료시켜 같은 포인트를 잔액에서 한 번 더 뺀다. 다른 취소 경로(주문 취소·결제 취소 API)처럼
FIFO 로 회수해야 두 값이 맞는다.

고객이 적립 포인트를 이미 써 버렸으면 회수할 수 없다. 토스 쪽 취소는 이미 끝난 일이라 주문 상태는 토스를
따라가야 하고, 대신 회수하지 못한 포인트를 결제 로그에 남겨 사람이 볼 수 있게 한다.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.models.point import PointHistory
from shopping.services.point_service import PointService

PAYMENT_KEY = "reclaim_payment_key_001"
VA_SECRET = "ps_test_secret_reclaim"


@pytest.mark.django_db
class TestWebhookPointReclaim:
    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, order, payment, webhook_url, mock_get_payment, webhook_data_builder):
        self.client = api_client
        self.user = user
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url
        self.mock_get_payment = mock_get_payment
        self.build = webhook_data_builder

    def _post(self, status_value, **extra):
        body = self.build(
            status=status_value,
            order_id=str(self.order.id),
            payment_key=PAYMENT_KEY,
            amount=int(self.payment.amount),
            **extra,
        )
        self.mock_get_payment()
        response = self.client.post(self.webhook_url, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        return response

    def _pay(self, **extra):
        """DONE 웹훅으로 결제 완료 — 운영과 같은 경로로 적립 건(PointHistory earn)이 생긴다"""
        self._post("DONE", **extra)
        self.order.refresh_from_db()
        self.user.refresh_from_db()
        earned = self.order.earned_points
        assert earned > 0
        earn_row = PointHistory.objects.get(user=self.user, order=self.order, type="earn")
        return earned, earn_row

    def _expire_all_earn_rows(self):
        PointHistory.objects.filter(user=self.user, type="earn").update(expires_at=timezone.now() - timedelta(days=1))
        PointService().expire_points()
        self.user.refresh_from_db()

    def test_cancel_reclaims_earn_row_so_expiry_does_not_deduct_again(self):
        """결제 취소 웹훅 → 적립 건이 소진 처리되어, 만료 배치가 같은 포인트를 다시 빼지 않는다"""
        balance_before = self.user.points
        earned, earn_row = self._pay()

        self._post("CANCELED", cancel_reason="고객 요청")

        self.user.refresh_from_db()
        earn_row.refresh_from_db()
        assert self.user.points == balance_before
        assert PointService().get_remaining_points(earn_row) == 0

        self._expire_all_earn_rows()
        assert self.user.points == balance_before  # 수정 전: balance_before - earned

    def test_deposit_revert_reclaims_earn_row_so_expiry_does_not_deduct_again(self):
        """가상계좌 입금 되돌림 웹훅 → 같은 불변식"""
        issued = self.build(
            status="WAITING_FOR_DEPOSIT",
            order_id=str(self.order.id),
            payment_key=PAYMENT_KEY,
            amount=int(self.payment.amount),
            secret=VA_SECRET,
        )["data"]
        self.payment.mark_as_waiting_for_deposit(issued)
        balance_before = self.user.points
        earned, earn_row = self._pay(method="가상계좌")

        self._post("WAITING_FOR_DEPOSIT", secret=VA_SECRET)

        self.order.refresh_from_db()
        self.user.refresh_from_db()
        earn_row.refresh_from_db()
        assert self.order.status == "confirmed"
        assert self.order.earned_points == 0
        assert self.user.points == balance_before
        assert PointService().get_remaining_points(earn_row) == 0

        self._expire_all_earn_rows()
        assert self.user.points == balance_before

    def test_cancel_after_points_spent_still_cancels_and_records_unreclaimed(self):
        """적립 포인트를 이미 다 썼으면 회수는 못 하지만, 주문은 토스를 따라 취소되고 미회수가 기록된다"""
        earned, _ = self._pay()
        type(self.user).objects.filter(pk=self.user.pk).update(points=0)

        self._post("CANCELED", cancel_reason="고객 요청")

        self.order.refresh_from_db()
        self.user.refresh_from_db()
        assert self.order.status == "canceled"
        assert self.user.points == 0
        assert PaymentLog.objects.filter(payment=self.payment, message__contains="적립 포인트 미회수").exists()

    def test_deposit_revert_after_points_spent_keeps_earned_points_and_records_unreclaimed(self):
        """입금 되돌림에서 회수하지 못한 적립은 주문에 그대로 남긴다 — 0으로 지우면 기록이 사라진다"""
        issued = self.build(
            status="WAITING_FOR_DEPOSIT",
            order_id=str(self.order.id),
            payment_key=PAYMENT_KEY,
            amount=int(self.payment.amount),
            secret=VA_SECRET,
        )["data"]
        self.payment.mark_as_waiting_for_deposit(issued)
        earned, _ = self._pay(method="가상계좌")
        type(self.user).objects.filter(pk=self.user.pk).update(points=0)

        self._post("WAITING_FOR_DEPOSIT", secret=VA_SECRET)

        self.order.refresh_from_db()
        self.user.refresh_from_db()
        assert self.order.status == "confirmed"
        assert self.order.earned_points == earned
        assert self.user.points == 0
        assert PaymentLog.objects.filter(payment=self.payment, message__contains="적립 포인트 미회수").exists()
