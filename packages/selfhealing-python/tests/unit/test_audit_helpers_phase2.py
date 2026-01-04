"""
Tests for Phase 2 audit helpers.

Tests chaos experiment, emergency mode, and error budget gate audit logging.
Reference: docs/self_healing/middleware_system/20_AUDIT_UNIFICATION_PLAN.md

Phase 2 goals:
- Chaos experiment audit integration (ChaosExperiment._audit → log_chaos_experiment_audit)
- Emergency mode audit integration (EmergencyModeManager._log_audit → log_emergency_mode_audit)
- Error budget gate audit integration (ErrorBudgetGate._audit_block → log_error_budget_blocked_audit)
"""

import pytest
from unittest.mock import MagicMock, patch, call
import logging


# =============================================================================
# AuditEventType Tests
# =============================================================================


class TestAuditEventTypePhase2:
    """Tests for Phase 2 AuditEventType additions."""

    def test_chaos_event_types_exist(self):
        """Should have Chaos-related event types."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "CHAOS_EXPERIMENT_STARTED")
        assert hasattr(AuditEventType, "CHAOS_EXPERIMENT_COMPLETED")
        assert hasattr(AuditEventType, "CHAOS_INJECTION_APPLIED")
        assert hasattr(AuditEventType, "CHAOS_ROLLBACK_TRIGGERED")
        
        assert AuditEventType.CHAOS_EXPERIMENT_STARTED.value == "chaos_experiment_started"
        assert AuditEventType.CHAOS_EXPERIMENT_COMPLETED.value == "chaos_experiment_completed"
        assert AuditEventType.CHAOS_INJECTION_APPLIED.value == "chaos_injection_applied"
        assert AuditEventType.CHAOS_ROLLBACK_TRIGGERED.value == "chaos_rollback_triggered"

    def test_emergency_mode_event_types_exist(self):
        """Should have Emergency Mode-related event types."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "EMERGENCY_MODE_ACTIVATED")
        assert hasattr(AuditEventType, "EMERGENCY_MODE_DEACTIVATED")
        
        assert AuditEventType.EMERGENCY_MODE_ACTIVATED.value == "emergency_mode_activated"
        assert AuditEventType.EMERGENCY_MODE_DEACTIVATED.value == "emergency_mode_deactivated"

    def test_error_budget_event_types_exist(self):
        """Should have Error Budget-related event types."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        assert hasattr(AuditEventType, "ERROR_BUDGET_DEPLETED")
        assert hasattr(AuditEventType, "ERROR_BUDGET_BLOCKED")
        
        assert AuditEventType.ERROR_BUDGET_DEPLETED.value == "error_budget_depleted"
        assert AuditEventType.ERROR_BUDGET_BLOCKED.value == "error_budget_blocked"


# =============================================================================
# Chaos Experiment Audit Helper Tests
# =============================================================================


class TestLogChaosExperimentAudit:
    """Tests for log_chaos_experiment_audit function."""

    def test_returns_record_id(self):
        """Should return audit record ID."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ):
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            record_id = log_chaos_experiment_audit(
                experiment_id="chaos-abc123",
                event_type="experiment_started",
                experiment_type="latency_injection",
            )
            
            assert record_id.startswith("audit-")
            assert len(record_id) == 14  # "audit-" + 8 hex chars

    def test_logs_experiment_started_to_wal(self):
        """Should write experiment_started to WAL with correct event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-test123",
                event_type="experiment_started",
                experiment_type="latency_injection",
                config={"target_service": "payment-api", "injection_rate": 0.01},
                ttl_seconds=600,
                expires_at="2026-01-05T10:00:00+00:00",
            )
            
            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "CHAOS_EXPERIMENT_STARTED"
            assert call_kwargs["source"] == "ChaosExperiment"
            assert call_kwargs["target_id"] == "chaos-test123"
            assert "experiment_started" in call_kwargs["details"]["event_type"]

    def test_logs_experiment_completed_to_wal(self):
        """Should write experiment_completed with CHAOS_EXPERIMENT_COMPLETED event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=2,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-completed123",
                event_type="experiment_completed",
                experiment_type="error_5xx",
                result={"status": "completed", "errors_injected": 150},
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "CHAOS_EXPERIMENT_COMPLETED"

    def test_logs_rollback_triggered_to_wal(self):
        """Should write rollback events with CHAOS_ROLLBACK_TRIGGERED event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=3,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            # Test various rollback-related events
            for event in ["rollback_started", "kill_requested", "auto_abort_ttl_expired", "auto_abort_stop_condition"]:
                log_chaos_experiment_audit(
                    experiment_id="chaos-abort123",
                    event_type=event,
                    reason="SLA breach detected",
                )
                
                call_kwargs = mock_wal.call_args[1]
                assert call_kwargs["event_type"] == "CHAOS_ROLLBACK_TRIGGERED", f"Failed for {event}"

    def test_logs_chaos_injection_applied(self):
        """Should write injection events with CHAOS_INJECTION_APPLIED event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=4,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-inject123",
                event_type="chaos_injection_started",
                dry_run=False,
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "CHAOS_INJECTION_APPLIED"

    def test_includes_dry_run_flag(self):
        """Should include dry_run flag in details."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=5,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-dry123",
                event_type="chaos_injection_simulated",
                dry_run=True,
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["dry_run"] is True

    def test_logs_to_standard_logger_fallback(self, caplog):
        """Should log to standard logger when no request/buffer available."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=6,
        ):
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            with caplog.at_level(logging.INFO):
                log_chaos_experiment_audit(
                    experiment_id="chaos-log123",
                    event_type="experiment_started",
                )
            
            assert "[ChaosAudit]" in caplog.text
            assert "chaos-log123" in caplog.text
            assert "experiment_started" in caplog.text

    def test_removes_none_values_from_details(self):
        """Should remove None values from details dict."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=7,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-clean123",
                event_type="experiment_started",
                config=None,
                result=None,
                violations=None,
            )
            
            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]
            assert "config" not in details
            assert "result" not in details
            assert "violations" not in details


# =============================================================================
# Emergency Mode Audit Helper Tests
# =============================================================================


class TestLogEmergencyModeAudit:
    """Tests for log_emergency_mode_audit function."""

    def test_logs_activation_to_wal(self):
        """Should write activation events with EMERGENCY_MODE_ACTIVATED event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ) as mock_wal, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            result = log_emergency_mode_audit(
                action="activate",
                level="LEVEL_2",
                is_active=True,
                activated_by="admin",
                reason="High error rate detected",
                expires_at="2026-01-05T11:00:00+00:00",
            )
            
            assert result == 1
            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "EMERGENCY_MODE_ACTIVATED"
            assert call_kwargs["source"] == "EmergencyModeManager"

    def test_logs_auto_activation_to_wal(self):
        """Should use EMERGENCY_MODE_ACTIVATED for auto_activate action."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=2,
        ) as mock_wal, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            log_emergency_mode_audit(
                action="auto_activate",
                level="LEVEL_1",
                is_active=True,
                activated_by="system",
                is_auto_triggered=True,
                reason="Circuit breaker cascade",
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "EMERGENCY_MODE_ACTIVATED"
            assert call_kwargs["details"]["is_auto_triggered"] is True

    def test_logs_deactivation_to_wal(self):
        """Should write deactivation events with EMERGENCY_MODE_DEACTIVATED event type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=3,
        ) as mock_wal, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            log_emergency_mode_audit(
                action="deactivate",
                level="NORMAL",
                is_active=False,
                deactivated_by="admin",
                reason="System recovered",
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "EMERGENCY_MODE_DEACTIVATED"
            assert call_kwargs["details"]["deactivated_by"] == "admin"

    def test_includes_severity_based_on_action(self):
        """Should set severity based on action type."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=4,
        ) as mock_wal, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            # Activation should be critical
            log_emergency_mode_audit(
                action="activate",
                level="LEVEL_3",
                is_active=True,
                activated_by="admin",
                reason="Critical failure",
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["severity"] == "critical"
            
            # Deactivation should be warning
            log_emergency_mode_audit(
                action="deactivate",
                level="NORMAL",
                is_active=False,
                deactivated_by="admin",
                reason="Recovered",
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["severity"] == "warning"

    def test_includes_tag_field(self):
        """Should include formatted tag field."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=5,
        ) as mock_wal, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            log_emergency_mode_audit(
                action="escalate",
                level="LEVEL_3",
                is_active=True,
                activated_by="system",
                reason="Escalation",
            )
            
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["tag"] == "EMERGENCY_ESCALATE"

    def test_logs_to_standard_logger_fallback(self, caplog):
        """Should log to standard logger when no request/buffer available."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=6,
        ), patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            with caplog.at_level(logging.INFO):
                log_emergency_mode_audit(
                    action="activate",
                    level="LEVEL_1",
                    is_active=True,
                    activated_by="admin",
                    reason="Test reason",
                )
            
            assert "[EmergencyModeAudit]" in caplog.text
            assert "ACTIVATE" in caplog.text
            assert "level=LEVEL_1" in caplog.text

    def test_calls_log_config_change_for_compatibility(self):
        """Should also call log_config_change for backward compatibility."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=7,
        ), patch(
            "selfhealing.audit.log_config_change",
        ) as mock_config_change:
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            log_emergency_mode_audit(
                action="activate",
                level="LEVEL_2",
                is_active=True,
                activated_by="admin",
                reason="Test",
            )
            
            mock_config_change.assert_called_once()
            call_kwargs = mock_config_change.call_args[1]
            assert call_kwargs["config_type"] == "emergency_mode"
            assert call_kwargs["config_key"] == "state"
            assert call_kwargs["user"] == "admin"


# =============================================================================
# Error Budget Gate Audit Helper Tests
# =============================================================================


class TestLogErrorBudgetBlockedAudit:
    """Tests for log_error_budget_blocked_audit function."""

    def test_logs_blocked_action_to_wal(self):
        """Should write blocked events to WAL."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            result = log_error_budget_blocked_audit(
                action="chaos_experiment",
                gate_status="blocked",
                error_budget_percent=5.5,
                threshold_percent=10.0,
                reason="Error budget critically low: 5.5% < 10%",
            )
            
            assert result == 1
            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "ERROR_BUDGET_BLOCKED"
            assert call_kwargs["source"] == "ErrorBudgetGate"
            assert call_kwargs["target_id"] == "chaos_experiment"
            assert call_kwargs["success"] is False

    def test_includes_error_budget_details(self):
        """Should include error budget details in WAL entry."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=2,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            log_error_budget_blocked_audit(
                action="auto_replay",
                gate_status="blocked",
                error_budget_percent=3.2,
                threshold_percent=10.0,
                reason="Budget low",
            )
            
            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]
            assert details["action"] == "auto_replay"
            assert details["gate_status"] == "blocked"
            assert details["error_budget_percent"] == 3.2
            assert details["threshold_percent"] == 10.0
            assert details["manual_mode_enforced"] is True

    def test_logs_rate_limited_status(self):
        """Should handle fail_open_rate_limited status."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=3,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            log_error_budget_blocked_audit(
                action="deployment",
                gate_status="fail_open_rate_limited",
                error_budget_percent=None,
                threshold_percent=10.0,
                reason="Rate limit exceeded during fail-open",
            )
            
            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]
            assert details["gate_status"] == "fail_open_rate_limited"
            assert "error_budget_percent" not in details  # None values removed

    def test_logs_to_standard_logger_fallback(self, caplog):
        """Should log to standard logger when no request/buffer available."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=4,
        ):
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            with caplog.at_level(logging.WARNING):
                log_error_budget_blocked_audit(
                    action="chaos_experiment",
                    gate_status="blocked",
                    error_budget_percent=5.5,
                    threshold_percent=10.0,
                    reason="Budget low",
                )
            
            assert "[ErrorBudgetAudit]" in caplog.text
            assert "BLOCKED" in caplog.text
            assert "chaos_experiment" in caplog.text
            assert "5.5%" in caplog.text

    def test_handles_none_error_budget_percent(self, caplog):
        """Should handle None error_budget_percent gracefully."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=5,
        ):
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            with caplog.at_level(logging.WARNING):
                log_error_budget_blocked_audit(
                    action="test_action",
                    gate_status="blocked",
                    error_budget_percent=None,
                    threshold_percent=10.0,
                    reason="Budget unavailable",
                )
            
            assert "N/A" in caplog.text  # None displayed as N/A


# =============================================================================
# ChaosExperiment Integration Tests
# =============================================================================


class TestChaosExperimentMigration:
    """Tests for ChaosExperiment._audit migration."""

    def test_audit_method_calls_helper(self):
        """ChaosExperiment._audit should call log_chaos_experiment_audit."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers.log_chaos_experiment_audit",
            return_value="audit-test1234",
        ) as mock_helper:
            from selfhealing.services.chaos.base import ChaosExperiment, ExperimentConfig
            
            # Create a concrete subclass for testing
            class TestExperiment(ChaosExperiment):
                experiment_type = "test"
                
                def inject_chaos(self) -> bool:
                    return True
                
                def rollback(self) -> None:
                    pass
            
            experiment = TestExperiment(
                experiment_id="test-exp-123",
                config=ExperimentConfig(target_service="test-service"),
            )
            
            experiment._audit("experiment_started", {
                "config": {"target_service": "test-service"},
                "ttl_seconds": 600,
            })
            
            mock_helper.assert_called_once()
            call_kwargs = mock_helper.call_args[1]
            assert call_kwargs["experiment_id"] == "test-exp-123"
            assert call_kwargs["event_type"] == "experiment_started"

    def test_audit_records_list_preserved(self):
        """Should maintain _audit_records list for backward compatibility."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ):
            from selfhealing.services.chaos.base import ChaosExperiment, ExperimentConfig
            
            class TestExperiment(ChaosExperiment):
                experiment_type = "test"
                
                def inject_chaos(self) -> bool:
                    return True
                
                def rollback(self) -> None:
                    pass
            
            experiment = TestExperiment(experiment_id="test-exp-456")
            
            # Call _audit multiple times
            experiment._audit("event1", {})
            experiment._audit("event2", {})
            experiment._audit("event3", {})
            
            # _audit_records should contain all record IDs
            assert len(experiment._audit_records) == 3
            for record_id in experiment._audit_records:
                assert record_id.startswith("audit-")


# =============================================================================
# EmergencyModeManager Integration Tests
# =============================================================================


class TestEmergencyModeManagerMigration:
    """Tests for EmergencyModeManager._log_audit migration."""

    def test_log_audit_calls_helper(self):
        """EmergencyModeManager._log_audit should call log_emergency_mode_audit."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers.log_emergency_mode_audit",
            return_value=1,
        ) as mock_helper, patch(
            "selfhealing.core.state_backend.get_state_backend",
        ), patch(
            "selfhealing.services.event_bus.get_event_bus",
        ):
            from selfhealing.services.emergency_mode.manager import GracefulDegradationManager
            from selfhealing.services.emergency_mode.enums import EmergencyLevel
            
            # Reset singleton for testing
            GracefulDegradationManager._instance = None
            manager = GracefulDegradationManager()
            
            # Manually set state for testing
            manager._state.level = EmergencyLevel.LEVEL_2
            manager._state.is_active = True
            manager._state.is_auto_triggered = False
            manager._state.expires_at = "2026-01-05T11:00:00+00:00"
            
            manager._log_audit("activate", "admin", "High error rate")
            
            mock_helper.assert_called_once()
            call_kwargs = mock_helper.call_args[1]
            assert call_kwargs["action"] == "activate"
            assert call_kwargs["level"] == "LEVEL_2"
            assert call_kwargs["is_active"] is True
            assert call_kwargs["activated_by"] == "admin"
            assert call_kwargs["reason"] == "High error rate"
            
            # Cleanup singleton
            GracefulDegradationManager._instance = None


# =============================================================================
# ErrorBudgetGate Integration Tests
# =============================================================================


class TestErrorBudgetGateMigration:
    """Tests for ErrorBudgetGate._audit_block migration."""

    def test_audit_block_calls_helper(self):
        """ErrorBudgetGate._audit_block should call log_error_budget_blocked_audit."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers.log_error_budget_blocked_audit",
            return_value=1,
        ) as mock_helper:
            from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate
            from selfhealing.services.error_budget_gate.config import GateCheckResult, GateStatus
            
            gate = ErrorBudgetGate()
            
            result = GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=5.5,
                threshold_percent=10.0,
                reason="Error budget critically low",
            )
            
            gate._audit_block("chaos_experiment", result)
            
            mock_helper.assert_called_once()
            call_kwargs = mock_helper.call_args[1]
            assert call_kwargs["action"] == "chaos_experiment"
            assert call_kwargs["gate_status"] == "blocked"
            assert call_kwargs["error_budget_percent"] == 5.5
            assert call_kwargs["threshold_percent"] == 10.0

    def test_audit_block_handles_exceptions_gracefully(self, caplog):
        """Should not raise exception if audit helper fails."""
        with patch(
            "selfhealing.services.audit_helpers.log_error_budget_blocked_audit",
            side_effect=Exception("Audit failed"),
        ):
            from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate
            from selfhealing.services.error_budget_gate.config import GateCheckResult, GateStatus
            
            gate = ErrorBudgetGate()
            
            result = GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=5.5,
                threshold_percent=10.0,
                reason="Test",
            )
            
            with caplog.at_level(logging.WARNING):
                # Should not raise
                gate._audit_block("test_action", result)
            
            assert "Failed to audit block" in caplog.text


# =============================================================================
# Buffer Integration Tests
# =============================================================================


class TestBufferIntegration:
    """Tests for request buffer integration."""

    def test_chaos_audit_adds_to_buffer_when_request_provided(self):
        """Should add to buffer when request is provided."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            mock_request = MagicMock()
            
            log_chaos_experiment_audit(
                experiment_id="chaos-buf123",
                event_type="experiment_started",
                request=mock_request,
            )
            
            mock_buffer.assert_called_once()
            call_kwargs = mock_buffer.call_args[1]
            assert call_kwargs["request"] == mock_request
            assert call_kwargs["source"] == "ChaosExperiment"

    def test_emergency_audit_adds_to_buffer_when_request_provided(self):
        """Should add to buffer when request is provided."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit_helpers import log_emergency_mode_audit
            
            mock_request = MagicMock()
            
            log_emergency_mode_audit(
                action="activate",
                level="LEVEL_1",
                is_active=True,
                activated_by="admin",
                reason="Test",
                request=mock_request,
            )
            
            mock_buffer.assert_called_once()
            call_kwargs = mock_buffer.call_args[1]
            assert call_kwargs["request"] == mock_request
            assert call_kwargs["source"] == "EmergencyModeManager"

    def test_error_budget_audit_adds_to_buffer_when_request_provided(self):
        """Should add to buffer when request is provided."""
        with patch(
            "selfhealing.services.audit_helpers._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit_helpers._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer:
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            mock_request = MagicMock()
            
            log_error_budget_blocked_audit(
                action="test",
                gate_status="blocked",
                error_budget_percent=5.0,
                threshold_percent=10.0,
                reason="Test",
                request=mock_request,
            )
            
            mock_buffer.assert_called_once()
            call_kwargs = mock_buffer.call_args[1]
            assert call_kwargs["request"] == mock_request
            assert call_kwargs["source"] == "ErrorBudgetGate"
