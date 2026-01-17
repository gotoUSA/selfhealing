"""
Tests for Pydantic Settings Advanced Features.

Tests for the 12 additional settings classes:
- SLASettings
- SLOSettings
- IdempotencySettings
- ForensicSettings
- LoggingSettings
- MetricsSettings
- NotificationSettings
- ErrorBudgetSettings
- GovernanceSettings
- ChaosSettings
- DriftThresholdSettings
- L2StorageSettings
"""

import pytest
from pydantic import ValidationError


class TestSLASettings:
    """Tests for SLASettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.sla import reset_sla_settings
        reset_sla_settings()
        yield
        reset_sla_settings()

    def test_default_values(self):
        """기본값이 core/config.py:SLAConfig와 일치하는지 검증."""
        from selfhealing.settings.sla import SLASettings
        
        settings = SLASettings()
        
        assert settings.default_hours == 24
        assert settings.thresholds_by_domain == {}

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.sla import SLASettings
        
        monkeypatch.setenv("SELFHEALING_SLA_DEFAULT_HOURS", "48")
        
        settings = SLASettings()
        
        assert settings.default_hours == 48

    def test_validation_default_hours_range(self):
        """default_hours 범위 (1-720) 검증."""
        from selfhealing.settings.sla import SLASettings
        
        with pytest.raises(ValidationError):
            SLASettings(default_hours=0)
        
        with pytest.raises(ValidationError):
            SLASettings(default_hours=721)

    def test_get_threshold(self):
        """도메인별 임계값 조회 검증."""
        from datetime import timedelta
        from selfhealing.settings.sla import SLASettings
        
        settings = SLASettings(
            default_hours=24,
            thresholds_by_domain={"payment": 1, "order": 2}
        )
        
        assert settings.get_threshold("payment") == timedelta(hours=1)
        assert settings.get_threshold("order") == timedelta(hours=2)
        assert settings.get_threshold("unknown") == timedelta(hours=24)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.sla import get_sla_settings, reset_sla_settings
        
        settings1 = get_sla_settings()
        settings2 = get_sla_settings()
        
        assert settings1 is settings2


class TestSLOSettings:
    """Tests for SLOSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.slo import reset_slo_settings
        reset_slo_settings()
        yield
        reset_slo_settings()

    def test_default_values(self):
        """기본값이 core/config.py:SLOConfigRuntime과 일치하는지 검증."""
        from selfhealing.settings.slo import SLOSettings
        
        settings = SLOSettings()
        
        assert settings.default_window_days == 30
        assert settings.default_target == 0.999
        assert settings.default_fast_burn_rate == 14.4
        assert settings.default_slow_burn_rate == 3.0
        assert settings.slos == []

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.slo import SLOSettings
        
        monkeypatch.setenv("SELFHEALING_SLO_DEFAULT_WINDOW_DAYS", "7")
        monkeypatch.setenv("SELFHEALING_SLO_DEFAULT_TARGET", "0.995")
        
        settings = SLOSettings()
        
        assert settings.default_window_days == 7
        assert settings.default_target == 0.995

    def test_validation_target_range(self):
        """default_target 범위 (0.9-1.0) 검증."""
        from selfhealing.settings.slo import SLOSettings
        
        with pytest.raises(ValidationError):
            SLOSettings(default_target=0.89)
        
        with pytest.raises(ValidationError):
            SLOSettings(default_target=1.01)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.slo import get_slo_settings, reset_slo_settings
        
        settings1 = get_slo_settings()
        settings2 = get_slo_settings()
        
        assert settings1 is settings2


class TestIdempotencySettings:
    """Tests for IdempotencySettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.idempotency import reset_idempotency_settings
        reset_idempotency_settings()
        yield
        reset_idempotency_settings()

    def test_default_values(self):
        """기본값이 core/config.py:IdempotencyConfig와 일치하는지 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings
        
        settings = IdempotencySettings()
        
        assert settings.default_cache_ttl == 60
        assert settings.extended_cache_ttl == 300
        assert settings.short_cache_ttl == 60
        assert settings.clock_skew_tolerance_seconds == 5.0

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings
        
        monkeypatch.setenv("SELFHEALING_IDEMPOTENCY_DEFAULT_CACHE_TTL", "120")
        
        settings = IdempotencySettings()
        
        assert settings.default_cache_ttl == 120

    def test_validation_cache_ttl_range(self):
        """default_cache_ttl 범위 (1-3600) 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings
        
        with pytest.raises(ValidationError):
            IdempotencySettings(default_cache_ttl=0)
        
        with pytest.raises(ValidationError):
            IdempotencySettings(default_cache_ttl=3601)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.idempotency import get_idempotency_settings
        
        settings1 = get_idempotency_settings()
        settings2 = get_idempotency_settings()
        
        assert settings1 is settings2


class TestForensicSettings:
    """Tests for ForensicSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.forensic import reset_forensic_settings
        reset_forensic_settings()
        yield
        reset_forensic_settings()

    def test_default_values(self):
        """기본값이 core/config.py:ForensicConfig와 일치하는지 검증."""
        from selfhealing.settings.forensic import ForensicSettings
        
        settings = ForensicSettings()
        
        assert settings.error_message_max_length == 500
        assert settings.response_body_max_length == 5000
        assert settings.user_agent_max_length == 500
        assert settings.max_stack_frames == 50
        assert settings.max_context_size_bytes == 65536
        assert settings.include_local_variables is False
        assert settings.sanitize_sensitive_data is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.forensic import ForensicSettings
        
        monkeypatch.setenv("SELFHEALING_FORENSIC_MAX_STACK_FRAMES", "100")
        
        settings = ForensicSettings()
        
        assert settings.max_stack_frames == 100

    def test_validation_max_stack_frames_range(self):
        """max_stack_frames 범위 (10-200) 검증."""
        from selfhealing.settings.forensic import ForensicSettings
        
        with pytest.raises(ValidationError):
            ForensicSettings(max_stack_frames=5)
        
        with pytest.raises(ValidationError):
            ForensicSettings(max_stack_frames=201)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.forensic import get_forensic_settings
        
        settings1 = get_forensic_settings()
        settings2 = get_forensic_settings()
        
        assert settings1 is settings2


class TestLoggingSettings:
    """Tests for LoggingSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.logging_config import reset_logging_settings
        reset_logging_settings()
        yield
        reset_logging_settings()

    def test_default_values(self):
        """기본값이 core/config.py:LoggingConfig와 일치하는지 검증."""
        from selfhealing.settings.logging_config import LoggingSettings
        
        settings = LoggingSettings()
        
        assert settings.dlq_log_level == "INFO"
        assert settings.circuit_breaker_log_level == "INFO"
        assert settings.forensic_log_level == "DEBUG"
        assert settings.emergency_log_level == "WARNING"
        assert settings.include_timestamps is True
        assert settings.structured_json is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.logging_config import LoggingSettings
        
        monkeypatch.setenv("SELFHEALING_LOGGING_DLQ_LOG_LEVEL", "DEBUG")
        
        settings = LoggingSettings()
        
        assert settings.dlq_log_level == "DEBUG"

    def test_validation_log_level(self):
        """로그 레벨 유효값 검증."""
        from selfhealing.settings.logging_config import LoggingSettings
        
        # Valid levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            settings = LoggingSettings(dlq_log_level=level)
            assert settings.dlq_log_level == level
        
        # Invalid level
        with pytest.raises(ValidationError):
            LoggingSettings(dlq_log_level="INVALID")

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.logging_config import get_logging_settings
        
        settings1 = get_logging_settings()
        settings2 = get_logging_settings()
        
        assert settings1 is settings2


class TestMetricsSettings:
    """Tests for MetricsSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.metrics import reset_metrics_settings
        reset_metrics_settings()
        yield
        reset_metrics_settings()

    def test_default_values(self):
        """기본값이 core/config.py:MetricsConfig와 일치하는지 검증."""
        from selfhealing.settings.metrics import MetricsSettings
        
        settings = MetricsSettings()
        
        assert settings.enabled is True
        assert settings.prefix == "selfhealing"
        assert settings.collection_interval == 60
        assert settings.export_prometheus is True
        assert settings.jitter_enabled is True
        assert settings.jitter_max_delay_seconds == 60.0

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.metrics import MetricsSettings
        
        monkeypatch.setenv("SELFHEALING_METRICS_COLLECTION_INTERVAL", "30")
        
        settings = MetricsSettings()
        
        assert settings.collection_interval == 30

    def test_validation_collection_interval_range(self):
        """collection_interval 범위 (1-3600) 검증."""
        from selfhealing.settings.metrics import MetricsSettings
        
        with pytest.raises(ValidationError):
            MetricsSettings(collection_interval=0)
        
        with pytest.raises(ValidationError):
            MetricsSettings(collection_interval=3601)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.metrics import get_metrics_settings
        
        settings1 = get_metrics_settings()
        settings2 = get_metrics_settings()
        
        assert settings1 is settings2


class TestNotificationSettings:
    """Tests for NotificationSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.notification import reset_notification_settings
        reset_notification_settings()
        yield
        reset_notification_settings()

    def test_default_values(self):
        """기본값이 core/config.py:NotificationConfig와 일치하는지 검증."""
        from selfhealing.settings.notification import NotificationSettings
        
        settings = NotificationSettings()
        
        assert settings.enabled is True
        assert settings.channels == ["email"]
        assert settings.critical_threshold == 10
        assert settings.warning_threshold == 5
        assert settings.slack_block_text_limit == 3000
        assert settings.description_max_length == 500

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.notification import NotificationSettings
        
        monkeypatch.setenv("SELFHEALING_NOTIFICATION_CRITICAL_THRESHOLD", "20")
        
        settings = NotificationSettings()
        
        assert settings.critical_threshold == 20

    def test_validation_threshold_range(self):
        """critical_threshold 범위 (1-100) 검증."""
        from selfhealing.settings.notification import NotificationSettings
        
        with pytest.raises(ValidationError):
            NotificationSettings(critical_threshold=0)
        
        with pytest.raises(ValidationError):
            NotificationSettings(critical_threshold=101)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.notification import get_notification_settings
        
        settings1 = get_notification_settings()
        settings2 = get_notification_settings()
        
        assert settings1 is settings2


class TestErrorBudgetSettings:
    """Tests for ErrorBudgetSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.error_budget import reset_error_budget_settings
        reset_error_budget_settings()
        yield
        reset_error_budget_settings()

    def test_default_values(self):
        """기본값이 core/config.py:ErrorBudgetConfig와 일치하는지 검증."""
        from selfhealing.settings.error_budget import ErrorBudgetSettings
        
        settings = ErrorBudgetSettings()
        
        assert settings.threshold_healthy == 75.0
        assert settings.threshold_caution == 50.0
        assert settings.threshold_warning == 20.0
        assert settings.threshold_critical == 0.0
        assert settings.burn_rate_fast_critical == 14.4
        assert settings.heartbeat_enabled is True
        assert settings.heartbeat_interval_seconds == 60

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.error_budget import ErrorBudgetSettings
        
        monkeypatch.setenv("SELFHEALING_ERRORBUDGET_BURN_RATE_FAST_CRITICAL", "20.0")
        
        settings = ErrorBudgetSettings()
        
        assert settings.burn_rate_fast_critical == 20.0

    def test_validation_threshold_range(self):
        """threshold_healthy 범위 (50.0-100.0) 검증."""
        from selfhealing.settings.error_budget import ErrorBudgetSettings
        
        with pytest.raises(ValidationError):
            ErrorBudgetSettings(threshold_healthy=40.0)
        
        with pytest.raises(ValidationError):
            ErrorBudgetSettings(threshold_healthy=101.0)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.error_budget import get_error_budget_settings
        
        settings1 = get_error_budget_settings()
        settings2 = get_error_budget_settings()
        
        assert settings1 is settings2


class TestGovernanceSettings:
    """Tests for GovernanceSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.governance import reset_governance_settings
        reset_governance_settings()
        yield
        reset_governance_settings()

    def test_default_values(self):
        """기본값이 core/config.py:GovernanceConfig와 일치하는지 검증."""
        from selfhealing.settings.governance import GovernanceSettings
        
        settings = GovernanceSettings()
        
        assert settings.threshold_operator == 0.15
        assert settings.threshold_admin == 0.30
        assert settings.emergency_expiry_hours == 8
        assert settings.default_mode == "NORMAL"
        assert settings.four_eyes_enabled is False

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.governance import GovernanceSettings
        
        monkeypatch.setenv("SELFHEALING_GOVERNANCE_EMERGENCY_EXPIRY_HOURS", "12")
        
        settings = GovernanceSettings()
        
        assert settings.emergency_expiry_hours == 12

    def test_validation_mode(self):
        """default_mode 유효값 검증."""
        from selfhealing.settings.governance import GovernanceSettings
        
        # Valid modes
        for mode in ["NORMAL", "STRICT"]:
            settings = GovernanceSettings(default_mode=mode)
            assert settings.default_mode == mode
        
        # Invalid mode
        with pytest.raises(ValidationError):
            GovernanceSettings(default_mode="INVALID")

    def test_validation_threshold_range(self):
        """threshold_operator 범위 (0.01-1.0) 검증."""
        from selfhealing.settings.governance import GovernanceSettings
        
        with pytest.raises(ValidationError):
            GovernanceSettings(threshold_operator=0.0)
        
        with pytest.raises(ValidationError):
            GovernanceSettings(threshold_operator=1.1)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.governance import get_governance_settings
        
        settings1 = get_governance_settings()
        settings2 = get_governance_settings()
        
        assert settings1 is settings2


class TestChaosSettings:
    """Tests for ChaosSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.chaos import reset_chaos_settings
        reset_chaos_settings()
        yield
        reset_chaos_settings()

    def test_default_values(self):
        """기본값이 core/config.py:ChaosConfig와 일치하는지 검증."""
        from selfhealing.settings.chaos import ChaosSettings
        
        settings = ChaosSettings()
        
        assert settings.enabled is False  # Safety: disabled by default
        assert settings.max_blast_radius == 0.10
        assert settings.max_failure_rate == 0.20
        assert settings.auto_rollback_enabled is True
        assert settings.dry_run_default is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.chaos import ChaosSettings
        
        monkeypatch.setenv("SELFHEALING_CHAOS_MAX_BLAST_RADIUS", "0.20")
        
        settings = ChaosSettings()
        
        assert settings.max_blast_radius == 0.20

    def test_validation_blast_radius_range(self):
        """max_blast_radius 범위 (0.0-0.5) 검증."""
        from selfhealing.settings.chaos import ChaosSettings
        
        with pytest.raises(ValidationError):
            ChaosSettings(max_blast_radius=-0.1)
        
        with pytest.raises(ValidationError):
            ChaosSettings(max_blast_radius=0.6)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.chaos import get_chaos_settings
        
        settings1 = get_chaos_settings()
        settings2 = get_chaos_settings()
        
        assert settings1 is settings2


class TestDriftThresholdSettings:
    """Tests for DriftThresholdSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.drift_threshold import reset_drift_threshold_settings
        reset_drift_threshold_settings()
        yield
        reset_drift_threshold_settings()

    def test_default_values(self):
        """기본값이 core/config.py:DriftThresholdConfig와 일치하는지 검증."""
        from selfhealing.settings.drift_threshold import DriftThresholdSettings
        
        settings = DriftThresholdSettings()
        
        assert settings.enabled is True
        assert settings.warning_threshold == 0.05
        assert settings.critical_threshold == 0.20
        assert settings.incident_threshold == 0.50
        assert settings.alert_enabled is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.drift_threshold import DriftThresholdSettings
        
        monkeypatch.setenv("SELFHEALING_DRIFT_WARNING_THRESHOLD", "0.10")
        
        settings = DriftThresholdSettings()
        
        assert settings.warning_threshold == 0.10

    def test_validation_threshold_range(self):
        """warning_threshold 범위 (0.01-0.50) 검증."""
        from selfhealing.settings.drift_threshold import DriftThresholdSettings
        
        with pytest.raises(ValidationError):
            DriftThresholdSettings(warning_threshold=0.0)
        
        with pytest.raises(ValidationError):
            DriftThresholdSettings(warning_threshold=0.6)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.drift_threshold import get_drift_threshold_settings
        
        settings1 = get_drift_threshold_settings()
        settings2 = get_drift_threshold_settings()
        
        assert settings1 is settings2


class TestL2StorageSettings:
    """Tests for L2StorageSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.l2_storage import reset_l2_storage_settings
        reset_l2_storage_settings()
        yield
        reset_l2_storage_settings()

    def test_default_values(self):
        """기본값 검증."""
        from selfhealing.settings.l2_storage import L2StorageSettings
        
        settings = L2StorageSettings()
        
        assert settings.enabled is False  # Disabled by default
        assert settings.redis_timeout_ms == 1000
        assert settings.shadow_log_enabled is True
        assert settings.reconciliation_enabled is True
        assert settings.reconciliation_interval_seconds == 300

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.l2_storage import L2StorageSettings
        
        monkeypatch.setenv("SELFHEALING_L2STORAGE_REDIS_TIMEOUT_MS", "2000")
        
        settings = L2StorageSettings()
        
        assert settings.redis_timeout_ms == 2000

    def test_validation_redis_timeout_range(self):
        """redis_timeout_ms 범위 (100-10000) 검증."""
        from selfhealing.settings.l2_storage import L2StorageSettings
        
        with pytest.raises(ValidationError):
            L2StorageSettings(redis_timeout_ms=50)
        
        with pytest.raises(ValidationError):
            L2StorageSettings(redis_timeout_ms=10001)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.l2_storage import get_l2_storage_settings
        
        settings1 = get_l2_storage_settings()
        settings2 = get_l2_storage_settings()
        
        assert settings1 is settings2


class TestPydanticConsistencyWithLegacy:
    """
    Pydantic 설정과 기존 dataclass 설정의 일관성 검증.
    """

    def test_sla_consistency(self):
        """SLAConfig와 SLASettings 기본값 일치."""
        from selfhealing.core.config import SLAConfig
        from selfhealing.settings.sla import SLASettings
        
        legacy = SLAConfig()
        pydantic = SLASettings()
        
        assert pydantic.default_hours == legacy.default_hours

    def test_idempotency_consistency(self):
        """IdempotencyConfig와 IdempotencySettings 기본값 일치."""
        from selfhealing.core.config import IdempotencyConfig
        from selfhealing.settings.idempotency import IdempotencySettings
        
        legacy = IdempotencyConfig()
        pydantic = IdempotencySettings()
        
        assert pydantic.default_cache_ttl == legacy.default_cache_ttl
        assert pydantic.extended_cache_ttl == legacy.extended_cache_ttl
        assert pydantic.short_cache_ttl == legacy.short_cache_ttl
        assert pydantic.clock_skew_tolerance_seconds == legacy.clock_skew_tolerance_seconds

    def test_forensic_consistency(self):
        """ForensicConfig와 ForensicSettings 기본값 일치."""
        from selfhealing.core.config import ForensicConfig
        from selfhealing.settings.forensic import ForensicSettings
        
        legacy = ForensicConfig()
        pydantic = ForensicSettings()
        
        assert pydantic.error_message_max_length == legacy.error_message_max_length
        assert pydantic.response_body_max_length == legacy.response_body_max_length
        assert pydantic.max_stack_frames == legacy.max_stack_frames
        assert pydantic.max_context_size_bytes == legacy.max_context_size_bytes

    def test_logging_consistency(self):
        """LoggingConfig와 LoggingSettings 기본값 일치."""
        from selfhealing.core.config import LoggingConfig
        from selfhealing.settings.logging_config import LoggingSettings
        
        legacy = LoggingConfig()
        pydantic = LoggingSettings()
        
        assert pydantic.dlq_log_level == legacy.dlq_log_level
        assert pydantic.circuit_breaker_log_level == legacy.circuit_breaker_log_level
        assert pydantic.structured_json == legacy.structured_json

    def test_metrics_consistency(self):
        """MetricsConfig와 MetricsSettings 기본값 일치."""
        from selfhealing.core.config import MetricsConfig
        from selfhealing.settings.metrics import MetricsSettings
        
        legacy = MetricsConfig()
        pydantic = MetricsSettings()
        
        assert pydantic.enabled == legacy.enabled
        assert pydantic.prefix == legacy.prefix
        assert pydantic.collection_interval == legacy.collection_interval

    def test_notification_consistency(self):
        """NotificationConfig와 NotificationSettings 기본값 일치."""
        from selfhealing.core.config import NotificationConfig
        from selfhealing.settings.notification import NotificationSettings
        
        legacy = NotificationConfig()
        pydantic = NotificationSettings()
        
        assert pydantic.enabled == legacy.enabled
        assert pydantic.critical_threshold == legacy.critical_threshold
        assert pydantic.warning_threshold == legacy.warning_threshold
        assert pydantic.slack_block_text_limit == legacy.slack_block_text_limit

    def test_error_budget_consistency(self):
        """ErrorBudgetConfig와 ErrorBudgetSettings 기본값 일치."""
        from selfhealing.core.config import ErrorBudgetConfig
        from selfhealing.settings.error_budget import ErrorBudgetSettings
        
        legacy = ErrorBudgetConfig()
        pydantic = ErrorBudgetSettings()
        
        assert pydantic.threshold_healthy == legacy.threshold_healthy
        assert pydantic.threshold_caution == legacy.threshold_caution
        assert pydantic.burn_rate_fast_critical == legacy.burn_rate_fast_critical
        assert pydantic.heartbeat_enabled == legacy.heartbeat_enabled

    def test_governance_consistency(self):
        """GovernanceConfig와 GovernanceSettings 기본값 일치."""
        from selfhealing.core.config import GovernanceConfig
        from selfhealing.settings.governance import GovernanceSettings
        
        legacy = GovernanceConfig()
        pydantic = GovernanceSettings()
        
        assert pydantic.threshold_operator == legacy.threshold_operator
        assert pydantic.threshold_admin == legacy.threshold_admin
        assert pydantic.emergency_expiry_hours == legacy.emergency_expiry_hours
        assert pydantic.default_mode == legacy.default_mode

    def test_chaos_consistency(self):
        """ChaosConfig와 ChaosSettings 기본값 일치."""
        from selfhealing.core.config import ChaosConfig
        from selfhealing.settings.chaos import ChaosSettings
        
        legacy = ChaosConfig()
        pydantic = ChaosSettings()
        
        assert pydantic.max_blast_radius == legacy.max_blast_radius
        assert pydantic.max_failure_rate == legacy.max_failure_rate
        assert pydantic.auto_rollback_enabled == legacy.auto_rollback_enabled

    def test_drift_threshold_consistency(self):
        """DriftThresholdConfig와 DriftThresholdSettings 기본값 일치."""
        from selfhealing.core.config import DriftThresholdConfig
        from selfhealing.settings.drift_threshold import DriftThresholdSettings
        
        legacy = DriftThresholdConfig()
        pydantic = DriftThresholdSettings()
        
        assert pydantic.warning_threshold == legacy.warning_threshold
        assert pydantic.critical_threshold == legacy.critical_threshold
        assert pydantic.alert_enabled == legacy.alert_enabled
