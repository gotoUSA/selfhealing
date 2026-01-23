"""
Tests for Recovery Hysteresis Factor in AntiFlappingGuard.

72번 문서 §5.1.1에 정의된 recovery_hysteresis_factor 구현 테스트.

테스트 대상:
- recovery_hysteresis_factor 필드
- get_effective_stability_duration 메서드
- 환경변수 SELFHEALING_RECOVERY_HYSTERESIS_FACTOR 지원
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from selfhealing.services.coordination.anti_flapping import (
    AntiFlappingGuard,
    EMERGENCY_LEVEL_COOLDOWN_SECONDS,
)


class TestRecoveryHysteresisFactor:
    """recovery_hysteresis_factor 필드 테스트."""
    
    def test_default_value_is_1_15(self):
        """기본값이 1.15인지 확인."""
        guard = AntiFlappingGuard()
        assert guard.recovery_hysteresis_factor == 1.15
    
    def test_custom_value(self):
        """커스텀 값 설정 가능."""
        guard = AntiFlappingGuard(recovery_hysteresis_factor=1.20)
        assert guard.recovery_hysteresis_factor == 1.20
    
    def test_status_includes_factor(self):
        """get_status()에 recovery_hysteresis_factor 포함."""
        guard = AntiFlappingGuard(recovery_hysteresis_factor=1.25)
        status = guard.get_status()
        assert "recovery_hysteresis_factor" in status
        assert status["recovery_hysteresis_factor"] == 1.25


class TestGetEffectiveStabilityDuration:
    """get_effective_stability_duration 메서드 테스트."""
    
    def test_non_recovery_returns_base_duration(self):
        """is_recovery=False일 때 기본 대기 시간 반환."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.15,
        )
        duration = guard.get_effective_stability_duration(is_recovery=False)
        assert duration == 600
    
    def test_recovery_applies_hysteresis_factor(self):
        """is_recovery=True일 때 히스테리시스 팩터 적용."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.15,
        )
        duration = guard.get_effective_stability_duration(is_recovery=True)
        # 600 * 1.15 = 690
        assert duration == 690
    
    def test_factor_1_0_no_change(self):
        """팩터가 1.0이면 대기 시간 변화 없음."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.0,
        )
        duration = guard.get_effective_stability_duration(is_recovery=True)
        assert duration == 600
    
    def test_factor_1_20_conservative(self):
        """팩터가 1.20이면 20% 증가."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.20,
        )
        duration = guard.get_effective_stability_duration(is_recovery=True)
        # 600 * 1.20 = 720
        assert duration == 720
    
    def test_result_is_integer(self):
        """결과가 정수형."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.15,
        )
        duration = guard.get_effective_stability_duration(is_recovery=True)
        assert isinstance(duration, int)
    
    def test_different_base_durations(self):
        """다양한 기본 대기 시간에서 팩터 적용 확인."""
        guard = AntiFlappingGuard(
            min_stable_duration_before_recovery_seconds=300,  # 5분
            recovery_hysteresis_factor=1.15,
        )
        duration = guard.get_effective_stability_duration(is_recovery=True)
        # 300 * 1.15 = 345
        assert duration == 345


class TestEnvironmentVariableSupport:
    """환경변수 SELFHEALING_RECOVERY_HYSTERESIS_FACTOR 지원 테스트."""
    
    def test_env_var_sets_default(self, monkeypatch):
        """환경변수로 기본값 설정."""
        monkeypatch.setenv("SELFHEALING_RECOVERY_HYSTERESIS_FACTOR", "1.30")
        
        # AntiFlappingGuard 생성 시 환경변수 값 사용
        guard = AntiFlappingGuard()
        assert guard.recovery_hysteresis_factor == 1.30
    
    def test_explicit_value_overrides_env(self, monkeypatch):
        """명시적 값이 환경변수보다 우선."""
        monkeypatch.setenv("SELFHEALING_RECOVERY_HYSTERESIS_FACTOR", "1.30")
        
        guard = AntiFlappingGuard(recovery_hysteresis_factor=1.50)
        assert guard.recovery_hysteresis_factor == 1.50


class TestHysteresisIntegration:
    """히스테리시스와 기존 기능 통합 테스트."""
    
    def test_cooldown_and_hysteresis_independent(self):
        """쿨다운과 히스테리시스는 독립적으로 동작."""
        guard = AntiFlappingGuard(
            level_cooldown_seconds=300,
            min_stable_duration_before_recovery_seconds=600,
            recovery_hysteresis_factor=1.15,
        )
        
        # 쿨다운 값은 영향받지 않음
        assert guard.level_cooldown_seconds == 300
        
        # 히스테리시스는 Recovery에만 적용
        normal_duration = guard.get_effective_stability_duration(is_recovery=False)
        recovery_duration = guard.get_effective_stability_duration(is_recovery=True)
        
        assert normal_duration == 600
        assert recovery_duration == 690
    
    def test_status_contains_all_timing_fields(self):
        """상태에 모든 타이밍 필드 포함."""
        guard = AntiFlappingGuard(
            level_cooldown_seconds=300,
            cooldown_after_recovery_seconds=600,
            recovery_hysteresis_factor=1.15,
        )
        
        status = guard.get_status()
        
        assert "level_cooldown_seconds" in status
        assert "cooldown_after_recovery_seconds" in status
        assert "recovery_hysteresis_factor" in status
