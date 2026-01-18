"""
TTL (Self-Expiration) Tests for Chaos Engine Safety Mechanisms

Tests the TTL (Time-To-Live) functionality that ensures chaos experiments
automatically expire, preventing runaway chaos if the engine dies.

Phase 3: Chaos Safety Implementation Plan
Reference: docs/self_healing/CHAOS_SAFETY_IMPLEMENTATION_PLAN.md
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.chaos.stop_conditions import (
    TTLConfig,
)
from selfhealing.services.chaos.experiments import (
    ExperimentConfig,
    ExperimentStatus,
    LatencyInjectionExperiment,
)


# =============================================================================
# TTLConfig Tests
# =============================================================================


class TestTTLConfig:
    """TTLConfig 데이터 클래스 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정되는지 확인."""
        config = TTLConfig()

        assert config.default_ttl_seconds == 600  # 10분
        assert config.min_ttl_seconds == 60  # 1분
        assert config.max_ttl_seconds == 3600  # 1시간
        assert config.auto_expiration_enabled is True

    def test_to_dict(self):
        """딕셔너리 변환 테스트."""
        config = TTLConfig(
            default_ttl_seconds=300,
            min_ttl_seconds=30,
            max_ttl_seconds=1800,
            auto_expiration_enabled=False,
        )

        result = config.to_dict()

        assert result["default_ttl_seconds"] == 300
        assert result["min_ttl_seconds"] == 30
        assert result["max_ttl_seconds"] == 1800
        assert result["auto_expiration_enabled"] is False

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        data = {
            "default_ttl_seconds": 900,
            "min_ttl_seconds": 120,
            "max_ttl_seconds": 7200,
            "auto_expiration_enabled": True,
        }

        config = TTLConfig.from_dict(data)

        assert config.default_ttl_seconds == 900
        assert config.min_ttl_seconds == 120
        assert config.max_ttl_seconds == 7200
        assert config.auto_expiration_enabled is True

    def test_from_dict_with_defaults(self):
        """부분 데이터로 생성 시 기본값 적용 테스트."""
        data = {"default_ttl_seconds": 450}

        config = TTLConfig.from_dict(data)

        assert config.default_ttl_seconds == 450
        assert config.min_ttl_seconds == 60  # 기본값
        assert config.max_ttl_seconds == 3600  # 기본값

    def test_validate_ttl_within_range(self):
        """범위 내 TTL 검증 테스트."""
        config = TTLConfig(
            min_ttl_seconds=60,
            max_ttl_seconds=3600,
        )

        assert config.validate_ttl(300) == 300
        assert config.validate_ttl(600) == 600

    def test_validate_ttl_below_min(self):
        """최소값 미만 TTL 검증 테스트."""
        config = TTLConfig(
            min_ttl_seconds=60,
            max_ttl_seconds=3600,
        )

        # 60 미만은 60으로 조정
        assert config.validate_ttl(30) == 60
        assert config.validate_ttl(0) == 60

    def test_validate_ttl_above_max(self):
        """최대값 초과 TTL 검증 테스트."""
        config = TTLConfig(
            min_ttl_seconds=60,
            max_ttl_seconds=3600,
        )

        # 3600 초과는 3600으로 조정
        assert config.validate_ttl(7200) == 3600
        assert config.validate_ttl(10000) == 3600


# =============================================================================
# Experiment TTL Integration Tests
# =============================================================================


class TestExperimentTTL:
    """실험 클래스의 TTL 통합 테스트."""

    def test_default_ttl_seconds(self):
        """실험의 기본 TTL 값 확인."""
        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="test"))

        assert experiment.default_ttl_seconds == 600  # 10분

    def test_config_ttl_override(self):
        """설정을 통한 TTL 오버라이드 테스트."""
        config = ExperimentConfig(
            target_service="payment",
            ttl_seconds=300,  # 5분으로 오버라이드
        )
        experiment = LatencyInjectionExperiment(config=config)

        effective_ttl = experiment.get_effective_ttl()

        assert effective_ttl == 300

    @patch("selfhealing.services.chaos.base.experiment.now")
    def test_calculate_expires_at(self, mock_now):
        """만료 시간 계산 테스트."""
        fixed_time = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)
        mock_now.return_value = fixed_time

        config = ExperimentConfig(
            target_service="payment",
            ttl_seconds=600,  # 10분
        )
        experiment = LatencyInjectionExperiment(config=config)

        expires_at = experiment._calculate_expires_at()

        expected = fixed_time + timedelta(seconds=600)
        assert expires_at == expected
        assert experiment._effective_ttl == 600

    @patch("selfhealing.services.chaos.base.experiment.now")
    def test_is_expired_false(self, mock_now):
        """만료되지 않은 상태 테스트."""
        fixed_time = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)
        mock_now.return_value = fixed_time

        config = ExperimentConfig(
            target_service="payment",
            ttl_seconds=600,
        )
        experiment = LatencyInjectionExperiment(config=config)
        experiment._calculate_expires_at()

        # 현재 시간은 만료 전
        assert experiment.is_expired() is False

    @patch("selfhealing.services.chaos.base.experiment.now")
    def test_is_expired_true(self, mock_now):
        """만료된 상태 테스트."""
        initial_time = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)
        mock_now.return_value = initial_time

        config = ExperimentConfig(
            target_service="payment",
            ttl_seconds=600,  # 10분
        )
        experiment = LatencyInjectionExperiment(config=config)
        experiment._calculate_expires_at()

        # 시간을 11분 후로 이동
        mock_now.return_value = initial_time + timedelta(minutes=11)

        assert experiment.is_expired() is True

    @patch("selfhealing.services.chaos.base.experiment.now")
    def test_is_expired_without_expires_at(self, mock_now):
        """expires_at이 설정되지 않은 경우 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)

        experiment = LatencyInjectionExperiment(config=ExperimentConfig(target_service="test"))
        # _calculate_expires_at() 호출하지 않음

        # expires_at이 None이면 만료되지 않음
        assert experiment.is_expired() is False


# =============================================================================
# TTL Auto-Expiration During Monitoring Tests
# =============================================================================


class TestTTLAutoExpiration:
    """모니터링 중 TTL 자동 만료 테스트."""

    @patch("selfhealing.services.chaos.base.experiment.now")
    @patch("time.sleep")
    def test_monitoring_loop_stops_on_ttl_expiration(self, mock_sleep, mock_now):
        """TTL 만료 시 모니터링 루프가 중단되는지 테스트."""
        initial_time = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)
        current_time = [initial_time]

        def advancing_now():
            return current_time[0]

        def advance_time(seconds):
            current_time[0] += timedelta(seconds=seconds)

        mock_now.side_effect = advancing_now
        mock_sleep.side_effect = lambda s: advance_time(s)

        config = ExperimentConfig(
            target_service="payment",
            duration_seconds=300,  # 5분
            ttl_seconds=60,  # 1분 (일부러 duration보다 짧게)
        )
        experiment = LatencyInjectionExperiment(config=config)
        experiment._calculate_expires_at()

        # 만료 시간을 현재 시간보다 과거로 설정 (즉시 만료)
        experiment._expires_at = current_time[0] - timedelta(seconds=1)

        # 모니터링 루프 실행 - is_expired가 호출되도록 mock
        with patch.object(experiment, "_collect_impact_metrics", return_value={}):
            # stop_conditions 모듈의 get_stop_conditions_checker를 mock
            with patch("selfhealing.services.chaos.stop_conditions.get_stop_conditions_checker") as mock_checker:
                mock_checker.return_value = MagicMock()
                mock_checker.return_value.check.return_value = MagicMock(should_stop=False, violations=[])
                metrics = experiment._monitor_with_kill_switch()

        # TTL 만료로 인해 kill이 요청되어야 함
        assert experiment._kill_requested is True


# =============================================================================
# ExperimentResult TTL Fields Tests
# =============================================================================


class TestExperimentResultTTLFields:
    """ExperimentResult의 TTL 관련 필드 테스트."""

    @patch("selfhealing.services.chaos.base.experiment.now")
    @patch("time.sleep")
    def test_result_contains_ttl_info(self, mock_sleep, mock_now):
        """실험 결과에 TTL 정보가 포함되는지 테스트."""
        mock_now.return_value = datetime(2025, 12, 21, 10, 0, 0, tzinfo=timezone.utc)

        config = ExperimentConfig(
            target_service="payment",
            duration_seconds=1,
            ttl_seconds=600,
            dry_run=True,  # dry run으로 빠르게 테스트
        )
        experiment = LatencyInjectionExperiment(config=config)

        # Dry run 실행
        with patch.object(experiment, "capture_steady_state", return_value={}):
            with patch.object(experiment, "pre_flight_check", return_value=True):
                result = experiment.execute()

        assert result.dry_run is True
        assert result.ttl_seconds == 600
        assert result.expires_at != ""

    def test_result_to_dict_includes_ttl(self):
        """결과의 to_dict()에 TTL 필드가 포함되는지 테스트."""
        from selfhealing.services.chaos.experiments import ExperimentResult

        result = ExperimentResult(
            experiment_id="test-123",
            experiment_type="latency_injection",
            status="completed",
            dry_run=False,
            ttl_seconds=600,
            expires_at="2025-12-21T10:10:00",
            auto_expired=False,
        )

        result_dict = result.to_dict()

        assert result_dict["ttl_seconds"] == 600
        assert result_dict["expires_at"] == "2025-12-21T10:10:00"
        assert result_dict["auto_expired"] is False
        assert result_dict["dry_run"] is False
