"""
Failed External Request 모델 (Dead Letter Queue)

외부 API 호출 재시도 최대 횟수 초과 후 복구 불가능한 요청을 추적합니다.
수동 복구 또는 배치 재처리를 위한 데이터를 저장합니다.

NOTE: 도메인 중립적 설계 - 특정 비즈니스 도메인(결제, 주문 등)에 의존하지 않음
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import models
from django.utils import timezone


class FailedExternalRequest(models.Model):
    """
    Dead Letter Queue: 복구 불가능한 외부 요청 추적 (도메인 중립)

    최대 재시도 횟수를 초과하거나 복구 불가능한 오류가 발생한 외부 API 요청을
    별도로 저장하여 수동 검토 및 복구를 지원합니다.

    설계 원칙:
    - FK 대신 entity_type/entity_id로 느슨한 결합
    - 어떤 비즈니스 도메인에서도 사용 가능
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

    # 도메인 타입 (어떤 외부 서비스인지) - 확장 가능한 설계
    DOMAIN_CHOICES = [
        ("external_api", "외부 API"),
        ("payment", "결제"),
        ("point", "포인트"),
        ("inventory", "재고"),
        ("webhook", "웹훅"),
        ("notification", "알림"),
    ]

    # 도메인 (어떤 종류의 외부 요청인지)
    domain = models.CharField(
        max_length=50,
        choices=DOMAIN_CHOICES,
        default="external_api",
        verbose_name="도메인",
    )

    # ========================================
    # Generic Entity Reference (도메인 중립)
    # ========================================
    entity_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="엔티티 타입",
        help_text="관련 엔티티 타입 (예: 'order', 'payment', 'subscription')",
    )

    entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="엔티티 ID",
        help_text="관련 엔티티의 ID",
    )

    # 추가 엔티티 참조를 위한 JSON 필드
    entity_refs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="엔티티 참조",
        help_text="추가 엔티티 참조 (예: {'user_id': 123, 'tenant_id': 'abc'})",
    )

    # 사용자 ID (도메인 중립 - FK 대신 정수 ID 사용)
    user_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="사용자 ID",
    )

    # 외부 요청 식별자 (API 키, 웹훅ID 등)
    external_request_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="외부 요청 ID",
    )

    # 외부 트랜잭션 ID
    external_transaction_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="외부 트랜잭션 ID",
    )

    # 금액 (해당하는 경우)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=Decimal("0"),
        verbose_name="금액",
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

    # 해결자 ID (도메인 중립)
    resolved_by_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="해결자 ID",
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
        db_table = "shopping_failed_external_request"
        verbose_name = "실패한 외부 요청 (DLQ)"
        verbose_name_plural = "실패한 외부 요청 목록 (DLQ)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["failure_type", "-created_at"]),
            models.Index(fields=["domain", "-created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        entity_info = f"{self.entity_type}:{self.entity_id}" if self.entity_type else "N/A"
        return f"[{self.domain}] {self.get_failure_type_display()} {entity_info} - {self.get_status_display()}"

    def mark_as_resolved(self, resolved_by_id: int | None = None, note: str = "") -> None:
        """해결됨으로 표시"""
        self.status = "resolved"
        self.resolved_at = timezone.now()
        self.resolved_by_id = resolved_by_id
        self.resolution_note = note
        self.save(update_fields=["status", "resolved_at", "resolved_by_id", "resolution_note", "updated_at"])

    def mark_as_rejected(self, resolved_by_id: int | None = None, note: str = "") -> None:
        """복구 불가로 표시"""
        self.status = "rejected"
        self.resolved_at = timezone.now()
        self.resolved_by_id = resolved_by_id
        self.resolution_note = note
        self.save(update_fields=["status", "resolved_at", "resolved_by_id", "resolution_note", "updated_at"])

    @classmethod
    def create_from_failure(
        cls,
        domain: str = "external_api",
        entity_type: str = "",
        entity_id: str = "",
        entity_refs: dict[str, Any] | None = None,
        user_id: int | None = None,
        failure_type: str = "unknown",
        error_code: str = "",
        error_message: str = "",
        retry_count: int = 0,
        external_request_id: str = "",
        external_transaction_id: str = "",
        amount: Decimal | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        snapshot_data: dict[str, Any] | None = None,
    ) -> "FailedExternalRequest":
        """
        외부 요청 실패로부터 Dead Letter 레코드 생성 (도메인 중립)

        Args:
            domain: 비즈니스 도메인 (external_api, payment, point 등)
            entity_type: 관련 엔티티 타입 (예: 'order', 'subscription')
            entity_id: 관련 엔티티 ID
            entity_refs: 추가 엔티티 참조 딕셔너리
            user_id: 사용자 ID
            failure_type: 실패 유형
            error_code: 에러 코드
            error_message: 에러 메시지
            retry_count: 재시도 횟수
            external_request_id: 외부 요청 ID
            external_transaction_id: 외부 트랜잭션 ID
            amount: 금액 (해당하는 경우)
            request_data: 요청 데이터
            response_data: 응답 데이터
            metadata: 추가 메타데이터
            snapshot_data: 복구용 스냅샷 (metadata에 병합)
        """
        from datetime import timedelta

        from django.conf import settings

        # SELF_HEALING 설정 우선, PAYMENT_RECOVERY는 하위 호환
        self_healing_config = getattr(settings, "SELF_HEALING", {})
        legacy_config = getattr(settings, "PAYMENT_RECOVERY", {})
        retention_days = self_healing_config.get(
            "DLQ_RETENTION_DAYS",
            legacy_config.get("DLQ_RETENTION_DAYS", 30)
        )
        expires_at = timezone.now() + timedelta(days=retention_days)

        # 메타데이터에 스냅샷 병합
        final_metadata = metadata or {}
        if snapshot_data:
            final_metadata["snapshot_data"] = snapshot_data

        return cls.objects.create(
            domain=domain,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_refs=entity_refs or {},
            user_id=user_id,
            external_request_id=external_request_id,
            external_transaction_id=external_transaction_id,
            amount=amount if amount is not None else Decimal("0"),
            failure_type=failure_type,
            error_code=error_code,
            error_message=error_message,
            retry_count=retry_count,
            last_retry_at=timezone.now() if retry_count > 0 else None,
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=final_metadata,
            expires_at=expires_at,
        )

    # Backward compatibility aliases removed - use entity_type/entity_id instead
class CircuitBreakerState(models.Model):
    """
    Circuit Breaker 상태 저장

    외부 서비스 장애 감지 시 운영자가 수동으로 활성화하거나,
    자동으로 상태를 추적합니다.
    """

    STATE_CHOICES = [
        ("closed", "정상 (Closed)"),
        ("open", "차단 (Open)"),
        ("half_open", "테스트 중 (Half-Open)"),
    ]

    # 서비스 식별자 (예: 'external_api', 'payment_gateway')
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

    # 수동 제어자 ID (도메인 중립)
    controlled_by_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="제어자 ID",
    )

    # 제어 사유
    control_reason = models.TextField(
        blank=True,
        verbose_name="제어 사유",
    )

    # Manual override expiration (TTL)
    # Prevents indefinite manual blocks - auto-expires after configured duration
    manual_override_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="수동 제어 만료 시각",
        help_text="Manual override automatically expires after this time",
    )

    # Half-open request counter for governance
    # Limits how many test requests are allowed in half-open state
    half_open_request_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Half-Open 요청 횟수",
        help_text="Number of requests allowed through in half-open state",
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

        # SELF_HEALING 설정 우선, PAYMENT_RECOVERY는 하위 호환
        self_healing_config = getattr(settings, "SELF_HEALING", {})
        legacy_config = getattr(settings, "PAYMENT_RECOVERY", {})
        cb_config = self_healing_config.get("CIRCUIT_BREAKER", legacy_config)

        if cb_config.get("CIRCUIT_BREAKER_ENABLED", cb_config.get("ENABLED", False)):
            threshold = cb_config.get("CIRCUIT_BREAKER_FAILURE_THRESHOLD", cb_config.get("FAILURE_THRESHOLD", 5))
            if self.failure_count >= threshold and self.state == "closed":
                self.state = "open"
                self.opened_at = timezone.now()

        self.save()

    def record_success(self) -> None:
        """성공 기록 및 상태 전환 체크"""
        from django.conf import settings

        if self.state == "half_open":
            self.success_count += 1

            # SELF_HEALING 설정 우선
            self_healing_config = getattr(settings, "SELF_HEALING", {})
            legacy_config = getattr(settings, "PAYMENT_RECOVERY", {})
            cb_config = self_healing_config.get("CIRCUIT_BREAKER", legacy_config)
            success_threshold = cb_config.get("CIRCUIT_BREAKER_SUCCESS_THRESHOLD", cb_config.get("SUCCESS_THRESHOLD", 2))

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

        # SELF_HEALING 설정 우선
        self_healing_config = getattr(settings, "SELF_HEALING", {})
        legacy_config = getattr(settings, "PAYMENT_RECOVERY", {})
        cb_config = self_healing_config.get("CIRCUIT_BREAKER", legacy_config)

        # Circuit Breaker가 비활성화되어 있으면 항상 허용
        if not cb_config.get("CIRCUIT_BREAKER_ENABLED", cb_config.get("ENABLED", False)):
            return True

        if self.state == "closed":
            return True

        if self.state == "open":
            # Recovery timeout 확인
            recovery_timeout = cb_config.get("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", cb_config.get("RECOVERY_TIMEOUT", 60))
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

    def force_open(
        self,
        controlled_by_id: int | None = None,
        reason: str = "",
        ttl_minutes: int = 90,
    ) -> None:
        """
        Manually transition to OPEN state (block all requests).

        Manual override = "Code Freeze for Recovery System"
        - Automatic failure/success recording is bypassed
        - AI/retry logic recommendations are ignored
        - Replay mode becomes manual-only
        - Override expires automatically after TTL to prevent forgotten blocks

        Args:
            controlled_by_id: User ID who initiated the override
            reason: Reason for the manual override
            ttl_minutes: Time-to-live in minutes (default 90, max recommended 180)
        """
        from datetime import timedelta

        self.state = "open"
        self.opened_at = timezone.now()
        self.manually_controlled = True
        self.controlled_by_id = controlled_by_id
        self.control_reason = reason
        # Set TTL - manual overrides should never be indefinite
        self.manual_override_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        self.save()

    def force_close(self, controlled_by_id: int | None = None, reason: str = "") -> None:
        """
        Manually transition to CLOSED state (allow all requests).

        Clears manual override and resets all counters.
        If triggered with replay, failures are routed to REQUIRES_REVIEW.

        Args:
            controlled_by_id: User ID who initiated the override
            reason: Reason for the manual override
        """
        self.state = "closed"
        self.failure_count = 0
        self.success_count = 0
        self.half_open_request_count = 0
        self.opened_at = None
        self.manually_controlled = True
        self.controlled_by_id = controlled_by_id
        self.control_reason = reason
        # Clear TTL on close
        self.manual_override_expires_at = None
        self.save()

    def is_manual_override_expired(self) -> bool:
        """
        Check if manual override has expired.

        Returns:
            True if manual override TTL has passed, False otherwise
        """
        if not self.manually_controlled:
            return False
        if not self.manual_override_expires_at:
            return False
        return timezone.now() >= self.manual_override_expires_at

    def expire_manual_override(self) -> None:
        """
        Expire the manual override and transition based on current state.

        When a manual override expires:
        - If OPEN: transition to HALF_OPEN for gradual recovery
        - Clear the manually_controlled flag
        - Log the expiration for audit
        """
        if not self.is_manual_override_expired():
            return

        # If manually opened, transition to half-open for testing
        if self.state == "open":
            self.state = "half_open"
            self.success_count = 0
            self.half_open_request_count = 0

        self.manually_controlled = False
        self.manual_override_expires_at = None
        self.control_reason = f"{self.control_reason} [EXPIRED]"
        self.save()
