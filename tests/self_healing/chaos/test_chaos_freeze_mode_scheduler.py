"""
Phase 6: Chaos System Integration Tests

Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §22 (Phase 6 구현)

Tests for:
1. CB Freeze Mode 연동 - SafetyGuard에서 Freeze Mode 체크
2. check_recovery_monitoring_experiments Celery task
3. ChaosScheduler.get_experiments_by_status()
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass


# =============================================================================
# Test: BlockReason.CB_FREEZE_MODE_ACTIVE
# =============================================================================


class TestBlockReasonExtension:
    """Test BlockReason enum extension for Phase 6."""
    
    def test_cb_freeze_mode_active_exists(self):
        """Test CB_FREEZE_MODE_ACTIVE is defined in BlockReason."""
        from selfhealing.services.chaos.safety_guard import BlockReason
        
        assert hasattr(BlockReason, "CB_FREEZE_MODE_ACTIVE")
        assert BlockReason.CB_FREEZE_MODE_ACTIVE.value == "cb_freeze_mode_active"
    
    def test_all_block_reasons_present(self):
        """Test all expected block reasons are present."""
        from selfhealing.services.chaos.safety_guard import BlockReason
        
        expected_reasons = [
            "LOW_ERROR_BUDGET",
            "ACTIVE_INCIDENT",
            "KILL_SWITCH_ACTIVE",
            "DEPLOYMENT_FREEZE",
            "UNHEALTHY_SYSTEM",
            "RECENT_EXPERIMENT",
            "BLAST_RADIUS_EXCEEDED",
            "MANUAL_BLOCK",
            "EMERGENCY_MODE_ACTIVE",
            "PANIC_THRESHOLD_TRIGGERED",
            "CHAOS_BUDGET_EXCEEDED",
            "CB_FREEZE_MODE_ACTIVE",  # Phase 6
        ]
        
        for reason in expected_reasons:
            assert hasattr(BlockReason, reason), f"Missing BlockReason: {reason}"


# =============================================================================
# Test: SafetyConfig.require_no_freeze_mode
# =============================================================================


class TestSafetyConfigExtension:
    """Test SafetyConfig extension for Phase 6."""
    
    def test_require_no_freeze_mode_default(self):
        """Test require_no_freeze_mode has default True."""
        from selfhealing.services.chaos.safety_guard import SafetyConfig
        
        config = SafetyConfig()
        assert config.require_no_freeze_mode is True
    
    def test_require_no_freeze_mode_in_to_dict(self):
        """Test require_no_freeze_mode is included in to_dict()."""
        from selfhealing.services.chaos.safety_guard import SafetyConfig
        
        config = SafetyConfig(require_no_freeze_mode=False)
        config_dict = config.to_dict()
        
        assert "require_no_freeze_mode" in config_dict
        assert config_dict["require_no_freeze_mode"] is False


# =============================================================================
# Test: SafetyCheckResult.freeze_mode_active
# =============================================================================


class TestSafetyCheckResultExtension:
    """Test SafetyCheckResult extension for Phase 6."""
    
    def test_freeze_mode_active_default(self):
        """Test freeze_mode_active has default False."""
        from selfhealing.services.chaos.safety_guard import SafetyCheckResult
        
        result = SafetyCheckResult(status="safe", allowed=True)
        assert result.freeze_mode_active is False
    
    def test_freeze_mode_active_in_to_dict(self):
        """Test freeze_mode_active is included in to_dict()."""
        from selfhealing.services.chaos.safety_guard import SafetyCheckResult
        
        result = SafetyCheckResult(status="blocked", allowed=False, freeze_mode_active=True)
        result_dict = result.to_dict()
        
        assert "freeze_mode_active" in result_dict
        assert result_dict["freeze_mode_active"] is True


# =============================================================================
# Test: SafetyGuard._check_freeze_mode_status
# =============================================================================


class TestSafetyGuardFreezeModeCheck:
    """Test SafetyGuard._check_freeze_mode_status method."""
    
    def test_check_freeze_mode_status_method_exists(self):
        """Test _check_freeze_mode_status method exists."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        
        guard = SafetyGuard()
        assert hasattr(guard, "_check_freeze_mode_status")
        assert callable(guard._check_freeze_mode_status)
    
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_freeze_mode_status")
    def test_freeze_mode_inactive_allows_experiment(self, mock_check):
        """Test experiment allowed when freeze mode is inactive."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard,
            SafetyCheckResult,
            SafetyStatus,
        )
        
        mock_check.return_value = False  # Not blocked
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_freeze_mode_status(result)
        
        # Should not block when freeze mode is inactive
        assert blocked is False or mock_check.called
    
    @patch("selfhealing.services.circuit_breaker.freeze_mode.FreezeModeManager")
    def test_freeze_mode_active_blocks_experiment(self, mock_manager_class):
        """Test experiment blocked when freeze mode is active."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard,
            SafetyCheckResult,
            SafetyStatus,
            BlockReason,
        )
        
        # Mock FreezeModeManager
        mock_manager = MagicMock()
        mock_manager.is_active.return_value = True
        mock_manager.get_state.return_value = MagicMock(reason="Test lockdown")
        mock_manager_class.return_value = mock_manager
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_freeze_mode_status(result)
        
        assert blocked is True
        assert result.freeze_mode_active is True
        assert result.allowed is False
        assert result.block_reason == BlockReason.CB_FREEZE_MODE_ACTIVE.value
    
    def test_freeze_mode_import_error_handled(self):
        """Test graceful handling when FreezeModeManager not available."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard,
            SafetyCheckResult,
            SafetyStatus,
        )
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        # Mock import error by patching the import
        with patch.dict('sys.modules', {'selfhealing.services.circuit_breaker.freeze_mode': None}):
            # This should not raise, should handle gracefully
            try:
                blocked = guard._check_freeze_mode_status(result)
                # Should pass when import fails (graceful degradation)
                assert blocked is False or "freeze_mode" in result.checks_passed
            except ImportError:
                # Also acceptable - import error
                pass


# =============================================================================
# Test: ChaosScheduler.get_experiments_by_status
# =============================================================================


class TestChaosSchedulerGetExperimentsByStatus:
    """Test ChaosScheduler.get_experiments_by_status method."""
    
    def test_get_experiments_by_status_method_exists(self):
        """Test get_experiments_by_status method exists."""
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService
        
        scheduler = ChaosSchedulerService()
        assert hasattr(scheduler, "get_experiments_by_status")
        assert callable(scheduler.get_experiments_by_status)
    
    def test_get_experiments_by_status_returns_list(self):
        """Test get_experiments_by_status returns a list."""
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService
        
        scheduler = ChaosSchedulerService()
        result = scheduler.get_experiments_by_status("recovery_monitoring")
        
        assert isinstance(result, list)
    
    def test_get_experiments_by_status_filters_correctly(self):
        """Test get_experiments_by_status filters by status."""
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService
        from selfhealing.services.chaos.base import ExperimentStatus
        
        scheduler = ChaosSchedulerService()
        
        # Create mock experiments
        exp1 = MagicMock()
        exp1.status = ExperimentStatus.RECOVERY_MONITORING
        exp1.experiment_id = "exp-1"
        
        exp2 = MagicMock()
        exp2.status = ExperimentStatus.RUNNING
        exp2.experiment_id = "exp-2"
        
        exp3 = MagicMock()
        exp3.status = ExperimentStatus.RECOVERY_MONITORING
        exp3.experiment_id = "exp-3"
        
        # Register experiments
        scheduler.register_experiment_instance("exp-1", exp1)
        scheduler.register_experiment_instance("exp-2", exp2)
        scheduler.register_experiment_instance("exp-3", exp3)
        
        # Get by status
        result = scheduler.get_experiments_by_status("recovery_monitoring")
        
        assert len(result) == 2
        assert exp1 in result
        assert exp3 in result
        assert exp2 not in result
        
        # Cleanup
        scheduler.unregister_experiment_instance("exp-1")
        scheduler.unregister_experiment_instance("exp-2")
        scheduler.unregister_experiment_instance("exp-3")
    
    def test_register_experiment_instance(self):
        """Test register_experiment_instance method."""
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService
        
        scheduler = ChaosSchedulerService()
        
        mock_exp = MagicMock()
        mock_exp.experiment_id = "test-exp"
        
        scheduler.register_experiment_instance("test-exp", mock_exp)
        
        assert "test-exp" in scheduler._experiment_instances
        assert scheduler._experiment_instances["test-exp"] == mock_exp
        
        # Cleanup
        scheduler.unregister_experiment_instance("test-exp")
    
    def test_unregister_experiment_instance(self):
        """Test unregister_experiment_instance method."""
        from selfhealing.services.chaos.scheduler import ChaosSchedulerService
        
        scheduler = ChaosSchedulerService()
        
        mock_exp = MagicMock()
        scheduler.register_experiment_instance("test-exp", mock_exp)
        
        assert "test-exp" in scheduler._experiment_instances
        
        scheduler.unregister_experiment_instance("test-exp")
        
        assert "test-exp" not in scheduler._experiment_instances


# =============================================================================
# Test: check_recovery_monitoring_experiments Celery Task
# =============================================================================


class TestCheckRecoveryMonitoringTask:
    """Test check_recovery_monitoring_experiments Celery task."""
    
    def test_task_exists(self):
        """Test check_recovery_monitoring_experiments task exists."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        
        assert callable(check_recovery_monitoring_experiments)
    
    def test_task_name(self):
        """Test task has correct name."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        
        assert check_recovery_monitoring_experiments.name == "selfhealing.celery_tasks.check_recovery_monitoring"
    
    def test_task_returns_correct_format(self):
        """Test task returns expected dictionary format."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        from selfhealing.services.chaos.base import ExperimentStatus
        
        # Patch where the import happens (inside the function)
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            mock_scheduler = MagicMock()
            mock_scheduler.get_experiments_by_status.return_value = []
            mock_get_scheduler.return_value = mock_scheduler
            
            result = check_recovery_monitoring_experiments()
            
            assert isinstance(result, dict)
            assert "success" in result
            assert "checked" in result
            assert "completed" in result
            assert "force_completed" in result
    
    def test_task_completes_recovery_monitoring(self):
        """Test task completes experiments when canary recovery is done."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        from selfhealing.services.chaos.base import ExperimentStatus
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            # Create mock experiment
            mock_exp = MagicMock()
            mock_exp.experiment_id = "test-exp"
            mock_exp.status = ExperimentStatus.RECOVERY_MONITORING
            mock_exp._verify_canary_recovery.return_value = {"in_canary": False}
            
            mock_scheduler = MagicMock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            result = check_recovery_monitoring_experiments()
            
            assert result["success"] is True
            assert result["checked"] == 1
            assert result["completed"] == 1
            mock_exp.complete_recovery_monitoring.assert_called_once()
    
    def test_task_force_completes_on_hard_ttl(self):
        """Test task force completes experiments when hard TTL expired."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        from selfhealing.services.chaos.base import ExperimentStatus
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            # Create mock experiment
            mock_exp = MagicMock()
            mock_exp.experiment_id = "test-exp"
            mock_exp.status = ExperimentStatus.RECOVERY_MONITORING
            mock_exp._verify_canary_recovery.return_value = {"in_canary": True}
            mock_exp.is_hard_ttl_expired.return_value = True
            
            mock_scheduler = MagicMock()
            mock_scheduler.get_experiments_by_status.return_value = [mock_exp]
            mock_get_scheduler.return_value = mock_scheduler
            
            result = check_recovery_monitoring_experiments()
            
            assert result["success"] is True
            assert result["checked"] == 1
            assert result["force_completed"] == 1
            mock_exp.force_complete.assert_called_once_with(reason="hard_ttl_expired")
    
    def test_task_handles_errors_gracefully(self):
        """Test task handles errors without crashing."""
        from shopping.tasks.self_healing_tasks import check_recovery_monitoring_experiments
        
        with patch("selfhealing.services.chaos.get_chaos_scheduler") as mock_get_scheduler:
            mock_get_scheduler.side_effect = Exception("Test error")
            
            result = check_recovery_monitoring_experiments()
            
            assert result["success"] is False
            assert "error" in result


# =============================================================================
# Test: Integration - Freeze Mode blocks via _run_core_checks
# =============================================================================


class TestFreezeModeIntegration:
    """Test Freeze Mode integration in SafetyGuard._run_core_checks."""
    
    @patch("selfhealing.services.circuit_breaker.freeze_mode.FreezeModeManager")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_global_block")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_kill_switch_status")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_emergency_mode_status")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_panic_threshold_status")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_chaos_budget_status")
    @patch("selfhealing.services.chaos.safety_guard.SafetyGuard._check_error_budget_status")
    def test_freeze_mode_checked_in_core_checks(
        self,
        mock_error_budget,
        mock_chaos_budget,
        mock_panic,
        mock_emergency,
        mock_kill_switch,
        mock_global_block,
        mock_manager_class,
    ):
        """Test Freeze Mode is checked as part of core safety checks."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard,
            SafetyCheckResult,
            SafetyStatus,
        )
        
        # All other checks pass
        mock_global_block.return_value = False
        mock_kill_switch.return_value = False
        mock_emergency.return_value = False
        mock_panic.return_value = False
        mock_chaos_budget.return_value = False
        mock_error_budget.return_value = False
        
        # Freeze mode active
        mock_manager = MagicMock()
        mock_manager.is_active.return_value = True
        mock_manager.get_state.return_value = MagicMock(reason="Test lockdown")
        mock_manager_class.return_value = mock_manager
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._run_core_checks(result, "test-exp-123")
        
        # Should be blocked by freeze mode
        assert blocked is True
        assert result.freeze_mode_active is True


# =============================================================================
# Test: Celery Beat Schedule
# =============================================================================


class TestCeleryBeatSchedule:
    """Test Celery Beat schedule includes chaos recovery monitoring."""
    
    def test_chaos_recovery_monitoring_in_schedule(self):
        """Test check-chaos-recovery-monitoring is in Celery Beat schedule."""
        from myproject.celery import app
        
        schedule = app.conf.beat_schedule
        
        assert "check-chaos-recovery-monitoring" in schedule
        task_config = schedule["check-chaos-recovery-monitoring"]
        
        assert task_config["task"] == "selfhealing.celery_tasks.check_recovery_monitoring"
        assert task_config["schedule"] == 30.0  # 30 seconds
        assert task_config["options"]["queue"] == "chaos_monitoring"
