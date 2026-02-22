"""
Unit tests for Emergency Coordination Enums.

Tests:
- EmergencyScope enum values
- ActionType enum values
- CommandPrecedence ordering
- RecoveryStatus enum values
"""


from selfhealing.services.coordination.enums import (
    ActionType,
    CommandPrecedence,
    EmergencyScope,
    RecoveryStatus,
)


class TestEmergencyScope:
    """EmergencyScope enum 테스트."""

    def test_regional_value(self):
        """REGIONAL 값 확인."""
        assert EmergencyScope.REGIONAL.value == "regional"

    def test_global_value(self):
        """GLOBAL 값 확인."""
        assert EmergencyScope.GLOBAL.value == "global"

    def test_string_comparison(self):
        """문자열 비교 가능 확인."""
        assert EmergencyScope.REGIONAL == "regional"
        assert EmergencyScope.GLOBAL == "global"


class TestActionType:
    """ActionType enum 테스트."""

    def test_governance_actions(self):
        """Governance 관련 액션 확인."""
        assert ActionType.GOVERNANCE_STRICT.value == "governance_strict"
        assert ActionType.GOVERNANCE_NORMAL.value == "governance_normal"

    def test_canary_actions(self):
        """Canary 관련 액션 확인."""
        assert ActionType.CANARY_PAUSE.value == "canary_pause"
        assert ActionType.CANARY_ROLLBACK.value == "canary_rollback"
        assert ActionType.CANARY_RESUME.value == "canary_resume"

    def test_budget_actions(self):
        """Budget 관련 액션 확인."""
        assert ActionType.BUDGET_MULTIPLIER.value == "budget_multiplier"
        assert ActionType.BUDGET_RESET.value == "budget_reset"

    def test_notification_action(self):
        """Notification 액션 확인."""
        assert ActionType.NOTIFICATION.value == "notification"


class TestCommandPrecedence:
    """CommandPrecedence enum 테스트."""

    def test_ordering(self):
        """우선순위 순서 확인."""
        assert CommandPrecedence.SYSTEM_AUTO < CommandPrecedence.OPERATOR_COMMAND
        assert CommandPrecedence.OPERATOR_COMMAND < CommandPrecedence.ADMIN_OVERRIDE
        assert CommandPrecedence.ADMIN_OVERRIDE < CommandPrecedence.MAINTENANCE_MODE
        assert CommandPrecedence.MAINTENANCE_MODE < CommandPrecedence.KILL_SWITCH

    def test_kill_switch_highest(self):
        """KILL_SWITCH가 최우선."""
        assert CommandPrecedence.KILL_SWITCH == max(CommandPrecedence)

    def test_system_auto_lowest(self):
        """SYSTEM_AUTO가 최하위."""
        assert CommandPrecedence.SYSTEM_AUTO == min(CommandPrecedence)

    def test_int_values(self):
        """정수 값 확인."""
        assert int(CommandPrecedence.SYSTEM_AUTO) == 1
        assert int(CommandPrecedence.OPERATOR_COMMAND) == 2
        assert int(CommandPrecedence.ADMIN_OVERRIDE) == 3
        assert int(CommandPrecedence.MAINTENANCE_MODE) == 4
        assert int(CommandPrecedence.KILL_SWITCH) == 5


class TestRecoveryStatus:
    """RecoveryStatus enum 테스트."""

    def test_all_statuses_exist(self):
        """모든 상태가 정의됨."""
        statuses = [
            RecoveryStatus.NOT_STARTED,
            RecoveryStatus.IN_PROGRESS,
            RecoveryStatus.HEALTH_CHECK,
            RecoveryStatus.READY_TO_RESTORE,
            RecoveryStatus.COMPLETED,
            RecoveryStatus.FAILED,
            RecoveryStatus.ABORTED,
        ]
        assert len(statuses) == 7

    def test_ready_to_restore_value(self):
        """READY_TO_RESTORE 값 확인 (수동 승인 대기)."""
        assert RecoveryStatus.READY_TO_RESTORE.value == "ready_to_restore"
