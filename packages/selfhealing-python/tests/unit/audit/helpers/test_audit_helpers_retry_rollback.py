"""
Tests for retry and rollback audit helpers.

Tests retry, system control, and rollback audit logging functionality.
"""

from unittest.mock import patch


class TestLogRetryAudit:
    """Tests for log_retry_audit function."""

    def test_logs_retry_attempted_to_standard_logger(self):
        """Should log RETRY status when attempt < max_attempts."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=1,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            result = log_retry_audit(
                domain="payment",
                attempt=1,
                max_attempts=3,
                success=False,
                error_type="ConnectionError",
                error_message="Connection refused",
                wait_time=4.0,
            )

            assert result == 1  # WAL sequence number
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "retry_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["status"] == "RETRY"
            assert call_kwargs["domain"] == "payment"
            assert call_kwargs["attempt"] == 1

    def test_logs_retry_exhausted_when_max_attempts_reached(self):
        """Should log EXHAUSTED status when attempt >= max_attempts."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=2,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            result = log_retry_audit(
                domain="payment",
                attempt=3,
                max_attempts=3,
                success=False,
                error_type="TimeoutError",
                error_message="Request timed out",
            )

            assert result == 2
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "retry_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["status"] == "EXHAUSTED"
            assert call_kwargs["attempt"] == 3

    def test_logs_retry_success(self):
        """Should log SUCCESS status when retry succeeds."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=3,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain="payment",
                attempt=2,
                max_attempts=3,
                success=True,
            )

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "retry_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["status"] == "SUCCESS"

    def test_wal_event_type_is_retry_exhausted(self):
        """Should use RETRY_EXHAUSTED event type when exhausted."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=4,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain="payment",
                attempt=3,
                max_attempts=3,
                success=False,
            )

            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "RETRY_EXHAUSTED"

    def test_wal_event_type_is_retry_attempted(self):
        """Should use RETRY_ATTEMPTED event type when not exhausted."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=5,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain="payment",
                attempt=1,
                max_attempts=3,
                success=False,
            )

            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "RETRY_ATTEMPTED"

    def test_includes_rate_limited_flag(self):
        """Should include rate_limited flag in details."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=6,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain="payment",
                attempt=1,
                max_attempts=3,
                success=False,
                rate_limited=True,
            )

            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["rate_limited"] is True

    def test_includes_context_in_details(self):
        """Should merge context into details."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=7,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain="payment",
                attempt=1,
                max_attempts=3,
                success=False,
                context={"order_id": "ORD-123", "payment_id": "PAY-456"},
            )

            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["order_id"] == "ORD-123"
            assert call_kwargs["details"]["payment_id"] == "PAY-456"


class TestLogSystemControlAudit:
    """Tests for log_system_control_audit function."""

    def test_logs_enable_action(self):
        """Should log ENABLE action."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=10,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_system_control_audit

            result = log_system_control_audit(
                action="enable",
                actor="admin",
                old_state={"enabled": False},
                new_state={"enabled": True},
                reason="Maintenance completed",
            )

            assert result == 10
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "system_control_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["action"] == "ENABLE"
            assert call_kwargs["actor"] == "admin"
            assert call_kwargs["value"] == "Maintenance completed"

    def test_logs_disable_action(self):
        """Should log DISABLE action."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=11,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_system_control_audit

            log_system_control_audit(
                action="disable",
                actor="system",
                reason="Emergency shutdown",
            )

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "system_control_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["action"] == "DISABLE"

    def test_wal_event_type_is_system_control_changed(self):
        """Should use SYSTEM_CONTROL_CHANGED event type."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=12,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_system_control_audit

            log_system_control_audit(
                action="enable_dry_run",
                actor="admin",
            )

            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "SYSTEM_CONTROL_CHANGED"
            assert call_kwargs["source"] == "SystemControl"

    def test_includes_state_changes_in_details(self):
        """Should include old and new state in details."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=13,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_system_control_audit

            log_system_control_audit(
                action="disable",
                actor="admin",
                old_state={"enabled": True, "dry_run": False},
                new_state={"enabled": False, "dry_run": False},
            )

            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["details"]["old_state"]["enabled"] is True
            assert call_kwargs["details"]["new_state"]["enabled"] is False


class TestLogRollbackAudit:
    """Tests for log_rollback_audit function."""

    def test_logs_pending_state(self):
        """Should log PENDING status when rollback is requested."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=20,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_rollback_audit

            result = log_rollback_audit(
                request_id="rb-001",
                stage_name="production",
                state="pending",
                triggered_by="system",
                reason="Error rate exceeded threshold",
            )

            assert result == 20
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "rollback_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["state"] == "PENDING"
            assert call_kwargs["request_id"] == "rb-001"
            assert call_kwargs["stage_name"] == "production"

    def test_logs_completed_state(self):
        """Should log COMPLETED status when rollback succeeds."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=21,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_rollback_audit

            log_rollback_audit(
                request_id="rb-002",
                stage_name="staging",
                state="completed",
                triggered_by="admin",
                affected_components=["api", "worker"],
                duration_seconds=45.5,
            )

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "rollback_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["state"] == "COMPLETED"
            assert call_kwargs["value"] == 45.5

    def test_logs_failed_state_with_errors(self):
        """Should log FAILED status with errors."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=22,
            ),
            patch("selfhealing.services.audit.retry_audit.logger") as mock_logger,
        ):
            from selfhealing.services.audit_helpers import log_rollback_audit

            log_rollback_audit(
                request_id="rb-003",
                stage_name="production",
                state="failed",
                triggered_by="system",
                errors=["Timeout exceeded", "Handler crashed"],
            )

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert call_args == "rollback_audit.event"
            call_kwargs = mock_logger.info.call_args[1]
            assert call_kwargs["state"] == "FAILED"

    def test_wal_event_type_is_rollback_performed(self):
        """Should use ROLLBACK_PERFORMED event type."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=23,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_rollback_audit

            log_rollback_audit(
                request_id="rb-004",
                stage_name="production",
                state="completed",
                triggered_by="system",
            )

            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["event_type"] == "ROLLBACK_PERFORMED"
            assert call_kwargs["source"] == "RollbackService"
            assert call_kwargs["target_id"] == "rb-004"

    def test_error_message_from_errors_list(self):
        """Should join errors into error_message."""
        with (
            patch(
                "selfhealing.services.audit.base._get_audit_adapter",
                return_value=None,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=24,
            ) as mock_wal,
        ):
            from selfhealing.services.audit_helpers import log_rollback_audit

            log_rollback_audit(
                request_id="rb-005",
                stage_name="production",
                state="failed",
                triggered_by="system",
                errors=["Error 1", "Error 2"],
            )

            call_kwargs = mock_wal.call_args[1]
            assert call_kwargs["error_message"] == "Error 1; Error 2"
            assert call_kwargs["success"] is False


class TestAuditEventTypeExtensions:
    """Tests for retry and rollback AuditEventType additions."""

    def test_retry_attempted_exists(self):
        """RETRY_ATTEMPTED should exist in AuditEventType."""
        from selfhealing.audit.event_buffer import AuditEventType

        assert hasattr(AuditEventType, "RETRY_ATTEMPTED")
        assert AuditEventType.RETRY_ATTEMPTED.value == "retry_attempted"

    def test_retry_exhausted_exists(self):
        """RETRY_EXHAUSTED should exist in AuditEventType."""
        from selfhealing.audit.event_buffer import AuditEventType

        assert hasattr(AuditEventType, "RETRY_EXHAUSTED")
        assert AuditEventType.RETRY_EXHAUSTED.value == "retry_exhausted"

    def test_system_control_changed_exists(self):
        """SYSTEM_CONTROL_CHANGED should exist in AuditEventType."""
        from selfhealing.audit.event_buffer import AuditEventType

        assert hasattr(AuditEventType, "SYSTEM_CONTROL_CHANGED")
        assert AuditEventType.SYSTEM_CONTROL_CHANGED.value == "system_control_changed"

    def test_rollback_performed_exists(self):
        """ROLLBACK_PERFORMED should exist in AuditEventType."""
        from selfhealing.audit.event_buffer import AuditEventType

        assert hasattr(AuditEventType, "ROLLBACK_PERFORMED")
        assert AuditEventType.ROLLBACK_PERFORMED.value == "rollback_performed"


class TestRetryHandlerAuditIntegration:
    """Tests for RetryHandler audit integration."""

    def test_retry_handler_calls_audit_on_success(self):
        """RetryHandler should call audit on successful execution."""
        with (
            patch(
                "selfhealing.services.retry_handler._is_system_enabled",
                return_value=True,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=100,
            ) as mock_wal,
        ):
            from selfhealing.services.retry_handler import RetryConfig, RetryHandler

            config = RetryConfig(max_attempts=3, domain="payment")
            handler = RetryHandler(config=config)

            def successful_func():
                return "success"

            result = handler.execute(successful_func)

            assert result.success is True
            # WAL should be called for audit
            assert mock_wal.called

    def test_retry_handler_calls_audit_on_failure(self):
        """RetryHandler should call audit on failed execution."""
        with (
            patch(
                "selfhealing.services.retry_handler._is_system_enabled",
                return_value=True,
            ),
            patch(
                "selfhealing.services.audit.retry_audit._write_to_wal",
                return_value=101,
            ) as mock_wal,
            patch(
                "selfhealing.services.retry_handler.RetryHandler._move_to_dlq",
                return_value=None,
            ),
        ):
            from selfhealing.services.retry_handler import RetryConfig, RetryHandler

            config = RetryConfig(max_attempts=2, domain="payment", enable_dlq=False)
            handler = RetryHandler(config=config)
            # AdaptiveRetryBudget이 소량 요청에서 재시도를 차단하지 않도록 패치
            handler._retry_budget.should_allow_retry = lambda: True

            call_count = 0

            def failing_func():
                nonlocal call_count
                call_count += 1
                raise ValueError("Always fails")

            result = handler.execute(failing_func)

            assert result.success is False
            assert call_count == 2  # Should retry once
            # WAL should be called multiple times for audit (once per attempt)
            assert mock_wal.call_count >= 2


class TestSystemControlAuditIntegration:
    """Tests for SystemControlManager audit integration."""

    def test_system_control_calls_audit_on_disable(self):
        """SystemControlManager should call audit when disabled."""
        with patch(
            "selfhealing.services.audit.retry_audit._write_to_wal",
            return_value=200,
        ) as mock_wal:
            from selfhealing.services.system_control import SystemControlManager

            # Reset singleton for test
            SystemControlManager._instance = None

            manager = SystemControlManager()
            manager.reset()  # Ensure enabled state
            manager.disable(actor="test_admin", reason="Test disable")

            # Should have called WAL for audit
            assert mock_wal.called
            # Find the disable call
            disable_calls = [c for c in mock_wal.call_args_list if c[1].get("details", {}).get("action") == "disable"]
            assert len(disable_calls) >= 1

    def test_system_control_calls_audit_on_enable(self):
        """SystemControlManager should call audit when enabled."""
        with patch(
            "selfhealing.services.audit.retry_audit._write_to_wal",
            return_value=201,
        ) as mock_wal:
            from selfhealing.services.system_control import SystemControlManager

            # Reset singleton for test
            SystemControlManager._instance = None

            manager = SystemControlManager()
            manager.disable(actor="system", reason="Setup")
            mock_wal.reset_mock()  # Clear previous calls

            manager.enable(actor="test_admin", reason="Test enable")

            # Should have called WAL for audit
            assert mock_wal.called


class TestRollbackServiceAuditIntegration:
    """Tests for RollbackService audit integration."""

    def test_rollback_service_calls_audit_on_request(self):
        """RollbackService should call audit when rollback is requested."""
        with patch(
            "selfhealing.services.audit.retry_audit._write_to_wal",
            return_value=300,
        ) as mock_wal:
            from selfhealing.services.rollback.service import RollbackService

            # Reset singleton for test
            RollbackService._instance = None

            service = RollbackService()
            service.clear()

            request = service.request_rollback(
                stage_name="production",
                reason="Error rate exceeded",
                triggered_by="system",
            )

            assert request is not None
            # Should have called WAL for audit (pending state)
            assert mock_wal.called

            pending_calls = [c for c in mock_wal.call_args_list if c[1].get("details", {}).get("state") == "pending"]
            assert len(pending_calls) >= 1

    def test_rollback_service_calls_audit_on_execution(self):
        """RollbackService should call audit when rollback is executed."""
        with patch(
            "selfhealing.services.audit.retry_audit._write_to_wal",
            return_value=301,
        ) as mock_wal:
            from selfhealing.services.rollback.service import (
                RollbackService,
                RollbackStrategy,
            )

            # Reset singleton for test
            RollbackService._instance = None

            service = RollbackService()
            service.clear()

            # Set policy to manual to prevent auto-execute
            service.set_policy(
                stage_name="test_stage",
                strategy=RollbackStrategy.MANUAL,
            )

            request = service.request_rollback(
                stage_name="test_stage",
                reason="Test rollback",
                triggered_by="test",
            )

            mock_wal.reset_mock()

            result = service.execute_rollback(request.request_id)

            # Should have called WAL for audit (completed/failed state)
            assert mock_wal.called
