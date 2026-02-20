"""
Tests for Emergency Mode Enums

Covers:
- EmergencyLevel enum
- EMERGENCY_LEVEL_RULES
"""

import pytest


class TestEmergencyLevel:
    """Tests for EmergencyLevel enum."""
    
    def test_normal_level(self):
        """Test NORMAL level value."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        assert EmergencyLevel.NORMAL.value == 0
    
    def test_level_1(self):
        """Test LEVEL_1 value."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        assert EmergencyLevel.LEVEL_1.value == 1
    
    def test_level_2(self):
        """Test LEVEL_2 value."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        assert EmergencyLevel.LEVEL_2.value == 2
    
    def test_level_3(self):
        """Test LEVEL_3 value."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        assert EmergencyLevel.LEVEL_3.value == 3
    
    def test_levels_are_ordered(self):
        """Test emergency levels are properly ordered."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        assert EmergencyLevel.NORMAL.value < EmergencyLevel.LEVEL_1.value
        assert EmergencyLevel.LEVEL_1.value < EmergencyLevel.LEVEL_2.value
        assert EmergencyLevel.LEVEL_2.value < EmergencyLevel.LEVEL_3.value


class TestEmergencyLevelRules:
    """Tests for EMERGENCY_LEVEL_RULES constant."""
    
    def test_all_levels_have_rules(self):
        """Test all levels have rules defined."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        for level in EmergencyLevel:
            assert level in EMERGENCY_LEVEL_RULES
    
    def test_rules_have_all_tiers(self):
        """Test rules have all tier types."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        required_tiers = ["critical", "standard", "non_essential"]
        
        for level, rules in EMERGENCY_LEVEL_RULES.items():
            for tier in required_tiers:
                assert tier in rules
    
    def test_normal_level_allows_all(self):
        """Test NORMAL level allows all traffic."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL]
        
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 1.0
    
    def test_level_1_blocks_non_essential(self):
        """Test LEVEL_1 blocks non-essential traffic."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_1]
        
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 0.0
    
    def test_level_2_limits_standard(self):
        """Test LEVEL_2 limits standard traffic."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_2]
        
        assert rules["critical"] == 1.0
        assert rules["standard"] < 1.0
        assert rules["non_essential"] == 0.0
    
    def test_level_3_limits_critical(self):
        """Test LEVEL_3 limits even critical traffic."""
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )
        
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_3]
        
        assert rules["critical"] < 1.0
        assert rules["standard"] == 0.0
        assert rules["non_essential"] == 0.0
    
    def test_traffic_multipliers_are_valid(self):
        """Test traffic multipliers are between 0 and 1."""
        from selfhealing.services.emergency_mode.enums import EMERGENCY_LEVEL_RULES
        
        for level, rules in EMERGENCY_LEVEL_RULES.items():
            for tier, multiplier in rules.items():
                assert 0.0 <= multiplier <= 1.0
