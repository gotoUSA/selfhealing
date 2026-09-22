"""
가상계좌 웹훅 테스트 — 입금 대기 → 입금 완료 / 입금 취소 / 기한 만료

토스 가상계좌 흐름:
1. 승인 API 응답 status=WAITING_FOR_DEPOSIT (발급) — 여기서는 fixture 로 그 상태를 만든다
2. 입금 → DEPOSIT_CALLBACK (평평한 본문, secret 검증) 또는 PAYMENT_STATUS_CHANGED(DONE)
3. 송금 한도 초과 등 입금 오류 → DONE 이 WAITING_FOR_DEPOSIT 으로 되돌아옴
4. 입금 기한 경과 → EXPIRED → 롤백(재고 복구·주문 취소)

두 웹훅 모두 본문이 아니라 결제 조회 API 응답(mock_get_payment)으로 처리된다.
"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.payment import PaymentLog

VA_SECRET = "ps_test_secret_0001"
VA_PAYMENT_KEY = "va_payment_key_001"


@pytest.fixture
def waiting_payment(payment, webhook_data_builder):
    """가상계좌가 발급되어 입금 대기 중인 Payment (승인 응답을 그대로 기록한 상태)"""
    issued = webhook_data_builder(
        status="WAITING_FOR_DEPOSIT",
        order_id=str(payment.order.id),
        payment_key=VA_PAYMENT_KEY,
        amount=int(payment.amount),
        secret=VA_SECRET,
    )["data"]
    payment.mark_as_waiting_for_deposit(issued)
    payment.refresh_from_db()
    return payment


@pytest.mark.django_db
class TestDepositCallback:
    """DEPOSIT_CALLBACK — 평평한 본문 + secret 검증 + 조회 재확인"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, waiting_payment, webhook_url):
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = waiting_payment
        self.webhook_url = webhook_url

    def test_deposit_done_marks_paid_once(
        self, mock_get_payment, webhook_data_builder, deposit_callback_builder
    ):
        """입금 완료 → 토스 조회 DONE → 결제 완료·주문 paid·판매량·포인트 1회"""
        # Arrange — 토스는 이제 DONE 이라고 답한다
        webhook_data_builder(
            status="DONE",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
            method="가상계좌",
        )
        mock_get_payment()
        body = deposit_callback_builder(order_id=str(self.order.id), secret=VA_SECRET)

        # Act
        response = self.client.post(self.webhook_url, body, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        assert self.payment.status == "done"
        assert self.payment.method == "가상계좌"
        assert self.order.status == "paid"
        assert self.product.sold_count == 1
        assert self.product.stock == 9  # 재고는 주문 생성 때 이미 잡혀 있었다

        self.user.refresh_from_db()
        expected_points = int(self.order.total_amount * Decimal("0.01"))
        assert self.user.points == 5000 + expected_points

        # 같은 입금 웹훅이 다시 와도 두 번 반영되지 않는다
        response = self.client.post(self.webhook_url, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        self.product.refresh_from_db()
        self.user.refresh_from_db()
        assert self.product.sold_count == 1
        assert self.user.points == 5000 + expected_points

    def test_wrong_secret_rejected_without_lookup(
        self, mock_get_payment, deposit_callback_builder
    ):
        """secret 불일치 → 400, 토스 조회도 하지 않는다"""
        lookup = mock_get_payment()
        body = deposit_callback_builder(
            order_id=str(self.order.id), secret="forged_secret"
        )

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "Invalid secret"
        lookup.assert_not_called()
        self.payment.refresh_from_db()
        assert self.payment.status == "waiting_for_deposit"

    def test_unknown_order_rejected(self, mock_get_payment, deposit_callback_builder):
        """우리 DB에 없는 주문번호 → 400"""
        lookup = mock_get_payment()
        body = deposit_callback_builder(order_id="NO_SUCH_ORDER", secret=VA_SECRET)

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "Unknown order"
        lookup.assert_not_called()

    def test_callback_before_issue_rejected(
        self, mock_get_payment, deposit_callback_builder
    ):
        """아직 발급(paymentKey·secret) 전인 결제로 온 입금 웹훅 → 400"""
        self.payment.payment_key = None
        self.payment.toss_secret = ""
        self.payment.status = "ready"
        self.payment.save()
        mock_get_payment()
        body = deposit_callback_builder(order_id=str(self.order.id), secret=VA_SECRET)

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "Unknown order"

    def test_deposit_canceled_after_paid_restores_stock(
        self, mock_get_payment, webhook_data_builder, deposit_callback_builder
    ):
        """입금 후 가상계좌 결제 취소 → 조회 CANCELED → 취소 흐름 (재고 복구·주문 취소)"""
        # Arrange — 먼저 입금 완료로 만든다
        webhook_data_builder(
            status="DONE",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
        )
        mock_get_payment()
        self.client.post(
            self.webhook_url,
            deposit_callback_builder(order_id=str(self.order.id), secret=VA_SECRET),
            format="json",
        )
        self.product.refresh_from_db()
        assert self.product.sold_count == 1

        # 이제 토스는 CANCELED 라고 답한다
        webhook_data_builder(
            status="CANCELED",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
            cancel_reason="가상계좌 환불",
        )
        body = deposit_callback_builder(
            order_id=str(self.order.id), secret=VA_SECRET, status="CANCELED"
        )

        # Act
        response = self.client.post(self.webhook_url, body, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        assert self.payment.status == "canceled"
        assert self.payment.cancel_reason == "가상계좌 환불"
        assert self.order.status == "canceled"
        assert self.product.stock == 10
        assert self.product.sold_count == 0


@pytest.mark.django_db
class TestWaitingForDepositStatusChanged:
    """PAYMENT_STATUS_CHANGED 로 오는 가상계좌 상태 변화"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    def test_issue_webhook_before_finalize_records_waiting(
        self, mock_get_payment, webhook_data_builder
    ):
        """승인 마감보다 발급 웹훅이 먼저 오면 입금 대기로 기록 (계좌 정보·secret 저장)"""
        mock_get_payment()
        body = webhook_data_builder(
            status="WAITING_FOR_DEPOSIT",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
            secret=VA_SECRET,
        )

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "waiting_for_deposit"
        assert self.payment.payment_key == VA_PAYMENT_KEY
        assert self.payment.toss_secret == VA_SECRET
        assert self.payment.virtual_account_bank_code == "20"
        assert self.payment.virtual_account_number == "X6505636518308"
        assert self.payment.virtual_account_due_date is not None
        assert self.order.status == "confirmed"  # 입금 전 — 주문은 그대로
        self.product.refresh_from_db()
        assert self.product.sold_count == 0

        # 같은 웹훅 재전송은 no-op
        response = self.client.post(self.webhook_url, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert (
            PaymentLog.objects.filter(payment=self.payment, log_type="webhook").count()
            == 1
        )

    def test_deposit_reverted_unpays_order(
        self, mock_get_payment, webhook_data_builder
    ):
        """DONE 뒤 WAITING_FOR_DEPOSIT (입금 오류로 되돌림) → 판매량·적립 포인트 되돌리고 주문 confirmed"""
        # Arrange — 입금 완료 상태로
        mock_get_payment()
        self.client.post(
            self.webhook_url,
            webhook_data_builder(
                status="DONE",
                order_id=str(self.order.id),
                payment_key=VA_PAYMENT_KEY,
                amount=int(self.payment.amount),
                method="가상계좌",
            ),
            format="json",
        )
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        assert self.order.status == "paid"
        assert self.product.sold_count == 1
        earned = self.order.earned_points
        assert earned > 0

        # Act — 토스가 입금을 되돌렸다
        body = webhook_data_builder(
            status="WAITING_FOR_DEPOSIT",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
            secret=VA_SECRET,
        )
        response = self.client.post(self.webhook_url, body, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.user.refresh_from_db()
        assert self.payment.status == "waiting_for_deposit"
        assert self.order.status == "confirmed"
        assert self.order.earned_points == 0
        assert self.product.sold_count == 0
        assert self.product.stock == 9  # 재고는 계속 잡아 둔다 (다시 입금할 수 있다)
        assert self.user.points == 5000  # 적립 회수

    def test_expired_rolls_back_order(self, mock_get_payment, webhook_data_builder):
        """입금 기한 경과 → payment expired, 롤백 태스크가 재고 복구·주문 취소 (aborted 로 덮이지 않는다)"""
        # Arrange — 입금 대기 상태
        mock_get_payment()
        self.client.post(
            self.webhook_url,
            webhook_data_builder(
                status="WAITING_FOR_DEPOSIT",
                order_id=str(self.order.id),
                payment_key=VA_PAYMENT_KEY,
                amount=int(self.payment.amount),
                secret=VA_SECRET,
            ),
            format="json",
        )

        # Act — 기한 만료
        body = webhook_data_builder(
            status="EXPIRED",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
        )
        response = self.client.post(self.webhook_url, body, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        assert self.payment.status == "expired"
        assert "만료" in self.payment.fail_reason
        assert self.order.status == "payment_failed"  # rollback_payment_failure (eager)
        assert self.product.stock == 10

        # 만료 뒤 같은 웹훅 재전송은 no-op
        response = self.client.post(self.webhook_url, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        self.product.refresh_from_db()
        assert self.product.stock == 10

    def test_done_after_expiry_is_not_auto_processed(
        self, mock_get_payment, webhook_data_builder
    ):
        """만료 처리(재고 복구) 뒤에 입금이 잡히면 자동으로 되살리지 않고 에러 로그만 남긴다"""
        mock_get_payment()
        self.client.post(
            self.webhook_url,
            webhook_data_builder(
                status="EXPIRED",
                order_id=str(self.order.id),
                payment_key=VA_PAYMENT_KEY,
                amount=int(self.payment.amount),
            ),
            format="json",
        )
        self.payment.refresh_from_db()
        assert self.payment.status == "expired"

        body = webhook_data_builder(
            status="DONE",
            order_id=str(self.order.id),
            payment_key=VA_PAYMENT_KEY,
            amount=int(self.payment.amount),
        )
        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "expired"
        assert self.order.status == "payment_failed"
        assert PaymentLog.objects.filter(
            payment=self.payment, log_type="error", message__contains="수동 정산"
        ).exists()
