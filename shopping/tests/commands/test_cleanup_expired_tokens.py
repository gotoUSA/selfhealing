"""cleanup_expired_tokens management command 테스트"""

from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

import pytest

from shopping.models.password_reset import PasswordResetToken
from shopping.tests.factories import UserFactory


class TestCleanupExpiredTokens:
    """cleanup_expired_tokens 커맨드 테스트"""

    @pytest.fixture
    def setup_tokens(self, db):
        """다양한 상태의 토큰 생성"""
        user = UserFactory()
        now = timezone.now()
        timeout = getattr(settings, "PASSWORD_RESET_TIMEOUT", 86400)

        # 만료된 미사용 토큰
        expired_unused = PasswordResetToken.objects.create(
            user=user,
            token_hash="expired_unused_hash_123",
            is_used=False,
        )
        expired_unused.created_at = now - timedelta(seconds=timeout + 3600)
        expired_unused.save(update_fields=["created_at"])

        # 유효한 미사용 토큰
        valid_unused = PasswordResetToken.objects.create(
            user=user,
            token_hash="valid_unused_hash_456",
            is_used=False,
        )

        # 오래전에 사용된 토큰 (30일 초과)
        old_used = PasswordResetToken.objects.create(
            user=user,
            token_hash="old_used_hash_789",
            is_used=True,
            used_at=now - timedelta(days=31),
        )
        old_used.created_at = now - timedelta(days=60)
        old_used.save(update_fields=["created_at"])

        # 최근에 사용된 토큰
        recent_used = PasswordResetToken.objects.create(
            user=user,
            token_hash="recent_used_hash_abc",
            is_used=True,
            used_at=now - timedelta(days=10),
        )

        return {
            "expired_unused": expired_unused,
            "valid_unused": valid_unused,
            "old_used": old_used,
            "recent_used": recent_used,
        }

    def test_deletes_expired_unused_tokens(self, setup_tokens):
        """만료된 미사용 토큰 삭제"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", stdout=out)

        # Assert
        assert not PasswordResetToken.objects.filter(
            pk=setup_tokens["expired_unused"].pk
        ).exists()
        assert PasswordResetToken.objects.filter(
            pk=setup_tokens["valid_unused"].pk
        ).exists()

    def test_deletes_old_used_tokens(self, setup_tokens):
        """오래전에 사용된 토큰 삭제"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", stdout=out)

        # Assert
        assert not PasswordResetToken.objects.filter(
            pk=setup_tokens["old_used"].pk
        ).exists()
        assert PasswordResetToken.objects.filter(
            pk=setup_tokens["recent_used"].pk
        ).exists()

    def test_dry_run_does_not_delete(self, setup_tokens):
        """dry-run 옵션 시 실제 삭제 안함"""
        # Arrange
        initial_count = PasswordResetToken.objects.count()
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", dry_run=True, stdout=out)

        # Assert
        assert PasswordResetToken.objects.count() == initial_count
        assert "DRY RUN" in out.getvalue()

    def test_custom_used_days(self, setup_tokens):
        """used-days 옵션으로 사용된 토큰 보관 기간 변경"""
        # Arrange
        out = StringIO()

        # Act - 60일로 설정하면 31일 된 토큰도 유지
        call_command("cleanup_expired_tokens", used_days=60, stdout=out)

        # Assert - old_used(31일)는 삭제 안됨
        assert PasswordResetToken.objects.filter(
            pk=setup_tokens["old_used"].pk
        ).exists()

    def test_no_tokens_to_delete(self, db):
        """삭제할 토큰이 없는 경우"""
        # Arrange
        user = UserFactory()
        PasswordResetToken.objects.create(
            user=user,
            token_hash="valid_token_hash",
            is_used=False,
        )
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", stdout=out)

        # Assert
        assert "삭제할 토큰이 없습니다" in out.getvalue()

    def test_output_contains_statistics(self, setup_tokens):
        """출력에 통계 정보 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", stdout=out)
        output = out.getvalue()

        # Assert
        assert "만료" in output or "토큰" in output
        assert "삭제" in output

    def test_empty_database(self, db):
        """토큰이 전혀 없는 경우"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_expired_tokens", stdout=out)

        # Assert
        assert "0개" in out.getvalue() or "삭제할 토큰이 없습니다" in out.getvalue()
