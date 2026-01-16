"""
Phase 4: Legacy Deprecation and Backward Compatibility Tests.

레거시 dataclass와 새 Pydantic Settings 간의 호환성 테스트.

Reference: docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

import warnings
import pytest


class TestBackwardCompatibilityAliases:
    """selfhealing.settings의 레거시 alias 테스트."""
    
    def test_circuit_breaker_config_alias(self):
        """CircuitBreakerConfig alias가 CircuitBreakerSettings와 동일."""
        from selfhealing.settings import CircuitBreakerConfig, CircuitBreakerSettings
        
        assert CircuitBreakerConfig is CircuitBreakerSettings
    
    def test_dlq_config_alias(self):
        """DLQConfig alias가 DLQSettings와 동일."""
        from selfhealing.settings import DLQConfig, DLQSettings
        
        assert DLQConfig is DLQSettings
    
    def test_retry_config_alias(self):
        """RetryConfig alias가 RetrySettings와 동일."""
        from selfhealing.settings import RetryConfig, RetrySettings
        
        assert RetryConfig is RetrySettings
    
    def test_rate_limit_config_alias(self):
        """RateLimitConfig alias가 RateLimitSettings와 동일."""
        from selfhealing.settings import RateLimitConfig, RateLimitSettings
        
        assert RateLimitConfig is RateLimitSettings
    
    def test_security_config_alias(self):
        """SecurityConfig alias가 SecuritySettings와 동일."""
        from selfhealing.settings import SecurityConfig, SecuritySettings
        
        assert SecurityConfig is SecuritySettings
    
    def test_all_phase1_aliases_exist(self):
        """Phase 1 모든 alias 존재 확인."""
        from selfhealing import settings
        
        phase1_aliases = [
            "CircuitBreakerConfig",
            "DLQConfig",
            "RetryConfig",
            "RateLimitConfig",
            "SecurityConfig",
        ]
        
        for alias in phase1_aliases:
            assert hasattr(settings, alias), f"Missing alias: {alias}"
    
    def test_all_phase2_aliases_exist(self):
        """Phase 2 모든 alias 존재 확인."""
        from selfhealing import settings
        
        phase2_aliases = [
            "SLAConfig",
            "IdempotencyConfig",
            "ForensicConfig",
            "LoggingConfig",
            "MetricsConfig",
            "NotificationConfig",
            "ErrorBudgetConfig",
            "GovernanceConfig",
            "ChaosConfig",
            "DriftThresholdConfig",
            "L2StorageConfig",
        ]
        
        for alias in phase2_aliases:
            assert hasattr(settings, alias), f"Missing alias: {alias}"


class TestLegacyConfigModuleDeprecation:
    """core/config.py 모듈 deprecation warning 테스트."""
    
    def test_core_config_import_raises_deprecation_warning(self):
        """core.config import 시 DeprecationWarning 발생."""
        # 모듈이 이미 로드되어 있을 수 있으므로 캐시 제거
        import sys
        
        # 모듈 캐시 정리 (테스트 격리)
        modules_to_remove = [k for k in sys.modules.keys() if 'selfhealing.core.config' in k]
        for mod in modules_to_remove:
            del sys.modules[mod]
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            # 이 import가 deprecation warning을 발생시켜야 함
            from selfhealing.core import config  # noqa: F401
            
            # DeprecationWarning이 발생했는지 확인
            deprecation_warnings = [
                warning for warning in w
                if issubclass(warning.category, DeprecationWarning)
                and "deprecated" in str(warning.message).lower()
            ]
            
            # 적어도 하나의 deprecation warning이 있어야 함
            assert len(deprecation_warnings) >= 1, (
                f"Expected DeprecationWarning, got: {[str(x.message) for x in w]}"
            )


class TestLegacyConfigClassesStillWork:
    """레거시 dataclass가 여전히 작동하는지 테스트 (하위 호환성)."""
    
    def test_legacy_circuit_breaker_config_works(self):
        """레거시 CircuitBreakerConfig가 작동."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(failure_threshold=10)
        assert config.failure_threshold == 10
        assert config.enabled is True  # default
    
    def test_legacy_dlq_config_works(self):
        """레거시 DLQConfig가 작동."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core.config import DLQConfig
        
        config = DLQConfig(max_retries=5)
        assert config.max_retries == 5
        assert config.enabled is True  # default
    
    def test_legacy_self_healing_config_works(self):
        """레거시 SelfHealingConfig가 작동."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core.config import SelfHealingConfig, get_config
        
        config = get_config()
        assert config is not None
        assert hasattr(config, "circuit_breaker")
        assert hasattr(config, "dlq")


class TestPydanticSettingsAsReplacement:
    """Pydantic Settings가 레거시를 대체할 수 있는지 테스트."""
    
    def test_pydantic_settings_same_defaults(self):
        """Pydantic Settings의 기본값이 레거시와 동일."""
        from selfhealing.settings import CircuitBreakerSettings
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core.config import CircuitBreakerConfig
        
        pydantic_settings = CircuitBreakerSettings()
        legacy_config = CircuitBreakerConfig()
        
        # 주요 필드 비교
        assert pydantic_settings.failure_threshold == legacy_config.failure_threshold
        assert pydantic_settings.recovery_timeout == legacy_config.recovery_timeout
        assert pydantic_settings.enabled == legacy_config.enabled
        assert pydantic_settings.success_threshold == legacy_config.success_threshold
    
    def test_pydantic_settings_env_override(self, monkeypatch):
        """Pydantic Settings는 환경변수 오버라이드 지원."""
        from selfhealing.settings import CircuitBreakerSettings, reset_circuit_breaker_settings
        
        reset_circuit_breaker_settings()
        monkeypatch.setenv("SELFHEALING_CB_FAILURE_THRESHOLD", "20")
        
        settings = CircuitBreakerSettings()
        assert settings.failure_threshold == 20
        
        reset_circuit_breaker_settings()
    
    def test_pydantic_settings_validation(self):
        """Pydantic Settings는 범위 검증 지원."""
        from selfhealing.settings import CircuitBreakerSettings
        from pydantic import ValidationError
        
        # 범위 초과 시 ValidationError 발생
        with pytest.raises(ValidationError):
            CircuitBreakerSettings(failure_threshold=0)  # ge=1 위반
        
        with pytest.raises(ValidationError):
            CircuitBreakerSettings(failure_threshold=101)  # le=100 위반


class TestMigrationPath:
    """마이그레이션 경로 테스트."""
    
    def test_can_use_alias_from_settings(self):
        """settings 모듈의 alias로 마이그레이션 가능."""
        # 레거시 코드
        # from selfhealing.core.config import CircuitBreakerConfig
        
        # 마이그레이션 후 (alias 사용)
        from selfhealing.settings import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(failure_threshold=10)
        assert config.failure_threshold == 10
    
    def test_can_use_new_settings_directly(self):
        """새 Settings 클래스 직접 사용 가능."""
        from selfhealing.settings import CircuitBreakerSettings
        
        settings = CircuitBreakerSettings(failure_threshold=10)
        assert settings.failure_threshold == 10
    
    def test_can_use_getter_functions(self):
        """getter 함수로 싱글톤 사용 가능."""
        from selfhealing.settings import (
            get_circuit_breaker_settings,
            reset_circuit_breaker_settings,
        )
        
        reset_circuit_breaker_settings()
        settings = get_circuit_breaker_settings()
        
        # 싱글톤 확인
        settings2 = get_circuit_breaker_settings()
        assert settings is settings2
        
        reset_circuit_breaker_settings()
