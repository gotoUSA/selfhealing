"""웹훅 이벤트 로깅 모델"""

from django.db import models


class WebhookEvent(models.Model):
    """
    웹훅 이벤트 로깅 모델

    토스페이먼츠 등 외부 서비스의 웹훅 이벤트를 기록합니다.
    중복 방지 목적이 아닌 순수 감사 로그 목적입니다.

    Note:
        - 중복 방지는 Redis TTL (60초)로 처리
        - 이 모델은 로깅/디버깅 목적으로만 사용
    """

    event_id = models.CharField(
        max_length=200,
        db_index=True,
        verbose_name="이벤트 ID",
        help_text="웹훅 고유 식별자 (orderId + createdAt 조합)",
    )
    event_type = models.CharField(
        max_length=50,
        verbose_name="이벤트 타입",
        help_text="PAYMENT.DONE, PAYMENT.CANCELED 등",
    )
    source = models.CharField(
        max_length=50,
        default="toss",
        verbose_name="소스",
        help_text="웹훅 발송 서비스 (toss, kakao 등)",
    )
    order_id = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="주문 ID",
    )
    processed_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="처리 시간",
    )

    class Meta:
        db_table = "shopping_webhook_event"
        verbose_name = "웹훅 이벤트"
        verbose_name_plural = "웹훅 이벤트 목록"
        ordering = ["-processed_at"]
        indexes = [
            models.Index(fields=["source", "-processed_at"]),
            models.Index(fields=["-processed_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.source}] {self.event_type} - {self.order_id}"
