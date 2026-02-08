"""
Tests for Settings Extensions and New Settings.

확장/신규 생성된 Settings 모듈에 대한 테스트입니다:
- ErrorBudgetPropagationSettings 확장 필드
- ThrottleSettings 확장 필드
- AntiFlappingSettings 확장 필드
- GovernanceSettings 확장 필드
- SamplingSettings (신규)
- SteadyStateSettings (신규)
"""

import os
from unittest import mock

import pytest


# =============================================================================
# ErrorBudgetPropagationSettings Extension Tests
# =============================================================================


class TestErrorBudgetPropagationSettingsExtension:
    """ErrorBudgetPropagationSettings 확장 필드 테스트."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.error_budget_propagation import (
            reset_error_budget_propagation_settings,
        )
        reset_error_budget_propagation_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.error_budget_propagation import (
            reset_error_budget_propagation_settings,
        )
        reset_error_budget_propagation_settings()

    def test_extended_default_values(self):
        """확장된 필드들의 기본값 검증."""
        from selfhealing.settings.error_budget_propagation import (
            get_error_budget_propagation_settings,
        )
        settings = get_error_budget_propagation_settings()

        # Multiplier Caps (constants.py 기반)
        assert settings.max_crisis_multiplier_cap == 10.0
        assert settings.max_domain_multiplier == 24.0
        assert settings.max_combined_multiplier == 10.0

        # Cache Settings
        assert settings.default_cache_ttl_seconds == 30.0

        # Refund Settings
        assert settings.refund_ratio == 0.5
        assert settings.refund_proposal_expiry_hours == 24

        # Combine Strategy
        assert settings.default_combine_strategy == "max"

    def test_extended_env_override(self):
        """확장된 필드들의 환경 변수 오버라이드 테스트."""
        from selfhealing.settings.error_budget_propagation import (
            reset_error_budget_propagation_settings,
            get_error_budget_propagation_settings,
        )
        
        with mock.patch.dict(os.environ, {
            "SELFHEALING_ERROR_BUDGET_PROPAGATION_MAX_CRISIS_MULTIPLIER_CAP": "15.0",
            "SELFHEALING_ERROR_BUDGET_PROPAGATION_REFUND_RATIO": "0.7",
            "SELFHEALING_ERROR_BUDGET_PROPAGATION_DEFAULT_COMBINE_STRATEGY": "sum",
        }):
            reset_error_budget_propagation_settings()
            settings = get_error_budget_propagation_settings()

            assert settings.max_crisis_multiplier_cap == 15.0
            assert settings.refund_ratio == 0.7
            assert settings.default_combine_strategy == "sum"

    def test_combine_strategy_validation(self):
        """결합 전략 유효성 검증 테스트."""
        from selfhealing.settings.error_budget_propagation import (
            ErrorBudgetPropagationSettings,
        )

        # 유효한 전략
        for strategy in ["max", "sum", "multiply"]:
            settings = ErrorBudgetPropagationSettings(
                default_combine_strategy=strategy
            )
            assert settings.default_combine_strategy == strategy

        # 무효한 전략
        with pytest.raises(ValueError):
            ErrorBudgetPropagationSettings(default_combine_strategy="invalid")


# =============================================================================
# ThrottleSettings Extension Tests
# =============================================================================


class TestThrottleSettingsExtension:
    """ThrottleSettings 확장 필드 테스트."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.throttle import reset_throttle_settings
        reset_throttle_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.throttle import reset_throttle_settings
        reset_throttle_settings()

    def test_extended_default_values(self):
        """확장된 필드들의 기본값 검증 (GradientCalculator 기반)."""
        from selfhealing.settings.throttle import get_throttle_settings
        settings = get_throttle_settings()

        # GradientCalculator 설정
        assert settings.sample_window_seconds == 10.0
        assert settings.gradient_min_samples == 3

        # 기존 필드 확인
        assert settings.smoothing_factor == 0.5

    def test_extended_env_override(self):
        """확장된 필드들의 환경 변수 오버라이드 테스트."""
        from selfhealing.settings.throttle import (
            reset_throttle_settings,
            get_throttle_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_THROTTLE_SAMPLE_WINDOW_SECONDS": "20.0",
            "SELFHEALING_THROTTLE_GRADIENT_MIN_SAMPLES": "5",
        }):
            reset_throttle_settings()
            settings = get_throttle_settings()

            assert settings.sample_window_seconds == 20.0
            assert settings.gradient_min_samples == 5


# =============================================================================
# AntiFlappingSettings Extension Tests
# =============================================================================


class TestAntiFlappingSettingsExtension:
    """AntiFlappingSettings 확장 필드 테스트."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.anti_flapping import reset_anti_flapping_settings
        reset_anti_flapping_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.anti_flapping import reset_anti_flapping_settings
        reset_anti_flapping_settings()

    def test_extended_default_values(self):
        """확장된 필드들의 기본값 검증 (AntiFlappingWindow 기반)."""
        from selfhealing.settings.anti_flapping import get_anti_flapping_settings
        settings = get_anti_flapping_settings()

        # AntiFlappingWindow 설정
        assert settings.window_seconds == 60
        assert settings.similarity_threshold == 0.01  # 1%
        assert settings.max_similar_changes == 3

    def test_extended_env_override(self):
        """확장된 필드들의 환경 변수 오버라이드 테스트."""
        from selfhealing.settings.anti_flapping import (
            reset_anti_flapping_settings,
            get_anti_flapping_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_ANTI_FLAPPING_WINDOW_SECONDS": "120",
            "SELFHEALING_ANTI_FLAPPING_SIMILARITY_THRESHOLD": "0.02",
            "SELFHEALING_ANTI_FLAPPING_MAX_SIMILAR_CHANGES": "5",
        }):
            reset_anti_flapping_settings()
            settings = get_anti_flapping_settings()

            assert settings.window_seconds == 120
            assert settings.similarity_threshold == 0.02
            assert settings.max_similar_changes == 5


# =============================================================================
# GovernanceSettings Extension Tests
# =============================================================================


class TestGovernanceSettingsExtension:
    """GovernanceSettings 확장 필드 테스트."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.governance import reset_governance_settings
        reset_governance_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.governance import reset_governance_settings
        reset_governance_settings()

    def test_extended_default_values(self):
        """확장된 필드들의 기본값 검증 (governance_checks.py 기반)."""
        from selfhealing.settings.governance import get_governance_settings
        settings = get_governance_settings()

        # emergency_min_level (check_all_governance 함수 파라미터 기본값)
        assert settings.emergency_min_level == 2

    def test_extended_env_override(self):
        """확장된 필드들의 환경 변수 오버라이드 테스트."""
        from selfhealing.settings.governance import (
            reset_governance_settings,
            get_governance_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_GOVERNANCE_EMERGENCY_MIN_LEVEL": "3",
        }):
            reset_governance_settings()
            settings = get_governance_settings()

            assert settings.emergency_min_level == 3


# =============================================================================
# SamplingSettings Tests (신규)
# =============================================================================


class TestSamplingSettings:
    """SamplingSettings 테스트 (신규 생성)."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.sampling import reset_sampling_settings
        reset_sampling_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.sampling import reset_sampling_settings
        reset_sampling_settings()

    def test_default_values(self):
        """기본값 검증 (audit/performance/sampling.py SamplingConfig 기반)."""
        from selfhealing.settings.sampling import get_sampling_settings
        settings = get_sampling_settings()

        assert settings.sample_rate == 0.1  # 10%
        assert settings.min_samples == 10
        assert settings.max_samples == 1000
        assert settings.full_verify_on_failure is True

    def test_env_override(self):
        """환경 변수 오버라이드 테스트."""
        from selfhealing.settings.sampling import (
            reset_sampling_settings,
            get_sampling_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_SAMPLING_SAMPLE_RATE": "0.2",
            "SELFHEALING_SAMPLING_MIN_SAMPLES": "20",
            "SELFHEALING_SAMPLING_MAX_SAMPLES": "2000",
            "SELFHEALING_SAMPLING_FULL_VERIFY_ON_FAILURE": "false",
        }):
            reset_sampling_settings()
            settings = get_sampling_settings()

            assert settings.sample_rate == 0.2
            assert settings.min_samples == 20
            assert settings.max_samples == 2000
            assert settings.full_verify_on_failure is False

    def test_singleton_pattern(self):
        """싱글톤 패턴 테스트."""
        from selfhealing.settings.sampling import get_sampling_settings
        
        settings1 = get_sampling_settings()
        settings2 = get_sampling_settings()
        
        assert settings1 is settings2

    def test_validation_sample_rate_bounds(self):
        """샘플링 비율 범위 검증."""
        from selfhealing.settings.sampling import SamplingSettings

        # 유효한 범위
        settings = SamplingSettings(sample_rate=0.5)
        assert settings.sample_rate == 0.5

        # 하한 미만
        with pytest.raises(ValueError):
            SamplingSettings(sample_rate=0.001)  # ge=0.01

        # 상한 초과
        with pytest.raises(ValueError):
            SamplingSettings(sample_rate=1.5)  # le=1.0


# =============================================================================
# SteadyStateSettings Tests (신규)
# =============================================================================


class TestSteadyStateSettings:
    """SteadyStateSettings 테스트 (신규 생성)."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.steady_state import reset_steady_state_settings
        reset_steady_state_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.steady_state import reset_steady_state_settings
        reset_steady_state_settings()

    def test_default_values(self):
        """기본값 검증 (services/chaos/base/models.py SteadyStateHypothesis 기반)."""
        from selfhealing.settings.steady_state import get_steady_state_settings
        settings = get_steady_state_settings()

        assert settings.p50_latency_max_ms == 100.0
        assert settings.p99_latency_max_ms == 500.0
        assert settings.error_rate_max_percent == 0.1
        assert settings.throughput_min_rps == 100.0

    def test_env_override(self):
        """환경 변수 오버라이드 테스트."""
        from selfhealing.settings.steady_state import (
            reset_steady_state_settings,
            get_steady_state_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_STEADY_STATE_P50_LATENCY_MAX_MS": "150.0",
            "SELFHEALING_STEADY_STATE_P99_LATENCY_MAX_MS": "750.0",
            "SELFHEALING_STEADY_STATE_ERROR_RATE_MAX_PERCENT": "0.5",
            "SELFHEALING_STEADY_STATE_THROUGHPUT_MIN_RPS": "200.0",
        }):
            reset_steady_state_settings()
            settings = get_steady_state_settings()

            assert settings.p50_latency_max_ms == 150.0
            assert settings.p99_latency_max_ms == 750.0
            assert settings.error_rate_max_percent == 0.5
            assert settings.throughput_min_rps == 200.0

    def test_singleton_pattern(self):
        """싱글톤 패턴 테스트."""
        from selfhealing.settings.steady_state import get_steady_state_settings

        settings1 = get_steady_state_settings()
        settings2 = get_steady_state_settings()

        assert settings1 is settings2

    def test_validation_latency_bounds(self):
        """레이턴시 범위 검증."""
        from selfhealing.settings.steady_state import SteadyStateSettings

        # 유효한 범위
        settings = SteadyStateSettings(p50_latency_max_ms=50.0)
        assert settings.p50_latency_max_ms == 50.0

        # 하한 미만
        with pytest.raises(ValueError):
            SteadyStateSettings(p50_latency_max_ms=0.5)  # ge=1.0

    def test_validation_error_rate_bounds(self):
        """에러율 범위 검증."""
        from selfhealing.settings.steady_state import SteadyStateSettings

        # 상한 초과
        with pytest.raises(ValueError):
            SteadyStateSettings(error_rate_max_percent=150.0)  # le=100.0


# =============================================================================
# ForensicSettings Rate Limiter Extension Tests
# =============================================================================


class TestForensicSettingsRateLimiterExtension:
    """ForensicSettings Rate Limiter 확장 필드 테스트."""

    def setup_method(self):
        """Reset settings before each test."""
        from selfhealing.settings.forensic import reset_forensic_settings
        reset_forensic_settings()

    def teardown_method(self):
        """Reset settings after each test."""
        from selfhealing.settings.forensic import reset_forensic_settings
        reset_forensic_settings()

    def test_rate_limiter_default_values(self):
        """Rate Limiter 관련 필드 기본값 검증 (ForensicRateLimiter 기반)."""
        from selfhealing.settings.forensic import get_forensic_settings

        settings = get_forensic_settings()

        # ForensicRateLimiter 기본값과 동일해야 함
        assert settings.rate_limit_exception_limit == 10
        assert settings.rate_limit_snapshot_limit == 1
        assert settings.rate_limit_anomaly_limit == 5
        assert settings.rate_limit_window_seconds == 60.0

    def test_rate_limiter_env_override(self):
        """Rate Limiter 환경 변수 오버라이드 테스트."""
        from selfhealing.settings.forensic import (
            get_forensic_settings,
            reset_forensic_settings,
        )

        with mock.patch.dict(os.environ, {
            "SELFHEALING_FORENSIC_RATE_LIMIT_EXCEPTION_LIMIT": "20",
            "SELFHEALING_FORENSIC_RATE_LIMIT_SNAPSHOT_LIMIT": "3",
            "SELFHEALING_FORENSIC_RATE_LIMIT_ANOMALY_LIMIT": "10",
            "SELFHEALING_FORENSIC_RATE_LIMIT_WINDOW_SECONDS": "120.0",
        }):
            reset_forensic_settings()
            settings = get_forensic_settings()

            assert settings.rate_limit_exception_limit == 20
            assert settings.rate_limit_snapshot_limit == 3
            assert settings.rate_limit_anomaly_limit == 10
            assert settings.rate_limit_window_seconds == 120.0

    def test_rate_limiter_validation_bounds(self):
        """Rate Limiter 범위 검증."""
        from selfhealing.settings.forensic import ForensicSettings

        # 유효한 범위
        settings = ForensicSettings(rate_limit_exception_limit=50)
        assert settings.rate_limit_exception_limit == 50

        # 하한 미만
        with pytest.raises(ValueError):
            ForensicSettings(rate_limit_exception_limit=0)  # ge=1
