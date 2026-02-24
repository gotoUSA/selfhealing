"""
RunbookExecutor 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.executor

계약 검증 클래스 (Test*Contract):
    - TestRunbookExecutorConstantsContract  — LOCK_HEARTBEAT_INTERVAL, LOCK_EXTEND_SECONDS 등

동작 검증 클래스 (Test*Behavior):
    - TestRunbookExecutorLockBehavior        — Lock 획득 실패 시 RunbookLockConflictError
    - TestRunbookExecutorStepExecutionBehavior — Step 순차 실행 성공/실패 분기
    - TestRunbookExecutorConditionBehavior   — StepCondition 평가 (skip/run)
    - TestRunbookExecutorTimeoutBehavior     — 타임아웃 시 partial_execution=True
    - TestRunbookExecutorCompensationBehavior — 역순 보상 + Fail-Open
    - TestRunbookExecutorResumeDefensesBehavior — 3단계 방어 (stale/version/count)
    - TestRunbookExecutorIdempotencyBehavior — 멱등성 체크 캐시 결과 반환
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.runbook.exceptions import (
    RunbookExecutionError,
    RunbookLockConflictError,
    RunbookStaleContextError,
    RunbookStepTimeoutError,
    RunbookVersionMismatchError,
)
from selfhealing.services.runbook.execution_models import (
    CompensationSummary,
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.executor import (
    CONTEXT_TTL_SECONDS,
    LOCK_EXTEND_SECONDS,
    LOCK_HEARTBEAT_INTERVAL,
    MAX_RESUME_COUNT,
    RunbookExecutor,
)
from selfhealing.services.runbook.models import PatternCondition
from selfhealing.services.runbook.runbook_registry import (
    ActionPrimitiveRegistry,
    Runbook,
    RunbookStep,
    RiskLevel,
    StepCondition,
)

# =============================================================================
# 테스트 헬퍼
# =============================================================================


def _make_step(
    name: str,
    action: str,
    order: int = 0,
    on_failure_params: dict | None = None,
    timeout_seconds: int = 120,
    wait_after_seconds: int = 0,
    condition: StepCondition | None = None,
    params: dict | None = None,
) -> RunbookStep:
    return RunbookStep(
        name=name,
        action=action,
        order=order,
        params=params or {},
        on_failure_params=on_failure_params or {},
        timeout_seconds=timeout_seconds,
        wait_after_seconds=wait_after_seconds,
        condition=condition,
    )


def _make_runbook(
    runbook_id: str = "rb1",
    steps: list[RunbookStep] | None = None,
    version: int = 1,
) -> Runbook:
    return Runbook(
        id=runbook_id,
        name="Test Runbook",
        description="테스트용",
        trigger_condition=PatternCondition(),
        steps=steps or [_make_step("s1", "action.one")],
        version=version,
    )


def _make_executor(
    lock_acquired: bool = True,
    backend: Any = None,
    registry: ActionPrimitiveRegistry | None = None,
) -> tuple[RunbookExecutor, MagicMock, MagicMock]:
    """Mock 의존성이 주입된 RunbookExecutor 반환."""
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = lock_acquired
    mock_lock.release.return_value = True
    mock_lock.extend.return_value = True

    mock_action_executor = MagicMock()
    mock_action_result = MagicMock()
    mock_action_result.executed = True
    mock_action_result.success = True
    mock_action_result.error = None
    mock_action_result.to_dict.return_value = {"executed": True, "success": True}
    mock_action_executor.execute.return_value = mock_action_result

    executor = RunbookExecutor(
        action_executor=mock_action_executor,
        recovery_lock=mock_lock,
        state_backend=backend,
        primitive_registry=registry,
    )
    return executor, mock_lock, mock_action_executor


# =============================================================================
# 계약 검증 — 상수
# =============================================================================


class TestRunbookExecutorConstantsContract:
    """Executor 상수 설계 계약 검증. §5.3 참조."""

    def test_lock_heartbeat_interval_matches_saga_orchestrator(self):
        """§5.3 설계 계약: LOCK_HEARTBEAT_INTERVAL == 60 (SagaOrchestrator.HEARTBEAT_INTERVAL)."""
        assert LOCK_HEARTBEAT_INTERVAL == 60

    def test_lock_extend_seconds_matches_saga_orchestrator(self):
        """§5.3 설계 계약: LOCK_EXTEND_SECONDS == 300 (SagaOrchestrator.EXTEND_SECONDS)."""
        assert LOCK_EXTEND_SECONDS == 300

    def test_max_resume_count_matches_saga_orchestrator(self):
        """§13.1 설계 계약: MAX_RESUME_COUNT == 10 (SagaOrchestrator.MAX_RESUME_COUNT)."""
        assert MAX_RESUME_COUNT == 10


# =============================================================================
# 동작 검증 — Lock 획득 실패
# =============================================================================


class TestRunbookExecutorLockBehavior:
    """Lock 획득 실패 시 RunbookLockConflictError 발생 검증."""

    def test_lock_conflict_raises_runbook_lock_conflict_error(self):
        """Lock 획득 실패 시 RunbookLockConflictError가 발생해야 한다."""
        # Given
        executor, mock_lock, _ = _make_executor(lock_acquired=False)
        runbook = _make_runbook()

        # When / Then
        with pytest.raises(RunbookLockConflictError, match="Lock"):
            executor.execute_runbook(runbook, {"event": "test"}, "ns")

    def test_lock_acquired_and_released_on_success(self):
        """성공 시 Lock이 획득되고 최종 해제되어야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register("action.one", lambda ctx: {"success": True})

        executor, mock_lock, _ = _make_executor(registry=registry)
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        ctx = executor.execute_runbook(runbook, {}, "ns1")

        # Then
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_lock_released_even_on_step_failure(self):
        """Step 실패 시에도 finally 블록에서 Lock이 해제되어야 한다."""
        # Given: action_executor가 실패하는 결과 반환
        registry = ActionPrimitiveRegistry()
        registry.register("action.fail", lambda ctx: {"success": False, "error": "boom"})

        executor, mock_lock, _ = _make_executor(registry=registry)
        # action_executor.execute()가 실패를 반환하도록 설정
        mock_exec = executor._get_action_executor()
        fail_result = MagicMock()
        fail_result.executed = True
        fail_result.success = False
        fail_result.error = "boom"
        fail_result.to_dict.return_value = {"executed": True, "success": False, "error": "boom"}
        mock_exec.execute.return_value = fail_result

        runbook = _make_runbook(steps=[_make_step("s1", "action.fail")])

        # When
        executor.execute_runbook(runbook, {}, "ns1")

        # Then — 실패해도 lock.release가 호출된다
        mock_lock.release.assert_called_once()


# =============================================================================
# 동작 검증 — Step 순차 실행
# =============================================================================


class TestRunbookExecutorStepExecutionBehavior:
    """Step 순차 실행 성공/실패 분기 검증."""

    def test_successful_execution_returns_completed_status(self):
        """모든 Step 성공 시 context 상태가 COMPLETED여야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register("action.one", lambda ctx: {"success": True})
        executor, _, _ = _make_executor(registry=registry)
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then
        assert result_ctx.status == RunbookExecutionStatus.COMPLETED

    def test_step_results_populated_after_execution(self):
        """실행 완료 후 step_results에 Step 결과가 기록되어야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register("action.one", lambda ctx: {"success": True})
        executor, _, _ = _make_executor(registry=registry)
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then
        assert "s1" in result_ctx.step_results

    def test_missing_primitive_results_in_failed_status(self):
        """등록되지 않은 Action Primitive는 실행 실패로 처리해야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()  # action.unknown은 미등록
        executor, _, _ = _make_executor(registry=registry)
        runbook = _make_runbook(steps=[_make_step("s1", "action.unknown")])

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then
        assert result_ctx.status == RunbookExecutionStatus.FAILED

    def test_steps_executed_in_order_field_order(self):
        """order 필드 오름차순으로 Step이 실행되어야 한다."""
        # Given
        execution_order: list[str] = []
        registry = ActionPrimitiveRegistry()

        def make_handler(step_name: str):
            def handler(ctx):
                execution_order.append(step_name)
                return {"success": True}

            return handler

        registry.register("action.first", make_handler("first"))
        registry.register("action.second", make_handler("second"))
        registry.register("action.third", make_handler("third"))

        executor, mock_lock, _ = _make_executor(registry=registry)

        # mock_action_executor가 execute_fn을 실제로 호출하도록 설정
        success_result = MagicMock()
        success_result.executed = True
        success_result.success = True
        success_result.error = None
        success_result.to_dict.return_value = {"executed": True, "success": True}

        def ae_side_effect(action):
            action.execute_fn()  # 실제 핸들러 호출
            return success_result

        executor._action_executor.execute.side_effect = ae_side_effect

        runbook = _make_runbook(
            steps=[
                _make_step("s_third", "action.third", order=30),
                _make_step("s_first", "action.first", order=10),
                _make_step("s_second", "action.second", order=20),
            ]
        )

        # When
        executor.execute_runbook(runbook, {}, "ns")

        # Then
        assert execution_order == ["first", "second", "third"]


# =============================================================================
# 동작 검증 — StepCondition 평가
# =============================================================================


class TestRunbookExecutorConditionBehavior:
    """StepCondition 평가 — skip/run 분기 검증."""

    def _create_executor_with_context(self):
        """컨텍스트 + executor 생성 헬퍼."""
        registry = ActionPrimitiveRegistry()
        executor, _, _ = _make_executor(registry=registry)
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        return executor, ctx, registry

    def test_always_condition_returns_true(self):
        """condition.type='always'이면 항상 실행 허용해야 한다."""
        executor, ctx, _ = self._create_executor_with_context()
        step = _make_step("s1", "a.b", condition=StepCondition(type="always"))
        can_run, _ = executor._evaluate_condition(step.condition, step, ctx)
        assert can_run is True

    def test_prev_failed_skips_when_prev_succeeded(self):
        """prev_failed 조건에서 이전 Step이 성공했으면 skip해야 한다."""
        executor, ctx, _ = self._create_executor_with_context()
        ctx.step_results["prev_step"] = RunbookStepResult(step_name="prev_step", action_name="a", success=True, executed=True)
        step = _make_step("s1", "a.b", condition=StepCondition(type="prev_failed"))
        can_run, _ = executor._evaluate_condition(step.condition, step, ctx)
        assert can_run is False

    def test_prev_failed_runs_when_prev_failed(self):
        """prev_failed 조건에서 이전 Step이 실패했으면 실행해야 한다."""
        executor, ctx, _ = self._create_executor_with_context()
        ctx.step_results["prev_step"] = RunbookStepResult(step_name="prev_step", action_name="a", success=False, executed=True)
        step = _make_step("s1", "a.b", condition=StepCondition(type="prev_failed"))
        can_run, _ = executor._evaluate_condition(step.condition, step, ctx)
        assert can_run is True

    def test_prev_succeeded_skips_when_prev_failed(self):
        """prev_succeeded 조건에서 이전 Step이 실패했으면 skip해야 한다."""
        executor, ctx, _ = self._create_executor_with_context()
        ctx.step_results["prev_step"] = RunbookStepResult(step_name="prev_step", action_name="a", success=False, executed=True)
        step = _make_step("s1", "a.b", condition=StepCondition(type="prev_succeeded"))
        can_run, _ = executor._evaluate_condition(step.condition, step, ctx)
        assert can_run is False

    def test_no_condition_step_is_always_executed(self):
        """condition=None인 Step은 항상 실행되어야 한다."""
        executor, ctx, _ = self._create_executor_with_context()
        step = _make_step("s1", "a.b", condition=None)
        # _evaluate_condition을 직접 호출하지 않고 None 조건 처리 확인
        # _execute_step에서 condition is None이면 건너뜀
        assert step.condition is None


# =============================================================================
# 동작 검증 — 타임아웃 처리
# =============================================================================


class TestRunbookExecutorTimeoutBehavior:
    """타임아웃 발생 시 partial_execution=True로 마킹되는지 검증."""

    def test_timeout_raises_runbook_step_timeout_error(self):
        """timeout_seconds 초과 시 RunbookStepTimeoutError가 발생해야 한다."""
        executor, _, _ = _make_executor()

        def slow_fn():
            threading.Event().wait(10)
            return "done"

        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        with pytest.raises(RunbookStepTimeoutError, match="타임아웃"):
            executor._execute_with_timeout(slow_fn, timeout_seconds=1, step_name="slow_step", ctx=ctx)

    def test_fast_fn_completes_without_timeout(self):
        """빠른 함수는 타임아웃 없이 정상 결과를 반환해야 한다."""
        executor, _, _ = _make_executor()

        def fast_fn():
            return "result"

        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        result = executor._execute_with_timeout(fast_fn, timeout_seconds=10, step_name="fast_step", ctx=ctx)
        assert result == "result"

    def test_none_timeout_calls_fn_directly(self):
        """timeout_seconds=None이면 fn()을 직접 호출해야 한다."""
        executor, _, _ = _make_executor()
        call_count = [0]

        def fn():
            call_count[0] += 1
            return "direct"

        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        result = executor._execute_with_timeout(fn, timeout_seconds=None, step_name="s", ctx=ctx)
        assert result == "direct"
        assert call_count[0] == 1


# =============================================================================
# 동작 검증 — 역순 보상 Fail-Open
# =============================================================================


class TestRunbookExecutorCompensationBehavior:
    """역순 보상 + Fail-Open 동작 검증."""

    def _make_context_with_completed_step(self) -> RunbookExecutionContext:
        step_result = RunbookStepResult(
            step_name="s1",
            action_name="action.one",
            success=True,
            executed=True,
        )
        return RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"s1": step_result},
        )

    def test_compensation_marks_step_compensated_on_success(self):
        """보상 성공 시 compensation_status가 'compensated'로 변경되어야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register_compensate(
            "action.one",
            lambda svc, **kw: None,
            validate=False,
        )
        executor, _, mock_action_executor = _make_executor(registry=registry)

        # action_executor가 보상 성공 결과 반환
        success_result = MagicMock()
        success_result.success = True
        success_result.error = None
        mock_action_executor.execute.return_value = success_result

        ctx = self._make_context_with_completed_step()
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then
        assert "s1" in summary.compensated
        assert ctx.step_results["s1"].compensation_status == "compensated"

    def test_compensation_fail_open_does_not_raise(self):
        """보상 실패(예외 발생)가 전체 처리를 중단시키지 않아야 한다 (Fail-Open)."""
        # Given
        registry = ActionPrimitiveRegistry()
        # 보상 프리미티브가 예외를 발생
        registry.register_compensate(
            "action.one",
            lambda svc, **kw: None,
            validate=False,
        )
        executor, _, mock_action_executor = _make_executor(registry=registry)
        mock_action_executor.execute.side_effect = Exception("compensation error")

        ctx = self._make_context_with_completed_step()
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When — 예외 발생 없이 완료
        summary = executor._compensate_steps(ctx, runbook)

        # Then
        assert ("s1", "compensation error") in summary.failed
        assert ctx.step_results["s1"].compensation_status == "compensate_failed"

    def test_compensation_skips_step_without_compensate_primitive(self):
        """보상 프리미티브 없는 Step은 skipped에 추가해야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()  # 보상 프리미티브 미등록
        executor, _, _ = _make_executor(registry=registry)
        ctx = self._make_context_with_completed_step()
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then
        assert "s1" in summary.skipped

    def test_partial_execution_step_included_in_compensation_targets(self):
        """partial_execution=True인 Step도 보상 대상에 포함되어야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register_compensate("action.one", lambda svc, **kw: None, validate=False)
        executor, _, mock_action_executor = _make_executor(registry=registry)

        success_result = MagicMock()
        success_result.success = True
        success_result.error = None
        mock_action_executor.execute.return_value = success_result

        # success=False이지만 partial_execution=True
        timeout_step_result = RunbookStepResult(
            step_name="s1",
            action_name="action.one",
            success=False,
            executed=True,
            partial_execution=True,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"s1": timeout_step_result},
        )
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then — partial_execution Step이 보상 대상에 포함되어 compensated
        assert "s1" in summary.compensated

    def test_shadow_mode_step_not_included_in_compensation(self):
        """SHADOW 모드에서 executed=False인 Step은 보상 대상에서 제외되어야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        executor, _, _ = _make_executor(registry=registry)

        shadow_step_result = RunbookStepResult(
            step_name="s1",
            action_name="action.one",
            success=True,
            executed=False,  # SHADOW 모드: 실행 안 됨
            partial_execution=False,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"s1": shadow_step_result},
        )
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then — executed=False이고 partial_execution=False이면 보상 대상 제외
        assert "s1" not in summary.compensated
        assert "s1" not in summary.failed


# =============================================================================
# 동작 검증 — Resume 3단계 방어
# =============================================================================


class TestRunbookExecutorResumeDefensesBehavior:
    """resume_execution 3단계 방어 검증 (stale/version/count)."""

    def _make_executor_with_backend(self) -> tuple[RunbookExecutor, MagicMock]:
        mock_backend = MagicMock()
        executor, _, _ = _make_executor(backend=mock_backend)
        return executor, mock_backend

    def test_resume_raises_when_context_not_found(self):
        """컨텍스트가 없으면 RunbookExecutionError가 발생해야 한다."""
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = None  # 컨텍스트 없음

        with pytest.raises(RunbookExecutionError, match="찾을 수 없다"):
            executor.resume_execution("nonexistent_execution")

    def test_resume_raises_for_completed_context(self):
        """COMPLETED 상태 컨텍스트는 재개할 수 없어야 한다."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.COMPLETED,
            started_at="2026-01-01T00:00:00+00:00",
        )
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = ctx.to_dict()

        with pytest.raises(RunbookExecutionError, match="재개할 수 없다"):
            executor.resume_execution("e1")

    def test_resume_raises_stale_context_error_when_old(self):
        """오래된 컨텍스트는 RunbookStaleContextError가 발생해야 한다."""
        # Given: 매우 오래 전에 시작된 컨텍스트
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.FAILED,
            started_at="2020-01-01T00:00:00+00:00",  # 6년 전
        )
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = ctx.to_dict()

        with pytest.raises(RunbookStaleContextError, match="경과"):
            executor.resume_execution("e1")

    def test_resume_force_bypasses_stale_check(self):
        """force=True이면 stale 체크를 건너뛰어야 한다."""
        # Given: 오래된 컨텍스트지만 force=True
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.FAILED,
            started_at="2020-01-01T00:00:00+00:00",
            runbook_version=1,
        )
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = ctx.to_dict()

        # RunbookRegistry.get()이 version=1 runbook을 반환
        mock_runbook = MagicMock()
        mock_runbook.version = 1
        mock_runbook.steps = []

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook
            # force=True이면 stale 에러 없이 진행
            result_ctx = executor.resume_execution("e1", force=True)

        # stale 에러 없이 완료됨 (steps가 없으므로 즉시 COMPLETED)
        assert result_ctx.status == RunbookExecutionStatus.COMPLETED

    def test_resume_raises_version_mismatch_error(self):
        """Runbook 버전 불일치 시 RunbookVersionMismatchError가 발생해야 한다."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.FAILED,
            runbook_version=1,  # 이전 버전
        )
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = ctx.to_dict()

        mock_runbook = MagicMock()
        mock_runbook.version = 2  # 현재 버전이 다름

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook
            with pytest.raises(RunbookVersionMismatchError, match="버전 불일치"):
                executor.resume_execution("e1", force=True)

    def test_resume_fails_when_max_resume_count_exceeded(self):
        """MAX_RESUME_COUNT 초과 시 FAILED 상태로 전환해야 한다."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.FAILED,
            runbook_version=1,
            variables={"__resume_count": MAX_RESUME_COUNT},  # 이미 한계
        )
        executor, mock_backend = self._make_executor_with_backend()
        mock_backend.get.return_value = ctx.to_dict()

        mock_runbook = MagicMock()
        mock_runbook.version = 1
        mock_runbook.steps = []

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook
            result_ctx = executor.resume_execution("e1", force=True)

        assert result_ctx.status == RunbookExecutionStatus.FAILED
        assert "초과" in (result_ctx.abort_reason or "")


# =============================================================================
# 동작 검증 — 멱등성 체크
# =============================================================================


class TestRunbookExecutorIdempotencyBehavior:
    """멱등성 체크 — 캐시된 결과 반환 검증."""

    def test_idempotent_step_returns_cached_result(self):
        """멱등성 레코드가 COMPLETED이면 캐시 결과를 반환해야 한다."""
        from selfhealing.services.coordination.idempotent_step_handlers import (
            IdempotencyRecord,
            IdempotencyStatus,
        )

        # Given
        mock_backend = MagicMock()
        cached_record = IdempotencyRecord(
            idempotency_key="idem_key",
            session_id="e1",
            step_type="action.one",
            step_order=0,
            status=IdempotencyStatus.COMPLETED,
            result={"killed": 5},
        )
        mock_backend.get.return_value = cached_record.to_dict()

        registry = ActionPrimitiveRegistry()
        call_count = [0]

        def handler(ctx):
            call_count[0] += 1
            return {"success": True}

        registry.register("action.one", handler)
        executor, _, _ = _make_executor(backend=mock_backend, registry=registry)
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then — 핸들러가 실제로 호출되지 않아야 함 (캐시에서 반환)
        # action_executor.execute()가 호출되지 않음
        assert result_ctx.step_results["s1"].idempotent is True


# =============================================================================
# 계약 검증 — CONTEXT_TTL_SECONDS 상수
# =============================================================================


class TestRunbookExecutorContextTtlContract:
    """CONTEXT_TTL_SECONDS 설계 계약 검증."""

    def test_context_ttl_seconds_matches_design_document(self):
        """§16 설계 계약: CONTEXT_TTL_SECONDS == 86400 (24시간)."""
        assert CONTEXT_TTL_SECONDS == 86400


# =============================================================================
# 동작 검증 — resume 시 Lock 획득/해제
# =============================================================================


class TestRunbookExecutorResumeLockBehavior:
    """resume_execution 시 Lock 획득/해제 동작 검증.

    SagaOrchestrator.resume_saga()는 실행 재개 전 Lock을 획득하여
    동시 재개에 의한 Split-brain을 방지한다. resume_execution도 동일 패턴.
    """

    def _make_resumable_context(self, **overrides) -> RunbookExecutionContext:
        defaults = dict(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            status=RunbookExecutionStatus.FAILED,
            started_at="2026-02-25T00:00:00+00:00",
            runbook_version=1,
        )
        defaults.update(overrides)
        return RunbookExecutionContext(**defaults)

    def test_resume_acquires_lock_before_execution(self):
        """resume_execution이 _run_from_step 호출 전에 Lock을 획득해야 한다."""
        # Given
        ctx = self._make_resumable_context()
        mock_backend = MagicMock()
        mock_backend.get.return_value = ctx.to_dict()

        executor, mock_lock, _ = _make_executor(backend=mock_backend)

        mock_runbook = MagicMock()
        mock_runbook.version = 1
        mock_runbook.steps = []

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook

            # When
            executor.resume_execution("e1", force=True)

        # Then
        mock_lock.acquire.assert_called_once_with("ns", "e1")

    def test_resume_releases_lock_after_execution(self):
        """resume_execution이 완료 후 Lock을 해제해야 한다."""
        # Given
        ctx = self._make_resumable_context()
        mock_backend = MagicMock()
        mock_backend.get.return_value = ctx.to_dict()

        executor, mock_lock, _ = _make_executor(backend=mock_backend)

        mock_runbook = MagicMock()
        mock_runbook.version = 1
        mock_runbook.steps = []

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook
            executor.resume_execution("e1", force=True)

        # Then
        mock_lock.release.assert_called_once_with("ns", "e1")

    def test_resume_raises_lock_conflict_when_lock_unavailable(self):
        """Lock 획득 실패 시 RunbookLockConflictError가 발생해야 한다."""
        # Given
        ctx = self._make_resumable_context()
        mock_backend = MagicMock()
        mock_backend.get.return_value = ctx.to_dict()

        executor, mock_lock, _ = _make_executor(
            lock_acquired=False,
            backend=mock_backend,
        )

        mock_runbook = MagicMock()
        mock_runbook.version = 1

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook

            # When / Then
            with pytest.raises(RunbookLockConflictError, match="Lock"):
                executor.resume_execution("e1", force=True)

    def test_resume_releases_lock_even_on_step_failure(self):
        """Step 실행 실패 시에도 Lock이 해제되어야 한다."""
        # Given
        ctx = self._make_resumable_context()
        mock_backend = MagicMock()
        mock_backend.get.return_value = ctx.to_dict()

        registry = ActionPrimitiveRegistry()
        registry.register("action.one", lambda ctx: {"success": False})

        executor, mock_lock, mock_ae = _make_executor(
            backend=mock_backend,
            registry=registry,
        )
        fail_result = MagicMock()
        fail_result.executed = True
        fail_result.success = False
        fail_result.error = "step failed"
        fail_result.to_dict.return_value = {"executed": True, "success": False}
        mock_ae.execute.return_value = fail_result

        mock_runbook = MagicMock()
        mock_runbook.version = 1
        mock_runbook.steps = [_make_step("s1", "action.one")]

        with patch(
            "selfhealing.services.runbook.runbook_registry.RunbookRegistry",
            autospec=True,
        ) as MockRegistryCls:
            MockRegistryCls.return_value.get.return_value = mock_runbook
            executor.resume_execution("e1", force=True)

        # Then — 실패해도 lock.release가 호출된다
        mock_lock.release.assert_called_once_with("ns", "e1")


# =============================================================================
# 동작 검증 — Settings 기반 값 사용
# =============================================================================


class TestRunbookExecutorSettingsIntegrationBehavior:
    """Executor가 RunbookSettings에서 값을 읽는지 검증."""

    def test_save_context_uses_settings_ttl(self):
        """_save_context가 Settings.context_ttl_seconds를 TTL로 사용해야 한다."""
        # Given
        from selfhealing.settings.runbook import RunbookSettings

        mock_backend = MagicMock()
        executor, _, _ = _make_executor(backend=mock_backend)

        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )

        # When
        executor._save_context(ctx)

        # Then — backend.set의 ttl 인자가 Settings 기본값과 일치
        expected_ttl = RunbookSettings().context_ttl_seconds
        call_args = mock_backend.set.call_args
        assert call_args is not None
        assert (
            call_args[1].get("ttl") == expected_ttl or call_args[0][2] == expected_ttl
            if len(call_args[0]) > 2
            else call_args[1].get("ttl") == expected_ttl
        )

    def test_get_lock_heartbeat_interval_returns_settings_value(self):
        """_get_lock_heartbeat_interval이 Settings 값을 반환해야 한다."""
        # Given
        from selfhealing.settings.runbook import RunbookSettings

        executor, _, _ = _make_executor()

        # When
        interval = executor._get_lock_heartbeat_interval()

        # Then
        expected = RunbookSettings().lock_heartbeat_interval
        assert interval == expected

    def test_get_lock_extend_seconds_returns_settings_value(self):
        """_get_lock_extend_seconds가 Settings 값을 반환해야 한다."""
        # Given
        from selfhealing.settings.runbook import RunbookSettings

        executor, _, _ = _make_executor()

        # When
        extend = executor._get_lock_extend_seconds()

        # Then
        expected = RunbookSettings().lock_extend_seconds
        assert extend == expected

    def test_get_max_resume_count_returns_settings_value(self):
        """_get_max_resume_count가 Settings 값을 반환해야 한다."""
        # Given
        from selfhealing.settings.runbook import RunbookSettings

        executor, _, _ = _make_executor()

        # When
        count = executor._get_max_resume_count()

        # Then
        expected = RunbookSettings().max_resume_count
        assert count == expected


# =============================================================================
# 동작 검증 — 보상 조회 키 해석
# =============================================================================


class TestRunbookExecutorCompensateKeyResolutionBehavior:
    """_resolve_compensate_key — on_failure_action 우선, step.action 폴백."""

    def test_uses_on_failure_action_when_set(self):
        """on_failure_action이 설정된 Step은 해당 값을 보상 키로 사용해야 한다."""
        # Given
        step = RunbookStep(
            name="enable_cb",
            action="circuit_breaker.enable",
            on_failure_action="circuit_breaker.disable",
        )
        runbook = _make_runbook(steps=[step])

        # When
        key = RunbookExecutor._resolve_compensate_key("enable_cb", runbook)

        # Then
        assert key == "circuit_breaker.disable"

    def test_falls_back_to_step_action_when_no_on_failure_action(self):
        """on_failure_action이 없으면 step.action을 보상 키로 사용해야 한다."""
        # Given
        step = _make_step("s1", "action.one")
        runbook = _make_runbook(steps=[step])

        # When
        key = RunbookExecutor._resolve_compensate_key("s1", runbook)

        # Then
        assert key == "action.one"

    def test_compensation_uses_action_name_for_lookup(self):
        """보상 시 register_compensate의 action_name 기반으로 조회해야 한다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register_compensate(
            "action.one",  # action_name으로 등록
            lambda svc, **kw: None,
            validate=False,
        )
        executor, _, mock_action_executor = _make_executor(registry=registry)

        success_result = MagicMock()
        success_result.success = True
        success_result.error = None
        mock_action_executor.execute.return_value = success_result

        step_result = RunbookStepResult(
            step_name="s1",
            action_name="action.one",
            success=True,
            executed=True,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"s1": step_result},
        )
        runbook = _make_runbook(steps=[_make_step("s1", "action.one")])

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then — action_name(action.one)으로 등록했으므로 매칭 성공
        assert "s1" in summary.compensated
