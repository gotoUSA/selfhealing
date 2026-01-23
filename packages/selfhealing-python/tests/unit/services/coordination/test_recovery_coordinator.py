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

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_state import (
    RecoveryStepType,
    RecoveryStep,
    RecoverySession,
)
from selfhealing.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from selfhealing.services.coordination.recovery_coordinator import (
    RecoveryCoordinator,
    get_recovery_coordinator,
    reset_recovery_coordinator,
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
        
        # 세션도 실패 상태
        active = coordinator.get_active_session("global")
        assert active is None  # 실패 후 클리어됨

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
        
        # 의존성 없어도 성공 (skipped 표시)
        assert result["success"] is True
        assert result.get("skipped") is True
