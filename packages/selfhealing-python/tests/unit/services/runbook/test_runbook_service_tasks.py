"""
Runbook Service-layer Tasks Unit Tests.

tasks.py(278)의 단위 테스트:
- resume_runbook_task — 승인 후 파이프라인 재개 Celery 태스크
- scan_orphan_runbook_executions — 고아 런북 스캔 태스크
- _is_approved_but_not_resumed — 승인 완료 미재개 감지 헬퍼

Test Categories:
    A. Contract — 상수, 태스크 설정
    B. Behavior — 태스크 동작, 고아 스캔 로직
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from selfhealing.services.runbook.execution_models import (
    ApprovalDecisionType,
    RunbookExecutionContext,
    RunbookExecutionStatus,
)
from selfhealing.services.runbook.tasks import (
    APPROVED_STALE_THRESHOLD_SECONDS,
    ORPHAN_SCAN_STALE_THRESHOLD_SECONDS,
    _is_approved_but_not_resumed,
    resume_runbook_task,
    scan_orphan_runbook_executions,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_ctx_dict(
    execution_id: str = "runbook-test-001",
    status: str = "executing",
    started_at: str | None = None,
    namespace: str = "global",
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "runbook_id": "rb-001",
        "namespace": namespace,
        "trigger_event": {},
        "runbook_version": 1,
        "step_results": {},
        "variables": {},
        "current_step_index": 0,
        "status": status,
        "started_at": started_at,
        "completed_at": None,
        "abort_reason": None,
    }


# =============================================================================
# A. Contract Tests
# =============================================================================


class TestRunbookServiceTasksConstantsContract:
    """서비스 태스크 상수 계약 검증."""

    def test_orphan_scan_stale_threshold_is_600(self):
        """고아 판별 임계값 계약: 600초(10분)."""
        assert ORPHAN_SCAN_STALE_THRESHOLD_SECONDS == 600

    def test_approved_stale_threshold_is_120(self):
        """승인 완료 미재개 임계값 계약: 120초(2분)."""
        assert APPROVED_STALE_THRESHOLD_SECONDS == 120


class TestResumeRunbookTaskContract:
    """resume_runbook_task 태스크 설정 계약."""

    def test_task_name(self):
        """태스크 이름 계약값."""
        assert resume_runbook_task.name == "selfhealing.runbook.resume_pipeline"

    def test_max_retries(self):
        """최대 재시도 계약값: 3."""
        assert resume_runbook_task.max_retries == 3

    def test_default_retry_delay(self):
        """재시도 대기 계약값: 60초."""
        assert resume_runbook_task.default_retry_delay == 60

    def test_queue_name(self):
        """큐 이름 계약값."""
        assert resume_runbook_task.queue == "selfhealing_runbook"


class TestScanOrphanTaskContract:
    """scan_orphan_runbook_executions 태스크 설정 계약."""

    def test_task_name(self):
        """태스크 이름 계약값."""
        assert (
            scan_orphan_runbook_executions.name
            == "selfhealing.runbook.scan_orphan_executions"
        )

    def test_queue_name(self):
        """큐 이름 계약값."""
        assert scan_orphan_runbook_executions.queue == "selfhealing_recovery"


# =============================================================================
# B. Behavior Tests — resume_runbook_task
# =============================================================================


class TestResumeRunbookTaskBehavior:
    """resume_runbook_task 동작 검증."""

    @patch("selfhealing.services.runbook.service.RunbookService")
    def test_calls_resume_pipeline(self, MockService):
        """RunbookService.resume_pipeline()을 execution_id로 호출."""
        mock_service = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.status.value = "completed"
        mock_service.resume_pipeline.return_value = mock_ctx
        MockService.return_value = mock_service

        # When — Celery 태스크를 동기 호출
        result = resume_runbook_task.apply(args=["exec-001"]).get()

        # Then
        mock_service.resume_pipeline.assert_called_once_with("exec-001")
        assert result["status"] == "completed"
        assert result["execution_id"] == "exec-001"


# =============================================================================
# B. Behavior Tests — scan_orphan_runbook_executions
# =============================================================================


class TestScanOrphanExecutionsBehavior:
    """scan_orphan_runbook_executions 동작 검증."""

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_empty_backend_returns_zero_scanned(self, mock_get_backend):
        """백엔드에 항목 없으면 scanned=0."""
        mock_backend = MagicMock()
        mock_backend.get_all.return_value = {}
        mock_get_backend.return_value = mock_backend

        result = scan_orphan_runbook_executions.apply().get()

        assert result["scanned"] == 0
        assert result["resumed"] == 0
        assert result["skipped"] == 0

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_backend_error_returns_empty_results(self, mock_get_backend):
        """백엔드 오류 시 빈 결과 반환."""
        mock_backend = MagicMock()
        mock_backend.get_all.side_effect = RuntimeError("redis down")
        mock_get_backend.return_value = mock_backend

        result = scan_orphan_runbook_executions.apply().get()

        assert result["scanned"] == 0

    @patch(
        "selfhealing.services.runbook.tasks.resume_runbook_task", new_callable=MagicMock
    )
    @patch(
        "selfhealing.services.coordination.distributed_recovery_lock.get_distributed_recovery_lock"
    )
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_stale_executing_detected_as_orphan(
        self, mock_get_backend, mock_get_lock, mock_resume_task
    ):
        """EXECUTING + started_at > 10분 전 → 고아로 판별 + resume 디스패치."""
        stale_time = (
            datetime.now(timezone.utc)
            - timedelta(seconds=ORPHAN_SCAN_STALE_THRESHOLD_SECONDS + 60)
        ).isoformat()

        ctx_data = _make_ctx_dict(
            execution_id="orphan-001",
            status="executing",
            started_at=stale_time,
        )

        mock_backend = MagicMock()
        mock_backend.get_all.return_value = {"key1": ctx_data}
        mock_get_backend.return_value = mock_backend

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_get_lock.return_value = mock_lock

        # When
        result = scan_orphan_runbook_executions.apply().get()

        # Then
        assert result["scanned"] == 1
        assert result["resumed"] == 1
        mock_resume_task.delay.assert_called_once_with("orphan-001")
        mock_lock.release.assert_called_once()

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_fresh_executing_not_orphan(self, mock_get_backend):
        """EXECUTING + started_at 최근 → 고아가 아님."""
        fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()

        ctx_data = _make_ctx_dict(
            execution_id="fresh-001",
            status="executing",
            started_at=fresh_time,
        )

        mock_backend = MagicMock()
        mock_backend.get_all.return_value = {"key1": ctx_data}
        mock_get_backend.return_value = mock_backend

        result = scan_orphan_runbook_executions.apply().get()

        assert result["scanned"] == 1
        assert result["resumed"] == 0
        assert result["skipped"] == 1

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_invalid_context_data_skipped(self, mock_get_backend):
        """역직렬화 실패 데이터는 skipped 카운트."""
        mock_backend = MagicMock()
        mock_backend.get_all.return_value = {"key1": {"invalid": "data"}}
        mock_get_backend.return_value = mock_backend

        result = scan_orphan_runbook_executions.apply().get()

        assert result["scanned"] == 1
        assert result["skipped"] == 1

    @patch(
        "selfhealing.services.coordination.distributed_recovery_lock.get_distributed_recovery_lock"
    )
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_lock_not_acquired_skips_orphan(self, mock_get_backend, mock_get_lock):
        """락 획득 실패 시 해당 항목 스킵."""
        stale_time = (
            datetime.now(timezone.utc)
            - timedelta(seconds=ORPHAN_SCAN_STALE_THRESHOLD_SECONDS + 60)
        ).isoformat()

        ctx_data = _make_ctx_dict(
            execution_id="orphan-locked",
            status="executing",
            started_at=stale_time,
        )

        mock_backend = MagicMock()
        mock_backend.get_all.return_value = {"key1": ctx_data}
        mock_get_backend.return_value = mock_backend

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        mock_get_lock.return_value = mock_lock

        result = scan_orphan_runbook_executions.apply().get()

        assert result["resumed"] == 0
        assert result["skipped"] == 1


# =============================================================================
# B. Behavior Tests — _is_approved_but_not_resumed
# =============================================================================


class TestIsApprovedButNotResumedBehavior:
    """_is_approved_but_not_resumed 헬퍼 동작 검증."""

    def _make_ctx(
        self,
        execution_id: str = "exec-001",
        status: RunbookExecutionStatus = RunbookExecutionStatus.WAITING_APPROVAL,
    ) -> RunbookExecutionContext:
        return RunbookExecutionContext(
            execution_id=execution_id,
            runbook_id="rb-001",
            namespace="global",
            trigger_event={},
            status=status,
        )

    @patch("selfhealing.services.runbook.approval_gate.get_runbook_approval_gate")
    def test_no_approval_request_returns_false(self, mock_get_gate):
        """승인 요청이 없으면 False."""
        mock_gate = MagicMock()
        mock_gate._load_approval_request.return_value = None
        mock_get_gate.return_value = mock_gate

        ctx = self._make_ctx()
        now = datetime.now(timezone.utc)

        assert _is_approved_but_not_resumed(ctx, now) is False

    @patch("selfhealing.services.runbook.approval_gate.get_runbook_approval_gate")
    def test_waiting_status_returns_false(self, mock_get_gate):
        """승인 상태가 WAITING이면 False."""
        mock_request = MagicMock()
        mock_request.status = ApprovalDecisionType.WAITING
        mock_request.decided_at = None

        mock_gate = MagicMock()
        mock_gate._load_approval_request.return_value = mock_request
        mock_get_gate.return_value = mock_gate

        ctx = self._make_ctx()
        now = datetime.now(timezone.utc)

        assert _is_approved_but_not_resumed(ctx, now) is False

    @patch("selfhealing.services.runbook.approval_gate.get_runbook_approval_gate")
    def test_recently_approved_returns_false(self, mock_get_gate):
        """승인 완료 후 임계값 이내면 False."""
        now = datetime.now(timezone.utc)
        decided_at = (now - timedelta(seconds=30)).isoformat()

        mock_request = MagicMock()
        mock_request.status = ApprovalDecisionType.MANUALLY_APPROVED
        mock_request.decided_at = decided_at

        mock_gate = MagicMock()
        mock_gate._load_approval_request.return_value = mock_request
        mock_get_gate.return_value = mock_gate

        ctx = self._make_ctx()

        assert _is_approved_but_not_resumed(ctx, now) is False

    @patch("selfhealing.services.runbook.approval_gate.get_runbook_approval_gate")
    def test_stale_approved_returns_true(self, mock_get_gate):
        """승인 완료 후 임계값 초과하면 True."""
        now = datetime.now(timezone.utc)
        decided_at = (
            now - timedelta(seconds=APPROVED_STALE_THRESHOLD_SECONDS + 60)
        ).isoformat()

        mock_request = MagicMock()
        mock_request.status = ApprovalDecisionType.MANUALLY_APPROVED
        mock_request.decided_at = decided_at

        mock_gate = MagicMock()
        mock_gate._load_approval_request.return_value = mock_request
        mock_get_gate.return_value = mock_gate

        ctx = self._make_ctx()

        assert _is_approved_but_not_resumed(ctx, now) is True

    @patch("selfhealing.services.runbook.approval_gate.get_runbook_approval_gate")
    def test_timer_approved_also_detected(self, mock_get_gate):
        """TIMER_APPROVED 상태도 승인으로 판별."""
        now = datetime.now(timezone.utc)
        decided_at = (
            now - timedelta(seconds=APPROVED_STALE_THRESHOLD_SECONDS + 60)
        ).isoformat()

        mock_request = MagicMock()
        mock_request.status = ApprovalDecisionType.TIMER_APPROVED
        mock_request.decided_at = decided_at

        mock_gate = MagicMock()
        mock_gate._load_approval_request.return_value = mock_request
        mock_get_gate.return_value = mock_gate

        ctx = self._make_ctx()

        assert _is_approved_but_not_resumed(ctx, now) is True

    def test_import_error_returns_false(self):
        """approval_gate import 실패 시 False."""
        ctx = self._make_ctx()
        now = datetime.now(timezone.utc)

        with patch(
            "selfhealing.services.runbook.approval_gate.get_runbook_approval_gate",
            side_effect=ImportError,
        ):
            assert _is_approved_but_not_resumed(ctx, now) is False
