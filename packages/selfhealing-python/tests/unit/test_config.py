"""
Unit tests for configuration management.
"""

import pytest
from datetime import timedelta


class TestSelfHealingConfig:
    """Tests for SelfHealingConfig."""

    def test_default_config(self):
        from selfhealing.core.config import SelfHealingConfig

        config = SelfHealingConfig()

        assert config.circuit_breaker.failure_threshold == 5
        assert config.circuit_breaker.recovery_timeout == 60
        assert config.dlq.max_retries == 3
        assert config.retry.backoff_strategy == "exponential"

    def test_from_dict(self):
        from selfhealing.core.config import SelfHealingConfig

        config_dict = {
            "circuit_breaker": {
                "failure_threshold": 10,
                "recovery_timeout": 120,
            },
            "dlq": {
                "max_retries": 5,
            },
            "auto_replay_enabled": False,
        }

        config = SelfHealingConfig.from_dict(config_dict)

        assert config.circuit_breaker.failure_threshold == 10
        assert config.dlq.max_retries == 5
        assert config.auto_replay_enabled is False

    def test_to_dict(self):
        from selfhealing.core.config import SelfHealingConfig

        config = SelfHealingConfig()
        config_dict = config.to_dict()

        assert "circuit_breaker" in config_dict
        assert "dlq" in config_dict
        assert config_dict["circuit_breaker"]["failure_threshold"] == 5

    def test_domain_specific_config(self):
        from selfhealing.core.config import SelfHealingConfig

        config = SelfHealingConfig(
            domain_configs={
                "payment": {
                    "circuit_breaker": {
                        "failure_threshold": 3,
                    }
                }
            }
        )

        # Default config
        default_cb = config.get_circuit_breaker_config()
        assert default_cb.failure_threshold == 5

        # Payment domain config
        payment_cb = config.get_circuit_breaker_config(domain="payment")
        assert payment_cb.failure_threshold == 3

    def test_get_retry_config_with_domain(self):
        from selfhealing.core.config import SelfHealingConfig

        config = SelfHealingConfig(
            domain_configs={
                "webhook": {
                    "retry": {
                        "max_attempts": 5,
                        "backoff_base": 2,
                    }
                }
            }
        )

        # Default retry config
        default_retry = config.get_retry_config()
        assert default_retry.max_attempts == 3

        # Webhook domain config
        webhook_retry = config.get_retry_config(domain="webhook")
        assert webhook_retry.max_attempts == 5
        assert webhook_retry.backoff_base == 2


class TestGlobalConfig:
    """Tests for global configuration functions."""

    def test_get_config_creates_default(self):
        from selfhealing.core.config import get_config, set_config

        # Reset global config
        set_config(None)

        config = get_config()
        assert config is not None
        assert config.circuit_breaker.failure_threshold == 5

    def test_set_config(self):
        from selfhealing.core.config import SelfHealingConfig, get_config, set_config

        custom_config = SelfHealingConfig()
        custom_config.debug_mode = True

        set_config(custom_config)

        retrieved = get_config()
        assert retrieved.debug_mode is True

    def test_configure_helper(self):
        from selfhealing.core.config import configure, get_config

        configure(
            debug_mode=True,
            auto_replay_enabled=False,
        )

        config = get_config()
        assert config.debug_mode is True
        assert config.auto_replay_enabled is False

    def test_reload_config(self):
        from selfhealing.core.config import (
            SelfHealingConfig,
            get_config,
            set_config,
            reload_config,
        )

        # Set custom config
        custom = SelfHealingConfig()
        custom.debug_mode = True
        set_config(custom)

        assert get_config().debug_mode is True

        # Reload resets to defaults
        reload_config()
        assert get_config().debug_mode is False


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig."""

    def test_defaults(self):
        from selfhealing.core.config import CircuitBreakerConfig

        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60
        assert config.half_open_max_calls == 3
        assert config.enabled is True

    def test_custom_values(self):
        from selfhealing.core.config import CircuitBreakerConfig

        config = CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout=300,
        )

        assert config.failure_threshold == 10
        assert config.recovery_timeout == 300

    def test_rate_limit_cascade_settings(self):
        from selfhealing.core.config import CircuitBreakerConfig

        config = CircuitBreakerConfig()

        assert config.rate_limit_cascade_threshold == 10
        assert config.rate_limit_cascade_window_seconds == 60

    def test_self_ddos_settings(self):
        from selfhealing.core.config import CircuitBreakerConfig

        config = CircuitBreakerConfig()

        assert config.self_ddos_protection_enabled is True
        assert config.self_ddos_request_threshold == 100


class TestDLQConfig:
    """Tests for DLQConfig."""

    def test_defaults(self):
        from selfhealing.core.config import DLQConfig

        config = DLQConfig()

        assert config.max_retries == 3
        assert config.retry_delay == 60
        assert config.expiry_hours == 72
        assert config.batch_size == 10
        assert config.enabled is True
        assert config.retention_days == 30


class TestRetryConfig:
    """Tests for RetryConfig."""

    def test_defaults(self):
        from selfhealing.core.config import RetryConfig

        config = RetryConfig()

        assert config.max_attempts == 3
        assert config.backoff_strategy == "exponential"
        assert config.backoff_base == 4
        assert config.jitter is True
        assert config.jitter_percent == 25


class TestSLAConfig:
    """Tests for SLAConfig."""

    def test_defaults(self):
        from selfhealing.core.config import SLAConfig

        config = SLAConfig()

        assert config.payment_hours == 1
        assert config.point_hours == 4
        assert config.inventory_hours == 2
        assert config.default_hours == 24

    def test_get_threshold(self):
        from selfhealing.core.config import SLAConfig

        config = SLAConfig()

        payment_threshold = config.get_threshold("payment")
        assert payment_threshold == timedelta(hours=1)

        unknown_threshold = config.get_threshold("unknown")
        assert unknown_threshold == timedelta(hours=24)  # default


class TestConvenienceGetters:
    """Tests for convenience getter functions."""

    def test_get_circuit_breaker_settings(self):
        from selfhealing.core.config import (
            set_config,
            SelfHealingConfig,
            get_circuit_breaker_settings,
        )

        set_config(None)  # Reset

        cb = get_circuit_breaker_settings()
        assert cb.failure_threshold == 5

    def test_get_dlq_settings(self):
        from selfhealing.core.config import (
            set_config,
            get_dlq_settings,
        )

        set_config(None)  # Reset

        dlq = get_dlq_settings()
        assert dlq.max_retries == 3

    def test_get_retry_settings(self):
        from selfhealing.core.config import (
            set_config,
            get_retry_settings,
        )

        set_config(None)  # Reset

        retry = get_retry_settings()
        assert retry.max_attempts == 3

    def test_get_sla_thresholds(self):
        from selfhealing.core.config import (
            set_config,
            get_sla_thresholds,
        )

        set_config(None)  # Reset

        sla = get_sla_thresholds()
        assert sla.payment_hours == 1

    def test_get_forensic_settings(self):
        from selfhealing.core.config import (
            set_config,
            get_forensic_settings,
        )

        set_config(None)  # Reset

        forensic = get_forensic_settings()
        assert forensic.error_message_max_length == 500


class TestSecurityConfig:
    """Tests for SecurityConfig."""

    def test_defaults(self):
        from selfhealing.core.config import SecurityConfig

        config = SecurityConfig()

        assert config.rate_limit_window_seconds == 60
        assert config.rate_limit_max_requests == 100
        assert config.temporary_ban_hours == 1
        assert config.permanent_ban_threshold == 5


class TestForensicConfig:
    """Tests for ForensicConfig."""

    def test_defaults(self):
        from selfhealing.core.config import ForensicConfig

        config = ForensicConfig()

        assert config.error_message_max_length == 500
        assert config.response_body_max_length == 5000
        assert config.user_agent_max_length == 500
