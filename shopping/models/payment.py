from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Payment(models.Model):
    """
    결제 정보 모델
    토스페이먼츠 결제 데이터를 저장합니다.
    """

    # 결제 상태 선택지
    STATUS_CHOICES = [
        ("ready", "결제 준비"),  # 결제창 오픈 전
        ("in_progress", "결제 진행중"),  # 결제창에서 진행중
        ("waiting_for_deposit", "입금 대기"),  # 가상계좌 입금 대기
        ("done", "결제 완료"),  # 결제 성공
        ("canceled", "결제 취소"),  # 전체 취소
        ("partial_canceled", "부분 취소"),  # 부분 취소 (향후 확장용)
        ("aborted", "결제 실패"),  # 결제 실패
        ("expired", "결제 만료"),  # 가상계좌 입금 기한 만료
    ]

    # 주문 참조 (1:1 관계)
    order = models.OneToOneField(
        "Order",
        on_delete=models.PROTECT,  # 결제 기록이 있으면 주문 삭제 불가
        related_name="payment",
        verbose_name="주문",
    )

    # 토스페이먼츠 결제 정보
    payment_key = models.CharField(
        max_length=200,
        unique=True,
        null=True,
        blank=True,
        verbose_name="토스 결제키",
        help_text="토스페이먼츠에서 발급한 고유 결제키",
    )

    toss_order_id = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="토스 주문번호",
        help_text="우리 시스템의 주문번호",
    )

    # 멱등성 키 (결제 중복 방지)
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="멱등성 키",
        help_text="결제 요청의 멱등성을 보장하기 위한 고유 키 (클라이언트가 생성, Redis TTL 60초로 관리)",
    )

    # 금액 정보
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        verbose_name="결제 금액",
    )

    # 결제 수단 정보
    method = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="결제 수단",
        help_text="카드, 계좌이체, 가상계좌 등",
    )

    # 카드 정보 (카드 결제시)
    card_company = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="카드사",
    )

    card_number = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="카드번호",
        help_text="마스킹된 카드번호",
    )

    installment_plan_months = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(60)],
        verbose_name="할부 개월수",
        help_text="0은 일시불, 최대 60개월",
    )

    # 결제 상태
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="ready",
        verbose_name="결제 상태",
    )

    # 승인 정보
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="승인 일시",
    )

    # 영수증 URL
    receipt_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="영수증 URL",
    )

    # 가상계좌 정보 (승인 응답 virtualAccount 객체 — 고객에게 안내할 입금 계좌)
    virtual_account_bank_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="가상계좌 은행코드",
    )

    virtual_account_number = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="가상계좌 번호",
    )

    virtual_account_due_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="입금 기한",
    )

    # 토스가 승인 응답에 주는 웹훅 검증값 — 가상계좌 입금 웹훅(DEPOSIT_CALLBACK)의 secret 과 비교
    toss_secret = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="토스 웹훅 검증값",
    )

    # 취소 정보 (전체 취소시)
    is_canceled = models.BooleanField(
        default=False,
        verbose_name="취소 여부",
    )

    canceled_amount = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=Decimal("0"),
        verbose_name="취소 금액",
    )

    cancel_reason = models.TextField(
        blank=True,
        verbose_name="취소 사유",
    )

    canceled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="취소 일시",
    )

    # 토스페이먼츠 원본 응답 저장 (디버깅용)
    raw_response = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="토스 응답 원본",
        help_text="토스페이먼츠 API 응답 전체",
    )

    # 실패 정보
    fail_reason = models.TextField(
        blank=True,
        verbose_name="실패 사유",
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
        db_table = "shopping_payment"
        verbose_name = "결제"
        verbose_name_plural = "결제 목록"
        ordering = ["-created_at"]
        indexes = [
            # payment_key, toss_order_id는 unique=True로 자동 인덱스 생성
            models.Index(fields=["status", "-created_at"]),  # 상태별 최근 결제 조회
            models.Index(fields=["-created_at"]),  # 전체 최근 결제 조회
        ]

    def __str__(self) -> str:
        return f"{self.toss_order_id} - {self.get_status_display()} ({self.amount:,}원)"

    def clean(self) -> None:
        """모델 수준 검증"""
        super().clean()

        # 1. 결제 금액은 0보다 커야 함
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "결제 금액은 0보다 커야 합니다."})

        # 2. 취소 금액이 결제 금액을 초과할 수 없음
        if self.canceled_amount > self.amount:
            raise ValidationError({"canceled_amount": "취소 금액이 결제 금액을 초과할 수 없습니다."})

        # 3. 완료 상태에서는 payment_key 필수
        if self.status == "done" and not self.payment_key:
            raise ValidationError({"payment_key": "결제 완료 상태에서는 결제키가 필요합니다."})

        # 4. 완료 상태에서는 승인 일시 필수
        if self.status == "done" and not self.approved_at:
            raise ValidationError({"approved_at": "결제 완료 상태에서는 승인 일시가 필요합니다."})

        # 5. 취소 상태에서는 취소 관련 필드 필수
        if self.status == "canceled" and not self.canceled_at:
            raise ValidationError({"canceled_at": "결제 취소 상태에서는 취소 일시가 필요합니다."})

    @property
    def is_paid(self) -> bool:
        """결제 완료 여부"""
        return self.status == "done"

    @property
    def is_waiting_for_deposit(self) -> bool:
        """가상계좌 입금 대기 여부"""
        return self.status == "waiting_for_deposit"

    @property
    def can_cancel(self) -> bool:
        """취소 가능 여부"""
        return self.status == "done" and not self.is_canceled

    def mark_as_paid(self, payment_data: dict[str, Any]) -> None:
        """
        결제 완료 처리
        토스페이먼츠 승인 응답으로 정보 업데이트
        """
        self.status = "done"
        self.payment_key = payment_data.get("paymentKey")
        self.approved_at = payment_data.get("approvedAt")
        self.method = payment_data.get("method", "")

        # 카드 정보 저장
        card = payment_data.get("card", {})
        if card:
            self.card_company = card.get("company", "")
            self.card_number = card.get("number", "")
            self.installment_plan_months = card.get("installmentPlanMonths", 0)

        # 영수증 URL
        self.receipt_url = payment_data.get("receipt", {}).get("url", "")

        # 민감 정보 제거 후 원본 응답 저장
        self.raw_response = self.sanitize_raw_response(payment_data)

        self.save()

    def mark_as_waiting_for_deposit(self, payment_data: dict[str, Any]) -> None:
        """
        가상계좌 발급 처리 (status=WAITING_FOR_DEPOSIT)
        토스페이먼츠 승인 응답으로 계좌 정보와 웹훅 검증값을 저장하고 입금을 기다린다
        """
        self.status = "waiting_for_deposit"
        self.payment_key = payment_data.get("paymentKey") or self.payment_key
        self.method = payment_data.get("method", "") or self.method

        virtual_account = payment_data.get("virtualAccount") or {}
        self.virtual_account_bank_code = virtual_account.get("bankCode", "")
        self.virtual_account_number = virtual_account.get("accountNumber", "")
        self.virtual_account_due_date = virtual_account.get("dueDate")

        self.toss_secret = payment_data.get("secret", "") or self.toss_secret

        # 민감 정보 제거 후 원본 응답 저장
        self.raw_response = self.sanitize_raw_response(payment_data)

        self.save()

    def mark_as_expired(self, reason: str = "") -> None:
        """결제 만료 처리 (가상계좌 입금 기한 경과 등)"""
        self.status = "expired"
        self.fail_reason = reason
        self.save()

    def mark_as_failed(self, reason: str = "") -> None:
        """결제 실패 처리"""
        self.status = "aborted"
        self.fail_reason = reason
        self.save()

    def mark_as_canceled(self, cancel_data: dict[str, Any]) -> None:
        """
        결제 취소 처리
        토스페이먼츠 Payment 객체(취소 API 응답 · 결제 조회 응답)로 정보 업데이트
        """
        self.status = "canceled"
        self.is_canceled = True
        self.canceled_amount = self.amount  # 전체 취소

        # 토스 Payment 객체는 취소 이력을 cancels[] 에 담는다 — 마지막 항목이 이번 취소
        cancels = cancel_data.get("cancels") or []
        latest_cancel = cancels[-1] if cancels else cancel_data
        self.cancel_reason = latest_cancel.get("cancelReason", "")
        self.canceled_at = latest_cancel.get("canceledAt")

        # 민감 정보 제거 후 원본 응답 업데이트
        self.raw_response = self.sanitize_raw_response(cancel_data)

        self.save()

    def mark_as_partial_canceled(self, partial_amount: Decimal, cancel_data: dict[str, Any]) -> None:
        """
        부분 취소 처리

        TODO: 부분 취소 로직 구현 필요
        - 취소 금액 누적 관리
        - 부분 취소 내역 추적
        - 잔여 금액 계산
        """
        # 구현 예정
        raise NotImplementedError("부분 취소 기능은 향후 구현 예정입니다.")

    def sanitize_raw_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """
        민감 정보를 제거한 응답 데이터 반환

        보안상 저장하지 말아야 할 정보:
        - 완전한 카드번호
        - CVV/CVC
        - 비밀번호
        - 개인 인증 정보
        """
        # 저장하지 않을 민감 필드 목록
        sensitive_fields = {"cardNumber", "cvv", "cvc", "password", "pin"}

        def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
            """재귀적으로 민감 정보 제거"""
            sanitized = {}
            for key, value in data.items():
                if key in sensitive_fields:
                    sanitized[key] = "***REDACTED***"
                elif isinstance(value, dict):
                    sanitized[key] = _sanitize(value)
                elif isinstance(value, list):
                    sanitized[key] = [_sanitize(item) if isinstance(item, dict) else item for item in value]
                else:
                    sanitized[key] = value
            return sanitized

        return _sanitize(response)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Payment는 반드시 PaymentService를 통해서만 생성되어야 합니다.

        올바른 사용:
            PaymentService.create_payment(order, payment_method)

        테스트 등에서 직접 생성 시 모든 필드를 명시적으로 설정해야 합니다:
            Payment.objects.create(
                order=order,
                toss_order_id=Payment.toss_order_id_for(order),  # Toss orderId와 일치 (명시적 설정 필수)
                amount=order.final_amount,
                status="ready",
            )
        """
        super().save(*args, **kwargs)

    @staticmethod
    def toss_order_id_for(order) -> str:
        """
        토스에 보내는 orderId — 주문번호를 쓴다

        토스 규칙: 영문 대소문자·숫자·-·_ 만, 6자 이상 64자 이하. 주문 PK 는 짧으면(예: "9") 결제창에서
        거부되므로 14자리 주문번호(예: 20260922000009)를 쓴다. 승인·조회·웹훅이 전부 이 값으로 우리 결제를 찾는다.
        """
        return order.order_number


class PaymentLog(models.Model):
    """
    결제 로그 모델
    결제 과정의 모든 이벤트를 기록합니다.
    """

    LOG_TYPE_CHOICES = [
        ("request", "결제 요청"),
        ("approve", "결제 승인"),
        ("cancel", "결제 취소"),
        ("webhook", "웹훅 수신"),
        ("error", "에러 발생"),
    ]

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="결제",
    )

    log_type = models.CharField(
        max_length=20,
        choices=LOG_TYPE_CHOICES,
        verbose_name="로그 타입",
    )

    message = models.TextField(verbose_name="로그 메시지")

    data = models.JSONField(default=dict, blank=True, verbose_name="추가 데이터")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성일시")

    class Meta:
        db_table = "shopping_payment_logs"
        verbose_name = "결제 로그"
        verbose_name_plural = "결제 로그 목록"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["payment", "-created_at"]),  # 특정 결제의 최근 로그 조회
            models.Index(fields=["log_type"]),  # 로그 타입별 필터링
            models.Index(fields=["-created_at"]),  # 전체 최근 로그 조회
        ]

    def __str__(self) -> str:
        return f"[{self.get_log_type_display()}] {self.payment.order_id} - {self.created_at}"
