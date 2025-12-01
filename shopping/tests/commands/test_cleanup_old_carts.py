"""cleanup_old_carts management command 테스트"""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

import pytest

from shopping.models.cart import Cart
from shopping.tests.factories import UserFactory


class TestCleanupOldCarts:
    """cleanup_old_carts 커맨드 테스트"""

    @pytest.fixture
    def setup_carts(self, db):
        """다양한 상태의 장바구니 생성"""
        user = UserFactory()
        now = timezone.now()

        # 비회원 활성 장바구니 - 오래된 것 (7일 초과)
        anon_old_active = Cart.objects.create(
            session_key="old_session_123",
            is_active=True,
        )
        anon_old_active.updated_at = now - timedelta(days=8)
        Cart.objects.filter(pk=anon_old_active.pk).update(updated_at=now - timedelta(days=8))

        # 비회원 활성 장바구니 - 최근 것
        anon_recent_active = Cart.objects.create(
            session_key="recent_session_456",
            is_active=True,
        )

        # 비회원 비활성 장바구니 (즉시 삭제 대상)
        anon_inactive = Cart.objects.create(
            session_key="inactive_session_789",
            is_active=False,
        )

        # 회원 비활성 장바구니 - 오래된 것 (90일 초과)
        user_old_inactive = Cart.objects.create(
            user=user,
            is_active=False,
        )
        Cart.objects.filter(pk=user_old_inactive.pk).update(updated_at=now - timedelta(days=91))

        # 회원 활성 장바구니
        user2 = UserFactory()
        user_active = Cart.objects.create(
            user=user2,
            is_active=True,
        )

        return {
            "anon_old_active": anon_old_active,
            "anon_recent_active": anon_recent_active,
            "anon_inactive": anon_inactive,
            "user_old_inactive": user_old_inactive,
            "user_active": user_active,
        }

    def test_deletes_old_anonymous_active_carts(self, setup_carts):
        """오래된 비회원 활성 장바구니 삭제"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        assert not Cart.objects.filter(pk=setup_carts["anon_old_active"].pk).exists()
        assert Cart.objects.filter(pk=setup_carts["anon_recent_active"].pk).exists()

    def test_deletes_anonymous_inactive_carts_immediately(self, setup_carts):
        """비회원 비활성 장바구니 즉시 삭제"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        assert not Cart.objects.filter(pk=setup_carts["anon_inactive"].pk).exists()

    def test_deletes_old_user_inactive_carts(self, setup_carts):
        """오래된 회원 비활성 장바구니 삭제"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        assert not Cart.objects.filter(pk=setup_carts["user_old_inactive"].pk).exists()

    def test_preserves_user_active_carts(self, setup_carts):
        """회원 활성 장바구니 유지"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        assert Cart.objects.filter(pk=setup_carts["user_active"].pk).exists()

    def test_dry_run_does_not_delete(self, setup_carts):
        """dry-run 옵션 시 실제 삭제 안함"""
        # Arrange
        initial_count = Cart.objects.count()
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", dry_run=True, stdout=out)

        # Assert
        assert Cart.objects.count() == initial_count
        assert "DRY RUN" in out.getvalue()

    def test_custom_anonymous_days(self, setup_carts):
        """anonymous-days 옵션으로 비회원 장바구니 보관 기간 변경"""
        # Arrange
        out = StringIO()

        # Act - 10일로 설정하면 8일 된 장바구니는 유지
        call_command("cleanup_old_carts", anonymous_days=10, stdout=out)

        # Assert
        assert Cart.objects.filter(pk=setup_carts["anon_old_active"].pk).exists()

    def test_custom_inactive_days(self, setup_carts):
        """inactive-days 옵션으로 회원 비활성 장바구니 보관 기간 변경"""
        # Arrange
        out = StringIO()

        # Act - 100일로 설정하면 91일 된 장바구니는 유지
        call_command("cleanup_old_carts", inactive_days=100, stdout=out)

        # Assert
        assert Cart.objects.filter(pk=setup_carts["user_old_inactive"].pk).exists()

    def test_output_contains_statistics(self, setup_carts):
        """출력에 통계 정보 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)
        output = out.getvalue()

        # Assert
        assert "장바구니" in output
        assert "삭제" in output

    def test_no_carts_to_delete(self, db):
        """삭제할 장바구니가 없는 경우"""
        # Arrange
        user = UserFactory()
        Cart.objects.create(user=user, is_active=True)
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        output = out.getvalue()
        assert "0개" in output or "삭제" in output

    def test_empty_database(self, db):
        """장바구니가 전혀 없는 경우"""
        # Arrange
        out = StringIO()

        # Act
        call_command("cleanup_old_carts", stdout=out)

        # Assert
        output = out.getvalue()
        assert "0개" in output or "장바구니" in output
