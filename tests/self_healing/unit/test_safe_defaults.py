"""
Unit tests for Safe Defaults functionality.

Phase 6: Fail-Safe Default 강화
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import List


# =============================================================================
# Test Safe Defaults Module
# =============================================================================


class TestSafeDefaultsConstants:
    """Test SAFE_DEFAULTS constant values."""

    def test_safe_defaults_contains_circuit_breaker(self):
        """Circuit Breaker Safe Default가 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        assert "circuit_breaker" in SAFE_DEFAULTS
        cb_defaults = SAFE_DEFAULTS["circuit_breaker"]
        
        # 필수 키 확인
        assert cb_defaults["enabled"] is True
        assert cb_defaults["failure_threshold"] == 5
        assert cb_defaults["recovery_timeout"] == 60
        assert cb_defaults["success_threshold"] == 2
        assert cb_defaults["half_open_max_calls"] == 3

    def test_safe_defaults_contains_dlq(self):
        """DLQ Safe Default가 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        assert "dlq" in SAFE_DEFAULTS
        dlq_defaults = SAFE_DEFAULTS["dlq"]
        
        assert dlq_defaults["enabled"] is True
        assert dlq_defaults["max_retries"] == 3
        assert dlq_defaults["expiry_hours"] == 72
        assert dlq_defaults["retention_days"] == 30

    def test_safe_defaults_contains_retry(self):
        """Retry Safe Default가 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        assert "retry" in SAFE_DEFAULTS
        retry_defaults = SAFE_DEFAULTS["retry"]
        
        assert retry_defaults["max_attempts"] == 3
        assert retry_defaults["backoff_strategy"] == "exponential"
        assert retry_defaults["jitter"] is True

    def test_safe_defaults_contains_chaos(self):
        """Chaos Safe Default가 보수적으로 설정되어 있어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        assert "chaos" in SAFE_DEFAULTS
        chaos_defaults = SAFE_DEFAULTS["chaos"]
        
        # Chaos는 기본 비활성화
        assert chaos_defaults["enabled"] is False
        # Blast Radius 제한
        assert chaos_defaults["max_blast_radius"] == 0.05
        # Dry Run 기본 활성화
        assert chaos_defaults["dry_run"] is True

    def test_safe_defaults_contains_logging(self):
        """Logging Safe Default가 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        assert "logging" in SAFE_DEFAULTS
        logging_defaults = SAFE_DEFAULTS["logging"]
        
        assert logging_defaults["dlq_log_level"] == "INFO"
        assert logging_defaults["circuit_breaker_log_level"] == "INFO"
        assert logging_defaults["include_user_info"] is False  # 보안상 비활성화

    def test_safe_defaults_all_config_types_present(self):
        """모든 주요 설정 유형이 Safe Default에 포함되어야 함."""
        from selfhealing.core.safe_defaults import SAFE_DEFAULTS
        
        required_types = [
            "circuit_breaker",
            "dlq",
            "retry",
            "rate_limit",
            "sla",
            "slo",
            "security",
            "forensic",
            "logging",
            "notification",
            "metrics",
            "error_budget",
            "idempotency",
            "chaos",
            "emergency",
        ]
        
        for config_type in required_types:
            assert config_type in SAFE_DEFAULTS, f"{config_type} should be in SAFE_DEFAULTS"


class TestValidationRules:
    """Test validation rules."""

    def test_validation_rules_circuit_breaker(self):
        """Circuit Breaker 검증 규칙이 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import VALIDATION_RULES
        
        assert "circuit_breaker" in VALIDATION_RULES
        rules = VALIDATION_RULES["circuit_breaker"]
        
        assert "failure_threshold" in rules
        assert rules["failure_threshold"] == (1, 100)
        
        assert "recovery_timeout" in rules
        assert rules["recovery_timeout"] == (1, 3600)

    def test_validation_rules_chaos_blast_radius(self):
        """Chaos blast_radius가 50% 초과 불가."""
        from selfhealing.core.safe_defaults import VALIDATION_RULES
        
        assert "chaos" in VALIDATION_RULES
        rules = VALIDATION_RULES["chaos"]
        
        # max_blast_radius 최대값 0.5 (50%)
        assert rules["max_blast_radius"] == (0.0, 0.5)

    def test_valid_log_levels(self):
        """유효한 로그 레벨이 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import VALID_LOG_LEVELS
        
        expected = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        assert VALID_LOG_LEVELS == expected

    def test_valid_backoff_strategies(self):
        """유효한 backoff 전략이 정의되어 있어야 함."""
        from selfhealing.core.safe_defaults import VALID_BACKOFF_STRATEGIES
        
        assert "exponential" in VALID_BACKOFF_STRATEGIES
        assert "linear" in VALID_BACKOFF_STRATEGIES
        assert "constant" in VALID_BACKOFF_STRATEGIES
        assert "decorrelated_jitter" in VALID_BACKOFF_STRATEGIES


class TestGetSafeDefault:
    """Test get_safe_default function."""

    def test_get_safe_default_existing_key(self):
        """존재하는 키의 Safe Default를 가져올 수 있어야 함."""
        from selfhealing.core.safe_defaults import get_safe_default
        
        result = get_safe_default("circuit_breaker", "failure_threshold")
        assert result == 5

    def test_get_safe_default_nonexistent_key(self):
        """존재하지 않는 키는 None 반환."""
        from selfhealing.core.safe_defaults import get_safe_default
        
        result = get_safe_default("circuit_breaker", "nonexistent_key")
        assert result is None

    def test_get_safe_default_nonexistent_type(self):
        """존재하지 않는 설정 유형은 None 반환."""
        from selfhealing.core.safe_defaults import get_safe_default
        
        result = get_safe_default("nonexistent_type", "any_key")
        assert result is None

    def test_get_safe_defaults_for_type(self):
        """특정 설정 유형의 모든 Safe Default를 가져올 수 있어야 함."""
        from selfhealing.core.safe_defaults import get_safe_defaults_for_type
        
        result = get_safe_defaults_for_type("dlq")
        assert isinstance(result, dict)
        assert "enabled" in result
        assert "max_retries" in result


class TestIsValidValue:
    """Test is_valid_value function."""

    def test_valid_value_within_range(self):
        """범위 내 값은 유효."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # failure_threshold: 1-100
        assert is_valid_value("circuit_breaker", "failure_threshold", 5) is True
        assert is_valid_value("circuit_breaker", "failure_threshold", 50) is True
        assert is_valid_value("circuit_breaker", "failure_threshold", 100) is True

    def test_invalid_value_below_range(self):
        """범위 미만 값은 무효."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # failure_threshold: 1-100
        assert is_valid_value("circuit_breaker", "failure_threshold", 0) is False

    def test_invalid_value_above_range(self):
        """범위 초과 값은 무효."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # failure_threshold: 1-100
        assert is_valid_value("circuit_breaker", "failure_threshold", 101) is False

    def test_invalid_none_value(self):
        """None 값은 무효."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        assert is_valid_value("circuit_breaker", "failure_threshold", None) is False

    def test_valid_log_level(self):
        """유효한 로그 레벨."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        assert is_valid_value("logging", "dlq_log_level", "INFO") is True
        assert is_valid_value("logging", "dlq_log_level", "DEBUG") is True
        assert is_valid_value("logging", "dlq_log_level", "WARNING") is True

    def test_invalid_log_level(self):
        """무효한 로그 레벨."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        assert is_valid_value("logging", "dlq_log_level", "INVALID") is False
        assert is_valid_value("logging", "dlq_log_level", "info") is False  # 대소문자 구분

    def test_valid_backoff_strategy(self):
        """유효한 backoff 전략."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        assert is_valid_value("retry", "backoff_strategy", "exponential") is True
        assert is_valid_value("retry", "backoff_strategy", "linear") is True

    def test_invalid_backoff_strategy(self):
        """무효한 backoff 전략."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        assert is_valid_value("retry", "backoff_strategy", "invalid") is False

    def test_chaos_blast_radius_limits(self):
        """Chaos blast_radius 제한 검증."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # 0.0-0.5 범위
        assert is_valid_value("chaos", "max_blast_radius", 0.05) is True
        assert is_valid_value("chaos", "max_blast_radius", 0.5) is True
        assert is_valid_value("chaos", "max_blast_radius", 0.6) is False
        assert is_valid_value("chaos", "max_blast_radius", -0.1) is False


class TestValidateWithSafeFallback:
    """Test validate_with_safe_fallback function."""

    def test_valid_values_unchanged(self):
        """유효한 값은 변경되지 않음."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        values = {"failure_threshold": 10, "recovery_timeout": 120}
        result = validate_with_safe_fallback("circuit_breaker", values, log_changes=False)
        
        assert result["failure_threshold"] == 10
        assert result["recovery_timeout"] == 120

    def test_invalid_value_replaced_with_safe_default(self):
        """무효한 값은 Safe Default로 대체."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        # failure_threshold 0은 무효 (범위: 1-100)
        values = {"failure_threshold": 0}
        result = validate_with_safe_fallback("circuit_breaker", values, log_changes=False)
        
        # Safe Default: 5
        assert result["failure_threshold"] == 5

    def test_none_value_replaced_with_safe_default(self):
        """None 값은 Safe Default로 대체."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        values = {"failure_threshold": None}
        result = validate_with_safe_fallback("circuit_breaker", values, log_changes=False)
        
        assert result["failure_threshold"] == 5

    def test_invalid_value_without_safe_default_kept(self):
        """Safe Default가 없는 무효한 값은 유지 (경고만)."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        # 존재하지 않는 키
        values = {"unknown_key": "invalid_value"}
        result = validate_with_safe_fallback("circuit_breaker", values, log_changes=False)
        
        # Safe Default가 없으므로 원래 값 유지
        assert result["unknown_key"] == "invalid_value"

    def test_mixed_valid_and_invalid_values(self):
        """유효/무효 혼합 값 처리."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        values = {
            "failure_threshold": 10,  # 유효
            "recovery_timeout": 10000,  # 무효 (범위 초과)
        }
        result = validate_with_safe_fallback("circuit_breaker", values, log_changes=False)
        
        assert result["failure_threshold"] == 10  # 유효 값 유지
        assert result["recovery_timeout"] == 60  # Safe Default로 대체


class TestGetValidationErrors:
    """Test get_validation_errors function."""

    def test_no_errors_for_valid_values(self):
        """유효한 값은 오류 없음."""
        from selfhealing.core.safe_defaults import get_validation_errors
        
        values = {"failure_threshold": 10, "recovery_timeout": 60}
        errors = get_validation_errors("circuit_breaker", values)
        
        assert len(errors) == 0

    def test_error_for_below_minimum(self):
        """최소값 미만 오류."""
        from selfhealing.core.safe_defaults import get_validation_errors
        
        values = {"failure_threshold": 0}
        errors = get_validation_errors("circuit_breaker", values)
        
        assert "failure_threshold" in errors
        assert "below minimum" in errors["failure_threshold"]

    def test_error_for_above_maximum(self):
        """최대값 초과 오류."""
        from selfhealing.core.safe_defaults import get_validation_errors
        
        values = {"failure_threshold": 200}
        errors = get_validation_errors("circuit_breaker", values)
        
        assert "failure_threshold" in errors
        assert "exceeds maximum" in errors["failure_threshold"]

    def test_error_for_none_value(self):
        """None 값 오류."""
        from selfhealing.core.safe_defaults import get_validation_errors
        
        values = {"failure_threshold": None}
        errors = get_validation_errors("circuit_breaker", values)
        
        assert "failure_threshold" in errors
        assert "cannot be None" in errors["failure_threshold"]

    def test_error_for_invalid_log_level(self):
        """무효한 로그 레벨 오류."""
        from selfhealing.core.safe_defaults import get_validation_errors
        
        values = {"dlq_log_level": "INVALID"}
        errors = get_validation_errors("logging", values)
        
        assert "dlq_log_level" in errors
        assert "Invalid log level" in errors["dlq_log_level"]


class TestValidateAllWithSafeFallback:
    """Test validate_all_with_safe_fallback function."""

    def test_validates_multiple_config_types(self):
        """여러 설정 유형을 한번에 검증."""
        from selfhealing.core.safe_defaults import validate_all_with_safe_fallback
        
        config_dict = {
            "circuit_breaker": {"failure_threshold": 0},  # 무효
            "dlq": {"max_retries": 5},  # 유효
        }
        result = validate_all_with_safe_fallback(config_dict, log_changes=False)
        
        assert result["circuit_breaker"]["failure_threshold"] == 5  # Safe Default
        assert result["dlq"]["max_retries"] == 5  # 유효 값 유지


class TestApplySafeDefaultsToMissing:
    """Test apply_safe_defaults_to_missing function."""

    def test_fills_missing_keys_with_defaults(self):
        """누락된 키에 Safe Default 채움."""
        from selfhealing.core.safe_defaults import apply_safe_defaults_to_missing
        
        values = {"failure_threshold": 10}  # recovery_timeout 누락
        result = apply_safe_defaults_to_missing("circuit_breaker", values)
        
        assert result["failure_threshold"] == 10  # 기존 값 유지
        assert result["recovery_timeout"] == 60  # Safe Default로 채움

    def test_existing_values_not_overwritten(self):
        """기존 값은 덮어쓰지 않음."""
        from selfhealing.core.safe_defaults import apply_safe_defaults_to_missing
        
        values = {"failure_threshold": 20, "recovery_timeout": 120}
        result = apply_safe_defaults_to_missing("circuit_breaker", values)
        
        assert result["failure_threshold"] == 20
        assert result["recovery_timeout"] == 120


class TestValidateChaosConfig:
    """Test validate_chaos_config function."""

    def test_clamps_blast_radius_above_50_percent(self):
        """Blast Radius 50% 초과 시 클램핑."""
        from selfhealing.core.safe_defaults import validate_chaos_config
        
        values = {"max_blast_radius": 0.8}
        result = validate_chaos_config(values)
        
        assert result["max_blast_radius"] == 0.5

    def test_clamps_negative_blast_radius(self):
        """음수 Blast Radius 0으로 클램핑."""
        from selfhealing.core.safe_defaults import validate_chaos_config
        
        values = {"max_blast_radius": -0.1}
        result = validate_chaos_config(values)
        
        assert result["max_blast_radius"] == 0.0

    def test_clamps_failure_rate_above_50_percent(self):
        """Failure Rate 50% 초과 시 클램핑."""
        from selfhealing.core.safe_defaults import validate_chaos_config
        
        values = {"failure_rate": 0.7}
        result = validate_chaos_config(values)
        
        assert result["failure_rate"] == 0.5

    def test_valid_chaos_values_unchanged(self):
        """유효한 Chaos 값은 변경되지 않음."""
        from selfhealing.core.safe_defaults import validate_chaos_config
        
        values = {"max_blast_radius": 0.1, "failure_rate": 0.05, "dry_run": False}
        result = validate_chaos_config(values)
        
        assert result["max_blast_radius"] == 0.1
        assert result["failure_rate"] == 0.05

    @patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "myproject.settings.production"})
    def test_forces_dry_run_in_production(self):
        """Production 환경에서 dry_run 강제 활성화."""
        from selfhealing.core.safe_defaults import validate_chaos_config
        
        values = {"dry_run": False}
        result = validate_chaos_config(values)
        
        assert result["dry_run"] is True


class TestValidateStartupConfig:
    """Test validate_startup_config function."""

    def test_validates_and_fixes_invalid_config(self):
        """무효한 설정을 검증하고 수정."""
        from selfhealing.core.safe_defaults import validate_startup_config
        
        @dataclass
        class MockCircuitBreakerConfig:
            failure_threshold: int = 0  # 무효
            recovery_timeout: int = 60
        
        @dataclass
        class MockConfig:
            circuit_breaker: MockCircuitBreakerConfig = field(
                default_factory=MockCircuitBreakerConfig
            )
        
        config = MockConfig()
        changes = validate_startup_config(config, log_changes=False)
        
        # failure_threshold가 Safe Default로 수정됨
        assert config.circuit_breaker.failure_threshold == 5
        assert changes >= 1

    def test_valid_config_unchanged(self):
        """유효한 설정은 변경되지 않음."""
        from selfhealing.core.safe_defaults import validate_startup_config, SAFE_DEFAULTS
        
        @dataclass
        class MockCircuitBreakerConfig:
            # 모든 Safe Default와 동일한 값 사용
            enabled: bool = True
            failure_threshold: int = 5
            recovery_timeout: int = 60
            success_threshold: int = 2
            half_open_max_calls: int = 3
            half_open_request_limit: int = 10
            rate_limit_cascade_threshold: int = 10
            rate_limit_cascade_window_seconds: int = 60
            self_ddos_protection_enabled: bool = True
            self_ddos_request_threshold: int = 100
            self_ddos_window_seconds: int = 10
            self_ddos_backoff_multiplier: float = 2.0
        
        @dataclass
        class MockConfig:
            circuit_breaker: MockCircuitBreakerConfig = field(
                default_factory=MockCircuitBreakerConfig
            )
        
        config = MockConfig()
        changes = validate_startup_config(config, log_changes=False)
        
        # 모든 값이 유효하므로 변경 없음
        assert config.circuit_breaker.failure_threshold == 5
        assert config.circuit_breaker.recovery_timeout == 60
        assert changes == 0


# =============================================================================
# Test Serializer Safe Default Integration
# =============================================================================


class TestSerializerSafeDefaultIntegration:
    """Test Serializer Safe Default integration."""

    def test_circuit_breaker_serializer_has_config_type(self):
        """CircuitBreakerConfigSerializer has _config_type."""
        from selfhealing.api.django.serializers.config import CircuitBreakerConfigSerializer
        
        assert CircuitBreakerConfigSerializer._config_type == "circuit_breaker"

    def test_dlq_serializer_has_config_type(self):
        """DLQConfigSerializer has _config_type."""
        from selfhealing.api.django.serializers.config import DLQConfigSerializer
        
        assert DLQConfigSerializer._config_type == "dlq"

    def test_retry_serializer_has_config_type(self):
        """RetryConfigSerializer has _config_type."""
        from selfhealing.api.django.serializers.config import RetryConfigSerializer
        
        assert RetryConfigSerializer._config_type == "retry"

    def test_logging_serializer_has_config_type(self):
        """LoggingConfigSerializer has _config_type."""
        from selfhealing.api.django.serializers.config import LoggingConfigSerializer
        
        assert LoggingConfigSerializer._config_type == "logging"

    def test_forensic_serializer_has_config_type(self):
        """ForensicConfigSerializer has _config_type."""
        from selfhealing.api.django.serializers.config import ForensicConfigSerializer
        
        assert ForensicConfigSerializer._config_type == "forensic"

    def test_apply_strategy_mixin_has_validate_with_safe_fallback(self):
        """ApplyStrategyMixin has validate_with_safe_fallback method."""
        from selfhealing.api.django.serializers.config import ApplyStrategyMixin
        
        assert hasattr(ApplyStrategyMixin, "validate_with_safe_fallback")


# =============================================================================
# Test AppConfig Startup Validation Integration
# =============================================================================


class TestAppConfigStartupValidation:
    """Test AppConfig startup validation integration."""

    def test_app_config_has_validate_startup_config_method(self):
        """SelfHealingConfig has _validate_startup_config method."""
        from selfhealing.adapters.django.apps import SelfHealingConfig
        
        assert hasattr(SelfHealingConfig, "_validate_startup_config")

    def test_validate_startup_config_is_callable(self):
        """_validate_startup_config method is callable."""
        from selfhealing.adapters.django.apps import SelfHealingConfig
        
        app_config = SelfHealingConfig("selfhealing", __import__("selfhealing"))
        assert callable(getattr(app_config, "_validate_startup_config", None))


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_unknown_config_type_returns_empty_defaults(self):
        """알 수 없는 설정 유형은 빈 딕셔너리 반환."""
        from selfhealing.core.safe_defaults import get_safe_defaults_for_type
        
        result = get_safe_defaults_for_type("unknown_type")
        assert result == {}

    def test_validate_with_empty_values(self):
        """빈 값 딕셔너리 검증."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        result = validate_with_safe_fallback("circuit_breaker", {}, log_changes=False)
        assert result == {}

    def test_is_valid_value_unknown_config_type(self):
        """알 수 없는 설정 유형의 값은 유효로 간주 (규칙 없음)."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # 규칙이 없으므로 범위 검증 스킵, 기본 True
        result = is_valid_value("unknown_type", "any_key", 123)
        assert result is True

    def test_boolean_field_validation(self):
        """Boolean 필드 검증."""
        from selfhealing.core.safe_defaults import is_valid_value
        
        # enabled로 시작하는 필드는 boolean이어야 함
        assert is_valid_value("circuit_breaker", "enabled", True) is True
        assert is_valid_value("circuit_breaker", "enabled", False) is True
        
        # boolean이 아닌 값은 무효
        assert is_valid_value("circuit_breaker", "enabled", "true") is False
        assert is_valid_value("circuit_breaker", "enabled", 1) is False
