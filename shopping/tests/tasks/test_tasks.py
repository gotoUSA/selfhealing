"""
point_tasks.py 테스트

shopping/tasks/point_tasks.py의 Celery 비동기 작업 테스트:
- process_single_user_points: 특정 사용자 포인트 만료 처리
- cleanup_old_point_histories: 오래된 포인트 이력 정리
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.tasks import cleanup_old_point_histories, process_single_user_points
from shopping.tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
class TestProcessSingleUserPoints:
    """특정 사용자 포인트 만료 처리"""

    def test_success_returns_status_and_username(self, mocker):
        """정상 처리 시 성공 상태와 사용자명 반환"""
        # Arrange
        user = UserFactory()

        mock_service = mocker.MagicMock()
        mock_service.get_expired_points.return_value = []

        mocker.patch(
            "shopping.services.point_service.PointService",
            return_value=mock_service,
        )

        # Act
        result = process_single_user_points(user_id=user.id)

        # Assert
        assert result["status"] == "success"
        assert result["user"] == user.username
        assert result["expired_count"] == 0

    def test_expired_points_count_returned(self, mocker):
        """만료된 포인트 개수 반환"""
        # Arrange
        user = UserFactory()
        mock_expired_point = mocker.MagicMock()
        mock_expired_point.user_id = user.id

        mock_service = mocker.MagicMock()
        mock_service.get_expired_points.return_value = [
            mock_expired_point,
            mock_expired_point,
        ]

        mocker.patch(
            "shopping.services.point_service.PointService",
            return_value=mock_service,
        )

        # Act
        result = process_single_user_points(user_id=user.id)

        # Assert
        assert result["expired_count"] == 2

    def test_nonexistent_user_returns_error(self):
        """존재하지 않는 사용자 ID로 호출 시 에러 반환"""
        # Arrange
        non_existent_user_id = 99999

        # Act
        result = process_single_user_points(user_id=non_existent_user_id)

        # Assert
        assert result["status"] == "error"
        assert f"User {non_existent_user_id} not found" in result["message"]

    def test_service_exception_returns_error(self, mocker):
        """서비스 예외 발생 시 에러 반환"""
        # Arrange
        user = UserFactory()

        mock_service = mocker.MagicMock()
        mock_service.get_expired_points.side_effect = Exception("DB connection error")

        mocker.patch(
            "shopping.services.point_service.PointService",
            return_value=mock_service,
        )

        # Act
        result = process_single_user_points(user_id=user.id)

        # Assert
        assert result["status"] == "error"
        assert "DB connection error" in result["message"]


@pytest.mark.django_db(transaction=True)
class TestCleanupOldPointHistories:
    """오래된 포인트 이력 정리"""

    def test_deletes_old_expire_histories(self):
        """오래된 만료 이력 삭제"""
        # Arrange
        user = UserFactory()

        old_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="테스트 만료",
        )
        PointHistory.objects.filter(id=old_history.id).update(created_at=timezone.now() - timedelta(days=800))

        recent_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-50,
            balance=50,
            description="최근 만료",
        )
        PointHistory.objects.filter(id=recent_history.id).update(created_at=timezone.now() - timedelta(days=365))

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert not PointHistory.objects.filter(id=old_history.id).exists()
        assert PointHistory.objects.filter(id=recent_history.id).exists()

    def test_only_deletes_expire_type(self):
        """만료 타입만 삭제 (적립은 유지)"""
        # Arrange
        user = UserFactory()
        old_date = timezone.now() - timedelta(days=800)

        earn_history = PointHistory.objects.create(
            user=user,
            type="earn",
            points=100,
            balance=100,
            description="적립",
        )
        PointHistory.objects.filter(id=earn_history.id).update(created_at=old_date)

        expire_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="만료",
        )
        PointHistory.objects.filter(id=expire_history.id).update(created_at=old_date)

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["deleted_count"] == 1
        assert PointHistory.objects.filter(id=earn_history.id).exists()
        assert not PointHistory.objects.filter(id=expire_history.id).exists()

    def test_custom_days_parameter(self):
        """커스텀 보관 기간으로 삭제"""
        # Arrange
        user = UserFactory()

        history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="만료",
        )
        PointHistory.objects.filter(id=history.id).update(created_at=timezone.now() - timedelta(days=100))

        # Act
        result = cleanup_old_point_histories(days=90)

        # Assert
        assert result["deleted_count"] == 1

    def test_no_histories_returns_zero_count(self):
        """삭제할 이력이 없으면 deleted_count 0"""
        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 0

    def test_boundary_cutoff_keeps_recent(self):
        """cutoff 날짜 직후 이력은 유지"""
        # Arrange
        user = UserFactory()

        boundary_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="경계 케이스",
        )
        PointHistory.objects.filter(id=boundary_history.id).update(created_at=timezone.now() - timedelta(days=729))

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert PointHistory.objects.filter(id=boundary_history.id).exists()

    def test_boundary_cutoff_deletes_old(self):
        """cutoff 날짜 직전 이력은 삭제"""
        # Arrange
        user = UserFactory()

        old_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="하루 전",
        )
        PointHistory.objects.filter(id=old_history.id).update(created_at=timezone.now() - timedelta(days=731))

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["deleted_count"] == 1
        assert not PointHistory.objects.filter(id=old_history.id).exists()

    def test_database_error_returns_error(self, mocker):
        """DB 오류 발생 시 에러 반환"""
        # Arrange
        mocker.patch(
            "shopping.models.point.PointHistory.objects.filter",
            side_effect=Exception("Database error"),
        )

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "error"
        assert "Database error" in result["message"]
