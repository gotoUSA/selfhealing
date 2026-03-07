"""
Emergency Mode Audit Helper Tests.

Tests for log_emergency_mode_audit function.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

from unittest.mock import patch


class TestLogEmergencyModeAudit:
    """Tests for log_emergency_mode_audit function."""

    def test_logs_activation_to_wal(self):
        """Should write activation events with EMERGENCY_MODE_ACTIVATED event type."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=1,
            ) as mock_wal,
            patch(
                "selfhealing.audit.log_config_change",
            ),
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=2,
            ) as mock_wal,
            patch(
                "selfhealing.audit.log_config_change",
            ),
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=3,
            ) as mock_wal,
            patch(
                "selfhealing.audit.log_config_change",
            ),
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=4,
            ) as mock_wal,
            patch(
                "selfhealing.audit.log_config_change",
            ),
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=5,
            ) as mock_wal,
            patch(
                "selfhealing.audit.log_config_change",
            ),
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

            log_emergency_mode_audit(
                action="escalate",
                level="LEVEL_3",
                is_active=True,
                activated_by="system",
                reason="Escalation",
            )

            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["tag"] == "EMERGENCY_ESCALATE"

    def test_logs_to_standard_logger_fallback(self):
        """Should log to standard logger when no request/buffer available."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=6,
            ),
            patch(
                "selfhealing.audit.log_config_change",
            ),
            patch("selfhealing.services.audit.chaos_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

            log_emergency_mode_audit(
                action="activate",
                level="LEVEL_1",
                is_active=True,
                activated_by="admin",
                reason="Test reason",
            )

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "emergency_mode_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["emergency_action"] == "ACTIVATE"
            assert call_kwargs["emergency_level"] == "LEVEL_1"

    def test_calls_log_config_change_for_compatibility(self):
        """Should also call log_config_change for backward compatibility."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=7,
            ),
            patch(
                "selfhealing.audit.log_config_change",
            ) as mock_config_change,
        ):
            from selfhealing.services.audit import log_emergency_mode_audit

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
