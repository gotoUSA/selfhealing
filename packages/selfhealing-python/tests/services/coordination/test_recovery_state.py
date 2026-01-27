"""
Unit tests for Recovery State Models.

Tests:
- RecoveryStepType enum values
- RecoveryStep dataclass (생성, 직렬화, 역직렬화)
- RecoverySession dataclass (생성, 단계 관리, 진행 상황)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
"""

import pytest
from datetime import datetime, timezone

from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_state import (
    RecoveryStepType,
    RecoveryStep,
    RecoverySession,
)


class TestRecoveryStepType:
    """RecoveryStepType enum 테스트."""

    def test_all_step_types_exist(self):
        """모든 복구 단계 유형이 정의됨."""
        assert RecoveryStepType.BUDGET_RESET == "budget_reset"
        assert RecoveryStepType.HEALTH_CHECK == "health_check"
        assert RecoveryStepType.CANARY_RESUME == "canary_resume"
        assert RecoveryStepType.GOVERNANCE_NORMAL == "governance_normal"

    def test_step_type_count(self):
        """4개의 복구 단계 유형."""
        assert len(RecoveryStepType) == 4

    def test_step_type_from_string(self):
        """문자열에서 enum 변환."""
        step_type = RecoveryStepType("budget_reset")
        assert step_type == RecoveryStepType.BUDGET_RESET

    def test_invalid_step_type_raises(self):
        """잘못된 단계 유형은 ValueError."""
        with pytest.raises(ValueError):
            RecoveryStepType("invalid_type")


class TestRecoveryStep:
    """RecoveryStep dataclass 테스트."""

    def test_create_step_with_defaults(self):
        """기본값으로 단계 생성."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
        )

        assert step.step_type == RecoveryStepType.BUDGET_RESET
        assert step.order == 1
        assert step.status == RecoveryStatus.NOT_STARTED
        assert step.wait_after_seconds == 0
        assert step.params == {}
        assert step.started_at is None
        assert step.completed_at is None
        assert step.error_message is None

    def test_create_step_with_params(self):
        """파라미터로 단계 생성."""
        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            wait_after_seconds=60,
            params={
                "duration_minutes": 5,
                "error_rate_threshold": 0.1,
            },
        )

        assert step.step_type == RecoveryStepType.HEALTH_CHECK
        assert step.wait_after_seconds == 60
        assert step.params["duration_minutes"] == 5
        assert step.params["error_rate_threshold"] == 0.1

    def test_step_to_dict(self):
        """단계를 딕셔너리로 변환."""
        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            status=RecoveryStatus.COMPLETED,
            wait_after_seconds=30,
            params={"resume_paused_only": True},
            started_at="2026-01-23T10:00:00+00:00",
            completed_at="2026-01-23T10:01:00+00:00",
        )

        result = step.to_dict()

        assert result["step_type"] == "canary_resume"
        assert result["order"] == 3
        assert result["status"] == "completed"
        assert result["wait_after_seconds"] == 30
        assert result["params"]["resume_paused_only"] is True
        assert result["started_at"] == "2026-01-23T10:00:00+00:00"
        assert result["completed_at"] == "2026-01-23T10:01:00+00:00"

    def test_step_from_dict(self):
        """딕셔너리에서 단계 생성."""
        data = {
            "step_type": "governance_normal",
            "order": 4,
            "status": "in_progress",
            "wait_after_seconds": 300,
            "params": {"reason": "AUTO-RECOVERY"},
            "started_at": "2026-01-23T10:00:00+00:00",
        }

        step = RecoveryStep.from_dict(data)

        assert step.step_type == RecoveryStepType.GOVERNANCE_NORMAL
        assert step.order == 4
        assert step.status == RecoveryStatus.IN_PROGRESS
        assert step.wait_after_seconds == 300
        assert step.params["reason"] == "AUTO-RECOVERY"
        assert step.started_at == "2026-01-23T10:00:00+00:00"

    def test_step_from_dict_defaults(self):
        """딕셔너리에서 기본값 적용."""
        data = {
            "step_type": "budget_reset",
            "order": 1,
        }

        step = RecoveryStep.from_dict(data)

        assert step.status == RecoveryStatus.NOT_STARTED
        assert step.wait_after_seconds == 0
        assert step.params == {}

    def test_step_with_error(self):
        """실패 상태의 단계."""
        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            status=RecoveryStatus.FAILED,
            error_message="Error rate 15% >= threshold 10%",
        )

        assert step.status == RecoveryStatus.FAILED
        assert "15%" in step.error_message


class TestRecoverySession:
    """RecoverySession dataclass 테스트."""

    @pytest.fixture
    def sample_steps(self):
        """테스트용 단계 목록."""
        return [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=60,
                params={"duration_minutes": 5},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.CANARY_RESUME,
                order=3,
                wait_after_seconds=30,
            ),
            RecoveryStep(
                step_type=RecoveryStepType.GOVERNANCE_NORMAL,
                order=4,
                wait_after_seconds=300,
            ),
        ]

    def test_create_session_with_defaults(self):
        """기본값으로 세션 생성."""
        session = RecoverySession(
            id="recovery-abc123",
            namespace="global",
            trigger_level="LEVEL_3",
        )

        assert session.id == "recovery-abc123"
        assert session.namespace == "global"
        assert session.trigger_level == "LEVEL_3"
        assert session.status == RecoveryStatus.NOT_STARTED
        assert session.steps == []
        assert session.current_step_index == 0
        assert session.started_at is None
        assert session.completed_at is None
        assert session.initiated_by == "system"
        assert session.abort_reason is None
        assert session.cascade_event_id is None

    def test_create_session_with_steps(self, sample_steps):
        """단계와 함께 세션 생성."""
        session = RecoverySession(
            id="recovery-def456",
            namespace="seoul",
            trigger_level="LEVEL_2",
            status=RecoveryStatus.IN_PROGRESS,
            steps=sample_steps,
            started_at="2026-01-23T10:00:00+00:00",
            initiated_by="admin@example.com",
        )

        assert len(session.steps) == 4
        assert session.status == RecoveryStatus.IN_PROGRESS
        assert session.initiated_by == "admin@example.com"

    def test_get_current_step(self, sample_steps):
        """현재 진행 중인 단계 조회."""
        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            steps=sample_steps,
            current_step_index=0,
        )

        current = session.get_current_step()
        assert current.step_type == RecoveryStepType.BUDGET_RESET

        # 다음 단계로 이동
        session.current_step_index = 1
        current = session.get_current_step()
        assert current.step_type == RecoveryStepType.HEALTH_CHECK

    def test_get_current_step_after_completion(self, sample_steps):
        """모든 단계 완료 후 현재 단계는 None."""
        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            steps=sample_steps,
            current_step_index=4,  # 모든 단계 완료
        )

        assert session.get_current_step() is None

    def test_is_complete_false(self, sample_steps):
        """일부 단계 미완료 시 is_complete는 False."""
        sample_steps[0].status = RecoveryStatus.COMPLETED
        sample_steps[1].status = RecoveryStatus.IN_PROGRESS

        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            steps=sample_steps,
        )

        assert session.is_complete() is False

    def test_is_complete_true(self, sample_steps):
        """모든 단계 완료 시 is_complete는 True."""
        for step in sample_steps:
            step.status = RecoveryStatus.COMPLETED

        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            steps=sample_steps,
        )

        assert session.is_complete() is True

    def test_get_progress(self, sample_steps):
        """진행 상황 조회."""
        sample_steps[0].status = RecoveryStatus.COMPLETED
        sample_steps[1].status = RecoveryStatus.COMPLETED

        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            steps=sample_steps,
            current_step_index=2,
        )

        progress = session.get_progress()

        assert progress["completed_steps"] == 2
        assert progress["total_steps"] == 4
        assert progress["progress_percent"] == 50.0
        assert progress["current_step"] == 2
        assert progress["current_step_type"] == "canary_resume"

    def test_session_to_dict(self, sample_steps):
        """세션을 딕셔너리로 변환."""
        session = RecoverySession(
            id="recovery-abc123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=sample_steps,
            current_step_index=1,
            started_at="2026-01-23T10:00:00+00:00",
            initiated_by="system",
            cascade_event_id="cascade-xyz789",
        )

        result = session.to_dict()

        assert result["id"] == "recovery-abc123"
        assert result["namespace"] == "global"
        assert result["trigger_level"] == "LEVEL_3"
        assert result["status"] == "in_progress"
        assert len(result["steps"]) == 4
        assert result["current_step_index"] == 1
        assert result["cascade_event_id"] == "cascade-xyz789"

    def test_session_from_dict(self):
        """딕셔너리에서 세션 생성."""
        data = {
            "id": "recovery-def456",
            "namespace": "seoul",
            "trigger_level": "LEVEL_2",
            "status": "completed",
            "steps": [
                {
                    "step_type": "budget_reset",
                    "order": 1,
                    "status": "completed",
                },
                {
                    "step_type": "health_check",
                    "order": 2,
                    "status": "completed",
                    "wait_after_seconds": 60,
                },
            ],
            "current_step_index": 2,
            "started_at": "2026-01-23T10:00:00+00:00",
            "completed_at": "2026-01-23T10:15:00+00:00",
            "initiated_by": "admin@example.com",
        }

        session = RecoverySession.from_dict(data)

        assert session.id == "recovery-def456"
        assert session.namespace == "seoul"
        assert session.status == RecoveryStatus.COMPLETED
        assert len(session.steps) == 2
        assert session.steps[0].step_type == RecoveryStepType.BUDGET_RESET
        assert session.steps[1].wait_after_seconds == 60

    def test_session_aborted(self):
        """중단된 세션."""
        session = RecoverySession(
            id="recovery-test",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.ABORTED,
            abort_reason="Re-failure detected: error_rate 20%",
            completed_at="2026-01-23T10:05:00+00:00",
        )

        assert session.status == RecoveryStatus.ABORTED
        assert "Re-failure" in session.abort_reason

    def test_empty_session_is_complete(self):
        """단계 없는 세션은 완료 상태."""
        session = RecoverySession(
            id="recovery-empty",
            namespace="global",
            trigger_level="LEVEL_1",
            steps=[],
        )

        # 빈 목록의 all()은 True
        assert session.is_complete() is True

    def test_empty_session_progress(self):
        """단계 없는 세션의 진행 상황."""
        session = RecoverySession(
            id="recovery-empty",
            namespace="global",
            trigger_level="LEVEL_1",
            steps=[],
        )

        progress = session.get_progress()

        assert progress["completed_steps"] == 0
        assert progress["total_steps"] == 0
        assert progress["progress_percent"] == 0
        assert progress["current_step_type"] is None


class TestRecoveryStatusEnum:
    """RecoveryStatus enum이 7개 상태를 갖는지 확인."""

    def test_all_recovery_statuses_exist(self):
        """모든 복구 상태가 정의됨."""
        assert RecoveryStatus.NOT_STARTED == "not_started"
        assert RecoveryStatus.IN_PROGRESS == "in_progress"
        assert RecoveryStatus.HEALTH_CHECK == "health_check"
        assert RecoveryStatus.READY_TO_RESTORE == "ready_to_restore"
        assert RecoveryStatus.COMPLETED == "completed"
        assert RecoveryStatus.FAILED == "failed"
        assert RecoveryStatus.ABORTED == "aborted"

    def test_recovery_status_count(self):
        """10개의 복구 상태 (7개 기본 + 3개 추가: NORMAL, EMERGENCY, RECOVERING)."""
        assert len(RecoveryStatus) == 10
