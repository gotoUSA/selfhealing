"""create_load_test_users management command 테스트"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command

import pytest

User = get_user_model()


class TestCreateLoadTestUsers:
    """create_load_test_users 커맨드 테스트"""

    def test_creates_default_count_users(self, db):
        """기본 100명의 사용자 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", stdout=out)

        # Assert
        created_count = User.objects.filter(
            username__startswith="load_test_user_"
        ).count()
        assert created_count == 100

    def test_creates_custom_count_users(self, db):
        """count 옵션으로 사용자 수 지정"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=10, stdout=out)

        # Assert
        created_count = User.objects.filter(
            username__startswith="load_test_user_"
        ).count()
        assert created_count == 10

    def test_creates_users_with_points(self, db):
        """사용자에게 포인트 지급"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=5, points=10000, stdout=out)

        # Assert
        user = User.objects.get(username="load_test_user_0")
        assert user.points == 10000

    def test_creates_users_with_default_points(self, db):
        """기본 50000 포인트 지급"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, stdout=out)

        # Assert
        user = User.objects.get(username="load_test_user_0")
        assert user.points == 50000

    def test_clear_option_removes_existing_users(self, db):
        """clear 옵션으로 기존 사용자 삭제 후 재생성"""
        # Arrange
        out = StringIO()
        call_command("create_load_test_users", count=5, stdout=out)
        initial_ids = set(
            User.objects.filter(
                username__startswith="load_test_user_"
            ).values_list("id", flat=True)
        )

        # Act
        call_command("create_load_test_users", count=3, clear=True, stdout=out)

        # Assert
        new_count = User.objects.filter(
            username__startswith="load_test_user_"
        ).count()
        assert new_count == 3

    def test_skips_existing_users(self, db):
        """이미 존재하는 사용자는 건너뜀"""
        # Arrange
        out = StringIO()
        call_command("create_load_test_users", count=5, stdout=out)

        # Act
        call_command("create_load_test_users", count=10, stdout=out)
        output = out.getvalue()

        # Assert
        total_count = User.objects.filter(
            username__startswith="load_test_user_"
        ).count()
        assert total_count == 10
        assert "이미 존재" in output or "건너뜀" in output

    def test_users_have_email_verified(self, db):
        """생성된 사용자는 이메일 인증 완료 상태"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, stdout=out)

        # Assert
        users = User.objects.filter(username__startswith="load_test_user_")
        for user in users:
            assert user.is_email_verified is True

    def test_users_have_correct_password(self, db):
        """생성된 사용자의 비밀번호 확인"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=1, stdout=out)

        # Assert
        user = User.objects.get(username="load_test_user_0")
        assert user.check_password("testpass123")

    def test_users_have_correct_email_format(self, db):
        """생성된 사용자의 이메일 형식 확인"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, stdout=out)

        # Assert
        user = User.objects.get(username="load_test_user_0")
        assert user.email == "load_test_0@example.com"

    def test_output_contains_instructions(self, db):
        """출력에 사용 방법 안내 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, stdout=out)
        output = out.getvalue()

        # Assert
        assert "locust" in output.lower() or "부하 테스트" in output

    def test_zero_points_creates_no_point_record(self, db):
        """포인트 0인 경우 포인트 레코드 생성 안함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, points=0, stdout=out)

        # Assert
        user = User.objects.get(username="load_test_user_0")
        # 포인트가 0이면 포인트 관련 레코드 생성 안함
        assert user.points == 0

    def test_username_starts_from_zero(self, db):
        """사용자명이 0부터 시작"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_load_test_users", count=3, stdout=out)

        # Assert
        assert User.objects.filter(username="load_test_user_0").exists()
        assert User.objects.filter(username="load_test_user_1").exists()
        assert User.objects.filter(username="load_test_user_2").exists()
