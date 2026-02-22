from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    pass


class Notification(models.Model):
    """
    인앱 알림 모델

    사용자에게 실시간으로 알림을 보내기 위한 모델입니다.
    답변 알림, 배송 알림 등 다양한 용도로 사용됩니다.
    """

    # 알림 타입 선택지
    TYPE_CHOICES = [
        ("qa_answer", "상품 문의 답변"),
        ("order_status", "주문 상태 변경"),
        ("point_earned", "포인트 적립"),
        ("point_expiring", "포인트 만료 예정"),
        ("review_reply", "리뷰 답글"),
        ("return", "교환/환불"),
    ]

    # 기본 정보
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="사용자",
    )

    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="알림 타입")

    # 알림 내용
    title = models.CharField(max_length=100, verbose_name="제목")
    message = models.TextField(verbose_name="내용")

    # 링크 (클릭 시 이동할 URL)
    link = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="링크",
        help_text="클릭 시 이동할 URL (예: /products/123/questions/45)",
    )

    # 추가 메타데이터 (JSON)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="메타데이터",
        help_text="알림 관련 추가 데이터 (예: return_id, order_id 등)",
    )

    # 읽음 여부
    is_read = models.BooleanField(default=False, verbose_name="읽음 여부")

    # 읽은 시간
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="읽은 시간")

    # 생성 시간
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성 시간")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "알림"
        verbose_name_plural = "알림"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "is_read"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_notification_type_display()}] {self.title}"

    def mark_as_read(self) -> None:
        """알림을 읽음 처리"""
        from django.utils import timezone

        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])
