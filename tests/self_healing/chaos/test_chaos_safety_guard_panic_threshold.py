"""
Phase 2 Unit Tests - Safety Guard Extensions (32_CHAOS_SYSTEM_INTEGRATION.md)

Tests for:
- SafetyGuard._check_panic_threshold() (§5)
- SafetyGuard._check_panic_threshold_status() (§5.2.1)
- ChaosExperiment._get_cb_state_snapshot() (§2.2.1)
- ChaosExperiment.capture_steady_state_with_cb() (§2.2.2)

Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §12.1 Phase 2
"""

from unittest.mock import patch, MagicMock

# Import at module level for @patch.object decorators
from selfhealing.services.chaos.safety_guard import SafetyGuard


# =============================================================================
# Panic Threshold Safety Guard Tests (§5)
# =============================================================================

class TestSafetyGuardPanicThreshold:
    """Test SafetyGuard panic threshold checks."""

    def test_block_reason_panic_threshold_exists(self):
        """Test that PANIC_THRESHOLD_TRIGGERED block reason exists."""
        from selfhealing.services.chaos.safety_guard import BlockReason
        
        assert hasattr(BlockReason, 'PANIC_THRESHOLD_TRIGGERED')
        assert BlockReason.PANIC_THRESHOLD_TRIGGERED.value == "panic_threshold_triggered"

    def test_safety_check_result_panic_fields(self):
        """Test SafetyCheckResult has panic-related fields."""
        from selfhealing.services.chaos.safety_guard import SafetyCheckResult
        
        result = SafetyCheckResult(
            status="safe",
            allowed=True,
        )
        
        # Default values
        assert result.panic_threshold_triggered is False
        assert result.panic_open_rate == 0.0
        assert result.panic_open_circuits == []

    def test_safety_check_result_to_dict_includes_panic(self):
        """Test SafetyCheckResult.to_dict() includes panic fields."""
        from selfhealing.services.chaos.safety_guard import SafetyCheckResult
        
        result = SafetyCheckResult(
            status="safe",
            allowed=True,
            panic_threshold_triggered=True,
            panic_open_rate=75.0,
            panic_open_circuits=["payment-api", "order-api"],
        )
        
        result_dict = result.to_dict()
        
        assert "panic_threshold_triggered" in result_dict
        assert result_dict["panic_threshold_triggered"] is True
        assert result_dict["panic_open_rate"] == 75.0
        assert "payment-api" in result_dict["panic_open_circuits"]

    @patch("selfhealing.services.circuit_breaker.panic_threshold.PanicThresholdMonitor")
    def test_check_panic_threshold_not_triggered(self, mock_monitor_cls):
        """Test _check_panic_threshold when panic is not triggered."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        
        # Mock PanicThresholdMonitor
        mock_monitor = MagicMock()
        mock_result = MagicMock()
        mock_result.triggered = False
        mock_result.open_rate = 30.0
        mock_result.open_count = 3
        mock_result.total_count = 10
        mock_result.open_circuits = ["payment-api", "order-api", "user-api"]
        mock_monitor.check_panic_threshold.return_value = mock_result
        mock_monitor_cls.return_value = mock_monitor
        
        guard = SafetyGuard()
        result = guard._check_panic_threshold()
        
        assert result["triggered"] is False
        assert result["open_rate"] == 30.0
        assert result["open_count"] == 3
        assert result["total_count"] == 10
        assert len(result["open_circuits"]) == 3

    @patch("selfhealing.services.circuit_breaker.panic_threshold.PanicThresholdMonitor")
    def test_check_panic_threshold_triggered(self, mock_monitor_cls):
        """Test _check_panic_threshold when panic IS triggered (70%+)."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        
        # Mock PanicThresholdMonitor - 70%+ OPEN
        mock_monitor = MagicMock()
        mock_result = MagicMock()
        mock_result.triggered = True
        mock_result.open_rate = 75.0
        mock_result.open_count = 15
        mock_result.total_count = 20
        mock_result.open_circuits = ["svc-1", "svc-2", "svc-3"]
        mock_monitor.check_panic_threshold.return_value = mock_result
        mock_monitor_cls.return_value = mock_monitor
        
        guard = SafetyGuard()
        result = guard._check_panic_threshold()
        
        assert result["triggered"] is True
        assert result["open_rate"] == 75.0
        assert result["open_count"] == 15

    @patch.object(SafetyGuard, '_check_panic_threshold')
    def test_check_panic_threshold_status_blocks_when_triggered(self, mock_check):
        """Test that _check_panic_threshold_status blocks when triggered."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus, BlockReason,
        )
        
        mock_check.return_value = {
            "triggered": True,
            "open_rate": 75.0,
            "open_count": 15,
            "total_count": 20,
            "open_circuits": ["svc-1", "svc-2"],
        }
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_panic_threshold_status(result)
        
        assert blocked is True
        assert result.allowed is False
        assert result.status == SafetyStatus.BLOCKED.value
        assert result.block_reason == BlockReason.PANIC_THRESHOLD_TRIGGERED.value
        assert "PANIC" in result.block_message
        assert "75.0%" in result.block_message
        assert result.panic_threshold_triggered is True
        assert result.panic_open_rate == 75.0
        assert "panic_threshold" in result.checks_failed

    @patch.object(SafetyGuard, '_check_panic_threshold')
    def test_check_panic_threshold_status_warning_at_50_percent(self, mock_check):
        """Test that _check_panic_threshold_status warns at 50%+ OPEN."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus,
        )
        
        mock_check.return_value = {
            "triggered": False,
            "open_rate": 55.0,
            "open_count": 11,
            "total_count": 20,
            "open_circuits": ["svc-1", "svc-2"],
        }
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_panic_threshold_status(result)
        
        assert blocked is False  # Not blocked
        assert result.allowed is True
        assert result.status == SafetyStatus.WARNING.value
        assert len(result.warnings) == 1
        assert "55.0%" in result.warnings[0]
        assert "panic_threshold" in result.checks_passed

    @patch.object(SafetyGuard, '_check_panic_threshold')
    def test_check_panic_threshold_status_passes_below_50_percent(self, mock_check):
        """Test that _check_panic_threshold_status passes cleanly below 50%."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus,
        )
        
        mock_check.return_value = {
            "triggered": False,
            "open_rate": 30.0,
            "open_count": 6,
            "total_count": 20,
            "open_circuits": ["svc-1"],
        }
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._check_panic_threshold_status(result)
        
        assert blocked is False
        assert result.allowed is True
        assert result.status == SafetyStatus.SAFE.value
        assert len(result.warnings) == 0
        assert "panic_threshold" in result.checks_passed

    def test_check_panic_threshold_exception_handling(self):
        """Test _check_panic_threshold handles exceptions gracefully."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        
        guard = SafetyGuard()
        
        # Mock import to fail
        with patch.dict('sys.modules', {'selfhealing.services.circuit_breaker.panic_threshold': None}):
            result = guard._check_panic_threshold()
        
        # Should fail-open (return not triggered)
        assert result["triggered"] is False
        assert result["open_rate"] == 0.0

    @patch.object(SafetyGuard, '_check_panic_threshold_status')
    @patch.object(SafetyGuard, '_check_emergency_mode_status')
    @patch.object(SafetyGuard, '_check_kill_switch_status')
    @patch.object(SafetyGuard, '_check_global_block')
    @patch.object(SafetyGuard, '_check_error_budget_status')
    def test_run_core_checks_includes_panic_threshold(
        self,
        mock_error_budget,
        mock_global_block,
        mock_kill_switch,
        mock_emergency,
        mock_panic,
    ):
        """Test that _run_core_checks includes panic threshold check."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, SafetyCheckResult, SafetyStatus,
        )
        
        # All checks pass except panic threshold
        mock_global_block.return_value = False
        mock_kill_switch.return_value = False
        mock_emergency.return_value = False
        mock_panic.return_value = True  # Panic blocks
        mock_error_budget.return_value = False
        
        guard = SafetyGuard()
        result = SafetyCheckResult(status=SafetyStatus.SAFE.value, allowed=True)
        
        blocked = guard._run_core_checks(result, "test-exp-123")
        
        assert blocked is True
        mock_panic.assert_called_once()


# =============================================================================
# CB State Snapshot Tests (§2)
# =============================================================================

class TestCBStateSnapshot:
    """Test ChaosExperiment CB state snapshot methods."""

    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_get_cb_state_snapshot_success(self, mock_get_cb):
        """Test _get_cb_state_snapshot returns correct structure."""
        from selfhealing.services.chaos.base import ChaosExperiment, ExperimentConfig
        
        # Mock CB service
        mock_service = MagicMock()
        mock_service.get_state.return_value = "CLOSED"
        mock_service.should_allow.return_value = True
        mock_get_cb.return_value = mock_service
        
        # Create a concrete implementation for testing
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> None:
                pass
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = TestExperiment(config=config)
        
        snapshot = experiment._get_cb_state_snapshot()
        
        assert "target_service_state" in snapshot
        assert snapshot["target_service_state"] == "CLOSED"
        assert "is_allowed" in snapshot
        assert snapshot["is_allowed"] is True
        assert "timestamp" in snapshot

    def test_get_cb_state_snapshot_exception_handling(self):
        """Test _get_cb_state_snapshot handles exceptions gracefully."""
        from selfhealing.services.chaos.base import ChaosExperiment, ExperimentConfig
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> None:
                pass
        
        config = ExperimentConfig(target_service="payment-api")
        experiment = TestExperiment(config=config)
        
        # Mock import to fail
        with patch.dict('sys.modules', {'selfhealing.services.circuit_breaker': None}):
            snapshot = experiment._get_cb_state_snapshot()
        
        # Should return empty dict on error
        assert snapshot == {}

    @patch("selfhealing.services.circuit_breaker.get_circuit_breaker_service")
    def test_capture_steady_state_with_cb(self, mock_get_cb):
        """Test capture_steady_state_with_cb includes CB state."""
        from selfhealing.services.chaos.base import ChaosExperiment, ExperimentConfig
        
        # Mock CB service
        mock_service = MagicMock()
        mock_service.get_state.return_value = "HALF_OPEN"
        mock_service.should_allow.return_value = True
        mock_get_cb.return_value = mock_service
        
        class TestExperiment(ChaosExperiment):
            experiment_type = "test"
            
            def inject_chaos(self) -> bool:
                return True
            
            def rollback(self) -> None:
                pass
        
        config = ExperimentConfig(target_service="order-api")
        experiment = TestExperiment(config=config)
        
        steady_state = experiment.capture_steady_state_with_cb()
        
        # Should have regular metrics
        assert "p50_latency_ms" in steady_state
        assert "p99_latency_ms" in steady_state
        assert "error_rate_percent" in steady_state
        
        # Should have CB state
        assert "circuit_breaker" in steady_state
        assert steady_state["circuit_breaker"]["target_service_state"] == "HALF_OPEN"


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase2Integration:
    """Integration tests for Phase 2 functionality."""

    @patch("selfhealing.services.circuit_breaker.panic_threshold.PanicThresholdMonitor")
    def test_safety_guard_check_with_panic(self, mock_monitor_cls):
        """Test full safety guard check with panic threshold."""
        from selfhealing.services.chaos.safety_guard import (
            SafetyGuard, BlockReason, SafetyStatus,
        )
        
        # Mock panic threshold as triggered
        mock_monitor = MagicMock()
        mock_result = MagicMock()
        mock_result.triggered = True
        mock_result.open_rate = 80.0
        mock_result.open_count = 8
        mock_result.total_count = 10
        mock_result.open_circuits = ["svc-1", "svc-2"]
        mock_monitor.check_panic_threshold.return_value = mock_result
        mock_monitor_cls.return_value = mock_monitor
        
        guard = SafetyGuard()
        
        # Mock other checks to pass
        with patch.object(guard, '_check_kill_switch', return_value=False):
            with patch.object(guard, '_check_emergency_mode', return_value={"active": False, "level": "NORMAL", "level_value": 0}):
                with patch.object(guard, '_check_error_budget', return_value={"remaining_percent": 100.0}):
                    result = guard.check(experiment_id="test-exp-456")
        
        assert result.allowed is False
        assert result.status == SafetyStatus.BLOCKED.value
        assert result.block_reason == BlockReason.PANIC_THRESHOLD_TRIGGERED.value
        assert "PANIC" in result.block_message
        assert result.panic_threshold_triggered is True
        assert result.panic_open_rate == 80.0

    def test_phase2_documentation_reference(self):
        """Verify Phase 2 implementation matches documentation."""
        from selfhealing.services.chaos.safety_guard import SafetyGuard
        from selfhealing.services.chaos.base import ChaosExperiment
        
        # Verify methods exist
        guard = SafetyGuard()
        assert hasattr(guard, '_check_panic_threshold')
        assert hasattr(guard, '_check_panic_threshold_status')
        assert hasattr(guard, '_log_panic_block_audit')
        
        # Verify ChaosExperiment has CB snapshot methods
        assert hasattr(ChaosExperiment, '_get_cb_state_snapshot')
        assert hasattr(ChaosExperiment, 'capture_steady_state_with_cb')
