"""
Emergency Mode Enums and Config Tests.

Tests for EmergencyLevel, EmergencyLevelRules, RecoveryGateConfig, EmergencyState.
"""

import pytest


# =============================================================================
# EmergencyLevel Tests
# =============================================================================


class TestEmergencyLevel:
    """EmergencyLevel Enum 테스트."""
    
    def test_emergency_levels_defined(self):
        """모든 비상 레벨이 정의되어 있어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel
        
        assert EmergencyLevel.NORMAL.value == 0
        assert EmergencyLevel.LEVEL_1.value == 1
        assert EmergencyLevel.LEVEL_2.value == 2
        assert EmergencyLevel.LEVEL_3.value == 3
    
    def test_emergency_level_ordering(self):
        """비상 레벨은 값 순서가 있어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel
        
        # Enum 값 기반 비교 사용
        assert EmergencyLevel.NORMAL.value < EmergencyLevel.LEVEL_1.value
        assert EmergencyLevel.LEVEL_1.value < EmergencyLevel.LEVEL_2.value
        assert EmergencyLevel.LEVEL_2.value < EmergencyLevel.LEVEL_3.value
    
    def test_all_levels_have_rules(self):
        """모든 레벨에 대한 규칙이 정의되어 있어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        for level in EmergencyLevel:
            assert level in EMERGENCY_LEVEL_RULES
            rules = EMERGENCY_LEVEL_RULES[level]
            assert "critical" in rules
            assert "standard" in rules
            assert "non_essential" in rules


class TestEmergencyLevelRules:
    """비상 레벨별 규칙 테스트."""
    
    def test_normal_level_allows_all(self):
        """NORMAL 레벨은 모든 트래픽을 허용해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 1.0
    
    def test_level_1_blocks_non_essential(self):
        """LEVEL_1은 non_essential만 차단해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_1]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 0.0
    
    def test_level_2_limits_standard(self):
        """LEVEL_2는 standard를 10%로 제한해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_2]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 0.1
        assert rules["non_essential"] == 0.0
    
    def test_level_3_limits_critical(self):
        """LEVEL_3은 critical도 50%로 제한해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_3]
        assert rules["critical"] == 0.5
        assert rules["standard"] == 0.0
        assert rules["non_essential"] == 0.0
    
    def test_higher_level_more_restrictive(self):
        """더 높은 레벨일수록 더 제한적이어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EMERGENCY_LEVEL_RULES
        
        for i, level in enumerate(EmergencyLevel):
            if i == 0:
                continue
            prev_level = list(EmergencyLevel)[i - 1]
            
            current_rules = EMERGENCY_LEVEL_RULES[level]
            prev_rules = EMERGENCY_LEVEL_RULES[prev_level]
            
            # 현재 레벨의 합계가 이전 레벨보다 작거나 같아야 함
            current_sum = sum(current_rules.values())
            prev_sum = sum(prev_rules.values())
            assert current_sum <= prev_sum, (
                f"{level.name} should be more restrictive than {prev_level.name}"
            )


# =============================================================================
# RecoveryGateConfig Tests
# =============================================================================


class TestRecoveryGateConfig:
    """RecoveryGateConfig 테스트."""
    
    def test_default_config(self):
        """기본 설정이 올바르게 설정되어야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig
        
        config = RecoveryGateConfig()
        assert config.stabilization_period_seconds == 300
        assert config.require_metrics_stable is True
        assert config.cpu_threshold_percent == 80.0
        assert config.error_rate_threshold == 0.05
        assert config.gradual_recovery is True
        assert config.level_step_delay_seconds == 60
        assert config.auto_rollback_on_failure is True
    
    def test_config_serialization(self):
        """설정이 직렬화/역직렬화되어야 함."""
        from selfhealing.services.emergency_mode import RecoveryGateConfig
        
        config = RecoveryGateConfig(
            stabilization_period_seconds=600,
            cpu_threshold_percent=90.0,
        )
        
        data = config.to_dict()
        restored = RecoveryGateConfig.from_dict(data)
        
        assert restored.stabilization_period_seconds == 600
        assert restored.cpu_threshold_percent == 90.0


# =============================================================================
# EmergencyState Tests
# =============================================================================


class TestEmergencyState:
    """EmergencyState 테스트."""
    
    def test_default_state(self):
        """기본 상태는 NORMAL이어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EmergencyState
        
        state = EmergencyState()
        assert state.level == EmergencyLevel.NORMAL
        assert state.is_active is False
    
    def test_state_serialization(self):
        """상태가 직렬화/역직렬화되어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, EmergencyState
        
        state = EmergencyState(
            level=EmergencyLevel.LEVEL_2,
            is_active=True,
            activated_by="admin",
            activation_reason="Test",
        )
        
        data = state.to_dict()
        assert data["level"] == 2
        assert data["is_active"] is True
        
        restored = EmergencyState.from_dict(data)
        assert restored.level == EmergencyLevel.LEVEL_2
        assert restored.is_active is True
        assert restored.activated_by == "admin"
