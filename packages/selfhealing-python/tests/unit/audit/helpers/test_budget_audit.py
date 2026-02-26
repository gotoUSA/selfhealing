"""
Error Budget Gate Audit Helper Tests.

Tests for log_error_budget_blocked_audit function.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

from unittest.mock import patch


class TestLogErrorBudgetBlockedAudit:
    """Tests for log_error_budget_blocked_audit function."""

    def test_logs_blocked_action_to_wal(self):
        """Should write blocked events to WAL."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=2,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

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
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=3,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

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

    def test_logs_to_standard_logger_fallback(self):
        """Should log to standard logger when no request/buffer available."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=4,
            ),
            patch("selfhealing.services.audit.chaos_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action="chaos_experiment",
                gate_status="blocked",
                error_budget_percent=5.5,
                threshold_percent=10.0,
                reason="Budget low",
            )

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert call_args == "error_budget_audit.blocked"
            call_kwargs = mock_logger.warning.call_args[1]
            assert call_kwargs["blocked_action"] == "chaos_experiment"
            assert call_kwargs["budget_str"] == "5.5%"

    def test_handles_none_error_budget_percent(self):
        """Should handle None error_budget_percent gracefully."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=5,
            ),
            patch("selfhealing.services.audit.chaos_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action="test_action",
                gate_status="blocked",
                error_budget_percent=None,
                threshold_percent=10.0,
                reason="Budget unavailable",
            )

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert call_args == "error_budget_audit.blocked"
            call_kwargs = mock_logger.warning.call_args[1]
            assert call_kwargs["budget_str"] == "N/A"  # None displayed as N/A

    def test_includes_forensic_fields_when_provided(self):
        """Should include forensic fields (trace_id, actor_roles) when provided."""
        with patch(
            "selfhealing.services.audit.chaos_audit._write_to_wal",
            return_value=6,
        ) as mock_wal:
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action="auto_replay",
                gate_status="blocked",
                error_budget_percent=5.0,
                threshold_percent=10.0,
                reason="Budget low",
                blocked_request_trace_id="trace-abc123",
                actor_roles=["operator", "admin"],
            )

            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]

            # 포렌식 필드 확인
            assert details["blocked_request_trace_id"] == "trace-abc123"
            assert details["actor_roles_at_block"] == ["operator", "admin"]
            assert "blocked_at" in details  # timestamp 포함

            # WAL 최상위에도 전달되어야 함
            assert call_kwargs.get("trace_id") == "trace-abc123"
            assert call_kwargs.get("actor_roles") == ["operator", "admin"]

    def test_auto_extracts_trace_id_from_context(self):
        """Should auto-extract trace_id when not provided."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=7,
            ) as mock_wal,
            patch(
                "selfhealing.audit.trace.get_trace_id",
                return_value="auto-trace-456",
            ),
        ):
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action="auto_replay",
                gate_status="blocked",
                error_budget_percent=5.0,
                # blocked_request_trace_id 미지정 → 자동 추출
            )

            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]
            assert details.get("blocked_request_trace_id") == "auto-trace-456"

    def test_handles_missing_trace_context_gracefully(self):
        """Should handle missing trace context without error."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=8,
            ) as mock_wal,
            patch.dict(
                "sys.modules",
                {"selfhealing.audit.trace": None},
            ),
        ):
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            # ImportError 발생해도 실패하지 않음
            result = log_error_budget_blocked_audit(
                action="auto_replay",
                gate_status="blocked",
                error_budget_percent=5.0,
            )

            assert result == 8  # WAL 기록은 성공
            call_kwargs = mock_wal.call_args[1]
            details = call_kwargs["details"]
            # trace_id가 None이면 details에서 제외됨 (None 값은 필터링됨)
            assert "blocked_request_trace_id" not in details or details.get("blocked_request_trace_id") is None

    def test_forensic_fields_in_log_message(self):
        """Should include trace_id in log warning message."""
        with (
            patch(
                "selfhealing.services.audit.chaos_audit._write_to_wal",
                return_value=9,
            ),
            patch("selfhealing.services.audit.chaos_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action="test_action",
                gate_status="blocked",
                error_budget_percent=5.5,
                blocked_request_trace_id="trace-xyz789",
            )

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert call_args == "error_budget_audit.blocked"
            call_kwargs = mock_logger.warning.call_args[1]
            # trace_id 앞 8자리가 kwargs에 포함되어야 함
            assert call_kwargs["trace_str"] == "trace-xy"
