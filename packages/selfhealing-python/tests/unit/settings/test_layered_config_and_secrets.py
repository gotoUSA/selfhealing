"""
Pydantic Settings Advanced Features Tests.

테스트 대상:
- TestLayeredProvider: Request-scoped 설정 오버라이드 (contextvars 기반 4-Level 계층화)
- TestSecretsSettings: SecretStr 기반 민감 정보 자동 마스킹
- TestPartialUpdate: PATCH 요청 시 부분 검증 지원
- TestConfigDriftAudit: 설정 변경 감지 및 Audit 로깅
- TestPydanticSettingsIntegration: 모듈 export 및 Context Manager 통합

Note: 이 테스트 파일은 Django 없이 단위 테스트로 실행됩니다.
Django 의존 테스트는 tests/self_healing/ 디렉토리에서 실행됩니다.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestLayeredProvider:
    """Layered Configuration Provider 테스트."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """테스트 전 오버라이드 초기화."""
        from selfhealing.settings.layered_provider import clear_request_overrides
        clear_request_overrides()
        yield
        clear_request_overrides()
    
    def test_import_layered_provider(self):
        """layered_provider 모듈 임포트 테스트."""
        from selfhealing.settings.layered_provider import (
            get_layered_settings,
            set_request_override,
            get_request_override,
            clear_request_overrides,
            get_all_request_overrides,
            detect_config_source,
            get_config_with_sources,
            RequestOverrideContext,
        )
        assert callable(get_layered_settings)
        assert callable(set_request_override)
        assert callable(get_request_override)
        assert callable(clear_request_overrides)
        assert callable(get_all_request_overrides)
        assert callable(detect_config_source)
        assert callable(get_config_with_sources)
        assert RequestOverrideContext is not None
    
    def test_set_and_get_request_override(self):
        """Request 스코프 오버라이드 설정 및 조회."""
        from selfhealing.settings.layered_provider import (
            set_request_override,
            get_request_override,
            clear_request_overrides,
        )
        
        clear_request_overrides()
        
        # 오버라이드 설정
        set_request_override("circuit_breaker", {"failure_threshold": 20})
        
        # 조회
        override = get_request_override("circuit_breaker")
        assert override == {"failure_threshold": 20}
        
        # 없는 타입 조회
        empty = get_request_override("nonexistent")
        assert empty == {}
        
        # 정리
        clear_request_overrides()
        assert get_request_override("circuit_breaker") == {}
    
    def test_get_all_request_overrides(self):
        """모든 Request 오버라이드 조회."""
        from selfhealing.settings.layered_provider import (
            set_request_override,
            get_all_request_overrides,
            clear_request_overrides,
        )
        
        clear_request_overrides()
        
        set_request_override("circuit_breaker", {"failure_threshold": 10})
        set_request_override("retry", {"max_retries": 5})
        
        all_overrides = get_all_request_overrides()
        assert "circuit_breaker" in all_overrides
        assert "retry" in all_overrides
        assert all_overrides["circuit_breaker"]["failure_threshold"] == 10
        assert all_overrides["retry"]["max_retries"] == 5
        
        clear_request_overrides()
    
    def test_request_override_context(self):
        """RequestOverrideContext context manager 테스트."""
        from selfhealing.settings.layered_provider import (
            RequestOverrideContext,
            get_request_override,
            clear_request_overrides,
        )
        
        clear_request_overrides()
        
        # 컨텍스트 내에서 오버라이드
        with RequestOverrideContext("circuit_breaker", {"failure_threshold": 99}):
            override = get_request_override("circuit_breaker")
            assert override["failure_threshold"] == 99
        
        # 컨텍스트 종료 후 복원
        override = get_request_override("circuit_breaker")
        assert override == {}
    
    def test_get_layered_settings_basic(self):
        """get_layered_settings 기본 동작."""
        from selfhealing.settings.layered_provider import (
            get_layered_settings,
            clear_request_overrides,
        )
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        clear_request_overrides()
        
        # 기본값만 사용 (runtime, request 제외)
        settings = get_layered_settings(
            CircuitBreakerSettings,
            "circuit_breaker",
            include_runtime=False,
            include_request=False,
        )
        
        assert isinstance(settings, CircuitBreakerSettings)
        assert settings.failure_threshold == 5  # 기본값
    
    def test_get_layered_settings_with_request_override(self):
        """get_layered_settings에서 Request 오버라이드 적용."""
        from selfhealing.settings.layered_provider import (
            get_layered_settings,
            set_request_override,
            clear_request_overrides,
        )
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        clear_request_overrides()
        
        # Request 오버라이드 설정
        set_request_override("circuit_breaker", {"failure_threshold": 50})
        
        # 오버라이드 적용됨
        settings = get_layered_settings(
            CircuitBreakerSettings,
            "circuit_breaker",
            include_runtime=False,
            include_request=True,
        )
        
        assert settings.failure_threshold == 50
        
        clear_request_overrides()
    
    def test_detect_config_source_default(self):
        """설정 출처 감지 - DEFAULT."""
        from selfhealing.settings.layered_provider import (
            detect_config_source,
            clear_request_overrides,
        )
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        clear_request_overrides()
        
        # exclude_exceptions 필드는 환경변수로 설정 어려움 → DEFAULT일 확률 높음
        source = detect_config_source(
            CircuitBreakerSettings,
            "circuit_breaker",
            "excluded_exceptions",  # 환경변수/runtime에서 거의 안 건드는 필드
        )
        
        # ENV 변수 설정 안됨 → DEFAULT 또는 RUNTIME
        # 테스트 환경에 따라 RUNTIME 캐시가 있을 수 있음
        assert source in ("DEFAULT", "RUNTIME")
    
    def test_detect_config_source_request(self):
        """설정 출처 감지 - REQUEST."""
        from selfhealing.settings.layered_provider import (
            detect_config_source,
            set_request_override,
            clear_request_overrides,
        )
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        clear_request_overrides()
        set_request_override("circuit_breaker", {"failure_threshold": 10})
        
        source = detect_config_source(
            CircuitBreakerSettings,
            "circuit_breaker",
            "failure_threshold",
        )
        
        assert source == "REQUEST"
        
        clear_request_overrides()
    
    def test_convenience_functions(self):
        """Convenience 함수들 테스트 - 함수가 Settings 인스턴스를 반환하는지 확인."""
        from selfhealing.settings.layered_provider import (
            get_circuit_breaker_layered,
            get_retry_layered,
            get_dlq_layered,
            get_rate_limit_layered,
            clear_request_overrides,
        )
        from selfhealing.settings import (
            CircuitBreakerSettings,
            RetrySettings,
            DLQSettings,
            RateLimitSettings,
        )
        
        clear_request_overrides()
        
        cb = get_circuit_breaker_layered()
        retry = get_retry_layered()
        dlq = get_dlq_layered()
        rate_limit = get_rate_limit_layered()
        
        # 올바른 타입 반환
        assert isinstance(cb, CircuitBreakerSettings)
        assert isinstance(retry, RetrySettings)
        assert isinstance(dlq, DLQSettings)
        assert isinstance(rate_limit, RateLimitSettings)
        
        # 기본값 범위 내 (runtime 오버라이드가 있을 수 있음)
        assert 1 <= cb.failure_threshold <= 100
        assert 1 <= retry.max_attempts <= 20  # max_retries가 아니라 max_attempts
        assert dlq.max_retries > 0  # max_items가 아니라 max_retries
        assert rate_limit.base_delay > 0  # default_limit이 아니라 base_delay


class TestSecretsSettings:
    """SecretStr 기반 민감 정보 설정 테스트."""
    
    def test_import_secrets(self):
        """secrets 모듈 임포트."""
        from selfhealing.settings.secrets import (
            SecretsSettings,
            get_secrets,
            reset_secrets,
        )
        assert SecretsSettings is not None
        assert callable(get_secrets)
        assert callable(reset_secrets)
    
    def test_secret_str_default_empty(self):
        """비밀번호 기본값은 빈 문자열."""
        from selfhealing.settings.secrets import SecretsSettings
        
        secrets = SecretsSettings()
        
        # SecretStr의 get_secret_value()로 실제 값 확인
        assert secrets.database_password.get_secret_value() == ""
        assert secrets.redis_password.get_secret_value() == ""
        assert secrets.toss_secret_key.get_secret_value() == ""
    
    def test_secret_str_masking_repr(self):
        """SecretStr은 repr에서 마스킹됨."""
        from selfhealing.settings.secrets import SecretsSettings
        
        # 환경변수로 시크릿 설정
        with patch.dict(os.environ, {"SELFHEALING_SECRET_DATABASE_PASSWORD": "my_password"}):
            secrets = SecretsSettings()
            
            # repr에서 마스킹
            repr_str = repr(secrets.database_password)
            assert "my_password" not in repr_str
            assert "**" in repr_str
            
            # 실제 값은 get_secret_value()로만 접근
            assert secrets.database_password.get_secret_value() == "my_password"
    
    def test_secret_str_masking_str(self):
        """SecretStr은 str()에서도 마스킹됨."""
        from selfhealing.settings.secrets import SecretsSettings
        
        with patch.dict(os.environ, {"SELFHEALING_SECRET_REDIS_PASSWORD": "redis123"}):
            secrets = SecretsSettings()
            
            # str()에서도 마스킹
            str_val = str(secrets.redis_password)
            assert "redis123" not in str_val
            assert "**" in str_val
    
    def test_has_methods(self):
        """has_* 메서드 테스트."""
        from selfhealing.settings.secrets import SecretsSettings
        
        # 빈 상태
        secrets = SecretsSettings()
        assert secrets.has_database_password() is False
        assert secrets.has_redis_password() is False
        assert secrets.has_toss_secret() is False
        assert secrets.has_slack_webhook() is False
        
        # 값이 있는 상태
        with patch.dict(os.environ, {"SELFHEALING_SECRET_DATABASE_PASSWORD": "pass"}):
            secrets = SecretsSettings()
            assert secrets.has_database_password() is True
    
    def test_get_masked_summary(self):
        """마스킹된 요약 테스트."""
        from selfhealing.settings.secrets import SecretsSettings
        
        with patch.dict(os.environ, {
            "SELFHEALING_SECRET_DATABASE_PASSWORD": "pass",
            "SELFHEALING_SECRET_TOSS_SECRET_KEY": "key123",
        }):
            secrets = SecretsSettings()
            summary = secrets.get_masked_summary()
            
            assert summary["database_password"] is True
            assert summary["toss_secret_key"] is True
            assert summary["redis_password"] is False
            assert summary["slack_webhook_token"] is False
    
    def test_get_secrets_singleton(self):
        """get_secrets는 싱글톤을 반환."""
        from selfhealing.settings.secrets import get_secrets, reset_secrets
        
        reset_secrets()
        
        s1 = get_secrets()
        s2 = get_secrets()
        
        assert s1 is s2
        
        reset_secrets()


class TestPartialUpdate:
    """Partial Update (PATCH) 지원 테스트.
    
    Pydantic 모델의 부분 검증 로직을 테스트합니다.
    Django/DRF 없이 순수 Python으로 검증 로직만 테스트합니다.
    
    Note: PydanticSerializerMixin의 validate_with_pydantic_partial 메서드는
    Django 통합 테스트(tests/self_healing/)에서 테스트됩니다.
    여기서는 핵심 Pydantic 검증 로직만 테스트합니다.
    """
    
    def test_partial_update_logic_with_pydantic_only(self):
        """Pydantic만으로 부분 업데이트 검증 로직 테스트."""
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        # 현재 설정
        current = CircuitBreakerSettings()
        current_dict = current.model_dump()
        
        # 부분 업데이트 데이터
        partial_data = {"failure_threshold": 20}
        
        # 병합 후 검증 (validate_with_pydantic_partial의 핵심 로직)
        merged = current_dict.copy()
        merged.update(partial_data)
        validated = CircuitBreakerSettings.model_validate(merged)
        
        # 검증 결과
        assert validated.failure_threshold == 20
        assert validated.recovery_timeout == current.recovery_timeout  # 다른 필드 유지
        
        # 변경된 필드만 추출
        result = {k: v for k, v in validated.model_dump().items() if k in partial_data}
        assert result == {"failure_threshold": 20}
    
    def test_partial_update_with_defaults(self):
        """기본값과 병합하여 부분 업데이트."""
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        
        # 기본 설정 생성
        defaults = CircuitBreakerSettings()
        
        # 부분 데이터
        partial_data = {"failure_threshold": 10, "recovery_timeout": 120}
        
        # 병합
        merged = defaults.model_dump()
        merged.update(partial_data)
        validated = CircuitBreakerSettings.model_validate(merged)
        
        assert validated.failure_threshold == 10
        assert validated.recovery_timeout == 120
        
        # 변경된 필드만
        result = {k: v for k, v in validated.model_dump().items() if k in partial_data}
        assert len(result) == 2
    
    def test_partial_update_validation_error_pydantic(self):
        """부분 업데이트 시 Pydantic 검증 오류."""
        from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
        from pydantic import ValidationError
        
        defaults = CircuitBreakerSettings()
        
        # 유효하지 않은 값
        partial_data = {"failure_threshold": 0}  # ge=1 위반
        
        merged = defaults.model_dump()
        merged.update(partial_data)
        
        with pytest.raises(ValidationError):
            CircuitBreakerSettings.model_validate(merged)


class TestConfigDriftAudit:
    """Config Drift Audit 테스트."""
    
    def test_compute_diff(self):
        """_compute_diff 메서드 테스트."""
        from selfhealing.services.runtime_config.base import BaseConfigManager
        
        manager = BaseConfigManager()
        
        old = {"a": 1, "b": 2, "c": 3}
        new = {"a": 1, "b": 5, "c": 3}  # b만 변경
        
        diff = manager._compute_diff(old, new)
        
        assert diff is not None
        assert diff["old"] == {"b": 2}
        assert diff["new"] == {"b": 5}
    
    def test_compute_diff_no_changes(self):
        """_compute_diff - 변경 없음."""
        from selfhealing.services.runtime_config.base import BaseConfigManager
        
        manager = BaseConfigManager()
        
        old = {"a": 1, "b": 2}
        new = {"a": 1, "b": 2}
        
        diff = manager._compute_diff(old, new)
        
        assert diff is None
    
    def test_compute_diff_multiple_changes(self):
        """_compute_diff - 여러 필드 변경."""
        from selfhealing.services.runtime_config.base import BaseConfigManager
        
        manager = BaseConfigManager()
        
        old = {"a": 1, "b": 2, "c": 3}
        new = {"a": 10, "b": 2, "c": 30}  # a, c 변경
        
        diff = manager._compute_diff(old, new)
        
        assert diff is not None
        assert set(diff["old"].keys()) == {"a", "c"}
        assert set(diff["new"].keys()) == {"a", "c"}
        assert diff["old"]["a"] == 1
        assert diff["new"]["a"] == 10
    
    def test_emit_config_change_audit(self):
        """_emit_config_change_audit 메서드 테스트."""
        from selfhealing.services.runtime_config.base import BaseConfigManager
        
        manager = BaseConfigManager()
        
        # log_config_change 패치
        with patch("selfhealing.services.runtime_config.base.logger") as mock_logger:
            with patch("selfhealing.audit.log_config_change") as mock_log:
                manager._emit_config_change_audit(
                    config_type="circuit_breaker",
                    changed_by="test_user",
                    reason="test reason",
                    old_values={"failure_threshold": 5},
                    new_values={"failure_threshold": 10},
                )
                
                # log_config_change 호출됨
                mock_log.assert_called_once()
                call_kwargs = mock_log.call_args[1]
                assert call_kwargs["config_type"] == "CIRCUIT_BREAKER"
                assert call_kwargs["config_key"] == "failure_threshold"
                assert call_kwargs["old_value"] == 5
                assert call_kwargs["new_value"] == 10
                assert call_kwargs["user"] == "test_user"
    
    def test_update_config_emits_audit(self):
        """_update_config가 audit 이벤트를 발행."""
        from selfhealing.services.runtime_config.base import BaseConfigManager
        
        manager = BaseConfigManager()
        
        # 먼저 현재 값 확인
        current_config = manager._get_config("circuit_breaker")
        current_threshold = current_config.get("failure_threshold", 5)
        
        # 다른 값으로 변경 (현재 값과 다른 값)
        new_threshold = current_threshold + 10 if current_threshold < 90 else current_threshold - 10
        
        with patch.object(manager, "_emit_config_change_audit") as mock_emit:
            with patch.object(manager, "_save_to_history"):
                # failure_threshold 변경
                manager._update_config(
                    "circuit_breaker",
                    changed_by="admin",
                    reason="테스트",
                    failure_threshold=new_threshold,
                )
                
                # 값이 실제로 변경되었으면 audit 발행됨
                if current_threshold != new_threshold:
                    mock_emit.assert_called_once()
                    call_kwargs = mock_emit.call_args[1]
                    assert call_kwargs["config_type"] == "circuit_breaker"
                    assert call_kwargs["changed_by"] == "admin"
                    assert "failure_threshold" in call_kwargs["new_values"]


class TestPydanticSettingsIntegration:
    """Pydantic Settings 모듈 Export 및 통합 테스트."""
    
    def test_layered_provider_and_secrets_exports(self):
        """settings 모듈에서 Layered Provider와 Secrets 기능들이 export됨."""
        from selfhealing.settings import (
            # Layered Provider
            get_layered_settings,
            set_request_override,
            get_request_override,
            clear_request_overrides,
            RequestOverrideContext,
            # Secrets
            SecretsSettings,
            get_secrets,
            reset_secrets,
        )
        
        assert callable(get_layered_settings)
        assert callable(set_request_override)
        assert callable(get_request_override)
        assert callable(clear_request_overrides)
        assert RequestOverrideContext is not None
        assert SecretsSettings is not None
        assert callable(get_secrets)
        assert callable(reset_secrets)
    
    def test_layered_provider_with_context_manager(self):
        """Layered Provider와 Context Manager 통합 테스트."""
        from selfhealing.settings import (
            get_layered_settings,
            RequestOverrideContext,
            clear_request_overrides,
            CircuitBreakerSettings,
        )
        
        clear_request_overrides()
        
        # 기본 값
        settings1 = get_layered_settings(
            CircuitBreakerSettings,
            "circuit_breaker",
            include_runtime=False,
        )
        assert settings1.failure_threshold == 5
        
        # Context Manager로 오버라이드
        with RequestOverrideContext("circuit_breaker", {"failure_threshold": 100}):
            settings2 = get_layered_settings(
                CircuitBreakerSettings,
                "circuit_breaker",
                include_runtime=False,
            )
            assert settings2.failure_threshold == 100
        
        # 컨텍스트 종료 후 원래 값
        settings3 = get_layered_settings(
            CircuitBreakerSettings,
            "circuit_breaker",
            include_runtime=False,
        )
        assert settings3.failure_threshold == 5
