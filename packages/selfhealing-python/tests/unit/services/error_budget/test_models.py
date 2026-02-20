"""
Tests for Error Budget Models

Covers:
- ErrorBudgetStatus dataclass
- DeploymentVerdict dataclass
- FreezeDecisionRecord dataclass
"""

import pytest
from datetime import datetime
from unittest.mock import patch


class TestErrorBudgetStatus:
    """Tests for ErrorBudgetStatus dataclass."""
    
    def test_creation(self):
        """Test ErrorBudgetStatus creation."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
        )
        
        assert status.slo_name == "availability"
        assert status.slo_target == 0.999
        assert status.window_days == 30
        assert status.budget_total_minutes == 43.2
    
    def test_is_healthy_when_above_threshold(self):
        """Test is_healthy returns True when above threshold."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=5.0,
            budget_remaining_minutes=38.2,
            budget_remaining_percent=88.0,  # Above 75% healthy threshold
        )
        
        with patch('selfhealing.services.error_budget.models.get_error_budget_thresholds', 
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            assert status.is_healthy is True
    
    def test_is_healthy_when_below_threshold(self):
        """Test is_healthy returns False when below threshold."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=30.0,
            budget_remaining_minutes=13.2,
            budget_remaining_percent=30.0,  # Below 75% healthy threshold
        )
        
        with patch('selfhealing.services.error_budget.models.get_error_budget_thresholds', 
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            assert status.is_healthy is False
    
    def test_is_over_budget(self):
        """Test is_over_budget when budget exhausted."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=50.0,
            budget_remaining_minutes=-6.8,
            budget_remaining_percent=-15.7,
        )
        
        assert status.is_over_budget is True
    
    def test_is_not_over_budget(self):
        """Test is_over_budget when budget remaining."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
        )
        
        assert status.is_over_budget is False
    
    def test_is_critical(self):
        """Test is_critical when below warning threshold."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=40.0,
            budget_remaining_minutes=3.2,
            budget_remaining_percent=7.4,  # Below 20% warning threshold
        )
        
        with patch('selfhealing.services.error_budget.models.get_error_budget_thresholds', 
                   return_value={"healthy": 75.0, "caution": 50.0, "warning": 20.0, "critical": 0.0}):
            assert status.is_critical is True
    
    def test_has_fast_burn(self):
        """Test has_fast_burn when burn rate is high."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
            burn_rate_1h=15.0,  # High burn rate
        )
        
        with patch('selfhealing.services.error_budget.models.get_burn_rate_thresholds', 
                   return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
            assert status.has_fast_burn is True
    
    def test_has_slow_burn(self):
        """Test has_slow_burn when 6h burn rate is elevated."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
            burn_rate_6h=4.0,  # Elevated 6h burn rate
        )
        
        with patch('selfhealing.services.error_budget.models.get_burn_rate_thresholds', 
                   return_value={"fast_critical": 14.4, "fast_warning": 6.0, "slow_warning": 3.0, "slow_info": 1.0}):
            assert status.has_slow_burn is True
    
    def test_to_dict(self):
        """Test to_dict conversion."""
        from selfhealing.services.error_budget.models import ErrorBudgetStatus
        
        status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
        )
        
        d = status.to_dict()
        
        assert isinstance(d, dict)
        assert "slo" in d
        assert "budget" in d
        assert "burn_rate" in d
        assert "health" in d
        
        assert d["slo"]["name"] == "availability"
        assert d["budget"]["remaining_percent"] == 76.85


class TestDeploymentVerdict:
    """Tests for DeploymentVerdict dataclass."""
    
    def test_creation(self):
        """Test DeploymentVerdict creation."""
        from selfhealing.services.error_budget.models import DeploymentVerdict, ErrorBudgetStatus
        from selfhealing.services.error_budget.enums import FreezeStatus
        
        budget_status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=10.0,
            budget_remaining_minutes=33.2,
            budget_remaining_percent=76.85,
        )
        
        verdict = DeploymentVerdict(
            status=FreezeStatus.PROCEED,
            budget_status=budget_status,
            message="All clear for deployment",
            recommendation="Proceed with normal deployment",
            reasons=[],
            allowed_deployment_types=["feature", "bugfix", "hotfix"],
        )
        
        assert verdict.status == FreezeStatus.PROCEED
        assert verdict.message == "All clear for deployment"
        assert len(verdict.allowed_deployment_types) == 3
    
    def test_verdict_with_freeze_recommended(self):
        """Test DeploymentVerdict with freeze recommended."""
        from selfhealing.services.error_budget.models import DeploymentVerdict, ErrorBudgetStatus
        from selfhealing.services.error_budget.enums import FreezeStatus
        
        budget_status = ErrorBudgetStatus(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=40.0,
            budget_remaining_minutes=3.2,
            budget_remaining_percent=7.4,
        )
        
        verdict = DeploymentVerdict(
            status=FreezeStatus.FREEZE_RECOMMENDED,
            budget_status=budget_status,
            message="Error budget critically low",
            recommendation="Only hotfixes and security patches allowed",
            reasons=["Budget below 20%"],
            allowed_deployment_types=["hotfix", "security_patch"],
        )
        
        assert verdict.status == FreezeStatus.FREEZE_RECOMMENDED
        assert "hotfix" in verdict.allowed_deployment_types
        assert "feature" not in verdict.allowed_deployment_types


class TestFreezeDecisionRecord:
    """Tests for FreezeDecisionRecord dataclass."""
    
    def test_creation(self):
        """Test FreezeDecisionRecord creation."""
        from selfhealing.services.error_budget.models import FreezeDecisionRecord
        from selfhealing.services.error_budget.enums import FreezeStatus
        from selfhealing.core.timezone import now
        
        record = FreezeDecisionRecord(
            decision_id="freeze_20251229120000",
            decision_type="freeze_acknowledged",
            decided_by="admin@example.com",
            decided_at=now(),
            budget_remaining_percent=15.0,
            freeze_status=FreezeStatus.FREEZE_RECOMMENDED,
            justification="High error rate observed",
        )
        
        assert record.decision_id == "freeze_20251229120000"
        assert record.decision_type == "freeze_acknowledged"
        assert record.decided_by == "admin@example.com"
        assert record.freeze_status == FreezeStatus.FREEZE_RECOMMENDED
    
    def test_override_record(self):
        """Test FreezeDecisionRecord for override."""
        from selfhealing.services.error_budget.models import FreezeDecisionRecord
        from selfhealing.services.error_budget.enums import FreezeStatus, OverrideType
        from selfhealing.core.timezone import now
        
        record = FreezeDecisionRecord(
            decision_id="override_20251229120000",
            decision_type="override_approved",
            decided_by="cto@example.com",
            decided_at=now(),
            budget_remaining_percent=10.0,
            freeze_status=FreezeStatus.FREEZE_RECOMMENDED,
            justification="Critical security fix required",
            override_type=OverrideType.SECURITY_PATCH,
            deployment_id="deploy-123",
        )
        
        assert record.decision_type == "override_approved"
        assert record.override_type == OverrideType.SECURITY_PATCH
        assert record.deployment_id == "deploy-123"
