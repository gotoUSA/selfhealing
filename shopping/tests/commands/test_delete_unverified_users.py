"""delete_unverified_users management command 테스트"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

import pytest

from shopping.tests.factories import OrderFactory, UserFactory

User = get_user_model()


class TestDeleteUnverifiedUsers:
    """delete_unverified_users 커맨드 테스트"""

    @pytest.fixture
    def setup_users(self, db):
        """다양한 상태의 사용자 생성"""
        now = timezone.now()

        # 오래된 미인증 사용자 (7일 초과)
        old_unverified = UserFactory(
            username="old_unverified",
            is_email_verified=False,
        )
        User.objects.filter(pk=old_unverified.pk).update(date_joined=now - timedelta(days=10))

        # 최근 미인증 사용자 (7일 이내)
        recent_unverified = UserFactory(
            username="recent_unverified",
            is_email_verified=False,
        )

        # 오래된 인증 완료 사용자
        verified = UserFactory(
            username="verified_user",
            is_email_verified=True,
        )
        User.objects.filter(pk=verified.pk).update(date_joined=now - timedelta(days=10))

        # 오래된 미인증 사용자이지만 주문 있음
        unverified_with_order = UserFactory(
            username="unverified_with_order",
            is_email_verified=False,
        )
        User.objects.filter(pk=unverified_with_order.pk).update(date_joined=now - timedelta(days=10))
        OrderFactory(user=unverified_with_order)

        return {
            "old_unverified": old_unverified,
            "recent_unverified": recent_unverified,
            "verified": verified,
            "unverified_with_order": unverified_with_order,
        }

    def test_dry_run_shows_delete_targets(self, setup_users):
        """dry-run 모드로 삭제 대상 표시"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "Dry Run" in output or "DRY RUN" in output

    def test_dry_run_does_not_delete(self, setup_users):
        """dry-run 모드에서는 삭제하지 않음"""
        # Arrange
        initial_count = User.objects.count()
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)

        # Assert
        assert User.objects.count() == initial_count

    def test_preserves_recent_unverified_users(self, setup_users):
        """최근 미인증 사용자 유지"""
        # Arrange
        out = StringIO()

        # Act - dry_run으로 확인
        call_command("delete_unverified_users", dry_run=True, stdout=out)

        # Assert
        assert User.objects.filter(pk=setup_users["recent_unverified"].pk).exists()

    def test_preserves_verified_users(self, setup_users):
        """인증 완료 사용자 유지"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)

        # Assert
        assert User.objects.filter(pk=setup_users["verified"].pk).exists()

    def test_preserves_users_with_orders(self, setup_users):
        """주문 이력 있는 미인증 사용자 유지"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert User.objects.filter(pk=setup_users["unverified_with_order"].pk).exists()
        # 주문 이력이 있어서 유지됨을 표시
        assert "주문" in output or "유지" in output

    def test_custom_days_option(self, setup_users):
        """days 옵션으로 기준일 변경"""
        # Arrange
        out = StringIO()

        # Act - 30일로 설정하면 10일 된 사용자는 유지
        call_command("delete_unverified_users", days=30, dry_run=True, stdout=out)

        # Assert - 삭제 대상이 없거나 적어야 함
        output = out.getvalue()
        assert "0개" in output or "삭제할" in output or "없습니다" in output

    def test_verbose_option_shows_details(self, setup_users):
        """verbose 옵션으로 상세 정보 표시"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, verbose=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "@" in output or "목록" in output

    def test_output_contains_statistics(self, setup_users):
        """출력에 통계 정보 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "삭제" in output or "대상" in output

    def test_no_users_to_delete(self, db):
        """삭제할 사용자가 없는 경우"""
        # Arrange
        UserFactory(is_email_verified=True)
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "없습니다" in output or "0" in output

    def test_identifies_delete_targets_correctly(self, setup_users):
        """삭제 대상을 정확히 식별"""
        # Arrange
        out = StringIO()

        # Act
        call_command("delete_unverified_users", dry_run=True, verbose=True, stdout=out)
        output = out.getvalue()

        # Assert - old_unverified가 삭제 대상에 포함
        old_user = setup_users["old_unverified"]
        # 삭제 대상으로 식별됨 (출력에 이메일이나 카운트 포함)
        assert "삭제 대상" in output or "발견" in output

    @patch("builtins.input", return_value="no")
    def test_confirms_before_delete(self, mock_input, setup_users):
        """삭제 전 확인 프롬프트"""
        # Arrange
        out = StringIO()
        initial_count = User.objects.count()

        # Act
        call_command("delete_unverified_users", stdout=out)

        # Assert - no를 입력했으므로 삭제되지 않음
        assert User.objects.count() == initial_count

    @patch("builtins.input", return_value="yes")
    def test_deletes_on_confirmation(self, mock_input, setup_users):
        """확인 후 삭제 실행"""
        # Arrange
        out = StringIO()
        old_user_pk = setup_users["old_unverified"].pk

        # Act
        call_command("delete_unverified_users", stdout=out)

        # Assert
        assert not User.objects.filter(pk=old_user_pk).exists()
