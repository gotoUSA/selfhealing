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
        retention_days = self_healing_config.get("DLQ_RETENTION_DAYS", legacy_config.get("DLQ_RETENTION_DAYS", 30))
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
