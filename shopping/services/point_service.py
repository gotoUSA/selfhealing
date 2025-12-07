"""
포인트 관련 비즈니스 로직
FIFO 방식 포인트 사용 및 만료 처리
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from shopping.constants import (
    LOCK_CONTENTION_CRITICAL_THRESHOLD,
    LOCK_CONTENTION_WARNING_THRESHOLD,
)
from shopping.models.point import PointHistory

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from shopping.models.order import Order

User = get_user_model()
logger = logging.getLogger(__name__)


class PointService:
    """포인트 관련 서비스 클래스"""

    @staticmethod
    @transaction.atomic
    def add_points(
        user: AbstractBaseUser,
        amount: int,
        type: str = "earn",
        order: Optional[Order] = None,
        description: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        포인트 추가 (동시성 제어 포함)

        Args:
            user: 사용자
            amount: 추가할 포인트
            type: 포인트 타입 (earn, cancel_refund 등)
            order: 관련 주문 (선택)
            description: 설명
            metadata: 추가 메타데이터 (선택)

        Returns:
            bool: 성공 여부
        """
        if amount <= 0:
            logger.warning(f"Invalid point amount: {amount}")
            return False

        # 동시성 제어: F() 객체로 안전하게 증가
        User.objects.filter(pk=user.pk).update(points=F("points") + amount)

        # F() 객체로 업데이트 후 최신 값 가져오기
        user.refresh_from_db()

        # 포인트 이력 기록
        PointHistory.create_history(
            user=user,
            points=amount,
            balance=user.points,
            type=type,
            order=order,
            description=description or f"포인트 {amount}점 추가",
            metadata=metadata or {},
        )

        logger.info(f"포인트 추가: user_id={user.id}, amount={amount}, " f"type={type}, description={description}")

        return True

    @staticmethod
    @transaction.atomic
    def use_points(
        user: AbstractBaseUser,
        amount: int,
        type: str = "use",
        order: Optional[Order] = None,
        description: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        포인트 차감 (동시성 제어 포함)

        Args:
            user: 사용자
            amount: 차감할 포인트
            type: 포인트 타입 (use, cancel_deduct 등)
            order: 관련 주문 (선택)
            description: 설명
            metadata: 추가 메타데이터 (선택)

        Returns:
            bool: 성공 여부
        """
        if amount <= 0:
            logger.warning(f"Invalid point amount: {amount}")
            return False

        # 동시성 제어: select_for_update로 락 획득
        locked_user = User.objects.select_for_update().get(pk=user.pk)

        if locked_user.points < amount:
            logger.warning(f"포인트 부족: user_id={user.id}, " f"required={amount}, available={locked_user.points}")
            return False

        # F() 객체로 안전하게 차감
        User.objects.filter(pk=user.pk).update(points=F("points") - amount)

        # F() 객체로 업데이트 후 최신 값 가져오기
        user.refresh_from_db()

        # 포인트 이력 기록 (음수로 기록)
        PointHistory.create_history(
            user=user,
            points=-amount,
            balance=user.points,
            type=type,
            order=order,
            description=description or f"포인트 {amount}점 사용",
            metadata=metadata or {},
        )

        logger.info(f"포인트 차감: user_id={user.id}, amount={amount}, " f"type={type}, description={description}")

        return True

    def get_expired_points(self) -> list[PointHistory]:
        """
        만료된 포인트 조회

        Returns:
            만료된 포인트 이력 리스트
        """
        now = timezone.now()

        # 만료되지 않은 적립 포인트 중 만료일이 지난 것들
        expired_points = (
            PointHistory.objects.filter(type="earn", expires_at__lte=now)
            .exclude(
                # 이미 만료 처리된 포인트 제외
                metadata__contains={"expired": True}
            )
            .select_related("user")
        )

        return list(expired_points)

    def get_expiring_points_soon(self, days: int = 7) -> list[PointHistory]:
        """
        곧 만료될 포인트 조회 (기본 7일 이내)

        Args:
            days: 만료 예정 일수

        Returns:
            만료 예정 포인트 리스트
        """
        now = timezone.now()
        target_date = now + timedelta(days=days)

        # 7일 이내 만료 예정이고 아직 알림 안 보낸 포인트
        expiring_points = (
            PointHistory.objects.filter(
                type="earn",
                expires_at__gt=now,  # 아직 만료되지 않음
                expires_at__lte=target_date,  # 7일 이내 만료
            )
            .exclude(metadata__contains={"expiry_notified": True})
            .select_related("user")
        )

        return list(expiring_points)

    @transaction.atomic
    def expire_points(self) -> int:
        """
        만료된 포인트 일괄 처리

        Returns:
            처리된 포인트 건수
        """
        expired_points = self.get_expired_points()
        expired_count = 0

        for point_history in expired_points:
            try:
                # 남은 포인트 계산
                remaining = self.get_remaining_points(point_history)

                if remaining > 0:
                    # 동시성 제어 1: User 락 획득 (Deadlock 방지를 위해 User 먼저 락)
                    user = User.objects.select_for_update().get(pk=point_history.user_id)

                    # 동시성 제어 2: PointHistory 락 획득 및 상태 재확인
                    # 이미 다른 트랜잭션에서 만료 처리했을 수 있음
                    current_ph = PointHistory.objects.select_for_update().get(pk=point_history.id)
                    if current_ph.metadata.get("expired"):
                        continue

                    # 재계산 (혹시 그 사이 사용되었을 수 있음)
                    remaining = self.get_remaining_points(current_ph)
                    if remaining <= 0:
                        continue

                    # F() 객체로 안전하게 차감 (Greatest로 0 이하 방지)
                    User.objects.filter(pk=user.pk).update(points=Greatest(F("points") - remaining, 0))

                    # F() 객체로 업데이트 후 최신 값 가져오기
                    user.refresh_from_db()

                    # 만료 이력 생성
                    PointHistory.create_history(
                        user=user,
                        points=-remaining,
                        balance=user.points,
                        type="expire",
                        description=f"포인트 만료 (적립일: {point_history.created_at.date()})",
                        metadata={
                            "original_history_id": point_history.id,
                            "original_points": point_history.points,
                            "expired_amount": remaining,
                        },
                    )

                    # 원본 이력에 만료 표시
                    current_ph.metadata["expired"] = True
                    current_ph.metadata["expired_at"] = timezone.now().isoformat()
                    current_ph.metadata["expired_amount"] = remaining
                    current_ph.save(update_fields=["metadata"])

                    expired_count += 1

                    logger.info(f"포인트 만료 처리: User={user.username}, " f"Amount={remaining}, HistoryID={current_ph.id}")

            except Exception as e:
                logger.error(f"포인트 만료 처리 실패: HistoryID={point_history.id}, " f"Error={str(e)}")
                continue

        return expired_count

    def get_remaining_points(self, point_history: PointHistory) -> int:
        """
        특정 적립 건의 남은 포인트 계산

        Args:
            point_history: 포인트 적립 이력

        Returns:
            남은 포인트
        """
        if point_history.type != "earn":
            return 0

        # 메타데이터에서 사용된 포인트 확인
        used_amount = point_history.metadata.get("used_amount", 0)
        remaining = point_history.points - used_amount

        return max(0, remaining)

    def _validate_point_usage(self, amount: int, type: str, minimum_use_amount: int = 100) -> Optional[dict[str, Any]]:
        """
        포인트 사용 유효성 검증

        Args:
            amount: 사용할 포인트
            type: 포인트 타입
            minimum_use_amount: 최소 사용 금액

        Returns:
            에러가 있으면 에러 응답 dict, 없으면 None
        """
        if amount <= 0:
            return {
                "success": False,
                "used_details": [],
                "message": "사용할 포인트는 0보다 커야 합니다.",
            }

        if type == "use" and amount < minimum_use_amount:
            return {
                "success": False,
                "used_details": [],
                "message": f"포인트는 최소 {minimum_use_amount}포인트 이상 사용 가능합니다.",
            }

        return None

    def _update_earn_metadata(self, point_history: PointHistory, use_amount: int) -> None:
        """
        적립 포인트의 메타데이터 업데이트 (사용 기록 추가)

        Args:
            point_history: 적립 포인트 이력
            use_amount: 사용할 포인트
        """
        # JSONField는 in-place 수정이 save()에서 감지되지 않을 수 있으므로
        # 전체 dict를 복사하고 재할당해야 함
        earn_metadata = point_history.metadata.copy() if point_history.metadata else {}

        # used_amount 업데이트
        earn_metadata["used_amount"] = earn_metadata.get("used_amount", 0) + use_amount

        # usage_history 업데이트
        if "usage_history" not in earn_metadata:
            earn_metadata["usage_history"] = []
        earn_metadata["usage_history"].append({"amount": use_amount, "used_at": timezone.now().isoformat()})

        # 전체 metadata 재할당 (Django가 변경 감지하도록)
        point_history.metadata = earn_metadata
        point_history.save(update_fields=["metadata"])

    def _log_fifo_performance(self, user_pk: int, amount: int, elapsed: float, details_count: int) -> None:
        """
        FIFO 포인트 사용 성능 로깅

        Args:
            user_pk: 사용자 PK
            amount: 사용 포인트
            elapsed: 경과 시간
            details_count: 처리된 이력 수
        """
        if elapsed > LOCK_CONTENTION_CRITICAL_THRESHOLD:
            logger.error(
                f"포인트 FIFO 사용 심각한 지연: user_id={user_pk}, amount={amount}, "
                f"elapsed={elapsed:.2f}s, histories_processed={details_count}, "
                f"possible_deadlock=True"
            )
        elif elapsed > LOCK_CONTENTION_WARNING_THRESHOLD:
            logger.warning(
                f"포인트 FIFO 사용 지연: user_id={user_pk}, amount={amount}, "
                f"elapsed={elapsed:.2f}s, histories_processed={details_count}, "
                f"possible_lock_contention=True"
            )

    def get_usable_points(self, user: AbstractBaseUser, for_cancel: bool = True) -> int:
        """
        원장(PointHistory) 기준으로 실제 사용 가능한 포인트 계산

        Args:
            user: 사용자
            for_cancel: True면 cancel_deduct용 (만료되지 않은 포인트만),
                       False면 일반 사용 (모든 포인트)

        Returns:
            사용 가능한 포인트 합계
        """
        now = timezone.now()
        query = PointHistory.objects.filter(user=user, type="earn")

        if for_cancel:
            # cancel_deduct는 만료되지 않은 포인트만
            query = query.filter(expires_at__gt=now)

        query = query.exclude(metadata__contains={"expired": True})

        total_usable = 0
        for point_history in query:
            remaining = self.get_remaining_points(point_history)
            if remaining > 0:
                total_usable += remaining

        return total_usable

    @transaction.atomic
    def use_points_fifo(
        self,
        user: AbstractBaseUser,
        amount: int,
        type: str = "use",
        order: Optional[Order] = None,
        description: str = "",
        metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        FIFO 방식으로 포인트 사용

        Args:
            user: 사용자
            amount: 사용할 포인트
            type: 포인트 타입 (use, cancel_deduct 등)
            order: 관련 주문 (선택)
            description: 설명
            metadata: 추가 메타데이터 (선택)

        Returns:
            {
                'success': bool,
                'used_details': [{'history_id': int, 'amount': int}],
                'message': str
            }
        """
        start_time = time.time()
        MINIMUM_USE_AMOUNT = 100

        # 유효성 검증
        validation_error = self._validate_point_usage(amount, type, MINIMUM_USE_AMOUNT)
        if validation_error:
            return validation_error

        # 동시성 제어: select_for_update로 락 획득
        lock_start_time = time.time()
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        lock_elapsed = time.time() - lock_start_time

        if lock_elapsed > LOCK_CONTENTION_WARNING_THRESHOLD:
            logger.warning(
                f"포인트 사용자 락 획득 지연: user_id={user.pk}, elapsed={lock_elapsed:.2f}s, "
                f"possible_lock_contention=True"
            )

        if locked_user.points < amount:
            return {
                "success": False,
                "used_details": [],
                "message": "포인트가 부족합니다.",
            }

        # FIFO 방식 포인트 차감 수행
        used_details, remaining_to_use = self._consume_points_fifo(user, amount, type)

        # FIFO 차감 후 검증 (cancel_deduct만 해당)
        if type == "cancel_deduct" and remaining_to_use > 0:
            return {
                "success": False,
                "used_details": [],
                "message": f"유효한 포인트가 부족합니다. (필요: {amount}, 사용 가능: {amount - remaining_to_use})",
            }

        # F() 객체로 안전하게 포인트 차감
        User.objects.filter(pk=user.pk).update(points=F("points") - amount)
        user.refresh_from_db()

        # 사용 이력 생성
        history_metadata = metadata.copy() if metadata else {}
        history_metadata["used_details"] = used_details

        PointHistory.create_history(
            user=user,
            points=-amount,
            balance=user.points,
            type=type,
            order=order,
            description=description or "포인트 사용 (FIFO)",
            metadata=history_metadata,
        )

        total_elapsed = time.time() - start_time
        self._log_fifo_performance(user.pk, amount, total_elapsed, len(used_details))

        return {
            "success": True,
            "used_details": used_details,
            "message": f"{amount} 포인트를 사용했습니다.",
        }

    def _consume_points_fifo(self, user: AbstractBaseUser, amount: int, type: str) -> tuple[list[dict[str, Any]], int]:
        """
        FIFO 방식으로 포인트 이력에서 실제 차감 수행

        Args:
            user: 사용자
            amount: 사용할 포인트
            type: 포인트 타입

        Returns:
            (used_details, remaining_to_use) 튜플
        """
        now = timezone.now()
        query = PointHistory.objects.select_for_update().filter(user=user, type="earn")

        # 취소 회수는 만료되지 않은 포인트만 회수 가능
        if type == "cancel_deduct":
            query = query.filter(expires_at__gt=now)

        available_points = query.exclude(metadata__contains={"expired": True}).order_by("expires_at", "created_at")

        used_details = []
        remaining_to_use = amount

        for point_history in available_points:
            if remaining_to_use <= 0:
                break

            available = self.get_remaining_points(point_history)
            if available <= 0:
                continue

            use_from_this = min(available, remaining_to_use)

            # 메타데이터 업데이트
            self._update_earn_metadata(point_history, use_from_this)

            used_details.append(
                {
                    "history_id": point_history.id,
                    "amount": use_from_this,
                    "expires_at": point_history.expires_at.isoformat(),
                }
            )

            remaining_to_use -= use_from_this

        return used_details, remaining_to_use

    def send_expiry_notifications(self) -> int:
        """
        만료 예정 포인트 알림 발송

        Returns:
            알림 발송 건수
        """
        from shopping.tasks import send_email_notification

        expiring_points = self.get_expiring_points_soon(days=7)

        # 사용자별로 그룹화
        user_points = {}
        for point in expiring_points:
            user_id = point.user_id
            if user_id not in user_points:
                user_points[user_id] = {"user": point.user, "points": [], "total": 0}
            remaining = self.get_remaining_points(point)
            if remaining > 0:
                user_points[user_id]["points"].append(point)
                user_points[user_id]["total"] += remaining

        notification_count = 0

        for user_data in user_points.values():
            user = user_data["user"]
            total_expiring = user_data["total"]

            if total_expiring > 0:
                # 이메일 발송
                subject = f"포인트 만료 예정 안내 - {total_expiring:,} 포인트"
                message = self._create_expiry_notification_message(user, user_data["points"], total_expiring)

                try:
                    send_email_notification(user.email, subject, message)

                    # 알림 발송 표시
                    for point in user_data["points"]:
                        point.metadata["expiry_notified"] = True
                        point.metadata["notified_at"] = timezone.now().isoformat()
                        point.save(update_fields=["metadata"])

                    notification_count += 1

                    logger.info(f"포인트 만료 알림 발송: User={user.username}, " f"points={total_expiring}")

                except Exception as e:
                    logger.error(f"알림 발송 실패: User={user.username}, " f"Error={str(e)}")
        return notification_count

    def _create_expiry_notification_message(self, user: AbstractBaseUser, points: list[PointHistory], total: int) -> str:
        """
        만료 알림 메세지 생성

        Args:
            user: 사용자
            points: 만료 예정 포인트 리스트
            total: 총 만료 예정 포인트
        Returns:
            이메일 메시지
        """
        message = f"""
안녕하세요, {user.username}님!

보유하신 포인트 중 일부가 곧 만료될 예정입니다.

[만료 예정 포인트]
총 {total:,} 포인트

[상세 내역]
"""

        for point in points:
            remaining = self.get_remaining_points(point)
            expiry_date = point.expires_at.strftime("%Y년 %m월 %d일")
            message += f"- {remaining:,}P (만료일: {expiry_date})\n"

        message += """

만료되기 전에 사용해 주세요!

감사합니다.
쇼핑몰 드림
"""

        return message
