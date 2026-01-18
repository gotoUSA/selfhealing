"""
Chaos Experiment Audit Helper Tests.

Tests for log_chaos_experiment_audit function.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

import pytest
from unittest.mock import patch


class TestLogChaosExperimentAudit:
    """Tests for log_chaos_experiment_audit function."""

    def test_returns_record_id(self):
        """Should return audit record ID."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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

    def test_logs_to_standard_logger_fallback(self):
        """Should log to standard logger when no request/buffer available."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=6,
        ), patch(
            "selfhealing.services.audit.chaos_audit.logger"
        ) as mock_logger:
            from selfhealing.services.audit_helpers import log_chaos_experiment_audit
            
            log_chaos_experiment_audit(
                experiment_id="chaos-log123",
                event_type="experiment_started",
            )
            
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert "[ChaosAudit]" in call_args
            assert "chaos-log123" in call_args
            assert "experiment_started" in call_args

    def test_removes_none_values_from_details(self):
        """Should remove None values from details dict."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
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
