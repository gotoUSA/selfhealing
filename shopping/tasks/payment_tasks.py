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
    TOSS_CONFIRM_SOFT_TIME_LIMIT,
    TOSS_CONFIRM_TIME_LIMIT,
    TOSS_CONFIRM_TIMEOUT,
    TOSS_NON_RETRYABLE_ERRORS,
    TOSS_PAYMENT_STATUS_DONE,
    TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT,
    TOSS_RECONCILE_ERRORS,
    TOSS_RETRYABLE_ERRORS,
)
from ..models.cart import Cart
from ..models.order import Order
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..services.payment_service import PaymentService
from ..services.point_service import PointService
from ..utils.toss_payment import TossPaymentClient, TossPaymentError
from .retry_policy import retry_unless_exhausted, retry_with_backoff

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


def _mark_confirm_aborted(order_id: int) -> None:
    """롤백을 결정한 분기에서만 결제를 aborted 로 표시한다 (재시도 중에는 in_progress 를 유지)

    롤백 태스크 발행이 유실돼도 detect_orphaned_orders 가 confirmed + aborted 로 찾아 정리한다.
    """
    try:
        Payment.objects.filter(order_id=order_id).update(status="aborted")
    except Exception as mark_error:
        logger.error(f"결제 aborted 표시 실패: order_id={order_id}, error={str(mark_error)}")


def _reconcile_confirmed_payment(
    task,
    toss_client: TossPaymentClient,
    payment_key: str,
    order_id: int,
    amount: int,
    error: TossPaymentError,
    toss_order_id: str | None = None,
) -> dict | None:
    """
    "이미 처리된 결제" 오류를 받았을 때 결제 조회 API 로 실제 상태를 확인한다 (PG 대사)

    우리 승인 요청이 토스에 닿았는데 응답만 잃은 경우(타임아웃), 재시도는 ALREADY_PROCESSED_PAYMENT 를
    받는다. 이때 롤백하면 고객 카드는 긁혔는데 우리 DB 는 실패로 남는다(고아 결제). 그래서 롤백 전에
    조회 API 로 확인하고, 승인 완료(DONE) 이며 주문번호·금액이 일치하면 그 응답을 승인 응답 대신 돌려준다.

    Returns:
        승인이 확인되면 조회 응답 (finalize_payment_confirm 이 그대로 받을 수 있는 형식), 아니면 None

    Raises:
        조회 자체가 실패하면 재시도. 재시도까지 소진되면 롤백하지 않고 운영자 알림 후 실패로 남긴다
        — 확인 없이 롤백하는 것이 바로 이 함수가 막으려는 사고이기 때문
    """
    logger.warning(f"승인 오류 {error.code} 수신, 조회 API 로 대사 시작: order_id={order_id}")

    try:
        payment_data = toss_client.get_payment(payment_key, timeout=TOSS_CONFIRM_TIMEOUT)
    except TossPaymentError as lookup_error:
        logger.error(f"결제 조회 실패: order_id={order_id}, error={lookup_error.code} - {lookup_error.message}")
        retry_unless_exhausted(task, lookup_error)
        logger.critical(
            f"결제 조회 재시도 소진 — 승인 여부 미확인, 수동 대사 필요: order_id={order_id}, "
            f"payment_key={payment_key}"
        )
        try:
            payment = Payment.objects.get(order_id=order_id)
            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message="승인 여부 미확인 (조회 API 재시도 소진) — 수동 대사 필요",
                data={"error_code": error.code, "lookup_error_code": lookup_error.code, "order_id": order_id},
            )
        except Exception as log_error:
            logger.error(f"대사 실패 로그 기록 실패: {str(log_error)}")
        notify_payment_failure.delay(
            order_id,
            "reconcile_failed",
            details=f"{error.code} 수신 후 조회 API 실패({lookup_error.code}) — 승인 여부 미확인",
            severity="critical",
        )
        raise

    status = payment_data.get("status")
    expected_order_id = toss_order_id or str(order_id)
    order_id_matches = str(payment_data.get("orderId")) == expected_order_id
    amount_matches = payment_data.get("totalAmount") is not None and int(payment_data["totalAmount"]) == int(amount)

    # 카드 승인(DONE)뿐 아니라 가상계좌 발급(WAITING_FOR_DEPOSIT)도 "승인 요청이 닿은" 상태 — finalize 가 입금 대기로 기록한다
    if status in (TOSS_PAYMENT_STATUS_DONE, TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT) and order_id_matches and amount_matches:
        logger.warning(f"조회 API 로 승인 확인, 정상 마감으로 진행: order_id={order_id}, payment_key={payment_key}")
        try:
            payment = Payment.objects.get(order_id=order_id)
            PaymentLog.objects.create(
                payment=payment,
                log_type="approve",
                message=f"{error.code} 수신 → 조회 API 로 승인 확인 (대사)",
                data={"error_code": error.code, "order_id": order_id, "status": status},
            )
        except Exception as log_error:
            logger.error(f"대사 로그 기록 실패: {str(log_error)}")
        return payment_data

    logger.error(
        f"조회 결과 승인 아님, 롤백 진행: order_id={order_id}, status={status}, "
        f"orderId={payment_data.get('orderId')}, totalAmount={payment_data.get('totalAmount')}, expected_amount={amount}"
    )
    return None


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.call_toss_confirm_api",
    queue="external_api",
    max_retries=3,
    # 수동 self.retry 에도 적용되도록 retry_countdown 이 읽는다 (0~10초, 0~20초, 0~40초)
    retry_backoff=10,
    retry_backoff_max=60,
    retry_jitter=True,
    # HTTP 타임아웃(TOSS_CONFIRM_TIMEOUT)이 이 제한보다 먼저 끝나야 한다 — constants 참고
    time_limit=TOSS_CONFIRM_TIME_LIMIT,
    soft_time_limit=TOSS_CONFIRM_SOFT_TIME_LIMIT,
    acks_late=True,
)
def call_toss_confirm_api(self, payment_key: str, order_id: int, amount: int, toss_order_id: str | None = None) -> dict:
    """
    Toss 결제 승인 API 호출 (외부 API만 호출, DB 작업 없음)

    Args:
        payment_key: 토스 결제 키
        order_id: 주문 ID (우리 PK — 로그·DB 조회용)
        amount: 결제 금액
        toss_order_id: 결제창에 넘긴 토스 orderId (주문번호). 없으면 str(order_id) — 이미 큐에 있던 옛 메시지 호환

    Returns:
        Toss API 응답 데이터

    Raises:
        TossPaymentError: API 호출 실패
    """
    toss_order_id = toss_order_id or str(order_id)
    logger.info(f"Toss API 호출 시작: order_id={order_id}, toss_order_id={toss_order_id}, amount={amount}")

    try:
        toss_client = TossPaymentClient()
        payment_data = toss_client.confirm_payment(
            payment_key=payment_key,
            order_id=toss_order_id,
            amount=amount,
        )

        logger.info(f"Toss API 호출 성공: order_id={order_id}")
        return payment_data

    except SoftTimeLimitExceeded as e:
        # 승인 요청이 토스에 닿았는지 모른다 — 여기서 롤백하면 토스가 승인한 결제를 실패로 만든다(고아 결제).
        # HTTP 타임아웃이 soft limit 보다 짧아 정상적으로는 여기 오지 않는다. 오면 재시도로 넘겨
        # 첫 승인 또는 ALREADY_PROCESSED → 조회 API 대사 경로를 타게 한다. 결제는 in_progress 로 둔다.
        logger.error(f"Toss API 호출 시간 제한 초과, 재시도로 승인 여부 확인: order_id={order_id}")
        try:
            payment = Payment.objects.get(order_id=order_id)
            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message="결제 처리 시간 초과 — 재시도로 승인 여부 확인",
                data={"error_code": "TIMEOUT", "order_id": order_id},
            )
        except Exception as log_error:
            logger.error(f"타임아웃 로그 기록 실패: {str(log_error)}")

        retry_unless_exhausted(self, e)
        # 소진: 승인 여부를 끝내 확인 못 함 — 롤백하지 않고 사람이 본다 (대사 조회 소진과 같은 정책)
        logger.critical(f"결제 승인 시간 초과 재시도 소진 — 승인 여부 미확인, 수동 대사 필요: order_id={order_id}")
        notify_payment_failure.delay(
            order_id,
            "confirm_timeout_unresolved",
            details="승인 요청 시간 제한 초과가 재시도까지 반복 — 승인 여부 미확인",
            severity="critical",
        )
        raise

    except TossPaymentError as e:
        logger.error(f"Toss API 호출 실패: order_id={order_id}, error={e.message}")

        # 0. 승인이 이미 됐을 수 있는 오류 (타임아웃 뒤 재시도): 롤백 전에 조회 API 로 대사
        if e.code in TOSS_RECONCILE_ERRORS:
            confirmed_payment = _reconcile_confirmed_payment(
                self, toss_client, payment_key, order_id, amount, e, toss_order_id=toss_order_id
            )
            if confirmed_payment is not None:
                return confirmed_payment
            # 조회 결과 승인 아님 → 아래 기존 롤백 경로로

        # 에러 로그 기록 — 상태는 여기서 바꾸지 않는다. 재시도할 오류면 in_progress 를 유지해야
        # 폴링과 웹훅이 '실패'로 오해하지 않는다. aborted 는 롤백을 결정한 분기에서만 표시한다.
        try:
            payment = Payment.objects.get(order_id=order_id)
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
            _mark_confirm_aborted(order_id)
            rollback_payment_failure.delay(order_id, f"결제 실패: {e.message}")
            raise

        # 2. 명시적 재시도 가능 오류 (NETWORK_ERROR, TIMEOUT 등)
        if e.code in TOSS_RETRYABLE_ERRORS:
            logger.warning(f"재시도 가능 오류, 재시도: {e.code}")
            retry_unless_exhausted(self, e)
            logger.error(f"최대 재시도 횟수 초과: order_id={order_id}")
            _mark_confirm_aborted(order_id)
            rollback_payment_failure.delay(order_id, f"결제 실패 (재시도 초과): {e.message}")
            raise

        # 3. HTTP 5xx 오류는 재시도
        if hasattr(e, "status_code") and e.status_code >= 500:
            logger.warning(f"서버 오류, 재시도: {e.code}")
            retry_unless_exhausted(self, e)
            logger.error(f"최대 재시도 횟수 초과 (서버 오류): order_id={order_id}")
            _mark_confirm_aborted(order_id)
            rollback_payment_failure.delay(order_id, f"결제 실패 (서버 오류 재시도 초과): {e.message}")
            raise

        # 4. 그 외 4xx 오류는 롤백 후 재시도 안 함
        logger.error(f"클라이언트 오류, 재시도 안 함: {e.code}")
        _mark_confirm_aborted(order_id)
        rollback_payment_failure.delay(order_id, f"결제 실패: {e.message}")
        raise


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.finalize_payment_confirm",
    queue="payment_critical",
    max_retries=5,
    # 수동 self.retry 에도 적용되도록 retry_countdown 이 읽는다 (0~5, 0~10, 0~20, 0~40, 0~60초)
    retry_backoff=5,
    retry_backoff_max=60,
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

            # 가상계좌: 승인 응답은 "발급"이지 "입금"이 아니다 — 입금 웹훅이 올 때까지 결제 완료 처리하지 않는다
            if toss_response.get("status") == TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT:
                PaymentService.record_virtual_account_issued(payment, toss_response)
                return {
                    "status": "waiting_for_deposit",
                    "payment_id": payment_id,
                    "order_id": payment.order_id,
                }

            # 주문도 잠그고 본다 — 취소된 주문을 결제 완료로 되살리지 않는다. 주문 취소의 결제 울타리가 승인 중
            # 취소를 막으므로 여기 걸리는 건 울타리 밖의 경합뿐이다. 토스는 청구했으니 결제는 사실대로 done 으로
            # 남기고, 환불은 결제 취소로 한다(주문이 이미 canceled 면 cancel_payment 는 돈만 돌려준다) — 알림.
            order = Order.objects.select_for_update().get(pk=payment.order_id)
            payment.mark_as_paid(toss_response)
            if order.status == "canceled":
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="error",
                    message="취소된 주문의 결제가 승인됨 — 결제 취소로 환불 필요",
                    data=toss_response,
                )
                logger.critical(f"취소된 주문의 결제 승인: payment_id={payment_id}, order_id={order.id} — 환불 필요")
                canceled_order_id = order.id
                transaction.on_commit(lambda: _alert_canceled_order_charged(payment_id, canceled_order_id))
                return {"status": "order_canceled", "payment_id": payment_id, "order_id": order.id}

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

        retry_unless_exhausted(self, e)
        # 소진: 토스는 승인(청구)했는데 우리 DB 에 반영하지 못했다. 결제는 in_progress 로 남는다 —
        # reconcile_stalled_payments 가 토스 조회로 다시 마감을 시도하고, 여기서는 사람이 알 수 있게 알린다.
        logger.critical(f"결제 최종 처리 재시도 소진 — 토스 승인분 미반영: payment_id={payment_id}")
        _alert_finalize_exhausted(payment_id, e)
        raise


def _alert_canceled_order_charged(payment_id: int, order_id: int) -> None:
    """취소된 주문에 청구된 결제를 critical 알림으로 남긴다 (환불은 사람이 결제 취소로)"""
    try:
        notify_payment_failure.delay(
            order_id,
            "canceled_order_charged",
            details=f"payment_id={payment_id} 취소된 주문의 결제가 승인됨 — 결제 취소 API로 환불 필요",
            severity="critical",
        )
    except Exception as notify_error:
        logger.error(f"취소 주문 청구 알림 발행 실패: payment_id={payment_id}, error={str(notify_error)}")


def _alert_finalize_exhausted(payment_id: int, error: Exception) -> None:
    """결제 마감 재시도 소진을 결제 로그와 critical 알림으로 남긴다 (각각 독립적으로 실패 허용)"""
    order_id = None
    try:
        payment = Payment.objects.get(pk=payment_id)
        order_id = payment.order_id
        PaymentLog.objects.create(
            payment=payment,
            log_type="error",
            message="결제 최종 처리 재시도 소진 — 토스 승인분 미반영, 대사 대기",
            data={"error": str(error)[:500]},
        )
    except Exception as log_error:
        logger.error(f"마감 소진 로그 기록 실패: payment_id={payment_id}, error={str(log_error)}")
    try:
        notify_payment_failure.delay(
            order_id,
            "finalize_exhausted",
            details=f"payment_id={payment_id} 결제 마감 재시도 소진: {str(error)[:300]}",
            severity="critical",
        )
    except Exception as notify_error:
        logger.error(f"마감 소진 알림 발행 실패: payment_id={payment_id}, error={str(notify_error)}")


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.rollback_payment_failure",
    queue="payment_critical",
    max_retries=3,
    # 수동 self.retry 에도 적용되도록 retry_countdown 이 읽는다 (0~5, 0~10, 0~20초)
    retry_backoff=5,
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
                # 가상계좌 기한 만료(expired)는 실패 사유가 다르므로 aborted 로 덮어쓰지 않는다
                if payment.status not in ["aborted", "failed", "expired"]:
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
        raise retry_with_backoff(self, e)


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
            rollback_payment_failure.delay(
                order_id=order.id, fail_reason=f"Orphan detection (stale for >{threshold_minutes}min)"
            )
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


RECONCILE_UNRESOLVED_MESSAGE = "대사 미해결"


def _flag_stalled_payment_unresolved(payment: Payment, reason: str) -> bool:
    """자동으로 닫을 수 없는 멈춘 결제를 한 번만 기록·알린다. 새로 알렸으면 True"""
    if PaymentLog.objects.filter(payment=payment, message__startswith=RECONCILE_UNRESOLVED_MESSAGE).exists():
        return False
    PaymentLog.objects.create(
        payment=payment,
        log_type="error",
        message=f"{RECONCILE_UNRESOLVED_MESSAGE} — {reason} (수동 확인 필요)",
        data={"order_id": payment.order_id, "payment_key": payment.payment_key, "reason": reason},
    )
    try:
        notify_payment_failure.delay(payment.order_id, "stalled_payment_unresolved", details=reason, severity="critical")
    except Exception as notify_error:
        logger.error(f"멈춘 결제 알림 발행 실패: payment_id={payment.id}, error={str(notify_error)}")
    return True


@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.reconcile_stalled_payments",
    queue="external_api",
    time_limit=120,
    soft_time_limit=110,
)
def reconcile_stalled_payments(self, stall_minutes: int | None = None) -> dict:
    """
    승인 처리 중(in_progress)으로 오래 멈춘 결제를 토스 조회 API 로 대사한다 (Beat: reconcile-stalled-payments)

    결제 마감(finalize) 재시도가 소진되거나 승인 체인 메시지를 잃으면 결제가 in_progress 로 남는다.
    만료 배치는 in_progress 를 건너뛰고 고아 감지는 aborted 만 보므로, 여기서 토스에 현재 상태를 묻는다.
    - 승인(DONE)·가상계좌 발급이고 주문번호·금액이 맞으면 finalize_payment_confirm 을 다시 발행한다
      (마감은 행 락 + is_paid 재검사로 멱등 — 원래 체인과 겹쳐도 한 번만 처리)
    - 그 외(미승인·토스가 모르는 결제·키 없음)는 자동으로 롤백하지 않는다. 확인 없는 롤백이 바로
      고아 결제를 만드는 행동이라, 한 번만 기록·알리고 사람이 본다.

    Args:
        stall_minutes: in_progress 로 이 시간(분) 넘게 멈춘 결제만 본다. 기본 settings.PAYMENT_RECONCILE_AFTER_MINUTES

    Returns:
        candidates / refinalized / unresolved / errors
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    if stall_minutes is None:
        stall_minutes = settings.PAYMENT_RECONCILE_AFTER_MINUTES
    cutoff = timezone.now() - timedelta(minutes=stall_minutes)
    stalled = Payment.objects.filter(status="in_progress", updated_at__lt=cutoff).select_related("order")

    result = {"status": "completed", "candidates": 0, "refinalized": 0, "unresolved": 0, "errors": []}
    toss_client = TossPaymentClient()

    for payment in stalled:
        result["candidates"] += 1
        try:
            if not payment.payment_key:
                if _flag_stalled_payment_unresolved(payment, "payment_key 없음 — 토스 조회 불가"):
                    result["unresolved"] += 1
                continue

            try:
                payment_data = toss_client.get_payment(payment.payment_key, timeout=TOSS_CONFIRM_TIMEOUT)
            except TossPaymentError as lookup_error:
                if lookup_error.status_code == 404:
                    if _flag_stalled_payment_unresolved(payment, "토스가 모르는 결제 (승인 요청이 닿지 않음)"):
                        result["unresolved"] += 1
                else:
                    # 조회 자체 실패 — 다음 주기에 다시 본다
                    result["errors"].append(f"payment_id={payment.id}: {lookup_error.code}")
                continue

            status = payment_data.get("status")
            expected_order_id = payment.toss_order_id or str(payment.order_id)
            matches = (
                str(payment_data.get("orderId")) == expected_order_id
                and payment_data.get("totalAmount") is not None
                and int(payment_data["totalAmount"]) == int(payment.amount)
            )

            if status in (TOSS_PAYMENT_STATUS_DONE, TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT) and matches:
                logger.warning(f"멈춘 결제 대사: 토스 {status} 확인 → 마감 재발행: payment_id={payment.id}")
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="approve",
                    message=f"대사: 멈춘 결제의 토스 상태 {status} 확인 → 결제 마감 재발행",
                    data={"status": status, "order_id": payment.order_id},
                )
                finalize_payment_confirm.delay(payment_data, payment.id, payment.order.user_id)
                result["refinalized"] += 1
            elif _flag_stalled_payment_unresolved(
                payment, f"토스 상태 {status}, 주문번호·금액 일치 {matches}"
            ):
                result["unresolved"] += 1
        except Exception as e:
            result["errors"].append(f"payment_id={payment.id}: {str(e)}")
            logger.error(f"멈춘 결제 대사 실패: payment_id={payment.id}, error={str(e)}")

    if result["candidates"]:
        logger.warning(f"멈춘 결제 대사 완료: {result}")
    return result


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
