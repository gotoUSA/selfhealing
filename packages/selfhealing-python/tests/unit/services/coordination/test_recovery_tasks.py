"""
Unit tests for Recovery Tasks.

Tests:
- check_recovery_trigger_task
- execute_recovery_step_task
- monitor_recovery_health_task
- check_stale_pending_recoveries_task

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4
"""

from unittest.mock import MagicMock, patch

from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_tasks import (
    check_recovery_trigger_task,
    check_stale_pending_recoveries_task,
    cleanup_old_recovery_sessions_task,
    execute_recovery_step_task,
    get_recovery_beat_schedule,
    monitor_recovery_health_task,
)


class TestCheckRecoveryTriggerTask:
    """check_recovery_trigger_task 테스트."""

    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_not_in_emergency(self, mock_get_coordinator):
        """Emergency 상태가 아니면 트리거 안 함."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_current_status.return_value = RecoveryStatus.NORMAL
        mock_get_coordinator.return_value = mock_coordinator

        result = check_recovery_trigger_task(namespace="global")

        assert result["triggered"] is False
        assert "Not in EMERGENCY" in result["reason"]

    @patch("selfhealing.services.coordination.recovery_tasks.execute_recovery_step_task")
    @patch("selfhealing.services.coordination.recovery_tasks._fetch_current_error_rate")
    @patch("selfhealing.services.coordination.recovery_tasks._check_stability_duration")
    @patch("selfhealing.services.coordination.recovery_tasks.get_regional_recovery_policy_engine")
    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_trigger_recovery_auto(
        self,
        mock_get_coordinator,
        mock_get_policy,
        mock_check_stability,
        mock_fetch_rate,
        mock_execute_step,
    ):
        """자동 복구 트리거."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_current_status.return_value = RecoveryStatus.EMERGENCY
        mock_session = MagicMock()
        mock_session.session_id = "recovery-123"
        mock_coordinator.start_recovery.return_value = mock_session
        mock_get_coordinator.return_value = mock_coordinator

        mock_config = MagicMock()
        mock_config.require_manual_approval = False
        mock_config.recovery_error_threshold = 0.10
        mock_config.stability_check_minutes = 5
        mock_policy = MagicMock()
        mock_policy.get_config.return_value = mock_config
        mock_get_policy.return_value = mock_policy

        mock_fetch_rate.return_value = 0.05  # 5% error rate
        mock_check_stability.return_value = True

        result = check_recovery_trigger_task(namespace="global")

        assert result["triggered"] is True
        assert result["session_id"] == "recovery-123"
        # Verify execute_recovery_step_task.delay was called
        mock_execute_step.delay.assert_called_once()

    @patch("selfhealing.services.coordination.recovery_tasks._fetch_current_error_rate")
    @patch("selfhealing.services.coordination.recovery_tasks.get_regional_recovery_policy_engine")
    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_not_trigger_high_error_rate(
        self,
        mock_get_coordinator,
        mock_get_policy,
        mock_fetch_rate,
    ):
        """에러율 높으면 트리거 안 함."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_current_status.return_value = RecoveryStatus.EMERGENCY
        mock_get_coordinator.return_value = mock_coordinator

        mock_config = MagicMock()
        mock_config.recovery_error_threshold = 0.10
        mock_policy = MagicMock()
        mock_policy.get_config.return_value = mock_config
        mock_get_policy.return_value = mock_policy

        mock_fetch_rate.return_value = 0.15  # 15% error rate

        result = check_recovery_trigger_task(namespace="global")

        assert result["triggered"] is False
        assert "15.0%" in result["reason"]


class TestExecuteRecoveryStepTask:
    """execute_recovery_step_task 테스트."""

    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_session_not_found(self, mock_get_coordinator):
        """세션 없으면 에러."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_session.return_value = None
        mock_get_coordinator.return_value = mock_coordinator

        result = execute_recovery_step_task(session_id="nonexistent")

        assert "not found" in result.get("error", "").lower()

    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_circuit_breaker")
    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_already_completed(self, mock_get_coordinator, mock_get_cb):
        """이미 완료된 세션."""
        mock_session = MagicMock()
        mock_session.status = RecoveryStatus.COMPLETED
        mock_coordinator = MagicMock()
        mock_coordinator.get_session.return_value = mock_session
        mock_get_coordinator.return_value = mock_coordinator

        result = execute_recovery_step_task(session_id="completed-123")

        assert result["completed"] is True
        assert result["status"] == "completed"


class TestMonitorRecoveryHealthTask:
    """monitor_recovery_health_task 테스트."""

    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_not_recovering(self, mock_get_coordinator):
        """복구 중이 아니면 건강."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_current_status.return_value = RecoveryStatus.NORMAL
        mock_get_coordinator.return_value = mock_coordinator

        result = monitor_recovery_health_task(namespace="global")

        assert result["healthy"] is True
        assert result["tripped"] is False

    @patch("selfhealing.services.coordination.recovery_tasks._fetch_current_error_rate")
    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_circuit_breaker")
    @patch("selfhealing.services.coordination.recovery_tasks.get_recovery_coordinator")
    def test_circuit_breaker_trip(
        self,
        mock_get_coordinator,
        mock_get_cb,
        mock_fetch_rate,
    ):
        """회로 차단기 트립."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_current_status.return_value = RecoveryStatus.RECOVERING
        mock_get_coordinator.return_value = mock_coordinator

        mock_cb = MagicMock()
        mock_cb.check_and_trip.return_value = {
            "tripped": True,
            "state": "open",
            "should_re_escalate": True,
            "reason": "Error rate too high",
        }
        mock_get_cb.return_value = mock_cb

        mock_fetch_rate.return_value = 0.25

        result = monitor_recovery_health_task(namespace="global")

        assert result["healthy"] is False
        assert result["tripped"] is True
        assert result["should_re_escalate"] is True


class TestCheckStalePendingRecoveriesTask:
    """check_stale_pending_recoveries_task 테스트."""

    @patch("selfhealing.services.coordination.recovery_tasks.get_pending_recovery_approval_manager")
    def test_no_stale_requests(self, mock_get_manager):
        """방치된 요청 없음."""
        mock_manager = MagicMock()
        mock_manager.expire_old_requests.return_value = []
        mock_manager.check_and_send_reminders.return_value = []
        mock_manager.list_stale_requests.return_value = []
        mock_get_manager.return_value = mock_manager

        result = check_stale_pending_recoveries_task()

        assert result["stale_count"] == 0
        assert result["reminded_count"] == 0
        assert result["expired_count"] == 0

    @patch("selfhealing.services.coordination.recovery_tasks.get_pending_recovery_approval_manager")
    def test_stale_requests_found(self, mock_get_manager):
        """방치된 요청 발견."""
        mock_stale = MagicMock()
        mock_stale.request_id = "stale-123"
        mock_stale.namespace = "seoul"
        mock_stale.get_waiting_time_minutes.return_value = 45.0

        mock_manager = MagicMock()
        mock_manager.expire_old_requests.return_value = []
        mock_manager.check_and_send_reminders.return_value = []
        mock_manager.list_stale_requests.return_value = [mock_stale]
        mock_get_manager.return_value = mock_manager

        result = check_stale_pending_recoveries_task()

        assert result["stale_count"] == 1
        assert result["stale_requests"][0]["request_id"] == "stale-123"


class TestCleanupOldRecoverySessionsTask:
    """cleanup_old_recovery_sessions_task 테스트."""

    @patch("selfhealing.services.coordination.recovery_tasks.get_pending_recovery_approval_manager")
    def test_cleanup(self, mock_get_manager):
        """정리 실행."""
        mock_manager = MagicMock()
        mock_manager.cleanup_old_requests.return_value = 5
        mock_get_manager.return_value = mock_manager

        result = cleanup_old_recovery_sessions_task(max_age_hours=168)

        assert result["cleaned_count"] == 5


class TestGetRecoveryBeatSchedule:
    """Beat 스케줄 테스트."""

    def test_schedule_contains_tasks(self):
        """필요한 태스크가 스케줄에 포함됨."""
        schedule = get_recovery_beat_schedule()

        assert "check-recovery-trigger-every-minute" in schedule
        assert "monitor-recovery-health-every-30s" in schedule
        assert "check-stale-pending-every-10min" in schedule
        assert "cleanup-old-sessions-daily" in schedule

    def test_schedule_structure(self):
        """스케줄 구조 확인."""
        schedule = get_recovery_beat_schedule()

        trigger_task = schedule["check-recovery-trigger-every-minute"]

        assert "task" in trigger_task
        assert "schedule" in trigger_task
        assert trigger_task["task"] == "selfhealing.check_recovery_trigger"
