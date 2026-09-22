"""토스페이먼츠 웹훅 진입점

토스 웹훅 계약 (https://docs.tosspayments.com/reference/using-api/webhook-events):
- PAYMENT_STATUS_CHANGED: 본문 = {"eventType", "createdAt", "data": {Payment 객체}}.
  서명 헤더가 없다 (서명 검증은 payout.changed / seller.changed 전용).
- DEPOSIT_CALLBACK (가상계좌 입금): 본문이 평평하다 — {"createdAt", "secret", "status", "transactionKey", "orderId"}.
  secret 이 승인 응답의 Payment.secret 과 같아야 한다.
- 두 경우 모두 본문을 신뢰하지 않고, paymentKey 로 결제 조회 API를 호출해 그 응답(현재 상태)으로 처리한다.
- 10초 안에 200 을 돌려주지 않으면 1·4·16·64·256·1024·4096분 간격으로 최대 7회 재전송한다.
  → 같은 이벤트가 여러 번 오는 것이 정상이며, 중복 방어는 TossWebhookService 가 맡는다.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable

from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from ..models.payment import Payment
from ..serializers.payment_serializers import (
    DepositCallbackSerializer,
    PaymentWebhookSerializer,
)
from ..services.toss_webhook_service import TossWebhookService
from ..utils.toss_payment import TossPaymentClient, TossPaymentError

# 로거 설정
logger = logging.getLogger(__name__)

# 결제 조회 API 타임아웃 (초). 토스는 10초 안에 200 이 없으면 실패로 보고 재전송하므로 그보다 짧게 둔다.
WEBHOOK_LOOKUP_TIMEOUT = 5

# Payment.status (결제 조회 API 응답) → 핸들러 매핑
STATUS_HANDLERS: dict[str, Callable[[dict], None]] = {
    "DONE": TossWebhookService.handle_payment_done,
    "CANCELED": TossWebhookService.handle_payment_canceled,
    "WAITING_FOR_DEPOSIT": TossWebhookService.handle_waiting_for_deposit,
    "ABORTED": TossWebhookService.handle_payment_failed,
    "EXPIRED": TossWebhookService.handle_payment_failed,
}

# 우리 쪽 상태 변경이 없는 상태(READY/IN_PROGRESS)와 미지원(PARTIAL_CANCELED)은 200 으로 무시
IGNORED_STATUSES = frozenset({"READY", "IN_PROGRESS", "PARTIAL_CANCELED"})


def _error_response(message: str, status_code: int) -> Response:
    """에러 응답 생성 헬퍼"""
    return Response({"error": message}, status=status_code)


def _is_deposit_callback(payload) -> bool:
    """가상계좌 입금 웹훅인지 — 유일하게 eventType 없이 평평한 본문으로 온다"""
    return (
        isinstance(payload, dict) and "eventType" not in payload and "secret" in payload
    )


def _lookup_and_dispatch(
    payment_key: str, order_id: str, webhook_status: str | None
) -> Response:
    """
    결제 조회 API로 진위·현재 상태를 확인하고 상태별 핸들러로 보낸다

    본문의 status 는 참고만 — 재전송이 늦게 오면 이미 과거 상태일 수 있으므로 조회 응답이 기준이다.
    """
    # 1. 진위 확인 — 토스에 결제 조회. 본문이 아니라 이 응답이 진실이다.
    try:
        payment_info = TossPaymentClient().get_payment(
            payment_key, timeout=WEBHOOK_LOOKUP_TIMEOUT
        )
    except TossPaymentError as e:
        if e.status_code == 404:
            # 토스가 모르는 paymentKey — 위조 또는 잘못된 요청. 재전송받을 이유가 없다.
            logger.warning(
                f"Webhook for unknown payment rejected: payment_key={payment_key}, order_id={order_id}"
            )
            return _error_response("Unknown payment", status.HTTP_400_BAD_REQUEST)
        # 네트워크 오류 · 토스 5xx · 우리 키 설정 오류 → 500 으로 재전송을 받는다
        logger.error(
            f"Webhook payment lookup failed: payment_key={payment_key}, error={e.code}: {e.message}"
        )
        return _error_response(
            "Payment lookup failed", status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    if payment_info.get("orderId") != order_id:
        logger.warning(
            f"Webhook orderId mismatch rejected: webhook={order_id}, toss={payment_info.get('orderId')}, "
            f"payment_key={payment_key}"
        )
        return _error_response("orderId mismatch", status.HTTP_400_BAD_REQUEST)

    # 2. 현재 상태로 디스패치
    current_status = payment_info.get("status")
    if current_status != webhook_status:
        logger.info(
            f"Webhook status superseded: webhook={webhook_status}, current={current_status}, order_id={order_id}"
        )

    handler = STATUS_HANDLERS.get(current_status)

    try:
        if handler:
            handler(payment_info)
        elif current_status in IGNORED_STATUSES:
            logger.info(
                f"Webhook status ignored: status={current_status}, order_id={order_id}"
            )
        else:
            logger.warning(
                f"Webhook with unknown payment status: status={current_status}, order_id={order_id}"
            )

        return Response({"message": "Webhook processed"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return _error_response(
            "Processing failed", status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def _handle_deposit_callback(payload: dict) -> Response:
    """
    가상계좌 입금 웹훅(DEPOSIT_CALLBACK)

    paymentKey 가 본문에 없으므로 orderId 로 우리 Payment 를 찾아 paymentKey 를 얻고,
    secret 을 승인 응답에서 저장해 둔 값과 비교한 뒤 같은 조회 파이프라인을 탄다.
    """
    serializer = DepositCallbackSerializer(data=payload)
    if not serializer.is_valid():
        logger.error(f"Invalid deposit callback data: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    order_id = serializer.validated_data["orderId"]
    secret = serializer.validated_data["secret"]

    payment = Payment.objects.filter(toss_order_id=order_id).first()
    if payment is None or not payment.payment_key:
        # 우리 DB에 없는 주문, 또는 아직 승인(가상계좌 발급) 전 — 위조이거나 순서가 어긋난 요청
        logger.warning(
            f"Deposit callback for unknown order rejected: order_id={order_id}"
        )
        return _error_response("Unknown order", status.HTTP_400_BAD_REQUEST)

    # secret 검증 — 승인 응답의 Payment.secret 과 같아야 한다 (타이밍 공격 방지를 위해 compare_digest)
    if not payment.toss_secret or not hmac.compare_digest(secret, payment.toss_secret):
        logger.warning(
            f"Deposit callback with invalid secret rejected: order_id={order_id}"
        )
        return _error_response("Invalid secret", status.HTTP_400_BAD_REQUEST)

    return _lookup_and_dispatch(
        payment.payment_key, order_id, serializer.validated_data.get("status")
    )


@extend_schema(
    request=None,
    responses={
        200: {"type": "object", "properties": {"message": {"type": "string"}}},
        400: {"type": "object", "properties": {"error": {"type": "string"}}},
        500: {"type": "object", "properties": {"error": {"type": "string"}}},
    },
    summary="토스페이먼츠 결제 웹훅",
    description="""
토스페이먼츠에서 결제 상태가 변경되면 이 엔드포인트로 알림을 보냅니다.

**처리하는 이벤트:**
- `PAYMENT_STATUS_CHANGED` — data = Payment 객체
- `DEPOSIT_CALLBACK` — 가상계좌 입금 (평평한 본문, secret 검증)
- 그 외 이벤트는 200 으로 무시

**처리하는 결제 상태 (조회 API 응답의 `status`):**
- DONE: 결제 완료 (카드 승인 · 가상계좌 입금)
- WAITING_FOR_DEPOSIT: 가상계좌 입금 대기 (입금 취소 시 되돌림)
- CANCELED: 결제 취소
- ABORTED / EXPIRED: 승인 실패 · 입금 기한 만료

**진위 확인:**
- 웹훅 본문은 신뢰하지 않고, `paymentKey` 로 결제 조회 API를 호출한 응답으로 처리합니다.
    """,
    tags=["Webhooks"],
)
@csrf_exempt  # 외부 서비스 호출이므로 CSRF 검증 제외
@api_view(["POST"])
@permission_classes([AllowAny])  # 토스페이먼츠 서버에서 호출하므로 인증 불필요
def toss_webhook(request: Request) -> Response:
    """
    토스페이먼츠 웹훅 처리

    POST /api/webhooks/toss/

    1. 본문 파싱 — DEPOSIT_CALLBACK(평평한 본문)은 secret 검증 후, 그 외는 eventType 이
       PAYMENT_STATUS_CHANGED 일 때만 진행 (나머지는 200 으로 무시)
    2. 진위 확인 — paymentKey 로 결제 조회 API 호출, orderId 교차 검증
    3. 조회 응답의 status 로 핸들러 디스패치 (웹훅 본문의 status 는 참고만)
    4. 핸들러 예외 시 500 — 토스가 재전송하도록
    """

    # 가상계좌 입금 웹훅 — 본문 모양이 다르다
    if _is_deposit_callback(request.data):
        return _handle_deposit_callback(request.data)

    # 1. 웹훅 본문 파싱
    serializer = PaymentWebhookSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"Invalid webhook data: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # 지원하지 않는 이벤트는 무시 (CANCEL_STATUS_CHANGED, METHOD_UPDATED 등)
    if not serializer.is_supported:
        logger.info(f"Webhook event ignored: {serializer.validated_data['eventType']}")
        return Response({"message": "Event ignored"}, status=status.HTTP_200_OK)

    webhook_payment = serializer.validated_data["data"]
    payment_key = webhook_payment.get("paymentKey")
    order_id = webhook_payment.get("orderId")
    if not payment_key or not order_id:
        logger.warning("Webhook payload missing paymentKey or orderId")
        return _error_response(
            "paymentKey and orderId are required", status.HTTP_400_BAD_REQUEST
        )

    return _lookup_and_dispatch(payment_key, order_id, webhook_payment.get("status"))
