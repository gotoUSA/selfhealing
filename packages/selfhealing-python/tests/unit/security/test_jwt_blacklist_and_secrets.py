"""
JWT 블랙리스트 훅 등록 및 시크릿 검증 테스트.

apps.py의 _register_jwt_blacklist_hook()과 _validate_secrets()의
동작을 검증합니다.
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.django.apps import SelfHealingConfig
from selfhealing.services.security.hooks import (
    get_session_invalidation_hooks,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def app_config():
    """SelfHealingConfig 인스턴스."""
    return SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))


# =============================================================================
# JWT Blacklist Hook Registration Tests
# =============================================================================


class TestJWTBlacklistHookRegistrationBehavior:
    """JWT 블랙리스트 훅 등록 동작 검증."""

    def test_hook_registered_when_token_blacklist_installed(self, app_config):
        """token_blacklist 앱 설치 시 훅이 등록되는지 확인."""
        with patch("django.apps.apps.is_installed", return_value=True):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 1

    def test_hook_not_registered_when_token_blacklist_missing(self, app_config):
        """token_blacklist 미설치 시 훅이 등록되지 않는지 확인."""
        with patch("django.apps.apps.is_installed", return_value=False):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 0

    def test_hook_skipped_on_import_error(self, app_config):
        """ImportError 발생 시 훅 등록이 건너뛰어지는지 확인."""
        with patch(
            "django.apps.apps.is_installed",
            side_effect=ImportError("No module found"),
        ):
            # 예외가 전파되지 않아야 함
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 0

    def test_registered_hook_blacklists_user_tokens(self, app_config):
        """등록된 훅이 사용자의 OutstandingToken을 블랙리스트에 추가하는지 확인."""
        with patch("django.apps.apps.is_installed", return_value=True):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 1

        # 훅 호출 시 OutstandingToken/BlacklistedToken 사용 확인
        mock_outstanding = MagicMock()
        mock_token_1 = MagicMock()
        mock_token_2 = MagicMock()
        mock_outstanding.objects.filter.return_value = [mock_token_1, mock_token_2]

        mock_blacklisted = MagicMock()
        mock_blacklisted.objects.get_or_create.return_value = (MagicMock(), True)

        with patch.dict(
            "sys.modules",
            {
                "rest_framework_simplejwt": MagicMock(),
                "rest_framework_simplejwt.token_blacklist": MagicMock(),
                "rest_framework_simplejwt.token_blacklist.models": MagicMock(
                    OutstandingToken=mock_outstanding,
                    BlacklistedToken=mock_blacklisted,
                ),
            },
        ):
            result = hooks[0](42)

        mock_outstanding.objects.filter.assert_called_once_with(user_id=42)
        assert mock_blacklisted.objects.get_or_create.call_count == 2
        assert result == "jwt_blacklisted(2)"

    def test_registered_hook_returns_empty_when_no_tokens(self, app_config):
        """블랙리스트할 토큰이 없을 때 빈 문자열을 반환하는지 확인."""
        with patch("django.apps.apps.is_installed", return_value=True):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()

        mock_outstanding = MagicMock()
        mock_outstanding.objects.filter.return_value = []

        with patch.dict(
            "sys.modules",
            {
                "rest_framework_simplejwt": MagicMock(),
                "rest_framework_simplejwt.token_blacklist": MagicMock(),
                "rest_framework_simplejwt.token_blacklist.models": MagicMock(
                    OutstandingToken=mock_outstanding,
                    BlacklistedToken=MagicMock(),
                ),
            },
        ):
            result = hooks[0](42)

        assert result == ""


# =============================================================================
# Secrets Validation Tests
# =============================================================================


class TestValidateSecretsBehavior:
    """시크릿 검증 동작 검증."""

    def test_validate_secrets_called_successfully(self, app_config):
        """validate_required_secrets()가 정상 호출되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            return_value={"critical": [], "warning": [], "info": []},
        ) as mock_validate:
            app_config._validate_secrets()

            mock_validate.assert_called_once()

    def test_validate_secrets_logs_critical_count(self, app_config):
        """CRITICAL 시크릿 미설정 시 에러 로그가 출력되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            return_value={"critical": ["encryption_key"], "warning": [], "info": []},
        ):
            with patch("selfhealing.adapters.django.apps.logger") as mock_logger:
                app_config._validate_secrets()

                mock_logger.error.assert_called_once()
                assert "1 CRITICAL" in mock_logger.error.call_args[0][0]

    def test_validate_secrets_logs_warning_count(self, app_config):
        """IMPORTANT 시크릿 미설정 시 경고 로그가 출력되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            return_value={"critical": [], "warning": ["database_password"], "info": []},
        ):
            with patch("selfhealing.adapters.django.apps.logger") as mock_logger:
                app_config._validate_secrets()

                mock_logger.warning.assert_called_once()
                assert "1 important" in mock_logger.warning.call_args[0][0]

    def test_validate_secrets_logs_success(self, app_config):
        """모든 시크릿이 설정되었을 때 성공 로그가 출력되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            return_value={"critical": [], "warning": [], "info": []},
        ):
            with patch("selfhealing.adapters.django.apps.logger") as mock_logger:
                app_config._validate_secrets()

                mock_logger.info.assert_called_once()
                assert "All secrets validated" in mock_logger.info.call_args[0][0]

    def test_validate_secrets_critical_failure_blocks_startup(self, app_config):
        """프로덕션 CRITICAL 시크릿 미설정 시 RuntimeError가 재발생하는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            side_effect=RuntimeError("CRITICAL secrets not configured in production"),
        ):
            with pytest.raises(RuntimeError, match="CRITICAL secrets not configured"):
                app_config._validate_secrets()

    def test_validate_secrets_critical_failure_logs_resolution_guide(self, app_config):
        """프로덕션 CRITICAL 시크릿 미설정 시 traceback + resolution guide가 로깅되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            side_effect=RuntimeError("CRITICAL secrets not configured"),
        ):
            with patch("selfhealing.adapters.django.apps.logger") as mock_logger:
                with pytest.raises(RuntimeError):
                    app_config._validate_secrets()

                mock_logger.critical.assert_called_once()
                log_message = mock_logger.critical.call_args[0][0]
                assert "SELFHEALING_SECRET_ENCRYPTION_KEY" in log_message
                assert "SELFHEALING_SECRET_AUDIT_SIGNING_KEY" in log_message
                assert "Resolution:" in log_message
                # exc_info=True 확인
                assert mock_logger.critical.call_args[1].get("exc_info") is True

    def test_validate_secrets_other_error_continues(self, app_config):
        """기타 오류 시 시작이 계속되는지 확인 (best-effort)."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            side_effect=Exception("Unexpected error"),
        ):
            # 예외가 전파되지 않아야 함
            app_config._validate_secrets()

    def test_validate_secrets_import_error_continues(self, app_config):
        """secrets 모듈 import 실패 시 시작이 계속되는지 확인."""
        with patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            side_effect=ImportError("No module named 'selfhealing.settings.secrets'"),
        ):
            # ImportError → except Exception 블록에서 처리, 전파되지 않아야 함
            app_config._validate_secrets()
