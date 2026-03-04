"""
297번 Executor Enhancements 단위 테스트.

커밋 7007156b에서 추가된 3가지 기능에 대한 테스트:
    1. continue_on_failure: 검증 게이트 실패 시 abort 없이 다음 Step 진행
    2. poll_interval_seconds: wait.stabilize에서 주기적 메트릭 확인 + Fail-Fast
    3. labels fail-fast: 미해석 ${} 템플릿 변수를 가진 라벨 즉시 거부

테스트 대상:
    selfhealing.services.runbook.executor (continue_on_failure, compensation 제외)
    selfhealing.services.runbook.primitives (poll_interval, labels fail-fast, neq)
    selfhealing.services.runbook.runbook_registry (validate() warnings, STATE_CHANGING_ACTIONS)

계약 검증 클래스 (Test*Contract):
    - TestRunbookStepContinueOnFailureContract
    - TestRunbookStateChangingActionsContract
    - TestAssertMetricParamsLabelsContract
    - TestWaitStabilizeParamsPollIntervalContract
    - TestNeqOperatorContract

동작 검증 클래스 (Test*Behavior):
    - TestContinueOnFailureExecutorBehavior
    - TestContinueOnFailureCompensationBehavior
    - TestRunbookValidateWarningsBehavior
    - TestRunbookRegistryWarningLoggingBehavior
    - TestAssertMetricLabelsFailFastBehavior
    - TestWaitStabilizeLabelsFailFastBehavior
    - TestWaitStabilizePollIntervalBehavior
    - TestQueryMetricLabelsPassthroughBehavior
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from selfhealing.services.runbook.execution_models import (
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.executor import RunbookExecutor
from selfhealing.services.runbook.models import PatternCondition
from selfhealing.services.runbook.primitives import (
    _OPERATOR_MAP,
    AssertMetricParams,
    WaitStabilizeParams,
    _handle_assert_metric,
    _handle_wait_stabilize,
)
from selfhealing.services.runbook.runbook_registry import (
    ActionPrimitiveRegistry,
    Runbook,
    RunbookRegistry,
    RunbookStep,
    RunbookStepContext,
)

# =============================================================================
# 테스트 헬퍼
# =============================================================================


def _make_step(
    name: str,
    action: str,
    order: int = 0,
    continue_on_failure: bool = False,
    params: dict | None = None,
    on_failure_params: dict | None = None,
    timeout_seconds: int = 120,
) -> RunbookStep:
    return RunbookStep(
        name=name,
        action=action,
        order=order,
        continue_on_failure=continue_on_failure,
        params=params or {},
        on_failure_params=on_failure_params or {},
        timeout_seconds=timeout_seconds,
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


def _make_ctx(
    params: dict | None = None,
    runbook_id: str = "rb_test",
    step_name: str = "step_1",
    initiated_by: str = "tester",
) -> RunbookStepContext:
    """최소 구성의 RunbookStepContext 생성."""
    return RunbookStepContext(
        runbook_id=runbook_id,
        step_name=step_name,
        params=params or {},
        prev_results={},
        execution_id="exec_001",
        initiated_by=initiated_by,
    )


def _make_registry(
    event_bus: Any = None,
    state_backend: Any = None,
    primitive_registry: Any = None,
    cached_enabled_map: dict[str, bool] | None = None,
) -> RunbookRegistry:
    """의존성 없는 RunbookRegistry 생성 (지연 로드 방지)."""
    registry = RunbookRegistry.__new__(RunbookRegistry)
    registry._runbooks = {}
    registry._lock = threading.Lock()
    registry._event_bus = event_bus
    registry._state_backend = state_backend
    registry._primitive_registry = primitive_registry
    registry._cached_enabled_map = cached_enabled_map or {}
    return registry


# =============================================================================
# 계약 검증 — RunbookStep.continue_on_failure 기본값
# =============================================================================


class TestRunbookStepContinueOnFailureContract:
    """RunbookStep.continue_on_failure 설계 계약값 검증 (297 §2)."""

    def test_continue_on_failure_default_is_false(self):
        """continue_on_failure 기본값은 False (abort가 기본 동작)."""
        step = RunbookStep(name="s", action="a")
        assert step.continue_on_failure is False

    def test_continue_on_failure_can_be_set_to_true(self):
        """continue_on_failure=True로 명시 설정 가능."""
        step = RunbookStep(name="s", action="a", continue_on_failure=True)
        assert step.continue_on_failure is True


# =============================================================================
# 계약 검증 — Runbook.STATE_CHANGING_ACTIONS
# =============================================================================


class TestRunbookStateChangingActionsContract:
    """Runbook.STATE_CHANGING_ACTIONS 설계 계약 검증 (297 §2.5)."""

    def test_state_changing_actions_is_frozenset(self):
        """STATE_CHANGING_ACTIONS는 frozenset 타입."""
        assert isinstance(Runbook.STATE_CHANGING_ACTIONS, frozenset)

    def test_state_changing_actions_contains_config_set(self):
        """config.set은 상태 변경 action."""
        assert "config.set" in Runbook.STATE_CHANGING_ACTIONS

    def test_state_changing_actions_contains_recovery_start(self):
        """recovery.start는 상태 변경 action."""
        assert "recovery.start" in Runbook.STATE_CHANGING_ACTIONS

    def test_state_changing_actions_contains_emergency_activate(self):
        """emergency.activate는 상태 변경 action."""
        assert "emergency.activate" in Runbook.STATE_CHANGING_ACTIONS

    def test_state_changing_actions_contains_emergency_deactivate(self):
        """emergency.deactivate는 상태 변경 action."""
        assert "emergency.deactivate" in Runbook.STATE_CHANGING_ACTIONS

    def test_state_changing_actions_count_is_four(self):
        """상태 변경 action은 4개."""
        assert len(Runbook.STATE_CHANGING_ACTIONS) == 4

    def test_assert_metric_is_not_state_changing(self):
        """assert.metric은 검증 게이트이므로 상태 변경 action이 아니다."""
        assert "assert.metric" not in Runbook.STATE_CHANGING_ACTIONS

    def test_wait_stabilize_is_not_state_changing(self):
        """wait.stabilize는 대기 프리미티브이므로 상태 변경 action이 아니다."""
        assert "wait.stabilize" not in Runbook.STATE_CHANGING_ACTIONS


# =============================================================================
# 계약 검증 — AssertMetricParams.labels 필드
# =============================================================================


class TestAssertMetricParamsLabelsContract:
    """AssertMetricParams.labels 설계 계약값 검증 (297 §4.2)."""

    def test_labels_default_is_none(self):
        """labels 기본값은 None (Global 스코프)."""
        p = AssertMetricParams(metric_name="m", operator="gt", threshold=1.0)
        assert p.labels is None

    def test_labels_accepts_dict(self):
        """labels는 dict[str, str]을 허용한다."""
        p = AssertMetricParams(
            metric_name="m",
            operator="gt",
            threshold=1.0,
            labels={"service": "payments"},
        )
        assert p.labels == {"service": "payments"}

    def test_neq_operator_is_valid(self):
        """operator 'neq'가 유효하다 (297에서 추가)."""
        p = AssertMetricParams(metric_name="m", operator="neq", threshold=0.0)
        assert p.operator == "neq"


# =============================================================================
# 계약 검증 — WaitStabilizeParams.poll_interval_seconds 필드
# =============================================================================


class TestWaitStabilizeParamsPollIntervalContract:
    """WaitStabilizeParams.poll_interval_seconds 설계 계약값 검증 (297 §3)."""

    def test_poll_interval_seconds_default_is_zero(self):
        """poll_interval_seconds 기본값은 0 (비폴링 모드)."""
        p = WaitStabilizeParams(seconds=10)
        assert p.poll_interval_seconds == 0

    def test_poll_interval_seconds_ge_zero(self):
        """poll_interval_seconds는 0 이상이어야 한다."""
        field_info = WaitStabilizeParams.model_fields["poll_interval_seconds"]
        ge_val = None
        for m in field_info.metadata:
            if hasattr(m, "ge"):
                ge_val = m.ge
        assert ge_val == 0

    def test_poll_interval_seconds_le_300(self):
        """poll_interval_seconds는 300 이하여야 한다."""
        field_info = WaitStabilizeParams.model_fields["poll_interval_seconds"]
        le_val = None
        for m in field_info.metadata:
            if hasattr(m, "le"):
                le_val = m.le
        assert le_val == 300

    def test_labels_default_is_none(self):
        """WaitStabilizeParams.labels 기본값은 None."""
        p = WaitStabilizeParams(seconds=10)
        assert p.labels is None

    def test_neq_operator_is_valid_in_wait_stabilize(self):
        """WaitStabilizeParams에서도 'neq' operator가 유효하다."""
        p = WaitStabilizeParams(seconds=10, operator="neq")
        assert p.operator == "neq"


# =============================================================================
# 계약 검증 — _OPERATOR_MAP neq
# =============================================================================


class TestNeqOperatorContract:
    """_OPERATOR_MAP 'neq' 연산자 계약 검증 (297에서 추가)."""

    def test_neq_operator_exists_in_map(self):
        """_OPERATOR_MAP에 'neq' 키가 존재한다."""
        assert "neq" in _OPERATOR_MAP

    def test_neq_operator_evaluates_correctly(self):
        """neq 연산자는 a != b."""
        assert _OPERATOR_MAP["neq"](5, 6) is True
        assert _OPERATOR_MAP["neq"](5, 5) is False

    def test_operator_map_has_six_operators(self):
        """_OPERATOR_MAP은 6개 연산자를 갖는다 (gt/lt/eq/neq/gte/lte)."""
        assert len(_OPERATOR_MAP) == 6


# =============================================================================
# 동작 검증 — continue_on_failure Executor 동작
# =============================================================================


class TestContinueOnFailureExecutorBehavior:
    """continue_on_failure=True인 Step 실패 시 abort 없이 다음 Step 진행 검증 (297 §2.3)."""

    def test_continue_on_failure_step_does_not_abort_pipeline(self):
        """continue_on_failure=True인 Step이 실패해도 다음 Step이 실행된다."""
        # Given
        execution_order: list[str] = []
        registry = ActionPrimitiveRegistry()

        def make_handler(step_name: str, success: bool):
            def handler(ctx):
                execution_order.append(step_name)
                if success:
                    return {"success": True}
                return {"success": False, "error": "check failed"}

            return handler

        registry.register("assert.check", make_handler("check", False))
        registry.register("action.recover", make_handler("recover", True))

        executor, mock_lock, mock_ae = _make_executor(registry=registry)

        # action_executor가 execute_fn을 실제로 호출하도록 설정
        call_idx = [0]
        results = []

        def ae_side_effect(action):
            action.execute_fn()
            idx = call_idx[0]
            call_idx[0] += 1
            if idx == 0:
                # 첫 번째 Step(check) — 실패
                r = MagicMock()
                r.executed = True
                r.success = False
                r.error = "check failed"
                r.idempotent = False
                r.to_dict.return_value = {"executed": True, "success": False}
                return r
            else:
                # 두 번째 Step(recover) — 성공
                r = MagicMock()
                r.executed = True
                r.success = True
                r.error = None
                r.to_dict.return_value = {"executed": True, "success": True}
                return r

        mock_ae.execute.side_effect = ae_side_effect

        runbook = _make_runbook(
            steps=[
                _make_step("check", "assert.check", order=1, continue_on_failure=True),
                _make_step("recover", "action.recover", order=2),
            ]
        )

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then — 두 Step 모두 실행됨
        assert "check" in execution_order
        assert "recover" in execution_order
        assert result_ctx.status == RunbookExecutionStatus.COMPLETED

    def test_normal_step_failure_aborts_pipeline(self):
        """continue_on_failure=False(기본)인 Step 실패 시 파이프라인이 중단된다."""
        # Given
        execution_order: list[str] = []
        registry = ActionPrimitiveRegistry()

        def make_handler(step_name: str):
            def handler(ctx):
                execution_order.append(step_name)
                return {"success": False, "error": "fail"}

            return handler

        registry.register("action.fail", make_handler("fail"))
        registry.register("action.next", lambda ctx: {"success": True})

        executor, _, mock_ae = _make_executor(registry=registry)

        fail_result = MagicMock()
        fail_result.executed = True
        fail_result.success = False
        fail_result.error = "fail"
        fail_result.idempotent = False
        fail_result.to_dict.return_value = {"executed": True, "success": False}
        mock_ae.execute.return_value = fail_result

        runbook = _make_runbook(
            steps=[
                _make_step(
                    "fail_step", "action.fail", order=1, continue_on_failure=False
                ),
                _make_step("next_step", "action.next", order=2),
            ]
        )

        # When
        result_ctx = executor.execute_runbook(runbook, {}, "ns")

        # Then — 실패 후 파이프라인 중단, next_step 실행 안 됨
        assert result_ctx.status == RunbookExecutionStatus.FAILED
        assert "next_step" not in result_ctx.step_results


# =============================================================================
# 동작 검증 — continue_on_failure 보상 제외
# =============================================================================


class TestContinueOnFailureCompensationBehavior:
    """continue_on_failure=True로 실패한 Step은 보상 대상에서 제외 (297 §2.4)."""

    def test_continue_on_failure_failed_step_excluded_from_compensation(self):
        """continue_on_failure=True로 실패한 Step은 보상 대상에서 제외된다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register_compensate(
            "assert.check", lambda svc, **kw: None, validate=False
        )
        executor, _, mock_ae = _make_executor(registry=registry)

        success_result = MagicMock()
        success_result.success = True
        success_result.error = None
        mock_ae.execute.return_value = success_result

        # continue_on_failure=True인 Step이 실패한 상태
        failed_check = RunbookStepResult(
            step_name="check",
            action_name="assert.check",
            success=False,
            executed=True,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"check": failed_check},
        )
        runbook = _make_runbook(
            steps=[_make_step("check", "assert.check", continue_on_failure=True)]
        )

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then — 보상 대상에서 제외 (compensated에도 failed에도 없어야 함)
        assert "check" not in summary.compensated
        assert all(name != "check" for name, _ in summary.failed)

    def test_normal_failed_step_is_not_excluded_from_compensation(self):
        """continue_on_failure=False인 실패 Step은 보상 대상 필터링 되지 않는다."""
        # Given
        registry = ActionPrimitiveRegistry()
        registry.register_compensate(
            "action.one", lambda svc, **kw: None, validate=False
        )
        executor, _, mock_ae = _make_executor(registry=registry)

        success_result = MagicMock()
        success_result.success = True
        success_result.error = None
        mock_ae.execute.return_value = success_result

        # partial_execution=True인 일반 실패 Step (보상 대상)
        failed_step = RunbookStepResult(
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
            step_results={"s1": failed_step},
        )
        runbook = _make_runbook(
            steps=[_make_step("s1", "action.one", continue_on_failure=False)]
        )

        # When
        summary = executor._compensate_steps(ctx, runbook)

        # Then — partial_execution=True이므로 보상 대상
        assert "s1" in summary.compensated

    def test_get_continue_on_failure_step_names_returns_correct_set(self):
        """_get_continue_on_failure_step_names는 continue_on_failure=True Step 이름만 반환."""
        runbook = _make_runbook(
            steps=[
                _make_step("check_health", "assert.metric", continue_on_failure=True),
                _make_step("do_recovery", "recovery.start", continue_on_failure=False),
                _make_step("verify", "assert.metric", continue_on_failure=True),
            ]
        )

        result = RunbookExecutor._get_continue_on_failure_step_names(runbook)

        assert result == {"check_health", "verify"}

    def test_get_continue_on_failure_step_names_empty_when_none_set(self):
        """모든 Step이 continue_on_failure=False이면 빈 set 반환."""
        runbook = _make_runbook(
            steps=[
                _make_step("s1", "action.one"),
                _make_step("s2", "action.two"),
            ]
        )

        result = RunbookExecutor._get_continue_on_failure_step_names(runbook)

        assert result == set()


# =============================================================================
# 동작 검증 — Runbook.validate() warnings
# =============================================================================


class TestRunbookValidateWarningsBehavior:
    """Runbook.validate()가 continue_on_failure + 상태 변경 action에 대해 경고 (297 §2.5)."""

    def test_validate_returns_three_tuple(self):
        """validate()는 (valid, error, warnings) 3-tuple을 반환한다."""
        rb = _make_runbook()
        result = rb.validate()
        assert len(result) == 3

    def test_valid_runbook_returns_empty_warnings(self):
        """유효한 런북의 경고 목록은 비어 있다."""
        rb = _make_runbook(
            steps=[_make_step("s1", "assert.metric", continue_on_failure=True)]
        )
        valid, error, warnings = rb.validate()
        assert valid is True
        assert error == ""
        assert warnings == []

    def test_continue_on_failure_on_state_changing_action_produces_warning(self):
        """continue_on_failure=True + 상태 변경 action은 경고를 발생시킨다."""
        rb = _make_runbook(
            steps=[
                _make_step("dangerous", "config.set", continue_on_failure=True),
            ]
        )
        valid, error, warnings = rb.validate()

        assert valid is True
        assert len(warnings) == 1
        assert "dangerous" in warnings[0]
        assert "config.set" in warnings[0]
        assert "continue_on_failure" in warnings[0]

    def test_continue_on_failure_on_non_state_changing_action_no_warning(self):
        """continue_on_failure=True + 검증 action(assert.metric)은 경고 없음."""
        rb = _make_runbook(
            steps=[
                _make_step("check", "assert.metric", continue_on_failure=True),
            ]
        )
        _, _, warnings = rb.validate()
        assert warnings == []

    def test_multiple_state_changing_steps_produce_multiple_warnings(self):
        """여러 상태 변경 Step에 continue_on_failure=True면 각각 경고."""
        rb = _make_runbook(
            steps=[
                _make_step("s1", "config.set", continue_on_failure=True),
                _make_step("s2", "recovery.start", continue_on_failure=True),
                _make_step("s3", "assert.metric", continue_on_failure=True),
            ]
        )
        _, _, warnings = rb.validate()
        assert len(warnings) == 2  # s1, s2만 경고 (s3은 검증 게이트)

    def test_continue_on_failure_false_on_state_changing_no_warning(self):
        """continue_on_failure=False이면 상태 변경 action이어도 경고 없음."""
        rb = _make_runbook(
            steps=[
                _make_step("s1", "config.set", continue_on_failure=False),
            ]
        )
        _, _, warnings = rb.validate()
        assert warnings == []

    def test_invalid_runbook_returns_empty_warnings(self):
        """무효한 런북(빈 steps)은 경고 목록이 비어 있다."""
        from selfhealing.services.runbook.models import PatternCondition

        rb = Runbook(
            id="rb_empty",
            name="Empty",
            description="",
            trigger_condition=PatternCondition(),
            steps=[],
        )
        valid, error, warnings = rb.validate()
        assert valid is False
        assert warnings == []


# =============================================================================
# 동작 검증 — RunbookRegistry.register() 경고 로깅
# =============================================================================


class TestRunbookRegistryWarningLoggingBehavior:
    """RunbookRegistry.register()가 validate() 경고를 logger.warning으로 기록 (297 §2.5)."""

    def test_register_logs_warnings_from_validate(self):
        """validate()가 반환한 warnings를 logger.warning으로 기록한다."""
        registry = _make_registry()
        rb = _make_runbook(
            steps=[
                _make_step("danger", "config.set", continue_on_failure=True),
            ]
        )

        with patch(
            "selfhealing.services.runbook.runbook_registry.logger"
        ) as mock_logger:
            registry.register(rb)
            mock_logger.warning.assert_called()
            call_kwargs = mock_logger.warning.call_args
            assert "validation_warning" in str(call_kwargs)

    def test_register_does_not_log_when_no_warnings(self):
        """경고 없는 런북 등록 시 warning 로그가 남지 않는다."""
        registry = _make_registry()
        rb = _make_runbook(
            steps=[_make_step("safe", "assert.metric", continue_on_failure=True)]
        )

        with patch(
            "selfhealing.services.runbook.runbook_registry.logger"
        ) as mock_logger:
            registry.register(rb)
            # warning이 호출되지 않아야 함
            for call in mock_logger.warning.call_args_list:
                assert "validation_warning" not in str(call)


# =============================================================================
# 동작 검증 — assert.metric labels fail-fast
# =============================================================================


class TestAssertMetricLabelsFailFastBehavior:
    """assert.metric에서 미해석 라벨 감지 시 즉시 실패 (297 §4.2)."""

    def test_unresolved_template_label_returns_failed(self):
        """${service}처럼 미해석 템플릿 변수는 LABEL_NOT_RESOLVED 실패."""
        ctx = _make_ctx(
            params={
                "metric_name": "error_rate",
                "operator": "lt",
                "threshold": 0.05,
                "labels": {"service": "${service_name}"},
            }
        )
        result = _handle_assert_metric(ctx)

        assert result.success is False
        assert result.error_code == "LABEL_NOT_RESOLVED"
        assert result.retryable is False
        assert "service" in result.error

    def test_empty_label_value_returns_failed(self):
        """빈 문자열 라벨 값도 LABEL_NOT_RESOLVED 실패."""
        ctx = _make_ctx(
            params={
                "metric_name": "error_rate",
                "operator": "lt",
                "threshold": 0.05,
                "labels": {"service": ""},
            }
        )
        result = _handle_assert_metric(ctx)

        assert result.success is False
        assert result.error_code == "LABEL_NOT_RESOLVED"

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    def test_resolved_labels_passed_to_query_metric(self, mock_query):
        """정상 라벨은 _query_metric에 전달된다."""
        mock_query.return_value = 0.01

        ctx = _make_ctx(
            params={
                "metric_name": "error_rate",
                "operator": "lt",
                "threshold": 0.05,
                "labels": {"service": "payments"},
            }
        )
        result = _handle_assert_metric(ctx)

        assert result.success is True
        mock_query.assert_called_once_with("error_rate", labels={"service": "payments"})

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    def test_none_labels_skips_fail_fast_check(self, mock_query):
        """labels=None이면 fail-fast 검증을 건너뛴다."""
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={
                "metric_name": "cpu_usage",
                "operator": "gt",
                "threshold": 50.0,
            }
        )
        result = _handle_assert_metric(ctx)

        assert result.success is True
        mock_query.assert_called_once_with("cpu_usage", labels=None)


# =============================================================================
# 동작 검증 — wait.stabilize labels fail-fast
# =============================================================================


class TestWaitStabilizeLabelsFailFastBehavior:
    """wait.stabilize에서 미해석 라벨 감지 시 즉시 실패 (297 §4.3)."""

    def test_unresolved_template_label_returns_failed(self):
        """${var}처럼 미해석 템플릿 변수는 LABEL_NOT_RESOLVED 실패."""
        ctx = _make_ctx(
            params={
                "seconds": 10,
                "assert_metric": "health",
                "threshold": 0.9,
                "labels": {"namespace": "${namespace}"},
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "LABEL_NOT_RESOLVED"
        assert result.retryable is False

    def test_empty_label_value_returns_failed(self):
        """빈 문자열 라벨 값도 LABEL_NOT_RESOLVED 실패."""
        ctx = _make_ctx(
            params={
                "seconds": 10,
                "labels": {"service": ""},
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "LABEL_NOT_RESOLVED"

    @patch("time.sleep")
    def test_resolved_labels_no_fail_fast(self, mock_sleep):
        """정상 라벨은 fail-fast 없이 진행."""
        ctx = _make_ctx(
            params={
                "seconds": 5,
                "labels": {"service": "orders"},
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True


# =============================================================================
# 동작 검증 — wait.stabilize poll_interval_seconds
# =============================================================================


class TestWaitStabilizePollIntervalBehavior:
    """wait.stabilize poll_interval_seconds Polling 모드 동작 검증 (297 §3.3)."""

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_checks_metric_periodically(self, mock_sleep, mock_query):
        """poll_interval_seconds > 0이면 주기적으로 메트릭을 확인한다."""
        # 모든 확인에서 정상값 반환
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={
                "seconds": 6,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True
        # 6초 / 2초 간격 = 3번 sleep + 최종 검증 1번 = 총 4번 query
        assert mock_query.call_count == 4  # 3 poll + 1 final

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_fail_fast_on_violation(self, mock_sleep, mock_query):
        """poll 도중 메트릭이 조건을 위반하면 즉시 WAIT_STABILIZE_FAIL_FAST."""
        # 첫 번째 poll에서 위반
        mock_query.return_value = 50.0

        ctx = _make_ctx(
            params={
                "seconds": 10,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_STABILIZE_FAIL_FAST"
        assert result.data["fail_fast"] is True
        assert result.data["metric_value"] == 50.0

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_fail_fast_includes_elapsed_info(self, mock_sleep, mock_query):
        """Fail-Fast 시 elapsed/total 정보가 결과 data에 포함된다."""
        mock_query.return_value = 70.0  # threshold 80.0 미만

        ctx = _make_ctx(
            params={
                "seconds": 10,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 3,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_STABILIZE_FAIL_FAST"
        assert result.data["waited_seconds"] == 3
        assert result.data["total_seconds"] == 10

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_fail_fast_stops_early(self, mock_sleep, mock_query):
        """poll 도중 위반 시 전체 대기 시간을 채우지 않고 조기 종료된다."""
        mock_query.return_value = 70.0  # threshold 80.0 미만

        ctx = _make_ctx(
            params={
                "seconds": 10,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 3,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        # 전체 10초를 기다리지 않고 3초 만에 종료됨을 sleep 호출 횟수로 검증
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(3)

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_passes_after_full_wait(self, mock_sleep, mock_query):
        """전체 대기 완료 후 최종 검증 통과 시 성공."""
        mock_query.return_value = 90.0

        ctx = _make_ctx(
            params={
                "seconds": 4,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_final_check_fails(self, mock_sleep, mock_query):
        """poll 중에는 통과했지만 최종 검증에서 실패하면 WAIT_STABILIZE_FAILED."""
        # poll 중에는 통과, 마지막에 실패
        call_count = [0]

        def side_effect(metric_name, labels=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                return 90.0  # poll 중 통과
            return 70.0  # 최종 검증 실패

        mock_query.side_effect = side_effect

        ctx = _make_ctx(
            params={
                "seconds": 4,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_STABILIZE_FAILED"

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_none_metric_does_not_trigger_fail_fast(
        self, mock_sleep, mock_query
    ):
        """poll 중 메트릭이 None이면 fail-fast를 트리거하지 않는다."""
        # poll 중 None, 최종에서 정상값
        call_count = [0]

        def side_effect(metric_name, labels=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                return None  # 메트릭 미발견
            return 90.0  # 최종 정상

        mock_query.side_effect = side_effect

        ctx = _make_ctx(
            params={
                "seconds": 4,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_zero_poll_interval_uses_legacy_sleep_mode(self, mock_sleep, mock_query):
        """poll_interval_seconds=0이면 기존 동작(전체 sleep + 사후 검증) 사용."""
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={
                "seconds": 5,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 0,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True
        # 전체 sleep 1번 + query 1번
        mock_sleep.assert_called_once_with(5)
        mock_query.assert_called_once()

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_poll_mode_with_labels_passes_labels_to_query(self, mock_sleep, mock_query):
        """Polling 모드에서 labels가 _query_metric에 전달된다."""
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={
                "seconds": 2,
                "assert_metric": "health",
                "operator": "gte",
                "threshold": 80.0,
                "poll_interval_seconds": 2,
                "labels": {"service": "payments"},
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True
        # 모든 _query_metric 호출에 labels가 전달되었는지 확인
        for call in mock_query.call_args_list:
            assert call.kwargs.get("labels") == {"service": "payments"} or call[1].get(
                "labels"
            ) == {"service": "payments"}

    def test_poll_interval_above_max_raises_validation_error(self):
        """poll_interval_seconds=301은 ValidationError."""
        with pytest.raises(ValidationError):
            WaitStabilizeParams(seconds=10, poll_interval_seconds=301)

    def test_poll_interval_negative_raises_validation_error(self):
        """poll_interval_seconds=-1은 ValidationError."""
        with pytest.raises(ValidationError):
            WaitStabilizeParams(seconds=10, poll_interval_seconds=-1)


# =============================================================================
# 동작 검증 — _query_metric labels passthrough
# =============================================================================


class TestQueryMetricLabelsPassthroughBehavior:
    """_query_metric가 labels를 provider.query에 전달하는지 검증."""

    @patch("selfhealing.factory.ProviderRegistry")
    def test_query_metric_passes_labels_to_provider(self, mock_registry_cls):
        """labels 인자가 provider.query()에 전달된다."""
        from selfhealing.services.runbook.primitives import _query_metric

        mock_provider = MagicMock()
        mock_provider.query.return_value = 42.0
        mock_registry_cls.get.return_value = mock_provider

        result = _query_metric("test_metric", labels={"env": "prod"})

        assert result == 42.0
        mock_provider.query.assert_called_once_with(
            "test_metric", labels={"env": "prod"}
        )

    @patch("selfhealing.factory.ProviderRegistry")
    def test_query_metric_passes_none_labels_to_provider(self, mock_registry_cls):
        """labels=None도 provider.query()에 전달된다."""
        from selfhealing.services.runbook.primitives import _query_metric

        mock_provider = MagicMock()
        mock_provider.query.return_value = 10.0
        mock_registry_cls.get.return_value = mock_provider

        result = _query_metric("test_metric", labels=None)

        assert result == 10.0
        mock_provider.query.assert_called_once_with("test_metric", labels=None)
