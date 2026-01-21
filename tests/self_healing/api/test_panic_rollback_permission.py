"""
IsPanicRollbackAuthorized 권한 클래스 테스트.

Break Glass 패턴에 따른 Panic Rollback 권한을 검증합니다:
- Admin은 reason 제공 시 항상 허용
- Operator도 reason 제공 시 허용 (Emergency Escalation)
- reason 없으면 거부
- 미인증 사용자 거부

Django 설정이 필요하므로 전역 tests 폴더에 위치합니다.

Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    - AWS Break Glass Pattern
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.django_db


class TestIsPanicRollbackAuthorizedPermission:
    """IsPanicRollbackAuthorized 권한 클래스 테스트."""

    def test_unauthenticated_user_is_denied(self):
        """미인증 사용자는 거부된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = None
        request.data = {"reason": "test reason"}

        result = permission.has_permission(request, MagicMock())

        assert result is False

    def test_user_not_authenticated_is_denied(self):
        """is_authenticated=False인 사용자는 거부된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.data = {"reason": "test reason"}

        result = permission.has_permission(request, MagicMock())

        assert result is False

    def test_reason_required_for_panic_rollback(self):
        """reason이 없으면 거부되고 적절한 메시지가 설정된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.data = {}  # No reason

        result = permission.has_permission(request, MagicMock())

        assert result is False
        assert "reason" in permission.message.lower()

    def test_empty_whitespace_reason_is_denied(self):
        """빈 문자열이나 공백만 있는 reason도 거부된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.data = {"reason": "   "}  # Whitespace only

        result = permission.has_permission(request, MagicMock())

        assert result is False

    def test_admin_with_reason_is_allowed(self):
        """Admin은 reason 제공 시 허용된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True  # Admin
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"reason": "Production incident"}

        result = permission.has_permission(request, MagicMock())

        assert result is True

    def test_operator_with_reason_is_allowed_via_emergency_escalation(self):
        """Operator도 reason 제공 시 Emergency Escalation으로 허용된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = False  # Not staff

        # Operator group membership
        def filter_side_effect(name__in=None, name=None):
            mock_qs = MagicMock()
            if name__in:
                # Check if operator or higher group is requested
                if any(g in name__in for g in ["selfhealing_operator", "selfhealing_admin"]):
                    mock_qs.exists.return_value = True
                else:
                    mock_qs.exists.return_value = False
            else:
                mock_qs.exists.return_value = True
            return mock_qs

        request.user.groups.filter.side_effect = filter_side_effect
        request.data = {"reason": "Emergency: High error rate"}

        result = permission.has_permission(request, MagicMock())

        assert result is True

    def test_viewer_is_denied_even_with_reason(self):
        """Viewer는 reason이 있어도 거부된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False  # Not superuser

        # Only viewer group (not operator/admin)
        def filter_side_effect(name__in=None, name=None):
            mock_qs = MagicMock()
            # Always return False for operator/admin checks
            mock_qs.exists.return_value = False
            return mock_qs

        request.user.groups.filter.side_effect = filter_side_effect
        request.data = {"reason": "I want to rollback"}

        result = permission.has_permission(request, MagicMock())

        assert result is False

    @patch.dict("os.environ", {"DISABLE_SELFHEALING_AUTH": "true"})
    def test_auth_bypass_when_disabled_via_env(self):
        """DISABLE_SELFHEALING_AUTH=true면 인증을 바이패스한다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = None
        request.data = {}

        result = permission.has_permission(request, MagicMock())

        assert result is True


class TestIsPanicRollbackAuthorizedLogging:
    """IsPanicRollbackAuthorized 권한의 로깅 검증."""

    @patch("selfhealing.api.django.permissions.logger")
    def test_logs_warning_when_reason_is_missing(self, mock_logger):
        """reason 없이 요청 시 경고 로그가 기록된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.data = {}

        permission.has_permission(request, MagicMock())

        mock_logger.warning.assert_called()
        call_args = str(mock_logger.warning.call_args)
        assert "reason required" in call_args.lower()

    @patch("selfhealing.api.django.permissions.logger")
    def test_logs_warning_on_admin_panic_rollback_authorized(self, mock_logger):
        """Admin이 panic rollback 승인 시 경고 로그가 기록된다."""
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        permission = IsPanicRollbackAuthorized()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"reason": "Critical incident"}

        permission.has_permission(request, MagicMock())

        mock_logger.warning.assert_called()
        call_args = str(mock_logger.warning.call_args)
        assert "authorized" in call_args.lower()


class TestCanaryPanicRollbackViewPermission:
    """CanaryPanicRollbackView에 IsPanicRollbackAuthorized 권한이 적용되었는지 검증."""

    def test_canary_panic_rollback_view_uses_correct_permission(self):
        """CanaryPanicRollbackView가 IsPanicRollbackAuthorized를 사용한다."""
        from selfhealing.api.django.views.canary import CanaryPanicRollbackView
        from selfhealing.api.django.permissions import IsPanicRollbackAuthorized

        view = CanaryPanicRollbackView()

        # permission_classes에 IsPanicRollbackAuthorized가 포함되어야 함
        assert IsPanicRollbackAuthorized in view.permission_classes, (
            "CanaryPanicRollbackView should use IsPanicRollbackAuthorized permission"
        )

    def test_canary_panic_rollback_view_does_not_use_admin_only(self):
        """CanaryPanicRollbackView가 더 이상 IsSelfHealingAdmin만 사용하지 않는다."""
        from selfhealing.api.django.views.canary import CanaryPanicRollbackView
        from selfhealing.api.django.permissions import IsSelfHealingAdmin

        view = CanaryPanicRollbackView()

        # IsSelfHealingAdmin만 사용하면 안 됨 (Break Glass 패턴이 적용되지 않은 상태)
        assert view.permission_classes != [IsSelfHealingAdmin], (
            "CanaryPanicRollbackView should not use only IsSelfHealingAdmin"
        )
