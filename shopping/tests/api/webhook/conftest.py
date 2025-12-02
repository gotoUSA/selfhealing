"""
Webhook 테스트 전용 Fixture

전역 conftest.py의 fixture는 그대로 사용하고,
webhook 테스트에만 필요한 특화된 fixture를 정의합니다.

사용 가능한 전역 fixture:
- api_client: DRF APIClient
- user: 기본 사용자 (이메일 인증 완료, 포인트 5000)
- product: 기본 상품 (10,000원, 재고 10)
- multiple_products: 여러 상품 리스트
- order: pending 상태 기본 주문
- payment: ready 상태 결제
- paid_order: 결제 완료된 주문
- paid_payment: 결제 완료된 Payment
"""

import pytest
from django.urls import reverse


# ==========================================
# 1. 웹훅 URL Fixture
# ==========================================


@pytest.fixture
def webhook_url():
    """
    토스 웹훅 엔드포인트 URL

    모든 웹훅 테스트에서 공통으로 사용
    """
    return reverse("toss-webhook")


# ==========================================
# 2. 시그니처 검증 Mock Fixture
# ==========================================


@pytest.fixture
def mock_verify_webhook(mocker):
    """
    시그니처 검증 Mock 헬퍼

    Usage:
        # 검증 성공
        mock_verify_webhook()

        # 검증 실패
        mock_verify_webhook(False)
    """

    def _mock(return_value=True):
        return mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.verify_webhook",
            return_value=return_value,
        )

    return _mock


# ==========================================
# 3. 웹훅 데이터 Builder Fixture
# ==========================================


@pytest.fixture
def webhook_data_builder():
    """
    웹훅 요청 데이터 빌더

    커스터마이징 가능한 웹훅 데이터 생성
    - 기본값 제공
    - 부분 오버라이드 가능
    - 이벤트 타입별 데이터 자동 구성

    Usage:
        # PAYMENT.DONE (기본)
        data = webhook_data_builder(order_id="ORDER_001")

        # PAYMENT.CANCELED
        data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id="ORDER_001",
            cancel_reason="사용자 요청"
        )

        # PAYMENT.FAILED
        data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id="ORDER_001",
            fail_reason="카드 한도 초과"
        )
    """

    def _build(
        event_type="PAYMENT.DONE",
        order_id="ORDER_001",
        payment_key="test_payment_key_123",
        status=None,
        amount=10000,
        method="카드",
        approved_at="2025-01-15T10:00:00+09:00",
        cancel_reason=None,
        canceled_at=None,
        fail_reason=None,
        **kwargs,
    ):
        # 이벤트 타입에 따른 status 기본값 설정
        if status is None:
            if event_type == "PAYMENT.DONE":
                status = "DONE"
            elif event_type == "PAYMENT.CANCELED":
                status = "CANCELED"
            elif event_type == "PAYMENT.FAILED":
                status = "FAILED"

        # 기본 데이터 구조
        webhook_data = {
            "eventType": event_type,
            "data": {
                "orderId": order_id,
            },
        }

        # PAYMENT.DONE 이벤트
        if event_type == "PAYMENT.DONE":
            webhook_data["data"].update(
                {
                    "paymentKey": payment_key,
                    "status": status,
                    "totalAmount": amount,
                    "method": method,
                    "approvedAt": approved_at,
                }
            )

            # 카드 결제인 경우 카드 정보 추가
            if method == "카드":
                webhook_data["data"]["card"] = {
                    "company": "신한카드",
                    "number": "1234****",
                    "installmentPlanMonths": 0,
                }

        # PAYMENT.CANCELED 이벤트
        elif event_type == "PAYMENT.CANCELED":
            webhook_data["data"].update(
                {
                    "paymentKey": payment_key,
                    "status": status,
                    "cancelReason": cancel_reason or "사용자 요청",
                    "canceledAt": canceled_at or "2025-01-15T11:00:00+09:00",
                }
            )

        # PAYMENT.FAILED 이벤트
        elif event_type == "PAYMENT.FAILED":
            # fail_reason이 None일 때만 기본값 사용 (빈 문자열은 유지)
            webhook_data["data"]["failReason"] = (
                "카드 한도 초과" if fail_reason is None else fail_reason
            )

        # 추가 필드 병합
        webhook_data["data"].update(kwargs)

        return webhook_data

    return _build


# ==========================================
# 4. 웹훅 시그니처 상수
# ==========================================


@pytest.fixture
def webhook_signature():
    """
    테스트용 웹훅 시그니처

    모든 테스트에서 동일한 시그니처 사용
    """
    return "test_signature"
