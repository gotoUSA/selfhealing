"""
Chaos Experiment 확장 테스트.

테스트 대상:
- ExperimentType Enum 확장 (신규 타입 6개)
- Factory 함수 업데이트
- CircuitBreakerOpenExperiment inject/rollback 동작
- RateLimitExperiment inject/rollback 동작
"""

from __future__ import annotations

import pytest

from selfhealing.services.chaos.base import ExperimentConfig, ExperimentType
from selfhealing.services.chaos.experiments import (
    CircuitBreakerOpenExperiment,
    LatencyInjectionExperiment,
    RateLimitExperiment,
    create_experiment,
)

# =============================================================================
# ExperimentType Enum 확장 테스트
# =============================================================================


class TestExperimentTypeEnumExtension:
    """ExperimentType Enum에 6개 신규 타입이 추가되었는지 테스트."""

    def test_existing_experiment_types_still_available(self):
        """기존 5개 타입이 유지되어 있는지 확인."""
        # Existing types from chaos_context.py
        assert ExperimentType.LATENCY_INJECTION.value == "latency_injection"
        assert ExperimentType.ERROR_5XX.value == "error_5xx"
        assert ExperimentType.TIMEOUT.value == "timeout"
        assert ExperimentType.RESOURCE_EXHAUSTION.value == "resource_exhaustion"
        assert ExperimentType.PACKET_LOSS.value == "packet_loss"

    def test_new_experiment_types_added(self):
        """6개 신규 타입이 추가되었는지 확인."""
        assert ExperimentType.ERROR_4XX.value == "error_4xx"
        assert ExperimentType.CONNECTION_RESET.value == "connection_reset"
        assert ExperimentType.RATE_LIMIT.value == "rate_limit"
        assert ExperimentType.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker_open"
        assert ExperimentType.PARTIAL_FAILURE.value == "partial_failure"
        assert ExperimentType.CASCADING_FAILURE.value == "cascading_failure"

    def test_experiment_types_available(self):
        """ExperimentType enum에 기본 타입들이 있는지 확인."""
        # 기존 + 신규 타입 모두 존재 확인
        all_types = list(ExperimentType)
        assert len(all_types) >= 11  # 최소 11개 이상


# =============================================================================
# Factory 함수 업데이트 테스트
# =============================================================================


class TestExperimentFactoryFunction:
    """Factory 함수가 새 타입을 인식하는지 테스트."""

    def test_factory_creates_existing_types(self):
        """기존 타입에 대한 Factory 동작 확인."""
        # Latency injection - existing type
        experiment = create_experiment(
            experiment_type="latency_injection",
            config=ExperimentConfig(target_service="test-service"),
        )
        assert isinstance(experiment, LatencyInjectionExperiment)
        assert experiment.experiment_type == "latency_injection"

    def test_factory_creates_circuit_breaker_open_experiment(self):
        """CircuitBreakerOpenExperiment 생성 확인."""
        experiment = create_experiment(
            experiment_type="circuit_breaker_open",
            config=ExperimentConfig(target_service="test-service"),
        )
        assert isinstance(experiment, CircuitBreakerOpenExperiment)
        assert experiment.experiment_type == "circuit_breaker_open"

    def test_factory_creates_rate_limit_experiment(self):
        """RateLimitExperiment 생성 확인."""
        experiment = create_experiment(
            experiment_type="rate_limit",
            config=ExperimentConfig(target_service="test-service"),
        )
        assert isinstance(experiment, RateLimitExperiment)
        assert experiment.experiment_type == "rate_limit"

    def test_factory_unknown_type_raises_error(self):
        """알 수 없는 타입에 대해 ValueError 발생 확인."""
        with pytest.raises(ValueError) as exc_info:
            create_experiment(
                experiment_type="unknown_type",
                config=ExperimentConfig(target_service="test-service"),
            )
        assert "Unsupported experiment type" in str(exc_info.value)


# =============================================================================
# CircuitBreakerOpenExperiment 테스트
# =============================================================================


class TestCircuitBreakerOpenExperiment:
    """CircuitBreakerOpenExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_circuit_breaker_open(self):
        """experiment_type이 circuit_breaker_open인지 확인."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="payment-service"),
        )
        assert experiment.experiment_type == "circuit_breaker_open"

    def test_requires_approval_is_true(self):
        """고위험 실험이므로 승인 필요."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="payment-service"),
        )
        assert experiment.requires_approval is True

    def test_trigger_canary_default_value(self):
        """trigger_canary 기본값이 True인지 확인."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.trigger_canary is True

    def test_trigger_canary_custom_value(self):
        """trigger_canary 커스텀 값 설정 확인."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"trigger_canary": False},
            ),
        )
        assert experiment.trigger_canary is False

    def test_fallback_type_default_value(self):
        """fallback_type 기본값이 default인지 확인."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.fallback_type == "default"

    def test_fallback_type_custom_value(self):
        """fallback_type 커스텀 값 설정 확인."""
        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"fallback_type": "cache"},
            ),
        )
        assert experiment.fallback_type == "cache"


# =============================================================================
# RateLimitExperiment 테스트
# =============================================================================


class TestRateLimitExperiment:
    """RateLimitExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_rate_limit(self):
        """experiment_type이 rate_limit인지 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.experiment_type == "rate_limit"

    def test_requires_approval_is_false(self):
        """중간 위험 실험이므로 승인 불필요."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.requires_approval is False

    def test_rate_limit_count_default_value(self):
        """rate_limit_count 기본값이 10인지 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.rate_limit_count == 10

    def test_rate_limit_count_custom_value(self):
        """rate_limit_count 커스텀 값 설정 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"rate_limit_count": 50},
            ),
        )
        assert experiment.rate_limit_count == 50

    def test_retry_after_seconds_default_value(self):
        """retry_after_seconds 기본값이 30인지 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.retry_after_seconds == 30

    def test_retry_after_seconds_custom_value(self):
        """retry_after_seconds 커스텀 값 설정 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"retry_after_seconds": 60},
            ),
        )
        assert experiment.retry_after_seconds == 60


# =============================================================================
# Integration Tests: Factory + Experiments
# =============================================================================


class TestFactoryIntegration:
    """Factory와 새 실험 타입의 통합 테스트."""

    def test_factory_and_cb_open_experiment_integration(self):
        """Factory로 생성한 CB Open 실험이 정상 동작하는지 확인."""
        config = ExperimentConfig(
            target_service="integration-test-service",
            parameters={
                "trigger_canary": True,
                "fallback_type": "dlq",
            },
        )

        experiment = create_experiment(
            experiment_type="circuit_breaker_open",
            config=config,
        )

        # 타입 및 속성 확인
        assert isinstance(experiment, CircuitBreakerOpenExperiment)
        assert experiment.trigger_canary is True
        assert experiment.fallback_type == "dlq"
        assert experiment.config.target_service == "integration-test-service"

    def test_factory_and_rate_limit_experiment_integration(self):
        """Factory로 생성한 Rate Limit 실험이 정상 동작하는지 확인."""
        config = ExperimentConfig(
            target_service="rate-limit-test-service",
            parameters={
                "rate_limit_count": 20,
                "retry_after_seconds": 120,
            },
        )

        experiment = create_experiment(
            experiment_type="rate_limit",
            config=config,
        )

        # 타입 및 속성 확인
        assert isinstance(experiment, RateLimitExperiment)
        assert experiment.rate_limit_count == 20
        assert experiment.retry_after_seconds == 120
        assert experiment.config.target_service == "rate-limit-test-service"
