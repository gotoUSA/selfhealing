"""
Integration tests for Django AppConfig.

Tests the SelfHealingConfig.ready() and related signal handlers.
"""

import pytest
from unittest import mock


@pytest.mark.django_db
class TestAppConfigIntegration:
    """Integration tests for AppConfig.ready() and post_migrate signal."""

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
            # 시그널 핸들러 직접 호출
            create_selfhealing_groups(sender=mock.Mock())

            # 환경변수 스냅샷은 post_migrate에서 호출되지 않아야 함
            # (ready()에서만 호출됨)
            mock_log.assert_not_called()
