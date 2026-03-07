"""
Tests for Governance Checks Audit Logging.

차단 발생 시 AuditLogRepository에 기록이 남는지 검증합니다.
"왜 이때 작업이 안 됐지?" 질문에 답할 수 있도록 합니다.

Reference:
- packages/selfhealing-python/src/selfhealing/services/governance_checks.py
- packages/selfhealing-python/src/selfhealing/audit/audit_adapter.py
"""

from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_audit_adapter():
    """Mock AuditLogAdapter for testing."""
    adapter = MagicMock()
    adapter.log_governance_blocked = MagicMock()
    return adapter


@pytest.fixture
def mock_system_disabled():
    """Mock system disabled (Kill Switch active)."""
    with patch(
        "selfhealing.services.governance.checks.is_system_enabled",
        return_value=False,
    ):
        yield


@pytest.fixture
def mock_emergency_mode():
    """Mock emergency mode active."""
    with patch(
        "selfhealing.services.governance.checks.is_emergency_blocking",
        return_value=(True, "CRITICAL"),
    ):
        yield


@pytest.fixture
def mock_low_error_budget():
    """Mock low error budget."""
    with patch(
        "selfhealing.services.governance.checks.is_error_budget_blocking",
        return_value=(True, 5.0, 10.0),  # 5% budget, 10% threshold
    ):
        yield


# =============================================================================
# check_all_governance Audit Logging Tests
# =============================================================================


class TestCheckAllGovernanceAuditLogging:
    """check_all_governance() 함수의 Audit Logging 테스트."""

    def test_audit_log_on_kill_switch_block(self, mock_audit_adapter, mock_system_disabled):
        """Kill Switch 차단 시 Audit Log 기록 확인."""
        from selfhealing.services.governance.checks import (
            BlockReason,
            check_all_governance,
        )

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="test_chaos_injection",
                service_name="ChaosService",
                domain="chaos",
                audit_on_block=True,
            )

            # 차단되었는지 확인
            assert not result.allowed
            assert result.block_reason == BlockReason.KILL_SWITCH

            # Audit Log 호출 확인
            mock_audit_adapter.log_governance_blocked.assert_called_once()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]

            assert call_kwargs["block_reason"] == "kill_switch"
            assert call_kwargs["operation_name"] == "test_chaos_injection"
            assert call_kwargs["service_name"] == "ChaosService"

    def test_audit_log_on_emergency_block(self, mock_audit_adapter):
        """Emergency Mode 차단 시 Audit Log 기록 확인."""
        from selfhealing.services.governance.checks import (
            BlockReason,
            check_all_governance,
        )

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ), patch(
            "selfhealing.services.governance.checks.is_emergency_blocking",
            return_value=(True, "CRITICAL"),
        ), patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="test_config_apply",
                service_name="ConfigService",
                domain="config",
                audit_on_block=True,
            )

            assert not result.allowed
            assert result.block_reason == BlockReason.EMERGENCY_MODE

            mock_audit_adapter.log_governance_blocked.assert_called_once()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]

            assert call_kwargs["block_reason"] == "emergency_mode"
            assert call_kwargs["details"]["emergency_level"] == "CRITICAL"

    def test_audit_log_on_error_budget_block(self, mock_audit_adapter):
        """Error Budget 차단 시 Audit Log 기록 확인."""
        from selfhealing.services.governance.checks import (
            BlockReason,
            check_all_governance,
        )

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ), patch(
            "selfhealing.services.governance.checks.is_emergency_blocking",
            return_value=(False, None),
        ), patch(
            "selfhealing.services.governance.checks.is_error_budget_blocking",
            return_value=(True, 5.0, 10.0),
        ), patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="test_dlq_replay",
                service_name="ReplayService",
                domain="replay",
                audit_on_block=True,
            )

            assert not result.allowed
            assert result.block_reason == BlockReason.ERROR_BUDGET

            mock_audit_adapter.log_governance_blocked.assert_called_once()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]

            assert call_kwargs["block_reason"] == "error_budget"
            assert call_kwargs["details"]["error_budget_percent"] == 5.0
            assert call_kwargs["details"]["threshold_percent"] == 10.0

    def test_no_audit_log_when_allowed(self, mock_audit_adapter):
        """허용된 경우 Audit Log 미기록 확인."""
        from selfhealing.services.governance.checks import check_all_governance

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ), patch(
            "selfhealing.services.governance.checks.is_emergency_blocking",
            return_value=(False, None),
        ), patch(
            "selfhealing.services.governance.checks.is_error_budget_blocking",
            return_value=(False, 50.0, 10.0),
        ), patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="test_operation",
                audit_on_block=True,
            )

            assert result.allowed
            mock_audit_adapter.log_governance_blocked.assert_not_called()

    def test_no_audit_when_disabled(self, mock_audit_adapter, mock_system_disabled):
        """audit_on_block=False일 때 Audit Log 미기록 확인."""
        from selfhealing.services.governance.checks import check_all_governance

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="test_operation",
                audit_on_block=False,
            )

            assert not result.allowed
            mock_audit_adapter.log_governance_blocked.assert_not_called()


# =============================================================================
# Decorator Audit Logging Tests
# =============================================================================


class TestDecoratorAuditLogging:
    """데코레이터의 Audit Logging 테스트."""

    def test_require_system_enabled_logs_on_block(self, mock_audit_adapter, mock_system_disabled):
        """@require_system_enabled 차단 시 Audit Log 기록."""
        from selfhealing.services.governance.checks import require_system_enabled

        with patch(
            "selfhealing.services.governance.checks._log_governance_blocked"
        ) as mock_log:
            @require_system_enabled
            def my_operation():
                return "success"

            result = my_operation()

            assert not result.allowed
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            assert call_kwargs["operation_name"] == "my_operation"

    def test_require_not_emergency_logs_on_block(self, mock_audit_adapter, mock_emergency_mode):
        """@require_not_emergency 차단 시 Audit Log 기록."""
        from selfhealing.services.governance.checks import require_not_emergency

        with patch(
            "selfhealing.services.governance.checks._log_governance_blocked"
        ) as mock_log:
            @require_not_emergency(min_level=2)
            def my_critical_task():
                return "success"

            result = my_critical_task()

            assert not result.allowed
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            assert call_kwargs["operation_name"] == "my_critical_task"
            assert "emergency_level" in call_kwargs["details"]

    def test_require_error_budget_logs_on_block(self, mock_audit_adapter, mock_low_error_budget):
        """@require_error_budget 차단 시 Audit Log 기록."""
        from selfhealing.services.governance.checks import require_error_budget

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ), patch(
            "selfhealing.services.governance.checks.is_emergency_blocking",
            return_value=(False, None),
        ), patch(
            "selfhealing.services.governance.checks._log_governance_blocked"
        ) as mock_log:
            @require_error_budget()
            def my_risky_operation():
                return "success"

            result = my_risky_operation()

            assert not result.allowed
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            assert call_kwargs["operation_name"] == "my_risky_operation"
            assert "budget_percent" in call_kwargs["details"]

    def test_require_governance_logs_on_block(self, mock_audit_adapter, mock_system_disabled):
        """@require_governance 차단 시 Audit Log 기록 (check_all_governance 경유)."""
        from selfhealing.services.governance.checks import require_governance

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            @require_governance(operation_name="custom_op_name", audit_on_block=True)
            def my_automation():
                return "success"

            result = my_automation()

            assert not result.allowed
            mock_audit_adapter.log_governance_blocked.assert_called_once()


# =============================================================================
# Mixin Audit Logging Tests
# =============================================================================


class TestMixinAuditLogging:
    """GovernanceCheckMixin의 Audit Logging 테스트."""

    def test_mixin_check_governance_logs_on_block(self, mock_audit_adapter, mock_system_disabled):
        """Mixin.check_governance() 차단 시 Audit Log 기록."""
        from selfhealing.services.governance.checks import GovernanceCheckMixin

        class MyService(GovernanceCheckMixin):
            _governance_service_name = "MyService"
            _governance_domain = "testing"

        service = MyService()

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = service.check_governance(
                operation_name="do_risky_thing",
                audit_on_block=True,
            )

            assert not result.allowed
            mock_audit_adapter.log_governance_blocked.assert_called_once()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]

            assert call_kwargs["operation_name"] == "do_risky_thing"
            assert call_kwargs["service_name"] == "MyService"

    def test_mixin_is_automation_allowed_logs_on_block(
        self, mock_audit_adapter, mock_system_disabled
    ):
        """Mixin.is_automation_allowed() 차단 시 Audit Log 기록."""
        from selfhealing.services.governance.checks import GovernanceCheckMixin

        class AutomationService(GovernanceCheckMixin):
            _governance_service_name = "AutomationService"

        service = AutomationService()

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            allowed = service.is_automation_allowed(operation_name="auto_remediation")

            assert not allowed
            mock_audit_adapter.log_governance_blocked.assert_called_once()


# =============================================================================
# Fallback Logger Tests
# =============================================================================


class TestFallbackLogging:
    """AuditLogAdapter 없을 때 fallback 로깅 테스트."""

    def test_logs_to_standard_logger_when_adapter_unavailable(self, mock_system_disabled):
        """AuditLogAdapter가 없을 때 표준 로거로 기록."""
        from selfhealing.services.governance.checks import check_all_governance

        with patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=None,
        ), patch(
            "selfhealing.services.governance.checks.logger"
        ) as mock_logger:
            result = check_all_governance(
                operation_name="test_fallback",
                audit_on_block=True,
            )

            assert not result.allowed
            # 표준 로거가 호출되었는지 확인 (warning 레벨)
            assert mock_logger.warning.called


# =============================================================================
# AuditAction Enum Tests
# =============================================================================


class TestAuditActionGovernanceTypes:
    """AuditAction enum에 거버넌스 관련 타입 존재 확인."""

    def test_governance_blocked_action_exists(self):
        """GOVERNANCE_BLOCKED action 존재 확인."""
        from selfhealing.interfaces.audit_adapter import AuditAction

        assert hasattr(AuditAction, "GOVERNANCE_BLOCKED")
        assert AuditAction.GOVERNANCE_BLOCKED.value == "governance_blocked"

    def test_governance_kill_switch_action_exists(self):
        """GOVERNANCE_KILL_SWITCH action 존재 확인."""
        from selfhealing.interfaces.audit_adapter import AuditAction

        assert hasattr(AuditAction, "GOVERNANCE_KILL_SWITCH")
        assert AuditAction.GOVERNANCE_KILL_SWITCH.value == "governance_kill_switch"

    def test_governance_emergency_action_exists(self):
        """GOVERNANCE_EMERGENCY action 존재 확인."""
        from selfhealing.interfaces.audit_adapter import AuditAction

        assert hasattr(AuditAction, "GOVERNANCE_EMERGENCY")
        assert AuditAction.GOVERNANCE_EMERGENCY.value == "governance_emergency"

    def test_governance_error_budget_action_exists(self):
        """GOVERNANCE_ERROR_BUDGET action 존재 확인."""
        from selfhealing.interfaces.audit_adapter import AuditAction

        assert hasattr(AuditAction, "GOVERNANCE_ERROR_BUDGET")
        assert AuditAction.GOVERNANCE_ERROR_BUDGET.value == "governance_error_budget"


# =============================================================================
# Integration-like Tests
# =============================================================================


class TestGovernanceAuditIntegration:
    """실제 사용 시나리오 테스트."""

    def test_replay_service_audit_logging_scenario(self, mock_audit_adapter):
        """DLQ Replay가 차단될 때 Audit 기록 시나리오."""
        from selfhealing.services.governance.checks import GovernanceCheckMixin

        class ReplayService(GovernanceCheckMixin):
            _governance_service_name = "ReplayService"
            _governance_domain = "replay"

            def replay_dlq_messages(self, message_ids: list) -> dict:
                blocked = self.require_automation_allowed(
                    operation_name="replay_dlq_messages",
                )
                if blocked:
                    return {
                        "success": False,
                        "error": blocked.block_message,
                        "reason": blocked.block_reason.value,
                    }
                # ... actual replay logic
                return {"success": True, "replayed": len(message_ids)}

        service = ReplayService()

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=False,  # Kill Switch active
        ), patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = service.replay_dlq_messages(["msg1", "msg2"])

            assert not result["success"]
            assert result["reason"] == "kill_switch"

            # Audit Log가 기록되었는지 확인
            mock_audit_adapter.log_governance_blocked.assert_called()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]
            assert call_kwargs["operation_name"] == "replay_dlq_messages"

    def test_chaos_service_emergency_block_audit(self, mock_audit_adapter):
        """Chaos 주입이 Emergency Mode로 차단될 때 Audit 기록."""
        from selfhealing.services.governance.checks import (
            BlockReason,
            check_all_governance,
        )

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ), patch(
            "selfhealing.services.governance.checks.is_emergency_blocking",
            return_value=(True, "HIGH"),  # Emergency mode HIGH
        ), patch(
            "selfhealing.services.governance.checks._get_audit_adapter",
            return_value=mock_audit_adapter,
        ):
            result = check_all_governance(
                operation_name="inject_chaos_experiment",
                service_name="ChaosExecutionService",
                domain="chaos",
                audit_on_block=True,
            )

            assert result.block_reason == BlockReason.EMERGENCY_MODE

            mock_audit_adapter.log_governance_blocked.assert_called_once()
            call_kwargs = mock_audit_adapter.log_governance_blocked.call_args[1]

            # "왜 이때 작업이 안 됐지?"에 답할 수 있는 정보가 있는지 확인
            assert call_kwargs["operation_name"] == "inject_chaos_experiment"
            assert call_kwargs["block_reason"] == "emergency_mode"
            assert call_kwargs["details"]["emergency_level"] == "HIGH"
            assert call_kwargs["service_name"] == "ChaosExecutionService"
