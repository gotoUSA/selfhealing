"""
Runbook Adapter-layer Celery Tasks Unit Tests.

adapters/celery/tasks/runbook.py(278)의 단위 테스트:
- execute_runbook_for_event — 이벤트 기반 비동기 실행
- execute_runbook_manual — 수동 비동기 실행 + 락 충돌 처리
- check_approval_timers — 타이머/타임아웃/리마인더 폴링

Test Categories:
    A. Contract — 태스크 설정
    B. Behavior — 태스크 동작, 에러 핸들링
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.adapters.celery.tasks.runbook import (
    check_approval_timers,
    execute_runbook_for_event,
    execute_runbook_manual,
)

# =============================================================================
# A. Contract Tests
# =============================================================================


class TestExecuteRunbookForEventTaskContract:
    """execute_runbook_for_event 태스크 설정 계약."""

    def test_task_name(self):
        """태스크 이름 계약값."""
        assert (
            execute_runbook_for_event.name
            == "selfhealing.tasks.runbook.execute_runbook_for_event"
        )

    def test_max_retries(self):
        """최대 재시도 계약값: 1."""
        assert execute_runbook_for_event.max_retries == 1

    def test_default_retry_delay(self):
        """재시도 대기 계약값: 30초."""
        assert execute_runbook_for_event.default_retry_delay == 30

    def test_acks_late_enabled(self):
        """acks_late 활성화."""
        assert execute_runbook_for_event.acks_late is True


class TestExecuteRunbookManualTaskContract:
    """execute_runbook_manual 태스크 설정 계약."""

    def test_task_name(self):
        """태스크 이름 계약값."""
        assert (
            execute_runbook_manual.name
            == "selfhealing.tasks.runbook.execute_runbook_manual"
        )

    def test_max_retries_zero(self):
        """수동 실행은 재시도 없음: 0."""
        assert execute_runbook_manual.max_retries == 0

    def test_acks_late_enabled(self):
        """acks_late 활성화."""
        assert execute_runbook_manual.acks_late is True


class TestCheckApprovalTimersTaskContract:
    """check_approval_timers 태스크 설정 계약."""

    def test_task_name(self):
        """태스크 이름 계약값."""
        assert (
            check_approval_timers.name
            == "selfhealing.tasks.runbook.check_approval_timers"
        )


# =============================================================================
# B. Behavior Tests — execute_runbook_for_event
# =============================================================================


class TestExecuteRunbookForEventBehavior:
    """execute_runbook_for_event 태스크 동작 검증."""

    @patch("selfhealing.services.runbook.service.RunbookService")
    @patch("selfhealing.services.event_bus.bus.SelfHealingEvent")
    def test_no_match_returns_no_match_status(self, MockEvent, MockService):
        """패턴 미매칭 시 status='no_match' 반환."""
        mock_service = MagicMock()
        mock_service.handle_event.return_value = None
        MockService.return_value = mock_service
        MockEvent.from_dict.return_value = MagicMock()

        event_data = {"event_type": "circuit_breaker_opened"}

        # When
        result = execute_runbook_for_event.apply(args=[event_data]).get()

        # Then
        assert result["status"] == "no_match"
        assert result["event_type"] == "circuit_breaker_opened"

    @patch("selfhealing.services.runbook.service.RunbookService")
    @patch("selfhealing.services.event_bus.bus.SelfHealingEvent")
    def test_match_returns_execution_info(self, MockEvent, MockService):
        """매칭 성공 시 status, execution_id, runbook_id 반환."""
        mock_ctx = MagicMock()
        mock_ctx.status.value = "completed"
        mock_ctx.execution_id = "exec-001"
        mock_ctx.runbook_id = "rb-001"

        mock_service = MagicMock()
        mock_service.handle_event.return_value = mock_ctx
        MockService.return_value = mock_service
        MockEvent.from_dict.return_value = MagicMock()

        result = execute_runbook_for_event.apply(args=[{}]).get()

        assert result["status"] == "completed"
        assert result["execution_id"] == "exec-001"
        assert result["runbook_id"] == "rb-001"


# =============================================================================
# B. Behavior Tests — execute_runbook_manual
# =============================================================================


class TestExecuteRunbookManualBehavior:
    """execute_runbook_manual 태스크 동작 검증."""

    @patch("selfhealing.services.runbook.service.RunbookService")
    def test_successful_execution_returns_status(self, MockService):
        """정상 실행 시 status, execution_id 반환."""
        mock_ctx = MagicMock()
        mock_ctx.status.value = "completed"
        mock_ctx.execution_id = "exec-002"

        mock_service = MagicMock()
        mock_service.execute_runbook.return_value = mock_ctx
        MockService.return_value = mock_service

        result = execute_runbook_manual.apply(
            args=["rb-001"],
            kwargs={"namespace": "payment"},
        ).get()

        assert result["status"] == "completed"
        assert result["execution_id"] == "exec-002"
        mock_service.execute_runbook.assert_called_once_with("rb-001", "payment", None)

    @patch("selfhealing.services.runbook.service.RunbookService")
    def test_lock_conflict_returns_lock_info(self, MockService):
        """RunbookLockConflictError 시 lock_conflict 상태 + 소유자 정보."""
        from selfhealing.services.runbook.exceptions import RunbookLockConflictError

        mock_service = MagicMock()
        mock_service.execute_runbook.side_effect = RunbookLockConflictError("locked")
        MockService.return_value = mock_service

        with patch(
            "selfhealing.services.coordination.distributed_recovery_lock.get_distributed_recovery_lock"
        ) as mock_get_lock:
            mock_lock = MagicMock()
            mock_lock.get_lock_owner.return_value = "worker-1"
            mock_get_lock.return_value = mock_lock

            result = execute_runbook_manual.apply(
                args=["rb-001"],
                kwargs={"namespace": "payment"},
            ).get()

        assert result["status"] == "lock_conflict"
        assert result["current_owner"] == "worker-1"
        assert result["namespace"] == "payment"

    @patch("selfhealing.services.runbook.service.RunbookService")
    def test_general_error_returns_error_status(self, MockService):
        """일반 예외 시 status='error' 반환."""
        mock_service = MagicMock()
        mock_service.execute_runbook.side_effect = ValueError("bad input")
        MockService.return_value = mock_service

        result = execute_runbook_manual.apply(args=["rb-001"]).get()

        assert result["status"] == "error"
        assert "bad input" in result["error"]


# =============================================================================
# B. Behavior Tests — check_approval_timers
# =============================================================================


class TestCheckApprovalTimersBehavior:
    """check_approval_timers 태스크 동작 검증."""

    @patch("selfhealing.services.runbook.approval_gate.RunbookApprovalGate")
    def test_returns_aggregated_results(self, MockGate):
        """타이머/타임아웃/리마인더 결과를 집계하여 반환."""
        mock_gate = MagicMock()
        mock_gate.check_timer_approval.return_value = MagicMock()
        mock_gate.check_approval_timeouts.return_value = ["t1", "t2"]
        mock_gate.check_and_send_reminders.return_value = ["r1"]
        MockGate.return_value = mock_gate

        result = check_approval_timers.apply().get()

        assert result["timer_approved"] == 1
        assert result["timed_out"] == 2
        assert result["reminders_sent"] == 1

    @patch("selfhealing.services.runbook.approval_gate.RunbookApprovalGate")
    def test_timer_check_none_result(self, MockGate):
        """타이머 체크 결과 None이면 카운트 0."""
        mock_gate = MagicMock()
        mock_gate.check_timer_approval.return_value = None
        mock_gate.check_approval_timeouts.return_value = []
        mock_gate.check_and_send_reminders.return_value = []
        MockGate.return_value = mock_gate

        result = check_approval_timers.apply().get()

        assert result["timer_approved"] == 0
        assert result["timed_out"] == 0
        assert result["reminders_sent"] == 0

    @patch("selfhealing.services.runbook.approval_gate.RunbookApprovalGate")
    def test_individual_errors_handled_gracefully(self, MockGate):
        """개별 체크 실패 시 다른 체크는 계속 진행."""
        mock_gate = MagicMock()
        mock_gate.check_timer_approval.side_effect = RuntimeError("timer err")
        mock_gate.check_approval_timeouts.return_value = ["t1"]
        mock_gate.check_and_send_reminders.side_effect = RuntimeError("reminder err")
        MockGate.return_value = mock_gate

        result = check_approval_timers.apply().get()

        assert result["timer_approved"] == 0
        assert result["timed_out"] == 1
        assert result["reminders_sent"] == 0
