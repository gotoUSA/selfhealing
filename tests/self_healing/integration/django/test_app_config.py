"""
Integration tests for Django AppConfig.

Tests the SelfHealingConfig.ready() and related signal handlers.
"""

import pytest
from unittest import mock

from selfhealing.services.security.hooks import (
    clear_session_invalidation_hooks,
    get_session_invalidation_hooks,
)


class TestAppConfigIntegration:
    """Integration tests for AppConfig.ready() and post_migrate signal.

    Note: These tests mock all DB-related calls, so no actual DB connection is needed.
    """

    def test_ready_logs_env_snapshot(self):
        """ready()에서 환경변수 스냅샷이 기록된다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch("selfhealing.audit.env_snapshot.log_env_snapshot_to_audit") as mock_log:
            mock_log.return_value = True

            # _log_env_snapshot 메서드 직접 호출 (ready() 내부 동작 테스트)
            app_config._log_env_snapshot()

            # 환경변수 스냅샷 로깅이 호출되었는지 확인
            mock_log.assert_called_once()

    def test_ready_continues_on_env_snapshot_failure(self):
        """env_snapshot 실패해도 ready()는 성공."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch(
            "selfhealing.audit.env_snapshot.log_env_snapshot_to_audit",
            side_effect=Exception("Snapshot failed"),
        ):
            # 예외가 발생해도 전파되지 않아야 함
            try:
                app_config._log_env_snapshot()
            except Exception:
                pytest.fail("Should not raise exception when env_snapshot fails")

    def test_post_migrate_does_not_log_env_snapshot(self):
        """post_migrate 시그널에서는 환경변수 스냅샷을 로깅하지 않는다."""
        from selfhealing.adapters.django.apps import create_selfhealing_groups

        with mock.patch("selfhealing.audit.env_snapshot.log_env_snapshot_to_audit") as mock_log:
            # Mock Django Group model to avoid DB connection
            with mock.patch("django.contrib.auth.models.Group.objects.get_or_create") as mock_group:
                mock_group.return_value = (mock.Mock(), True)

                # 시그널 핸들러 직접 호출
                create_selfhealing_groups(sender=mock.Mock())

                # 환경변수 스냅샷은 post_migrate에서 호출되지 않아야 함
                # (ready()에서만 호출됨)
                mock_log.assert_not_called()


class TestAppConfigJWTBlacklistHook:
    """AppConfig의 JWT 블랙리스트 훅 등록 통합 테스트."""

    @pytest.fixture(autouse=True)
    def _reset_hooks(self):
        """각 테스트 전후로 콜백 레지스트리 초기화."""
        clear_session_invalidation_hooks()
        yield
        clear_session_invalidation_hooks()

    def test_ready_registers_jwt_hook_when_token_blacklist_installed(self):
        """ready() 실행 시 token_blacklist가 설치되어 있으면 JWT 훅이 등록된다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch("django.apps.apps.is_installed", return_value=True):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 1

    def test_ready_skips_jwt_hook_when_token_blacklist_missing(self):
        """ready() 실행 시 token_blacklist가 미설치이면 JWT 훅이 등록되지 않는다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch("django.apps.apps.is_installed", return_value=False):
            app_config._register_jwt_blacklist_hook()

        hooks = get_session_invalidation_hooks()
        assert len(hooks) == 0


class TestAppConfigSecretsValidation:
    """AppConfig의 시크릿 검증 통합 테스트."""

    def test_validate_secrets_called_in_validate_secrets_method(self):
        """_validate_secrets()에서 validate_required_secrets()가 호출된다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            return_value={"critical": [], "warning": [], "info": []},
        ) as mock_validate:
            app_config._validate_secrets()
            mock_validate.assert_called_once()

    def test_validate_secrets_production_critical_blocks_startup(self):
        """프로덕션에서 CRITICAL 시크릿 미설정 시 시작이 차단된다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with mock.patch(
            "selfhealing.settings.secrets.validate_required_secrets",
            side_effect=RuntimeError("CRITICAL secrets not configured in production"),
        ):
            with pytest.raises(RuntimeError, match="CRITICAL secrets"):
                app_config._validate_secrets()
