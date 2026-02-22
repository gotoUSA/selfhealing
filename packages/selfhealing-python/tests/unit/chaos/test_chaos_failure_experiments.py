"""
Chaos Failure Experiment Expansion Tests

Tests for:
- Error4xxExperiment 구현
- PartialFailureExperiment 구현
- ConnectionResetExperiment 구현
- CascadingFailureExperiment 구현
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.chaos.base import ExperimentConfig, ExperimentType
from selfhealing.services.chaos.experiments import (
    CascadingFailureExperiment,
    ConnectionResetExperiment,
    Error4xxExperiment,
    PartialFailureExperiment,
    create_experiment,
)

# =============================================================================
# Phase 2-1: Error4xxExperiment 테스트
# =============================================================================


class TestPhase2Error4xxExperiment:
    """Phase 2-1: Error4xxExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_error_4xx(self):
        """experiment_type이 error_4xx인지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.experiment_type == "error_4xx"

    def test_requires_approval_is_false(self):
        """저위험 실험이므로 승인 불필요."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.requires_approval is False

    def test_error_code_default_value(self):
        """error_code 기본값이 400인지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.error_code == 400

    def test_error_code_custom_value(self):
        """error_code 커스텀 값 설정 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 404},
            ),
        )
        assert experiment.error_code == 404

    def test_error_codes_list_selection(self):
        """error_codes 리스트에서 랜덤 선택 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_codes": [400, 401, 403, 404, 429]},
            ),
        )
        # error_codes가 있으면 그 중에서 선택됨
        assert experiment.error_code in [400, 401, 403, 404, 429]

    def test_error_message_default_for_400(self):
        """400 에러에 대한 기본 메시지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 400},
            ),
        )
        assert "Bad Request" in experiment.error_message
        assert "Chaos Experiment" in experiment.error_message

    def test_error_message_default_for_401(self):
        """401 에러에 대한 기본 메시지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 401},
            ),
        )
        assert "Unauthorized" in experiment.error_message

    def test_error_message_default_for_403(self):
        """403 에러에 대한 기본 메시지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 403},
            ),
        )
        assert "Forbidden" in experiment.error_message

    def test_error_message_default_for_404(self):
        """404 에러에 대한 기본 메시지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 404},
            ),
        )
        assert "Not Found" in experiment.error_message

    def test_error_message_default_for_429(self):
        """429 에러에 대한 기본 메시지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 429},
            ),
        )
        assert "Too Many Requests" in experiment.error_message

    def test_error_message_custom_value(self):
        """error_message 커스텀 값 설정 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_message": "Custom Error Message"},
            ),
        )
        assert experiment.error_message == "Custom Error Message"

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    def test_inject_chaos_applies_config(self, mock_apply_config):
        """inject_chaos가 설정을 적용하는지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"error_code": 429},
            ),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        call_args = mock_apply_config.call_args[0][0]
        assert call_args["error_4xx_injection"]["enabled"] is True
        assert call_args["error_4xx_injection"]["error_code"] == 429
        assert call_args["error_4xx_injection"]["target_service"] == "test-service"

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply_config):
        """rollback이 설정을 해제하는지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()

        call_args = mock_apply_config.call_args[0][0]
        assert call_args["error_4xx_injection"]["enabled"] is False

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    def test_rollback_is_idempotent(self, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인."""
        experiment = Error4xxExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()
        experiment.rollback()

        assert mock_apply_config.call_count == 1


# =============================================================================
# Phase 2-4: PartialFailureExperiment 테스트
# =============================================================================


class TestPhase2PartialFailureExperiment:
    """Phase 2-4: PartialFailureExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_partial_failure(self):
        """experiment_type이 partial_failure인지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.experiment_type == "partial_failure"

    def test_requires_approval_is_true(self):
        """고위험 실험이므로 승인 필요."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.requires_approval is True

    def test_failure_rate_default_value(self):
        """failure_rate 기본값이 0.30 (30%)인지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.failure_rate == 0.30

    def test_failure_rate_custom_value(self):
        """failure_rate 커스텀 값 설정 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"failure_rate": 0.50},
            ),
        )
        assert experiment.failure_rate == 0.50

    def test_affected_endpoints_default_value(self):
        """affected_endpoints 기본값이 빈 리스트인지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.affected_endpoints == []

    def test_affected_endpoints_custom_value(self):
        """affected_endpoints 커스텀 값 설정 확인."""
        endpoints = ["/api/v1/users", "/api/v1/orders"]
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"affected_endpoints": endpoints},
            ),
        )
        assert experiment.affected_endpoints == endpoints

    def test_trigger_shedding_default_value(self):
        """trigger_shedding 기본값이 True인지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.trigger_shedding is True

    def test_trigger_shedding_custom_value(self):
        """trigger_shedding 커스텀 값 설정 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"trigger_shedding": False},
            ),
        )
        assert experiment.trigger_shedding is False

    @patch("selfhealing.services.chaos.experiments.cascade._apply_chaos_config")
    def test_inject_chaos_applies_config(self, mock_apply_config):
        """inject_chaos가 설정을 적용하는지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={
                    "failure_rate": 0.40,
                    "affected_endpoints": ["/api/test"],
                },
            ),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        call_args = mock_apply_config.call_args[0][0]
        assert call_args["partial_failure"]["enabled"] is True
        assert call_args["partial_failure"]["failure_rate"] == 0.40
        assert call_args["partial_failure"]["affected_endpoints"] == ["/api/test"]

    @patch("selfhealing.services.chaos.experiments.cascade._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply_config):
        """rollback이 설정을 해제하는지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()

        call_args = mock_apply_config.call_args[0][0]
        assert call_args["partial_failure"]["enabled"] is False

    @patch("selfhealing.services.chaos.experiments.cascade._apply_chaos_config")
    def test_rollback_is_idempotent(self, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인."""
        experiment = PartialFailureExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()
        experiment.rollback()

        assert mock_apply_config.call_count == 1


# =============================================================================
# Phase 3-1: ConnectionResetExperiment 테스트
# =============================================================================


class TestPhase3ConnectionResetExperiment:
    """Phase 3-1: ConnectionResetExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_connection_reset(self):
        """experiment_type이 connection_reset인지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.experiment_type == "connection_reset"

    def test_requires_approval_is_true(self):
        """중-고위험 실험이므로 승인 필요."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="api-service"),
        )
        assert experiment.requires_approval is True

    def test_reset_after_bytes_default_value(self):
        """reset_after_bytes 기본값이 0 (즉시 리셋)인지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.reset_after_bytes == 0

    def test_reset_after_bytes_custom_value(self):
        """reset_after_bytes 커스텀 값 설정 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"reset_after_bytes": 1024},
            ),
        )
        assert experiment.reset_after_bytes == 1024

    def test_reset_probability_default_value(self):
        """reset_probability 기본값이 0.5 (50%)인지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )
        assert experiment.reset_probability == 0.5

    def test_reset_probability_custom_value(self):
        """reset_probability 커스텀 값 설정 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={"reset_probability": 0.75},
            ),
        )
        assert experiment.reset_probability == 0.75

    @patch("selfhealing.services.chaos.experiments.network._apply_chaos_config")
    def test_inject_chaos_applies_config(self, mock_apply_config):
        """inject_chaos가 설정을 적용하는지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(
                target_service="test-service",
                parameters={
                    "reset_after_bytes": 512,
                    "reset_probability": 0.80,
                },
            ),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        call_args = mock_apply_config.call_args[0][0]
        assert call_args["connection_reset"]["enabled"] is True
        assert call_args["connection_reset"]["reset_after_bytes"] == 512
        assert call_args["connection_reset"]["reset_probability"] == 0.80

    @patch("selfhealing.services.chaos.experiments.network._apply_chaos_config")
    def test_rollback_clears_config(self, mock_apply_config):
        """rollback이 설정을 해제하는지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()

        call_args = mock_apply_config.call_args[0][0]
        assert call_args["connection_reset"]["enabled"] is False

    @patch("selfhealing.services.chaos.experiments.network._apply_chaos_config")
    def test_rollback_is_idempotent(self, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인."""
        experiment = ConnectionResetExperiment(
            config=ExperimentConfig(target_service="test-service"),
        )

        experiment.rollback()
        experiment.rollback()

        assert mock_apply_config.call_count == 1


# =============================================================================
# Phase 3-4: CascadingFailureExperiment 테스트
# =============================================================================


class TestPhase3CascadingFailureExperiment:
    """Phase 3-4: CascadingFailureExperiment inject/rollback 동작 테스트."""

    def test_experiment_type_is_cascading_failure(self):
        """experiment_type이 cascading_failure인지 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        assert experiment.experiment_type == "cascading_failure"

    def test_requires_approval_is_true(self):
        """고위험 실험이므로 승인 필요."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        assert experiment.requires_approval is True

    def test_affected_services_default_value(self):
        """affected_services 기본값이 빈 리스트인지 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        assert experiment.affected_services == []

    def test_affected_services_custom_value(self):
        """affected_services 커스텀 값 설정 확인."""
        services = ["service-a", "service-b", "service-c"]
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={"affected_services": services},
            ),
        )
        assert experiment.affected_services == services

    def test_cascade_delay_seconds_default_value(self):
        """cascade_delay_seconds 기본값이 5인지 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        assert experiment.cascade_delay_seconds == 5

    def test_cascade_delay_seconds_custom_value(self):
        """cascade_delay_seconds 커스텀 값 설정 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={"cascade_delay_seconds": 10},
            ),
        )
        assert experiment.cascade_delay_seconds == 10

    def test_target_open_percent_default_value(self):
        """target_open_percent 기본값이 75.0인지 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        assert experiment.target_open_percent == 75.0

    def test_target_open_percent_custom_value(self):
        """target_open_percent 커스텀 값 설정 확인."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={"target_open_percent": 80.0},
            ),
        )
        assert experiment.target_open_percent == 80.0

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    def test_inject_chaos_returns_false_without_services(self, mock_apply_config):
        """affected_services가 없으면 False 반환."""
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(target_service="system"),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is False

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_inject_chaos_opens_all_services(
        self, mock_get_cb_service, mock_apply_config
    ):
        """inject_chaos가 모든 서비스의 CB를 OPEN하는지 확인."""
        mock_cb_service = MagicMock()
        mock_cb_service.force_open.return_value = MagicMock(success=True)
        mock_get_cb_service.return_value = mock_cb_service

        services = ["service-a", "service-b", "service-c"]
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={
                    "affected_services": services,
                    "cascade_delay_seconds": 0,  # 테스트 시 지연 없음
                },
            ),
        )
        experiment._calculate_expires_at()

        result = experiment.inject_chaos()

        assert result is True
        # 3개 서비스 모두 force_open 호출
        assert mock_cb_service.force_open.call_count == 3

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_inject_chaos_respects_kill_switch(
        self, mock_get_cb_service, mock_apply_config
    ):
        """kill switch가 활성화되면 주입을 중단하는지 확인."""
        mock_cb_service = MagicMock()
        mock_cb_service.force_open.return_value = MagicMock(success=True)
        mock_get_cb_service.return_value = mock_cb_service

        services = ["service-a", "service-b", "service-c"]
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={
                    "affected_services": services,
                    "cascade_delay_seconds": 0,
                },
            ),
        )
        experiment._calculate_expires_at()

        # 첫 번째 서비스 후 kill 요청
        def side_effect(*args, **kwargs):
            if mock_cb_service.force_open.call_count == 1:
                experiment._kill_requested = True
            return MagicMock(success=True)

        mock_cb_service.force_open.side_effect = side_effect

        result = experiment.inject_chaos()

        # kill switch로 인해 1개만 OPEN됨
        assert mock_cb_service.force_open.call_count == 1

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_rollback_closes_all_services(
        self, mock_get_cb_service, mock_apply_config
    ):
        """rollback이 모든 서비스의 CB를 CLOSE하는지 확인."""
        mock_cb_service = MagicMock()
        mock_get_cb_service.return_value = mock_cb_service

        services = ["service-a", "service-b", "service-c"]
        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={"affected_services": services},
            ),
        )

        experiment.rollback()

        # 3개 서비스 모두 force_close 호출
        assert mock_cb_service.force_close.call_count == 3

    @patch("selfhealing.services.chaos.experiments.http_errors._apply_chaos_config")
    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_rollback_is_idempotent(self, mock_get_cb_service, mock_apply_config):
        """rollback이 멱등성을 가지는지 확인."""
        mock_cb_service = MagicMock()
        mock_get_cb_service.return_value = mock_cb_service

        experiment = CascadingFailureExperiment(
            config=ExperimentConfig(
                target_service="system",
                parameters={"affected_services": ["service-a"]},
            ),
        )

        experiment.rollback()
        experiment.rollback()

        # force_close는 한 번만 호출
        assert mock_cb_service.force_close.call_count == 1


# =============================================================================
# Factory Integration Tests for Phase 2 & 3
# =============================================================================


class TestPhase2Phase3FactoryIntegration:
    """Factory와 Phase 2/3 실험 타입의 통합 테스트."""

    def test_all_phase2_phase3_types_in_factory(self):
        """모든 Phase 2/3 타입이 Factory에 등록되어 있는지 확인."""
        phase2_phase3_types = [
            ExperimentType.ERROR_4XX.value,
            ExperimentType.PARTIAL_FAILURE.value,
            ExperimentType.CONNECTION_RESET.value,
            ExperimentType.CASCADING_FAILURE.value,
        ]

        for exp_type in phase2_phase3_types:
            experiment = create_experiment(
                experiment_type=exp_type,
                config=ExperimentConfig(target_service="test"),
            )
            assert experiment is not None
            assert experiment.experiment_type == exp_type
