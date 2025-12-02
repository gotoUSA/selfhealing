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
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """완전한 데이터 구조 검증"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(
            event_type="PAYMENT.DONE",
            order_id="ORDER_001",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_empty_data_object(
        self, mock_verify_webhook, webhook_signature
    ):
        """빈 data 객체"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_missing_event_type_field(
        self, mock_verify_webhook, webhook_signature
    ):
        """eventType 필드 누락"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "eventType" in response.json()

    def test_missing_data_field(
        self, mock_verify_webhook, webhook_signature
    ):
        """data 필드 누락"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT.DONE",
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.json()

    def test_both_fields_missing(
        self, mock_verify_webhook, webhook_signature
    ):
        """모든 필수 필드 누락"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {}

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
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
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """PAYMENT.DONE 이벤트 허용"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(event_type="PAYMENT.DONE")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    def test_payment_canceled_event_accepted(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """PAYMENT.CANCELED 이벤트 허용"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(event_type="PAYMENT.CANCELED")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    def test_payment_failed_event_accepted(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """PAYMENT.FAILED 이벤트 허용"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(event_type="PAYMENT.FAILED")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_partial_canceled_event_ignored(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """PAYMENT.PARTIAL_CANCELED 이벤트 무시 (향후 지원)"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(
            event_type="PAYMENT.PARTIAL_CANCELED"
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_unknown_event_type_ignored(
        self, mock_verify_webhook, webhook_signature
    ):
        """지원하지 않는 이벤트 타입 무시"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT.UNKNOWN_EVENT",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Event ignored"

    def test_empty_event_type(
        self, mock_verify_webhook, webhook_signature
    ):
        """빈 문자열 eventType - serializer에서 거부"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "eventType" in response.json()

    def test_malformed_event_type(
        self, mock_verify_webhook, webhook_signature
    ):
        """잘못된 형식의 eventType"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT-DONE",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
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
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """POST 메서드 허용"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
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
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """성공 응답 형식"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(event_type="PAYMENT.DONE")

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조
        response_data = response.json()
        assert "message" in response_data
        assert response_data["message"] == "Webhook processed"

    def test_ignored_event_response_format(
        self, mock_verify_webhook, webhook_signature
    ):
        """무시된 이벤트 응답 형식"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT.UNKNOWN",
            "data": {"orderId": "ORDER_001"},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
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

    def test_missing_signature_response_format(
        self, webhook_data_builder
    ):
        """서명 누락 시 응답 형식"""
        # Arrange
        webhook_data = webhook_data_builder()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Assert - 응답 구조
        response_data = response.json()
        assert "error" in response_data
        assert response_data["error"] == "Signature missing"

    def test_invalid_signature_response_format(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """잘못된 서명 시 응답 형식"""
        # Arrange
        mock_verify_webhook(return_value=False)
        webhook_data = webhook_data_builder()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Assert - 응답 구조
        response_data = response.json()
        assert "error" in response_data
        assert response_data["error"] == "Invalid signature"

    def test_invalid_data_response_format(
        self, mock_verify_webhook, webhook_signature
    ):
        """잘못된 데이터 시 응답 형식"""
        # Arrange
        mock_verify_webhook()
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            # data 필드 누락
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 코드
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Assert - 응답 구조 (serializer errors)
        response_data = response.json()
        assert "data" in response_data
