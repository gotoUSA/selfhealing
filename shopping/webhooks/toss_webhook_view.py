from __future__ import annotations

import logging
from typing import Callable

from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from ..serializers.payment_serializers import PaymentWebhookSerializer
from ..services.toss_webhook_service import TossWebhookService
from ..utils.toss_payment import TossPaymentClient

# 로거 설정
logger = logging.getLogger(__name__)

# 이벤트 핸들러 매핑 (if-elif 체인 대체)
EVENT_HANDLERS: dict[str, Callable[[dict], None]] = {
    "PAYMENT.DONE": TossWebhookService.handle_payment_done,
    "PAYMENT.CANCELED": TossWebhookService.handle_payment_canceled,
    "PAYMENT.FAILED": TossWebhookService.handle_payment_failed,
}


def _error_response(message: str, status_code: int) -> Response:
    """에러 응답 생성 헬퍼"""
    return Response({"error": message}, status=status_code)


@extend_schema(
    request=None,
    responses={
        200: {"type": "object", "properties": {"message": {"type": "string"}}},
        400: {"type": "object", "properties": {"error": {"type": "string"}}},
        401: {"type": "object", "properties": {"error": {"type": "string"}}},
    },
    summary="토스페이먼츠 결제 웹훅",
    description="""
토스페이먼츠에서 결제 상태가 변경되면 이 엔드포인트로 알림을 보냅니다.

**웹훅 이벤트 타입:**
- PAYMENT.DONE: 결제 완료
- PAYMENT.CANCELED: 결제 취소
- PAYMENT.FAILED: 결제 실패

**보안:**
- 웹훅 서명 검증으로 토스페이먼츠에서 보낸 요청인지 확인
    """,
    tags=["Webhooks"],
)
@csrf_exempt  # 외부 서비스 호출이므로 CSRF 검증 제외
@api_view(["POST"])
@permission_classes([AllowAny])  # 토스페이먼츠 서버에서 호출하므로 인증 불필요
def toss_webhook(request: Request) -> Response:
    """
    토스페이먼츠 웹훅 처리

    토스페이먼츠에서 결제 상태가 변경되면 이 엔드포인트로 알림을 보냅니다.

    POST /api/webhooks/toss/

    웹훅 이벤트 타입:
    - PAYMENT.DONE: 결제 완료
    - PAYMENT.CANCELED: 결제 취소
    - PAYMENT.FAILED: 결제 실패
    - PAYMENT.PARTIAL_CANCELED: 부분 취소 (미지원)

    보안:
    - 웹훅 서명 검증으로 토스페이먼츠에서 보낸 요청인지 확인
    - 중복 처리 방지
    """

    # 1. 웹훅 서명 검증
    signature = request.headers.get("X-Toss-Webhook-Signature")
    if not signature:
        logger.warning("Webhook signature missing")
        return _error_response("Signature missing", status.HTTP_401_UNAUTHORIZED)

    # 토스페이먼츠 클라이언트로 서명 검증
    toss_client = TossPaymentClient()
    webhook_data = request.data

    try:
        if not toss_client.verify_webhook(webhook_data, signature):
            logger.warning("Invalid webhook signature")
            return _error_response("Invalid signature", status.HTTP_401_UNAUTHORIZED)
    except Exception as e:
        logger.error(f"Webhook signature verification error: {str(e)}")
        return _error_response("Signature verification failed", status.HTTP_400_BAD_REQUEST)

    # 2. 웹훅 데이터 파싱
    serializer = PaymentWebhookSerializer(data=webhook_data)
    if not serializer.is_valid():
        logger.error(f"Invalid webhook data: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # 지원하지 않는 이벤트는 무시
    if not serializer.is_supported:
        return Response({"message": "Event ignored"}, status=status.HTTP_200_OK)

    event_type = serializer.validated_data["eventType"]
    event_data = serializer.validated_data["data"]

    # 3. 이벤트 핸들러 디스패치
    handler = EVENT_HANDLERS.get(event_type)

    try:
        if handler:
            handler(event_data)
        elif event_type == "PAYMENT.PARTIAL_CANCELED":
            # 부분 취소는 향후 지원
            logger.info(f"Partial cancel event received: {event_data}")

        return Response({"message": "Webhook processed"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return _error_response("Processing failed", status.HTTP_500_INTERNAL_SERVER_ERROR)
