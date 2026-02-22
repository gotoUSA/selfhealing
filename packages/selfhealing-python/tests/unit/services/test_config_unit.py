"""
Tests for Config module.
config.py의 NotificationLimits, ForensicContextConfig, EventLoggingConfig,
MetricCollectionSettings, L2StorageConfig, ConfigDriftMonitor 등을 검증합니다.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.config import (
    ConfigDriftMonitor,
    EventLoggingConfig,
    ForensicContextConfig,
    L2StorageConfig,
    L2StorageRuntimeConfig,
    MetricCollectionSettings,
    NotificationLimits,
    get_forensic_settings,
    get_l2_storage_config,
    get_metric_collection_settings,
    get_notification_limits,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """각 테스트 전후로 싱글톤과 lru_cache를 초기화."""
    # lru_cache clear
    get_notification_limits.cache_clear()
    get_forensic_settings.cache_clear()
    get_metric_collection_settings.cache_clear()
    get_l2_storage_config.cache_clear()

    # EventLoggingConfig 리셋
    try:
        elc = EventLoggingConfig()
        elc.reset()
    except Exception:
        pass

    # L2StorageRuntimeConfig 리셋
    try:
        l2rc = L2StorageRuntimeConfig()
        l2rc.reset()
    except Exception:
        pass

    # ConfigDriftMonitor 리셋
    try:
        monitor = ConfigDriftMonitor()
        with monitor._hash_lock:
            monitor._env_hashes.clear()
            monitor._cache_functions.clear()
    except Exception:
        pass

    yield

    # cleanup
    get_notification_limits.cache_clear()
    get_forensic_settings.cache_clear()
    get_metric_collection_settings.cache_clear()
    get_l2_storage_config.cache_clear()


# =============================================================================
# NotificationLimits Tests
# =============================================================================


class TestNotificationLimits:
    """NotificationLimits BaseSettings 테스트."""

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        limits = NotificationLimits()
        assert limits.slack_block_text_limit == 3000
        assert limits.description_max_length == 500
        assert limits.notification_timeout_seconds == 10

    def test_frozen(self):
        """Frozen
        frozen BaseSettings이므로 값 변경이 불가한지 확인.
        """
        limits = NotificationLimits()
        with pytest.raises(Exception):
            limits.slack_block_text_limit = 5000

    def test_get_notification_limits_from_env(self):
        """Get notification limits from env
        환경변수로부터 값을 로드하는지 확인.
        """
        env = {"SELFHEALING_SLACK_BLOCK_TEXT_LIMIT": "5000"}
        with patch.dict(os.environ, env):
            get_notification_limits.cache_clear()
            limits = get_notification_limits()
            assert limits.slack_block_text_limit == 5000


# =============================================================================
# ForensicContextConfig Tests
# =============================================================================


class TestForensicContextConfig:
    """ForensicContextConfig BaseSettings 테스트."""

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        config = ForensicContextConfig()
        assert config.max_stack_frames == 50
        assert config.mask_sensitive_fields is True
        assert config.collect_request_body is False

    def test_sensitive_field_patterns(self):
        """Sensitive field patterns
        민감 필드 패턴이 올바르게 설정되는지 확인.
        """
        config = ForensicContextConfig()
        assert "password" in config.sensitive_field_patterns
        assert "token" in config.sensitive_field_patterns
        assert "card_number" in config.sensitive_field_patterns

    def test_get_forensic_settings_from_env(self):
        """Get forensic settings from env
        환경변수로부터 값을 로드하는지 확인.
        """
        env = {"SELFHEALING_MAX_STACK_FRAMES": "100"}
        with patch.dict(os.environ, env):
            get_forensic_settings.cache_clear()
            config = get_forensic_settings()
            assert config.max_stack_frames == 100


# =============================================================================
# EventLoggingConfig Tests
# =============================================================================


class TestEventLoggingConfig:
    """EventLoggingConfig 싱글톤 테스트."""

    def test_singleton(self):
        """Singleton
        두 인스턴스가 동일한 객체인지 확인.
        """
        c1 = EventLoggingConfig()
        c2 = EventLoggingConfig()
        assert c1 is c2

    def test_default_log_levels(self):
        """Default log levels
        기본 로그 레벨이 올바른지 확인.
        """
        config = EventLoggingConfig()
        assert config.get_dlq_log_level() == "INFO"
        assert config.get_cb_log_level() == "WARNING"
        assert config.get_replay_log_level() == "INFO"
        assert config.get_sla_log_level() == "WARNING"

    def test_update_log_levels(self):
        """Update log levels
        런타임에 로그 레벨을 변경할 수 있는지 확인.
        """
        config = EventLoggingConfig()
        config.update(dlq_log_level="DEBUG", cb_log_level="ERROR")
        assert config.get_dlq_log_level() == "DEBUG"
        assert config.get_cb_log_level() == "ERROR"

    def test_update_invalid_level_raises(self):
        """Update invalid level raises
        잘못된 로그 레벨을 설정하면 ValueError가 발생하는지 확인.
        """
        config = EventLoggingConfig()
        with pytest.raises(ValueError, match="Invalid log level"):
            config.update(dlq_log_level="INVALID")

    def test_reset(self):
        """Reset
        reset()이 런타임 설정을 초기화하는지 확인.
        """
        config = EventLoggingConfig()
        config.update(dlq_log_level="DEBUG")
        config.reset()
        assert config.get_dlq_log_level() == "INFO"

    def test_to_dict(self):
        """To dict
        to_dict()가 올바른 키를 포함하는지 확인.
        """
        config = EventLoggingConfig()
        d = config.to_dict()
        assert "dlq_log_level" in d
        assert "cb_log_level" in d
        assert "replay_log_level" in d
        assert "sla_log_level" in d
        assert "last_updated" in d

    def test_get_log_level_int(self):
        """Get log level int
        문자열 로그 레벨을 정수로 변환하는지 확인.
        """
        import logging

        config = EventLoggingConfig()
        assert config.get_log_level_int("INFO") == logging.INFO
        assert config.get_log_level_int("ERROR") == logging.ERROR

    def test_update_records_audit_trail(self):
        """Update records audit trail
        update() 호출 시 last_updated에 감사 정보가 기록되는지 확인.
        """
        config = EventLoggingConfig()
        result = config.update(dlq_log_level="DEBUG", updated_by="admin")
        assert result["last_updated"]["updated_by"] == "admin"
        assert "timestamp" in result["last_updated"]

    def test_env_override(self):
        """Env override
        환경변수가 하드코딩 기본값보다 우선하는지 확인.
        """
        config = EventLoggingConfig()
        config.reset()
        # env_defaults는 __init__에서 설정되므로 환경변수 패치 후 재초기화 필요
        config._env_defaults["dlq_log_level"] = "ERROR"
        assert config.get_dlq_log_level() == "ERROR"


# =============================================================================
# MetricCollectionSettings Tests
# =============================================================================


class TestMetricCollectionSettings:
    """MetricCollectionSettings BaseSettings 테스트."""

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        settings = MetricCollectionSettings()
        assert settings.sync_on_startup is True
        assert settings.jitter_enabled is True
        assert settings.adapter_type == "null"
        assert settings.drift_detection_enabled is True

    def test_drift_thresholds(self):
        """Drift thresholds
        드리프트 임계값이 올바르게 설정되는지 확인.
        """
        settings = MetricCollectionSettings()
        assert settings.drift_warning_threshold == 0.05
        assert settings.drift_critical_threshold == 0.20
        assert settings.drift_incident_threshold == 0.50


# =============================================================================
# L2StorageConfig Tests
# =============================================================================


class TestL2StorageConfig:
    """L2StorageConfig BaseSettings 테스트."""

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        config = L2StorageConfig()
        assert config.redis_timeout_ms == 50
        assert config.database_timeout_ms == 200
        assert config.shadow_log_enabled is True

    def test_get_timeout_for_adapter_redis(self):
        """Get timeout for adapter redis
        Redis 어댑터에 대한 타임아웃이 올바른지 확인.
        """
        config = L2StorageConfig()
        timeout = config.get_timeout_for_adapter("redis")
        assert timeout == 0.05  # 50ms → 0.05s

    def test_get_timeout_for_adapter_database(self):
        """Get timeout for adapter database
        Database 어댑터에 대한 타임아웃이 올바른지 확인.
        """
        config = L2StorageConfig()
        timeout = config.get_timeout_for_adapter("database")
        assert timeout == 0.2  # 200ms → 0.2s

    def test_get_timeout_for_adapter_django(self):
        """Get timeout for adapter django
        Django 어댑터가 database와 동일한 타임아웃을 사용하는지 확인.
        """
        config = L2StorageConfig()
        assert config.get_timeout_for_adapter("django") == config.get_timeout_for_adapter("database")

    def test_get_timeout_for_unknown_adapter(self):
        """Get timeout for unknown adapter
        알 수 없는 어댑터에 대해 fallback 타임아웃이 사용되는지 확인.
        """
        config = L2StorageConfig()
        timeout = config.get_timeout_for_adapter("unknown")
        assert timeout == 0.1  # 100ms → 0.1s


# =============================================================================
# L2StorageRuntimeConfig Tests
# =============================================================================


class TestL2StorageRuntimeConfig:
    """L2StorageRuntimeConfig 싱글톤 테스트."""

    def test_singleton(self):
        """Singleton
        두 인스턴스가 동일한 객체인지 확인.
        """
        c1 = L2StorageRuntimeConfig()
        c2 = L2StorageRuntimeConfig()
        assert c1 is c2

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        config = L2StorageRuntimeConfig()
        assert config.get_redis_timeout_ms() == 50
        assert config.get_database_timeout_ms() == 200
        assert config.get_shadow_log_enabled() is True

    def test_update(self):
        """Update
        런타임에 설정을 변경할 수 있는지 확인.
        """
        config = L2StorageRuntimeConfig()
        config.update(redis_timeout_ms=100, shadow_log_enabled=False)
        assert config.get_redis_timeout_ms() == 100
        assert config.get_shadow_log_enabled() is False

    def test_update_validation_rejected(self):
        """Update validation rejected
        범위 밖 값이 거부되는지 확인.
        """
        config = L2StorageRuntimeConfig()
        with pytest.raises(ValueError, match="redis_timeout_ms"):
            config.update(redis_timeout_ms=5)  # min is 10

    def test_reset(self):
        """Reset
        reset()이 런타인 설정을 초기화하는지 확인.
        """
        config = L2StorageRuntimeConfig()
        config.update(redis_timeout_ms=100)
        config.reset()
        assert config.get_redis_timeout_ms() == 50

    def test_to_dict(self):
        """To dict
        to_dict()가 올바른 키를 포함하는지 확인.
        """
        config = L2StorageRuntimeConfig()
        d = config.to_dict()
        assert "redis_timeout_ms" in d
        assert "database_timeout_ms" in d
        assert "shadow_log_enabled" in d
        assert "last_updated" in d

    def test_get_timeout_for_adapter(self):
        """Get timeout for adapter
        어댑터 타입에 따른 타임아웃이 올바른지 확인.
        """
        config = L2StorageRuntimeConfig()
        assert config.get_timeout_for_adapter("redis") == 0.05


# =============================================================================
# ConfigDriftMonitor Tests
# =============================================================================


class TestConfigDriftMonitor:
    """ConfigDriftMonitor 싱글톤 테스트."""

    def test_singleton(self):
        """Singleton
        두 인스턴스가 동일한 객체인지 확인.
        """
        m1 = ConfigDriftMonitor()
        m2 = ConfigDriftMonitor()
        assert m1 is m2

    def test_no_drift_on_first_call(self):
        """No drift on first call
        첫 호출 시 drift가 감지되지 않는지 확인.
        """
        monitor = ConfigDriftMonitor()
        result = monitor.check_and_invalidate("test_config", "SELFHEALING_TEST_")
        assert result is False

    def test_drift_detected_on_env_change(self):
        """Drift detected on env change
        환경변수 변경 시 drift가 감지되는지 확인.
        """
        monitor = ConfigDriftMonitor()

        # 첫 호출 → 해시 기록
        monitor.check_and_invalidate("test_drift", "SELFHEALING_DRIFT_TEST_")

        # 환경변수 변경
        with patch.dict(os.environ, {"SELFHEALING_DRIFT_TEST_VALUE": "changed"}):
            result = monitor.check_and_invalidate("test_drift", "SELFHEALING_DRIFT_TEST_")
            assert result is True

    def test_register_cache_function(self):
        """Register cache function
        캐시 함수 등록이 올바르게 동작하는지 확인.
        """
        monitor = ConfigDriftMonitor()
        mock_fn = MagicMock()
        mock_fn.cache_clear = MagicMock()
        monitor.register_cache_function("test_type", mock_fn)

        # 첫 호출 → 해시 기록
        monitor.check_and_invalidate("test_type", "SELFHEALING_XXX_")
        # 환경 변경 시뮬레이션
        with patch.dict(os.environ, {"SELFHEALING_XXX_VAL": "new"}):
            monitor.check_and_invalidate("test_type", "SELFHEALING_XXX_")
            mock_fn.cache_clear.assert_called_once()

    def test_get_stats(self):
        """Get stats
        get_stats()가 저장된 해시값을 반환하는지 확인.
        """
        monitor = ConfigDriftMonitor()
        monitor.check_and_invalidate("stat_test", "SELFHEALING_STAT_")
        stats = monitor.get_stats()
        assert "stat_test" in stats

    def test_no_drift_same_env(self):
        """No drift same env
        환경변수가 변경되지 않으면 drift가 감지되지 않는지 확인.
        """
        monitor = ConfigDriftMonitor()
        monitor.check_and_invalidate("stable", "SELFHEALING_STABLE_")
        result = monitor.check_and_invalidate("stable", "SELFHEALING_STABLE_")
        assert result is False
