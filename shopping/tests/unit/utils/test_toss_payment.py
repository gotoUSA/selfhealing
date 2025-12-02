from decimal import Decimal
from unittest.mock import Mock

from django.conf import settings

import pytest
import requests
from rest_framework import status

from shopping.utils.toss_payment import TOSS_ERROR_MESSAGES, TossPaymentClient, TossPaymentError, get_error_message


@pytest.mark.django_db
class TestTossPaymentClientInitialization:
    """TossPaymentClient 초기화 테스트"""

    def test_client_initialization_success(self):
        """정상적인 클라이언트 초기화"""
        client = TossPaymentClient()

        # settings에서 값이 제대로 로드되었는지 확인
        assert client.secret_key == settings.TOSS_SECRET_KEY
        assert client.client_key == settings.TOSS_CLIENT_KEY
        assert client.base_url == settings.TOSS_BASE_URL

        # 헤더가 제대로 설정되었는지 확인
        assert "Authorization" in client.headers
        assert client.headers["Authorization"].startswith("Basic ")
        assert client.headers["Content-Type"] == "application/json"

    def test_client_headers_format(self):
        """Basic Auth 헤더 형식 검증"""
        client = TossPaymentClient()

        # Basic 인증 헤더는 'Basic base64(secret_key:)' 형식
        auth_header = client.headers["Authorization"]
        assert auth_header.startswith("Basic ")

        # Base64로 인코딩된 부분이 있어야 함
        encoded_part = auth_header.split("Basic ")[1]
        assert len(encoded_part) > 0


@pytest.mark.django_db
class TestConfirmPayment:
    """결제 승인 API 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()

    def test_confirm_payment_success(self, mocker):
        """정상적인 결제 승인"""
        # Mock 응답 데이터
        mock_response_data = {
            "paymentKey": "test_payment_key_123",
            "orderId": "ORDER_20250115_001",
            "status": "DONE",
            "totalAmount": 13000,
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
            "card": {
                "company": "신한카드",
                "number": "1234****",
                "installmentPlanMonths": 0,
                "isInterestFree": False,
            },
        }

        # requests.post를 Mock으로 대체
        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = mock_response_data
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # 결제 승인 요청
        result = self.client.confirm_payment(
            payment_key="test_payment_key_123",
            order_id="ORDER_20250115_001",
            amount=13000,
        )

        # 결과 검증
        assert result == mock_response_data
        assert result["status"] == "DONE"
        assert result["totalAmount"] == 13000

        # API 호출 검증
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["paymentKey"] == "test_payment_key_123"
        assert call_kwargs["json"]["orderId"] == "ORDER_20250115_001"
        assert call_kwargs["json"]["amount"] == 13000

    def test_confirm_payment_api_error(self, mocker):
        """토스 API 에러 응답 (400, 401 등)"""
        # Mock 에러 응답
        mock_error_data = {
            "code": "ALREADY_PROCESSED_PAYMENT",
            "message": "이미 처리된 결제입니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_400_BAD_REQUEST
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        # TossPaymentError 발생 확인
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment(
                payment_key="test_key",
                order_id="ORDER_001",
                amount=10000,
            )

        # 에러 내용 검증
        error = exc_info.value
        assert error.code == "ALREADY_PROCESSED_PAYMENT"
        assert error.message == "이미 처리된 결제입니다."
        assert error.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_payment_network_error(self, mocker):
        """네트워크 오류 처리"""
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        )

        # TossPaymentError 발생 확인
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment(
                payment_key="test_key",
                order_id="ORDER_001",
                amount=10000,
            )

        # 에러 내용 검증
        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert "네트워크 오류" in error.message
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_confirm_payment_timeout_error(self, mocker):
        """요청 타임아웃 처리"""
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.Timeout("Request timeout"),
        )

        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment(
                payment_key="test_key",
                order_id="ORDER_001",
                amount=10000,
            )

        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_confirm_payment_decimal_amount_conversion(self, mocker):
        """Decimal 금액이 int로 변환되는지 확인"""
        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = {
            "paymentKey": "test_key",
            "orderId": "ORDER_001",
            "status": "DONE",
            "totalAmount": 13000,
        }
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Decimal 타입으로 금액 전달
        self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=Decimal("13000.00"),
        )

        # JSON 데이터에서 amount가 int로 변환되었는지 확인
        call_kwargs = mock_post.call_args[1]
        assert isinstance(call_kwargs["json"]["amount"], int)
        assert call_kwargs["json"]["amount"] == 13000


@pytest.mark.django_db
class TestCancelPayment:
    """결제 취소 API 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()

    def test_cancel_payment_success(self, mocker):
        """정상적인 결제 전체 취소"""
        mock_response_data = {
            "paymentKey": "test_payment_key_123",
            "orderId": "ORDER_20250115_001",
            "status": "CANCELED",
            "canceledAmount": 13000,
            "cancelReason": "고객 변심",
            "canceledAt": "2025-01-15T11:00:00+09:00",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = mock_response_data
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # 결제 취소 요청
        result = self.client.cancel_payment(
            payment_key="test_payment_key_123",
            cancel_reason="고객 변심",
        )

        # 결과 검증
        assert result == mock_response_data
        assert result["status"] == "CANCELED"
        assert result["cancelReason"] == "고객 변심"

        # API 호출 검증
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["cancelReason"] == "고객 변심"

    def test_cancel_payment_with_partial_amount(self, mocker):
        """부분 취소 (cancel_amount 지정)"""
        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = {"status": "CANCELED"}
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # 부분 취소 요청
        self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="부분 취소",
            cancel_amount=5000,
        )

        # cancelAmount가 전달되었는지 확인
        call_kwargs = mock_post.call_args[1]
        assert "cancelAmount" in call_kwargs["json"]
        assert call_kwargs["json"]["cancelAmount"] == 5000

    def test_cancel_payment_with_refund_account(self, mocker):
        """환불 계좌 정보 포함 (가상계좌 환불)"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "CANCELED"}
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        refund_account = {
            "bank": "신한은행",
            "accountNumber": "123456789",
            "holderName": "홍길동",
        }

        # 환불 계좌 정보 포함 취소
        self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="가상계좌 환불",
            refund_account=refund_account,
        )

        # refundReceiveAccount가 전달되었는지 확인
        call_kwargs = mock_post.call_args[1]
        assert "refundReceiveAccount" in call_kwargs["json"]
        assert call_kwargs["json"]["refundReceiveAccount"] == refund_account

    def test_cancel_payment_api_error(self, mocker):
        """취소 API 에러 응답"""
        mock_error_data = {
            "code": "INVALID_REQUEST",
            "message": "잘못된 요청입니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_400_BAD_REQUEST
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment(
                payment_key="test_key",
                cancel_reason="테스트 취소",
            )

        error = exc_info.value
        assert error.code == "INVALID_REQUEST"
        assert error.status_code == status.HTTP_400_BAD_REQUEST

    def test_cancel_payment_network_error(self, mocker):
        """취소 요청 네트워크 에러"""
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.RequestException("Network error"),
        )

        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment(
                payment_key="test_key",
                cancel_reason="테스트 취소",
            )

        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.django_db
class TestGetPayment:
    """결제 조회 API 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()

    def test_get_payment_success(self, mocker):
        """정상적인 결제 조회"""
        mock_response_data = {
            "paymentKey": "test_payment_key_123",
            "orderId": "ORDER_20250115_001",
            "status": "DONE",
            "totalAmount": 13000,
            "method": "카드",
            "approvedAt": "2025-01-15T10:00:00+09:00",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = mock_response_data
        mock_get = mocker.patch("requests.get", return_value=mock_response)

        # 결제 조회 요청
        result = self.client.get_payment(payment_key="test_payment_key_123")

        # 결과 검증
        assert result == mock_response_data
        assert result["status"] == "DONE"

        # API 호출 검증
        mock_get.assert_called_once()
        assert "test_payment_key_123" in mock_get.call_args[0][0]

    def test_get_payment_not_found(self, mocker):
        """존재하지 않는 결제 조회"""
        mock_error_data = {
            "code": "NOT_FOUND_PAYMENT",
            "message": "결제 정보를 찾을 수 없습니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_404_NOT_FOUND
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.get", return_value=mock_response)

        with pytest.raises(TossPaymentError) as exc_info:
            self.client.get_payment(payment_key="nonexistent_key")

        error = exc_info.value
        assert error.code == "NOT_FOUND_PAYMENT"
        assert error.status_code == status.HTTP_404_NOT_FOUND

    def test_get_payment_newwork_error(self, mocker):
        """조회 요청 네트워크 에러"""
        mocker.patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection failed"),
        )

        with pytest.raises(TossPaymentError) as exc_info:
            self.client.get_payment(payment_key="test_key")

        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.django_db
class TestVerifyWebhook:
    """웹훅 서명 검증 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self, mocker):
        """각 테스트마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()
        # 테스트용 웹훅 시크릿 키 설정
        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

    def test_verify_webhook_success(self, mocker):
        """정상적인 웹훅 서명 검증"""
        import hashlib
        import hmac
        import json

        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "paymentKey": "test_key",
                "orderId": "ORDER_001",
            },
        }

        # 올바른 서명 생성
        message = json.dumps(webhook_data, separators=(",", ":"), ensure_ascii=False)
        expected_signature = hmac.new(
            "test_webhook_secret".encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # 검증 실행
        is_valid = self.client.verify_webhook(webhook_data, expected_signature)

        # 검증 성공
        assert is_valid is True

    def test_verify_webhook_invalid_signature(self, mocker):
        """잘못된 서명으로 웹훅 검증 실패"""
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {"paymentKey": "test_key"},
        }

        # 잘못된 서명
        invalid_signature = "wrong_signature_12345"

        # 검증 실행
        is_valid = self.client.verify_webhook(webhook_data, invalid_signature)

        # 검증 실패
        assert is_valid is False

    def test_verify_webhook_signature_format(self, mocker):
        """웹훅 서명이 HMAC-SHA256 형식인지 확인"""
        webhook_data = {"eventType": "TEST"}

        # 빈 서명으로 검증
        is_valid = self.client.verify_webhook(webhook_data, "")

        # 서명이 맞지 않으면 False
        assert is_valid is False


@pytest.mark.django_db
class TestTossPaymentError:
    """TossPaymentError 예외 클래스 테스트"""

    def test_toss_payment_error_creation(self):
        """TossPaymentError 생성 테스트"""
        error = TossPaymentError(
            code="INVALID_REQUEST",
            message="잘못된 요청입니다.",
            status_code=400,
        )

        assert error.code == "INVALID_REQUEST"
        assert error.message == "잘못된 요청입니다."
        assert error.status_code == status.HTTP_400_BAD_REQUEST
        assert str(error) == "잘못된 요청입니다."

    def test_toss_payment_error_default_status_code(self):
        """status_code 기본값 확인 (400)"""
        error = TossPaymentError(
            code="TEST_ERROR",
            message="테스트 에러",
        )

        assert error.status_code == status.HTTP_400_BAD_REQUEST

    def test_toss_payment_error_to_dict(self):
        """to_dict() 메서드 테스트"""
        error = TossPaymentError(
            code="PROVIDER_ERROR",
            message="결제 승인에 실패했습니다.",
            status_code=500,
        )

        error_dict = error.to_dict()

        assert error_dict["code"] == "PROVIDER_ERROR"
        assert error_dict["message"] == "결제 승인에 실패했습니다."
        assert error_dict["status_code"] == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert isinstance(error_dict, dict)


@pytest.mark.django_db
class TestErrorMessageMapping:
    """토스 에러 코드 -> 한글 메세지 변환 테스트"""

    def test_get_error_message_known_code(self):
        """알려진 에러 코드 변환"""
        message = get_error_message("ALREADY_PROCESSED_PAYMENT")
        assert message == "이미 처리된 결제입니다."

        message = get_error_message("PROVIDER_ERROR")
        assert message == "결제 승인에 실패했습니다."

        message = get_error_message("INVALID_CARD_EXPIRATION")
        assert message == "카드 유효기간이 올바르지 않습니다."

    def test_get_error_message_unknown_code(self):
        """알 수 없는 에러 코드 처리"""
        message = get_error_message("UNKNOWN_ERROR_CODE_12345")
        assert message == "결제 처리 중 오류가 발생했습니다."

    def test_get_error_message_empty_code(self):
        """빈 에러 코드 처리"""
        message = get_error_message("")
        assert message == "결제 처리 중 오류가 발생했습니다."

    def test_toss_error_messages_coverage(self):
        """주요 에러 코드가 모두 매핑되어 있는지 확인"""
        important_codes = [
            "ALREADY_PROCESSED_PAYMENT",
            "PROVIDER_ERROR",
            "INVALID_REQUEST",
            "NOT_FOUND_PAYMENT",
            "INVALID_CARD_EXPIRATION",
            "EXCEED_MAX_DAILY_PAYMENT_COUNT",
        ]

        for code in important_codes:
            assert code in TOSS_ERROR_MESSAGES
            assert len(TOSS_ERROR_MESSAGES[code]) > 0


@pytest.mark.django_db
class TestPaymentWorkflow:
    """결제 전체 흐름 통합 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트 마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()

    def test_successful_payment_workflow(self, mocker):
        """성공적인 결제 승인 -> 조회 흐름"""
        # 1. 결제 승인 Mock
        confirm_response = {
            "paymentKey": "test_key_123",
            "orderId": "ORDER_001",
            "status": "DONE",
            "totalAmount": 10000,
        }

        mock_post = Mock()
        mock_post.status_code = status.HTTP_200_OK
        mock_post.json.return_value = confirm_response
        mocker.patch("requests.post", return_value=mock_post)

        # 결제 승인
        result = self.client.confirm_payment(
            payment_key="test_key_123",
            order_id="ORDER_001",
            amount=10000,
        )
        assert result["status"] == "DONE"

        # 2. 결제 조회 Mock
        get_response = {
            "paymentKey": "test_key_123",
            "status": "DONE",
            "totalAmount": 10000,
        }

        mock_get = Mock()
        mock_get.status_code = status.HTTP_200_OK
        mock_get.json.return_value = get_response
        mocker.patch("requests.get", return_value=mock_get)

        # 결제 조회
        payment_info = self.client.get_payment(payment_key="test_key_123")
        assert payment_info["status"] == "DONE"

    def test_payment_cancel_workflow(self, mocker):
        """결제 승인 -> 취소 흐름"""
        # 1. 결제 승인
        mock_confirm_response = Mock()
        mock_confirm_response.status_code = status.HTTP_200_OK
        mock_confirm_response.json.return_value = {"paymentKey": "test_key", "status": "DONE"}
        mocker.patch("requests.post", return_value=mock_confirm_response)

        self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=10000,
        )

        # 2. 결제 취소
        mock_cancel_response = Mock()
        mock_cancel_response.status_code = status.HTTP_200_OK
        mock_cancel_response.json.return_value = {
            "status": "CANCELED",
            "cancelReason": "고객 변심",
        }
        mocker.patch("requests.post", return_value=mock_cancel_response)

        cancel_result = self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="고객 변심",
        )
        assert cancel_result["status"] == "CANCELED"


@pytest.mark.django_db
class TestCreateBillingKey:
    """빌링키 발급 API 테스트 (정기 결제용)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """각 테스트마다 자동 실행되는 설정"""
        self.client = TossPaymentClient()

    def test_create_billing_key_success(self, mocker):
        """정상적인 빌링키 발급"""
        # Arrange
        mock_response_data = {
            "billingKey": "BIL_KEY_20250120_12345",
            "customerKey": "CUST_001",
            "authenticatedAt": "2025-01-20T10:00:00+09:00",
            "card": {
                "company": "신한카드",
                "number": "1234****",
                "cardType": "신용",
            },
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = mock_response_data
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.create_billing_key(
            customer_key="CUST_001",
            auth_key="AUTH_KEY_123",
        )

        # Assert
        assert result == mock_response_data
        assert result["billingKey"] == "BIL_KEY_20250120_12345"
        assert result["customerKey"] == "CUST_001"

        # API 호출 검증
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["customerKey"] == "CUST_001"
        assert call_kwargs["json"]["authKey"] == "AUTH_KEY_123"
        assert "/v1/billing/authorizations/issue" in mock_post.call_args[0][0]

    def test_create_billing_key_empty_customer_key(self, mocker):
        """빈 customer_key 전달 시 API 에러 응답"""
        # Arrange
        mock_error_data = {
            "code": "INVALID_REQUEST",
            "message": "customerKey는 필수 값입니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_400_BAD_REQUEST
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="",
                auth_key="AUTH_KEY_123",
            )

        error = exc_info.value
        assert error.code == "INVALID_REQUEST"
        assert error.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_billing_key_special_characters(self, mocker):
        """특수문자가 포함된 키 정상 처리"""
        # Arrange
        mock_response_data = {
            "billingKey": "BIL_KEY_SPECIAL",
            "customerKey": "CUST_특수문자_001",
            "authenticatedAt": "2025-01-20T10:00:00+09:00",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = mock_response_data
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.create_billing_key(
            customer_key="CUST_특수문자_001",
            auth_key="AUTH_KEY_!@#$%",
        )

        # Assert
        assert result["customerKey"] == "CUST_특수문자_001"

        # JSON 인코딩 확인
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["customerKey"] == "CUST_특수문자_001"
        assert call_kwargs["json"]["authKey"] == "AUTH_KEY_!@#$%"

    def test_create_billing_key_api_error_400(self, mocker):
        """Toss API 에러 응답 (400 Bad Request)"""
        # Arrange
        mock_error_data = {
            "code": "INVALID_REQUEST",
            "message": "잘못된 요청입니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_400_BAD_REQUEST
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="CUST_001",
                auth_key="INVALID_AUTH_KEY",
            )

        error = exc_info.value
        assert error.code == "INVALID_REQUEST"
        assert error.message == "잘못된 요청입니다."
        assert error.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_billing_key_api_error_401(self, mocker):
        """인증 실패 응답 (401 Unauthorized)"""
        # Arrange
        mock_error_data = {
            "code": "INVALID_API_KEY",
            "message": "API 키가 유효하지 않습니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_401_UNAUTHORIZED
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="CUST_001",
                auth_key="AUTH_KEY_123",
            )

        error = exc_info.value
        assert error.code == "INVALID_API_KEY"
        assert error.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_billing_key_network_error(self, mocker):
        """네트워크 오류 처리"""
        # Arrange
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        )

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="CUST_001",
                auth_key="AUTH_KEY_123",
            )

        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert "네트워크 오류" in error.message
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_create_billing_key_timeout_error(self, mocker):
        """요청 타임아웃 처리"""
        # Arrange
        mocker.patch(
            "requests.post",
            side_effect=requests.exceptions.Timeout("Request timeout after 30s"),
        )

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="CUST_001",
                auth_key="AUTH_KEY_123",
            )

        error = exc_info.value
        assert error.code == "NETWORK_ERROR"
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_create_billing_key_missing_error_code(self, mocker):
        """Toss API 응답에 code 필드가 없는 경우"""
        # Arrange
        mock_error_data = {
            "message": "알 수 없는 에러가 발생했습니다.",
        }

        mock_response = Mock()
        mock_response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_response.json.return_value = mock_error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.create_billing_key(
                customer_key="CUST_001",
                auth_key="AUTH_KEY_123",
            )

        error = exc_info.value
        assert error.code == "UNKNOWN"  # 기본값
        assert error.message == "알 수 없는 에러가 발생했습니다."
        assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
