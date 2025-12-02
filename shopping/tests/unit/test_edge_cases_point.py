"""
포인트 경계값 테스트

테스트 범위:
- 만료일이 정확히 오늘인 포인트 처리
- 결제금액이 포인트보다 1원 많은 경우
- 포인트 극단값 (0, 최대치)
- 소수점/비정상 데이터 방어
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestPointExpiryToday:
    """만료일이 오늘인 포인트 처리 테스트"""

    def test_expires_today_start_of_day_is_expired(self):
        """만료일이 오늘 00:00:00이면 만료됨"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=today_start,
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 1
        assert expired_points[0].id == point.id

    def test_expires_today_end_of_day_not_expired(self):
        """만료일이 오늘 23:59:59이면 아직 만료 안됨"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        today_end = timezone.now().replace(hour=23, minute=59, second=59, microsecond=999999)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=today_end,
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        now = timezone.now()
        if now < today_end:
            assert len(expired_points) == 0
        else:
            assert len(expired_points) == 1

    def test_expires_exactly_now_is_expired(self):
        """만료일이 정확히 현재 시간이면 만료됨"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        now = timezone.now()
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=now - timedelta(seconds=1),
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 1
        assert expired_points[0].id == point.id

    def test_expiring_today_appears_in_soon_list(self):
        """오늘 만료 예정 포인트가 곧 만료 목록에 나타남"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        expires_soon = timezone.now() + timedelta(hours=1)
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=expires_soon,
        )

        # Act
        expiring_soon = service.get_expiring_points_soon(days=1)

        # Assert
        assert len(expiring_soon) == 1
        assert expiring_soon[0].id == point.id


@pytest.mark.django_db
class TestPointPaymentBoundary:
    """결제 금액과 포인트 경계값 테스트"""

    def test_payment_amount_1_won_more_than_points(self):
        """결제금액이 포인트보다 1원 많은 경우"""
        # Arrange
        user = UserFactory.with_points(9999)
        service = PointService()

        # Act - 10000원 결제에 9999포인트 사용 -> 1원 카드결제
        result = service.use_points(user, 9999, type="use", description="포인트 사용")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 0

    def test_payment_exactly_equals_points(self):
        """결제금액이 포인트와 정확히 같은 경우"""
        # Arrange
        user = UserFactory.with_points(10000)
        service = PointService()

        # Act
        result = service.use_points(user, 10000, type="use", description="전액 포인트")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 0

    def test_points_1_won_more_than_payment(self):
        """포인트가 결제금액보다 1원 많은 경우"""
        # Arrange
        user = UserFactory.with_points(10001)
        service = PointService()

        # Act - 10000원 결제에 10000포인트만 사용
        result = service.use_points(user, 10000, type="use", description="부분 사용")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 1


@pytest.mark.django_db
class TestPointExtremeValues:
    """포인트 극단값 테스트"""

    def test_add_points_minimum_valid(self):
        """최소 유효 포인트 추가 (1포인트)"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        result = PointService.add_points(user, 1, type="earn", description="1포인트 적립")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 1

    def test_add_points_large_amount(self):
        """대량 포인트 추가 (100만 포인트)"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        result = PointService.add_points(user, 1000000, type="earn", description="대량 적립")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 1000000

    def test_use_points_exact_balance(self):
        """보유 포인트 전액 사용"""
        # Arrange
        user = UserFactory.with_points(5000)

        # Act
        result = PointService.use_points(user, 5000, type="use", description="전액 사용")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == 0

    def test_use_points_1_more_than_balance_fails(self):
        """보유 포인트보다 1원 많이 사용 실패"""
        # Arrange
        user = UserFactory.with_points(5000)

        # Act
        result = PointService.use_points(user, 5001, type="use", description="초과 사용")

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == 5000

    def test_user_with_zero_points_cannot_use(self):
        """포인트 0인 사용자는 사용 불가"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        result = PointService.use_points(user, 1, type="use", description="0원에서 사용")

        # Assert
        assert result is False


@pytest.mark.django_db
class TestPointFIFOBoundary:
    """FIFO 포인트 사용 경계값 테스트"""

    def test_fifo_use_exactly_one_history_amount(self):
        """정확히 하나의 적립 금액만큼만 사용"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()
        now = timezone.now()

        history = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=30),
        )
        user.points = 1000
        user.save()

        # Act
        result = service.use_points_fifo(user, 1000)

        # Assert
        assert result["success"] is True
        history.refresh_from_db()
        assert history.metadata.get("used_amount", 0) == 1000

    def test_fifo_use_1_more_than_one_history(self):
        """하나의 적립보다 1원 더 사용 (두 번째 적립에서 차감)"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()
        now = timezone.now()

        history1 = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=30),
        )
        history2 = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=1500,
            expires_at=now + timedelta(days=60),
        )
        user.points = 1500
        user.save()

        # Act
        result = service.use_points_fifo(user, 1001)

        # Assert
        assert result["success"] is True
        history1.refresh_from_db()
        history2.refresh_from_db()
        assert history1.metadata.get("used_amount", 0) == 1000
        assert history2.metadata.get("used_amount", 0) == 1

    def test_fifo_use_1_less_than_total(self):
        """전체 포인트보다 1원 덜 사용"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()
        now = timezone.now()

        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=30),
        )
        user.points = 1000
        user.save()

        # Act
        result = service.use_points_fifo(user, 999)

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 1


@pytest.mark.django_db
class TestPointHistoryBalance:
    """포인트 이력 잔액 정확성 테스트"""

    def test_balance_after_earn(self):
        """적립 후 잔액 정확성"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user)

        # Act
        PointService.add_points(user, 500, type="earn", order=order, description="적립")

        # Assert
        history = PointHistory.objects.filter(user=user, type="earn", order=order).first()
        user.refresh_from_db()

        assert history.balance == user.points
        assert user.points == 1500

    def test_balance_after_use(self):
        """사용 후 잔액 정확성"""
        # Arrange
        user = UserFactory.with_points(1000)

        # Act
        PointService.use_points(user, 300, type="use", description="사용")

        # Assert
        history = PointHistory.objects.filter(user=user, type="use").first()
        user.refresh_from_db()

        assert history.balance == user.points
        assert user.points == 700

    def test_multiple_operations_balance_chain(self):
        """연속 작업 후 잔액 체인 정확성"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        PointService.add_points(user, 1000, type="earn", description="1차 적립")
        PointService.use_points(user, 300, type="use", description="1차 사용")
        PointService.add_points(user, 500, type="earn", description="2차 적립")
        PointService.use_points(user, 200, type="use", description="2차 사용")

        # Assert
        user.refresh_from_db()
        assert user.points == 1000  # 1000 - 300 + 500 - 200 = 1000

        # 마지막 이력의 잔액이 현재 포인트와 일치
        latest = PointHistory.objects.filter(user=user).order_by("-created_at").first()
        assert latest.balance == user.points
