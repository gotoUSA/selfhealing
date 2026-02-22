"""
Meta-Watchdog 설정 테스트.

MetaWatchdogSettings 설정 관리 테스트.
"""

import os
from unittest import mock

from selfhealing.meta.config import (
    MetaWatchdogSettings,
    get_meta_watchdog_settings,
    reset_meta_watchdog_settings,
)


class TestMetaWatchdogSettings:
    """MetaWatchdogSettings 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        settings = MetaWatchdogSettings()

        assert settings.enabled is True
        assert settings.probe_interval_seconds == 30.0
        assert settings.probe_timeout_seconds == 10.0
        assert settings.stuck_threshold_seconds == 300.0
        assert settings.dlq_stuck_threshold_entries == 1000
        assert settings.self_cb_enabled is True
        assert settings.self_cb_failure_threshold == 5
        assert settings.self_cb_recovery_timeout_seconds == 60.0
        assert settings.escalation_enabled is True
        assert settings.escalation_cooldown_seconds == 3600.0
        assert settings.pagerduty_routing_key is None
        assert settings.slack_webhook_url is None
        assert settings.dry_run_mode is False
        assert settings.maintenance_components == []

    def test_env_override(self):
        """환경변수 오버라이드 확인."""
        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_META_ENABLED": "false",
                "SELFHEALING_META_PROBE_INTERVAL_SECONDS": "60",
                "SELFHEALING_META_DRY_RUN_MODE": "true",
            },
        ):
            settings = MetaWatchdogSettings()

            assert settings.enabled is False
            assert settings.probe_interval_seconds == 60.0
            assert settings.dry_run_mode is True

    def test_pagerduty_severity_options(self):
        """PagerDuty 심각도 옵션 확인."""
        for severity in ["critical", "error", "warning", "info"]:
            with mock.patch.dict(
                os.environ,
                {"SELFHEALING_META_PAGERDUTY_SEVERITY": severity},
            ):
                settings = MetaWatchdogSettings()
                assert settings.pagerduty_severity == severity

    def test_maintenance_components_list(self):
        """유지보수 컴포넌트 목록 확인."""
        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_META_MAINTENANCE_COMPONENTS": '["redis", "dlq"]'},
        ):
            settings = MetaWatchdogSettings()
            assert settings.maintenance_components == ["redis", "dlq"]


class TestSettingsSingleton:
    """설정 싱글톤 테스트."""

    def setup_method(self):
        """테스트 전 캐시 리셋."""
        reset_meta_watchdog_settings()

    def teardown_method(self):
        """테스트 후 캐시 리셋."""
        reset_meta_watchdog_settings()

    def test_singleton_returns_same_instance(self):
        """싱글톤이 동일 인스턴스 반환."""
        settings1 = get_meta_watchdog_settings()
        settings2 = get_meta_watchdog_settings()

        assert settings1 is settings2

    def test_reset_clears_cache(self):
        """리셋이 캐시 초기화."""
        settings1 = get_meta_watchdog_settings()
        reset_meta_watchdog_settings()
        settings2 = get_meta_watchdog_settings()

        # 새 인스턴스 생성 확인
        assert settings1 is not settings2
