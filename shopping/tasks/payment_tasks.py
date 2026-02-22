"""결제 관련 Celery 태스크"""

import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from django.db import transaction
from django.db.models import F

from ..constants import (
    LOCK_CONTENTION_CRITICAL_THRESHOLD,
    LOCK_CONTENTION_WARNING_THRESHOLD,
    TOSS_NON_RETRYABLE_ERRORS,
    TOSS_RETRYABLE_ERRORS,
)
from ..models.cart import Cart
from ..models.order import Order
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..services.point_service import PointService
from ..utils.toss_payment import TossPaymentClient, TossPaymentError

# Chaos injection imports
from ..chaos.decorators import (
    inject_async_task_failure,
    inject_rollback_failure,
    inject_partial_failure_after_pg,
    AsyncTaskChaosError,
    PartialFailureError,
    # Phase 2 injections
    inject_phase2_rollback_failure,
    inject_phase2_silent_task_failure,
    Phase2RollbackFailureError,
    Phase2SilentTaskError,
)

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.call_toss_confirm_api",
    queue="external_api",
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    time_limit=30,
    soft_time_limit=25,
    acks_late=True,
)
def call_toss_confirm_api(self, payment_key: str, order_id: int, amount: int) -> dict:
    """
    Toss 결제 승인 API 호출 (외부 API만 호출, DB 작업 없음)

    Args:
        payment_key: 토스 결제 키
        order_id: 주문 ID
        amount: 결제 금액

    Returns:
        Toss API 응답 데이터

    Raises:
        TossPaymentError: API 호출 실패
    """
    logger.info(f"Toss API 호출 시작: order_id={order_id}, amount={amount}")

    try:
        toss_client = TossPaymentClient()
        payment_data = toss_client.confirm_payment(
            payment_key=payment_key,
            order_id=str(order_id),  # Toss API는 문자열 orderId를 받음
            amount=amount,
        )

        logger.info(f"Toss API 호출 성공: order_id={order_id}")
        return payment_data

    except SoftTimeLimitExceeded:
        # 타임아웃: 정리 작업 수행
        logger.error(f"Toss API 호출 타임아웃: order_id={order_id}")
        try:
            payment = Payment.objects.get(order_id=order_id)
            payment.status = "timeout"
            payment.save(update_fields=["status"])

            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message="결제 처리 시간 초과",
                data={"error_code": "TIMEOUT", "order_id": order_id},
            )
        except Exception as log_error:
            logger.error(f"타임아웃 로그 기록 실패: {str(log_error)}")

        # 타임아웃 시 롤백 태스크 실행
        rollback_payment_failure.delay(order_id, "결제 처리 시간 초과")
        raise

    except TossPaymentError as e:
        logger.error(f"Toss API 호출 실패: order_id={order_id}, error={e.message}")

        # 에러 로그 기록 및 Payment 상태만 업데이트
        try:
            payment = Payment.objects.get(order_id=order_id)
            payment.status = "aborted"
            payment.save(update_fields=["status"])

            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message=f"Toss API 호출 실패: {e.message}",
                data={"error_code": e.code, "error_message": e.message},
            )
        except Exception as log_error:
            logger.error(f"에러 로그 기록 실패: {str(log_error)}")

        # 1. 재시도 불가능한 오류는 롤백 후 즉시 실패
        if e.code in TOSS_NON_RETRYABLE_ERRORS:
            logger.error(f"재시도 불가능한 오류: {e.code} - {e.message}")
            rollback_payment_failure.delay(order_id, f"결제 실패: {e.message}")
            raise

        # 2. 명시적 재시도 가능 오류 (NETWORK_ERROR, TIMEOUT 등)
        if e.code in TOSS_RETRYABLE_ERRORS:
            logger.warning(f"재시도 가능 오류, 재시도: {e.code}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                logger.error(f"최대 재시도 횟수 초과: order_id={order_id}")
                rollback_payment_failure.delay(order_id, f"결제 실패 (재시도 초과): {e.message}")
                raise

        # 3. HTTP 5xx 오류는 재시도
        if hasattr(e, "status_code") and e.status_code >= 500:
            logger.warning(f"서버 오류, 재시도: {e.code}")
            try:
                raise self.retry(exc=e)
            except self.MaxRetriesExceededError:
                logger.error(f"최대 재시도 횟수 초과 (서버 오류): order_id={order_id}")
                rollback_payment_failure.delay(order_id, f"결제 실패 (서버 오류 재시도 초과): {e.message}")
                raise

        # 4. 그 외 4xx 오류는 롤백 후 재시도 안 함
        logger.error(f"클라이언트 오류, 재시도 안 함: {e.code}")
        rollback_payment_failure.delay(order_id, f"결제 실패: {e.message}")
        raise


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.finalize_payment_confirm",
    queue="payment_critical",
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    time_limit=60,
    soft_time_limit=55,
    acks_late=True,
)
def finalize_payment_confirm(self, toss_response: dict, payment_id: int, user_id: int) -> dict:
    """
    Toss API 결과를 받아 결제 최종 처리
    - Payment 상태 업데이트
    - 재고 차감 (sold_count 증가)
    - Order 상태 변경
    - 장바구니 비활성화
    - 포인트 적립 태스크 트리거 (비동기)

    Args:
        toss_response: Toss API 응답 데이터
        payment_id: Payment ID
        user_id: 사용자 ID

    Returns:
        처리 결과
    """

    logger.info(f"결제 최종 처리 시작: payment_id={payment_id}")

    # [CHAOS] Async task failure injection - simulates task failure
    try:
        inject_async_task_failure(task_name="finalize_payment_confirm", task_id=self.request.id if self.request else None)
    except AsyncTaskChaosError as e:
        logger.error(f"[CHAOS] Async task failure injected: payment_id={payment_id}")
        # Re-raise to trigger retry mechanism
        raise self.retry(exc=e, countdown=5)

    # [CHAOS PHASE 2] BP-23: Silent task failure (no DLQ)
    # This exception will exhaust retries WITHOUT proper DLQ routing
    try:
        inject_phase2_silent_task_failure(
            task_name="finalize_payment_confirm", task_id=self.request.id if self.request else None
        )
    except Phase2SilentTaskError as e:
        logger.error(f"[CHAOS BP-23] Silent task failure: payment_id={payment_id}")
        # INTENTIONAL: This re-raise does NOT route to DLQ
        # Self-healing should detect orphaned tasks via forensic scans
        raise self.retry(exc=e, countdown=2)

    # [CHAOS] Partial failure after PG success - simulate internal failure
    try:
        inject_partial_failure_after_pg(payment_id=payment_id, pg_response=toss_response)
    except PartialFailureError:
        logger.error(f"[CHAOS] Partial failure injected in finalize: payment_id={payment_id}")
        # Trigger rollback
        rollback_payment_failure.delay(
            order_id=Payment.objects.get(pk=payment_id).order_id, fail_reason="[CHAOS] Partial failure after PG success"
        )
        raise

    try:
        with transaction.atomic():
            start_time = time.time()

            # 1. Payment 업데이트 (짧은 트랜잭션)
            lock_start_time = time.time()
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            lock_elapsed = time.time() - lock_start_time

            if lock_elapsed > LOCK_CONTENTION_WARNING_THRESHOLD:
                logger.warning(
                    f"결제 락 획득 지연: payment_id={payment_id}, elapsed={lock_elapsed:.2f}s, "
                    f"possible_lock_contention=True"
                )

            # 중복 처리 방지
            if payment.is_paid:
                logger.warning(f"이미 처리된 결제: payment_id={payment_id}")
                return {"status": "already_processed", "payment_id": payment_id}

            payment.mark_as_paid(toss_response)
            order = payment.order

            # 2. 재고 차감 (sold_count만 증가, stock은 주문 생성 시 이미 차감)
            stock_start_time = time.time()
            for order_item in order.order_items.select_for_update():
                if order_item.product:
                    Product.objects.filter(pk=order_item.product.pk).update(sold_count=F("sold_count") + order_item.quantity)

            stock_elapsed = time.time() - stock_start_time
            if stock_elapsed > LOCK_CONTENTION_WARNING_THRESHOLD:
                logger.warning(
                    f"sold_count 업데이트 지연: payment_id={payment_id}, order_id={order.id}, "
                    f"elapsed={stock_elapsed:.2f}s, possible_lock_contention=True"
                )

            # 3. Order 상태 변경
            order.status = "paid"
            order.payment_method = payment.method
            order.save(update_fields=["status", "payment_method", "updated_at"])

            # 4. 장바구니 비활성화
            Cart.objects.filter(user_id=user_id, is_active=True).update(is_active=False)

            # 5. 로그 기록
            PaymentLog.objects.create(
                payment=payment,
                log_type="approve",
                message="결제 승인 완료",
                data=toss_response,
            )

            total_elapsed = time.time() - start_time

            # 동시성 모니터링: 전체 처리 시간 체크
            if total_elapsed > LOCK_CONTENTION_CRITICAL_THRESHOLD:
                logger.error(
                    f"결제 처리 심각한 지연: payment_id={payment_id}, order_id={order.id}, "
                    f"elapsed={total_elapsed:.2f}s, possible_deadlock=True"
                )
            elif total_elapsed > LOCK_CONTENTION_WARNING_THRESHOLD:
                logger.warning(
                    f"결제 처리 지연: payment_id={payment_id}, order_id={order.id}, "
                    f"elapsed={total_elapsed:.2f}s, possible_lock_contention=True"
                )

        logger.info(f"결제 최종 처리 완료: payment_id={payment_id}, order_id={order.id}, " f"elapsed={total_elapsed:.2f}s")

        # 6. 포인트 적립은 별도 태스크로 (비동기)
        from .point_tasks import add_points_after_payment

        if order.final_amount > 0:
            add_points_after_payment.delay(user_id, order.id)

        return {
            "status": "success",
            "payment_id": payment_id,
            "order_id": order.id,
        }

    except Exception as e:
        logger.error(f"결제 최종 처리 실패: payment_id={payment_id}, error={str(e)}")

        # 재시도
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.rollback_payment_failure",
    queue="payment_critical",
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    time_limit=30,
    soft_time_limit=25,
    acks_late=True,
)
def rollback_payment_failure(self, order_id: int, fail_reason: str = "") -> dict:
    """
    결제 실패 시 롤백 처리
    - 주문 상태를 payment_failed로 변경
    - 재고 복구 (stock 증가)
    - 사용한 포인트 환불

    Args:
        order_id: 주문 ID
        fail_reason: 실패 사유

    Returns:
        롤백 처리 결과
    """
    logger.info(f"결제 실패 롤백 시작: order_id={order_id}, reason={fail_reason}")

    # [CHAOS] Rollback failure injection - test retry mechanism
    try:
        inject_rollback_failure(order_id=order_id)
    except Exception as e:
        logger.error(f"[CHAOS] Rollback failure injected: order_id={order_id}")
        raise self.retry(exc=e, countdown=3)

    # [CHAOS PHASE 2] BP-22: Secondary rollback failure
    # Rollback itself fails, leaving order in stuck state
    try:
        inject_phase2_rollback_failure(order_id=order_id, rollback_type="stock_restore")
    except Phase2RollbackFailureError as e:
        logger.error(f"[CHAOS BP-22] Secondary rollback failure: order_id={order_id}")
        # This should trigger escalation to DLQ with ROLLBACK_FAILURE type
        raise self.retry(exc=e, countdown=5)

    try:
        with transaction.atomic():
            # 1. 주문 조회 및 락
            order = Order.objects.select_for_update().get(pk=order_id)

            # 이미 롤백 처리된 주문인지 확인 (멱등성)
            if order.status in ["payment_failed", "canceled"]:
                logger.warning(f"이미 롤백 처리된 주문: order_id={order_id}, status={order.status}")
                return {"status": "already_processed", "order_id": order_id}

            # 결제 완료된 주문은 롤백 불가
            if order.status == "paid":
                logger.error(f"결제 완료된 주문은 롤백 불가: order_id={order_id}")
                return {"status": "error", "message": "paid order cannot be rolled back", "order_id": order_id}

            # confirmed 또는 pending 상태만 롤백 가능
            if order.status not in ["pending", "confirmed"]:
                logger.warning(f"롤백 불가능한 상태: order_id={order_id}, status={order.status}")
                return {"status": "skipped", "reason": f"invalid status: {order.status}", "order_id": order_id}

            # 2. 재고 복구
            stock_restored_count = 0
            for item in order.order_items.select_for_update():
                if item.product:
                    Product.objects.filter(pk=item.product.pk).update(stock=F("stock") + item.quantity)
                    stock_restored_count += item.quantity
                    logger.info(
                        f"재고 복구: order_id={order_id}, product_id={item.product.pk}, "
                        f"product_name={item.product_name}, quantity={item.quantity}"
                    )

            # 3. 사용한 포인트 환불
            points_refunded = 0
            if order.used_points > 0:
                points_refunded = order.used_points
                PointService.add_points(
                    user=order.user,
                    amount=points_refunded,
                    type="payment_fail_refund",
                    order=order,
                    description=f"주문 #{order.order_number} 결제 실패로 인한 포인트 환불",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                        "fail_reason": fail_reason,
                    },
                )
                logger.info(f"포인트 환불: order_id={order_id}, user_id={order.user.id}, points={points_refunded}")

            # 4. 주문 상태 변경
            order.status = "payment_failed"
            order.failure_reason = fail_reason or "결제 승인 실패"
            order.save(update_fields=["status", "failure_reason", "updated_at"])

            # 5. Payment 상태 확인 및 업데이트
            try:
                payment = Payment.objects.get(order=order)
                if payment.status not in ["aborted", "failed"]:
                    payment.status = "aborted"
                    payment.save(update_fields=["status"])

                PaymentLog.objects.create(
                    payment=payment,
                    log_type="rollback",
                    message=f"결제 실패 롤백 처리: {fail_reason}",
                    data={
                        "order_id": order_id,
                        "stock_restored": stock_restored_count,
                        "points_refunded": points_refunded,
                    },
                )
            except Payment.DoesNotExist:
                logger.warning(f"Payment 없음 (롤백 계속): order_id={order_id}")

            logger.info(
                f"결제 실패 롤백 완료: order_id={order_id}, "
                f"stock_restored={stock_restored_count}, points_refunded={points_refunded}"
            )

            return {
                "status": "success",
                "order_id": order_id,
                "stock_restored": stock_restored_count,
                "points_refunded": points_refunded,
            }

    except Order.DoesNotExist:
        logger.error(f"주문을 찾을 수 없음: order_id={order_id}")
        return {"status": "error", "message": "order not found", "order_id": order_id}

    except Exception as e:
        logger.error(f"결제 실패 롤백 처리 실패: order_id={order_id}, error={str(e)}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.detect_orphaned_orders",
    queue="default",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def detect_orphaned_orders(self, threshold_minutes: int = 10) -> dict:
    """
    불일치 상태의 주문을 감지하고 자동으로 롤백을 트리거합니다.

    불일치 상태: Order=confirmed + Payment=aborted
    - 정상적인 경우 rollback_payment_failure가 즉시 처리함
    - 하지만 네트워크 오류, 워커 다운 등으로 롤백이 누락될 수 있음
    - 이 태스크가 주기적으로 이런 "고아 주문"을 감지하여 보상 처리

    Args:
        threshold_minutes: 이 시간 이상 불일치 상태가 지속된 주문만 감지

    Returns:
        감지 및 처리 결과
    """
    from datetime import timedelta
    from django.utils import timezone

    logger.info(f"Orphaned order 감지 시작: threshold={threshold_minutes}분")

    threshold_time = timezone.now() - timedelta(minutes=threshold_minutes)

    # confirmed + aborted 상태가 threshold_minutes 이상 지속된 주문 찾기
    orphaned_orders = Order.objects.filter(
        status="confirmed", payment__status="aborted", updated_at__lt=threshold_time
    ).select_related("payment")

    detected_count = 0
    triggered_count = 0
    errors = []

    for order in orphaned_orders:
        detected_count += 1
        try:
            logger.warning(
                f"Orphaned order 감지: order_id={order.id}, "
                f"order_status={order.status}, payment_status={order.payment.status}, "
                f"updated_at={order.updated_at}"
            )

            # 롤백 태스크 트리거
            rollback_payment_failure.delay(order_id=order.id, reason=f"Orphan detection (stale for >{threshold_minutes}min)")
            triggered_count += 1

        except Exception as e:
            error_msg = f"order_id={order.id}: {str(e)}"
            errors.append(error_msg)
            logger.error(f"Orphaned order 롤백 트리거 실패: {error_msg}")

    # 결과 로깅
    if detected_count > 0:
        logger.warning(
            f"Orphaned order 감지 완료: " f"detected={detected_count}, triggered={triggered_count}, errors={len(errors)}"
        )
    else:
        logger.info("Orphaned order 없음 - 시스템 정상")

    return {
        "status": "completed",
        "detected": detected_count,
        "triggered": triggered_count,
        "errors": errors,
    }


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.notify_payment_failure",
    queue="default",
    max_retries=3,
    retry_backoff=True,
)
def notify_payment_failure(self, order_id: int, failure_type: str, details: str = "", severity: str = "warning") -> dict:
    """
    결제 실패 및 롤백 관련 관리자 알림을 전송합니다.

    현재는 로그로 기록하며, 향후 Slack/Email 연동 가능합니다.

    Args:
        order_id: 주문 ID
        failure_type: 실패 유형 (rollback_failed, orphan_detected, max_retries_exceeded 등)
        details: 상세 정보
        severity: 심각도 (info, warning, error, critical)

    Returns:
        알림 전송 결과
    """
    # 로그 레벨에 따른 로거 선택
    log_message = f"[PAYMENT ALERT] type={failure_type}, order_id={order_id}, " f"severity={severity}, details={details}"

    if severity == "critical":
        logger.critical(log_message)
    elif severity == "error":
        logger.error(log_message)
    elif severity == "warning":
        logger.warning(log_message)
    else:
        logger.info(log_message)

    # TODO: Slack 연동 (향후 확장)
    # if settings.SLACK_WEBHOOK_URL:
    #     send_slack_notification(...)

    # TODO: Email 연동 (향후 확장)
    # if settings.ADMIN_EMAIL:
    #     send_email_notification(...)

    return {
        "status": "notified",
        "order_id": order_id,
        "failure_type": failure_type,
        "severity": severity,
    }
