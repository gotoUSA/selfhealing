from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from ..models.cart import Cart
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..models.webhook_event import WebhookEvent
from ..serializers.payment_serializers import PaymentWebhookSerializer
from ..services.point_service import PointService
from ..utils.toss_payment import TossPaymentClient

# 로거 설정
logger = logging.getLogger(__name__)

# Redis TTL 상수 (Toss 실시간 결제 환경에 최적화)
WEBHOOK_EVENT_TTL = 60  # 60초 - 웹훅 재전송 방어


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
        return Response({"error": "Signature missing"}, status=status.HTTP_401_UNAUTHORIZED)

    # 토스페이먼츠 클라이언트로 서명 검증
    toss_client = TossPaymentClient()

    try:
        webhook_data = request.data

        # 서명 검증
        if not toss_client.verify_webhook(webhook_data, signature):
            logger.warning("Invalid webhook signature")
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

    except Exception as e:
        logger.error(f"Webhook signature verification error: {str(e)}")
        return Response(
            {"error": "Signature verification failed"},
            status=status.HTTP_400_BAD_REQUEST,
        )

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

    # 3. 이벤트 처리
    try:
        if event_type == "PAYMENT.DONE":
            handle_payment_done(event_data)

        elif event_type == "PAYMENT.CANCELED":
            handle_payment_canceled(event_data)

        elif event_type == "PAYMENT.FAILED":
            handle_payment_failed(event_data)

        elif event_type == "PAYMENT.PARTIAL_CANCELED":
            # 부분 취소는 향후 지원
            logger.info(f"Partial cancel event received: {event_data}")

        return Response({"message": "Webhook processed"}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return Response({"error": "Processing failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@transaction.atomic
def handle_payment_done(event_data: dict[str, Any]) -> None:
    """
    결제 완료 이벤트 처리

    결제창에서 결제 완료 후 confirm API 호출 전에
    웹훅이 먼저 도착할 수 있으므로 중복 처리 방지 필요

    중복 방어:
    1. Redis TTL 60초 (빠른 중복 체크)
    2. is_paid 상태 체크 (2차 방어)
    """
    order_id = event_data.get("orderId")

    # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어)
    if _is_webhook_duplicate(order_id, "PAYMENT.DONE"):
        logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
        return

    # 2. Redis에 먼저 마킹 (다른 요청 차단)
    _mark_webhook_processed(order_id, "PAYMENT.DONE")

    try:
        payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for order_id: {order_id}")
        return

    # 3. 이미 처리된 결제인지 확인 (2차 방어)
    if payment.is_paid:
        logger.info(f"Payment already processed: {order_id}")
        return

    # 최종 상태 보호 - 취소/실패된 결제는 재승인 불가
    if payment.status in ["canceled", "aborted"]:
        logger.info(f"Payment in final state {payment.status}, ignoring DONE event: {order_id}")
        return

    # Payment 정보 업데이트
    payment.mark_as_paid(event_data)

    # Order 상태 변경
    order = payment.order

    # 이미 paid 상태면 스킵 (confirm API에서 이미 처리)
    if order.status == "paid":
        logger.info(f"Order already paid: {order_id}")
        _log_webhook_event(order_id, "PAYMENT.DONE")
        return

    # sold_count 증가 (재고 차감은 주문 생성 시 이미 처리됨)
    if order.status != "paid":
        for order_item in order.order_items.select_for_update():
            if order_item.product:
                Product.objects.filter(pk=order_item.product.pk).update(
                    sold_count=F("sold_count") + order_item.quantity,
                )

    # 주문 상태 변경
    order.status = "paid"
    order.payment_method = payment.method or "card"
    order.save(update_fields=["status", "payment_method", "updated_at"])

    # 장바구니 비활성화
    Cart.objects.filter(user=order.user, is_active=True).update(is_active=False)

    # 포인트 적립 (confirm API에서 이미 적립된 경우 스킵)
    if order.user and order.earned_points == 0:
        earn_rate = order.user.get_earn_rate()
        product_amount = order.total_amount
        points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))

        if points_to_add > 0:
            PointService.add_points(
                user=order.user,
                amount=points_to_add,
                type="earn",
                order=order,
                description=f"주문 결제 완료 적립 ({order.order_number})",
                metadata={
                    "source": "webhook",
                    "earn_rate": f"{earn_rate}%",
                },
            )
            order.earned_points = points_to_add
            order.save(update_fields=["earned_points"])
            logger.info(f"Webhook 포인트 적립: order={order_id}, points={points_to_add}")

    # 웹훅 로그
    PaymentLog.objects.create(
        payment=payment,
        log_type="webhook",
        message="결제 완료 웹훅 처리",
        data=event_data,
    )

    # 웹훅 이벤트 로깅
    _log_webhook_event(order_id, "PAYMENT.DONE")

    logger.info(f"Payment done webhook processed: {order_id}")


@transaction.atomic
def handle_payment_canceled(event_data: dict[str, Any]) -> None:
    """
    결제 취소 이벤트 처리

    중복 방어:
    1. Redis TTL 60초 (빠른 중복 체크)
    2. is_canceled 상태 체크 (2차 방어)
    """
    order_id = event_data.get("orderId")

    # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어)
    if _is_webhook_duplicate(order_id, "PAYMENT.CANCELED"):
        logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
        return

    # 2. Redis에 먼저 마킹 (다른 요청 차단)
    _mark_webhook_processed(order_id, "PAYMENT.CANCELED")

    try:
        payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for order_id: {order_id}")
        return

    # 3. 이미 취소된 결제인지 확인 (2차 방어)
    if payment.is_canceled:
        logger.info(f"Payment already canceled: {order_id}")
        return

    # 최종 상태 보호 - 실패한 결제는 취소 불필요
    if payment.status in ["aborted"]:
        logger.info(f"Payment already failed (aborted), ignoring CANCELED event: {order_id}")
        return

    # Payment 정보 업데이트
    payment.mark_as_canceled(event_data)

    # Order 상태 변경
    order = payment.order

    # 이미 cancelled 상태면 스킵
    if order.status == "canceled":
        logger.info(f"Order already cancelled: {order_id}")
        _log_webhook_event(order_id, "PAYMENT.CANCELED")
        return

    # 재고 복구 (paid 상태였던 경우만)
    if order.status in ["paid", "preparing"]:
        for order_item in order.order_items.all():
            if order_item.product:
                updated = Product.objects.filter(
                    pk=order_item.product.pk,
                    sold_count__gte=order_item.quantity,
                ).update(
                    stock=F("stock") + order_item.quantity,
                    sold_count=F("sold_count") - order_item.quantity,
                )

                if updated == 0:
                    Product.objects.filter(pk=order_item.product.pk).update(
                        stock=F("stock") + order_item.quantity,
                        sold_count=0,
                    )
                    logger.warning(f"sold_count 부족으로 0 설정: product_id={order_item.product.pk}, " f"order_id={order.id}")

    # 포인트 회수 (상태 변경 전)
    if order.user and order.status in ["paid", "preparing"]:
        points_to_deduct = order.earned_points
        if points_to_deduct > 0:
            PointService.use_points(
                user=order.user,
                amount=points_to_deduct,
                type="cancel_deduct",
                order=order,
                description=f"주문 취소로 인한 적립 포인트 회수 ({order.order_number})",
            )

    # 주문 상태 변경
    order.status = "canceled"
    order.save(update_fields=["status", "updated_at"])

    # 웹훅 로그
    PaymentLog.objects.create(
        payment=payment,
        log_type="webhook",
        message="결제 취소 웹훅 처리",
        data=event_data,
    )

    # 웹훅 이벤트 로깅
    _log_webhook_event(order_id, "PAYMENT.CANCELED")

    logger.info(f"Payment canceled webhook processed: {order_id}")


def handle_payment_failed(event_data: dict[str, Any]) -> None:
    """
    결제 실패 이벤트 처리
    """
    order_id = event_data.get("orderId")
    fail_reason = event_data.get("failReason", "")

    try:
        payment = Payment.objects.get(toss_order_id=order_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for order_id: {order_id}")
        return

    # 이미 실패 처리된 경우 스킵
    if payment.status in ["aborted", "failed"]:
        logger.info(f"Payment already failed: {order_id}")
        return

    # 최종 상태 보호 - 완료/취소된 결제는 실패 처리 불가
    if payment.status in ["done", "canceled"]:
        logger.info(f"Payment in final state {payment.status}, ignoring FAILED event: {order_id}")
        return

    # Payment 실패 처리
    payment.mark_as_failed(fail_reason)

    # 웹훅 로그
    PaymentLog.objects.create(
        payment=payment,
        log_type="webhook",
        message=f"결제 실패 웹훅 처리: {fail_reason}",
        data=event_data,
    )

    # 웹훅 이벤트 로깅
    _log_webhook_event(order_id, "PAYMENT.FAILED")

    logger.info(f"Payment failed webhook processed: {order_id}")


def _get_webhook_cache_key(order_id: str, event_type: str) -> str:
    """웹훅 이벤트의 Redis 캐시 키 생성"""
    return f"webhook:toss:{order_id}:{event_type}"


def _is_webhook_duplicate(order_id: str, event_type: str) -> bool:
    """
    Redis에서 웹훅 중복 여부 확인 (TTL 60초)

    Returns:
        True if duplicate (already processed within 60s)
    """
    cache_key = _get_webhook_cache_key(order_id, event_type)
    return cache.get(cache_key) is not None


def _mark_webhook_processed(order_id: str, event_type: str) -> None:
    """Redis에 웹훅 처리 완료 마킹 (TTL 60초)"""
    cache_key = _get_webhook_cache_key(order_id, event_type)
    cache.set(cache_key, "1", timeout=WEBHOOK_EVENT_TTL)


def _log_webhook_event(order_id: str, event_type: str) -> None:
    """웹훅 이벤트 DB 로깅 (감사 추적용)"""
    try:
        WebhookEvent.objects.create(
            event_id=f"toss:{order_id}:{event_type}",
            event_type=event_type,
            source="toss",
            order_id=order_id,
        )
    except Exception as e:
        # 로깅 실패는 무시 (핵심 로직에 영향 주지 않음)
        logger.warning(f"Failed to log webhook event: {e}")
