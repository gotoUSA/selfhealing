"""
PointService 단위 테스트 - 기본 추가/차감

포인트 추가(add_points)와 차감(use_points) 메서드의
기본 동작, 유효성 검증, 로깅을 테스트합니다.
"""

import logging

import pytest

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# 포인트 추가 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceAddPoints:
    """포인트 추가 테스트"""

    def test_add_points_success(self):
        """정상적으로 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(user, 500, type="earn", description="테스트 적립")

        # Assert - 반환값 확인
        assert result is True

        # Assert - 사용자 포인트 증가
        user.refresh_from_db()
        assert user.points == initial_points + 500

        # Assert - 이력 생성 확인
        history = PointHistory.objects.filter(user=user, type="earn").latest("created_at")
        assert history.points == 500
        assert history.balance == user.points
        assert history.description == "테스트 적립"

    def test_add_points_with_order(self):
        """주문 연관 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user)

        # Act
        result = PointService.add_points(user, 100, type="earn", order=order, description="주문 적립")

        # Assert - 반환값 확인
        assert result is True

        # Assert - 주문 연관 이력 생성 확인
        history = PointHistory.objects.filter(user=user, order=order).first()
        assert history is not None
        assert history.order == order

    def test_add_points_with_metadata(self):
        """메타데이터 포함 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        metadata = {"event": "welcome_bonus", "campaign_id": 123}

        # Act
        result = PointService.add_points(user, 500, type="event", metadata=metadata)

        # Assert - 메타데이터 저장 확인
        assert result is True
        history = PointHistory.objects.filter(user=user, type="event").first()
        assert history.metadata == metadata

    @pytest.mark.parametrize(
        "invalid_amount,description",
        [
            (0, "0 포인트"),
            (-100, "음수 포인트"),
        ],
        ids=["zero", "negative"],
    )
    def test_add_points_invalid_amount(self, invalid_amount, description):
        """0 또는 음수 포인트 추가 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(user, invalid_amount)

        # Assert - 실패 반환
        assert result is False

        # Assert - 포인트 변화 없음
        user.refresh_from_db()
        assert user.points == initial_points

    def test_add_points_logging(self, caplog):
        """포인트 추가 시 로깅 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.point_service")
        user = UserFactory.with_points(1000)

        # Act
        PointService.add_points(user, 500, type="earn", description="테스트 적립")

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("포인트 추가" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("amount=500" in msg for msg in log_messages)


# ==========================================
# 포인트 차감 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceUsePoints:
    """포인트 차감 테스트"""

    def test_use_points_success(self):
        """정상적으로 포인트 차감"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, 300, type="use", description="테스트 사용")

        # Assert - 반환값 확인
        assert result is True

        # Assert - 사용자 포인트 감소
        user.refresh_from_db()
        assert user.points == initial_points - 300

        # Assert - 이력 생성 확인 (음수 포인트)
        history = PointHistory.objects.filter(user=user, type="use").latest("created_at")
        assert history.points == -300
        assert history.balance == user.points

    def test_use_points_with_order(self):
        """주문 연관 포인트 차감"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user)

        # Act
        result = PointService.use_points(user, 300, type="use", order=order, description="주문 사용")

        # Assert - 반환값 확인
        assert result is True

        # Assert - 주문 연관 이력 생성 확인
        history = PointHistory.objects.filter(user=user, order=order, type="use").first()
        assert history is not None
        assert history.order == order

    def test_use_points_insufficient_balance(self):
        """잔액 부족 시 포인트 차감 실패"""
        # Arrange
        user = UserFactory.with_points(100)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, 500)

        # Assert - 실패 반환
        assert result is False

        # Assert - 포인트 변화 없음
        user.refresh_from_db()
        assert user.points == initial_points

    @pytest.mark.parametrize(
        "invalid_amount,description",
        [
            (0, "0 포인트"),
            (-100, "음수 포인트"),
        ],
        ids=["zero", "negative"],
    )
    def test_use_points_invalid_amount(self, invalid_amount, description):
        """0 또는 음수 포인트 차감 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, invalid_amount)

        # Assert - 실패 반환
        assert result is False

        # Assert - 포인트 변화 없음
        user.refresh_from_db()
        assert user.points == initial_points

    def test_use_points_logging(self, caplog):
        """포인트 차감 시 로깅 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.point_service")
        user = UserFactory.with_points(1000)

        # Act
        PointService.use_points(user, 300, type="use", description="테스트 사용")

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("포인트 차감" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("amount=300" in msg for msg in log_messages)


# ==========================================
# 남은 포인트 계산 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceGetRemainingPoints:
    """남은 포인트 계산 테스트"""

    def test_get_remaining_points_full(self):
        """전액 남은 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()
        point_history = PointHistoryFactory.earn(user=user, points=100)

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 100

    def test_get_remaining_points_partial(self):
        """부분 사용된 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()
        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata["used_amount"] = 30
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert - 100 - 30 = 70
        assert remaining == 70

    def test_get_remaining_points_fully_used(self):
        """전액 사용된 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()
        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata["used_amount"] = 100
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 0

    def test_get_remaining_points_non_earn_type(self):
        """비적립 타입은 0 반환"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()
        point_history = PointHistoryFactory(
            user=user,
            type="use",
            points=-50,
            balance=user.points - 50,
        )

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert - use 타입은 0 반환
        assert remaining == 0

    def test_get_remaining_points_no_metadata(self):
        """메타데이터 없는 경우 전액 반환"""
        # Arrange
        user = UserFactory()
        service = PointService()
        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata = {}
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert - 메타데이터 없으면 전액
        assert remaining == 100


# ==========================================
# 포인트 이력 추적 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceHistoryTracking:
    """포인트 이력 추적 상세 테스트"""

    def test_add_points_with_default_description(self):
        """포인트 추가 시 기본 설명 생성"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        PointService.add_points(user, 500, type="earn")

        # Assert - 기본 설명 포함 확인
        history = PointHistory.objects.filter(user=user, type="earn").first()
        assert "500" in history.description
        assert "추가" in history.description
