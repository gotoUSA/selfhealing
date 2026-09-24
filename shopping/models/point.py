from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet, Sum

if TYPE_CHECKING:
    from shopping.models.order import Order
    from shopping.models.user import User

logger = logging.getLogger(__name__)


class PointHistoryManager(models.Manager):
    """포인트 이력 커스텀 매니저"""

    def get_total_earned(self, user: User) -> int:
        """
        사용자의 총 적립 포인트를 계산합니다.

        Args:
            user: 조회할 사용자

        Returns:
            int: 총 적립 포인트
        """
        result = self.filter(user=user, points__gt=0).aggregate(total=Sum("points"))
        return result["total"] or 0

    def get_total_used(self, user: User) -> int:
        """
        사용자의 총 사용 포인트를 계산합니다.

        Args:
            user: 조회할 사용자

        Returns:
            int: 총 사용 포인트 (절댓값)
        """
        result = self.filter(user=user, points__lt=0).aggregate(total=Sum("points"))
        return abs(result["total"] or 0)

    def get_expiring_soon(self, user: User, days: int = 30) -> int:
        """
        사용자의 만료 예정 포인트를 계산합니다.

        Args:
            user: 조회할 사용자
            days: 앞으로 며칠 이내 만료 포인트를 조회할지 (기본: 30일)

        Returns:
            int: 만료 예정 총 포인트
        """
        from datetime import timedelta

        from django.utils import timezone

        expire_date = timezone.now() + timedelta(days=days)
        histories = self.filter(
            user=user,
            type="earn",
            points__gt=0,
            expires_at__isnull=False,
            expires_at__lte=expire_date,
            expires_at__gt=timezone.now(),
        )
        # 원래 적립액이 아니라 남은 양 — 이미 쓴 포인트는 만료되지 않는다
        return sum(h.remaining_points for h in histories)

    def get_month_statistics(self, user: User, start_date) -> dict[str, int]:
        """
        특정 기간 동안의 포인트 통계를 계산합니다.

        Args:
            user: 조회할 사용자
            start_date: 시작 날짜

        Returns:
            dict: {"earned": 적립, "used": 사용}
        """
        earned = self.filter(user=user, points__gt=0, created_at__gte=start_date).aggregate(total=Sum("points"))["total"] or 0
        used = abs(
            self.filter(user=user, points__lt=0, created_at__gte=start_date).aggregate(total=Sum("points"))["total"] or 0
        )
        return {"earned": earned, "used": used}

    def optimized_for_list(self) -> QuerySet:
        """
        목록 조회에 최적화된 쿼리셋을 반환합니다.
        order 외래키를 select_related로 미리 로드합니다.
        """
        return self.select_related("order")


class PointHistory(models.Model):
    """
    포인트 이력 관리 모델
    사용자의 모든 포인트 변동 내역을 기록합니다.
    """

    objects = PointHistoryManager()

    # 포인트 이력 타입
    TYPE_CHOICES = [
        ("earn", "적립"),  # 구매 시 적립
        ("use", "사용"),  # 주문 시 사용
        ("cancel_refund", "취소환불"),  # 주문 취소로 인한 환불
        ("cancel_deduct", "취소차감"),  # 주문 취소로 인한 적립 포인트 차감
        ("payment_fail_refund", "결제실패환불"),  # 결제 실패로 인한 포인트 환불
        ("expire", "만료"),  # 유효기간 만료
        ("admin_add", "관리자지급"),  # 관리자가 수동으로 지급
        ("admin_deduct", "관리자차감"),  # 관리자가 수동으로 차감
        ("event", "이벤트"),  # 이벤트 지급
    ]

    # 사용자
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="point_histories",
        verbose_name="사용자",
    )

    # 포인트 변동량 (양수: 적립, 음수: 사용/차감)
    points = models.IntegerField(verbose_name="포인트", help_text="양수는 적립, 음수는 사용/차감")

    # 변경 후 잔액
    balance = models.PositiveIntegerField(verbose_name="잔액", help_text="변경 후 포인트 잔액")

    # 포인트 타입
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="타입")

    # 관련 주문 (있는 경우)
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="point_histories",
        verbose_name="관련 주문",
    )

    # 설명
    description = models.CharField(max_length=255, verbose_name="설명", help_text="포인트 변동 사유")

    # 유효기간 (적립 포인트의 경우)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="만료일시",
        help_text="적립 포인트의 유효기간",
    )

    # 메타 데이터 (추가 정보 저장용)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="메타데이터",
        help_text="추가 정보 (JSON)",
    )

    # 생성일시
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성일시")

    class Meta:
        db_table = "shopping_point_history"
        verbose_name = "포인트 이력"
        verbose_name_plural = "포인트 이력 목록"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["type"]),
            models.Index(fields=["order"]),
            models.Index(fields=["expires_at"]),  # 만료 포인트 배치 조회용
        ]
        constraints = [
            # 구매 적립은 주문당 한 번 — 적립 태스크가 재배달·재시도로 다시 돌아도 DB 가 두 번째를 거절한다
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(type="earn"),
                name="unique_earn_per_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.get_type_display()} {self.points:+d}P"

    def clean(self) -> None:
        """
        포인트 이력 데이터 검증
        """
        super().clean()

        # 포인트 변동량은 0이 될 수 없음
        if self.points == 0:
            raise ValidationError({"points": "포인트 변동량은 0이 될 수 없습니다."})

        # type별 points 부호 검증
        positive_types = {"earn", "cancel_refund", "admin_add", "event"}
        negative_types = {"use", "cancel_deduct", "admin_deduct", "expire"}

        if self.type in positive_types and self.points <= 0:
            raise ValidationError({"points": f"{self.get_type_display()}는 양수 포인트여야 합니다."})

        if self.type in negative_types and self.points >= 0:
            raise ValidationError({"points": f"{self.get_type_display()}는 음수 포인트여야 합니다."})

        # 잔액은 항상 0 이상이어야 함
        if self.balance < 0:
            raise ValidationError({"balance": "잔액은 음수가 될 수 없습니다."})

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """
        포인트 이력은 삭제할 수 없습니다.

        원장(Ledger) 시스템의 무결성을 위해 삭제를 금지합니다.
        오류 수정이 필요한 경우 반제(Reversal) 거래를 생성하세요.

        Raises:
            ValueError: 항상 발생
        """
        raise ValueError("포인트 이력은 삭제할 수 없습니다. " "오류 수정은 반제(Reversal) 거래를 생성해주세요.")

    @property
    def remaining_points(self) -> int:
        """
        적립 건의 남은 양 (적립액 − 사용량). 적립이 아니거나 만료 처리된 건은 0.

        만료 배치·만료 안내 메일·만료 예정 화면이 모두 이 값을 쓴다 — 같은 개념을 두 곳에서 따로 계산하면
        한쪽은 원래 적립액을, 다른 쪽은 남은 양을 보게 된다.
        """
        if self.type != "earn":
            return 0
        metadata = self.metadata or {}
        if metadata.get("expired"):
            return 0
        return max(0, self.points - metadata.get("used_amount", 0))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        포인트 이력 저장 (수정 제한)

        원장(Ledger) 시스템의 무결성을 위해:
        - 신규 생성: 허용
        - metadata 업데이트: 허용 (FIFO 추적용)
        - 핵심 필드(points, balance, type 등) 수정: 금지

        Note:
            포인트 이력을 직접 저장하지 마세요.
            반드시 PointHistory.create_history() 또는 PointService를 사용하세요.
        """
        if self.pk:  # 기존 레코드 수정
            update_fields = kwargs.get("update_fields")
            if update_fields:
                # metadata와 created_at만 업데이트 허용 (테스트용 날짜 조작 포함)
                allowed_fields = {"metadata", "created_at"}
                requested_fields = set(update_fields)
                if not requested_fields.issubset(allowed_fields):
                    disallowed = requested_fields - allowed_fields
                    raise ValueError(f"포인트 이력의 핵심 필드는 수정할 수 없습니다. " f"수정 불가 필드: {disallowed}")
            else:
                # update_fields 없이 save() 호출 시 경고
                logger.warning(f"PointHistory.save() 호출 시 update_fields를 명시해주세요. " f"(history_id={self.pk})")

        super().save(*args, **kwargs)

    @classmethod
    def create_history(
        cls,
        user: User,
        points: int,
        balance: int,
        type: str,
        order: Order | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> PointHistory:
        """
        포인트 이력 생성 헬퍼 메서드

        Args:
            user: 사용자
            points: 포인트 변동량
            balance: 변경 후 잔액 (명시적 전달 필수)
            type: 이력 타입
            order: 관련 주문 (선택)
            description: 설명 (선택)
            **kwargs: 추가 필드

        Returns:
            PointHistory: 생성된 이력 객체
        """

        # 설명 자동 생성
        if not description:
            type_display = dict(cls.TYPE_CHOICES).get(type, type)
            if order:
                description = f"주문 #{order.order_number} {type_display}"
            else:
                description = type_display

        # 유효기간 설정 (적립의 경우 1년)
        expires_at = kwargs.pop("expires_at", None)
        if type == "earn" and not expires_at:
            from datetime import timedelta

            from django.utils import timezone

            expires_at = timezone.now() + timedelta(days=365)

        return cls.objects.create(
            user=user,
            points=points,
            balance=balance,
            type=type,
            order=order,
            description=description,
            expires_at=expires_at,
            **kwargs,
        )

    @classmethod
    def get_user_balance(cls, user: User) -> int:
        """
        사용자의 현재 포인트 잔액을 계산합니다.

        가장 최근 이력의 balance를 반환하며, 이력이 없는 경우 0을 반환합니다.
        이는 최적화된 방법으로, 모든 이력을 합산하는 것보다 효율적입니다.

        Args:
            user: 포인트 잔액을 조회할 사용자

        Returns:
            int: 현재 포인트 잔액
        """
        # created_at 동률(같은 트랜잭션에서 연속 생성) 시 나중에 삽입된 행이 최신이 되도록 id로 끊는다
        latest_history = cls.objects.filter(user=user).order_by("-created_at", "-id").only("balance").first()
        return latest_history.balance if latest_history else 0

    @classmethod
    def get_expiring_points(cls, user: User, days: int = 30) -> dict[str, Any]:
        """
        사용자의 만료 예정 포인트를 조회합니다.

        Args:
            user: 조회할 사용자
            days: 앞으로 며칠 이내 만료 포인트를 조회할지 (기본: 30일)

        Returns:
            dict: {
                'total_expiring_points': 만료 예정 총 포인트,
                'expiring_histories': 만료 예정 이력 QuerySet,
                'earliest_expire_date': 가장 빠른 만료일
            }
        """
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        expire_threshold = now + timedelta(days=days)

        # 만료 예정이면서 아직 사용되지 않은 적립 포인트만 조회
        # (음수 포인트로 상쇄되지 않은 것)
        expiring_histories = cls.objects.filter(
            user=user,
            type="earn",
            expires_at__isnull=False,
            expires_at__lte=expire_threshold,
            expires_at__gte=now,
        ).order_by("expires_at")

        # 남은 양이 있는 적립 건만 — 다 쓴 적립 건은 만료될 게 없다
        remaining_ids = [h.pk for h in expiring_histories if h.remaining_points > 0]
        expiring_histories = expiring_histories.filter(pk__in=remaining_ids)

        # 만료 예정 포인트 총합
        total_expiring = sum(h.remaining_points for h in expiring_histories)

        # 가장 빠른 만료일
        earliest_expire = expiring_histories.values_list("expires_at", flat=True).first()

        return {
            "total_expiring_points": total_expiring,
            "expiring_histories": expiring_histories,
            "earliest_expire_date": earliest_expire,
        }
