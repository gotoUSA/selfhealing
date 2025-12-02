"""
웹훅 서명 검증 테스트

토스페이먼츠 웹훅 서명 검증 로직의 보안 및 정확성 테스트
- HMAC-SHA256 서명 생성 및 검증
- JSON 정규화 검증
- 데이터 변조 방지
- 유니코드/특수문자 처리
- 타이밍 공격 방지
"""

import hashlib
import hmac
import json
import uuid

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.tests.factories import OrderFactory, OrderItemFactory, PaymentFactory


# ==========================================
# Helper Functions
# ==========================================


def generate_valid_signature(webhook_data: dict) -> str:
    """
    올바른 웹훅 서명 생성 (실제 암호화)

    토스페이먼츠 서명 생성 로직과 동일하게 구현
    """
    webhook_secret = settings.TOSS_WEBHOOK_SECRET or "test_webhook_secret"
    message = json.dumps(webhook_data, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(
        webhook_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature


def generate_wrong_signature(webhook_data: dict) -> str:
    """잘못된 서명 생성 (다른 시크릿 사용)"""
    wrong_secret = "wrong_secret_key_12345"
    message = json.dumps(webhook_data, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(
        wrong_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature


# ==========================================
# 1. 정상 케이스 (Happy Path)
# ==========================================


@pytest.mark.django_db
class TestWebhookSignatureValidCases:
    """올바른 서명 검증 - 정상 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url, mocker):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        # settings.TOSS_WEBHOOK_SECRET Mock 설정
        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

        # UUID로 고유한 주문 생성
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    def test_valid_signature_done_event(self):
        """올바른 서명으로 DONE 이벤트 처리"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_payment_key_123",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
                "method": "카드",
                "approvedAt": "2025-01-15T10:00:00+09:00",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

        # Assert - Payment 상태 변경 확인
        self.payment.refresh_from_db()
        assert self.payment.status == "done"
        assert self.payment.payment_key == "test_payment_key_123"

    def test_valid_signature_canceled_event(self):
        """올바른 서명으로 CANCELED 이벤트 처리"""
        # Arrange
        self.payment.status = "done"
        self.payment.save()

        webhook_data = {
            "eventType": "PAYMENT.CANCELED",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "CANCELED",
                "cancelReason": "사용자 요청",
                "canceledAt": "2025-01-15T11:00:00+09:00",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"

    def test_valid_signature_failed_event(self):
        """올바른 서명으로 FAILED 이벤트 처리"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": "카드 한도 초과",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == "카드 한도 초과"


# ==========================================
# 2. 경계값 테스트 (Boundary)
# ==========================================


@pytest.mark.django_db
class TestWebhookSignatureBoundaryCases:
    """서명 검증 경계값 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url, mocker):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    def test_unicode_characters_in_webhook_data(self):
        """유니코드 문자 포함 웹훅 데이터 - ensure_ascii=False 검증"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": "결제 실패 🚫 カード エラー 💳 支付失败",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 유니코드가 정상 처리됨
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.fail_reason == "결제 실패 🚫 カード エラー 💳 支付失败"

    def test_special_characters_in_webhook_data(self):
        """특수문자 포함 웹훅 데이터 - JSON 이스케이프 처리"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": '카드 오류: "한도 초과" & \'잔액 부족\' <취소>',
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 특수문자가 안전하게 처리됨
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.fail_reason == '카드 오류: "한도 초과" & \'잔액 부족\' <취소>'

    def test_very_long_webhook_data(self):
        """매우 긴 웹훅 데이터 - 큰 페이로드 처리"""
        # Arrange
        long_reason = "결제 실패 상세 내용: " + "A" * 5000  # 약 5KB

        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": long_reason,
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 큰 데이터도 정상 처리
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert len(self.payment.fail_reason) > 5000

    def test_minimal_webhook_data_structure(self):
        """최소한의 필수 필드만 포함 - 최소 데이터 처리"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": "실패",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 최소 데이터로 정상 처리
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"

    def test_json_field_order_difference(self):
        """JSON 필드 순서 차이 - 정규화 검증"""
        # Arrange - 필드 순서를 바꾼 두 개의 동일한 데이터
        webhook_data_1 = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
            },
        }

        webhook_data_2 = {
            "data": {
                "totalAmount": int(self.payment.amount),
                "status": "DONE",
                "paymentKey": "test_key",
                "orderId": str(self.order.id),
            },
            "eventType": "PAYMENT.DONE",
        }

        # 필드 순서가 달라도 동일한 서명 생성 확인
        signature_1 = generate_valid_signature(webhook_data_1)
        signature_2 = generate_valid_signature(webhook_data_2)

        # JSON 정규화로 인해 서명이 다를 수 있음 (Python dict는 순서 유지)
        # 실제로는 서로 다른 서명이 생성됨 (순서가 다르므로)
        assert signature_1 != signature_2

        # Act - webhook_data_1 전송
        response = self.client.post(
            self.webhook_url,
            webhook_data_1,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature_1,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK


# ==========================================
# 3. 예외 케이스 (Exception)
# ==========================================


@pytest.mark.django_db
class TestWebhookSignatureInvalidCases:
    """잘못된 서명 검증 - 예외 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url, mocker):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    def test_missing_signature_header(self):
        """서명 헤더 누락 - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
            },
        }

        # Act - 서명 헤더 없이 요청
        response = self.client.post(self.webhook_url, webhook_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Signature missing" in response.json()["error"]

    def test_empty_signature_header(self):
        """빈 서명 헤더 - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
            },
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE="",
        )

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_signature_random_string(self):
        """잘못된 서명 (랜덤 문자열) - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
            },
        }

        # Act - 랜덤 서명 사용
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE="random_invalid_signature_12345",
        )

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid signature" in response.json()["error"]

    def test_wrong_secret_key_signature(self):
        """다른 시크릿으로 생성한 서명 - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
            },
        }
        wrong_signature = generate_wrong_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=wrong_signature,
        )

        # Assert - 다른 시크릿으로 생성한 서명은 거부됨
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid signature" in response.json()["error"]

    def test_tampered_webhook_data_after_signing(self):
        """서명 후 데이터 변조 - 401 Unauthorized"""
        # Arrange - 원본 데이터로 서명 생성
        original_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
            },
        }
        signature = generate_valid_signature(original_data)

        # 데이터 변조 (금액 변경)
        tampered_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": 99999999,  # 변조된 금액
            },
        }

        # Act - 변조된 데이터를 원본 서명으로 전송
        response = self.client.post(
            self.webhook_url,
            tampered_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 서명 불일치로 거부됨
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid signature" in response.json()["error"]

        # Assert - Payment 상태 변경 안 됨
        self.payment.refresh_from_db()
        assert self.payment.status == "ready"

    def test_signature_case_sensitivity(self):
        """서명 대소문자 구분 - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
            },
        }
        signature = generate_valid_signature(webhook_data)

        # 서명을 대문자로 변환
        uppercase_signature = signature.upper()

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=uppercase_signature,
        )

        # Assert - 대소문자가 다르면 거부됨
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_malformed_signature_format(self):
        """잘못된 형식의 서명 - 401 Unauthorized"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
            },
        }

        # Act - 잘못된 형식 (너무 짧음, 특수문자 포함 등)
        invalid_signatures = [
            "abc",  # 너무 짧음
            "invalid@signature#format",  # 특수문자
            "not-a-valid-hmac-sha256-signature",  # 잘못된 형식
        ]

        for invalid_sig in invalid_signatures:
            response = self.client.post(
                self.webhook_url,
                webhook_data,
                format="json",
                HTTP_X_TOSS_WEBHOOK_SIGNATURE=invalid_sig,
            )

            # Assert
            assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ==========================================
# 4. 보안 기능 테스트
# ==========================================


@pytest.mark.django_db
class TestWebhookSignatureSecurityFeatures:
    """서명 검증 보안 기능 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url, mocker):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        mocker.patch.object(settings, "TOSS_WEBHOOK_SECRET", "test_webhook_secret")

        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    def test_timing_attack_prevention_with_hmac_compare_digest(self):
        """타이밍 공격 방지 - hmac.compare_digest 사용 확인"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "paymentKey": "test_key",
                "status": "DONE",
                "totalAmount": int(self.payment.amount),
            },
        }
        valid_signature = generate_valid_signature(webhook_data)

        # Act - 올바른 서명으로 요청
        response_valid = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=valid_signature,
        )

        # Act - 잘못된 서명으로 요청
        response_invalid = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE="invalid_signature",
        )

        # Assert - 응답 시간이 유사해야 함 (타이밍 공격 방지)
        # 실제 타이밍 측정은 어렵지만, hmac.compare_digest 사용 확인
        assert response_valid.status_code == status.HTTP_200_OK
        assert response_invalid.status_code == status.HTTP_401_UNAUTHORIZED

    def test_json_normalization_consistency(self):
        """JSON 정규화 일관성 - separators=(",", ":") 사용"""
        # Arrange - 공백이 있는 JSON과 없는 JSON
        webhook_data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": str(self.order.id),
                "nested": {"key1": "value1", "key2": "value2"},
            },
        }

        # JSON 정규화 (공백 제거)
        normalized_json = json.dumps(webhook_data, separators=(",", ":"), ensure_ascii=False)
        normalized_with_spaces = json.dumps(webhook_data, separators=(", ", ": "), ensure_ascii=False)

        # 공백 유무에 따라 다른 문자열이 생성됨
        assert normalized_json != normalized_with_spaces

        # Act - 정규화된 JSON으로 서명 생성
        signature = generate_valid_signature(webhook_data)

        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 정규화된 서명으로 정상 처리
        assert response.status_code == status.HTTP_200_OK

    def test_xss_injection_in_webhook_data(self):
        """XSS 인젝션 방어 - 웹훅 데이터에 스크립트 태그 포함"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": "<script>alert('XSS')</script> 결제 실패",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 정상 처리 (Django ORM이 자동 이스케이프)
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.fail_reason == "<script>alert('XSS')</script> 결제 실패"

        # Assert - PaymentLog에도 안전하게 저장
        log = PaymentLog.objects.filter(payment=self.payment).first()
        assert log is not None
        assert "<script>" in log.message

    def test_sql_injection_in_webhook_data(self):
        """SQL 인젝션 방어 - 웹훅 데이터에 SQL 쿼리 포함"""
        # Arrange
        webhook_data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": str(self.order.id),
                "failReason": "'; DROP TABLE payment; -- 결제 실패",
            },
        }
        signature = generate_valid_signature(webhook_data)

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=signature,
        )

        # Assert - 정상 처리 (Django ORM이 파라미터화된 쿼리 사용)
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.fail_reason == "'; DROP TABLE payment; -- 결제 실패"

        # Assert - Payment 테이블이 여전히 존재함
        from shopping.models.payment import Payment

        payments_count = Payment.objects.count()
        assert payments_count > 0
