"""
point_tasks.py 추가 테스트

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
class TestProcessSingleUserPointsHappyPath:
    """특정 사용자 포인트 만료 처리 정상 케이스"""

    def test_processes_user_points_successfully(self, mocker):
        """사용자 포인트 처리가 성공적으로 완료됨"""
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

    def test_processes_expired_points_for_user(self, mocker):
        """만료된 포인트가 있는 사용자 처리"""
        # Arrange
        user = UserFactory()

        # 만료된 포인트 mock 객체 생성
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
        assert result["status"] == "success"
        assert result["expired_count"] == 2


@pytest.mark.django_db(transaction=True)
class TestProcessSingleUserPointsException:
    """특정 사용자 포인트 만료 처리 예외 케이스"""

    def test_returns_error_when_user_not_found(self):
        """존재하지 않는 사용자 ID로 호출 시 에러 반환"""
        # Arrange
        non_existent_user_id = 99999

        # Act
        result = process_single_user_points(user_id=non_existent_user_id)

        # Assert
        assert result["status"] == "error"
        assert f"User {non_existent_user_id} not found" in result["message"]

    def test_returns_error_on_service_exception(self, mocker):
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
class TestCleanupOldPointHistoriesHappyPath:
    """오래된 포인트 이력 정리 정상 케이스"""

    def test_deletes_old_expired_histories(self):
        """오래된 만료 이력이 삭제됨"""
        # Arrange
        user = UserFactory()

        # 3년 전 만료 이력 (삭제 대상)
        old_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="테스트 만료",
        )
        old_history.created_at = timezone.now() - timedelta(days=800)
        old_history.save()

        # 1년 전 만료 이력 (유지 대상)
        recent_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-50,
            balance=50,
            description="최근 만료",
        )
        recent_history.created_at = timezone.now() - timedelta(days=365)
        recent_history.save()

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert not PointHistory.objects.filter(id=old_history.id).exists()
        assert PointHistory.objects.filter(id=recent_history.id).exists()

    def test_only_deletes_expire_type_histories(self):
        """만료 타입 이력만 삭제됨"""
        # Arrange
        user = UserFactory()

        # 3년 전 적립 이력 (유지 대상 - expire 타입 아님)
        earn_history = PointHistory.objects.create(
            user=user,
            type="earn",
            points=100,
            balance=100,
            description="적립",
        )
        earn_history.created_at = timezone.now() - timedelta(days=800)
        earn_history.save()

        # 3년 전 만료 이력 (삭제 대상)
        expire_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="만료",
        )
        expire_history.created_at = timezone.now() - timedelta(days=800)
        expire_history.save()

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert PointHistory.objects.filter(id=earn_history.id).exists()
        assert not PointHistory.objects.filter(id=expire_history.id).exists()

    def test_custom_days_parameter(self):
        """커스텀 보관 기간으로 삭제"""
        # Arrange
        user = UserFactory()

        # 100일 전 만료 이력
        history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="만료",
        )
        history.created_at = timezone.now() - timedelta(days=100)
        history.save()

        # Act - 90일 이전 삭제
        result = cleanup_old_point_histories(days=90)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 1

    def test_no_histories_to_delete(self):
        """삭제할 이력이 없는 경우"""
        # Arrange - 아무 데이터도 생성하지 않음

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 0


@pytest.mark.django_db(transaction=True)
class TestCleanupOldPointHistoriesException:
    """오래된 포인트 이력 정리 예외 케이스"""

    def test_returns_error_on_delete_exception(self, mocker):
        """삭제 중 예외 발생 시 에러 반환"""
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


@pytest.mark.django_db(transaction=True)
class TestCleanupOldPointHistoriesBoundary:
    """오래된 포인트 이력 정리 경계 케이스"""

    def test_exactly_at_cutoff_date(self):
        """cutoff 날짜 직후에 생성된 이력 (유지됨)"""
        # Arrange
        user = UserFactory()

        # 729일 전 생성 (유지 대상 - cutoff_date 이후)
        # Note: timedelta(days=730)은 현재 시각 기준이므로 729일로 여유 확보
        boundary_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="경계 케이스",
        )
        boundary_history.created_at = timezone.now() - timedelta(days=729)
        boundary_history.save()

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        # 730일보다 최근이므로 유지됨
        assert result["status"] == "success"
        assert PointHistory.objects.filter(id=boundary_history.id).exists()

    def test_one_day_before_cutoff(self):
        """cutoff 하루 전 생성된 이력 (삭제됨)"""
        # Arrange
        user = UserFactory()

        # 731일 전 생성 (삭제 대상)
        old_history = PointHistory.objects.create(
            user=user,
            type="expire",
            points=-100,
            balance=0,
            description="하루 전",
        )
        old_history.created_at = timezone.now() - timedelta(days=731)
        old_history.save()

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert not PointHistory.objects.filter(id=old_history.id).exists()
