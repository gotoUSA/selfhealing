"""
토스페이먼츠 API 클라이언트 테스트

TossPaymentClient의 모든 메서드와 에러 시나리오 검증
- 실제 API 호출 없이 모킹으로 테스트
- 네트워크 에러, 타임아웃, 서버 장애 등 예외 상황 포함
"""

from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from shopping.utils.toss_payment import TOSS_ERROR_MESSAGES, TossPaymentClient, TossPaymentError, get_error_message


# ==========================================
# 1. 초기화
# ==========================================


@pytest.mark.django_db
class TestTossClientInitialization:
    """클라이언트 초기화"""

    def test_client_settings_loaded_correctly(self, toss_client):
        """settings 값이 정상 로드됨"""
        # Arrange & Act
        client = toss_client

        # Assert
        assert client.secret_key is not None
        assert client.client_key is not None
        assert client.base_url is not None

    def test_basic_auth_header_encoded_correctly(self, toss_client):
        """Basic Auth 헤더가 Base64로 인코딩됨"""
        # Arrange & Act
        client = toss_client

        # Assert
        assert "Authorization" in client.headers
        assert client.headers["Authorization"].startswith("Basic ")
        assert client.headers["Content-Type"] == "application/json"


# ==========================================
# 2. 결제 승인 API
# ==========================================


@pytest.mark.django_db
class TestTossConfirmPayment:
    """결제 승인 정상 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_confirm_payment_with_card_success(self, mocker, toss_success_response):
        """카드 결제 승인 성공"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = toss_success_response
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.confirm_payment(
            payment_key="test_payment_key_123",
            order_id="ORDER_20250115_001",
            amount=13000,
        )

        # Assert
        assert result["status"] == "DONE"
        assert result["totalAmount"] == 13000
        assert result["method"] == "카드"

    def test_confirm_payment_with_decimal_amount(self, mocker, toss_success_response):
        """Decimal 금액이 int로 변환됨"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = toss_success_response
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=Decimal("13000.00"),
        )

        # Assert
        call_kwargs = mock_post.call_args[1]
        assert isinstance(call_kwargs["json"]["amount"], int)
        assert call_kwargs["json"]["amount"] == 13000

    def test_confirm_payment_response_parsed_correctly(self, mocker, toss_success_response):
        """응답 데이터가 정상 파싱됨"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = toss_success_response
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.confirm_payment(
            payment_key="test_payment_key_123",
            order_id="ORDER_20250115_001",
            amount=13000,
        )

        # Assert
        assert "paymentKey" in result
        assert "orderId" in result
        assert "approvedAt" in result
        assert result["card"]["company"] == "신한카드"


@pytest.mark.django_db
class TestTossConfirmPaymentBoundary:
    """결제 승인 경계값"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_confirm_payment_with_zero_amount(self, mocker):
        """0원 결제 (포인트 전액 사용)"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "DONE", "totalAmount": 0}
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=0,
        )

        # Assert
        assert result["totalAmount"] == 0
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["amount"] == 0

    def test_confirm_payment_with_max_amount(self, mocker):
        """최대 금액 결제"""
        # Arrange
        max_amount = 100_000_000  # 1억원
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "DONE", "totalAmount": max_amount}
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=max_amount,
        )

        # Assert
        assert result["totalAmount"] == max_amount

    def test_confirm_payment_timeout_at_30_seconds(self, mocker):
        """정확히 30초에 타임아웃 발생"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "DONE", "totalAmount": 10000}
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        self.client.confirm_payment(
            payment_key="test_key",
            order_id="ORDER_001",
            amount=10000,
        )

        # Assert
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 30


@pytest.mark.django_db
class TestTossConfirmPaymentException:
    """결제 승인 예외 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    # --- 4xx 클라이언트 에러 ---

    def test_confirm_payment_already_processed(self, mocker, toss_error_response):
        """400: ALREADY_PROCESSED_PAYMENT"""
        # Arrange
        error_data = toss_error_response("ALREADY_PROCESSED_PAYMENT", "이미 처리된 결제입니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "ALREADY_PROCESSED_PAYMENT"
        assert exc_info.value.status_code == 400

    def test_confirm_payment_invalid_card(self, mocker, toss_error_response):
        """400: INVALID_CARD_EXPIRATION"""
        # Arrange
        error_data = toss_error_response("INVALID_CARD_EXPIRATION", "카드 유효기간이 올바르지 않습니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "INVALID_CARD_EXPIRATION"

    def test_confirm_payment_exceeded_daily_limit(self, mocker, toss_error_response):
        """400: EXCEED_MAX_DAILY_PAYMENT_COUNT"""
        # Arrange
        error_data = toss_error_response("EXCEED_MAX_DAILY_PAYMENT_COUNT", "일일 결제 한도를 초과했습니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "EXCEED_MAX_DAILY_PAYMENT_COUNT"

    def test_confirm_payment_invalid_api_key(self, mocker, toss_error_response):
        """401: INVALID_API_KEY"""
        # Arrange
        error_data = toss_error_response("INVALID_API_KEY", "API 키가 유효하지 않습니다.")
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "INVALID_API_KEY"
        assert exc_info.value.status_code == 401

    def test_confirm_payment_not_found(self, mocker, toss_error_response):
        """404: NOT_FOUND_PAYMENT"""
        # Arrange
        error_data = toss_error_response("NOT_FOUND_PAYMENT", "결제 정보를 찾을 수 없습니다.")
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "NOT_FOUND_PAYMENT"
        assert exc_info.value.status_code == 404

    def test_confirm_payment_rate_limit_exceeded(self, mocker):
        """429: API 호출 한도 초과"""
        # Arrange
        error_data = {"code": "RATE_LIMIT_EXCEEDED", "message": "API 호출 한도를 초과했습니다."}
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.status_code == 429

    # --- 5xx 서버 에러 ---

    def test_confirm_payment_server_error(self, mocker):
        """500: 토스 서버 내부 에러"""
        # Arrange
        error_data = {"code": "PROVIDER_ERROR", "message": "결제 승인에 실패했습니다."}
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.status_code == 500

    def test_confirm_payment_service_unavailable(self, mocker):
        """503: 토스 서버 점검 중"""
        # Arrange
        error_data = {"code": "SERVICE_UNAVAILABLE", "message": "서비스 점검 중입니다."}
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.status_code == 503

    # --- 네트워크 에러 ---

    def test_confirm_payment_connection_error(self, mocker):
        """네트워크 연결 실패"""
        # Arrange
        mocker.patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused"))

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "NETWORK_ERROR"
        assert exc_info.value.status_code == 500

    def test_confirm_payment_timeout_error(self, mocker):
        """요청 타임아웃"""
        # Arrange
        mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("Request timeout"))

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

        assert exc_info.value.code == "NETWORK_ERROR"
        assert "네트워크 오류" in exc_info.value.message

    # --- 잘못된 응답 ---

    def test_confirm_payment_invalid_json_response(self, mocker):
        """JSON 파싱 실패"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(Exception):
            self.client.confirm_payment("test_key", "ORDER_001", 10000)

    def test_confirm_payment_missing_required_fields(self, mocker):
        """필수 필드 누락 응답"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "DONE"}  # paymentKey, orderId 누락
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.confirm_payment("test_key", "ORDER_001", 10000)

        # Assert
        assert result["status"] == "DONE"
        assert "paymentKey" not in result


# ==========================================
# 3. 결제 취소 API
# ==========================================


@pytest.mark.django_db
class TestTossCancelPayment:
    """결제 취소 정상 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_cancel_payment_full_amount(self, mocker, toss_cancel_response):
        """전체 금액 취소"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = toss_cancel_response
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.cancel_payment(
            payment_key="test_payment_key_123",
            cancel_reason="고객 변심",
        )

        # Assert
        assert result["status"] == "CANCELED"
        assert result["cancelReason"] == "고객 변심"
        call_kwargs = mock_post.call_args[1]
        assert "cancelAmount" not in call_kwargs["json"]

    def test_cancel_payment_partial_amount(self, mocker):
        """부분 취소"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "CANCELED",
            "canceledAmount": 5000,
            "cancelReason": "부분 취소",
        }
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="부분 취소",
            cancel_amount=5000,
        )

        # Assert
        assert result["canceledAmount"] == 5000
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["cancelAmount"] == 5000

    def test_cancel_payment_with_refund_account(self, mocker):
        """가상계좌 환불 정보 포함"""
        # Arrange
        refund_account = {
            "bank": "신한은행",
            "accountNumber": "123456789",
            "holderName": "홍길동",
        }
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "CANCELED"}
        mock_post = mocker.patch("requests.post", return_value=mock_response)

        # Act
        self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="가상계좌 환불",
            refund_account=refund_account,
        )

        # Assert
        call_kwargs = mock_post.call_args[1]
        assert "refundReceiveAccount" in call_kwargs["json"]
        assert call_kwargs["json"]["refundReceiveAccount"] == refund_account


@pytest.mark.django_db
class TestTossCancelPaymentBoundary:
    """결제 취소 경계값"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_cancel_payment_minimum_amount(self, mocker):
        """최소 취소 금액"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "CANCELED", "canceledAmount": 100}
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="최소 취소",
            cancel_amount=100,
        )

        # Assert
        assert result["canceledAmount"] == 100

    def test_cancel_payment_exact_remaining_amount(self, mocker):
        """정확히 남은 금액만큼 취소"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "CANCELED", "canceledAmount": 8000}
        mocker.patch("requests.post", return_value=mock_response)

        # Act
        result = self.client.cancel_payment(
            payment_key="test_key",
            cancel_reason="남은 금액 취소",
            cancel_amount=8000,
        )

        # Assert
        assert result["canceledAmount"] == 8000


@pytest.mark.django_db
class TestTossCancelPaymentException:
    """결제 취소 예외 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_cancel_payment_already_canceled(self, mocker, toss_error_response):
        """이미 취소된 결제"""
        # Arrange
        error_data = toss_error_response("ALREADY_CANCELED_PAYMENT", "이미 취소된 결제입니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment("test_key", "고객 변심")

        assert exc_info.value.code == "ALREADY_CANCELED_PAYMENT"

    def test_cancel_payment_exceed_cancelable_amount(self, mocker, toss_error_response):
        """취소 가능 금액 초과"""
        # Arrange
        error_data = toss_error_response("EXCEED_CANCELABLE_AMOUNT", "취소 가능 금액을 초과했습니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment("test_key", "부분 취소", cancel_amount=999999)

        assert exc_info.value.code == "EXCEED_CANCELABLE_AMOUNT"

    def test_cancel_payment_network_error(self, mocker):
        """네트워크 에러"""
        # Arrange
        mocker.patch("requests.post", side_effect=requests.exceptions.RequestException("Network error"))

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment("test_key", "테스트 취소")

        assert exc_info.value.code == "NETWORK_ERROR"

    def test_cancel_payment_server_error(self, mocker):
        """500 서버 에러"""
        # Arrange
        error_data = {"code": "PROVIDER_ERROR", "message": "서버 에러"}
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = error_data
        mocker.patch("requests.post", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.cancel_payment("test_key", "테스트 취소")

        assert exc_info.value.status_code == 500


# ==========================================
# 4. 결제 조회 API
# ==========================================


@pytest.mark.django_db
class TestTossGetPayment:
    """결제 조회 정상 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_get_payment_success(self, mocker, toss_success_response):
        """결제 정보 조회 성공"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = toss_success_response
        mock_get = mocker.patch("requests.get", return_value=mock_response)

        # Act
        result = self.client.get_payment("test_payment_key_123")

        # Assert
        assert result["status"] == "DONE"
        assert result["paymentKey"] == "test_payment_key_123"
        assert "test_payment_key_123" in mock_get.call_args[0][0]


@pytest.mark.django_db
class TestTossGetPaymentException:
    """결제 조회 예외 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client):
        self.client = toss_client

    def test_get_payment_not_found(self, mocker, toss_error_response):
        """404: 결제 정보 없음"""
        # Arrange
        error_data = toss_error_response("NOT_FOUND_PAYMENT", "결제 정보를 찾을 수 없습니다.")
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.json.return_value = error_data
        mocker.patch("requests.get", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.get_payment("nonexistent_key")

        assert exc_info.value.code == "NOT_FOUND_PAYMENT"
        assert exc_info.value.status_code == 404

    def test_get_payment_invalid_payment_key(self, mocker, toss_error_response):
        """잘못된 payment_key"""
        # Arrange
        error_data = toss_error_response("INVALID_REQUEST", "잘못된 요청입니다.")
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = error_data
        mocker.patch("requests.get", return_value=mock_response)

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.get_payment("invalid_key")

        assert exc_info.value.code == "INVALID_REQUEST"

    def test_get_payment_network_error(self, mocker):
        """네트워크 에러"""
        # Arrange
        mocker.patch("requests.get", side_effect=requests.exceptions.ConnectionError("Connection failed"))

        # Act & Assert
        with pytest.raises(TossPaymentError) as exc_info:
            self.client.get_payment("test_key")

        assert exc_info.value.code == "NETWORK_ERROR"


# ==========================================
# 5. 웹훅 서명 검증
# ==========================================


@pytest.mark.django_db
class TestTossVerifyWebhook:
    """웹훅 서명 검증 정상 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client, mocker):
        self.client = toss_client
        # 테스트용 웹훅 시크릿 설정
        from django.conf import settings

        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

    def test_verify_webhook_with_valid_signature(self, toss_webhook_data):
        """정상 서명 검증 성공"""
        # Arrange
        import hashlib
        import hmac
        import json

        message = json.dumps(toss_webhook_data, separators=(",", ":"), ensure_ascii=False)
        valid_signature = hmac.new(
            "test_webhook_secret".encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Act
        is_valid = self.client.verify_webhook(toss_webhook_data, valid_signature)

        # Assert
        assert is_valid is True

    def test_verify_webhook_uses_hmac_sha256(self, toss_webhook_data):
        """HMAC-SHA256 알고리즘 사용 확인"""
        # Arrange
        import hashlib
        import hmac
        import json

        message = json.dumps(toss_webhook_data, separators=(",", ":"), ensure_ascii=False)
        expected_signature = hmac.new(
            "test_webhook_secret".encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Act
        is_valid = self.client.verify_webhook(toss_webhook_data, expected_signature)

        # Assert
        assert is_valid is True
        assert len(expected_signature) == 64  # SHA256 hex digest length


@pytest.mark.django_db
class TestTossVerifyWebhookException:
    """웹훅 서명 검증 예외 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, toss_client, mocker):
        self.client = toss_client
        from django.conf import settings

        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

    def test_verify_webhook_with_invalid_signature(self, toss_webhook_data):
        """잘못된 서명"""
        # Arrange
        invalid_signature = "invalid_signature_12345"

        # Act
        is_valid = self.client.verify_webhook(toss_webhook_data, invalid_signature)

        # Assert
        assert is_valid is False

    def test_verify_webhook_with_empty_signature(self, toss_webhook_data):
        """빈 서명"""
        # Arrange
        empty_signature = ""

        # Act
        is_valid = self.client.verify_webhook(toss_webhook_data, empty_signature)

        # Assert
        assert is_valid is False

    def test_verify_webhook_with_tampered_data(self):
        """데이터 변조 감지"""
        # Arrange
        import hashlib
        import hmac
        import json

        original_data = {"eventType": "PAYMENT.DONE", "data": {"amount": 10000}}
        message = json.dumps(original_data, separators=(",", ":"), ensure_ascii=False)
        valid_signature = hmac.new(
            "test_webhook_secret".encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # 데이터 변조
        tampered_data = {"eventType": "PAYMENT.DONE", "data": {"amount": 99999}}

        # Act
        is_valid = self.client.verify_webhook(tampered_data, valid_signature)

        # Assert
        assert is_valid is False

    def test_verify_webhook_timing_attack_safe(self, toss_webhook_data):
        """타이밍 공격 방지 (hmac.compare_digest)"""
        # Arrange
        import hashlib
        import hmac
        import json

        message = json.dumps(toss_webhook_data, separators=(",", ":"), ensure_ascii=False)
        valid_signature = hmac.new(
            "test_webhook_secret".encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Act
        is_valid = self.client.verify_webhook(toss_webhook_data, valid_signature)

        # Assert
        assert is_valid is True


# ==========================================
# 6. 에러 코드 매핑
# ==========================================


@pytest.mark.django_db
class TestTossErrorMapping:
    """에러 코드 매핑"""

    def test_known_error_code_returns_korean_message(self):
        """알려진 에러 코드 → 한글 메시지"""
        # Act & Assert
        assert get_error_message("ALREADY_PROCESSED_PAYMENT") == "이미 처리된 결제입니다."
        assert get_error_message("PROVIDER_ERROR") == "결제 승인에 실패했습니다."
        assert get_error_message("INVALID_CARD_EXPIRATION") == "카드 유효기간이 올바르지 않습니다."

    def test_unknown_error_code_returns_default_message(self):
        """알 수 없는 에러 → 기본 메시지"""
        # Act
        message = get_error_message("UNKNOWN_CODE_12345")

        # Assert
        assert message == "결제 처리 중 오류가 발생했습니다."

    def test_major_error_codes_are_mapped(self):
        """주요 에러 코드 커버리지 확인"""
        # Arrange
        important_codes = [
            "ALREADY_PROCESSED_PAYMENT",
            "PROVIDER_ERROR",
            "INVALID_REQUEST",
            "NOT_FOUND_PAYMENT",
            "INVALID_CARD_EXPIRATION",
            "EXCEED_MAX_DAILY_PAYMENT_COUNT",
        ]

        # Act & Assert
        for code in important_codes:
            assert code in TOSS_ERROR_MESSAGES
            assert len(TOSS_ERROR_MESSAGES[code]) > 0


# ==========================================
# 7. TossPaymentError 예외
# ==========================================


@pytest.mark.django_db
class TestTossPaymentError:
    """TossPaymentError 예외 클래스"""

    def test_error_instance_created_with_all_fields(self):
        """에러 객체 생성 - 모든 필드"""
        # Act
        error = TossPaymentError(
            code="INVALID_REQUEST",
            message="잘못된 요청입니다.",
            status_code=400,
        )

        # Assert
        assert error.code == "INVALID_REQUEST"
        assert error.message == "잘못된 요청입니다."
        assert error.status_code == 400

    def test_error_default_status_code_is_400(self):
        """기본 status_code는 400"""
        # Act
        error = TossPaymentError(
            code="TEST_ERROR",
            message="테스트 에러",
        )

        # Assert
        assert error.status_code == 400

    def test_error_to_dict_returns_correct_structure(self):
        """to_dict() 메서드"""
        # Arrange
        error = TossPaymentError(
            code="PROVIDER_ERROR",
            message="결제 승인에 실패했습니다.",
            status_code=500,
        )

        # Act
        error_dict = error.to_dict()

        # Assert
        assert error_dict["code"] == "PROVIDER_ERROR"
        assert error_dict["message"] == "결제 승인에 실패했습니다."
        assert error_dict["status_code"] == 500
        assert isinstance(error_dict, dict)

    def test_error_string_representation(self):
        """__str__() 메서드"""
        # Arrange
        error = TossPaymentError(
            code="TEST_ERROR",
            message="테스트 에러 메시지",
            status_code=400,
        )

        # Act
        error_str = str(error)

        # Assert
        assert error_str == "테스트 에러 메시지"
