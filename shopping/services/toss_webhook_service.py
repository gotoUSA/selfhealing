"""토스페이먼츠 웹훅 처리 서비스

웹훅 이벤트 처리 비즈니스 로직:
- 결제 완료, 취소, 실패 이벤트 처리
- Redis 기반 중복 웹훅 방어
- 재고/포인트/주문 상태 관리
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from ..models.cart import Cart
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..models.webhook_event import WebhookEvent
from .point_service import PointService

logger = logging.getLogger(__name__)

# Redis TTL 상수 (Toss 실시간 결제 환경에 최적화)
WEBHOOK_EVENT_TTL = 60  # 60초 - 웹훅 재전송 방어


class TossWebhookServiceError(Exception):
    """토스 웹훅 서비스 관련 에러"""

    pass


class TossWebhookService:
    """토스페이먼츠 웹훅 이벤트 처리 서비스"""

    # ========== 중복 방어 유틸리티 ==========

    @staticmethod
    def _get_webhook_cache_key(order_id: str, event_type: str) -> str:
        """웹훅 이벤트의 Redis 캐시 키 생성"""
        return f"webhook:toss:{order_id}:{event_type}"

    @staticmethod
    def is_webhook_duplicate(order_id: str, event_type: str) -> bool:
        """
        Redis에서 웹훅 중복 여부 확인 (TTL 60초)

        Args:
            order_id: 주문 ID
            event_type: 웹훅 이벤트 타입

        Returns:
            True if duplicate (already processed within 60s)
        """
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        return cache.get(cache_key) is not None

    @staticmethod
    def mark_webhook_processed(order_id: str, event_type: str) -> None:
        """Redis에 웹훅 처리 완료 마킹 (TTL 60초)"""
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        cache.set(cache_key, "1", timeout=WEBHOOK_EVENT_TTL)

    @staticmethod
    def log_webhook_event(order_id: str, event_type: str) -> None:
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

    # ========== 이벤트 핸들러 ==========

    @staticmethod
    @transaction.atomic
    def handle_payment_done(event_data: dict[str, Any]) -> None:
        """
        결제 완료 이벤트 처리

        결제창에서 결제 완료 후 confirm API 호출 전에
        웹훅이 먼저 도착할 수 있으므로 중복 처리 방지 필요

        중복 방어:
        1. Redis TTL 60초 (빠른 중복 체크)
        2. is_paid 상태 체크 (2차 방어)

        Args:
            event_data: 토스페이먼츠 웹훅 이벤트 데이터
        """
        order_id = event_data.get("orderId")

        # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어)
        if TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.DONE"):
            logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
            return

        # 2. Redis에 먼저 마킹 (다른 요청 차단)
        TossWebhookService.mark_webhook_processed(order_id, "PAYMENT.DONE")

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
            TossWebhookService.log_webhook_event(order_id, "PAYMENT.DONE")
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
        TossWebhookService.log_webhook_event(order_id, "PAYMENT.DONE")

        logger.info(f"Payment done webhook processed: {order_id}")

    @staticmethod
    @transaction.atomic
    def handle_payment_canceled(event_data: dict[str, Any]) -> None:
        """
        결제 취소 이벤트 처리

        중복 방어:
        1. Redis TTL 60초 (빠른 중복 체크)
        2. is_canceled 상태 체크 (2차 방어)

        Args:
            event_data: 토스페이먼츠 웹훅 이벤트 데이터
        """
        order_id = event_data.get("orderId")

        # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어)
        if TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.CANCELED"):
            logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
            return

        # 2. Redis에 먼저 마킹 (다른 요청 차단)
        TossWebhookService.mark_webhook_processed(order_id, "PAYMENT.CANCELED")

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
            TossWebhookService.log_webhook_event(order_id, "PAYMENT.CANCELED")
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
                        logger.warning(
                            f"sold_count 부족으로 0 설정: product_id={order_item.product.pk}, "
                            f"order_id={order.id}"
                        )

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
        TossWebhookService.log_webhook_event(order_id, "PAYMENT.CANCELED")

        logger.info(f"Payment canceled webhook processed: {order_id}")

    @staticmethod
    def handle_payment_failed(event_data: dict[str, Any]) -> None:
        """
        결제 실패 이벤트 처리

        Args:
            event_data: 토스페이먼츠 웹훅 이벤트 데이터
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
        TossWebhookService.log_webhook_event(order_id, "PAYMENT.FAILED")

        logger.info(f"Payment failed webhook processed: {order_id}")
