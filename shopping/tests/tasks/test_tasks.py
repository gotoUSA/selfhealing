"""
point_tasks.py 테스트

shopping/tasks/point_tasks.py의 Celery 비동기 작업 테스트:
- process_single_user_points: 특정 사용자 포인트 만료 처리
- cleanup_old_point_histories: 오래된 포인트 이력 정리
"""

import pytest

from shopping.models.point import PointHistory
from shopping.tasks import cleanup_old_point_histories, process_single_user_points
from shopping.tests.factories import PointHistoryFactory, UserFactory


# ==========================================
# 특정 사용자 포인트 만료 처리 테스트
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestProcessSingleUserPoints:
    """특정 사용자 포인트 만료 처리"""

    def test_success_returns_status_and_username(self):
        """정상 처리 시 성공 상태와 사용자명 반환"""
        # Arrange
        user = UserFactory()

        # Act
        result = process_single_user_points(user_id=user.id)

        # Assert - 성공 상태와 사용자명 반환
        assert result["status"] == "success"
        assert result["user"] == user.username

    def test_nonexistent_user_returns_error(self):
        """존재하지 않는 사용자 ID로 호출 시 에러 반환"""
        # Arrange
        non_existent_user_id = 99999

        # Act
        result = process_single_user_points(user_id=non_existent_user_id)

        # Assert - 에러 상태와 메시지 반환
        assert result["status"] == "error"
        assert f"User {non_existent_user_id} not found" in result["message"]


# ==========================================
# 오래된 포인트 이력 정리 테스트
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestCleanupOldPointHistories:
    """오래된 포인트 이력 정리"""

    def test_deletes_old_expire_histories(self):
        """오래된 만료 이력 삭제"""
        # Arrange - Factory 사용
        user = UserFactory()
        old_history = PointHistoryFactory.old_expire(user=user, days_ago=800)
        recent_history = PointHistoryFactory.recent_expire(user=user, days_ago=365)

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert - 태스크 결과 확인
        assert result["status"] == "success"
        assert result["deleted_count"] == 1

        # Assert - 800일 전 이력만 삭제됨
        assert not PointHistory.objects.filter(id=old_history.id).exists()

        # Assert - 365일 전 이력은 유지됨
        assert PointHistory.objects.filter(id=recent_history.id).exists()

    def test_only_deletes_expire_type(self):
        """만료 타입만 삭제 (적립 이력은 유지)"""
        # Arrange - Factory 사용
        user = UserFactory()
        earn_history = PointHistoryFactory.old_earn(user=user, days_ago=800)
        expire_history = PointHistoryFactory.old_expire(user=user, days_ago=800)

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert - expire 타입만 삭제됨
        assert result["deleted_count"] == 1
        assert PointHistory.objects.filter(id=earn_history.id).exists()
        assert not PointHistory.objects.filter(id=expire_history.id).exists()

    def test_custom_days_parameter(self):
        """커스텀 보관 기간으로 삭제"""
        # Arrange - Factory 사용
        user = UserFactory()
        history = PointHistoryFactory.old_expire(user=user, days_ago=100)

        # Act - 90일 기준으로 삭제
        result = cleanup_old_point_histories(days=90)

        # Assert - 100일 전 이력 삭제됨
        assert result["deleted_count"] == 1

    def test_no_histories_returns_zero_count(self):
        """삭제할 이력이 없으면 deleted_count 0"""
        # Arrange
        # 삭제할 이력이 없는 상태

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert
        assert result["status"] == "success"
        assert result["deleted_count"] == 0

    def test_boundary_cutoff_keeps_recent(self):
        """cutoff 날짜 직후 이력은 유지 (경계값 테스트)"""
        # Arrange - 729일 전 만료 이력 (730일 미만 → 유지)
        user = UserFactory()
        boundary_history = PointHistoryFactory.old_expire(user=user, days_ago=729)

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert - 경계값 직후이므로 유지됨
        assert PointHistory.objects.filter(id=boundary_history.id).exists()

    def test_boundary_cutoff_deletes_old(self):
        """cutoff 날짜 직전 이력은 삭제 (경계값 테스트)"""
        # Arrange - 731일 전 만료 이력 (730일 초과 → 삭제)
        user = UserFactory()
        old_history = PointHistoryFactory.old_expire(user=user, days_ago=731)

        # Act
        result = cleanup_old_point_histories(days=730)

        # Assert - 경계값 직전이므로 삭제됨
        assert result["deleted_count"] == 1
        assert not PointHistory.objects.filter(id=old_history.id).exists()
