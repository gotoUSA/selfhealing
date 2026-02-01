"""
Payment Recovery Service (L3 Self-Healing)

결제 장애 발생 시 자동 복구를 위한 추상화 레이어입니다.
현재 Celery + Django ORM 기반으로 구현되어 있으며,
추후 Kafka/RabbitMQ 도입 시 쉽게 마이그레이션 가능합니다.

주요 기능:
- Retry with exponential backoff
- Dead Letter Queue (DLQ)
- SLA timeout + abort 정책
- Circuit Breaker (Toggle 기반)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class PaymentRecoveryError(Exception):
    """결제 복구 관련 에러"""

    def __init__(self, message: str, code: str = "RECOVERY_ERROR", recoverable: bool = True):
        self.message = message
        self.code = code
        self.recoverable = recoverable
        super().__init__(message)


class CircuitBreakerOpenError(PaymentRecoveryError):
    """Circuit Breaker가 Open 상태일 때 발생"""

    def __init__(self, service_name: str):
        super().__init__(
            message=f"Circuit Breaker is OPEN for {service_name}. Request rejected.",
            code="CIRCUIT_BREAKER_OPEN",
            recoverable=False,
        )
        self.service_name = service_name


class SLATimeoutError(PaymentRecoveryError):
    """SLA 타임아웃 초과 시 발생"""

    def __init__(self, elapsed_seconds: float, sla_seconds: int):
        super().__init__(
            message=f"SLA timeout exceeded: {elapsed_seconds:.1f}s > {sla_seconds}s",
            code="SLA_TIMEOUT",
            recoverable=False,
        )
        self.elapsed_seconds = elapsed_seconds
        self.sla_seconds = sla_seconds


class PaymentRecoveryHandler(ABC):
    """
    결제 복구 핸들러 추상 클래스

    추후 Kafka/RabbitMQ 기반으로 교체 시 이 인터페이스를 구현하면 됩니다.
    """

    @abstractmethod
    def handle_failure(
        self,
        payment_id: int | None,
        order_id: int | None,
        error_code: str,
        error_message: str,
        retry_count: int,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """결제 실패 처리"""
        pass

    @abstractmethod
    def schedule_retry(
        self,
        payment_id: int,
        order_id: int,
        attempt: int,
        delay_seconds: int | None = None,
    ) -> str:
        """재시도 스케줄링 (태스크 ID 반환)"""
        pass

    @abstractmethod
    def move_to_dlq(
        self,
        payment_id: int | None,
        order_id: int | None,
        failure_type: str,
        error_code: str,
        error_message: str,
        retry_count: int,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Dead Letter Queue로 이동 (DLQ 레코드 ID 반환)"""
        pass

    @abstractmethod
    def check_circuit_breaker(self, service_name: str = "toss_payment") -> bool:
        """Circuit Breaker 상태 확인 (True: 요청 허용, False: 차단)"""
        pass

    @abstractmethod
    def check_sla_timeout(self, created_at) -> bool:
        """SLA 타임아웃 확인 (True: 타임아웃 초과)"""
        pass


class CeleryPaymentRecovery(PaymentRecoveryHandler):
    """
    Celery 기반 결제 복구 구현체

    현재 시스템에서 사용되는 구현체입니다.
    Celery의 재시도 메커니즘과 Django ORM을 활용합니다.
    """

    def __init__(self):
        self.config = getattr(settings, "PAYMENT_RECOVERY", {})

    # =========================================================================
    # Governance Integration
    # =========================================================================

    def is_circuit_breaker_blocking(self, service_name: str = "toss_payment") -> tuple[bool, str]:
        """
        Circuit Breaker가 차단 중인지 확인합니다.

        Returns:
            (is_blocked, reason) 튜플
        """
        if not self.check_circuit_breaker(service_name):
            return True, f"Circuit Breaker is OPEN for {service_name}"
        return False, ""

    def check_governance_for_retry(
        self,
        payment_id: int,
        operation_name: str = "payment_retry",
    ) -> dict | None:
        """
        결제 재시도 전 거버넌스 체크를 수행합니다.

        Returns:
            차단 시 결과 dict, 허용 시 None
        """
        is_blocked, reason = self.is_circuit_breaker_blocking()
        if is_blocked:
            logger.warning(f"[PaymentRecovery] {operation_name} blocked: {reason}")
            return {
                "status": "circuit_breaker_open",
                "payment_id": payment_id,
                "message": reason,
            }
        return None

    def get_backoff_delay(self, attempt: int) -> int:
        """지수 백오프 지연 시간 계산"""
        base = self.config.get("RETRY_BACKOFF_BASE", 4)
        max_delay = self.config.get("RETRY_BACKOFF_MAX", 180)

        # 지수 백오프: base^attempt (4, 16, 64, ...)
        delay = base**attempt

        # 최대값 제한
        delay = min(delay, max_delay)

        # Jitter 추가 (±25%)
        if self.config.get("RETRY_JITTER", True):
            import random

            jitter = delay * 0.25 * (random.random() * 2 - 1)
            delay = int(delay + jitter)

        return max(1, delay)  # 최소 1초

    def handle_failure(
        self,
        payment_id: int | None,
        order_id: int | None,
        error_code: str,
        error_message: str,
        retry_count: int,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        결제 실패 처리

        1. 재시도 가능 여부 확인
        2. 최대 재시도 횟수 확인
        3. 재시도 또는 DLQ 이동 결정
        """
        from ..constants import TOSS_NON_RETRYABLE_ERRORS

        max_attempts = self.config.get("RETRY_MAX_ATTEMPTS", 3)

        # 1. 재시도 불가능한 오류인 경우 즉시 DLQ
        if error_code in TOSS_NON_RETRYABLE_ERRORS:
            dlq_id = self.move_to_dlq(
                payment_id=payment_id,
                order_id=order_id,
                failure_type="non_retryable_error",
                error_code=error_code,
                error_message=error_message,
                retry_count=retry_count,
                request_data=request_data,
                response_data=response_data,
            )
            return {
                "action": "moved_to_dlq",
                "dlq_id": dlq_id,
                "reason": "non_retryable_error",
                "error_code": error_code,
            }

        # 2. 최대 재시도 횟수 초과
        if retry_count >= max_attempts:
            dlq_id = self.move_to_dlq(
                payment_id=payment_id,
                order_id=order_id,
                failure_type="max_retries_exceeded",
                error_code=error_code,
                error_message=error_message,
                retry_count=retry_count,
                request_data=request_data,
                response_data=response_data,
            )
            return {
                "action": "moved_to_dlq",
                "dlq_id": dlq_id,
                "reason": "max_retries_exceeded",
                "retry_count": retry_count,
            }

        # 3. 재시도 스케줄링
        if payment_id and order_id:
            task_id = self.schedule_retry(
                payment_id=payment_id,
                order_id=order_id,
                attempt=retry_count + 1,
            )
            return {
                "action": "retry_scheduled",
                "task_id": task_id,
                "attempt": retry_count + 1,
                "delay": self.get_backoff_delay(retry_count + 1),
            }

        # 4. payment_id나 order_id가 없으면 DLQ
        dlq_id = self.move_to_dlq(
            payment_id=payment_id,
            order_id=order_id,
            failure_type="unknown",
            error_code=error_code,
            error_message=error_message,
            retry_count=retry_count,
            request_data=request_data,
            response_data=response_data,
        )
        return {
            "action": "moved_to_dlq",
            "dlq_id": dlq_id,
            "reason": "missing_identifiers",
        }

    def schedule_retry(
        self,
        payment_id: int,
        order_id: int,
        attempt: int,
        delay_seconds: int | None = None,
    ) -> str:
        """Celery 태스크로 재시도 스케줄링"""
        from ..tasks.payment_recovery_tasks import retry_failed_payment

        if delay_seconds is None:
            delay_seconds = self.get_backoff_delay(attempt)

        result = retry_failed_payment.apply_async(
            args=[payment_id, order_id, attempt],
            countdown=delay_seconds,
        )

        logger.info(
            f"결제 재시도 스케줄링: payment_id={payment_id}, order_id={order_id}, "
            f"attempt={attempt}, delay={delay_seconds}s, task_id={result.id}"
        )

        return result.id

    def move_to_dlq(
        self,
        payment_id: int | None,
        order_id: int | None,
        failure_type: str,
        error_code: str,
        error_message: str,
        retry_count: int,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Dead Letter Queue에 실패 기록 저장"""
        from ..models.failed_external_request import FailedExternalRequest
        from ..models.payment import Payment
        from ..models.order import Order

        payment = None
        order = None
        user = None

        if payment_id:
            try:
                payment = Payment.objects.select_related("order__user").get(pk=payment_id)
                order = payment.order
                user = order.user if order else None
            except Payment.DoesNotExist:
                pass

        if not order and order_id:
            try:
                order = Order.objects.select_related("user").get(pk=order_id)
                user = order.user
            except Order.DoesNotExist:
                pass

        failed_request = FailedExternalRequest.create_from_failure(
            domain="payment",
            payment=payment,
            order=order,
            user=user,
            failure_type=failure_type,
            error_code=error_code,
            error_message=error_message,
            retry_count=retry_count,
            request_data=request_data,
            response_data=response_data,
            metadata=metadata,
        )

        logger.warning(
            f"외부 요청 DLQ 이동: dlq_id={failed_request.id}, payment_id={payment_id}, "
            f"order_id={order_id}, failure_type={failure_type}, error_code={error_code}"
        )

        # 알림 전송 (설정된 경우)
        if self.config.get("NOTIFY_ON_DLQ", True):
            self._notify_dlq_entry(failed_request)

        return failed_request.id

    def check_circuit_breaker(self, service_name: str = "toss_payment") -> bool:
        """
        Circuit Breaker 상태 확인 (Redis 기반)

        Returns:
            True: 요청 허용
            False: 요청 차단
        """
        # Circuit Breaker가 비활성화되어 있으면 항상 허용
        if not self.config.get("CIRCUIT_BREAKER_ENABLED", False):
            return True

        from selfhealing.factory import ProviderRegistry

        try:
            repo = ProviderRegistry.get_circuit_breaker_repo()
            state = repo.get_by_service_name(service_name)
            if state is None:
                return True  # 상태 없으면 허용
            return state.state == "closed" or state.state == "half_open"
        except Exception:
            return True  # 조회 실패 시 허용 (fail-open)

    def record_circuit_breaker_result(
        self,
        service_name: str = "toss_payment",
        success: bool = True,
    ) -> None:
        """Circuit Breaker에 결과 기록 (Redis 기반)"""
        if not self.config.get("CIRCUIT_BREAKER_ENABLED", False):
            return

        from selfhealing.factory import ProviderRegistry

        try:
            repo = ProviderRegistry.get_circuit_breaker_repo()
            if success:
                previous_state = repo.get_by_service_name(service_name)
                repo.record_success(service_name)
            else:
                previous_state = repo.get_by_service_name(service_name)
                current = repo.record_failure(service_name)

                # Open 상태로 전환된 경우 알림
                prev_state_str = previous_state.state if previous_state else "closed"
                if prev_state_str != "open" and current.state == "open":
                    if self.config.get("NOTIFY_ON_CIRCUIT_OPEN", True):
                        self._notify_circuit_open(service_name)
        except Exception:
            pass  # 기록 실패해도 비즈니스 로직 진행

    def check_sla_timeout(self, created_at) -> bool:
        """
        SLA 타임아웃 확인

        Returns:
            True: 타임아웃 초과 (abort 필요)
            False: 정상
        """
        if not self.config.get("SLA_ABORT_ENABLED", True):
            return False

        sla_timeout = self.config.get("SLA_TIMEOUT_SECONDS", 300)
        elapsed = (timezone.now() - created_at).total_seconds()

        return elapsed > sla_timeout

    def abort_for_sla(
        self,
        payment_id: int,
        order_id: int,
        created_at,
    ) -> dict[str, Any]:
        """SLA 타임아웃으로 인한 abort 처리"""
        sla_timeout = self.config.get("SLA_TIMEOUT_SECONDS", 300)
        elapsed = (timezone.now() - created_at).total_seconds()

        logger.error(
            f"SLA 타임아웃 abort: payment_id={payment_id}, order_id={order_id}, " f"elapsed={elapsed:.1f}s, sla={sla_timeout}s"
        )

        # DLQ로 이동
        dlq_id = self.move_to_dlq(
            payment_id=payment_id,
            order_id=order_id,
            failure_type="sla_timeout",
            error_code="SLA_TIMEOUT",
            error_message=f"SLA timeout exceeded: {elapsed:.1f}s > {sla_timeout}s",
            retry_count=0,
            metadata={
                "elapsed_seconds": elapsed,
                "sla_timeout_seconds": sla_timeout,
            },
        )

        # 롤백 태스크 트리거
        from ..tasks.payment_tasks import rollback_payment_failure

        rollback_payment_failure.delay(order_id, f"SLA 타임아웃 ({elapsed:.1f}s)")

        return {
            "action": "sla_abort",
            "dlq_id": dlq_id,
            "elapsed_seconds": elapsed,
            "sla_timeout_seconds": sla_timeout,
        }

    def _notify_dlq_entry(self, failed_payment) -> None:
        """DLQ 진입 알림"""
        from ..tasks.payment_tasks import notify_payment_failure

        notify_payment_failure.delay(
            order_id=failed_payment.order_id if failed_payment.order else None,
            failure_type="dlq_entry",
            details=f"Type: {failed_payment.failure_type}, Error: {failed_payment.error_code}",
            severity="warning",
        )

    def _notify_circuit_open(self, service_name: str) -> None:
        """Circuit Breaker Open 알림"""
        from ..tasks.payment_tasks import notify_payment_failure

        notify_payment_failure.delay(
            order_id=None,
            failure_type="circuit_breaker_open",
            details=f"Service: {service_name}",
            severity="critical",
        )


# 싱글톤 인스턴스
_recovery_handler: PaymentRecoveryHandler | None = None


def get_payment_recovery_handler() -> PaymentRecoveryHandler:
    """Payment Recovery Handler 싱글톤 반환"""
    global _recovery_handler
    if _recovery_handler is None:
        _recovery_handler = CeleryPaymentRecovery()
    return _recovery_handler
