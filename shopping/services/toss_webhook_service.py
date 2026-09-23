"""토스페이먼츠 웹훅 처리 서비스

PAYMENT_STATUS_CHANGED 웹훅의 결제 상태별 비즈니스 로직:
- DONE → 결제 완료, CANCELED → 결제 취소, ABORTED/EXPIRED → 결제 실패, WAITING_FOR_DEPOSIT → 가상계좌 입금 대기
- 입력은 토스 결제 조회 API 응답(Payment 객체) — 뷰가 웹훅 본문 대신 조회 응답을 넘긴다
- Redis 기반 중복 웹훅 방어 (토스는 200 을 못 받으면 최대 7회 재전송한다)
- 재고/포인트/주문 상태 관리
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from ..constants import TOSS_PAYMENT_STATUS_EXPIRED
from ..models.cart import Cart
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..models.webhook_event import WebhookEvent
from .point_service import PointService

logger = logging.getLogger(__name__)

# Redis TTL 상수 — 토스 첫 재전송이 1분 뒤이므로, 정상 처리(커밋)된 이벤트의 첫 재전송을 DB 전에 걸러낸다
WEBHOOK_EVENT_TTL = 60  # 60초


class TossWebhookServiceError(Exception):
    """토스 웹훅 서비스 관련 에러"""

    pass


def _restore_stock_for_order(order, restore_sold_count: bool = True) -> None:
    """
    주문의 재고 복구 헬퍼 함수

    Args:
        order: 주문 객체
        restore_sold_count: sold_count도 복구할지 여부
    """
    for order_item in order.order_items.all():
        if not order_item.product:
            continue

        if restore_sold_count:
            # 재고 복구 + sold_count 차감
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
        else:
            # 재고만 복구 (sold_count는 아직 증가하지 않음)
            Product.objects.filter(pk=order_item.product.pk).update(
                stock=F("stock") + order_item.quantity,
            )
            logger.info(
                f"재고 복구: product_id={order_item.product.pk}, " f"quantity={order_item.quantity}, order_id={order.id}"
            )


class TossWebhookService:
    """토스페이먼츠 웹훅 이벤트 처리 서비스"""

    # ========== 중복 방어 유틸리티 ==========

    @staticmethod
    def _get_webhook_cache_key(order_id: str, event_type: str) -> str:
        """웹훅 이벤트의 Redis 캐시 키 생성 (event_type = 토스 결제 상태: DONE, CANCELED, ...)"""
        return f"webhook:toss:{order_id}:{event_type}"

    @staticmethod
    def is_webhook_duplicate(order_id: str, event_type: str) -> bool:
        """
        Redis에서 웹훅 중복 여부 확인 (TTL 60초)

        Args:
            order_id: 주문 ID
            event_type: 토스 결제 상태 (DONE, CANCELED, ...)

        Returns:
            True if duplicate (already processed within 60s)
        """
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        return cache.get(cache_key) is not None

    @staticmethod
    def mark_webhook_processed(order_id: str, event_type: str) -> None:
        """
        Redis에 웹훅 처리 완료 마킹 (TTL 60초)

        cache.add 는 키가 없을 때만 쓰는 원자 연산(SETNX)이라 같은 이벤트가 겹쳐도 마킹은 한 번이다.
        """
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        cache.add(cache_key, "1", timeout=WEBHOOK_EVENT_TTL)

    @staticmethod
    def mark_webhook_processed_on_commit(order_id: str, event_type: str) -> None:
        """
        현재 트랜잭션이 커밋된 뒤에만 Redis 마킹

        마킹을 커밋 전에 하면 핸들러가 예외로 롤백돼도 키가 60초 남아, 토스의 첫 재전송(+1분)이
        1층에서 "중복"으로 버려질 수 있다. 커밋 뒤로 미루면 롤백된 처리는 흔적을 남기지 않는다.
        """
        transaction.on_commit(lambda: TossWebhookService.mark_webhook_processed(order_id, event_type))

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
        결제 완료(status=DONE) 처리

        결제창에서 결제 완료 후 confirm API 호출 전에
        웹훅이 먼저 도착할 수 있으므로 중복 처리 방지 필요

        중복 방어:
        1. Redis TTL 60초 (빠른 중복 체크 — 마킹은 커밋 뒤에만, cache.add 로 원자적으로)
        2. is_paid 상태 체크 (2차 방어, 행 락 안에서 — 진짜 보장)

        Args:
            event_data: 토스 Payment 객체 (결제 조회 API 응답)
        """
        order_id = event_data.get("orderId")

        # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어) — 빠른 필터일 뿐, 보장은 아래 행 락이 한다
        if TossWebhookService.is_webhook_duplicate(order_id, "DONE"):
            logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
            return

        try:
            payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
        except Payment.DoesNotExist:
            logger.error(f"Payment not found for order_id: {order_id}")
            return

        # 2. 이미 처리된 결제인지 확인 (2차 방어)
        if payment.is_paid:
            logger.info(f"Payment already processed: {order_id}")
            return

        # 우리는 실패로 끝냈는데 토스의 현재 상태는 승인(DONE) — 고객은 청구됐다.
        # 자동으로 되살리지 않는다(롤백이 재고를 이미 돌려놨다). 사람이 대사·환불하도록 기록하고 알린다.
        if payment.status == "aborted":
            logger.critical(f"Charged payment recorded as failed, manual reconciliation needed: {order_id}")
            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message="실패 처리한 결제에 토스 승인(DONE) 확인 — 수동 대사·환불 필요",
                data=event_data,
            )
            order_pk = payment.order_id
            payment_key = event_data.get("paymentKey", "")

            def _notify() -> None:
                try:
                    from ..tasks.payment_tasks import notify_payment_failure

                    notify_payment_failure.delay(
                        order_pk,
                        "charged_but_failed",
                        details=f"토스 DONE(paymentKey={payment_key}) 인데 결제는 aborted — 대사·환불 필요",
                        severity="critical",
                    )
                except Exception as notify_error:
                    logger.error(f"Charged-but-failed notification failed: {order_id}, error={notify_error}")

            transaction.on_commit(_notify)
            return

        # 최종 상태 보호 - 취소된 결제는 재승인 불가
        if payment.status == "canceled":
            logger.info(f"Payment in final state {payment.status}, ignoring DONE event: {order_id}")
            return

        # 만료 처리(재고 복구·주문 취소) 뒤에 입금이 잡힌 경우 — 자동으로 되살리지 않고 사람이 본다
        if payment.status == "expired":
            logger.error(f"Deposit arrived after expiry, manual reconciliation needed: {order_id}")
            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message="만료 처리된 결제에 입금 확인 — 수동 정산 필요",
                data=event_data,
            )
            return

        # 3. 여기서부터 실제 처리 — 마킹은 커밋된 뒤에만 (롤백되면 키도 없다)
        TossWebhookService.mark_webhook_processed_on_commit(order_id, "DONE")

        # Payment 정보 업데이트
        payment.mark_as_paid(event_data)

        # Order 상태 변경
        order = payment.order

        # 이미 paid 상태면 스킵 (confirm API에서 이미 처리)
        if order.status == "paid":
            logger.info(f"Order already paid: {order_id}")
            TossWebhookService.log_webhook_event(order_id, "DONE")
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
        TossWebhookService.log_webhook_event(order_id, "DONE")

        logger.info(f"Payment done webhook processed: {order_id}")

    @staticmethod
    @transaction.atomic
    def handle_payment_canceled(event_data: dict[str, Any]) -> None:
        """
        결제 취소(status=CANCELED) 처리

        중복 방어:
        1. Redis TTL 60초 (빠른 중복 체크 — 마킹은 커밋 뒤에만, cache.add 로 원자적으로)
        2. is_canceled 상태 체크 (2차 방어, 행 락 안에서 — 진짜 보장)

        Args:
            event_data: 토스 Payment 객체 (결제 조회 API 응답, 취소 이력은 cancels[])
        """
        order_id = event_data.get("orderId")

        # 1. Redis 중복 체크 (60초 내 동일 웹훅 방어) — 빠른 필터일 뿐, 보장은 아래 행 락이 한다
        if TossWebhookService.is_webhook_duplicate(order_id, "CANCELED"):
            logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
            return

        try:
            payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
        except Payment.DoesNotExist:
            logger.error(f"Payment not found for order_id: {order_id}")
            return

        # 2. 이미 취소된 결제인지 확인 (2차 방어)
        if payment.is_canceled:
            logger.info(f"Payment already canceled: {order_id}")
            return

        # 최종 상태 보호 - 실패한 결제는 취소 불필요
        if payment.status in ["aborted"]:
            logger.info(f"Payment already failed (aborted), ignoring CANCELED event: {order_id}")
            return

        # 3. 여기서부터 실제 처리 — 마킹은 커밋된 뒤에만 (롤백되면 키도 없다)
        TossWebhookService.mark_webhook_processed_on_commit(order_id, "CANCELED")

        # Payment 정보 업데이트
        payment.mark_as_canceled(event_data)

        # Order 상태 변경
        order = payment.order

        # 이미 cancelled 상태면 스킵
        if order.status == "canceled":
            logger.info(f"Order already cancelled: {order_id}")
            TossWebhookService.log_webhook_event(order_id, "CANCELED")
            return

        # 재고 복구 (재고가 차감된 상태들)
        if order.status in ["paid", "preparing"]:
            _restore_stock_for_order(order, restore_sold_count=True)
        elif order.status == "confirmed":
            _restore_stock_for_order(order, restore_sold_count=False)

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
        TossWebhookService.log_webhook_event(order_id, "CANCELED")

        logger.info(f"Payment canceled webhook processed: {order_id}")

    @staticmethod
    @transaction.atomic
    def handle_waiting_for_deposit(event_data: dict[str, Any]) -> None:
        """
        가상계좌 입금 대기(status=WAITING_FOR_DEPOSIT) 처리

        두 경우가 온다:
        1. 발급 직후 — 승인 마감(finalize)보다 웹훅이 먼저 도착하면 여기서 입금 대기로 기록한다
        2. 입금 취소 — 송금 한도 초과·네트워크 오류로 토스가 DONE 을 WAITING_FOR_DEPOSIT 으로 되돌린 경우.
           이미 결제 완료 처리했으면 판매량·적립 포인트를 되돌리고 주문을 confirmed 로 내린다 (재고는 그대로 잡아 둔다)

        Args:
            event_data: 토스 Payment 객체 (결제 조회 API 응답)
        """
        order_id = event_data.get("orderId")

        try:
            payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
        except Payment.DoesNotExist:
            logger.error(f"Payment not found for order_id: {order_id}")
            return

        if payment.is_waiting_for_deposit:
            logger.info(f"Payment already waiting for deposit: {order_id}")
            return

        if payment.status in ["canceled", "aborted", "expired"]:
            logger.info(f"Payment in final state {payment.status}, ignoring WAITING_FOR_DEPOSIT event: {order_id}")
            return

        order = payment.order

        if payment.is_paid:
            # 입금 취소 — 결제 완료 처리를 되돌린다
            logger.warning(f"Deposit reverted by Toss, un-paying order: {order_id}")

            for order_item in order.order_items.select_for_update():
                if order_item.product:
                    Product.objects.filter(pk=order_item.product.pk, sold_count__gte=order_item.quantity).update(
                        sold_count=F("sold_count") - order_item.quantity,
                    )

            if order.user and order.earned_points > 0:
                PointService.use_points(
                    user=order.user,
                    amount=order.earned_points,
                    type="cancel_deduct",
                    order=order,
                    description=f"가상계좌 입금 취소로 인한 적립 포인트 회수 ({order.order_number})",
                )
                order.earned_points = 0

            order.status = "confirmed"
            order.save(update_fields=["status", "earned_points", "updated_at"])

            PaymentLog.objects.create(
                payment=payment,
                log_type="webhook",
                message="가상계좌 입금 취소 — 입금 대기로 복귀",
                data=event_data,
            )
        else:
            PaymentLog.objects.create(
                payment=payment,
                log_type="webhook",
                message="가상계좌 발급 웹훅 처리, 입금 대기",
                data=event_data,
            )

        payment.mark_as_waiting_for_deposit(event_data)

        TossWebhookService.log_webhook_event(order_id, "WAITING_FOR_DEPOSIT")

        logger.info(f"Waiting-for-deposit webhook processed: {order_id}")

    @staticmethod
    def handle_payment_failed(event_data: dict[str, Any]) -> None:
        """
        결제 실패(status=ABORTED 승인 실패 / EXPIRED 가상계좌 입금 기한 만료) 처리

        결제 실패 시 롤백 처리:
        1. Payment 상태를 aborted(승인 실패) / expired(기한 만료)로 변경
        2. 롤백 태스크 트리거 (재고 복구, 포인트 환불, 주문 취소)

        Args:
            event_data: 토스 Payment 객체 (결제 조회 API 응답, 실패 사유는 failure.message)
        """
        from ..tasks.payment_tasks import rollback_payment_failure

        order_id = event_data.get("orderId")
        toss_status = event_data.get("status", "ABORTED")
        failure = event_data.get("failure") or {}
        fail_reason = failure.get("message") or (
            "가상계좌 입금 기한 만료" if toss_status == TOSS_PAYMENT_STATUS_EXPIRED else ""
        )

        try:
            payment = Payment.objects.get(toss_order_id=order_id)
        except Payment.DoesNotExist:
            logger.error(f"Payment not found for order_id: {order_id}")
            return

        # 이미 실패/만료 처리된 경우 스킵
        if payment.status in ["aborted", "failed", "expired"]:
            logger.info(f"Payment already failed: {order_id} ({payment.status})")
            return

        # 최종 상태 보호 - 완료/취소된 결제는 실패 처리 불가
        if payment.status in ["done", "canceled"]:
            logger.info(f"Payment in final state {payment.status}, ignoring FAILED event: {order_id}")
            return

        # Payment 실패 처리 — 가상계좌 입금 기한 경과는 expired, 승인 실패는 aborted
        if toss_status == TOSS_PAYMENT_STATUS_EXPIRED:
            payment.mark_as_expired(fail_reason)
        else:
            payment.mark_as_failed(fail_reason)

        # 롤백 태스크 트리거 (재고 복구, 포인트 환불)
        order = payment.order
        if order.status in ["pending", "confirmed"]:
            rollback_payment_failure.delay(order.id, fail_reason or "결제 실패 (웹훅)")
            logger.info(f"Rollback task triggered for order: {order.id}")

        # 웹훅 로그
        PaymentLog.objects.create(
            payment=payment,
            log_type="webhook",
            message=f"결제 실패 웹훅 처리: {fail_reason}",
            data=event_data,
        )

        # 웹훅 이벤트 로깅
        TossWebhookService.log_webhook_event(order_id, toss_status)

        logger.info(f"Payment failed webhook processed: {order_id} ({toss_status})")
