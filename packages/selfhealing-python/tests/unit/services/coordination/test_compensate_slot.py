"""
Unit tests for Recovery Step Compensate Slot.

테스트 대상:
- CompensationStatus Enum 계약
- CompensationResult 구조체
- RecoveryStep의 result_data / compensation_status 필드
- RecoveryCoordinator의 보상 핸들러 등록 및 역순 보상 실행
- _fail_session()의 COMPENSATING 상태 전이 및 보상 루프
- Lock 하트비트 호출
- 하위 호환성 (기존 JSON 역직렬화)
"""

import pytest
from unittest.mock import MagicMock, patch, call

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.coordination.enums import (
    CompensationStatus,
    RecoveryStatus,
)
from selfhealing.services.coordination.recovery_state import (
    CompensationResult,
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)
from selfhealing.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from selfhealing.services.coordination.recovery_coordinator import (
    RecoveryCoordinator,
)


# =========================================================================
# Contract Tests — 설계 계약값 검증 (하드코딩 필수)
# =========================================================================


class TestCompensationStatusContract:
    """CompensationStatus Enum 설계 계약값 검증."""

    def test_compensation_status_values(self):
        """CompensationStatus 4개 상태값 계약."""
        assert CompensationStatus.NOT_REQUIRED == "not_required"
        assert CompensationStatus.PENDING == "pending"
        assert CompensationStatus.COMPENSATED == "compensated"
        assert CompensationStatus.COMPENSATE_FAILED == "compensate_failed"

    def test_compensation_status_count(self):
        """CompensationStatus는 4개 멤버."""
        assert len(CompensationStatus) == 4

    def test_recovery_status_compensating_value(self):
        """RecoveryStatus.COMPENSATING 계약값: 'compensating'."""
        assert RecoveryStatus.COMPENSATING == "compensating"


class TestCompensationResultContract:
    """CompensationResult 구조체 계약 검증."""

    def test_empty_result_all_compensated(self):
        """빈 CompensationResult는 all_compensated == True."""
        result = CompensationResult()
        assert result.all_compensated is True
        assert result.compensated_steps == []
        assert result.failed_steps == []
        assert result.skipped_steps == []

    def test_all_compensated_false_when_failed(self):
        """failed_steps가 있으면 all_compensated == False."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            status=RecoveryStatus.COMPLETED,
        )
        result = CompensationResult(
            failed_steps=[(step, "compensation error")],
        )
        assert result.all_compensated is False

    def test_compensation_result_structure(self):
        """CompensationResult의 compensated/failed/skipped 분류 정확성."""
        compensated_step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            status=RecoveryStatus.COMPLETED,
        )
        failed_step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            status=RecoveryStatus.COMPLETED,
        )
        skipped_step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            status=RecoveryStatus.COMPLETED,
        )

        result = CompensationResult(
            compensated_steps=[compensated_step],
            failed_steps=[(failed_step, "timeout")],
            skipped_steps=[skipped_step],
        )

        assert len(result.compensated_steps) == 1
        assert len(result.failed_steps) == 1
        assert len(result.skipped_steps) == 1
        assert result.failed_steps[0][1] == "timeout"
        assert result.all_compensated is False


class TestRecoveryStepNewFieldsContract:
    """RecoveryStep 신규 필드 계약 검증."""

    def test_step_to_dict_includes_new_fields(self):
        """to_dict()에 result_data, compensation_status 포함."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            status=RecoveryStatus.COMPLETED,
            result_data={"success": True, "multiplier": 1.0},
            compensation_status=CompensationStatus.PENDING,
        )

        d = step.to_dict()

        assert "result_data" in d
        assert d["result_data"] == {"success": True, "multiplier": 1.0}
        assert "compensation_status" in d
        assert d["compensation_status"] == "pending"

    def test_step_from_dict_backward_compatible(self):
        """기존 JSON(새 필드 없음)에서 역직렬화 시 기본값 적용."""
        old_json = {
            "step_type": "budget_reset",
            "order": 1,
            "status": "completed",
        }

        step = RecoveryStep.from_dict(old_json)

        assert step.result_data == {}
        assert step.compensation_status == CompensationStatus.NOT_REQUIRED

    def test_step_from_dict_with_new_fields(self):
        """새 필드가 포함된 JSON에서 정상 역직렬화."""
        data = {
            "step_type": "canary_resume",
            "order": 3,
            "status": "completed",
            "result_data": {"success": True, "resumed_count": 3},
            "compensation_status": "compensated",
        }

        step = RecoveryStep.from_dict(data)

        assert step.result_data == {"success": True, "resumed_count": 3}
        assert step.compensation_status == CompensationStatus.COMPENSATED

    def test_step_default_new_fields(self):
        """RecoveryStep 생성 시 새 필드 기본값."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
        )

        assert step.result_data == {}
        assert step.compensation_status == CompensationStatus.NOT_REQUIRED


# =========================================================================
# Behavior Tests — 동작 검증 (소스 참조)
# =========================================================================


class TestRegisterStepHandlerBehavior:
    """register_step_handler() 보상 핸들러 등록 동작 검증."""

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

    def test_register_with_compensate(self, coordinator):
        """compensate 함수가 _compensate_handlers에 저장됨."""
        forward = MagicMock(return_value={"success": True})
        compensate = MagicMock(return_value={"success": True})

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            forward,
            compensate=compensate,
        )

        assert coordinator._step_handlers[RecoveryStepType.BUDGET_RESET] is forward
        assert coordinator._compensate_handlers[RecoveryStepType.BUDGET_RESET] is compensate

    def test_register_without_compensate(self, coordinator):
        """compensate 미지정 시 _compensate_handlers에 등록되지 않음."""
        forward = MagicMock(return_value={"success": True})

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            forward,
        )

        assert coordinator._step_handlers[RecoveryStepType.BUDGET_RESET] is forward
        assert RecoveryStepType.BUDGET_RESET not in coordinator._compensate_handlers

    def test_default_handlers_no_compensate(self, coordinator):
        """기본 핸들러 등록 시 _compensate_handlers는 비어있음."""
        assert len(coordinator._compensate_handlers) == 0


class TestExecuteNextStepResultDataBehavior:
    """execute_next_step()에서 result_data 저장 동작 검증."""

    @pytest.fixture
    def coordinator(self):
        """성공 핸들러가 등록된 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        # 모든 핸들러를 성공 반환으로 설정
        success_result = {"success": True, "multiplier": 1.0}
        for step_type in RecoveryStepType:
            coord.register_step_handler(
                step_type,
                MagicMock(return_value=success_result),
            )

        return coord

    def test_result_data_saved_on_step_success(self, coordinator):
        """Forward 성공 시 step.result_data에 핸들러 반환값 저장됨."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )

        step = coordinator.execute_next_step("global")

        assert step is not None
        assert step.status == RecoveryStatus.COMPLETED
        assert step.result_data == {"success": True, "multiplier": 1.0}
        assert step.compensation_status == CompensationStatus.PENDING


class TestAttemptCompensationBehavior:
    """_attempt_compensation() 역순 보상 동작 검증."""

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

    def _make_completed_session(self, coordinator, completed_count=2):
        """완료된 Step이 있는 세션 생성 헬퍼."""
        session = RecoverySession(
            id="recovery-test123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.COMPLETED,
                    result_data={"success": True, "multiplier": 1.0},
                    compensation_status=CompensationStatus.PENDING,
                ),
                RecoveryStep(
                    step_type=RecoveryStepType.HEALTH_CHECK,
                    order=2,
                    status=RecoveryStatus.COMPLETED,
                    result_data={"success": True},
                    compensation_status=CompensationStatus.PENDING,
                ),
                RecoveryStep(
                    step_type=RecoveryStepType.CANARY_RESUME,
                    order=3,
                    status=RecoveryStatus.NOT_STARTED,
                ),
            ][:completed_count + 1],
            current_step_index=completed_count,
        )
        return session

    def test_attempt_compensation_reverse_order(self, coordinator):
        """완료 Step이 역순(order 내림차순)으로 보상됨."""
        call_order = []

        def comp_budget(session, step):
            call_order.append(step.step_type)
            return {"success": True}

        def comp_health(session, step):
            call_order.append(step.step_type)
            return {"success": True}

        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: comp_budget,
            RecoveryStepType.HEALTH_CHECK: comp_health,
        }

        session = self._make_completed_session(coordinator)
        result = coordinator._attempt_compensation(session)

        # order=2(HEALTH_CHECK)가 먼저, order=1(BUDGET_RESET)이 나중
        assert call_order == [
            RecoveryStepType.HEALTH_CHECK,
            RecoveryStepType.BUDGET_RESET,
        ]
        assert len(result.compensated_steps) == 2
        assert result.all_compensated is True

    def test_attempt_compensation_skip_no_handler(self, coordinator):
        """compensate 미등록 Step은 건너뜀 + result.skipped_steps에 포함."""
        # 보상 핸들러 없이 실행
        session = self._make_completed_session(coordinator)
        result = coordinator._attempt_compensation(session)

        assert len(result.skipped_steps) == 2
        assert len(result.compensated_steps) == 0
        assert result.all_compensated is True  # 실패는 없으므로 True

    def test_attempt_compensation_fail_open(self, coordinator):
        """보상 실패해도 세션 실패 처리가 계속 진행됨 (Fail-Open)."""
        def comp_fail(session, step):
            return {"success": False, "error": "Cannot rollback budget"}

        def comp_success(session, step):
            return {"success": True}

        # BUDGET_RESET은 실패, HEALTH_CHECK은 성공
        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: comp_fail,
            RecoveryStepType.HEALTH_CHECK: comp_success,
        }

        session = self._make_completed_session(coordinator)
        result = coordinator._attempt_compensation(session)

        # 역순: HEALTH_CHECK → BUDGET_RESET
        assert len(result.compensated_steps) == 1
        assert len(result.failed_steps) == 1
        assert result.failed_steps[0][1] == "Cannot rollback budget"
        assert result.all_compensated is False

    def test_attempt_compensation_exception_fail_open(self, coordinator):
        """보상 핸들러가 예외를 던져도 다른 Step 보상이 계속됨."""
        def comp_raise(session, step):
            raise RuntimeError("unexpected error")

        def comp_success(session, step):
            return {"success": True}

        # HEALTH_CHECK(order=2)는 예외, BUDGET_RESET(order=1)은 성공
        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: comp_success,
            RecoveryStepType.HEALTH_CHECK: comp_raise,
        }

        session = self._make_completed_session(coordinator)
        result = coordinator._attempt_compensation(session)

        # 역순: HEALTH_CHECK(예외) → BUDGET_RESET(성공)
        assert len(result.compensated_steps) == 1
        assert len(result.failed_steps) == 1
        assert "unexpected error" in result.failed_steps[0][1]

    def test_attempt_compensation_empty(self, coordinator):
        """완료 Step 없으면 빈 CompensationResult 반환."""
        session = RecoverySession(
            id="recovery-empty",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.NOT_STARTED,
                ),
            ],
        )

        result = coordinator._attempt_compensation(session)

        assert result.compensated_steps == []
        assert result.failed_steps == []
        assert result.skipped_steps == []
        assert result.all_compensated is True

    def test_already_compensated_skip(self, coordinator):
        """compensation_status == COMPENSATED인 Step은 재보상하지 않음."""
        call_count = 0

        def comp_budget(session, step):
            nonlocal call_count
            call_count += 1
            return {"success": True}

        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: comp_budget,
        }

        session = RecoverySession(
            id="recovery-test123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.COMPLETED,
                    compensation_status=CompensationStatus.COMPENSATED,
                ),
            ],
        )

        result = coordinator._attempt_compensation(session)

        assert call_count == 0
        assert len(result.compensated_steps) == 1  # 이미 보상 완료로 집계
        assert result.all_compensated is True

    def test_compensation_status_persisted(self, coordinator):
        """보상 성공 시 step.compensation_status == COMPENSATED 저장됨."""
        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: lambda s, st: {"success": True},
        }

        session = self._make_completed_session(coordinator, completed_count=1)
        # _save_session을 위해 backend에 세션 데이터 설정
        coordinator._save_session(session)

        coordinator._attempt_compensation(session)

        budget_step = session.steps[0]
        assert budget_step.compensation_status == CompensationStatus.COMPENSATED

    def test_compensation_status_failed_persisted(self, coordinator):
        """보상 실패 시 step.compensation_status == COMPENSATE_FAILED 저장됨."""
        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: lambda s, st: {"success": False, "error": "fail"},
        }

        session = RecoverySession(
            id="recovery-test123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.COMPLETED,
                    compensation_status=CompensationStatus.PENDING,
                ),
            ],
        )
        coordinator._save_session(session)

        coordinator._attempt_compensation(session)

        assert session.steps[0].compensation_status == CompensationStatus.COMPENSATE_FAILED

    def test_lock_extended_during_compensation(self, coordinator):
        """보상 루프에서 _recovery_lock.extend() 호출됨."""
        mock_lock = MagicMock()
        mock_lock.extend = MagicMock(return_value=True)
        coordinator._recovery_lock = mock_lock

        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: lambda s, st: {"success": True},
            RecoveryStepType.HEALTH_CHECK: lambda s, st: {"success": True},
        }

        session = self._make_completed_session(coordinator)
        coordinator._attempt_compensation(session)

        # 완료 Step 2개 → extend() 2회 호출
        assert mock_lock.extend.call_count == 2
        # 각 호출의 additional_seconds=300 확인
        for call_args in mock_lock.extend.call_args_list:
            assert call_args == call(
                session.namespace,
                session.id,
                additional_seconds=300,
            )

    def test_abort_reason_accessible_in_compensate(self, coordinator):
        """compensate 핸들러 내에서 session.abort_reason 접근 가능."""
        captured_reason = {}

        def comp_capture(session, step):
            captured_reason["abort_reason"] = session.abort_reason
            return {"success": True}

        coordinator._compensate_handlers = {
            RecoveryStepType.BUDGET_RESET: comp_capture,
        }

        session = RecoverySession(
            id="recovery-test123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.COMPLETED,
                    compensation_status=CompensationStatus.PENDING,
                ),
            ],
        )
        # abort_reason 선설정 (실제로는 _fail_session에서 설정)
        session.abort_reason = "Health check failed: error_rate 15%"
        coordinator._save_session(session)

        coordinator._attempt_compensation(session)

        assert captured_reason["abort_reason"] == "Health check failed: error_rate 15%"


class TestFailSessionCompensationBehavior:
    """_fail_session()의 보상 루프 및 상태 전이 동작 검증."""

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

    def test_fail_session_calls_compensation(self, coordinator):
        """_fail_session()이 _attempt_compensation() 호출."""
        with patch.object(
            coordinator,
            "_attempt_compensation",
            return_value=CompensationResult(),
        ) as mock_comp:
            session = RecoverySession(
                id="recovery-test123",
                namespace="global",
                trigger_level="LEVEL_3",
                status=RecoveryStatus.IN_PROGRESS,
                steps=[],
            )
            # 락 획득 필요 (release 시)
            coordinator._recovery_lock.acquire("global", session.id)
            coordinator._save_session(session)

            coordinator._fail_session(session, "test error")

            mock_comp.assert_called_once_with(session)

    def test_fail_session_compensating_state(self, coordinator):
        """_fail_session() 진입 시 COMPENSATING → 보상 후 FAILED 전이."""
        status_log = []

        original_save = coordinator._save_session

        def spy_save(session):
            status_log.append(session.status)
            original_save(session)

        coordinator._save_session = spy_save

        session = RecoverySession(
            id="recovery-test123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[],
        )
        coordinator._recovery_lock.acquire("global", session.id)

        coordinator._fail_session(session, "step failed")

        # 첫 번째 save: COMPENSATING, 이후: FAILED
        assert RecoveryStatus.COMPENSATING in status_log
        assert status_log[-1] == RecoveryStatus.FAILED
        assert session.status == RecoveryStatus.FAILED
        assert session.abort_reason == "step failed"
        assert session.completed_at is not None


class TestStartRecoveryCompensatingBlockBehavior:
    """COMPENSATING 상태에서 새 복구 차단 동작 검증."""

    @pytest.fixture
    def coordinator(self):
        """테스트용 코디네이터."""
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        # 모든 핸들러를 성공 반환으로 설정
        for step_type in RecoveryStepType:
            coord.register_step_handler(
                step_type,
                MagicMock(return_value={"success": True}),
            )
        return coord

    def test_start_recovery_blocks_compensating(self, coordinator):
        """status == COMPENSATING인 세션이 있으면 새 복구 차단."""
        # COMPENSATING 상태의 세션을 직접 주입
        session = RecoverySession(
            id="recovery-comp123",
            namespace="global",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.COMPENSATING,
            steps=[],
        )
        coordinator._save_session(session)
        coordinator._set_active_session("global", session.id)
        # 락도 점유 (actual coordinator flow에서 COMPENSATING 상태의 세션은 락 보유)
        coordinator._recovery_lock.acquire("global", session.id)

        with pytest.raises(ValueError, match="Recovery already in progress"):
            coordinator.start_recovery(
                namespace="global",
                trigger_level="LEVEL_3",
            )
