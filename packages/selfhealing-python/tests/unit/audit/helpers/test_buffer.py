"""
Buffer Integration Tests.

Tests for request buffer integration with audit helpers.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

from unittest.mock import MagicMock, patch


class TestBufferIntegration:
    """Tests for request buffer integration."""

    def test_chaos_audit_adds_to_buffer_when_request_provided(self):
        """Should add to buffer when request is provided."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit.chaos_audit._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer:
            from selfhealing.services.audit import log_chaos_experiment_audit

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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit.chaos_audit._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer, patch(
            "selfhealing.audit.log_config_change",
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=1,
        ), patch(
            "selfhealing.services.audit.chaos_audit._try_add_to_buffer",
            return_value=True,
        ) as mock_buffer:
            from selfhealing.services.audit import (
                log_error_budget_blocked_audit,
            )

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
