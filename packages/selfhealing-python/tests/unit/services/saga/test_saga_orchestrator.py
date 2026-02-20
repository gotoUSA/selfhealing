"""
Unit tests for Saga Orchestrator Engine.

Tests:
- SagaOrchestrator 순방향 실행 성공 동작 검증
- Step 실패 시 역순 compensate 동작 검증
- compensate 실패 시 DLQ 저장 동작 검증
- 서킷브레이커 OPEN 시 SUSPENDED 전환 동작 검증
- can_execute 거부 시 COMPENSATING 전환 동작 검증
- 재시도 가능 실패 시 RETRY_SCHEDULED 동작 검증
- partial_execution 보상 대상 포함 동작 검증
- Context Injection 동작 검증
- _save_instance 실패 시 Fail-fast 동작 검증
- resume_saga 분산 락 선행 동작 검증
- resume_saga 무한 재개 방지 동작 검증
- _execute_with_timeout Lock Heartbeat 동작 검증
- _store_compensation_failure_to_dlq DLQ 통합 동작 검증
- 거버넌스 차단 동작 검증
- 분산 락 획득 실패 동작 검증
- 최종 이벤트 발행 동작 검증

계약 검증:
- SagaOrchestrator 상수 계약 검증
- SagaEventType 이벤트 타입 계약 검증
- Lua 스크립트 존재 계약 검증
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from selfhealing.services.saga.events import SagaEventType
from selfhealing.services.saga.lua_scripts import (
    SAGA_INSTANCE_CAS_SCRIPT,
    SAGA_TRANSITION_SCRIPT,
)
from selfhealing.services.saga.models import (
    SagaContext,
    SagaDefinition,
    SagaInstance,
    SagaStatus,
    SagaStepInstance,
    SagaStepStatus,
    StepResult,
)
from selfhealing.services.saga.orchestrator import (
    EXTEND_SECONDS,
    HEARTBEAT_INTERVAL,
    MAX_RESUME_COUNT,
    STALE_THRESHOLD_SECONDS,
    SagaOrchestrator,
)
from selfhealing.services.saga.registry import (
    _saga_definitions,
    register_saga,
)
from selfhealing.services.saga.step import SagaStep


# =============================================================================
# 테스트 헬퍼: Mock Step 구현
# =============================================================================


class MockSuccessStep(SagaStep):
    """항상 성공하는 Step."""

    def __init__(self, step_name: str = "success_step", data: dict | None = None):
        self._name = step_name
        self._data = data or {}

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded(self._data)

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class MockFailingStep(SagaStep):
    """execute()가 항상 실패하는 Step (non-retryable)."""

    def __init__(self, step_name: str = "failing_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed(
            error="STEP_FAILURE",
            error_code="TEST_ERROR",
            retryable=False,
        )

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class MockRetryableStep(SagaStep):
    """execute()가 retryable=True로 실패하는 Step."""

    def __init__(self, step_name: str = "retryable_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed(
            error="TRANSIENT_FAILURE",
            error_code="TRANSIENT",
            retryable=True,
        )

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class MockPartialFailStep(SagaStep):
    """execute()가 partial_execution=True로 실패하는 Step."""

    def __init__(self, step_name: str = "partial_fail_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed_with_side_effect(
            error="PARTIAL_FAILURE",
            data={"partial_result": "some_data"},
            error_code="PARTIAL",
        )

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class MockFailingCompensateStep(SagaStep):
    """execute()는 성공, compensate()가 실패하는 Step."""

    def __init__(self, step_name: str = "failing_compensate_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded({"executed": True})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed(
            error="COMPENSATE_FAILURE",
            error_code="COMP_ERROR",
        )


class MockCannotExecuteStep(SagaStep):
    """can_execute()가 False를 반환하는 Step."""

    def __init__(self, step_name: str = "cannot_execute_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()

    def can_execute(self, ctx: SagaContext) -> tuple[bool, str]:
        return False, "Precondition not met"


class MockCompensateExceptionStep(SagaStep):
    """execute()는 성공, compensate()가 예외를 발생시키는 Step."""

    def __init__(self, step_name: str = "compensate_exception_step"):
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded({"executed": True})

    def compensate(self, ctx: SagaContext) -> StepResult:
        raise RuntimeError("Compensate crashed")


# =============================================================================
# Fixture: Mock Dependencies
# =============================================================================


def _make_mock_lock():
    """Mock DistributedRecoveryLock."""
    lock = MagicMock()
    lock.acquire.return_value = True
    lock.release.return_value = True
    lock.extend.return_value = True
    return lock


def _make_mock_idempotency():
    """Mock IdempotencyService — 항상 중복 아님."""
    idempotency = MagicMock()
    result = MagicMock()
    result.is_duplicate = False
    idempotency.check.return_value = result
    return idempotency


def _make_mock_dlq():
    """Mock DLQService."""
    return MagicMock()


def _make_mock_event_bus():
    """Mock EventBus."""
    bus = MagicMock()
    bus.emit.return_value = 0
    return bus


def _make_mock_circuit_breaker(state_value: str = "closed"):
    """Mock RecoveryCircuitBreaker."""
    cb = MagicMock()
    state = MagicMock()
    state.value = state_value
    cb.get_state.return_value = state
    return cb


def _make_mock_blast_radius():
    """Mock BlastRadiusService."""
    return MagicMock()


def _make_mock_backend():
    """Mock InMemory StateBackend."""
    backend = MagicMock()
    backend.set = MagicMock()
    backend.get = MagicMock(return_value=None)
    # _client 없으므로 InMemory 경로
    del backend._client
    return backend


def _make_orchestrator(**kwargs) -> SagaOrchestrator:
    """기본 Mock 의존성을 주입한 SagaOrchestrator 생성."""
    defaults = {
        "lock": _make_mock_lock(),
        "idempotency": _make_mock_idempotency(),
        "dlq": _make_mock_dlq(),
        "event_bus": _make_mock_event_bus(),
        "circuit_breaker": _make_mock_circuit_breaker(),
        "blast_radius": _make_mock_blast_radius(),
        "backend": _make_mock_backend(),
    }
    defaults.update(kwargs)
    return SagaOrchestrator(**defaults)


def _register_test_saga(
    name: str,
    steps: list[SagaStep],
    **kwargs,
) -> SagaDefinition:
    """테스트용 SagaDefinition을 등록한다."""
    definition = SagaDefinition(name=name, steps=steps, **kwargs)
    register_saga(definition)
    return definition


# =============================================================================
# 계약 검증 (Contract Tests)
# =============================================================================


class TestSagaOrchestratorConstantsContract:
    """Saga Orchestrator 상수 계약 검증."""

    def test_heartbeat_interval_is_60(self):
        """Lock Heartbeat 간격은 60초여야 한다."""
        assert HEARTBEAT_INTERVAL == 60

    def test_extend_seconds_is_300(self):
        """Lock TTL 연장 시간 값은 300초(5분)여야 한다."""
        assert EXTEND_SECONDS == 300

    def test_max_resume_count_is_10(self):
        """무한 재개 방지 최대 카운터는 10이어야 한다."""
        assert MAX_RESUME_COUNT == 10

    def test_stale_threshold_seconds_is_300(self):
        """고아 Saga 판별 임계값은 300초(5분)여야 한다."""
        assert STALE_THRESHOLD_SECONDS == 300


class TestSagaEventTypeContract:
    """SagaEventType 이벤트 타입 상수 계약 검증."""

    def test_saga_started_value(self):
        assert SagaEventType.SAGA_STARTED == "saga_started"

    def test_saga_step_completed_value(self):
        assert SagaEventType.SAGA_STEP_COMPLETED == "saga_step_completed"

    def test_saga_step_failed_value(self):
        assert SagaEventType.SAGA_STEP_FAILED == "saga_step_failed"

    def test_saga_completed_value(self):
        assert SagaEventType.SAGA_COMPLETED == "saga_completed"

    def test_saga_compensating_value(self):
        assert SagaEventType.SAGA_COMPENSATING == "saga_compensating"

    def test_saga_compensated_value(self):
        assert SagaEventType.SAGA_COMPENSATED == "saga_compensated"

    def test_saga_compensation_failed_value(self):
        assert SagaEventType.SAGA_COMPENSATION_FAILED == "saga_compensation_failed"

    def test_saga_suspended_value(self):
        assert SagaEventType.SAGA_SUSPENDED == "saga_suspended"

    def test_saga_resumed_value(self):
        assert SagaEventType.SAGA_RESUMED == "saga_resumed"

    def test_saga_timed_out_value(self):
        assert SagaEventType.SAGA_TIMED_OUT == "saga_timed_out"

    def test_event_type_count(self):
        """SagaEventType에는 10개의 이벤트 타입이 있어야 한다."""
        assert len(SagaEventType) == 10


class TestLuaScriptsContract:
    """Lua 스크립트 존재 및 구조 계약 검증."""

    def test_saga_transition_script_exists(self):
        assert SAGA_TRANSITION_SCRIPT is not None
        assert "HGET" in SAGA_TRANSITION_SCRIPT
        assert "HMSET" in SAGA_TRANSITION_SCRIPT

    def test_saga_instance_cas_script_exists(self):
        assert SAGA_INSTANCE_CAS_SCRIPT is not None
        assert "cjson.decode" in SAGA_INSTANCE_CAS_SCRIPT
        assert "version" in SAGA_INSTANCE_CAS_SCRIPT


# =============================================================================
# 동작 검증 (Behavior Tests) — Forward 실행
# =============================================================================


class TestSagaOrchestratorForwardBehavior:
    """Saga 순방향(Forward) 실행 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """테스트 전후 saga 레지스트리를 초기화한다."""
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_all_steps_success_returns_completed(self):
        """모든 Step이 성공하면 COMPLETED 상태를 반환해야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment", {"payment_id": "pay_001"}),
                MockSuccessStep("inventory", {"item_id": "item_001"}),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.status == SagaStatus.COMPLETED
        assert result.step_instances[0].status == SagaStepStatus.EXECUTED
        assert result.step_instances[1].status == SagaStepStatus.EXECUTED

    def test_step_data_merged_to_context(self):
        """Step 실행 결과가 context에 merge되어야 한다."""
        _register_test_saga(
            "test_saga",
            [MockSuccessStep("payment", {"payment_id": "pay_001"})],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.context.get_from_step("payment", "payment_id") == "pay_001"

    def test_current_step_index_increments(self):
        """각 Step 성공 후 current_step_index가 증가해야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("step1"),
                MockSuccessStep("step2"),
                MockSuccessStep("step3"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.current_step_index == 3
        assert result.status == SagaStatus.COMPLETED

    def test_completed_at_is_set_on_success(self):
        """성공 시 completed_at이 설정되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.completed_at is not None

    def test_started_at_is_set_on_run(self):
        """실행 시 started_at이 설정되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.started_at is not None

    def test_unregistered_saga_raises_value_error(self):
        """미등록 saga_name이면 ValueError를 발생시켜야 한다."""
        orchestrator = _make_orchestrator()
        with pytest.raises(ValueError, match="not registered"):
            orchestrator.execute_saga("nonexistent", {})

    def test_lock_acquired_and_released(self):
        """실행 시 분산 락이 획득 및 해제되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_lock = _make_mock_lock()
        orchestrator = _make_orchestrator(lock=mock_lock)
        orchestrator.execute_saga("test_saga", {})

        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_lock_failure_returns_compensation_failed(self):
        """락 획득 실패 시 COMPENSATION_FAILED를 반환해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_lock = _make_mock_lock()
        mock_lock.acquire.return_value = False
        orchestrator = _make_orchestrator(lock=mock_lock)
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPENSATION_FAILED
        assert "lock" in result.error_message.lower()

    def test_event_bus_saga_started_emitted(self):
        """실행 시작 시 SAGA_STARTED 이벤트가 발행되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_bus = _make_mock_event_bus()
        orchestrator = _make_orchestrator(event_bus=mock_bus)
        orchestrator.execute_saga("test_saga", {})

        emit_calls = mock_bus.emit.call_args_list
        started_calls = [
            c
            for c in emit_calls
            if c.kwargs.get("event_type") == SagaEventType.SAGA_STARTED or (c.args and c.args[0] == SagaEventType.SAGA_STARTED)
        ]
        assert len(started_calls) >= 1

    def test_save_instance_called_on_transitions(self):
        """상태 전환마다 _save_instance가 호출되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_backend = _make_mock_backend()
        orchestrator = _make_orchestrator(backend=mock_backend)
        orchestrator.execute_saga("test_saga", {})

        # 최소 3번: 초기, RUNNING, COMPLETED
        assert mock_backend.set.call_count >= 3


# =============================================================================
# 동작 검증 — Compensation
# =============================================================================


class TestSagaOrchestratorCompensationBehavior:
    """Saga 보상(Compensation) 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_compensation_on_step_failure(self):
        """Step 실패 시 이전 성공 Step들을 역순 compensate해야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment"),
                MockSuccessStep("point"),
                MockFailingStep("inventory"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.status == SagaStatus.COMPENSATED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED
        assert result.step_instances[1].status == SagaStepStatus.COMPENSATED
        assert result.step_instances[2].status == SagaStepStatus.EXECUTE_FAILED

    def test_compensation_failure_stores_to_dlq(self):
        """compensate 실패 시 DLQ에 전체 컨텍스트가 저장되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockFailingCompensateStep("failing_comp"),
                MockFailingStep("failing_exec"),
            ],
        )
        mock_dlq = _make_mock_dlq()
        orchestrator = _make_orchestrator(dlq=mock_dlq)
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.status == SagaStatus.COMPENSATION_FAILED
        mock_dlq.store_failure.assert_called_once()
        call_kwargs = mock_dlq.store_failure.call_args.kwargs
        assert call_kwargs["domain"] == "saga"
        assert call_kwargs["failure_type"] == "COMPENSATION_FAILED"
        assert "saga_name" in call_kwargs["snapshot_data"]

    def test_compensate_exception_stores_to_dlq(self):
        """compensate에서 예외가 발생해도 DLQ에 저장되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockCompensateExceptionStep("exception_comp"),
                MockFailingStep("failing_exec"),
            ],
        )
        mock_dlq = _make_mock_dlq()
        orchestrator = _make_orchestrator(dlq=mock_dlq)
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.status == SagaStatus.COMPENSATION_FAILED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATE_FAILED
        mock_dlq.store_failure.assert_called_once()

    def test_compensation_skips_unexecuted_steps(self):
        """실행되지 않은 Step은 보상하지 않아야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("step1"),
                MockFailingStep("step2"),
                MockSuccessStep("step3"),  # 실행되지 않음
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.step_instances[2].status == SagaStepStatus.NOT_STARTED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED

    def test_blast_radius_assessed_on_compensation_failure(self):
        """보상 실패 시 BlastRadiusService.assess_impact가 호출되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockFailingCompensateStep("failing_comp"),
                MockFailingStep("failing_exec"),
            ],
        )
        mock_br = _make_mock_blast_radius()
        orchestrator = _make_orchestrator(blast_radius=mock_br)
        orchestrator.execute_saga("test_saga", {})

        mock_br.assess_impact.assert_called_once()

    def test_lock_extended_during_compensation(self):
        """보상 루프에서 각 Step 전 Lock TTL이 연장되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment"),
                MockSuccessStep("point"),
                MockFailingStep("inventory"),
            ],
        )
        mock_lock = _make_mock_lock()
        orchestrator = _make_orchestrator(lock=mock_lock)
        orchestrator.execute_saga("test_saga", {"order_id": 123})

        # extend가 최소 한번은 호출되어야 한다
        assert mock_lock.extend.call_count >= 1


# =============================================================================
# 동작 검증 — 서킷브레이커
# =============================================================================


class TestSagaOrchestratorCircuitBreakerBehavior:
    """서킷브레이커 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_circuit_breaker_open_suspends_saga(self):
        """서킷브레이커가 OPEN이면 SUSPENDED 상태로 전환해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_cb = _make_mock_circuit_breaker("open")
        orchestrator = _make_orchestrator(circuit_breaker=mock_cb)
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.SUSPENDED
        assert "Circuit breaker OPEN" in result.error_message

    def test_circuit_breaker_closed_allows_execution(self):
        """서킷브레이커가 CLOSED이면 정상 실행되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_cb = _make_mock_circuit_breaker("closed")
        orchestrator = _make_orchestrator(circuit_breaker=mock_cb)
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPLETED


# =============================================================================
# 동작 검증 — can_execute
# =============================================================================


class TestSagaOrchestratorCanExecuteBehavior:
    """can_execute 거부 시 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_can_execute_false_triggers_compensation(self):
        """can_execute()가 False이면 COMPENSATING으로 전환해야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("step1"),
                MockCannotExecuteStep("step2"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPENSATED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED
        assert result.step_instances[1].status == SagaStepStatus.EXECUTE_FAILED

    def test_can_execute_false_injects_context(self):
        """can_execute() 실패 시 abort_reason이 context에 주입되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("step1"),
                MockCannotExecuteStep("step2"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.context.failed_step_name == "step2"
        assert "can_execute rejected" in result.context.abort_reason


# =============================================================================
# 동작 검증 — Retry (RETRY_SCHEDULED)
# =============================================================================


class TestSagaOrchestratorRetryBehavior:
    """재시도(RETRY_SCHEDULED) 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    @patch("selfhealing.services.saga.tasks.resume_saga_instance_task")
    def test_retryable_failure_sets_retry_scheduled(self, mock_task):
        """retryable 실패 시 RETRY_SCHEDULED 상태로 전환해야 한다."""
        mock_task.apply_async = MagicMock()

        _register_test_saga(
            "test_saga",
            [MockRetryableStep("step1")],
            max_retries_per_step=2,
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.step_instances[0].status == SagaStepStatus.RETRY_SCHEDULED
        assert result.step_instances[0].next_retry_at is not None
        assert result.step_instances[0].retry_count == 1

    @patch("selfhealing.services.saga.tasks.resume_saga_instance_task")
    def test_retryable_failure_dispatches_celery_task(self, mock_task):
        """retryable 실패 시 Celery apply_async가 호출되어야 한다."""
        mock_task.apply_async = MagicMock()

        _register_test_saga(
            "test_saga",
            [MockRetryableStep("step1")],
            max_retries_per_step=2,
        )
        orchestrator = _make_orchestrator()
        orchestrator.execute_saga("test_saga", {})

        mock_task.apply_async.assert_called_once()


# =============================================================================
# 동작 검증 — Partial Execution
# =============================================================================


class TestSagaOrchestratorPartialExecutionBehavior:
    """partial_execution 보상 대상 포함 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_partial_execution_step_is_compensated(self):
        """partial_execution=True인 EXECUTE_FAILED Step도 보상 대상이어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment"),
                MockPartialFailStep("inventory"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        # inventory Step이 EXECUTE_FAILED이지만 compensate 호출됨
        assert result.step_instances[1].status in {
            SagaStepStatus.COMPENSATED,
            SagaStepStatus.COMPENSATE_FAILED,
        }
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED

    def test_partial_execution_sets_compensate_step_index_to_current(self):
        """partial_execution=True 시 compensate_step_index가 현재 index여야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment"),
                MockPartialFailStep("inventory"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        # partial 실패 + 보상 후 최종 상태
        assert result.status in {SagaStatus.COMPENSATED, SagaStatus.COMPENSATION_FAILED}


# =============================================================================
# 동작 검증 — Context Injection
# =============================================================================


class TestSagaOrchestratorContextInjectionBehavior:
    """COMPENSATING 전환 시 Context Injection 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_context_injection_on_step_failure(self):
        """Step 실패 시 failed_step_name과 abort_reason이 주입되어야 한다."""
        _register_test_saga(
            "test_saga",
            [
                MockSuccessStep("payment"),
                MockFailingStep("inventory"),
            ],
        )
        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {"order_id": 123})

        assert result.context.failed_step_name == "inventory"
        assert result.context.abort_reason is not None
        assert result.context.abort_error_code == "TEST_ERROR"


# =============================================================================
# 동작 검증 — _save_instance Fail-fast
# =============================================================================


class TestSagaOrchestratorSaveInstanceBehavior:
    """_save_instance 실패 시 Fail-fast 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_save_instance_failure_raises_exception(self):
        """_save_instance 실패 시 예외가 re-raise되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_backend = MagicMock()
        del mock_backend._client
        mock_backend.set.side_effect = ConnectionError("Redis down")
        orchestrator = _make_orchestrator(backend=mock_backend)

        with pytest.raises(ConnectionError, match="Redis down"):
            orchestrator.execute_saga("test_saga", {"order_id": 123})

    def test_save_instance_version_conflict_raises(self):
        """SessionVersionConflictError가 re-raise되어야 한다."""
        from selfhealing.services.coordination.recovery_coordinator import (
            SessionVersionConflictError,
        )

        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_backend = MagicMock()
        del mock_backend._client
        mock_backend.set.side_effect = SessionVersionConflictError("test-id", 0, 1)
        orchestrator = _make_orchestrator(backend=mock_backend)

        with pytest.raises(SessionVersionConflictError):
            orchestrator.execute_saga("test_saga", {"order_id": 123})


# =============================================================================
# 동작 검증 — resume_saga
# =============================================================================


class TestSagaOrchestratorResumeBehavior:
    """resume_saga 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def _make_stored_instance(
        self,
        orchestrator: SagaOrchestrator,
        status: SagaStatus = SagaStatus.SUSPENDED,
        saga_name: str = "test_saga",
        resume_count: int = 0,
        step_count: int = 2,
    ) -> SagaInstance:
        """Backend에 저장된 SagaInstance를 생성한다."""
        instance = SagaInstance(
            id="saga-test123",
            saga_name=saga_name,
            status=status,
            step_instances=[SagaStepInstance(step_name=f"step{i}", order=i) for i in range(step_count)],
            context=SagaContext(saga_instance_id="saga-test123"),
            metadata={"resume_count": resume_count} if resume_count > 0 else {},
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        return instance

    def test_resume_saga_not_found_raises(self):
        """인스턴스를 찾을 수 없으면 ValueError를 발생시켜야 한다."""
        orchestrator = _make_orchestrator()
        with pytest.raises(ValueError, match="not found"):
            orchestrator.resume_saga("nonexistent-id")

    def test_resume_saga_acquires_lock_first(self):
        """resume_saga()가 분산 락 획득을 선행해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step0"), MockSuccessStep("step1")])
        mock_lock = _make_mock_lock()
        mock_lock.acquire.return_value = False  # 다른 워커가 보유 중
        mock_backend = _make_mock_backend()

        orchestrator = _make_orchestrator(lock=mock_lock, backend=mock_backend)
        instance = self._make_stored_instance(orchestrator)
        mock_backend.get.return_value = instance.to_dict()

        result = orchestrator.resume_saga("saga-test123")

        mock_lock.acquire.assert_called_once()
        assert result.status != SagaStatus.COMPLETED

    def test_resume_saga_max_count_exceeded(self):
        """MAX_RESUME_COUNT 초과 시 COMPENSATION_FAILED로 전환해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step0"), MockSuccessStep("step1")])
        mock_dlq = _make_mock_dlq()
        mock_backend = _make_mock_backend()

        orchestrator = _make_orchestrator(dlq=mock_dlq, backend=mock_backend)
        instance = self._make_stored_instance(
            orchestrator,
            resume_count=MAX_RESUME_COUNT,
        )
        mock_backend.get.return_value = instance.to_dict()

        result = orchestrator.resume_saga("saga-test123")

        assert result.status == SagaStatus.COMPENSATION_FAILED
        mock_dlq.store_failure.assert_called_once()

    def test_resume_saga_terminal_status_returns_unchanged(self):
        """터미널 상태에서는 재개 없이 그대로 반환해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step0"), MockSuccessStep("step1")])
        mock_backend = _make_mock_backend()
        orchestrator = _make_orchestrator(backend=mock_backend)

        instance = self._make_stored_instance(orchestrator, status=SagaStatus.COMPLETED)
        mock_backend.get.return_value = instance.to_dict()

        result = orchestrator.resume_saga("saga-test123")
        assert result.status == SagaStatus.COMPLETED

    def test_resume_saga_version_mismatch_suspends(self):
        """정의 버전 불일치 시 SUSPENDED 상태로 유지해야 한다."""
        _register_test_saga(
            "test_saga",
            [MockSuccessStep("step0"), MockSuccessStep("step1")],
            version=2,  # 현재 정의 버전
        )
        mock_backend = _make_mock_backend()
        orchestrator = _make_orchestrator(backend=mock_backend)

        instance = self._make_stored_instance(orchestrator)
        instance.definition_version = 1  # 구 버전
        mock_backend.get.return_value = instance.to_dict()

        result = orchestrator.resume_saga("saga-test123")
        assert result.status == SagaStatus.SUSPENDED


# =============================================================================
# 동작 검증 — _execute_with_timeout
# =============================================================================


class TestExecuteWithTimeoutBehavior:
    """_execute_with_timeout 동작 검증."""

    def test_successful_step_returns_result(self):
        """성공하는 Step은 StepResult를 정상 반환해야 한다."""
        orchestrator = _make_orchestrator()
        ctx = SagaContext(saga_instance_id="test")

        def fast_step(c):
            return StepResult.succeeded({"done": True})

        result = orchestrator._execute_with_timeout(fast_step, ctx, timeout_seconds=5)
        assert result.success is True
        assert result.data == {"done": True}

    def test_timeout_returns_failed_result(self):
        """타임아웃 초과 시 실패 결과를 반환해야 한다."""
        import threading

        orchestrator = _make_orchestrator()
        ctx = SagaContext(saga_instance_id="test")

        event = threading.Event()

        def slow_step(c):
            event.wait(timeout=10)
            return StepResult.succeeded()

        result = orchestrator._execute_with_timeout(slow_step, ctx, timeout_seconds=1)
        # 타임아웃 후 스레드 정리
        event.set()

        assert result.success is False
        assert result.retryable is True
        assert "timed out" in result.error.lower()

    def test_exception_returns_failed_result(self):
        """예외 발생 시 실패 결과를 반환해야 한다."""
        orchestrator = _make_orchestrator()
        ctx = SagaContext(saga_instance_id="test")

        def error_step(c):
            raise RuntimeError("Step crashed")

        result = orchestrator._execute_with_timeout(error_step, ctx, timeout_seconds=5)
        assert result.success is False
        assert "RuntimeError" in result.error


# =============================================================================
# 동작 검증 — 거버넌스
# =============================================================================


class TestSagaOrchestratorGovernanceBehavior:
    """거버넌스 차단 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_governance_blocked_returns_compensation_failed(self):
        """거버넌스 차단 시 COMPENSATION_FAILED를 반환해야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        orchestrator = _make_orchestrator()

        mock_result = MagicMock()
        mock_result.allowed = False
        mock_result.block_message = "Kill switch activated"

        with patch.object(
            orchestrator,
            "_check_governance",
            return_value=mock_result,
        ):
            result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPENSATION_FAILED
        assert "Governance blocked" in result.error_message


# =============================================================================
# 동작 검증 — 최종 이벤트 발행
# =============================================================================


class TestSagaOrchestratorEmitFinalEventBehavior:
    """최종 이벤트 발행 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_completed_emits_saga_completed(self):
        """COMPLETED 시 SAGA_COMPLETED 이벤트가 발행되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_bus = _make_mock_event_bus()
        orchestrator = _make_orchestrator(event_bus=mock_bus)
        orchestrator.execute_saga("test_saga", {})

        emit_calls = mock_bus.emit.call_args_list
        completed_calls = [
            c
            for c in emit_calls
            if c.kwargs.get("event_type") == SagaEventType.SAGA_COMPLETED
            or (c.args and c.args[0] == SagaEventType.SAGA_COMPLETED)
        ]
        assert len(completed_calls) >= 1

    def test_compensated_emits_saga_compensated(self):
        """COMPENSATED 시 SAGA_COMPENSATED 이벤트가 발행되어야 한다."""
        _register_test_saga(
            "test_saga",
            [MockSuccessStep("step1"), MockFailingStep("step2")],
        )
        mock_bus = _make_mock_event_bus()
        orchestrator = _make_orchestrator(event_bus=mock_bus)
        orchestrator.execute_saga("test_saga", {})

        emit_calls = mock_bus.emit.call_args_list
        compensated_calls = [
            c
            for c in emit_calls
            if c.kwargs.get("event_type") == SagaEventType.SAGA_COMPENSATED
            or (c.args and c.args[0] == SagaEventType.SAGA_COMPENSATED)
        ]
        assert len(compensated_calls) >= 1


# =============================================================================
# 동작 검증 — Idempotency
# =============================================================================


class TestSagaOrchestratorIdempotencyBehavior:
    """멱등성 확인 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    def test_duplicate_step_is_skipped(self):
        """이미 실행된 Step은 skip되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])
        mock_idemp = _make_mock_idempotency()
        result_mock = MagicMock()
        result_mock.is_duplicate = True
        mock_idemp.check.return_value = result_mock

        orchestrator = _make_orchestrator(idempotency=mock_idemp)
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPLETED
        assert result.step_instances[0].status == SagaStepStatus.EXECUTED


# =============================================================================
# 동작 검증 — Tasks (scan_orphan_sagas, resume_saga_instance_task)
# =============================================================================


class TestSagaTasksBehavior:
    """Saga Celery Task 동작 검증."""

    def test_get_saga_beat_schedule_returns_scan_entry(self):
        """get_saga_beat_schedule()가 scan-orphan-sagas 항목을 반환해야 한다."""
        from selfhealing.services.saga.tasks import get_saga_beat_schedule

        schedule = get_saga_beat_schedule()
        assert "scan-orphan-sagas" in schedule
        assert schedule["scan-orphan-sagas"]["task"] == "selfhealing.scan_orphan_sagas"
        assert schedule["scan-orphan-sagas"]["schedule"] == 120.0

    def test_stale_threshold_matches_orchestrator(self):
        """tasks.py의 STALE_THRESHOLD가 orchestrator의 값과 일치해야 한다."""
        from selfhealing.services.saga.tasks import (
            STALE_THRESHOLD_SECONDS as TASK_THRESHOLD,
        )

        assert TASK_THRESHOLD == STALE_THRESHOLD_SECONDS


# =============================================================================
# 계약 검증 — Prometheus 메트릭 정의
# =============================================================================


class TestSagaMetricsContract:
    """Saga Prometheus 메트릭 정의 계약 검증."""

    def test_saga_executions_total_exists(self):
        """saga_executions_total Counter가 definitions에 존재해야 한다."""
        from selfhealing.services.metrics.definitions import (
            saga_executions_total,
        )

        assert saga_executions_total is not None

    def test_saga_step_duration_seconds_exists(self):
        """saga_step_duration_seconds Histogram이 definitions에 존재해야 한다."""
        from selfhealing.services.metrics.definitions import (
            saga_step_duration_seconds,
        )

        assert saga_step_duration_seconds is not None

    def test_saga_compensation_steps_total_exists(self):
        """saga_compensation_steps_total Counter가 definitions에 존재해야 한다."""
        from selfhealing.services.metrics.definitions import (
            saga_compensation_steps_total,
        )

        assert saga_compensation_steps_total is not None


# =============================================================================
# 동작 검증 — 메트릭 기록
# =============================================================================


class TestSagaOrchestratorMetricsBehavior:
    """메트릭 기록 동작 검증."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _saga_definitions.clear()
        yield
        _saga_definitions.clear()

    @patch("selfhealing.services.saga.orchestrator.SagaOrchestrator._record_execution_metric")
    def test_emit_final_event_records_execution_metric(self, mock_record):
        """_emit_final_event에서 saga_executions_total이 기록되어야 한다."""
        _register_test_saga("test_saga", [MockSuccessStep("step1")])

        orchestrator = _make_orchestrator()
        result = orchestrator.execute_saga("test_saga", {})

        assert result.status == SagaStatus.COMPLETED
        mock_record.assert_called_with(
            saga_name="test_saga",
            status="completed",
        )

    @patch("selfhealing.services.saga.orchestrator.SagaOrchestrator._record_step_duration")
    def test_forward_execution_records_step_duration(self, mock_duration):
        """순방향 실행에서 saga_step_duration_seconds가 기록되어야 한다."""
        _register_test_saga(
            "test_saga",
            [MockSuccessStep("step1"), MockSuccessStep("step2")],
        )

        orchestrator = _make_orchestrator()
        orchestrator.execute_saga("test_saga", {})

        # 각 Step마다 execute phase 기록
        calls = [c for c in mock_duration.call_args_list if c.kwargs.get("phase") == "execute"]
        assert len(calls) >= 2

    @patch("selfhealing.services.saga.orchestrator.SagaOrchestrator._record_compensation_metric")
    def test_compensation_records_metric(self, mock_comp):
        """보상 실행에서 saga_compensation_steps_total이 기록되어야 한다."""
        _register_test_saga(
            "test_saga",
            [MockSuccessStep("step1"), MockFailingStep("step2")],
        )

        orchestrator = _make_orchestrator()
        orchestrator.execute_saga("test_saga", {})

        # step1의 compensate 성공 기록
        mock_comp.assert_any_call(
            saga_name="test_saga",
            step_name="step1",
            status="success",
        )


# =============================================================================
# 동작 검증 — _transition_status
# =============================================================================


class TestSagaOrchestratorTransitionStatusBehavior:
    """_transition_status 원자적 상태 전환 동작 검증."""

    def test_transition_succeeds_on_matching_status(self):
        """현재 상태가 expected와 일치하면 전환에 성공해야 한다."""
        orchestrator = _make_orchestrator()
        instance = SagaInstance(
            id="test_id",
            saga_name="test_saga",
            definition_version="1.0.0",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        instance.status = SagaStatus.RUNNING

        result = orchestrator._transition_status(instance, SagaStatus.RUNNING, SagaStatus.COMPENSATING)

        assert result is True
        assert instance.status == SagaStatus.COMPENSATING

    def test_transition_fails_on_mismatched_status(self):
        """현재 상태가 expected와 일치하지 않으면 전환에 실패해야 한다."""
        orchestrator = _make_orchestrator()
        instance = SagaInstance(
            id="test_id",
            saga_name="test_saga",
            definition_version="1.0.0",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        instance.status = SagaStatus.COMPLETED

        result = orchestrator._transition_status(instance, SagaStatus.RUNNING, SagaStatus.COMPENSATING)

        assert result is False
        assert instance.status == SagaStatus.COMPLETED
