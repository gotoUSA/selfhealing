"""
Unit tests for Recovery Coordinator.

Tests:
- start_recovery() 기본 동작
- execute_next_step() 단계 실행
- abort_recovery() 복구 중단
- 전체 복구 플로우 (4단계)
- 중복 복구 방지
- 실패 처리

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_coordinator import (
    RecoveryCoordinator,
    get_recovery_coordinator,
    reset_recovery_coordinator,
)
from selfhealing.services.coordination.recovery_state import (
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)


class TestRecoveryCoordinatorInit:
    """RecoveryCoordinator 초기화 테스트."""

    def test_init_with_defaults(self):
        """기본값으로 초기화."""
        backend = MemoryStateBackend()
        coordinator = RecoveryCoordinator(backend=backend)

        assert coordinator is not None
        assert len(coordinator._step_handlers) == 4

    def test_init_with_custom_lock(self):
        """커스텀 락으로 초기화."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()

        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
        )

        assert coordinator._recovery_lock is lock

    def test_default_recovery_steps_exist(self):
        """기본 복구 단계가 정의됨."""
        assert "LEVEL_3" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS
        assert "LEVEL_2" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS
        assert "LEVEL_1" in RecoveryCoordinator.DEFAULT_RECOVERY_STEPS

        # LEVEL_3는 4단계
        level3_steps = RecoveryCoordinator.DEFAULT_RECOVERY_STEPS["LEVEL_3"]
        assert len(level3_steps) == 4

    def test_register_custom_handler(self):
        """커스텀 핸들러 등록."""
        backend = MemoryStateBackend()
        coordinator = RecoveryCoordinator(backend=backend)

        custom_handler = MagicMock(return_value={"success": True})
        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            custom_handler,
        )

        assert coordinator._step_handlers[RecoveryStepType.BUDGET_RESET] is custom_handler


class TestStartRecovery:
    """시작_recovery() 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

    def test_start_recovery_success(self, coordinator):
        """복구 시작 성공."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="system",
        )

        assert session.id.startswith("recovery-")
        assert session.namespace == "global"
        assert session.trigger_level == "LEVEL_3"
        assert session.status == RecoveryStatus.IN_PROGRESS
        assert session.initiated_by == "system"
        assert len(session.steps) == 4
        assert session.started_at is not None

    def test_start_recovery_with_user(self, coordinator):
        """사용자가 시작한 복구."""
        session = coordinator.start_recovery(
            namespace="seoul",
            trigger_level="LEVEL_2",
            initiated_by="admin@example.com",
        )

        assert session.initiated_by == "admin@example.com"

    def test_start_recovery_already_in_progress(self, coordinator):
        """이미 진행 중인 복구가 있으면 에러."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        with pytest.raises(ValueError) as exc_info:
            coordinator.start_recovery(
                namespace="global",
                trigger_level="LEVEL_3",
            )

        assert "already in progress" in str(exc_info.value)

    def test_start_recovery_different_namespace(self, coordinator):
        """다른 네임스페이스는 동시 복구 가능."""
        session1 = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        session2 = coordinator.start_recovery(
            namespace="seoul",
            trigger_level="LEVEL_2",
        )

        assert session1.namespace == "global"
        assert session2.namespace == "seoul"

    def test_start_recovery_unknown_level(self, coordinator):
        """알 수 없는 레벨은 에러."""
        with pytest.raises(ValueError) as exc_info:
            coordinator.start_recovery(
                namespace="global",
                trigger_level="LEVEL_99",
            )

        assert "No recovery steps" in str(exc_info.value)

    def test_start_recovery_session_saved(self, coordinator):
        """세션이 저장됨."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 저장된 세션 조회
        saved = coordinator.get_session("global", session.id)
        assert saved is not None
        assert saved.id == session.id

    def test_start_recovery_active_session_set(self, coordinator):
        """활성 세션이 설정됨."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        active = coordinator.get_active_session("global")
        assert active is not None
        assert active.id == session.id


class TestExecuteNextStep:
    """execute_next_step() 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터 (핸들러 모킹)."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(backend=backend, recovery_lock=lock)

        # 모든 핸들러를 성공으로 모킹
        for step_type in RecoveryStepType:
            coord.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        return coord

    def test_execute_first_step(self, coordinator):
        """첫 번째 단계 실행."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        step = coordinator.execute_next_step("global")

        assert step is not None
        assert step.step_type == RecoveryStepType.BUDGET_RESET
        assert step.status == RecoveryStatus.COMPLETED

    def test_execute_all_steps(self, coordinator):
        """모든 단계 순차 실행."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        executed_steps = []
        for _ in range(10):  # 최대 10회
            step = coordinator.execute_next_step("global")
            if step is None:
                break
            executed_steps.append(step)

        # LEVEL_3는 4단계
        assert len(executed_steps) == 4
        assert executed_steps[0].step_type == RecoveryStepType.BUDGET_RESET
        assert executed_steps[1].step_type == RecoveryStepType.HEALTH_CHECK
        assert executed_steps[2].step_type == RecoveryStepType.CANARY_RESUME
        assert executed_steps[3].step_type == RecoveryStepType.GOVERNANCE_NORMAL

    def test_execute_step_completes_session(self, coordinator):
        """모든 단계 완료 시 세션 상태가 COMPLETED."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 4단계 실행
        for _ in range(4):
            coordinator.execute_next_step("global")

        # 5번째 호출에서 완료 처리 (모든 단계 완료 확인 후)
        final_step = coordinator.execute_next_step("global")
        assert final_step is None

        # 활성 세션은 없어야 함
        active = coordinator.get_active_session("global")
        assert active is None

        # 저장된 세션은 COMPLETED 상태
        saved = coordinator.get_session("global", session.id)
        assert saved.status == RecoveryStatus.COMPLETED

    def test_execute_step_no_active_session(self, coordinator):
        """활성 세션 없으면 None 반환."""
        step = coordinator.execute_next_step("global")

        assert step is None

    def test_execute_step_failed_handler(self):
        """핸들러 실패 시 세션 실패."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        # BUDGET_RESET 핸들러를 실패로 모킹
        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            lambda s, st: {"success": False, "error": "Test error"},
        )

        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        step = coordinator.execute_next_step("global")

        assert step.status == RecoveryStatus.FAILED
        assert step.error_message == "Test error"

        # _fail_session()이 ACTIVE_SESSION_KEY를 유지하므로
        # 실패 세션이 조회 가능 (resume_recovery 지원)
        active = coordinator.get_active_session("global")
        assert active is not None
        assert active.status == RecoveryStatus.FAILED

    def test_execute_step_handler_exception(self):
        """핸들러 예외 시 세션 실패."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        def failing_handler(session, step):
            raise Exception("Handler exception")

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            failing_handler,
        )

        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        step = coordinator.execute_next_step("global")

        assert step.status == RecoveryStatus.FAILED
        assert "Handler exception" in step.error_message


class TestAbortRecovery:
    """abort_recovery() 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(backend=backend, recovery_lock=lock)

    def test_abort_recovery_success(self, coordinator):
        """복구 중단 성공."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        session = coordinator.abort_recovery(
            namespace="global",
            reason="Re-failure detected",
        )

        assert session is not None
        assert session.status == RecoveryStatus.ABORTED
        assert session.abort_reason == "Re-failure detected"
        assert session.completed_at is not None

    def test_abort_recovery_no_session(self, coordinator):
        """활성 세션 없으면 None 반환."""
        result = coordinator.abort_recovery(
            namespace="global",
            reason="No reason",
        )

        assert result is None

    def test_abort_recovery_clears_active(self, coordinator):
        """중단 후 활성 세션 클리어."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        coordinator.abort_recovery(
            namespace="global",
            reason="Test abort",
        )

        active = coordinator.get_active_session("global")
        assert active is None

    def test_abort_recovery_releases_lock(self, coordinator):
        """중단 후 락 해제."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        coordinator.abort_recovery(
            namespace="global",
            reason="Test abort",
        )

        # 락이 해제되어 새 복구 가능
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        assert session is not None


class TestFullRecoveryFlow:
    """전체 복구 플로우 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터 (핸들러 모킹)."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        # 모든 핸들러를 성공으로 모킹
        for step_type in RecoveryStepType:
            coord.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        return coord

    def test_complete_level3_recovery(self, coordinator):
        """LEVEL_3 전체 복구 플로우."""
        # 1. 복구 시작
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="system",
        )
        assert session.status == RecoveryStatus.IN_PROGRESS

        # 2. 단계 1: BUDGET_RESET
        step1 = coordinator.execute_next_step("global")
        assert step1.step_type == RecoveryStepType.BUDGET_RESET
        assert step1.status == RecoveryStatus.COMPLETED

        # 3. 단계 2: HEALTH_CHECK
        step2 = coordinator.execute_next_step("global")
        assert step2.step_type == RecoveryStepType.HEALTH_CHECK
        assert step2.status == RecoveryStatus.COMPLETED

        # 4. 단계 3: CANARY_RESUME
        step3 = coordinator.execute_next_step("global")
        assert step3.step_type == RecoveryStepType.CANARY_RESUME
        assert step3.status == RecoveryStatus.COMPLETED

        # 5. 단계 4: GOVERNANCE_NORMAL
        step4 = coordinator.execute_next_step("global")
        assert step4.step_type == RecoveryStepType.GOVERNANCE_NORMAL
        assert step4.status == RecoveryStatus.COMPLETED

        # 6. 완료 확인
        final_step = coordinator.execute_next_step("global")
        assert final_step is None

        # 7. 저장된 세션 확인
        saved_session = coordinator.get_session("global", session.id)
        assert saved_session.status == RecoveryStatus.COMPLETED
        assert saved_session.completed_at is not None

    def test_level2_has_3_steps(self, coordinator):
        """LEVEL_2는 3단계."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_2",
        )

        steps = []
        for _ in range(10):
            step = coordinator.execute_next_step("global")
            if step is None:
                break
            steps.append(step)

        assert len(steps) == 3
        assert steps[0].step_type == RecoveryStepType.BUDGET_RESET
        assert steps[1].step_type == RecoveryStepType.HEALTH_CHECK
        assert steps[2].step_type == RecoveryStepType.CANARY_RESUME

    def test_level1_has_2_steps(self, coordinator):
        """LEVEL_1은 2단계."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )

        steps = []
        for _ in range(10):
            step = coordinator.execute_next_step("global")
            if step is None:
                break
            steps.append(step)

        assert len(steps) == 2

    def test_recovery_after_completion(self, coordinator):
        """완료 후 다시 복구 가능."""
        # 첫 번째 복구
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        # 4단계 실행 + 5번째 호출로 완료 처리
        for _ in range(4):
            coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")  # 완료 처리

        # 두 번째 복구
        session2 = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        assert session2 is not None


class TestCheckRecoveryTrigger:
    """check_recovery_trigger() 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(backend=backend, recovery_lock=lock)

    def test_check_trigger_returns_dict(self, coordinator):
        """트리거 확인 결과는 딕셔너리."""
        result = coordinator.check_recovery_trigger("global")

        assert isinstance(result, dict)
        assert "can_recover" in result
        assert "current_level" in result


class TestSessionManagement:
    """세션 관리 테스트."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(backend=backend, recovery_lock=lock)

    def test_get_session_not_found(self, coordinator):
        """존재하지 않는 세션은 None."""
        session = coordinator.get_session("global", "nonexistent")
        assert session is None

    def test_get_active_session_not_found(self, coordinator):
        """활성 세션 없으면 None."""
        active = coordinator.get_active_session("global")
        assert active is None

    def test_session_progress_tracking(self, coordinator):
        """세션 진행 상황 추적."""
        # 성공 핸들러 등록
        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 2단계 실행
        coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")

        # 진행 상황 확인
        updated = coordinator.get_active_session("global")
        progress = updated.get_progress()

        assert progress["completed_steps"] == 2
        assert progress["total_steps"] == 4
        assert progress["progress_percent"] == 50.0


class TestSingleton:
    """싱글톤 팩토리 테스트."""

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        reset_recovery_coordinator()

        # 리셋 후 새 인스턴스 가능
        reset_recovery_coordinator()

    def test_get_singleton(self):
        """싱글톤 획득."""
        reset_recovery_coordinator()

        # MemoryStateBackend를 사용하도록 패치
        with patch("selfhealing.core.state_backend.get_state_backend") as mock:
            mock.return_value = MemoryStateBackend()

            coord1 = get_recovery_coordinator()
            coord2 = get_recovery_coordinator()

            assert coord1 is coord2

        reset_recovery_coordinator()


class TestDefaultHandlers:
    """기본 핸들러 테스트 (실제 의존성 없이)."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(backend=backend, recovery_lock=lock)

    def test_budget_reset_handler_no_dependency(self, coordinator):
        """BUDGET_RESET 핸들러 - 의존성 없을 때."""
        session = RecoverySession(
            id="test-session",
            namespace="global",
            trigger_level="LEVEL_3",
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            params={"target_multiplier": 1.0},
        )

        result = coordinator._handle_budget_reset(session, step)

        # 의존성 없어도 성공 (warning만 출력)
        assert result["success"] is True

    def test_health_check_handler_no_dependency(self, coordinator):
        """HEALTH_CHECK 핸들러 - 의존성 없을 때."""
        session = RecoverySession(
            id="test-session",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            params={"duration_minutes": 5, "error_rate_threshold": 0.1},
        )

        result = coordinator._handle_health_check(session, step)

        # 의존성 없으면 안정으로 가정
        assert result["success"] is True
        assert result["stability"]["assumed"] is True

    def test_canary_resume_handler_no_dependency(self, coordinator):
        """CANARY_RESUME 핸들러 - 의존성 없을 때."""
        session = RecoverySession(
            id="test-session",
            namespace="global",
            trigger_level="LEVEL_3",
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            params={"resume_paused_only": True},
        )

        result = coordinator._handle_canary_resume(session, step)

        # 의존성 없어도 성공 (skipped 표시)
        assert result["success"] is True
        assert result.get("skipped") is True

    def test_governance_normal_handler_no_dependency(self, coordinator):
        """GOVERNANCE_NORMAL 핸들러 - 의존성 없을 때."""
        session = RecoverySession(
            id="test-session",
            namespace="global",
            trigger_level="LEVEL_3",
        )
        step = RecoveryStep(
            step_type=RecoveryStepType.GOVERNANCE_NORMAL,
            order=4,
            params={"reason": "AUTO-RECOVERY"},
        )

        result = coordinator._handle_governance_normal(session, step)

        # 임포트 경로 수정으로 실제 EmergencyModeTracker 연결됨
        # 비활성 상태에서는 record_normal_restoration이 정상 완료
        assert result["success"] is True


# =============================================================================
# CascadeEvent 연동 테스트 (Phase 5.3)
# =============================================================================


class TestRecoveryCoordinatorCascadeEventIntegration:
    """RecoveryCoordinator CascadeEvent 연동 테스트 (Phase 5.3)."""

    @pytest.fixture
    def mock_cascade_auditor(self):
        """Mock CascadeEventAuditor."""
        mock = MagicMock()
        mock_event = MagicMock()
        mock_event.id = "cascade-test123"
        mock.record.return_value = mock_event
        return mock

    @pytest.fixture
    def mock_audit_recorder(self):
        """Mock RecoveryAuditRecorder."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def coordinator_with_auditors(self, mock_cascade_auditor, mock_audit_recorder):
        """감사기가 주입된 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
            cascade_auditor=mock_cascade_auditor,
            audit_recorder=mock_audit_recorder,
        )

    def test_start_recovery_records_cascade_event(self, coordinator_with_auditors, mock_cascade_auditor, mock_audit_recorder):
        """start_recovery()는 CascadeEvent를 기록해야 함."""
        session = coordinator_with_auditors.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="test-user",
        )

        # CascadeEventAuditor.record() 호출 확인
        assert mock_cascade_auditor.record.called
        call_kwargs = mock_cascade_auditor.record.call_args[1]

        assert call_kwargs["trigger_type"] == "RECOVERY_STARTED"
        assert call_kwargs["namespace"] == "global"
        assert call_kwargs["triggered_by"] == "test-user"
        assert "trigger_details" in call_kwargs
        assert call_kwargs["trigger_details"]["session_id"] == session.id

        # RecoveryAuditRecorder.record_recovery_event() 호출 확인
        assert mock_audit_recorder.record_recovery_event.called
        audit_call = mock_audit_recorder.record_recovery_event.call_args[1]
        assert audit_call["event_type"].value == "recovery_started"
        assert audit_call["session_id"] == session.id

    def test_execute_step_success_records_cascade_event(
        self, coordinator_with_auditors, mock_cascade_auditor, mock_audit_recorder
    ):
        """execute_next_step() 성공 시 CascadeEvent를 기록해야 함."""
        # 복구 시작
        session = coordinator_with_auditors.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="test-user",
        )

        # 기록 초기화
        mock_cascade_auditor.reset_mock()
        mock_audit_recorder.reset_mock()

        # 첫 번째 단계 실행
        step = coordinator_with_auditors.execute_next_step("global")

        # CascadeEvent 기록 확인
        assert mock_cascade_auditor.record.called
        call_kwargs = mock_cascade_auditor.record.call_args[1]
        assert call_kwargs["trigger_type"] == "RECOVERY_STEP_EXECUTED"
        assert "BUDGET_RESET" in call_kwargs["effects"][0]["action_type"].upper()

        # RecoveryAuditRecorder 호출 확인
        assert mock_audit_recorder.record_recovery_event.called
        audit_call = mock_audit_recorder.record_recovery_event.call_args[1]
        assert audit_call["event_type"].value == "recovery_step_executed"
        assert audit_call["step_type"] == "budget_reset"

    def test_execute_step_failure_records_cascade_event(self, mock_cascade_auditor, mock_audit_recorder):
        """execute_next_step() 실패 시 CascadeEvent를 기록해야 함."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
            cascade_auditor=mock_cascade_auditor,
            audit_recorder=mock_audit_recorder,
        )

        # 실패하는 핸들러 등록
        failing_handler = MagicMock(
            return_value={
                "success": False,
                "error": "Simulated failure",
            }
        )
        coordinator.register_step_handler(RecoveryStepType.BUDGET_RESET, failing_handler)

        # 복구 시작
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 기록 초기화
        mock_cascade_auditor.reset_mock()
        mock_audit_recorder.reset_mock()

        # 첫 번째 단계 실행 (실패)
        step = coordinator.execute_next_step("global")

        # CascadeEvent 기록 확인 (RECOVERY_STEP_FAILED)
        assert mock_cascade_auditor.record.called
        call_kwargs = mock_cascade_auditor.record.call_args[1]
        assert call_kwargs["trigger_type"] == "RECOVERY_STEP_FAILED"
        assert call_kwargs["effects"][0]["success"] is False

        # RecoveryAuditRecorder 호출 확인
        assert mock_audit_recorder.record_recovery_event.called
        audit_call = mock_audit_recorder.record_recovery_event.call_args[1]
        assert audit_call["event_type"].value == "recovery_step_failed"
        assert audit_call["error_message"] == "Simulated failure"

    def test_abort_recovery_records_cascade_event(self, coordinator_with_auditors, mock_cascade_auditor, mock_audit_recorder):
        """abort_recovery()는 CascadeEvent를 기록해야 함."""
        # 복구 시작
        session = coordinator_with_auditors.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 기록 초기화
        mock_cascade_auditor.reset_mock()
        mock_audit_recorder.reset_mock()

        # 복구 중단
        aborted = coordinator_with_auditors.abort_recovery(
            namespace="global",
            reason="Manual abort for testing",
        )

        # CascadeEvent 기록 확인
        assert mock_cascade_auditor.record.called
        call_kwargs = mock_cascade_auditor.record.call_args[1]
        assert call_kwargs["trigger_type"] == "RECOVERY_ABORTED"
        assert "abort_reason" in call_kwargs["effects"][0]["details"]

        # RecoveryAuditRecorder 호출 확인
        assert mock_audit_recorder.record_recovery_event.called
        audit_call = mock_audit_recorder.record_recovery_event.call_args[1]
        assert audit_call["event_type"].value == "recovery_aborted"
        assert audit_call["error_message"] == "Manual abort for testing"

    def test_complete_session_records_cascade_event(self, mock_cascade_auditor, mock_audit_recorder):
        """복구 완료 시 CascadeEvent를 기록해야 함."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
            cascade_auditor=mock_cascade_auditor,
            audit_recorder=mock_audit_recorder,
        )

        # 단일 단계로 빠르게 완료되는 복구 세션
        session = RecoverySession(
            id="test-session",
            namespace="test",
            trigger_level="LEVEL_1",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                )
            ],
            current_step_index=1,  # 모든 단계 완료됨
        )

        # 기록 초기화
        mock_cascade_auditor.reset_mock()
        mock_audit_recorder.reset_mock()

        # 완료 처리
        coordinator._complete_session(session)

        # CascadeEvent 기록 확인
        assert mock_cascade_auditor.record.called
        call_kwargs = mock_cascade_auditor.record.call_args[1]
        assert call_kwargs["trigger_type"] == "RECOVERY_COMPLETED"
        assert call_kwargs["effects"][0]["success"] is True

        # RecoveryAuditRecorder 호출 확인
        assert mock_audit_recorder.record_recovery_event.called
        audit_call = mock_audit_recorder.record_recovery_event.call_args[1]
        assert audit_call["event_type"].value == "recovery_completed"

    def test_cascade_auditor_not_set_still_works(self):
        """CascadeEventAuditor 미설정 시에도 정상 동작해야 함."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
            cascade_auditor=None,  # 미설정
            audit_recorder=None,  # 미설정
        )

        # 복구 시작 (예외 없이 동작해야 함)
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        assert session is not None
        assert session.status == RecoveryStatus.IN_PROGRESS

        # 단계 실행 (예외 없이 동작해야 함)
        step = coordinator.execute_next_step("global")
        assert step is not None

        # 중단 (예외 없이 동작해야 함)
        aborted = coordinator.abort_recovery("global", "Test abort")
        assert aborted is not None

    def test_cascade_event_contains_session_details(self, coordinator_with_auditors, mock_cascade_auditor):
        """CascadeEvent에 세션 상세 정보가 포함되어야 함."""
        session = coordinator_with_auditors.start_recovery(
            namespace="seoul",
            trigger_level="LEVEL_2",
            initiated_by="admin@example.com",
        )

        # trigger_details 검증
        call_kwargs = mock_cascade_auditor.record.call_args[1]
        trigger_details = call_kwargs["trigger_details"]

        assert trigger_details["session_id"] == session.id
        assert trigger_details["namespace"] == "seoul"
        assert trigger_details["trigger_level"] == "LEVEL_2"
        assert trigger_details["initiated_by"] == "admin@example.com"
        assert "status" in trigger_details

    def test_full_recovery_flow_with_cascade_audit(self, mock_cascade_auditor, mock_audit_recorder):
        """전체 복구 플로우에서 CascadeEvent 기록 확인."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
            cascade_auditor=mock_cascade_auditor,
            audit_recorder=mock_audit_recorder,
        )

        # 복구 시작
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        # 모든 단계 실행
        step_count = 0
        while True:
            step = coordinator.execute_next_step("global")
            if step is None:
                break
            step_count += 1

        # LEVEL_3는 4단계
        assert step_count == 4

        # CascadeEvent 호출 횟수 확인:
        # 1회 (start) + 4회 (steps) + 1회 (complete) = 6회
        # 단, _handle_all_steps_completed가 어떻게 구현되어 있는지에 따라 다름
        assert mock_cascade_auditor.record.call_count >= 5  # 최소 start + 4 steps


# =============================================================================
# _fail_session 동작 검증 (ACTIVE_SESSION_KEY 유지)
# =============================================================================


class TestFailSessionBehavior:
    """_fail_session() 동작 검증.

    _fail_session()이 ACTIVE_SESSION_KEY를 유지하여
    실패 세션이 get_active_session()으로 조회 가능한지 검증.
    """

    @pytest.fixture
    def coordinator(self):
        """실패 핸들러가 등록된 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        coord.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            lambda s, st: {"success": False, "error": "step failure"},
        )
        return coord

    def test_failed_session_remains_active(self, coordinator):
        """실패 후 get_active_session()이 FAILED 세션을 반환해야 한다."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        active = coordinator.get_active_session("global")
        assert active is not None
        assert active.status == RecoveryStatus.FAILED
        assert active.id == session.id

    def test_failed_session_has_abort_reason(self, coordinator):
        """실패 세션에 abort_reason이 기록되어야 한다."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        active = coordinator.get_active_session("global")
        assert active.abort_reason == "step failure"

    def test_failed_session_has_completed_at(self, coordinator):
        """실패 세션에 completed_at이 기록되어야 한다."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        active = coordinator.get_active_session("global")
        assert active.completed_at is not None

    def test_failed_session_lock_released(self, coordinator):
        """실패 후 분산 락이 해제되어야 한다."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        # 락이 해제되었므로 새 복구 시작 가능
        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            lambda s, st: {"success": True},
        )
        new_session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        assert new_session is not None
        assert new_session.status == RecoveryStatus.IN_PROGRESS

    def test_new_recovery_overwrites_failed_active_key(self, coordinator):
        """새 복구 시작 시 실패 세션의 ACTIVE_SESSION_KEY가 덮어써져야 한다."""
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        # 새 복구 시작 (FAILED 세션은 start_recovery 차단 대상이 아님)
        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            lambda s, st: {"success": True},
        )
        new_session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        active = coordinator.get_active_session("global")
        assert active.id == new_session.id
        assert active.status == RecoveryStatus.IN_PROGRESS


# =============================================================================
# resume_recovery 동작 검증
# =============================================================================


class TestResumeRecoveryBehavior:
    """resume_recovery() 동작 검증.

    실패한 복구 세션의 재개 기능을 검증.
    멱등성 인프라, 메타데이터 기록, 무한 루프 방지 등.
    """

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트마다 설정 캐시 초기화."""
        from selfhealing.settings.recovery_coordinator import (
            reset_recovery_coordinator_settings,
        )

        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    def _make_coordinator_with_failing_step(
        self,
        fail_step_type: RecoveryStepType = RecoveryStepType.CANARY_RESUME,
    ) -> RecoveryCoordinator:
        """특정 단계에서 실패하는 코디네이터 생성."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        for step_type in RecoveryStepType:
            if step_type == fail_step_type:
                coord.register_step_handler(
                    step_type,
                    lambda s, st: {"success": False, "error": f"{fail_step_type.value} failed"},
                )
            else:
                coord.register_step_handler(
                    step_type,
                    lambda s, st: {"success": True},
                )
        return coord

    def test_resume_no_failed_session_raises(self):
        """실패 세션이 없으면 ValueError."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        with pytest.raises(ValueError, match="No failed recovery session"):
            coordinator.resume_recovery(namespace="global")

    def test_resume_in_progress_session_raises(self):
        """IN_PROGRESS 세션에서는 ValueError."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )

        with pytest.raises(ValueError, match="No failed recovery session"):
            coordinator.resume_recovery(namespace="global")

    def test_resume_creates_new_session(self):
        """실패 세션 재개 시 새 세션이 생성되어야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.CANARY_RESUME,
        )
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="operator",
        )

        # BUDGET_RESET 성공 → HEALTH_CHECK 성공 → CANARY_RESUME 실패
        coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")

        failed_session = coordinator.get_active_session("global")
        assert failed_session.status == RecoveryStatus.FAILED

        # 재개 시 모든 핸들러를 성공으로 교체
        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        new_session = coordinator.resume_recovery(
            namespace="global",
            initiated_by="admin",
        )

        assert new_session.id != session.id
        assert new_session.status == RecoveryStatus.IN_PROGRESS
        assert new_session.trigger_level == session.trigger_level

    def test_resume_metadata_resumed_from(self):
        """재개 세션의 metadata에 원본 세션 ID가 기록되어야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.BUDGET_RESET,
        )
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="operator",
        )
        coordinator.execute_next_step("global")

        # 핸들러 성공으로 교체 후 재개
        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        new_session = coordinator.resume_recovery(
            namespace="global",
            initiated_by="admin",
        )

        assert new_session.metadata["resumed_from"] == session.id

    def test_resume_metadata_resumed_from_step(self):
        """재개 세션의 metadata에 실패 지점 인덱스가 기록되어야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.CANARY_RESUME,
        )
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        # BUDGET_RESET(0) 성공 → HEALTH_CHECK(1) 성공 → CANARY_RESUME(2) 실패
        coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")
        coordinator.execute_next_step("global")

        failed_session = coordinator.get_active_session("global")
        expected_step_index = failed_session.current_step_index

        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        new_session = coordinator.resume_recovery(namespace="global")

        assert new_session.metadata["resumed_from_step"] == expected_step_index

    def test_resume_metadata_resume_count_increments(self):
        """재개할 때마다 resume_count가 1씩 증가해야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.BUDGET_RESET,
        )
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        # 1차 재개 (실패 핸들러 유지로 다시 실패)
        new1 = coordinator.resume_recovery(namespace="global")
        assert new1.metadata["resume_count"] == 1

        # 새 세션에서 다시 실패
        coordinator.execute_next_step("global")

        # 2차 재개
        new2 = coordinator.resume_recovery(namespace="global")
        assert new2.metadata["resume_count"] == 2

    def test_resume_metadata_original_initiated_by(self):
        """재개 세션의 metadata에 원본 initiated_by가 기록되어야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.BUDGET_RESET,
        )
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="original_operator",
        )
        coordinator.execute_next_step("global")

        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        new_session = coordinator.resume_recovery(
            namespace="global",
            initiated_by="resume_admin",
        )

        assert new_session.initiated_by == "resume_admin"
        assert new_session.metadata["original_initiated_by"] == "original_operator"

    def test_resume_max_count_exceeded_raises(self):
        """max_resume_count 초과 시 ValueError."""
        from selfhealing.settings.recovery_coordinator import (
            get_recovery_coordinator_settings,
        )

        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.BUDGET_RESET,
        )
        settings = get_recovery_coordinator_settings()
        max_count = settings.max_resume_count

        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        coordinator.execute_next_step("global")

        # max_resume_count번 재개
        for i in range(max_count):
            coordinator.resume_recovery(namespace="global")
            coordinator.execute_next_step("global")

        # max_resume_count + 1번째 재개 시도 → ValueError
        with pytest.raises(ValueError, match="Max resume count"):
            coordinator.resume_recovery(namespace="global")

    def test_resume_preserves_trigger_level(self):
        """재개 세션이 원본 세션의 trigger_level을 유지해야 한다."""
        coordinator = self._make_coordinator_with_failing_step(
            RecoveryStepType.BUDGET_RESET,
        )
        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_2",
        )
        coordinator.execute_next_step("global")

        for step_type in RecoveryStepType:
            coordinator.register_step_handler(
                step_type,
                lambda s, st: {"success": True},
            )

        new_session = coordinator.resume_recovery(namespace="global")
        assert new_session.trigger_level == "LEVEL_2"


# =============================================================================
# max_resume_count 설정 계약 검증
# =============================================================================


class TestMaxResumeCountContract:
    """max_resume_count 설정 필드 계약 검증.

    RecoveryCoordinatorSettings.max_resume_count의
    기본값, 범위 제한이 설계대로 구현되었는지 검증.
    """

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트마다 설정 캐시 초기화."""
        from selfhealing.settings.recovery_coordinator import (
            reset_recovery_coordinator_settings,
        )

        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    def test_default_value(self):
        """max_resume_count 기본값은 3이어야 한다."""
        from selfhealing.settings.recovery_coordinator import (
            RecoveryCoordinatorSettings,
        )

        settings = RecoveryCoordinatorSettings()
        assert settings.max_resume_count == 3

    def test_min_bound(self):
        """max_resume_count 최솟값은 1이어야 한다."""
        from pydantic import ValidationError

        from selfhealing.settings.recovery_coordinator import (
            RecoveryCoordinatorSettings,
        )

        with pytest.raises(ValidationError):
            RecoveryCoordinatorSettings(max_resume_count=0)

    def test_max_bound(self):
        """max_resume_count 최댓값은 10이어야 한다."""
        from pydantic import ValidationError

        from selfhealing.settings.recovery_coordinator import (
            RecoveryCoordinatorSettings,
        )

        with pytest.raises(ValidationError):
            RecoveryCoordinatorSettings(max_resume_count=11)

    def test_valid_range(self):
        """유효 범위(1~10) 내 값은 설정 가능해야 한다."""
        from selfhealing.settings.recovery_coordinator import (
            RecoveryCoordinatorSettings,
        )

        settings = RecoveryCoordinatorSettings(max_resume_count=5)
        assert settings.max_resume_count == 5
