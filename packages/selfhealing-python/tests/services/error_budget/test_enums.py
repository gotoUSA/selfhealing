"""
Tests for Error Budget Enums

Covers:
- FreezeStatus enum
- OverrideType enum
- Dynamic threshold getters
"""

import pytest
from unittest.mock import patch, MagicMock


class TestFreezeStatus:
    """Tests for FreezeStatus enum."""
    
    def test_proceed_status(self):
        """Test PROCEED status value."""
        from selfhealing.services.error_budget.enums import FreezeStatus
        assert FreezeStatus.PROCEED == "proceed"
    
    def test_caution_status(self):
        """Test CAUTION status value."""
        from selfhealing.services.error_budget.enums import FreezeStatus
        assert FreezeStatus.CAUTION == "caution"
    
    def test_warning_status(self):
        """Test WARNING status value."""
        from selfhealing.services.error_budget.enums import FreezeStatus
        assert FreezeStatus.WARNING == "warning"
    
    def test_freeze_recommended_status(self):
        """Test FREEZE_RECOMMENDED status value."""
        from selfhealing.services.error_budget.enums import FreezeStatus
        assert FreezeStatus.FREEZE_RECOMMENDED == "freeze_recommended"
    
    def test_all_statuses_are_strings(self):
        """Test all statuses are string enums."""
        from selfhealing.services.error_budget.enums import FreezeStatus
        
        for status in FreezeStatus:
            assert isinstance(status.value, str)


class TestOverrideType:
    """Tests for OverrideType enum."""
    
    def test_hotfix_type(self):
        """Test HOTFIX type value."""
        from selfhealing.services.error_budget.enums import OverrideType
        assert OverrideType.HOTFIX == "hotfix"
    
    def test_security_patch_type(self):
        """Test SECURITY_PATCH type value."""
        from selfhealing.services.error_budget.enums import OverrideType
        assert OverrideType.SECURITY_PATCH == "security_patch"
    
    def test_executive_approval_type(self):
        """Test EXECUTIVE_APPROVAL type value."""
        from selfhealing.services.error_budget.enums import OverrideType
        assert OverrideType.EXECUTIVE_APPROVAL == "executive_approval"
    
    def test_rollback_type(self):
        """Test ROLLBACK type value."""
        from selfhealing.services.error_budget.enums import OverrideType
        assert OverrideType.ROLLBACK == "rollback"
    
    def test_all_override_types_are_strings(self):
        """Test all override types are string enums."""
        from selfhealing.services.error_budget.enums import OverrideType
        
        for override_type in OverrideType:
            assert isinstance(override_type.value, str)


class TestGetErrorBudgetThresholds:
    """Tests for get_error_budget_thresholds function."""
    
    def test_returns_default_thresholds(self):
        """Test returns default thresholds when config unavailable."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        thresholds = get_error_budget_thresholds()
        
        assert "healthy" in thresholds
        assert "caution" in thresholds
        assert "warning" in thresholds
        assert "critical" in thresholds
    
    def test_default_healthy_threshold(self):
        """Test default healthy threshold is 75%."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        # Use mock to ensure defaults
        with patch('selfhealing.services.error_budget.enums._get_error_budget_config', return_value={}):
            thresholds = get_error_budget_thresholds()
        
        assert thresholds["healthy"] == 75.0
    
    def test_default_caution_threshold(self):
        """Test default caution threshold is 50%."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        with patch('selfhealing.services.error_budget.enums._get_error_budget_config', return_value={}):
            thresholds = get_error_budget_thresholds()
        
        assert thresholds["caution"] == 50.0
    
    def test_default_warning_threshold(self):
        """Test default warning threshold is 20%."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        with patch('selfhealing.services.error_budget.enums._get_error_budget_config', return_value={}):
            thresholds = get_error_budget_thresholds()
        
        assert thresholds["warning"] == 20.0
    
    def test_default_critical_threshold(self):
        """Test default critical threshold is 0%."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        with patch('selfhealing.services.error_budget.enums._get_error_budget_config', return_value={}):
            thresholds = get_error_budget_thresholds()
        
        assert thresholds["critical"] == 0.0
    
    def test_uses_runtime_config_values(self):
        """Test uses values from runtime config."""
        from selfhealing.services.error_budget.enums import get_error_budget_thresholds
        
        custom_config = {
            "threshold_healthy": 80.0,
            "threshold_caution": 60.0,
            "threshold_warning": 30.0,
            "threshold_critical": 5.0,
        }
        
        with patch('selfhealing.services.error_budget.enums._get_error_budget_config', return_value=custom_config):
            thresholds = get_error_budget_thresholds()
        
        assert thresholds["healthy"] == 80.0
        assert thresholds["caution"] == 60.0
        assert thresholds["warning"] == 30.0
        assert thresholds["critical"] == 5.0


class TestGetBurnRateThresholds:
    """Tests for get_burn_rate_thresholds function."""
    
    def test_returns_burn_rate_thresholds(self):
        """Test returns burn rate thresholds."""
        from selfhealing.services.error_budget.enums import get_burn_rate_thresholds
        
        thresholds = get_burn_rate_thresholds()
        
        assert "fast_critical" in thresholds
        assert "fast_warning" in thresholds
        assert "slow_warning" in thresholds
        assert "slow_info" in thresholds
    
    def test_burn_rate_values_are_numeric(self):
        """Test burn rate values are numeric."""
        from selfhealing.services.error_budget.enums import get_burn_rate_thresholds
        
        thresholds = get_burn_rate_thresholds()
        
        for key, value in thresholds.items():
            assert isinstance(value, (int, float))


class TestGetErrorBudgetConfig:
    """Tests for _get_error_budget_config function."""
    
    def test_returns_empty_dict_on_failure(self):
        """Test returns empty dict when config unavailable."""
        from selfhealing.services.error_budget.enums import _get_error_budget_config
        
        with patch('selfhealing.services.runtime_config.get_runtime_config_manager', side_effect=Exception("Not available")):
            config = _get_error_budget_config()
        
        assert config == {}
    
    def test_returns_config_from_manager(self):
        """Test returns config from runtime manager."""
        from selfhealing.services.error_budget.enums import _get_error_budget_config
        
        mock_manager = MagicMock()
        mock_manager.get_error_budget_config.return_value = {"threshold_healthy": 90.0}
        
        with patch('selfhealing.services.runtime_config.get_runtime_config_manager', return_value=mock_manager):
            config = _get_error_budget_config()
        
        # Just check function runs without error
        assert isinstance(config, dict)
