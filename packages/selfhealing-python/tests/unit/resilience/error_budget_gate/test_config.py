"""
ErrorBudgetGateConfig 테스트.

설정 객체의 생성, 변환, 직렬화 테스트.
"""



class TestErrorBudgetGateConfig:
    """ErrorBudgetGateConfig 테스트."""

    def test_default_config(self):
        """기본 설정 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig()

        assert config.enabled is True
        assert config.critical_threshold_percent == 10.0
        assert config.warning_threshold_percent == 20.0
        assert config.fail_open is True
        assert config.cache_ttl_seconds == 30

    def test_custom_config(self):
        """커스텀 설정 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig(
            enabled=False,
            critical_threshold_percent=15.0,
            warning_threshold_percent=30.0,
            fail_open=False,
            cache_ttl_seconds=60,
        )

        assert config.enabled is False
        assert config.critical_threshold_percent == 15.0
        assert config.warning_threshold_percent == 30.0
        assert config.fail_open is False
        assert config.cache_ttl_seconds == 60

    def test_config_to_dict(self):
        """설정 딕셔너리 변환 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig()
        config_dict = config.to_dict()

        assert "enabled" in config_dict
        assert "critical_threshold_percent" in config_dict
        assert "warning_threshold_percent" in config_dict
        assert "fail_open" in config_dict
        assert "cache_ttl_seconds" in config_dict

    def test_config_from_dict(self):
        """딕셔너리에서 설정 생성 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        data = {
            "enabled": False,
            "critical_threshold_percent": 5.0,
            "warning_threshold_percent": 15.0,
            "fail_open": False,
            "cache_ttl_seconds": 120,
        }

        config = ErrorBudgetGateConfig.from_dict(data)

        assert config.enabled is False
        assert config.critical_threshold_percent == 5.0
        assert config.warning_threshold_percent == 15.0
        assert config.fail_open is False
        assert config.cache_ttl_seconds == 120


class TestConfigNewFields:
    """새로 추가된 설정 필드 테스트."""

    def test_config_circuit_breaker_fields(self):
        """Circuit Breaker 설정 필드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig(
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=10,
            circuit_breaker_recovery_timeout=60,
        )

        assert config.circuit_breaker_enabled is True
        assert config.circuit_breaker_failure_threshold == 10
        assert config.circuit_breaker_recovery_timeout == 60

    def test_config_alert_fields(self):
        """알림 설정 필드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig(
            alert_on_fail_open=True,
            alert_cooldown_seconds=600,
        )

        assert config.alert_on_fail_open is True
        assert config.alert_cooldown_seconds == 600

    def test_config_to_dict_all_fields(self):
        """to_dict에 모든 필드 포함."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig()
        config_dict = config.to_dict()

        assert "circuit_breaker_enabled" in config_dict
        assert "circuit_breaker_failure_threshold" in config_dict
        assert "circuit_breaker_recovery_timeout" in config_dict
        assert "alert_on_fail_open" in config_dict
        assert "alert_cooldown_seconds" in config_dict

    def test_config_from_dict_all_fields(self):
        """from_dict에서 모든 필드 로드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        data = {
            "enabled": True,
            "circuit_breaker_enabled": False,
            "circuit_breaker_failure_threshold": 7,
            "circuit_breaker_recovery_timeout": 45,
            "alert_on_fail_open": False,
            "alert_cooldown_seconds": 120,
        }

        config = ErrorBudgetGateConfig.from_dict(data)

        assert config.circuit_breaker_enabled is False
        assert config.circuit_breaker_failure_threshold == 7
        assert config.circuit_breaker_recovery_timeout == 45
        assert config.alert_on_fail_open is False
        assert config.alert_cooldown_seconds == 120
