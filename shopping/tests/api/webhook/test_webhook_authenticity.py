"""
웹훅 진위 확인 테스트 — 결제 조회 API 재확인

토스 PAYMENT_STATUS_CHANGED 웹훅에는 서명 헤더가 없다.
뷰는 본문을 신뢰하지 않고 paymentKey 로 결제 조회 API(TossPaymentClient.get_payment)를 호출해,
그 응답(현재 상태)으로 처리한다. 여기서는 그 계약을 검증한다:

- 토스가 모르는 paymentKey → 400 (재전송 불필요)
- 조회 실패(네트워크/5xx/키 설정) → 500 (토스가 재전송)
- 본문 orderId 와 토스 orderId 불일치 → 400
- 본문의 status/금액/카드정보는 무시되고 토스 응답이 쓰인다
- 조회는 토스의 10초 응답 제한보다 짧은 타임아웃으로 호출된다
"""

import pytest
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.utils.toss_payment import TossPaymentError
from shopping.webhooks.toss_webhook_view import WEBHOOK_LOOKUP_TIMEOUT


@pytest.mark.django_db
class TestWebhookLookupRejections:
    """토스 조회로 걸러지는 요청"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    def test_unknown_payment_key_rejected_with_400(
        self, mock_get_payment, webhook_data_builder
    ):
        """토스가 모르는 paymentKey (위조) → 400, DB 무변경"""
        # Arrange
        mock_get_payment()  # 레지스트리에 없는 키는 토스 404
        initial_order_status = self.order.status
        webhook_data = webhook_data_builder(order_id=str(self.order.id))
        webhook_data["data"]["paymentKey"] = "forged_payment_key"

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "Unknown payment"

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "ready"
        assert self.order.status == initial_order_status
        assert not PaymentLog.objects.filter(payment=self.payment).exists()

    @pytest.mark.parametrize(
        "error",
        [
            TossPaymentError(
                code="NETWORK_ERROR", message="네트워크 오류", status_code=500
            ),
            TossPaymentError(
                code="UNAUTHORIZED_KEY",
                message="인증되지 않은 시크릿 키",
                status_code=401,
            ),
            TossPaymentError(
                code="FAILED_INTERNAL_SYSTEM_PROCESSING",
                message="내부 오류",
                status_code=500,
            ),
        ],
        ids=["network", "our-key-misconfigured", "toss-5xx"],
    )
    def test_lookup_failure_returns_500_so_toss_retries(
        self, mock_get_payment, webhook_data_builder, error
    ):
        """조회 실패(404 이외)는 500 — 토스 재전송으로 복구, DB 무변경"""
        # Arrange
        mock_get_payment(error=error)
        webhook_data = webhook_data_builder(order_id=str(self.order.id))

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["error"] == "Payment lookup failed"

        self.payment.refresh_from_db()
        assert self.payment.status == "ready"

    def test_order_id_mismatch_rejected_with_400(
        self, mock_get_payment, webhook_data_builder
    ):
        """본문 orderId 가 토스 조회의 orderId 와 다르면 400 — 남의 결제로 내 주문을 결제 처리할 수 없다"""
        # Arrange
        toss_payment = webhook_data_builder(order_id="SOMEONE_ELSES_ORDER")["data"]
        mock_get_payment(payment=toss_payment)

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id)
        )  # 본문은 내 주문을 가리킴

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "orderId mismatch"

        self.payment.refresh_from_db()
        assert self.payment.status == "ready"

    def test_missing_payment_key_rejected_before_lookup(
        self, mock_get_payment, webhook_data_builder
    ):
        """paymentKey 가 없으면 조회조차 하지 않고 400"""
        # Arrange
        lookup = mock_get_payment()
        webhook_data = webhook_data_builder(order_id=str(self.order.id))
        del webhook_data["data"]["paymentKey"]

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        lookup.assert_not_called()


@pytest.mark.django_db
class TestWebhookBodyIsUntrusted:
    """본문이 아니라 토스 조회 응답이 처리 기준이다"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    def test_body_status_is_superseded_by_toss_status(
        self, mock_get_payment, webhook_data_builder
    ):
        """본문은 DONE 이라지만 토스는 CANCELED — 취소로 처리된다 (늦게 온 재전송 시나리오)"""
        # Arrange
        toss_payment = webhook_data_builder(
            status="CANCELED",
            order_id=str(self.order.id),
            cancel_reason="사용자 요청",
        )["data"]
        mock_get_payment(payment=toss_payment)

        webhook_data = webhook_data_builder(status="DONE", order_id=str(self.order.id))

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"
        assert self.payment.cancel_reason == "사용자 요청"

    def test_tampered_body_fields_do_not_reach_the_database(
        self, mock_get_payment, webhook_data_builder
    ):
        """본문의 paymentKey/카드정보/금액을 바꿔도 저장되는 것은 토스 응답"""
        # Arrange
        toss_payment = webhook_data_builder(
            order_id=str(self.order.id),
            payment_key="real_key_from_toss",
            amount=int(self.payment.amount),
            method="카드",
        )["data"]
        mock_get_payment(payment=toss_payment)

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            payment_key="real_key_from_toss",
            amount=1,
            method="가상계좌",
        )
        webhook_data["data"]["card"] = {
            "company": "가짜카드",
            "number": "0000****",
            "installmentPlanMonths": 12,
        }

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        assert self.payment.status == "done"
        assert self.payment.payment_key == "real_key_from_toss"
        assert self.payment.method == "카드"
        assert self.payment.card_company == "신한카드"
        assert self.payment.installment_plan_months == 0

        log = PaymentLog.objects.get(payment=self.payment, log_type="webhook")
        assert log.data["method"] == "카드"
        assert log.data["totalAmount"] == int(self.payment.amount)

    def test_lookup_uses_short_timeout(self, mock_get_payment, webhook_data_builder):
        """조회는 토스의 10초 응답 제한 안에 끝나야 하므로 짧은 타임아웃으로 호출한다"""
        # Arrange
        lookup = mock_get_payment()
        webhook_data = webhook_data_builder(order_id=str(self.order.id))

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert WEBHOOK_LOOKUP_TIMEOUT < 10
        lookup.assert_called_once_with(
            "test_payment_key_123", timeout=WEBHOOK_LOOKUP_TIMEOUT
        )

    def test_hostile_strings_in_body_are_harmless(
        self, mock_get_payment, webhook_data_builder
    ):
        """본문에 스크립트/SQL 문자열이 있어도 처리 기준은 토스 응답 — 200, 저장값은 토스 것"""
        # Arrange
        toss_payment = webhook_data_builder(order_id=str(self.order.id))["data"]
        mock_get_payment(payment=toss_payment)

        webhook_data = webhook_data_builder(order_id=str(self.order.id))
        webhook_data["data"]["method"] = "<script>alert('xss')</script>"
        webhook_data["data"]["orderName"] = "'; DROP TABLE shopping_payment; --"

        # Act
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        assert self.payment.status == "done"
        assert self.payment.method == "카드"
