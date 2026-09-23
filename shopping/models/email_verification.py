from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    pass


class EmailVerificationToken(models.Model):
    """이메일 인증 토큰 모델"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_tokens",
        verbose_name="사용자",
        help_text="인증 토큰을 소유한 사용자",
    )

    # UUID 토큰 (링크용)
    token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="인증 토큰",
        help_text="이메일 링크에 사용되는 UUID 토큰 (URL-safe)",
    )

    # 6자리 인증 코드 (직접 입력용)
    verification_code = models.CharField(
        max_length=6,
        null=False,
        blank=False,
        editable=False,
        verbose_name="인증 코드",
        help_text="6자리 영문 대문자 + 숫자 조합 (직접 입력용)",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성일시")

    is_used = models.BooleanField(
        default=False,
        verbose_name="사용 여부",
        help_text="토큰 사용 여부 (True: 사용됨, False: 미사용)",
    )

    used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="사용일시",
        help_text="토큰이 사용된 일시",
    )

    class Meta:
        db_table = "email_verification_tokens"
        verbose_name = "이메일 인증 토큰"
        verbose_name_plural = "이메일 인증 토큰"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["verification_code", "user"]),
            models.Index(fields=["is_used", "created_at"]),  # 미사용 토큰 조회 최적화
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "verification_code"],
                name="unique_user_verification_code",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """저장시 6자리 코드 자동 생성"""
        if not self.verification_code:
            self.verification_code = self.generate_verification_code()
        super().save(*args, **kwargs)

    def generate_verification_code(self) -> str:
        """6자리 영문+숫자 코드 생성 (암호학적으로 안전)"""
        characters = string.ascii_uppercase + string.digits
        max_attempts = 10

        for attempt in range(max_attempts):
            # secrets 모듈 사용으로 암호학적으로 안전한 난수 생성
            code = "".join(secrets.choice(characters) for _ in range(6))

            # 중복 체크 (같은 사용자의 유효한 코드)
            if not EmailVerificationToken.objects.filter(
                user=self.user, verification_code=code, is_used=False
            ).exists():
                return code

        # 최대 재시도 초과 시 (발생 확률 극히 낮음: 1/(36^6)^10)
        raise ValueError(
            f"Failed to generate unique verification code after {max_attempts} attempts. "
            "This is extremely unlikely and may indicate a data issue."
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        """토큰 만료 여부 확인 (24시간)"""
        now = now or timezone.now()
        expiry_time = self.created_at + timedelta(hours=24)
        return now > expiry_time

    def mark_as_used(self) -> None:
        """토큰을 사용됨으로 표시"""
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=["is_used", "used_at"])

    def can_resend(self, now: datetime | None = None) -> bool:
        """재발송 가능 여부 (1분 제한)"""
        now = now or timezone.now()
        time_limit = self.created_at + timedelta(minutes=1)
        return now > time_limit

    def __str__(self) -> str:
        return f"{self.user.email} - {self.verification_code} ({'사용됨' if self.is_used else '미사용'})"


class EmailLog(models.Model):
    """이메일 발송 로그 (모니터링용)"""

    EMAIL_TYPE_CHOICES = [
        ("verification", "이메일 인증"),
        ("password_reset", "비밀번호 재설정"),
        ("order_confirm", "주문 확인"),
        ("marketing", "마케팅"),
    ]

    STATUS_CHOICES = [
        ("pending", "대기중"),
        ("sent", "발송완료"),
        ("failed", "발송실패"),
        ("opened", "열람됨"),
        ("clicked", "클릭됨"),
        ("verified", "인증완료"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="email_logs",
        verbose_name="사용자",
        help_text="이메일을 수신한 사용자 (탈퇴 시에도 로그 보존)",
    )

    email_type = models.CharField(
        max_length=20,
        choices=EMAIL_TYPE_CHOICES,
        verbose_name="이메일 유형",
        help_text="발송된 이메일의 유형",
    )

    recipient_email = models.EmailField(
        db_index=True,
        verbose_name="수신자 이메일",
        help_text="이메일 수신자 주소",
    )

    subject = models.CharField(
        max_length=255,
        verbose_name="제목",
        help_text="이메일 제목",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="상태",
        help_text="이메일 발송 상태 (pending → sent → opened/clicked → verified)",
    )

    token = models.ForeignKey(
        EmailVerificationToken,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
        verbose_name="인증 토큰",
        help_text="연결된 이메일 인증 토큰 (인증 이메일인 경우)",
    )

    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="발송일시",
        help_text="이메일이 발송된 일시",
    )

    opened_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="열람일시",
        help_text="이메일이 열람된 일시",
    )

    clicked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="클릭일시",
        help_text="이메일 내 링크가 클릭된 일시",
    )

    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="인증완료일시",
        help_text="인증이 완료된 일시",
    )

    error_message = models.TextField(
        blank=True,
        default="",
        verbose_name="에러 메시지",
        help_text="발송 실패 시 에러 메시지",
    )

    # 발송 태스크의 Celery task id — 같은 메시지가 재배달·재시도로 다시 와도 같은 로그를 찾는다
    task_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        verbose_name="발송 태스크 ID",
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="마지막 실패 일시",
        help_text="재발송 스윕이 진행 중인 재시도와 겹치지 않도록 기다리는 기준",
    )

    resend_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="재발송 횟수",
        help_text="retry_failed_emails_task 가 다시 보낸 횟수",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성일시")

    class Meta:
        db_table = "email_logs"
        verbose_name = "이메일 로그"
        verbose_name_plural = "이메일 로그"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "email_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
            # recipient_email은 필드에 db_index=True로 정의됨
        ]

    def clean(self) -> None:
        """상태 전이 검증 로직"""
        super().clean()

        # 상태 전이 규칙 정의
        valid_transitions = {
            "pending": ["sent", "failed"],
            "sent": ["opened", "clicked", "verified", "failed"],
            "failed": [],  # 실패 상태에서는 전이 불가
            "opened": ["clicked", "verified"],
            "clicked": ["verified"],
            "verified": [],  # 인증 완료는 최종 상태
        }

        # 기존 객체인 경우 상태 전이 검증
        if self.pk:
            try:
                old_instance = EmailLog.objects.get(pk=self.pk)
                old_status = old_instance.status

                # 상태 변경이 있는 경우 검증
                if old_status != self.status:
                    allowed_statuses = valid_transitions.get(old_status, [])
                    if self.status not in allowed_statuses:
                        raise ValidationError(
                            {
                                "status": f"Invalid status transition: {old_status} → {self.status}. "
                                f"Allowed transitions from {old_status}: {', '.join(allowed_statuses) or 'None'}"
                            }
                        )
            except EmailLog.DoesNotExist:
                pass  # 새로운 객체인 경우 검증 생략

        # 타임스탬프 일관성 검증
        if self.sent_at and self.opened_at and self.sent_at > self.opened_at:
            raise ValidationError({"opened_at": "열람일시는 발송일시보다 이후여야 합니다."})

        if self.opened_at and self.clicked_at and self.opened_at > self.clicked_at:
            raise ValidationError({"clicked_at": "클릭일시는 열람일시보다 이후여야 합니다."})

        if self.clicked_at and self.verified_at and self.clicked_at > self.verified_at:
            raise ValidationError({"verified_at": "인증완료일시는 클릭일시보다 이후여야 합니다."})

    def mark_as_sent(self) -> None:
        """발송 완료 처리"""
        self.status = "sent"
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at"])

    def mark_as_failed(self, error_message: str = "") -> None:
        """발송 실패 처리"""
        self.status = "failed"
        self.error_message = error_message
        self.failed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "failed_at"])

    def mark_as_verified(self) -> None:
        """인증 완료 처리"""
        self.status = "verified"
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "verified_at"])

    def __str__(self) -> str:
        return f"{self.email_type} - {self.recipient_email} ({self.status})"
