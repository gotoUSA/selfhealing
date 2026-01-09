"""
Phase 0 & Phase 1: Chaos Experiment Expansion Tests

Tests for:
- Phase 0-1: ExperimentType Enum 확장 (6개 신규 타입)
- Phase 0-2: Factory 함수 업데이트
- Phase 1-1: CircuitBreakerOpenExperiment 구현
- Phase 1-4: RateLimitExperiment 구현

Reference: docs/self_healing/middleware_system/31_CHAOS_EXPERIMENT_EXPANSION.md
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.chaos.base import ExperimentType, ExperimentConfig
from selfhealing.services.chaos.experiment_impl import (
    create_experiment,
    CircuitBreakerOpenExperiment,
    RateLimitExperiment,
    LatencyInjectionExperiment,
)


# =============================================================================
# Phase 0-1: ExperimentType Enum 확장 테스트
# =============================================================================


class TestPhase0ExperimentTypeEnum:
    """Phase 0-1: ExperimentType Enum에 6개 신규 타입이 추가되었는지 테스트."""

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
        # Phase 0-1: 6개 타입 추가됨 (31_CHAOS_EXPERIMENT_EXPANSION.md)
        assert ExperimentType.ERROR_4XX.value == "error_4xx"
        assert ExperimentType.CONNECTION_RESET.value == "connection_reset"
        assert ExperimentType.RATE_LIMIT.value == "rate_limit"
        assert ExperimentType.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker_open"
        assert ExperimentType.PARTIAL_FAILURE.value == "partial_failure"
        assert ExperimentType.CASCADING_FAILURE.value == "cascading_failure"

    def test_total_experiment_types_count(self):
        """총 11개 타입이 존재하는지 확인 (기존 5 + 신규 6)."""
        all_types = list(ExperimentType)
        assert len(all_types) == 11


# =============================================================================
# Phase 0-2: Factory 함수 업데이트 테스트
# =============================================================================


class TestPhase0FactoryFunction:
    """Phase 0-2: Factory 함수가 새 타입을 인식하는지 테스트."""

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
        assert "Unknown experiment type" in str(exc_info.value)


# =============================================================================
# Phase 1-1: CircuitBreakerOpenExperiment 테스트
# =============================================================================


class TestPhase1CircuitBreakerOpenExperiment:
    """Phase 1-1: CircuitBreakerOpenExperiment inject/rollback 동작 테스트."""

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

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_inject_chaos_calls_force_open(
        self, mock_get_cb_service, mock_apply_config
    ):
        """inject_chaos가 CB force_open을 호출하는지 확인."""
        # Setup mock
        mock_cb_service = MagicMock()
        mock_cb_service.force_open.return_value = MagicMock(success=True)
        mock_get_cb_service.return_value = mock_cb_service

        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        mock_cb_service.force_open.assert_called_once()
        call_kwargs = mock_cb_service.force_open.call_args[1]
        assert call_kwargs["service_name"] == "test-service"
        assert "Chaos Experiment" in call_kwargs["reason"]
        assert call_kwargs["controlled_by"] == "chaos_engine"

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_inject_chaos_returns_false_on_failure(
        self, mock_get_cb_service, mock_apply_config
    ):
        """CB force_open 실패 시 False 반환."""
        mock_cb_service = MagicMock()
        mock_cb_service.force_open.return_value = MagicMock(
            success=False, message="CB already open"
        )
        mock_get_cb_service.return_value = mock_cb_service

        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is False

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_rollback_calls_force_close(self, mock_get_cb_service, mock_apply_config):
        """rollback이 CB force_close를 호출하는지 확인."""
        mock_cb_service = MagicMock()
        mock_get_cb_service.return_value = mock_cb_service

        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()

        mock_cb_service.force_close.assert_called_once()
        call_kwargs = mock_cb_service.force_close.call_args[1]
        assert call_kwargs["service_name"] == "test-service"
        assert "Rollback" in call_kwargs["reason"]
        assert call_kwargs["trigger_replay"] is False

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_rollback_is_idempotent(self, mock_get_cb_service, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인 (두 번 호출해도 한 번만 실행)."""
        mock_cb_service = MagicMock()
        mock_get_cb_service.return_value = mock_cb_service

        experiment = CircuitBreakerOpenExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        # 첫 번째 호출
        experiment.rollback()
        assert experiment._rollback_completed is True

        # 두 번째 호출 - force_close가 다시 호출되지 않아야 함
        experiment.rollback()

        # force_close는 한 번만 호출되어야 함
        assert mock_cb_service.force_close.call_count == 1


# =============================================================================
# Phase 1-4: RateLimitExperiment 테스트
# =============================================================================


class TestPhase1RateLimitExperiment:
    """Phase 1-4: RateLimitExperiment inject/rollback 동작 테스트."""

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

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_rate_limit_tracker")
    def test_inject_chaos_records_rate_limits(
        self, mock_get_tracker, mock_apply_config
    ):
        """inject_chaos가 rate limit tracker에 기록하는지 확인."""
        mock_tracker = MagicMock()
        mock_get_tracker.return_value = mock_tracker

        experiment = RateLimitExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"rate_limit_count": 5},
            ),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        # rate_limit_count (5) 번 호출되어야 함
        assert mock_tracker.record_rate_limit.call_count == 5

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_rate_limit_tracker")
    def test_inject_chaos_passes_correct_parameters(
        self, mock_get_tracker, mock_apply_config
    ):
        """inject_chaos가 올바른 파라미터를 전달하는지 확인."""
        mock_tracker = MagicMock()
        mock_get_tracker.return_value = mock_tracker

        experiment = RateLimitExperiment(
            config=ExperimentConfig(
                target_service="payment-service",
                parameters={"rate_limit_count": 1, "retry_after_seconds": 45},
            ),
        )
        experiment._calculate_expires_at()

        experiment.inject_chaos()

        mock_tracker.record_rate_limit.assert_called_once_with(
            service_name="payment-service",
            retry_after=45,
        )

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply_config):
        """rollback이 설정을 해제하는지 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()

        # _apply_chaos_config가 enabled=False로 호출되었는지 확인
        call_args = mock_apply_config.call_args[0][0]
        assert call_args["rate_limit_injection"]["enabled"] is False

    @patch("selfhealing.services.chaos.experiment_impl._apply_chaos_config")
    def test_rollback_is_idempotent(self, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인."""
        experiment = RateLimitExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        # 첫 번째 호출
        experiment.rollback()
        assert experiment._rollback_completed is True

        # 두 번째 호출
        experiment.rollback()

        # _apply_chaos_config는 한 번만 호출되어야 함
        assert mock_apply_config.call_count == 1


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
