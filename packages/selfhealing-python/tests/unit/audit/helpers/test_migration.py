"""
Migration Integration Tests.

Tests for ChaosExperiment, EmergencyModeManager, and ErrorBudgetGate
migration to use audit helpers.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

from unittest.mock import patch


class TestChaosExperimentMigration:
    """Tests for ChaosExperiment._audit migration."""

    def test_audit_method_calls_helper(self):
        """ChaosExperiment._audit should call log_chaos_experiment_audit."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=1,
            ),
            patch(
                "selfhealing.services.audit.log_chaos_experiment_audit",
                return_value="audit-test1234",
            ) as mock_helper,
        ):
            from selfhealing.services.chaos.base import (
                ChaosExperiment,
                ExperimentConfig,
            )

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

            experiment._audit(
                "experiment_started",
                {
                    "config": {"target_service": "test-service"},
                    "ttl_seconds": 600,
                },
            )

            mock_helper.assert_called_once()
            call_kwargs = mock_helper.call_args[1]
            assert call_kwargs["experiment_id"] == "test-exp-123"
            assert call_kwargs["event_type"] == "experiment_started"

    def test_audit_records_list_preserved(self):
        """Should maintain _audit_records list for backward compatibility."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=1,
        ):
            from selfhealing.services.chaos.base import (
                ChaosExperiment,
            )

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


class TestEmergencyModeManagerMigration:
    """Tests for EmergencyModeManager._log_audit migration."""

    def test_log_audit_calls_helper(self):
        """EmergencyModeManager._log_audit should call log_emergency_mode_audit."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=1,
            ),
            patch(
                "selfhealing.services.audit.log_emergency_mode_audit",
                return_value=1,
            ) as mock_helper,
            patch(
                "selfhealing.core.state_backend.get_state_backend",
            ),
            patch(
                "selfhealing.services.event_bus.get_event_bus",
            ),
        ):
            from selfhealing.services.emergency_mode.enums import EmergencyLevel
            from selfhealing.services.emergency_mode.manager import (
                GracefulDegradationManager,
            )

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


class TestErrorBudgetGateMigration:
    """Tests for ErrorBudgetGate._audit_block migration."""

    def test_audit_block_calls_helper(self):
        """ErrorBudgetGate._audit_block should call log_error_budget_blocked_audit."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=1,
            ),
            patch(
                "selfhealing.services.audit.log_error_budget_blocked_audit",
                return_value=1,
            ) as mock_helper,
        ):
            from selfhealing.services.error_budget_gate.config import (
                GateCheckResult,
                GateStatus,
            )
            from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate

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

    def test_audit_block_handles_exceptions_gracefully(self):
        """Should not raise exception if audit helper fails."""
        with (
            patch(
                "selfhealing.services.audit.log_error_budget_blocked_audit",
                side_effect=Exception("Audit failed"),
            ),
            patch("selfhealing.services.error_budget_gate.gate.logger") as mock_logger,
        ):
            from selfhealing.services.error_budget_gate.config import (
                GateCheckResult,
                GateStatus,
            )
            from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate

            gate = ErrorBudgetGate()

            result = GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=5.5,
                threshold_percent=10.0,
                reason="Test",
            )

            # Should not raise
            gate._audit_block("test_action", result)

            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0][0]
            assert call_args == "error_budget_gate.failed_audit_block"
