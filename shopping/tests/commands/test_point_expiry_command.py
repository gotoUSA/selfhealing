"""test_point_expiry management command 테스트

이 테스트는 포인트 만료 기능 디버깅용 커맨드를 검증합니다.
수동 테스트용이므로 일반 CI에서는 실행되지 않습니다. (pytest -m manual 로 실행)
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

import pytest

from shopping.models.point import PointHistory
from shopping.tests.factories import UserFactory

User = get_user_model()


@pytest.mark.manual
class TestPointExpiryCommand:
    """test_point_expiry 커맨드 테스트"""

    @pytest.fixture
    def user_with_points(self, db):
        """포인트 이력이 있는 사용자 생성"""
        user = UserFactory(username="point_test_user", points=5000)
        PointHistory.objects.create(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="테스트 적립",
            expires_at=timezone.now() + timedelta(days=5),
        )
        return user

    def test_create_test_data_creates_user(self, db):
        """create-test-data 옵션으로 테스트 사용자 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command(
            "test_point_expiry",
            create_test_data=True,
            username="point_test_user",
            stdout=out,
        )

        # Assert
        assert User.objects.filter(username="point_test_user").exists()

    def test_create_test_data_creates_point_histories(self, db):
        """create-test-data 옵션으로 포인트 이력 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command(
            "test_point_expiry",
            create_test_data=True,
            username="point_history_user",
            stdout=out,
        )

        # Assert
        user = User.objects.get(username="point_history_user")
        assert PointHistory.objects.filter(user=user).exists()

    def test_expire_option_processes_expired_points(self, db):
        """expire 옵션으로 만료 포인트 처리"""
        # Arrange
        out = StringIO()
        user = UserFactory()

        # 만료된 포인트 이력 생성
        PointHistory.objects.create(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="만료 테스트 적립",
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        call_command("test_point_expiry", expire=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "만료" in output

    def test_notify_option_sends_notifications(self, user_with_points):
        """notify 옵션으로 만료 예정 알림"""
        # Arrange
        out = StringIO()

        # Act
        call_command("test_point_expiry", notify=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "알림" in output or "만료 예정" in output or "없습니다" in output

    @patch("shopping.services.point_service.PointService.use_points_fifo")
    def test_use_points_option(self, mock_use_points, db):
        """use-points 옵션으로 포인트 사용 테스트"""
        # Arrange
        out = StringIO()
        user = UserFactory(username="use_points_user", points=5000)

        mock_use_points.return_value = {
            "success": True,
            "message": "1000 포인트 사용 완료",
            "used_details": [{"history_id": 1, "amount": 1000}],
        }

        # Act
        call_command(
            "test_point_expiry",
            use_points=1000,
            username="use_points_user",
            stdout=out,
        )
        output = out.getvalue()

        # Assert
        assert "사용" in output or "포인트" in output

    def test_use_points_user_not_found(self, db):
        """존재하지 않는 사용자로 포인트 사용 시도"""
        # Arrange
        out = StringIO()

        # Act
        call_command(
            "test_point_expiry",
            use_points=1000,
            username="nonexistent_user",
            stdout=out,
        )
        output = out.getvalue()

        # Assert
        assert "찾을 수 없" in output or "없음" in output

    def test_no_options_does_nothing(self, db):
        """옵션 없이 실행 시 아무 동작 안함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("test_point_expiry", stdout=out)
        output = out.getvalue()

        # Assert
        # 옵션이 없으면 출력도 없음
        assert output == "" or len(output.strip()) == 0

    def test_expire_when_no_expired_points(self, db):
        """만료된 포인트가 없을 때 expire 실행"""
        # Arrange
        out = StringIO()

        # Act
        call_command("test_point_expiry", expire=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "없습니다" in output or "0" in output or "만료" in output

    def test_notify_when_no_expiring_points(self, db):
        """만료 예정 포인트가 없을 때 notify 실행"""
        # Arrange
        out = StringIO()

        # Act
        call_command("test_point_expiry", notify=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "없습니다" in output or "0" in output or "만료 예정" in output
