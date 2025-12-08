"""
결제 복구 관련 Celery 태스크 (L3 Self-Healing)

Retry with Exponential Backoff, Dead Letter Queue, SLA Timeout 처리를 담당합니다.
"""

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = get_task_logger(__name__)


def get_recovery_config() -> dict:
    """복구 설정 가져오기"""
    return getattr(settings, "PAYMENT_RECOVERY", {})


@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.retry_failed_payment",
    queue="payment_critical",
    max_retries=0,  # 자체 재시도 로직 사용
    time_limit=60,
    soft_time_limit=55,
    acks_late=True,
)
def retry_failed_payment(self, payment_id: int, order_id: int, attempt: int) -> dict:
    """
    실패한 결제 재시도

    지수 백오프로 스케줄링된 재시도 태스크입니다.
    Celery의 기본 retry 대신 PaymentRecoveryService에서 관리합니다.

    Args:
        payment_id: 결제 ID
        order_id: 주문 ID
        attempt: 현재 시도 횟수

    Returns:
        재시도 결과
    """
    from ..models.payment import Payment, PaymentLog
    from ..models.order import Order
    from ..services.payment_recovery_service import get_payment_recovery_handler
    from ..utils.toss_payment import TossPaymentClient, TossPaymentError

    logger.info(f"결제 재시도 시작: payment_id={payment_id}, order_id={order_id}, attempt={attempt}")

    recovery = get_payment_recovery_handler()
    config = get_recovery_config()

    try:
        # 1. Circuit Breaker 확인
        if not recovery.check_circuit_breaker():
            logger.warning(f"Circuit Breaker OPEN: payment_id={payment_id}")
            return {
                "status": "circuit_breaker_open",
                "payment_id": payment_id,
                "message": "Circuit Breaker is OPEN. Retry skipped.",
            }

        # 2. Payment 조회
        try:
            payment = Payment.objects.select_related("order").get(pk=payment_id)
        except Payment.DoesNotExist:
            logger.error(f"Payment 없음: payment_id={payment_id}")
            return {
                "status": "error",
                "message": "Payment not found",
                "payment_id": payment_id,
            }

        # 3. SLA 타임아웃 확인
        if recovery.check_sla_timeout(payment.created_at):
            result = recovery.abort_for_sla(payment_id, order_id, payment.created_at)
            return result

        # 4. 이미 완료된 결제 확인
        if payment.is_paid:
            logger.info(f"이미 완료된 결제: payment_id={payment_id}")
            return {
                "status": "already_paid",
                "payment_id": payment_id,
            }

        # 5. 결제 키가 없으면 재시도 불가
        if not payment.payment_key:
            logger.error(f"Payment key 없음: payment_id={payment_id}")
            recovery.move_to_dlq(
                payment_id=payment_id,
                order_id=order_id,
                failure_type="non_retryable_error",
                error_code="NO_PAYMENT_KEY",
                error_message="Payment key is missing",
                retry_count=attempt,
            )
            return {
                "status": "error",
                "message": "Payment key is missing",
                "payment_id": payment_id,
            }

        # 6. 토스 API 재호출
        toss_client = TossPaymentClient()

        try:
            payment_data = toss_client.confirm_payment(
                payment_key=payment.payment_key,
                order_id=str(order_id),
                amount=int(payment.amount),
            )

            # 성공 - Circuit Breaker 기록
            recovery.record_circuit_breaker_result(success=True)

            # Payment 상태 업데이트
            with transaction.atomic():
                payment.mark_as_paid(payment_data)

                PaymentLog.objects.create(
                    payment=payment,
                    log_type="approve",
                    message=f"결제 재시도 성공 (attempt={attempt})",
                    data=payment_data,
                )

            logger.info(f"결제 재시도 성공: payment_id={payment_id}, attempt={attempt}")

            # finalize 태스크 트리거
            from .payment_tasks import finalize_payment_confirm

            finalize_payment_confirm.delay(payment_data, payment_id, payment.order.user_id)

            return {
                "status": "success",
                "payment_id": payment_id,
                "attempt": attempt,
            }

        except TossPaymentError as e:
            # 실패 - Circuit Breaker 기록
            recovery.record_circuit_breaker_result(success=False)

            logger.error(f"결제 재시도 실패: payment_id={payment_id}, attempt={attempt}, error={e.message}")

            # 실패 처리 (재시도 또는 DLQ)
            result = recovery.handle_failure(
                payment_id=payment_id,
                order_id=order_id,
                error_code=e.code,
                error_message=e.message,
                retry_count=attempt,
                response_data={"error_code": e.code, "error_message": e.message},
            )

            PaymentLog.objects.create(
                payment=payment,
                log_type="error",
                message=f"결제 재시도 실패 (attempt={attempt}): {e.message}",
                data={"error_code": e.code, "action": result.get("action")},
            )

            return result

    except Exception as e:
        logger.exception(f"결제 재시도 중 예외: payment_id={payment_id}, error={str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "payment_id": payment_id,
        }


@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.check_sla_violations",
    queue="default",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_sla_violations(self, threshold_minutes: int | None = None) -> dict:
    """
    SLA 위반 결제 감지 및 처리

    정기적으로 실행되어 SLA 타임아웃을 초과한 결제를 감지하고
    abort 처리합니다.

    Args:
        threshold_minutes: 커스텀 임계값 (미지정 시 설정값 사용)

    Returns:
        감지 및 처리 결과
    """
    from datetime import timedelta
    from ..models.payment import Payment
    from ..services.payment_recovery_service import get_payment_recovery_handler

    config = get_recovery_config()
    recovery = get_payment_recovery_handler()

    if threshold_minutes is None:
        sla_seconds = config.get("SLA_TIMEOUT_SECONDS", 300)
        threshold_minutes = sla_seconds // 60

    threshold_time = timezone.now() - timedelta(minutes=threshold_minutes)

    # in_progress 상태로 오래 남아있는 결제 찾기
    stale_payments = Payment.objects.filter(
        status="in_progress",
        created_at__lt=threshold_time,
    ).select_related("order")

    detected_count = 0
    aborted_count = 0
    errors = []

    for payment in stale_payments:
        detected_count += 1
        try:
            logger.warning(
                f"SLA 위반 결제 감지: payment_id={payment.id}, "
                f"order_id={payment.order_id}, created_at={payment.created_at}"
            )

            recovery.abort_for_sla(
                payment_id=payment.id,
                order_id=payment.order_id,
                created_at=payment.created_at,
            )
            aborted_count += 1

        except Exception as e:
            error_msg = f"payment_id={payment.id}: {str(e)}"
            errors.append(error_msg)
            logger.error(f"SLA abort 처리 실패: {error_msg}")

    if detected_count > 0:
        logger.warning(f"SLA 위반 감지 완료: detected={detected_count}, aborted={aborted_count}, errors={len(errors)}")
    else:
        logger.info("SLA 위반 결제 없음")

    return {
        "status": "completed",
        "detected": detected_count,
        "aborted": aborted_count,
        "errors": errors,
    }


@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.cleanup_expired_dlq",
    queue="default",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def cleanup_expired_dlq(self) -> dict:
    """
    만료된 DLQ 레코드 정리

    DLQ_RETENTION_DAYS를 초과한 레코드를 정리합니다.
    resolved/rejected 상태인 레코드만 삭제합니다.

    Returns:
        정리 결과
    """
    from ..models.failed_payment import FailedPayment

    expired_records = FailedPayment.objects.filter(
        expires_at__lt=timezone.now(),
        status__in=["resolved", "rejected", "expired"],
    )

    count = expired_records.count()

    if count > 0:
        expired_records.delete()
        logger.info(f"만료된 DLQ 레코드 삭제: count={count}")
    else:
        logger.debug("만료된 DLQ 레코드 없음")

    return {
        "status": "completed",
        "deleted_count": count,
    }


@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.process_dlq_batch",
    queue="default",
    max_retries=1,
    time_limit=300,
    soft_time_limit=290,
)
def process_dlq_batch(self, batch_size: int = 10, failure_types: list | None = None) -> dict:
    """
    DLQ 배치 재처리

    pending 상태의 DLQ 레코드를 일괄 재처리합니다.
    운영자가 수동으로 트리거하거나 스케줄러에서 호출합니다.

    Args:
        batch_size: 한 번에 처리할 레코드 수
        failure_types: 처리할 실패 유형 필터 (None이면 전체)

    Returns:
        처리 결과
    """
    from ..models.failed_payment import FailedPayment
    from ..services.payment_recovery_service import get_payment_recovery_handler

    recovery = get_payment_recovery_handler()

    # Circuit Breaker 확인
    if not recovery.check_circuit_breaker():
        logger.warning("Circuit Breaker OPEN: DLQ 배치 처리 건너뜀")
        return {
            "status": "skipped",
            "reason": "circuit_breaker_open",
        }

    queryset = FailedPayment.objects.filter(status="pending")

    if failure_types:
        queryset = queryset.filter(failure_type__in=failure_types)

    records = list(queryset.order_by("created_at")[:batch_size])

    processed = 0
    retried = 0
    rejected = 0
    errors = []

    for record in records:
        try:
            # 상태를 reviewing으로 변경
            record.status = "reviewing"
            record.save(update_fields=["status", "updated_at"])

            # max_retries_exceeded만 재시도 가능
            if record.failure_type == "max_retries_exceeded":
                # 재시도 스케줄링
                if record.payment_id and record.order_id:
                    recovery.schedule_retry(
                        payment_id=record.payment_id,
                        order_id=record.order_id,
                        attempt=record.retry_count + 1,
                    )
                    retried += 1
                else:
                    record.status = "rejected"
                    record.resolution_note = "Missing payment_id or order_id"
                    rejected += 1
            else:
                # 다른 유형은 수동 검토 필요
                record.status = "pending"  # 다시 pending으로
                rejected += 1

            record.save()
            processed += 1

        except Exception as e:
            error_msg = f"dlq_id={record.id}: {str(e)}"
            errors.append(error_msg)
            logger.error(f"DLQ 처리 실패: {error_msg}")

            record.status = "pending"
            record.save(update_fields=["status"])

    logger.info(f"DLQ 배치 처리 완료: processed={processed}, retried={retried}, " f"rejected={rejected}, errors={len(errors)}")

    return {
        "status": "completed",
        "processed": processed,
        "retried": retried,
        "rejected": rejected,
        "errors": errors,
    }


@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.reset_circuit_breaker",
    queue="default",
    max_retries=1,
)
def reset_circuit_breaker(
    self,
    service_name: str = "toss_payment",
    action: str = "auto",  # 'open', 'close', 'auto'
    reason: str = "",
    controlled_by_id: int | None = None,
) -> dict:
    """
    Circuit Breaker 상태 수동 제어

    운영자가 PG 장애 감지 시 수동으로 Open하거나,
    복구 확인 후 Close할 수 있습니다.

    Args:
        service_name: 서비스명
        action: 'open', 'close', 'auto'
        reason: 제어 사유
        controlled_by_id: 제어자 ID

    Returns:
        처리 결과
    """
    from ..models.failed_payment import CircuitBreakerState
    from ..models.user import User

    state, created = CircuitBreakerState.objects.get_or_create(
        service_name=service_name,
        defaults={"state": "closed"},
    )

    controlled_by = None
    if controlled_by_id:
        try:
            controlled_by = User.objects.get(pk=controlled_by_id)
        except User.DoesNotExist:
            pass

    previous_state = state.state

    if action == "open":
        state.force_open(controlled_by=controlled_by, reason=reason)
        logger.warning(f"Circuit Breaker 수동 OPEN: service={service_name}, reason={reason}")
    elif action == "close":
        state.force_close(controlled_by=controlled_by, reason=reason)
        logger.info(f"Circuit Breaker 수동 CLOSE: service={service_name}, reason={reason}")
    else:
        # auto: 현재 상태 유지, 수동 제어 해제
        state.manually_controlled = False
        state.save(update_fields=["manually_controlled", "updated_at"])
        logger.info(f"Circuit Breaker 자동 모드로 전환: service={service_name}")

    return {
        "status": "completed",
        "service_name": service_name,
        "previous_state": previous_state,
        "current_state": state.state,
        "action": action,
    }
