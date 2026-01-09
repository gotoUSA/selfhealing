"""
Phase 3 Unit Tests - Chaos System Integration (32_CHAOS_SYSTEM_INTEGRATION.md)

Tests for:
- CircuitBreakerOpenExperiment._verify_canary_recovery() (§4)
- CircuitBreakerOpenExperiment._check_canary_started() (§4)
- FinOpsService.set_chaos_budget() / record_chaos_cost() (§17)
- SafetyGuard._check_chaos_budget_status() (§17.3)
- ChaosExperiment._record_hypothesis_validation() (§20.4)
- ChaosExperiment.record_finops_cost() (§10.2)

Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §12.1 Phase 3
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

# Import SafetyGuard at module level for @patch.object decorators
from selfhealing.services.chaos.safety_guard import SafetyGuard

# =============================================================================
# Canary Recovery Verification Tests (§4)
# =============================================================================

class TestCanaryRecoveryVerification:
    """Test CircuitBreakerOpenExperiment canary recovery methods."""

    @patch("selfhealing.services.circuit_breaker.canary_recovery.get_canary_recovery_manager")
    def test_verify_canary_recovery_in_canary(self, mock_get_manager):
        """Test _verify_canary_recovery when in canary state."""
        from selfhealing.services.chaos.experiment_impl import (
            CircuitBreakerOpenExperiment,
        )
        from selfhealing.services.chaos.base import ExperimentConfig
        
        # Mock CanaryRecoveryManager
        mock_manager = MagicMock()
        mock_state = MagicMock()
        mock_state.current_stage.value = "canary_1"
        mock_state.traffic_percent = 10.0
        mock_state.is_in_canary.return_value = True
        mock_state.success_rate = 95.0
        mock_state.stage_started_at = datetime(2026, 1, 9, 12, 0, 0, tzinfo=timezone.utc)
        mock_manager.get_recovery_state.return_value = mock_state
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = CircuitBreakerOpenExperiment(config=config)
        
        result = experiment._verify_canary_recovery()
        
        assert result["canary_state"] == "canary_1"
        assert result["traffic_percent"] == 10.0
        assert result["in_canary"] is True
        assert result["success_rate"] == 95.0

    @patch("selfhealing.services.circuit_breaker.canary_recovery.get_canary_recovery_manager")
    def test_verify_canary_recovery_not_in_canary(self, mock_get_manager):
        """Test _verify_canary_recovery when not in canary state."""
        from selfhealing.services.chaos.experiment_impl import CircuitBreakerOpenExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        # Mock no recovery state
        mock_manager = MagicMock()
        mock_manager.get_recovery_state.return_value = None
        mock_get_manager.return_value = mock_manager
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = CircuitBreakerOpenExperiment(config=config)
        
        result = experiment._verify_canary_recovery()
        
        assert result["canary_state"] == "not_in_canary"
        assert result["in_canary"] is False
        assert result["traffic_percent"] == 100.0

    def test_verify_canary_recovery_exception_handling(self):
        """Test _verify_canary_recovery handles exceptions gracefully."""
        from selfhealing.services.chaos.experiment_impl import CircuitBreakerOpenExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = CircuitBreakerOpenExperiment(config=config)
        
        # Mock import to fail
        with patch.dict('sys.modules', {'selfhealing.services.circuit_breaker.canary_recovery': None}):
            result = experiment._verify_canary_recovery()
        
        assert result["canary_state"] == "unknown"
        assert "error" in result

    def test_get_canary_verification_result(self):
        """Test get_canary_verification_result includes hypothesis match."""
        from selfhealing.services.chaos.experiment_impl import CircuitBreakerOpenExperiment
        from selfhealing.services.chaos.base import ExperimentConfig
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = CircuitBreakerOpenExperiment(config=config)
        
        # Mock _verify_canary_recovery
        with patch.object(experiment, '_verify_canary_recovery') as mock_verify:
            mock_verify.return_value = {
                "canary_state": "canary_1",
                "traffic_percent": 10.0,
                "in_canary": True,
            }
            
            result = experiment.get_canary_verification_result()
        
        assert "canary_status" in result
        assert result["canary_status"]["canary_state"] == "canary_1"
        assert "hypothesis_canary_match" in result
        assert result["expected_canary_stage"] == "canary_1"  # From CB_OPEN_HYPOTHESIS


# =============================================================================
# FinOps Chaos Budget Tests (§17)
# =============================================================================

class TestFinOpsChaosbudget:
    """Test FinOps chaos budget functionality."""

    def test_set_chaos_budget(self):
        """Test setting chaos budget."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()  # Reset state
        
        budget = finops.set_chaos_budget(
            max_budget=Decimal("100.00"),
            alert_threshold=0.8,
            hard_limit=True,
            reset_period="monthly",
        )
        
        assert budget is not None
        assert budget.max_budget == Decimal("100.00")
        assert budget.alert_threshold == 0.8
        assert budget.hard_limit is True

    def test_get_chaos_budget(self):
        """Test getting chaos budget."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        
        # Initially None
        assert finops.get_chaos_budget() is None
        
        # After setting
        finops.set_chaos_budget(max_budget=Decimal("50.00"))
        budget = finops.get_chaos_budget()
        
        assert budget is not None
        assert budget.max_budget == Decimal("50.00")

    def test_set_domain_weight(self):
        """Test domain weight setting."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        
        finops.set_domain_weight("payment", 3.0)
        finops.set_domain_weight("order", 2.0)
        
        assert finops.get_domain_weight("payment") == 3.0
        assert finops.get_domain_weight("order") == 2.0
        assert finops.get_domain_weight("unknown") == 1.0  # Default

    def test_domain_weight_max_cap(self):
        """Test domain weight is capped at MAX_CHAOS_WEIGHT_MULTIPLIER."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        
        finops.set_domain_weight("critical", 100.0)  # Exceeds max
        
        assert finops.get_domain_weight("critical") == finops.MAX_CHAOS_WEIGHT_MULTIPLIER

    def test_record_chaos_cost(self):
        """Test recording chaos experiment cost."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        
        # Set up budget
        finops.set_chaos_budget(max_budget=Decimal("100.00"))
        finops.set_domain_weight("payment", 2.0)
        
        # Record cost
        record = finops.record_chaos_cost(
            experiment_id="exp-123",
            experiment_type="circuit_breaker_open",
            target_domain="payment",
            success=True,
        )
        
        assert record is not None
        assert record.operation == "chaos_test"
        # Base cost is 0.02, weight is 2.0, so final cost is 0.04
        assert record.cost == Decimal("0.04")
        assert record.metadata["domain_weight"] == 2.0

    def test_record_chaos_cost_dry_run_skipped(self):
        """Test that dry_run experiments don't record cost."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        finops.set_chaos_budget(max_budget=Decimal("100.00"))
        
        record = finops.record_chaos_cost(
            experiment_id="exp-456",
            experiment_type="latency_injection",
            target_domain="order",
            dry_run=True,
        )
        
        assert record is None

    def test_get_chaos_budget_status(self):
        """Test getting chaos budget status."""
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        finops.clear()
        
        # Not configured
        status = finops.get_chaos_budget_status()
        assert status["configured"] is False
        
        # Configured
        finops.set_chaos_budget(max_budget=Decimal("100.00"))
        status = finops.get_chaos_budget_status()
        
        assert status["configured"] is True
        assert status["max_budget"] == "100.00"
        assert status["usage_percent"] == 0.0


# =============================================================================
# SafetyGuard Chaos Budget Tests (§17.3)
# =============================================================================

class TestSafetyGuardChaosBudget:
    """Test SafetyGuard chaos budget checks."""

    def test_block_reason_chaos_budget_exceeded_exists(self):
        """Test that CHAOS_BUDGET_EXCEEDED block reason exists."""
        from selfhealing.services.chaos.safety_guard import BlockReason
        
        assert hasattr(BlockReason, 'CHAOS_BUDGET_EXCEEDED')
        assert BlockReason.CHAOS_BUDGET_EXCEEDED.value == "chaos_budget_exceeded"

    @patch("selfhealing.services.finops.service.FinOpsService")
    def test_check_chaos_budget_status_not_configured(self, mock_finops_cls):
        """Test _check_chaos_budget_status when not configured."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus,
        )
        
        mock_finops = MagicMock()
        mock_finops.get_chaos_budget_status.return_value = {"configured": False}
        mock_finops_cls.return_value = mock_finops
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_chaos_budget_status(result)
        
        assert blocked is False
        assert "chaos_budget" in result.checks_passed

    @patch("selfhealing.services.finops.service.FinOpsService")
    def test_check_chaos_budget_status_blocks_when_exceeded(self, mock_finops_cls):
        """Test _check_chaos_budget_status blocks when budget exceeded."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus, BlockReason,
        )
        
        mock_finops = MagicMock()
        mock_finops.get_chaos_budget_status.return_value = {
            "configured": True,
            "max_budget": "100.00",
            "current_spent": "110.00",
            "usage_percent": 110.0,
            "is_over_budget": True,
            "alert_threshold": 0.8,
        }
        mock_finops_cls.return_value = mock_finops
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_chaos_budget_status(result)
        
        assert blocked is True
        assert result.allowed is False
        assert result.status == SafetyStatus.BLOCKED.value
        assert result.block_reason == BlockReason.CHAOS_BUDGET_EXCEEDED.value
        assert "110.0%" in result.block_message
        assert "chaos_budget" in result.checks_failed

    @patch("selfhealing.services.finops.service.FinOpsService")
    def test_check_chaos_budget_status_warns_at_80_percent(self, mock_finops_cls):
        """Test _check_chaos_budget_status warns at 80%+ usage."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus,
        )
        
        mock_finops = MagicMock()
        mock_finops.get_chaos_budget_status.return_value = {
            "configured": True,
            "usage_percent": 85.0,
            "is_over_budget": False,
            "alert_threshold": 0.8,
        }
        mock_finops_cls.return_value = mock_finops
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_chaos_budget_status(result)
        
        assert blocked is False  # Not blocked
        assert result.allowed is True
        assert result.status == SafetyStatus.WARNING.value
        assert len(result.warnings) == 1
        assert "85.0%" in result.warnings[0]
        assert "chaos_budget" in result.checks_passed


# =============================================================================
# Hypothesis Validation Tests (§20.4)
# =============================================================================

class TestHypothesisValidation:
    """Test FailureHypothesis validation and LearningService integration."""

    def test_failure_hypothesis_validate_pass(self):
        """Test FailureHypothesis.validate() when all conditions pass."""
        from selfhealing.services.chaos.experiment_impl import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
            tolerance_percent=20.0,
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=25.0,  # Within tolerance
            actual_canary_stage="canary_1",
            actual_cb_state="half_open",
        )
        
        assert passed is True
        assert len(violations) == 0

    def test_failure_hypothesis_validate_fail_recovery_time(self):
        """Test FailureHypothesis.validate() fails when recovery time exceeded."""
        from selfhealing.services.chaos.experiment_impl import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            tolerance_percent=20.0,
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=50.0,  # Exceeds 36s (30 + 20%)
        )
        
        assert passed is False
        assert len(violations) == 1
        assert "Recovery time" in violations[0]

    def test_failure_hypothesis_validate_fail_canary_stage(self):
        """Test FailureHypothesis.validate() fails on canary stage mismatch."""
        from selfhealing.services.chaos.experiment_impl import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            expected_recovery_time_seconds=30.0,
            expected_canary_stage="canary_1",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=25.0,
            actual_canary_stage="canary_2",
        )
        
        assert passed is False
        assert len(violations) == 1
        assert "Canary stage" in violations[0]

    def test_failure_hypothesis_to_dict(self):
        """Test FailureHypothesis.to_dict() serialization."""
        from selfhealing.services.chaos.experiment_impl import FailureHypothesis
        
        hypothesis = FailureHypothesis(
            description="Test hypothesis",
            expected_recovery_time_seconds=45.0,
            expected_canary_stage="canary_1",
        )
        
        result = hypothesis.to_dict()
        
        assert result["description"] == "Test hypothesis"
        assert result["expected_recovery_time_seconds"] == 45.0
        assert result["expected_canary_stage"] == "canary_1"

    def test_record_hypothesis_validation_method_exists(self):
        """Test that _record_hypothesis_validation method exists on ChaosExperiment."""
        from selfhealing.services.chaos.base import ChaosExperiment
        
        assert hasattr(ChaosExperiment, '_record_hypothesis_validation')

    def test_record_finops_cost_method_exists(self):
        """Test that record_finops_cost method exists on ChaosExperiment."""
        from selfhealing.services.chaos.base import ChaosExperiment
        
        assert hasattr(ChaosExperiment, 'record_finops_cost')


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase3Integration:
    """Integration tests for Phase 3 functionality."""

    def test_phase3_documentation_reference(self):
        """Verify Phase 3 implementation matches documentation."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard, BlockReason
        from selfhealing.services.chaos.base import ChaosExperiment
        from selfhealing.services.chaos.experiment_impl import (
            CircuitBreakerOpenExperiment,
            FailureHypothesis,
        )
        from selfhealing.services.finops.service import FinOpsService
        
        # Verify SafetyGuard has chaos budget check
        guard = SafetyGuard()
        assert hasattr(guard, '_check_chaos_budget_status')
        assert hasattr(guard, '_notify_chaos_budget_exceeded')
        assert hasattr(guard, '_notify_chaos_budget_warning')
        
        # Verify BlockReason
        assert hasattr(BlockReason, 'CHAOS_BUDGET_EXCEEDED')
        
        # Verify ChaosExperiment has Phase 3 methods
        assert hasattr(ChaosExperiment, '_record_hypothesis_validation')
        assert hasattr(ChaosExperiment, 'record_finops_cost')
        
        # Verify CircuitBreakerOpenExperiment has canary verification
        assert hasattr(CircuitBreakerOpenExperiment, '_verify_canary_recovery')
        assert hasattr(CircuitBreakerOpenExperiment, '_check_canary_started')
        assert hasattr(CircuitBreakerOpenExperiment, 'get_canary_verification_result')
        
        # Verify FinOpsService has chaos budget methods
        finops = FinOpsService()
        assert hasattr(finops, 'set_chaos_budget')
        assert hasattr(finops, 'get_chaos_budget')
        assert hasattr(finops, 'set_domain_weight')
        assert hasattr(finops, 'get_domain_weight')
        assert hasattr(finops, 'record_chaos_cost')
        assert hasattr(finops, 'get_chaos_budget_status')

    def test_cb_open_experiment_has_failure_hypothesis(self):
        """Test CircuitBreakerOpenExperiment has failure_hypothesis defined."""
        from selfhealing.services.chaos.experiment_impl import (
            CircuitBreakerOpenExperiment,
            CB_OPEN_HYPOTHESIS,
        )
        
        assert CircuitBreakerOpenExperiment.failure_hypothesis is not None
        assert CircuitBreakerOpenExperiment.failure_hypothesis == CB_OPEN_HYPOTHESIS
        assert CB_OPEN_HYPOTHESIS.expected_recovery_time_seconds == 60.0
        assert CB_OPEN_HYPOTHESIS.expected_canary_stage == "canary_1"
        assert CB_OPEN_HYPOTHESIS.expected_cb_state_after == "half_open"

    @patch("selfhealing.services.finops.service.FinOpsService")
    @patch.object(SafetyGuard, '_check_panic_threshold_status', return_value=False)
    @patch.object(SafetyGuard, '_check_emergency_mode_status', return_value=False)
    @patch.object(SafetyGuard, '_check_kill_switch_status', return_value=False)
    @patch.object(SafetyGuard, '_check_global_block', return_value=False)
    @patch.object(SafetyGuard, '_check_error_budget_status', return_value=False)
    def test_run_core_checks_includes_chaos_budget(
        self,
        mock_error_budget,
        mock_global_block,
        mock_kill_switch,
        mock_emergency,
        mock_panic,
        mock_finops_cls,
    ):
        """Test that _run_core_checks includes chaos budget check."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus, BlockReason,
        )
        
        # Mock budget as exceeded
        mock_finops = MagicMock()
        mock_finops.get_chaos_budget_status.return_value = {
            "configured": True,
            "usage_percent": 100.0,
            "is_over_budget": True,
            "alert_threshold": 0.8,
        }
        mock_finops_cls.return_value = mock_finops
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._run_core_checks(result, "test-exp-789")
        
        assert blocked is True
        assert result.block_reason == BlockReason.CHAOS_BUDGET_EXCEEDED.value
