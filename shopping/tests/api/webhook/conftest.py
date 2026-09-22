"""
Webhook 테스트 전용 Fixture

전역 conftest.py의 fixture는 그대로 사용하고,
webhook 테스트에만 필요한 특화된 fixture를 정의합니다.

토스 웹훅 계약 (docs.tosspayments.com/reference/using-api/webhook-events):
- 본문 = {"eventType": "PAYMENT_STATUS_CHANGED", "createdAt": ..., "data": {Payment 객체}}
- PAYMENT_STATUS_CHANGED 에는 서명 헤더가 없다 → 뷰는 paymentKey 로 결제 조회 API를 호출해 그 응답으로 처리한다.
  테스트는 그 조회 호출(TossPaymentClient.get_payment)을 mock 하고, 기본 동작은 "빌더가 만든 Payment 객체를 그대로 돌려준다"이다.

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

from shopping.utils.toss_payment import TossPaymentError

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
# 2. 토스 결제 조회 응답 레지스트리
# ==========================================


@pytest.fixture
def toss_payments():
    """
    "토스가 알고 있는 결제" 레지스트리 — paymentKey → Payment 객체(dict)

    webhook_data_builder 가 만든 Payment 객체를 여기에 등록하고,
    mock_get_payment 의 기본 동작이 이 레지스트리에서 조회한다.
    등록되지 않은 paymentKey 는 토스 404(NOT_FOUND_PAYMENT)로 응답한다.
    """
    return {}


# ==========================================
# 3. 결제 조회 API Mock Fixture
# ==========================================


@pytest.fixture
def mock_get_payment(mocker, toss_payments):
    """
    TossPaymentClient.get_payment mock 헬퍼

    Usage:
        # 기본: 빌더가 등록한 Payment 객체를 그대로 반환 (웹훅 본문 == 토스 조회 응답)
        mock_get_payment()

        # 토스 조회 응답을 직접 지정 (본문과 다른 상태/금액을 돌려주고 싶을 때)
        mock_get_payment(payment={"paymentKey": "...", "orderId": "...", "status": "CANCELED", ...})

        # 토스 조회 실패
        mock_get_payment(error=TossPaymentError(code="NOT_FOUND_PAYMENT", message="...", status_code=404))
    """

    def _mock(payment=None, error=None):
        if error is not None:
            side_effect = error
        elif payment is not None:

            def side_effect(payment_key, timeout=30):
                return payment

        else:

            def side_effect(payment_key, timeout=30):
                if payment_key not in toss_payments:
                    raise TossPaymentError(
                        code="NOT_FOUND_PAYMENT",
                        message="존재하지 않는 결제 정보 입니다.",
                        status_code=404,
                    )
                return toss_payments[payment_key]

        return mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.get_payment",
            side_effect=side_effect,
        )

    return _mock


# ==========================================
# 4. 웹훅 데이터 Builder Fixture
# ==========================================


@pytest.fixture
def webhook_data_builder(toss_payments):
    """
    웹훅 요청 데이터 빌더 — PAYMENT_STATUS_CHANGED 본문

    - 기본값 제공, 부분 오버라이드 가능
    - status 에 따라 Payment 객체 필드 자동 구성 (DONE / CANCELED / ABORTED / EXPIRED / ...)
    - 만든 Payment 객체를 toss_payments 레지스트리에 등록 → mock_get_payment() 기본 동작이 이를 반환

    Usage:
        # DONE (기본)
        data = webhook_data_builder(order_id="ORDER_001")

        # CANCELED
        data = webhook_data_builder(status="CANCELED", order_id="ORDER_001", cancel_reason="사용자 요청")

        # ABORTED (승인 실패)
        data = webhook_data_builder(status="ABORTED", order_id="ORDER_001", fail_reason="카드 한도 초과")

        # 가상계좌 발급 / 기한 만료
        data = webhook_data_builder(status="WAITING_FOR_DEPOSIT", order_id="ORDER_001", secret="ps_...")
        data = webhook_data_builder(status="EXPIRED", order_id="ORDER_001")
    """

    def _build(
        status="DONE",
        order_id="ORDER_001",
        payment_key="test_payment_key_123",
        amount=10000,
        method="카드",
        approved_at="2025-01-15T10:00:00+09:00",
        cancel_reason=None,
        canceled_at=None,
        fail_reason=None,
        secret=None,
        event_type="PAYMENT_STATUS_CHANGED",
        created_at="2025-01-15T10:00:01.000000+09:00",
        **kwargs,
    ):
        # 토스 Payment 객체 (결제 조회 API 응답과 같은 모양)
        payment = {
            "paymentKey": payment_key,
            "orderId": order_id,
            "status": status,
            "totalAmount": amount,
            "method": method,
        }

        if status == "DONE":
            payment["approvedAt"] = approved_at
            # 카드 결제인 경우 카드 정보 추가
            if method == "카드":
                payment["card"] = {
                    "company": "신한카드",
                    "number": "1234****",
                    "installmentPlanMonths": 0,
                }

        elif status in ("CANCELED", "PARTIAL_CANCELED"):
            payment["approvedAt"] = approved_at
            # 토스는 취소 이력을 cancels[] 에 담는다
            payment["cancels"] = [
                {
                    "cancelAmount": amount,
                    "cancelReason": cancel_reason or "사용자 요청",
                    "canceledAt": canceled_at or "2025-01-15T11:00:00+09:00",
                    "transactionKey": f"txn_{payment_key}",
                }
            ]

        elif status == "ABORTED":
            # 승인 실패 — failure 객체 (fail_reason 이 None 일 때만 기본값, 빈 문자열은 유지)
            payment["failure"] = {
                "code": "REJECT_CARD_COMPANY",
                "message": "카드 한도 초과" if fail_reason is None else fail_reason,
            }

        elif status in ("WAITING_FOR_DEPOSIT", "EXPIRED"):
            # 가상계좌 — 발급 정보 + 입금 웹훅 검증값(secret). EXPIRED 는 기한이 지난 같은 객체
            payment["method"] = "가상계좌"
            payment["virtualAccount"] = {
                "accountType": "일반",
                "accountNumber": "X6505636518308",
                "bankCode": "20",
                "customerName": "홍길동",
                "dueDate": "2025-01-22T23:59:59+09:00",
                "refundStatus": "NONE",
                "expired": status == "EXPIRED",
                "settlementStatus": "INCOMPLETED",
            }
            payment["secret"] = secret or "ps_test_secret_0001"

        # 추가 필드 병합 (failure=None 처럼 명시적 제거도 가능)
        for key, value in kwargs.items():
            if value is None:
                payment.pop(key, None)
            else:
                payment[key] = value

        toss_payments[payment_key] = payment

        return {
            "eventType": event_type,
            "createdAt": created_at,
            "data": payment,
        }

    return _build


# ==========================================
# 5. 가상계좌 입금 웹훅(DEPOSIT_CALLBACK) Builder Fixture
# ==========================================


@pytest.fixture
def deposit_callback_builder():
    """
    DEPOSIT_CALLBACK 본문 빌더 — 이 이벤트만 eventType/data 래퍼 없이 평평하다

    Usage:
        body = deposit_callback_builder(order_id="ORDER_001", secret="ps_...")            # 입금 완료
        body = deposit_callback_builder(order_id="ORDER_001", secret="ps_...", status="CANCELED")
    """

    def _build(
        order_id="ORDER_001",
        secret="ps_test_secret_0001",
        status="DONE",
        transaction_key="txn_deposit_0001",
        created_at="2025-01-16T09:30:00.000000+09:00",
    ):
        return {
            "createdAt": created_at,
            "secret": secret,
            "status": status,
            "transactionKey": transaction_key,
            "orderId": order_id,
        }

    return _build
