"""
Tests for Error Budget Calculator

Covers:
- ErrorBudgetCalculator class
- Budget status calculation
- SLO integration
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestErrorBudgetCalculatorInit:
    """Tests for ErrorBudgetCalculator initialization."""
    
    def test_init_with_defaults(self):
        """Test initialization with defaults."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        calculator = ErrorBudgetCalculator()
        
        assert calculator.slo_config is not None
    
    def test_init_with_custom_slo_config(self):
        """Test initialization with custom SLO config."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.slo import SLOConfig
        
        slo_config = SLOConfig()
        calculator = ErrorBudgetCalculator(slo_config=slo_config)
        
        assert calculator.slo_config is slo_config
    
    def test_init_with_stat_functions(self):
        """Test initialization with stat functions."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        def get_failed_ops(start_time, end_time):
            return {"total_errors": 10}
        
        def get_requests(start_time, end_time):
            return {"total_requests": 10000}
        
        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=get_failed_ops,
            get_request_stats=get_requests,
        )
        
        assert calculator._get_failed_operation_stats is get_failed_ops
        assert calculator._get_request_stats is get_requests


class TestCalculateBudgetStatus:
    """Tests for calculate_budget_status method."""
    
    def test_calculate_with_no_errors(self):
        """Test calculation with no errors."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        def get_failed_ops(start_time, end_time):
            return {"total_errors": 0}
        
        def get_requests(start_time, end_time):
            return {"total_requests": 100000}
        
        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=get_failed_ops,
            get_request_stats=get_requests,
        )
        
        status = calculator.calculate_budget_status()
        
        assert status.error_count_window == 0
        assert status.budget_remaining_percent > 0
    
    def test_calculate_with_some_errors(self):
        """Test calculation with some errors."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        def get_failed_ops(start_time, end_time):
            return {"total_errors": 50}
        
        def get_requests(start_time, end_time):
            return {"total_requests": 100000}
        
        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=get_failed_ops,
            get_request_stats=get_requests,
        )
        
        status = calculator.calculate_budget_status()
        
        assert status.error_count_window == 50
    
    def test_calculate_uses_slo_window(self):
        """Test calculation uses SLO window days."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.slo import SLOConfig, SLO, SLI
        
        slo_config = SLOConfig(slos=[
            SLO(name="availability", sli=SLI.AVAILABILITY, target=0.999, window_days=7)
        ])
        
        calculator = ErrorBudgetCalculator(slo_config=slo_config)
        
        status = calculator.calculate_budget_status("availability")
        
        assert status.window_days == 7
    
    def test_calculate_default_slo_when_not_found(self):
        """Test uses default SLO when specific SLO not found."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.slo import SLOConfig
        
        slo_config = SLOConfig(slos=[])  # Empty SLOs
        calculator = ErrorBudgetCalculator(slo_config=slo_config)
        
        # Should use default availability SLO
        status = calculator.calculate_budget_status("nonexistent")
        
        assert status.slo_name == "availability"
        assert status.slo_target == 0.999
        assert status.window_days == 30
    
    def test_calculate_handles_stat_function_failure(self):
        """Test handles failure in stat functions gracefully."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        def get_failed_ops(start_time, end_time):
            raise Exception("Database error")
        
        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=get_failed_ops,
        )
        
        # Should not raise, but return status with 0 errors
        status = calculator.calculate_budget_status()
        
        assert status.error_count_window == 0
    
    def test_calculate_with_custom_time_window(self):
        """Test calculation with custom time window."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.core.timezone import now
        from datetime import timedelta
        
        end_time = now()
        start_time = end_time - timedelta(days=7)
        
        calculator = ErrorBudgetCalculator()
        
        status = calculator.calculate_budget_status(
            window_start=start_time,
            window_end=end_time,
        )
        
        assert status is not None


class TestBudgetCalculation:
    """Tests for budget calculation logic."""
    
    def test_budget_total_from_slo(self):
        """Test budget total calculated from SLO."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        from selfhealing.slo import SLOConfig, SLO, SLI
        
        # 99.9% SLO over 30 days = 43.2 minutes of allowed downtime
        slo_config = SLOConfig(slos=[
            SLO(name="availability", sli=SLI.AVAILABILITY, target=0.999, window_days=30)
        ])
        
        calculator = ErrorBudgetCalculator(slo_config=slo_config)
        status = calculator.calculate_budget_status()
        
        # 30 days * 24 hours * 60 minutes * 0.001 = 43.2 minutes
        expected_budget = 30 * 24 * 60 * (1 - 0.999)
        assert abs(status.budget_total_minutes - expected_budget) < 0.1
    
    def test_budget_remaining_calculation(self):
        """Test budget remaining is correctly calculated."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        def get_failed_ops(start_time, end_time):
            return {"total_errors": 100}
        
        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=get_failed_ops,
        )
        
        status = calculator.calculate_budget_status()
        
        # Remaining should be total - consumed
        expected_remaining = status.budget_total_minutes - status.budget_consumed_minutes
        assert abs(status.budget_remaining_minutes - expected_remaining) < 0.1
    
    def test_budget_remaining_percent(self):
        """Test budget remaining percent calculation."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        calculator = ErrorBudgetCalculator()
        status = calculator.calculate_budget_status()
        
        # Percent should be (remaining / total) * 100
        if status.budget_total_minutes > 0:
            expected_percent = (status.budget_remaining_minutes / status.budget_total_minutes) * 100
            assert abs(status.budget_remaining_percent - expected_percent) < 0.1


class TestBurnRateCalculation:
    """Tests for burn rate calculation."""
    
    def test_burn_rate_fields_present(self):
        """Test burn rate fields are present."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        calculator = ErrorBudgetCalculator()
        status = calculator.calculate_budget_status()
        
        assert hasattr(status, 'burn_rate_1h')
        assert hasattr(status, 'burn_rate_6h')
    
    def test_burn_rate_default_zero(self):
        """Test burn rate defaults to zero."""
        from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
        
        calculator = ErrorBudgetCalculator()
        status = calculator.calculate_budget_status()
        
        # Without specific calculation, should be 0
        assert status.burn_rate_1h >= 0
        assert status.burn_rate_6h >= 0
