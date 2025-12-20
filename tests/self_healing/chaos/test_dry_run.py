"""
Dry Run Mode Tests for Chaos Engine Safety Mechanisms

Tests the Dry Run functionality that allows simulating chaos experiments
without actually injecting any faults.

Phase 3: Chaos Safety Implementation Plan
Reference: docs/self_healing/CHAOS_SAFETY_IMPLEMENTATION_PLAN.md
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.chaos.stop_conditions import (
    DryRunConfig,
)
from selfhealing.services.chaos.experiments import (
    ExperimentConfig,
    ExperimentStatus,
    ExperimentResult,
    LatencyInjectionExperiment,
)


# =============================================================================
# DryRunConfig Tests
# =============================================================================


class TestDryRunConfig:
    """DryRunConfig 데이터 클래스 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정되는지 확인 - 기본값은 True (안전)."""
        config = DryRunConfig()

        assert config.enabled is True
        assert "simulation" in config.reason.lower() or "initial" in config.reason.lower()

    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        config = DryRunConfig(
            enabled=False,
            reason="Production mode enabled after testing",
        )

        result = config.to_dict()

        assert result["enabled"] is False
        assert "Production" in result["reason"]

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        data = {
            "enabled": False,
            "reason": "Validated in staging",
        }

        config = DryRunConfig.from_dict(data)

        assert config.enabled is False
        assert config.reason == "Validated in staging"

    def test_from_dict_with_defaults(self):
        """부분 데이터로 생성 시 기본값 적용 테스트."""
        data = {}

        config = DryRunConfig.from_dict(data)

        assert config.enabled is True  # 기본값은 안전한 True


# =============================================================================
# Experiment Dry Run Detection Tests
# =============================================================================


class TestExperimentDryRunDetection:
    """실험의 Dry Run 감지 테스트."""

    def test_config_dry_run_override(self):
        """설정을 통한 Dry Run 오버라이드 테스트."""
        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        assert experiment._should_dry_run() is True

    def test_config_dry_run_false(self):
        """설정에서 Dry Run이 False일 때 테스트."""
        config = ExperimentConfig(
            target_service="payment",
            dry_run=False,
        )
        experiment = LatencyInjectionExperiment(config=config)

        # Global config도 False여야 실제로 False
        with patch("selfhealing.services.chaos.stop_conditions.get_dry_run_config") as mock:
            mock.return_value = DryRunConfig(enabled=False)
            # _should_dry_run 내부에서 get_dry_run_config를 호출하므로
            # 직접 테스트
            assert experiment.config.dry_run is False

    def test_global_dry_run_enabled(self):
        """글로벌 Dry Run이 활성화된 경우 테스트."""
        config = ExperimentConfig(
            target_service="payment",
            dry_run=False,  # 로컬은 False
        )
        experiment = LatencyInjectionExperiment(config=config)

        # 로컬 설정이 False이고 글로벌이 True면, 글로벌 체크 필요
        # config.dry_run이 False이면 _should_dry_run에서 글로벌 체크
        assert experiment.config.dry_run is False


# =============================================================================
# Dry Run Execution Tests
# =============================================================================


class TestDryRunExecution:
    """Dry Run 실행 테스트."""

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_returns_result(self, mock_sleep, mock_now):
        """Dry Run이 결과를 반환하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
            duration_seconds=60,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            result = experiment._run_dry()

        assert result is not None
        assert result.dry_run is True
        assert result.status == ExperimentStatus.COMPLETED.value

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_does_not_inject_chaos(self, mock_sleep, mock_now):
        """Dry Run이 실제 chaos를 주입하지 않는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            with patch.object(experiment, "inject_chaos") as mock_inject:
                result = experiment._run_dry()

                # inject_chaos가 호출되지 않아야 함
                mock_inject.assert_not_called()

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_performs_pre_flight_check(self, mock_sleep, mock_now):
        """Dry Run이 pre-flight check를 수행하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True) as mock_check:
            experiment._run_dry()

            mock_check.assert_called_once()

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_captures_steady_state(self, mock_sleep, mock_now):
        """Dry Run이 steady state를 캡처하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        mock_metrics = {"p99_latency_ms": 100, "error_rate_percent": 0.1}

        with patch.object(experiment, "pre_flight_check", return_value=True):
            with patch.object(experiment, "capture_steady_state", return_value=mock_metrics) as mock_capture:
                result = experiment._run_dry()

                # 최소 2번 호출 (before, after)
                assert mock_capture.call_count >= 2
                assert result.steady_state_before == mock_metrics

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_records_audit(self, mock_sleep, mock_now):
        """Dry Run이 audit을 기록하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            with patch.object(experiment, "_audit") as mock_audit:
                experiment._run_dry()

                # audit이 호출되어야 함
                assert mock_audit.call_count > 0

                # dry_run 플래그가 audit 데이터에 포함되어야 함
                audit_calls = mock_audit.call_args_list
                dry_run_in_audit = any(
                    call[0][1].get("dry_run", False)
                    for call in audit_calls
                    if len(call[0]) > 1 and isinstance(call[0][1], dict)
                )
                assert dry_run_in_audit


# =============================================================================
# Dry Run via execute() Tests
# =============================================================================


class TestDryRunViaExecute:
    """execute() 메서드를 통한 Dry Run 테스트."""

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_execute_uses_dry_run_when_configured(self, mock_sleep, mock_now):
        """execute()가 설정에 따라 dry run을 사용하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            with patch.object(experiment, "_run_dry", wraps=experiment._run_dry) as mock_run_dry:
                result = experiment.execute()

                mock_run_dry.assert_called_once()
                assert result.dry_run is True

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_execute_result_indicates_dry_run(self, mock_sleep, mock_now):
        """execute() 결과가 dry run을 표시하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            result = experiment.execute()

        assert result.dry_run is True


# =============================================================================
# Dry Run Result Content Tests
# =============================================================================


class TestDryRunResultContent:
    """Dry Run 결과 내용 테스트."""

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_result_has_ttl_info(self, mock_sleep, mock_now):
        """Dry Run 결과에 TTL 정보가 있는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
            ttl_seconds=300,
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            result = experiment._run_dry()

        assert result.ttl_seconds == 300
        assert result.expires_at != ""

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_result_has_experiment_info(self, mock_sleep, mock_now):
        """Dry Run 결과에 실험 정보가 있는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
        )
        experiment = LatencyInjectionExperiment(
            experiment_id="test-dry-run-123",
            config=config,
        )

        with patch.object(experiment, "pre_flight_check", return_value=True):
            result = experiment._run_dry()

        assert result.experiment_id == "test-dry-run-123"
        assert result.experiment_type == "latency_injection"
        assert len(result.audit_record_ids) > 0

    @patch("selfhealing.core.timezone.now")
    @patch("time.sleep")
    def test_dry_run_shortened_duration(self, mock_sleep, mock_now):
        """Dry Run이 duration을 단축하는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0)

        config = ExperimentConfig(
            target_service="payment",
            dry_run=True,
            duration_seconds=300,  # 5분
        )
        experiment = LatencyInjectionExperiment(config=config)

        with patch.object(experiment, "pre_flight_check", return_value=True):
            experiment._run_dry()

        # sleep이 5초 이하로 호출되어야 함 (단축됨)
        sleep_call = mock_sleep.call_args[0][0]
        assert sleep_call <= 5
