"""Admin 수동 포인트 적립 테스트

강제 취소/수동 적립 시 상태, 재고, 포인트가 정확히 변경되는지 확인
"""

import pytest

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import UserFactory


@pytest.mark.django_db
class TestAdminPointManualCredit:
    """Admin 수동 포인트 적립 테스트"""

    def test_manual_point_credit_updates_user_points(self):
        """수동 포인트 적립 시 사용자 포인트 정확히 증가"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        initial_points = user.points
        credit_amount = 5000

        # Act - 관리자 수동 적립 (PointService.add_points 사용)
        result = PointService.add_points(
            user=user,
            amount=credit_amount,
            type="admin_credit",
            description="관리자 수동 적립 - 이벤트 당첨",
            metadata={"admin_id": 1, "reason": "이벤트 당첨"},
        )

        # Assert
        user.refresh_from_db()
        assert result is True
        assert user.points == initial_points + credit_amount

    def test_manual_point_credit_creates_history(self):
        """수동 포인트 적립 시 이력 정확히 생성"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=1000)
        credit_amount = 3000
        description = "고객 불편 보상"

        # Act
        PointService.add_points(
            user=user,
            amount=credit_amount,
            type="admin_credit",
            description=description,
            metadata={"admin_id": 1, "reason": "CS 보상"},
        )

        # Assert
        history = PointHistory.objects.filter(user=user, type="admin_credit").first()
        assert history is not None
        assert history.points == credit_amount
        assert history.balance == 4000  # 1000 + 3000
        assert history.description == description

    def test_manual_point_credit_with_zero_initial_balance(self):
        """포인트 0인 사용자에게 수동 적립"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)
        credit_amount = 10000

        # Act
        result = PointService.add_points(
            user=user,
            amount=credit_amount,
            type="admin_credit",
            description="신규 가입 보너스",
        )

        # Assert
        user.refresh_from_db()
        assert result is True
        assert user.points == credit_amount

        history = PointHistory.objects.filter(user=user, type="admin_credit").first()
        assert history.balance == credit_amount

    def test_manual_point_credit_invalid_amount_fails(self):
        """잘못된 금액으로 수동 적립 시 실패"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(
            user=user,
            amount=0,  # 잘못된 금액
            type="admin_credit",
            description="잘못된 적립",
        )

        # Assert
        user.refresh_from_db()
        assert result is False
        assert user.points == initial_points  # 변화 없음

    def test_multiple_manual_credits_accumulate(self):
        """연속 수동 적립 시 누적"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0)

        # Act - 3회 적립
        PointService.add_points(user=user, amount=1000, type="admin_credit", description="1차 적립")
        PointService.add_points(user=user, amount=2000, type="admin_credit", description="2차 적립")
        PointService.add_points(user=user, amount=3000, type="admin_credit", description="3차 적립")

        # Assert
        user.refresh_from_db()
        assert user.points == 6000

        histories = PointHistory.objects.filter(user=user, type="admin_credit").order_by("created_at")
        assert histories.count() == 3

        # balance 누적 확인
        balances = list(histories.values_list("balance", flat=True))
        assert balances == [1000, 3000, 6000]
