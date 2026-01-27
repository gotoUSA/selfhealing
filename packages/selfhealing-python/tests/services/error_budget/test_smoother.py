"""
MultiplierSmoother 단위 테스트.

레벨 전환 시 가중치 점진적 변경 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.2
"""

from __future__ import annotations

import pytest

from selfhealing.services.error_budget.smoother import (
    MultiplierSmoother,
    MultiplierSmootherConfig,
    get_multiplier_smoother,
    configure_multiplier_smoother,
    reset_multiplier_smoother,
)


# =============================================================================
# MultiplierSmootherConfig 테스트
# =============================================================================

class TestMultiplierSmootherConfig:
    """MultiplierSmootherConfig 테스트."""
    
    def test_default_smoothing_factor(self):
        """기본 평활화 계수."""
        config = MultiplierSmootherConfig()
        assert config.smoothing_factor == 0.3
    
    def test_default_enabled(self):
        """기본 활성화 상태."""
        config = MultiplierSmootherConfig()
        assert config.enabled is True
    
    def test_custom_config(self):
        """커스텀 설정."""
        config = MultiplierSmootherConfig(
            smoothing_factor=0.5,
            sample_interval_seconds=10.0,
            enabled=False,
        )
        
        assert config.smoothing_factor == 0.5
        assert config.sample_interval_seconds == 10.0
        assert config.enabled is False


# =============================================================================
# MultiplierSmoother 테스트
# =============================================================================

class TestMultiplierSmoother:
    """MultiplierSmoother 테스트."""
    
    def test_initial_value_is_1(self):
        """초기값 1.0."""
        smoother = MultiplierSmoother()
        assert smoother.get_current_value() == 1.0
    
    def test_initial_target_is_1(self):
        """초기 목표값 1.0."""
        smoother = MultiplierSmoother()
        assert smoother.get_target_value() == 1.0
    
    def test_set_target_changes_target(self):
        """목표값 설정."""
        smoother = MultiplierSmoother()
        smoother.set_target(5.0)
        
        assert smoother.get_target_value() == 5.0
    
    def test_is_transitioning_after_target_set(self):
        """목표 설정 후 전환 중."""
        smoother = MultiplierSmoother()
        smoother.set_target(5.0)
        
        assert smoother.is_transitioning() is True
    
    def test_not_transitioning_initially(self):
        """초기에는 전환 중 아님."""
        smoother = MultiplierSmoother()
        assert smoother.is_transitioning() is False
    
    def test_disabled_returns_target_immediately(self):
        """비활성화 시 즉시 목표값 반환."""
        config = MultiplierSmootherConfig(enabled=False)
        smoother = MultiplierSmoother(config=config)
        
        smoother.set_target(5.0)
        value = smoother.get_smoothed_value()
        
        assert value == 5.0
    
    def test_smoothed_value_approaches_target(self):
        """평활화된 값이 목표값에 접근."""
        # 빠른 수렴을 위한 설정
        config = MultiplierSmootherConfig(
            smoothing_factor=0.5,
            sample_interval_seconds=0,  # 즉시 샘플링
        )
        smoother = MultiplierSmoother(config=config)
        
        smoother.set_target(5.0)
        
        # 첫 번째 샘플링
        value1 = smoother.get_smoothed_value()
        assert 1.0 < value1 < 5.0
        
        # 목표값에 가까워져야 함
        for _ in range(10):
            value = smoother.get_smoothed_value()
        
        # 거의 목표값에 도달
        assert abs(value - 5.0) < 0.1
    
    def test_exponential_smoothing_formula(self):
        """지수 평활화 공식 확인."""
        # α = 0.5, current = 1.0, target = 5.0
        # expected = 0.5 * 5.0 + 0.5 * 1.0 = 3.0
        config = MultiplierSmootherConfig(
            smoothing_factor=0.5,
            sample_interval_seconds=0,
        )
        smoother = MultiplierSmoother(config=config)
        
        smoother.set_target(5.0)
        value = smoother.get_smoothed_value()
        
        assert value == 3.0  # 0.5 * 5.0 + 0.5 * 1.0
    
    def test_force_converge(self):
        """강제 수렴."""
        smoother = MultiplierSmoother()
        smoother.set_target(5.0)
        
        assert smoother.is_transitioning() is True
        
        smoother.force_converge()
        
        assert smoother.get_current_value() == 5.0
        assert smoother.is_transitioning() is False
    
    def test_reset(self):
        """상태 초기화."""
        smoother = MultiplierSmoother()
        smoother.set_target(5.0)
        smoother.get_smoothed_value()
        
        smoother.reset()
        
        assert smoother.get_current_value() == 1.0
        assert smoother.get_target_value() == 1.0
    
    def test_transition_progress_zero_initially(self):
        """전환 시작 시 진행률 0."""
        config = MultiplierSmootherConfig(sample_interval_seconds=0)
        smoother = MultiplierSmoother(config=config)
        
        smoother.set_target(5.0)
        
        # 아직 샘플링 전
        progress = smoother.get_transition_progress()
        assert progress < 1.0
    
    def test_transition_progress_one_when_done(self):
        """전환 완료 시 진행률 1."""
        smoother = MultiplierSmoother()
        
        # 전환 없음 = 완료
        progress = smoother.get_transition_progress()
        assert progress == 1.0
    
    def test_downward_smoothing(self):
        """하강 방향 평활화."""
        config = MultiplierSmootherConfig(
            smoothing_factor=0.5,
            sample_interval_seconds=0,
        )
        smoother = MultiplierSmoother(config=config)
        
        # 먼저 5.0으로 강제 수렴
        smoother.set_target(5.0)
        smoother.force_converge()
        
        # 1.0으로 목표 변경
        smoother.set_target(1.0)
        value = smoother.get_smoothed_value()
        
        # 5.0에서 1.0으로 점진적 감소
        assert 1.0 < value < 5.0


# =============================================================================
# Singleton 테스트
# =============================================================================

class TestMultiplierSmootherSingleton:
    """싱글톤 테스트."""
    
    def setup_method(self):
        """테스트 전 초기화."""
        reset_multiplier_smoother()
    
    def teardown_method(self):
        """테스트 후 정리."""
        reset_multiplier_smoother()
    
    def test_get_returns_singleton(self):
        """싱글톤 반환."""
        s1 = get_multiplier_smoother()
        s2 = get_multiplier_smoother()
        
        assert s1 is s2
    
    def test_configure_creates_new_instance(self):
        """설정 시 새 인스턴스."""
        s1 = get_multiplier_smoother()
        
        config = MultiplierSmootherConfig(smoothing_factor=0.8)
        s2 = configure_multiplier_smoother(config)
        
        assert s1 is not s2
        assert s2.config.smoothing_factor == 0.8
    
    def test_reset_clears_singleton(self):
        """리셋 후 새 인스턴스."""
        s1 = get_multiplier_smoother()
        reset_multiplier_smoother()
        s2 = get_multiplier_smoother()
        
        assert s1 is not s2


# =============================================================================
# 시간 기반 테스트
# =============================================================================

class TestMultiplierSmootherTiming:
    """시간 기반 동작 테스트."""
    
    def test_sample_interval_respected(self):
        """샘플링 간격 준수."""
        current_time = [0.0]
        
        def mock_time():
            return current_time[0]
        
        config = MultiplierSmootherConfig(
            sample_interval_seconds=5.0,
            smoothing_factor=0.5,
        )
        smoother = MultiplierSmoother(config=config, time_func=mock_time)
        
        smoother.set_target(5.0)
        
        # 첫 번째 호출 - 초기값 반환 (아직 샘플링 전이므로)
        # _last_sample_time=0, now=0 이므로 0-0 < 5.0 조건에 걸림, 현재값(1.0) 반환
        v1 = smoother.get_smoothed_value()
        assert v1 == 1.0  # 초기값
        
        # 5초 후 - 첫 번째 실제 샘플링
        current_time[0] = 5.0
        v2 = smoother.get_smoothed_value()
        assert v2 == 3.0  # 0.5 * 5.0 + 0.5 * 1.0
        
        # 7초 - 간격 내 (변화 없음)
        current_time[0] = 7.0
        v3 = smoother.get_smoothed_value()
        assert v3 == 3.0  # 변화 없음
    
    def test_estimated_time_remaining(self):
        """남은 시간 추정."""
        current_time = [0.0]
        
        def mock_time():
            return current_time[0]
        
        config = MultiplierSmootherConfig(
            sample_interval_seconds=0,
            smoothing_factor=0.5,
        )
        smoother = MultiplierSmoother(config=config, time_func=mock_time)
        
        smoother.set_target(5.0)
        
        # 첫 번째 샘플링
        smoother.get_smoothed_value()
        current_time[0] = 1.0
        
        remaining = smoother.get_estimated_time_remaining()
        
        # 진행률에 따른 남은 시간
        assert remaining >= 0
