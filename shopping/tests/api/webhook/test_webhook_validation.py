"""
웹훅 데이터 검증 및 HTTP 메서드 검증 테스트

토스페이먼츠 웹훅의 요청 데이터 구조, 이벤트 타입, HTTP 메서드, 응답 형식 검증
비즈니스 로직 테스트는 별도 파일에서 수행
"""

import pytest
from rest_framework import status


@pytest.mark.django_db
class TestWebhookRequestDataValidation:
    """웹훅 요청 데이터 구조 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_valid_webhook_data_structure(
        self, mock_get_payment, webhook_data_builder
    ):
        """완전한 데이터 구조 검증"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="DONE",
            order_id="ORDER_001",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_empty_data_object(
        self, mock_get_payment
    ):
        """빈 data 객체 — paymentKey/orderId 가 없어 400"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "paymentKey and orderId are required"

    def test_data_must_be_an_object(
        self, mock_get_payment
    ):
        """data 가 객체가 아니면 serializer 에서 거부"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": ["not", "an", "object"],
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.json()

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_missing_event_type_field(
        self, mock_get_payment
    ):
        """eventType 필드 누락"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "eventType" in response.json()

    def test_missing_data_field(
        self, mock_get_payment
    ):
        """data 필드 누락"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.json()

    def test_both_fields_missing(
        self, mock_get_payment
    ):
        """모든 필수 필드 누락"""
        # Arrange
        mock_get_payment()
        webhook_data = {}

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestWebhookEventTypeValidation:
    """웹훅 이벤트 타입 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_payment_done_event_accepted(
        self, mock_get_payment, webhook_data_builder
    ):
        """PAYMENT_STATUS_CHANGED · status=DONE 처리"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(status="DONE")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    def test_payment_canceled_event_accepted(
        self, mock_get_payment, webhook_data_builder
    ):
        """PAYMENT_STATUS_CHANGED · status=CANCELED 처리"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(status="CANCELED")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    def test_payment_failed_event_accepted(
        self, mock_get_payment, webhook_data_builder
    ):
        """PAYMENT_STATUS_CHANGED · status=ABORTED 처리"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(status="ABORTED")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_partial_canceled_event_ignored(
        self, mock_get_payment, webhook_data_builder
    ):
        """status=PARTIAL_CANCELED 는 200 으로 무시 (미지원)"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="PARTIAL_CANCELED"
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_unknown_event_type_ignored(
        self, mock_get_payment
    ):
        """지원하지 않는 이벤트 타입 무시 — 토스 조회도 하지 않는다"""
        # Arrange
        lookup = mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT.UNKNOWN_EVENT",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Event ignored"
        lookup.assert_not_called()

    @pytest.mark.parametrize(
        "event_type",
        ["DEPOSIT_CALLBACK", "CANCEL_STATUS_CHANGED", "METHOD_UPDATED", "CUSTOMER_STATUS_CHANGED", "BILLING_DELETED"],
    )
    def test_other_toss_event_types_ignored(self, mock_get_payment, event_type):
        """토스의 다른 웹훅 이벤트는 200 으로 무시 (PAYMENT_STATUS_CHANGED 만 처리)"""
        # Arrange
        lookup = mock_get_payment()
        webhook_data = {
            "eventType": event_type,
            "createdAt": "2025-01-15T10:00:01.000000+09:00",
            "data": {"orderId": "ORDER_001", "paymentKey": "test_payment_key_123", "status": "DONE"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Event ignored"
        lookup.assert_not_called()

    def test_empty_event_type(
        self, mock_get_payment
    ):
        """빈 문자열 eventType - serializer에서 거부"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "eventType" in response.json()

    def test_malformed_event_type(
        self, mock_get_payment
    ):
        """잘못된 형식의 eventType"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT-DONE",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Event ignored"


@pytest.mark.django_db
class TestWebhookHttpMethodValidation:
    """웹훅 HTTP 메서드 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_post_method_allowed(
        self, mock_get_payment, webhook_data_builder
    ):
        """POST 메서드 허용"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_get_method_rejected(self):
        """GET 메서드 거부"""
        # Act
        response = self.client.get(self.webhook_url)

        # Assert
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_put_method_rejected(self):
        """PUT 메서드 거부"""
        # Act
        response = self.client.put(
            self.webhook_url,
            {},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_patch_method_rejected(self):
        """PATCH 메서드 거부"""
        # Act
        response = self.client.patch(
            self.webhook_url,
            {},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_delete_method_rejected(self):
        """DELETE 메서드 거부"""
        # Act
        response = self.client.delete(self.webhook_url)

        # Assert
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.django_db
class TestWebhookResponseFormat:
    """웹훅 응답 형식 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_success_response_format(
        self, mock_get_payment, webhook_data_builder
    ):
        """성공 응답 형식"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(status="DONE")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조
        response_data = response.json()
        assert "message" in response_data
        assert response_data["message"] == "Webhook processed"

    def test_ignored_event_response_format(
        self, mock_get_payment
    ):
        """무시된 이벤트 응답 형식"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT.UNKNOWN",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조
        response_data = response.json()
        assert "message" in response_data
        assert response_data["message"] == "Event ignored"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_unknown_payment_response_format(
        self, mock_get_payment, webhook_data_builder
    ):
        """토스가 모르는 paymentKey 일 때 응답 형식"""
        # Arrange
        mock_get_payment()  # 레지스트리에 등록되지 않은 키 → 토스 404
        webhook_data = webhook_data_builder()
        webhook_data["data"]["paymentKey"] = "forged_payment_key"

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Assert - 응답 구조
        response_data = response.json()
        assert "error" in response_data
        assert response_data["error"] == "Unknown payment"

    def test_lookup_failure_response_format(
        self, mock_get_payment, webhook_data_builder
    ):
        """토스 결제 조회 실패(네트워크) 시 응답 형식 — 500 으로 재전송을 받는다"""
        # Arrange
        from shopping.utils.toss_payment import TossPaymentError

        mock_get_payment(error=TossPaymentError(code="NETWORK_ERROR", message="네트워크 오류", status_code=500))
        webhook_data = webhook_data_builder()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        # Assert - 응답 구조
        response_data = response.json()
        assert "error" in response_data
        assert response_data["error"] == "Payment lookup failed"

    def test_invalid_data_response_format(
        self, mock_get_payment
    ):
        """잘못된 데이터 시 응답 형식"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            # data 필드 누락
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Assert - 응답 구조 (serializer errors)
        response_data = response.json()
        assert "data" in response_data
