"""
Failed Payment 모델 (Dead Letter Queue)

결제 재시도 최대 횟수 초과 후 복구 불가능한 결제를 추적합니다.
수동 복구 또는 배치 재처리를 위한 데이터를 저장합니다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import models
from django.utils import timezone


class FailedPayment(models.Model):
    """
    Dead Letter Queue: 복구 불가능한 결제 추적

    최대 재시도 횟수를 초과하거나 복구 불가능한 오류가 발생한 결제를
    별도로 저장하여 수동 검토 및 복구를 지원합니다.
    """

    # 실패 유형
    FAILURE_TYPE_CHOICES = [
        ("max_retries_exceeded", "최대 재시도 횟수 초과"),
        ("non_retryable_error", "재시도 불가능한 오류"),
        ("sla_timeout", "SLA 타임아웃 초과"),
        ("circuit_breaker_open", "Circuit Breaker 차단"),
        ("manual_abort", "수동 중단"),
        ("unknown", "알 수 없는 오류"),
    ]

    # 처리 상태
    STATUS_CHOICES = [
        ("pending", "검토 대기"),
        ("reviewing", "검토 중"),
        ("resolved", "해결됨"),
        ("rejected", "복구 불가"),
        ("expired", "보관 기간 만료"),
    ]

    # 원본 결제 참조 (nullable - 결제 생성 전 실패할 수도 있음)
    payment = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_records",
        verbose_name="원본 결제",
    )

    # 주문 참조
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_payment_records",
        verbose_name="주문",
    )

    # 사용자 참조
    user = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_payments",
        verbose_name="사용자",
    )

    # 결제 정보 스냅샷 (원본 데이터가 삭제되더라도 추적 가능)
    payment_key = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="토스 결제키",
    )

    toss_order_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="토스 주문번호",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=Decimal("0"),
        verbose_name="결제 금액",
    )

    # 실패 정보
    failure_type = models.CharField(
        max_length=30,
        choices=FAILURE_TYPE_CHOICES,
        default="unknown",
        verbose_name="실패 유형",
    )

    error_code = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="에러 코드",
    )

    error_message = models.TextField(
        blank=True,
        verbose_name="에러 메시지",
    )

    # 재시도 정보
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name="재시도 횟수",
    )

    last_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="마지막 재시도 시각",
    )

    # 처리 상태
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="처리 상태",
    )

    # 해결 정보
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="해결 시각",
    )

    resolved_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_failed_payments",
        verbose_name="해결자",
    )

    resolution_note = models.TextField(
        blank=True,
        verbose_name="해결 메모",
    )

    # 원본 요청/응답 데이터 (디버깅용)
    request_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="요청 데이터",
    )

    response_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="응답 데이터",
    )

    # 메타데이터 (추가 컨텍스트)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="메타데이터",
    )

    # 시간 정보
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="생성일시",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일시",
    )

    # 보관 만료일 (DLQ_RETENTION_DAYS 기준)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="보관 만료일",
    )

    class Meta:
        db_table = "shopping_failed_payment"
        verbose_name = "실패 결제 (DLQ)"
        verbose_name_plural = "실패 결제 목록 (DLQ)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["failure_type", "-created_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_failure_type_display()}] {self.toss_order_id or 'N/A'} - {self.get_status_display()}"

    def mark_as_resolved(self, resolved_by, note: str = "") -> None:
        """해결됨으로 표시"""
        self.status = "resolved"
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by
        self.resolution_note = note
        self.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note", "updated_at"])

    def mark_as_rejected(self, resolved_by, note: str = "") -> None:
        """복구 불가로 표시"""
        self.status = "rejected"
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by
        self.resolution_note = note
        self.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note", "updated_at"])

    @classmethod
    def create_from_payment_failure(
        cls,
        payment=None,
        order=None,
        user=None,
        failure_type: str = "unknown",
        error_code: str = "",
        error_message: str = "",
        retry_count: int = 0,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "FailedPayment":
        """결제 실패로부터 Dead Letter 레코드 생성"""
        from django.conf import settings
        from datetime import timedelta

        retention_days = settings.PAYMENT_RECOVERY.get("DLQ_RETENTION_DAYS", 30)
        expires_at = timezone.now() + timedelta(days=retention_days)

        return cls.objects.create(
            payment=payment,
            order=order,
            user=user,
            payment_key=payment.payment_key if payment and payment.payment_key else "",
            toss_order_id=payment.toss_order_id if payment else (str(order.id) if order else ""),
            amount=payment.amount if payment else (order.final_amount if order else Decimal("0")),
            failure_type=failure_type,
            error_code=error_code,
            error_message=error_message,
            retry_count=retry_count,
            last_retry_at=timezone.now() if retry_count > 0 else None,
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
            expires_at=expires_at,
        )


class CircuitBreakerState(models.Model):
    """
    Circuit Breaker 상태 저장

    외부 PG 장애 감지 시 운영자가 수동으로 활성화하거나,
    자동으로 상태를 추적합니다.
    """

    STATE_CHOICES = [
        ("closed", "정상 (Closed)"),
        ("open", "차단 (Open)"),
        ("half_open", "테스트 중 (Half-Open)"),
    ]

    # 서비스 식별자 (예: 'toss_payment', 'kakao_payment')
    service_name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="서비스명",
    )

    # 현재 상태
    state = models.CharField(
        max_length=20,
        choices=STATE_CHOICES,
        default="closed",
        verbose_name="상태",
    )

    # 연속 실패 카운터
    failure_count = models.PositiveIntegerField(
        default=0,
        verbose_name="연속 실패 횟수",
    )

    # Half-Open 상태에서의 성공 카운터
    success_count = models.PositiveIntegerField(
        default=0,
        verbose_name="성공 횟수 (Half-Open)",
    )

    # 마지막 실패 시각
    last_failure_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="마지막 실패 시각",
    )

    # Open 상태로 전환된 시각
    opened_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Open 전환 시각",
    )

    # 수동 제어 여부
    manually_controlled = models.BooleanField(
        default=False,
        verbose_name="수동 제어 여부",
        help_text="운영자가 수동으로 상태를 변경한 경우 True",
    )

    # 수동 제어자
    controlled_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="circuit_breaker_controls",
        verbose_name="제어자",
    )

    # 제어 사유
    control_reason = models.TextField(
        blank=True,
        verbose_name="제어 사유",
    )

    # 시간 정보
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="생성일시",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일시",
    )

    class Meta:
        db_table = "shopping_circuit_breaker_state"
        verbose_name = "Circuit Breaker 상태"
        verbose_name_plural = "Circuit Breaker 상태 목록"

    def __str__(self) -> str:
        return f"{self.service_name}: {self.get_state_display()}"

    def record_failure(self) -> None:
        """실패 기록 및 상태 전환 체크"""
        from django.conf import settings

        self.failure_count += 1
        self.last_failure_at = timezone.now()
        self.success_count = 0  # 성공 카운터 리셋

        # Circuit Breaker가 활성화되어 있고, 임계값 초과 시 Open
        if settings.PAYMENT_RECOVERY.get("CIRCUIT_BREAKER_ENABLED", False):
            threshold = settings.PAYMENT_RECOVERY.get("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5)
            if self.failure_count >= threshold and self.state == "closed":
                self.state = "open"
                self.opened_at = timezone.now()

        self.save()

    def record_success(self) -> None:
        """성공 기록 및 상태 전환 체크"""
        from django.conf import settings

        if self.state == "half_open":
            self.success_count += 1

            success_threshold = settings.PAYMENT_RECOVERY.get("CIRCUIT_BREAKER_SUCCESS_THRESHOLD", 2)
            if self.success_count >= success_threshold:
                # Close로 전환
                self.state = "closed"
                self.failure_count = 0
                self.success_count = 0
                self.opened_at = None

        elif self.state == "closed":
            # 정상 상태에서 성공 시 실패 카운터 리셋
            self.failure_count = 0

        self.save()

    def should_allow_request(self) -> bool:
        """요청 허용 여부 확인"""
        from django.conf import settings

        # Circuit Breaker가 비활성화되어 있으면 항상 허용
        if not settings.PAYMENT_RECOVERY.get("CIRCUIT_BREAKER_ENABLED", False):
            return True

        if self.state == "closed":
            return True

        if self.state == "open":
            # Recovery timeout 확인
            recovery_timeout = settings.PAYMENT_RECOVERY.get("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", 60)
            if self.opened_at:
                elapsed = (timezone.now() - self.opened_at).total_seconds()
                if elapsed >= recovery_timeout:
                    # Half-Open으로 전환
                    self.state = "half_open"
                    self.success_count = 0
                    self.save()
                    return True
            return False

        # half_open 상태: 제한된 요청 허용
        return True

    def force_open(self, controlled_by=None, reason: str = "") -> None:
        """수동으로 Open 상태로 전환 (PG 장애 감지 시)"""
        self.state = "open"
        self.opened_at = timezone.now()
        self.manually_controlled = True
        self.controlled_by = controlled_by
        self.control_reason = reason
        self.save()

    def force_close(self, controlled_by=None, reason: str = "") -> None:
        """수동으로 Close 상태로 전환 (PG 복구 확인 시)"""
        self.state = "closed"
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = None
        self.manually_controlled = True
        self.controlled_by = controlled_by
        self.control_reason = reason
        self.save()
