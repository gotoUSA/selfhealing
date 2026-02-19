"""
Unit tests for Saga Core Models.

Tests:
- SagaStatus enum 값/전이 계약 검증
- SagaStepStatus enum 값 계약 검증
- StepResult 팩토리 메서드 동작 검증
- SagaContext 데이터 조회/변이/직렬화 동작 검증
- SagaStepInstance 생성/직렬화 동작 검증
- SagaDefinition 유효성 검증 동작 검증
- SagaInstance 상태 추적/직렬화 동작 검증
- Saga 레지스트리 등록/조회 동작 검증
"""

import pytest

from selfhealing.services.saga.models import (
    ALLOWED_TRANSITIONS,
    SagaContext,
    SagaDefinition,
    SagaInstance,
    SagaStatus,
    SagaStepInstance,
    SagaStepStatus,
    StepResult,
)
from selfhealing.services.saga.registry import (
    _saga_definitions,
    get_saga_definition,
    list_saga_definitions,
    register_saga,
)
from selfhealing.services.saga.step import SagaStep


# =============================================================================
# 테스트 헬퍼: SagaStep 구체 구현 (테스트 전용)
# =============================================================================


class _DummyStep(SagaStep):
    """테스트용 SagaStep 구현."""

    def __init__(self, step_name: str = "dummy") -> None:
        self._name = step_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded({"executed": True})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded({"compensated": True})


class _CannotExecuteStep(SagaStep):
    """can_execute()가 False를 반환하는 테스트용 Step."""

    @property
    def name(self) -> str:
        return "cannot_execute"

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()

    def can_execute(self, ctx: SagaContext) -> tuple[bool, str]:
        return False, "precondition not met"


class _TimeoutStep(SagaStep):
    """timeout_seconds를 오버라이드하는 테스트용 Step."""

    @property
    def name(self) -> str:
        return "timeout_step"

    @property
    def timeout_seconds(self) -> int | None:
        return 30

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


# =============================================================================
# Fixture
# =============================================================================


@pytest.fixture(autouse=True)
def _clear_saga_registry():
    """각 테스트 후 레지스트리 초기화."""
    yield
    _saga_definitions.clear()


# =============================================================================
# SagaStatus — 계약 검증
# =============================================================================


class TestSagaStatusContract:
    """SagaStatus enum 값 계약 검증."""

    def test_pending_value(self):
        """PENDING 상태 값은 'pending'."""
        assert SagaStatus.PENDING == "pending"

    def test_running_value(self):
        """RUNNING 상태 값은 'running'."""
        assert SagaStatus.RUNNING == "running"

    def test_completed_value(self):
        """COMPLETED 상태 값은 'completed'."""
        assert SagaStatus.COMPLETED == "completed"

    def test_compensating_value(self):
        """COMPENSATING 상태 값은 'compensating'."""
        assert SagaStatus.COMPENSATING == "compensating"

    def test_compensated_value(self):
        """COMPENSATED 상태 값은 'compensated'."""
        assert SagaStatus.COMPENSATED == "compensated"

    def test_compensation_failed_value(self):
        """COMPENSATION_FAILED 상태 값은 'compensation_failed'."""
        assert SagaStatus.COMPENSATION_FAILED == "compensation_failed"

    def test_suspended_value(self):
        """SUSPENDED 상태 값은 'suspended'."""
        assert SagaStatus.SUSPENDED == "suspended"

    def test_timed_out_value(self):
        """TIMED_OUT 상태 값은 'timed_out'."""
        assert SagaStatus.TIMED_OUT == "timed_out"

    def test_status_count(self):
        """SagaStatus는 8개의 값을 가진다."""
        assert len(SagaStatus) == 8

    def test_from_string(self):
        """문자열에서 enum 변환."""
        assert SagaStatus("pending") == SagaStatus.PENDING

    def test_invalid_status_raises(self):
        """잘못된 상태 문자열은 ValueError."""
        with pytest.raises(ValueError):
            SagaStatus("invalid")


class TestAllowedTransitionsContract:
    """ALLOWED_TRANSITIONS 상태 전환 매핑 계약 검증."""

    def test_pending_transitions(self):
        """PENDING에서는 RUNNING으로만 전환 가능."""
        assert ALLOWED_TRANSITIONS[SagaStatus.PENDING] == {SagaStatus.RUNNING}

    def test_running_transitions(self):
        """RUNNING에서는 COMPLETED, COMPENSATING, SUSPENDED, TIMED_OUT으로 전환 가능."""
        assert ALLOWED_TRANSITIONS[SagaStatus.RUNNING] == {
            SagaStatus.COMPLETED,
            SagaStatus.COMPENSATING,
            SagaStatus.SUSPENDED,
            SagaStatus.TIMED_OUT,
        }

    def test_compensating_transitions(self):
        """COMPENSATING에서는 COMPENSATED, COMPENSATION_FAILED로 전환 가능."""
        assert ALLOWED_TRANSITIONS[SagaStatus.COMPENSATING] == {
            SagaStatus.COMPENSATED,
            SagaStatus.COMPENSATION_FAILED,
        }

    def test_suspended_transitions(self):
        """SUSPENDED에서는 RUNNING, COMPENSATING으로 전환 가능."""
        assert ALLOWED_TRANSITIONS[SagaStatus.SUSPENDED] == {
            SagaStatus.RUNNING,
            SagaStatus.COMPENSATING,
        }

    def test_terminal_states_have_no_transitions(self):
        """터미널 상태(COMPLETED, COMPENSATED, COMPENSATION_FAILED, TIMED_OUT)는 전환 없음."""
        terminal_states = [
            SagaStatus.COMPLETED,
            SagaStatus.COMPENSATED,
            SagaStatus.COMPENSATION_FAILED,
            SagaStatus.TIMED_OUT,
        ]
        for state in terminal_states:
            assert ALLOWED_TRANSITIONS[state] == set(), f"{state}는 터미널 상태"

    def test_all_statuses_have_transition_entry(self):
        """모든 SagaStatus가 ALLOWED_TRANSITIONS에 키로 존재."""
        for status in SagaStatus:
            assert status in ALLOWED_TRANSITIONS


# =============================================================================
# SagaStepStatus — 계약 검증
# =============================================================================


class TestSagaStepStatusContract:
    """SagaStepStatus enum 값 계약 검증."""

    def test_not_started_value(self):
        """NOT_STARTED 값은 'not_started'."""
        assert SagaStepStatus.NOT_STARTED == "not_started"

    def test_executing_value(self):
        """EXECUTING 값은 'executing'."""
        assert SagaStepStatus.EXECUTING == "executing"

    def test_executed_value(self):
        """EXECUTED 값은 'executed'."""
        assert SagaStepStatus.EXECUTED == "executed"

    def test_execute_failed_value(self):
        """EXECUTE_FAILED 값은 'execute_failed'."""
        assert SagaStepStatus.EXECUTE_FAILED == "execute_failed"

    def test_compensating_value(self):
        """COMPENSATING 값은 'compensating'."""
        assert SagaStepStatus.COMPENSATING == "compensating"

    def test_compensated_value(self):
        """COMPENSATED 값은 'compensated'."""
        assert SagaStepStatus.COMPENSATED == "compensated"

    def test_compensate_failed_value(self):
        """COMPENSATE_FAILED 값은 'compensate_failed'."""
        assert SagaStepStatus.COMPENSATE_FAILED == "compensate_failed"

    def test_step_status_count(self):
        """SagaStepStatus는 7개의 값을 가진다."""
        assert len(SagaStepStatus) == 7


# =============================================================================
# StepResult — 동작 검증
# =============================================================================


class TestStepResultBehavior:
    """StepResult 팩토리 메서드 및 필드 동작 검증."""

    def test_succeeded_returns_success(self):
        """succeeded() 팩토리는 success=True를 반환."""
        result = StepResult.succeeded()
        assert result.success is True
        assert result.data == {}
        assert result.error is None
        assert result.retryable is False
        assert result.partial_execution is False

    def test_succeeded_with_data(self):
        """succeeded()에 data를 전달하면 결과에 포함."""
        data = {"payment_id": "pay_001"}
        result = StepResult.succeeded(data=data)
        assert result.success is True
        assert result.data == data

    def test_failed_returns_failure(self):
        """failed() 팩토리는 success=False를 반환."""
        result = StepResult.failed(error="timeout", error_code="PG_TIMEOUT")
        assert result.success is False
        assert result.error == "timeout"
        assert result.error_code == "PG_TIMEOUT"
        assert result.retryable is False
        assert result.partial_execution is False

    def test_failed_with_retryable(self):
        """failed()에 retryable=True를 전달하면 재시도 가능 표시."""
        result = StepResult.failed(error="transient", error_code="NET_ERR", retryable=True)
        assert result.retryable is True

    def test_failed_with_side_effect_marks_partial(self):
        """failed_with_side_effect() 팩토리는 partial_execution=True를 설정."""
        result = StepResult.failed_with_side_effect(
            error="DB save error",
            data={"payment_id": "pay_001"},
            error_code="DB_SAVE_AFTER_PG_SUCCESS",
        )
        assert result.success is False
        assert result.partial_execution is True
        assert result.data == {"payment_id": "pay_001"}
        assert result.error == "DB save error"
        assert result.error_code == "DB_SAVE_AFTER_PG_SUCCESS"

    def test_failed_with_side_effect_none_data(self):
        """failed_with_side_effect()에 data=None이면 빈 dict."""
        result = StepResult.failed_with_side_effect(error="error")
        assert result.data == {}
        assert result.partial_execution is True

    def test_succeeded_none_data(self):
        """succeeded()에 data=None이면 빈 dict."""
        result = StepResult.succeeded(data=None)
        assert result.data == {}


# =============================================================================
# SagaContext — 동작 검증
# =============================================================================


class TestSagaContextBehavior:
    """SagaContext 조회/변이/직렬화 동작 검증."""

    def test_get_from_initial_data(self):
        """get()은 초기 데이터에서 값을 조회."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            initial_data={"order_id": 123, "amount": 50000},
        )
        assert ctx.get("order_id") == 123
        assert ctx.get("amount") == 50000

    def test_get_returns_default_for_missing_key(self):
        """get()은 존재하지 않는 key에 default 반환."""
        ctx = SagaContext(saga_instance_id="saga-001")
        assert ctx.get("missing") is None
        assert ctx.get("missing", "fallback") == "fallback"

    def test_get_step_results_override_initial(self):
        """get()은 step_results가 initial_data보다 우선."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            initial_data={"tx_id": "initial"},
            step_results={"payment": {"tx_id": "from_step"}},
        )
        assert ctx.get("tx_id") == "from_step"

    def test_get_latest_step_wins(self):
        """get()은 나중 Step 결과가 우선 (reversed 순서)."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            step_results={
                "step_a": {"key": "value_a"},
                "step_b": {"key": "value_b"},
            },
        )
        assert ctx.get("key") == "value_b"

    def test_get_step_data_returns_isolated_dict(self):
        """get_step_data()는 특정 Step의 결과만 반환."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            step_results={
                "payment": {"payment_id": "pay_001"},
                "point": {"point_tx_id": "pt_001"},
            },
        )
        assert ctx.get_step_data("payment") == {"payment_id": "pay_001"}
        assert ctx.get_step_data("point") == {"point_tx_id": "pt_001"}

    def test_get_step_data_unknown_step(self):
        """get_step_data()는 존재하지 않는 Step에 빈 dict 반환."""
        ctx = SagaContext(saga_instance_id="saga-001")
        assert ctx.get_step_data("unknown") == {}

    def test_get_from_step_specific_key(self):
        """get_from_step()은 특정 Step의 특정 key를 조회."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            step_results={
                "payment": {"tx_id": "pay_tx"},
                "point": {"tx_id": "point_tx"},
            },
        )
        assert ctx.get_from_step("payment", "tx_id") == "pay_tx"
        assert ctx.get_from_step("point", "tx_id") == "point_tx"

    def test_get_from_step_default(self):
        """get_from_step()은 존재하지 않는 key에 default 반환."""
        ctx = SagaContext(saga_instance_id="saga-001")
        assert ctx.get_from_step("payment", "tx_id") is None
        assert ctx.get_from_step("payment", "tx_id", "default") == "default"

    def test_merge_step_result(self):
        """merge_step_result()는 Step 결과를 컨텍스트에 저장."""
        ctx = SagaContext(saga_instance_id="saga-001")
        ctx.merge_step_result("payment", {"payment_id": "pay_001"})
        assert ctx.step_results["payment"] == {"payment_id": "pay_001"}

    def test_merge_step_result_overwrites(self):
        """merge_step_result()는 동일 Step 결과를 덮어쓴다."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            step_results={"payment": {"old": "data"}},
        )
        ctx.merge_step_result("payment", {"new": "data"})
        assert ctx.step_results["payment"] == {"new": "data"}

    def test_set_compensation_context(self):
        """set_compensation_context()는 보상 맥락 필드를 설정."""
        ctx = SagaContext(saga_instance_id="saga-001")
        ctx.set_compensation_context(
            failed_step_name="inventory",
            abort_reason="Insufficient stock",
            abort_error_code="INVENTORY_SHORTAGE",
        )
        assert ctx.failed_step_name == "inventory"
        assert ctx.abort_reason == "Insufficient stock"
        assert ctx.abort_error_code == "INVENTORY_SHORTAGE"

    def test_set_compensation_context_without_error_code(self):
        """set_compensation_context()에 error_code 미전달 시 None."""
        ctx = SagaContext(saga_instance_id="saga-001")
        ctx.set_compensation_context(
            failed_step_name="point",
            abort_reason="Point deduction failed",
        )
        assert ctx.abort_error_code is None

    def test_to_dict(self):
        """to_dict()는 모든 필드를 포함한 딕셔너리 반환."""
        ctx = SagaContext(
            saga_instance_id="saga-001",
            initial_data={"order_id": 123},
            step_results={"payment": {"payment_id": "pay_001"}},
            failed_step_name="inventory",
            abort_reason="error",
            abort_error_code="ERR_CODE",
        )
        result = ctx.to_dict()

        assert result["saga_instance_id"] == "saga-001"
        assert result["initial_data"] == {"order_id": 123}
        assert result["step_results"] == {"payment": {"payment_id": "pay_001"}}
        assert result["failed_step_name"] == "inventory"
        assert result["abort_reason"] == "error"
        assert result["abort_error_code"] == "ERR_CODE"

    def test_from_dict_roundtrip(self):
        """to_dict() → from_dict() 왕복 변환이 원본과 동일."""
        original = SagaContext(
            saga_instance_id="saga-001",
            initial_data={"order_id": 123},
            step_results={"payment": {"payment_id": "pay_001"}},
            failed_step_name="inventory",
            abort_reason="error",
            abort_error_code="ERR_CODE",
        )
        restored = SagaContext.from_dict(original.to_dict())

        assert restored.saga_instance_id == original.saga_instance_id
        assert restored.initial_data == original.initial_data
        assert restored.step_results == original.step_results
        assert restored.failed_step_name == original.failed_step_name
        assert restored.abort_reason == original.abort_reason
        assert restored.abort_error_code == original.abort_error_code

    def test_from_dict_with_minimal_data(self):
        """from_dict()는 필수 필드만으로 생성 가능."""
        data = {"saga_instance_id": "saga-002"}
        ctx = SagaContext.from_dict(data)
        assert ctx.saga_instance_id == "saga-002"
        assert ctx.initial_data == {}
        assert ctx.step_results == {}
        assert ctx.failed_step_name is None
        assert ctx.abort_reason is None
        assert ctx.abort_error_code is None


# =============================================================================
# SagaStepInstance — 동작 검증
# =============================================================================


class TestSagaStepInstanceBehavior:
    """SagaStepInstance 생성/직렬화 동작 검증."""

    def test_create_with_defaults(self):
        """기본값으로 Step 인스턴스 생성."""
        step = SagaStepInstance(step_name="payment", order=0)

        assert step.step_name == "payment"
        assert step.order == 0
        assert step.status == SagaStepStatus.NOT_STARTED
        assert step.execute_started_at is None
        assert step.execute_completed_at is None
        assert step.compensate_started_at is None
        assert step.compensate_completed_at is None
        assert step.error_message is None
        assert step.error_code is None
        assert step.retry_count == 0
        assert step.next_retry_at is None
        assert step.partial_execution is False

    def test_create_with_all_fields(self):
        """모든 필드를 지정하여 Step 인스턴스 생성."""
        step = SagaStepInstance(
            step_name="payment",
            order=0,
            status=SagaStepStatus.EXECUTED,
            execute_started_at="2026-02-19T10:00:00+00:00",
            execute_completed_at="2026-02-19T10:00:05+00:00",
            compensate_started_at=None,
            compensate_completed_at=None,
            error_message=None,
            error_code=None,
            retry_count=1,
            next_retry_at="2026-02-19T10:01:00+00:00",
            partial_execution=False,
        )
        assert step.status == SagaStepStatus.EXECUTED
        assert step.retry_count == 1

    def test_to_dict(self):
        """to_dict()는 모든 필드를 포함한 딕셔너리 반환."""
        step = SagaStepInstance(
            step_name="inventory",
            order=2,
            status=SagaStepStatus.EXECUTE_FAILED,
            error_message="out of stock",
            error_code="INVENTORY_SHORTAGE",
            partial_execution=True,
        )
        result = step.to_dict()

        assert result["step_name"] == "inventory"
        assert result["order"] == 2
        assert result["status"] == "execute_failed"
        assert result["error_message"] == "out of stock"
        assert result["error_code"] == "INVENTORY_SHORTAGE"
        assert result["partial_execution"] is True

    def test_from_dict_roundtrip(self):
        """to_dict() → from_dict() 왕복 변환이 원본과 동일."""
        original = SagaStepInstance(
            step_name="payment",
            order=0,
            status=SagaStepStatus.COMPENSATED,
            execute_started_at="2026-02-19T10:00:00+00:00",
            execute_completed_at="2026-02-19T10:00:05+00:00",
            compensate_started_at="2026-02-19T10:01:00+00:00",
            compensate_completed_at="2026-02-19T10:01:03+00:00",
            error_message=None,
            error_code=None,
            retry_count=2,
            next_retry_at="2026-02-19T10:02:00+00:00",
            partial_execution=False,
        )
        restored = SagaStepInstance.from_dict(original.to_dict())

        assert restored.step_name == original.step_name
        assert restored.order == original.order
        assert restored.status == original.status
        assert restored.execute_started_at == original.execute_started_at
        assert restored.execute_completed_at == original.execute_completed_at
        assert restored.compensate_started_at == original.compensate_started_at
        assert restored.compensate_completed_at == original.compensate_completed_at
        assert restored.retry_count == original.retry_count
        assert restored.next_retry_at == original.next_retry_at
        assert restored.partial_execution == original.partial_execution

    def test_from_dict_with_minimal_data(self):
        """from_dict()는 필수 필드만으로 생성. 나머지는 기본값."""
        data = {"step_name": "point", "order": 1}
        step = SagaStepInstance.from_dict(data)
        assert step.step_name == "point"
        assert step.order == 1
        assert step.status == SagaStepStatus.NOT_STARTED
        assert step.retry_count == 0
        assert step.partial_execution is False


# =============================================================================
# SagaDefinition — 동작 검증
# =============================================================================


class TestSagaDefinitionContract:
    """SagaDefinition 기본값 계약 검증."""

    def test_default_version(self):
        """기본 version은 1."""
        defn = SagaDefinition(name="test", steps=[_DummyStep()])
        assert defn.version == 1

    def test_default_timeout_seconds(self):
        """기본 timeout_seconds는 600."""
        defn = SagaDefinition(name="test", steps=[_DummyStep()])
        assert defn.timeout_seconds == 600

    def test_default_max_retries_per_step(self):
        """기본 max_retries_per_step은 2."""
        defn = SagaDefinition(name="test", steps=[_DummyStep()])
        assert defn.max_retries_per_step == 2

    def test_default_retry_backoff_strategy(self):
        """기본 retry_backoff_strategy는 'exponential'."""
        defn = SagaDefinition(name="test", steps=[_DummyStep()])
        assert defn.retry_backoff_strategy == "exponential"


class TestSagaDefinitionBehavior:
    """SagaDefinition 유효성 검증 동작 검증."""

    def test_validate_empty_name(self):
        """이름이 비어있으면 유효하지 않음."""
        defn = SagaDefinition(name="", steps=[_DummyStep()])
        valid, error = defn.validate()
        assert valid is False
        assert "name" in error.lower()

    def test_validate_no_steps(self):
        """Step이 없으면 유효하지 않음."""
        defn = SagaDefinition(name="test", steps=[])
        valid, error = defn.validate()
        assert valid is False
        assert "step" in error.lower()

    def test_validate_duplicate_step_names(self):
        """Step 이름이 중복되면 유효하지 않음."""
        defn = SagaDefinition(
            name="test",
            steps=[_DummyStep("dup"), _DummyStep("dup")],
        )
        valid, error = defn.validate()
        assert valid is False
        assert "duplicate" in error.lower()

    def test_validate_success(self):
        """유효한 정의는 (True, '') 반환."""
        defn = SagaDefinition(
            name="order_creation",
            steps=[_DummyStep("payment"), _DummyStep("point")],
        )
        valid, error = defn.validate()
        assert valid is True
        assert error == ""


# =============================================================================
# SagaInstance — 동작 검증
# =============================================================================


class TestSagaInstanceContract:
    """SagaInstance 기본값 계약 검증."""

    def test_default_status_is_pending(self):
        """기본 status는 PENDING."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        assert instance.status == SagaStatus.PENDING

    def test_default_definition_version(self):
        """기본 definition_version은 1."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        assert instance.definition_version == 1

    def test_default_version(self):
        """기본 OCC version은 0."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        assert instance.version == 0

    def test_default_compensate_step_index(self):
        """기본 compensate_step_index는 -1 (미시작)."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        assert instance.compensate_step_index == -1

    def test_default_initiated_by(self):
        """기본 initiated_by는 'system'."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        assert instance.initiated_by == "system"


class TestSagaInstanceBehavior:
    """SagaInstance 상태 추적/직렬화 동작 검증."""

    def _make_instance_with_steps(self) -> SagaInstance:
        """3단계 Step이 포함된 SagaInstance 생성 헬퍼."""
        steps = [
            SagaStepInstance(step_name="payment", order=0, status=SagaStepStatus.EXECUTED),
            SagaStepInstance(step_name="point", order=1, status=SagaStepStatus.EXECUTED),
            SagaStepInstance(step_name="inventory", order=2, status=SagaStepStatus.NOT_STARTED),
        ]
        return SagaInstance(
            id="saga-001",
            saga_name="order_creation",
            step_instances=steps,
            current_step_index=2,
            context=SagaContext(saga_instance_id="saga-001"),
        )

    def test_get_current_step(self):
        """get_current_step()은 current_step_index에 해당하는 Step 반환."""
        instance = self._make_instance_with_steps()
        current = instance.get_current_step()
        assert current is not None
        assert current.step_name == "inventory"

    def test_get_current_step_out_of_range(self):
        """current_step_index가 범위 밖이면 None 반환."""
        instance = SagaInstance(id="saga-001", saga_name="test", current_step_index=5)
        assert instance.get_current_step() is None

    def test_get_compensate_step(self):
        """get_compensate_step()은 compensate_step_index에 해당하는 Step 반환."""
        instance = self._make_instance_with_steps()
        instance.compensate_step_index = 1
        compensate = instance.get_compensate_step()
        assert compensate is not None
        assert compensate.step_name == "point"

    def test_get_compensate_step_not_started(self):
        """compensate_step_index가 -1이면 None 반환."""
        instance = self._make_instance_with_steps()
        assert instance.get_compensate_step() is None

    def test_get_executed_steps(self):
        """get_executed_steps()는 EXECUTED 상태인 Step만 반환."""
        instance = self._make_instance_with_steps()
        executed = instance.get_executed_steps()
        assert len(executed) == 2
        assert all(s.status == SagaStepStatus.EXECUTED for s in executed)

    def test_get_compensation_targets_includes_partial(self):
        """get_compensation_targets()는 partial_execution=True인 EXECUTE_FAILED도 포함."""
        steps = [
            SagaStepInstance(step_name="payment", order=0, status=SagaStepStatus.EXECUTED),
            SagaStepInstance(
                step_name="point",
                order=1,
                status=SagaStepStatus.EXECUTE_FAILED,
                partial_execution=True,
            ),
            SagaStepInstance(
                step_name="inventory",
                order=2,
                status=SagaStepStatus.EXECUTE_FAILED,
                partial_execution=False,
            ),
        ]
        instance = SagaInstance(id="saga-001", saga_name="test", step_instances=steps)
        targets = instance.get_compensation_targets()
        target_names = [t.step_name for t in targets]

        assert "payment" in target_names
        assert "point" in target_names
        assert "inventory" not in target_names

    def test_get_compensation_targets_excludes_non_partial_failed(self):
        """get_compensation_targets()는 partial_execution=False인 EXECUTE_FAILED 제외."""
        steps = [
            SagaStepInstance(
                step_name="failed_no_side_effect",
                order=0,
                status=SagaStepStatus.EXECUTE_FAILED,
                partial_execution=False,
            ),
        ]
        instance = SagaInstance(id="saga-001", saga_name="test", step_instances=steps)
        assert instance.get_compensation_targets() == []

    def test_get_progress(self):
        """get_progress()는 진행 상황 요약 딕셔너리 반환."""
        instance = self._make_instance_with_steps()
        instance.status = SagaStatus.RUNNING
        progress = instance.get_progress()

        assert progress["saga_name"] == "order_creation"
        assert progress["status"] == "running"
        assert progress["total_steps"] == 3
        assert progress["executed_steps"] == 2
        assert progress["compensated_steps"] == 0
        assert progress["current_step_index"] == 2

    def test_get_progress_empty_steps(self):
        """Step이 없으면 progress_percent는 0."""
        instance = SagaInstance(id="saga-001", saga_name="test")
        progress = instance.get_progress()
        assert progress["total_steps"] == 0
        assert progress["progress_percent"] == 0

    def test_to_dict(self):
        """to_dict()는 전체 인스턴스를 딕셔너리로 직렬화."""
        instance = self._make_instance_with_steps()
        instance.status = SagaStatus.RUNNING
        instance.started_at = "2026-02-19T10:00:00+00:00"
        instance.correlation_id = "corr-123"
        instance.metadata = {"source": "api"}

        result = instance.to_dict()

        assert result["id"] == "saga-001"
        assert result["saga_name"] == "order_creation"
        assert result["status"] == "running"
        assert len(result["step_instances"]) == 3
        assert result["context"] is not None
        assert result["started_at"] == "2026-02-19T10:00:00+00:00"
        assert result["correlation_id"] == "corr-123"
        assert result["metadata"] == {"source": "api"}

    def test_from_dict_roundtrip(self):
        """to_dict() → from_dict() 왕복 변환이 원본과 동일."""
        instance = self._make_instance_with_steps()
        instance.status = SagaStatus.RUNNING
        instance.definition_version = 2
        instance.version = 3
        instance.started_at = "2026-02-19T10:00:00+00:00"
        instance.initiated_by = "admin"
        instance.error_message = "test error"
        instance.correlation_id = "corr-123"
        instance.metadata = {"key": "value"}

        restored = SagaInstance.from_dict(instance.to_dict())

        assert restored.id == instance.id
        assert restored.saga_name == instance.saga_name
        assert restored.definition_version == instance.definition_version
        assert restored.version == instance.version
        assert restored.status == instance.status
        assert len(restored.step_instances) == len(instance.step_instances)
        assert restored.current_step_index == instance.current_step_index
        assert restored.compensate_step_index == instance.compensate_step_index
        assert restored.context is not None
        assert restored.started_at == instance.started_at
        assert restored.initiated_by == instance.initiated_by
        assert restored.error_message == instance.error_message
        assert restored.correlation_id == instance.correlation_id
        assert restored.metadata == instance.metadata

    def test_from_dict_without_context(self):
        """context가 None인 딕셔너리에서 복원."""
        data = {
            "id": "saga-002",
            "saga_name": "test",
            "context": None,
        }
        instance = SagaInstance.from_dict(data)
        assert instance.context is None

    def test_from_dict_minimal(self):
        """필수 필드만으로 from_dict() 복원."""
        data = {"id": "saga-003", "saga_name": "minimal"}
        instance = SagaInstance.from_dict(data)
        assert instance.id == "saga-003"
        assert instance.saga_name == "minimal"
        assert instance.status == SagaStatus.PENDING
        assert instance.definition_version == 1
        assert instance.version == 0
        assert instance.step_instances == []


# =============================================================================
# SagaStep ABC — 동작 검증
# =============================================================================


class TestSagaStepBehavior:
    """SagaStep ABC 인터페이스 동작 검증."""

    def test_dummy_step_execute(self):
        """구체 Step의 execute()가 정상 동작."""
        step = _DummyStep("payment")
        ctx = SagaContext(saga_instance_id="saga-001")
        result = step.execute(ctx)
        assert result.success is True
        assert result.data == {"executed": True}

    def test_dummy_step_compensate(self):
        """구체 Step의 compensate()가 정상 동작."""
        step = _DummyStep("payment")
        ctx = SagaContext(saga_instance_id="saga-001")
        result = step.compensate(ctx)
        assert result.success is True
        assert result.data == {"compensated": True}

    def test_can_execute_default_returns_true(self):
        """can_execute() 기본 구현은 (True, '') 반환."""
        step = _DummyStep()
        ctx = SagaContext(saga_instance_id="saga-001")
        can, reason = step.can_execute(ctx)
        assert can is True
        assert reason == ""

    def test_can_execute_override(self):
        """can_execute()를 오버라이드하면 False 반환 가능."""
        step = _CannotExecuteStep()
        ctx = SagaContext(saga_instance_id="saga-001")
        can, reason = step.can_execute(ctx)
        assert can is False
        assert "precondition" in reason

    def test_timeout_seconds_default_is_none(self):
        """timeout_seconds 기본값은 None."""
        step = _DummyStep()
        assert step.timeout_seconds is None

    def test_timeout_seconds_override(self):
        """timeout_seconds를 오버라이드하면 값 반환."""
        step = _TimeoutStep()
        assert step.timeout_seconds == 30

    def test_cannot_instantiate_abc(self):
        """SagaStep ABC는 직접 인스턴스화 불가."""
        with pytest.raises(TypeError):
            SagaStep()  # type: ignore[abstract]

    def test_name_property(self):
        """name 프로퍼티가 설정한 이름을 반환."""
        step = _DummyStep("my_step")
        assert step.name == "my_step"


# =============================================================================
# Saga Registry — 동작 검증
# =============================================================================


class TestSagaRegistryBehavior:
    """Saga 레지스트리 등록/조회 동작 검증."""

    def test_register_and_get(self):
        """정의 등록 후 조회 가능."""
        defn = SagaDefinition(
            name="order_creation",
            steps=[_DummyStep("payment"), _DummyStep("point")],
        )
        register_saga(defn)
        retrieved = get_saga_definition("order_creation")
        assert retrieved is not None
        assert retrieved.name == "order_creation"

    def test_get_unregistered_returns_none(self):
        """미등록 Saga 조회 시 None 반환."""
        assert get_saga_definition("nonexistent") is None

    def test_register_overwrites(self):
        """동일 이름으로 재등록 시 덮어쓴다."""
        defn_v1 = SagaDefinition(
            name="test_saga",
            version=1,
            steps=[_DummyStep("a")],
        )
        defn_v2 = SagaDefinition(
            name="test_saga",
            version=2,
            steps=[_DummyStep("a"), _DummyStep("b")],
        )
        register_saga(defn_v1)
        register_saga(defn_v2)

        retrieved = get_saga_definition("test_saga")
        assert retrieved is not None
        assert retrieved.version == 2
        assert len(retrieved.steps) == 2

    def test_register_invalid_raises(self):
        """유효하지 않은 정의 등록 시 ValueError."""
        defn = SagaDefinition(name="", steps=[])
        with pytest.raises(ValueError, match="Invalid saga definition"):
            register_saga(defn)

    def test_list_saga_definitions(self):
        """등록된 모든 Saga 이름 목록 반환."""
        register_saga(SagaDefinition(name="saga_a", steps=[_DummyStep("a")]))
        register_saga(SagaDefinition(name="saga_b", steps=[_DummyStep("b")]))
        names = list_saga_definitions()
        assert "saga_a" in names
        assert "saga_b" in names

    def test_list_empty_registry(self):
        """빈 레지스트리는 빈 목록 반환."""
        assert list_saga_definitions() == []
