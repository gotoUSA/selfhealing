"""
RunbookRegistry, Runbook, RiskLevel, ActionPrimitiveRegistry 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.runbook_registry

계약 검증 클래스 (Test*Contract):
    - TestRiskLevelContract
    - TestRunbookDefaultContract
    - TestRunbookStepDefaultContract
    - TestStepConditionDefaultContract
    - TestActionPrimitiveRegistryContract
    - TestRunbookRegistryConstantsContract
    - TestRunbookRegistryUpdatedEventContract

동작 검증 클래스 (Test*Behavior):
    - TestRiskLevelComparisonBehavior
    - TestRunbookValidateBehavior
    - TestRunbookRegistryRegisterBehavior
    - TestRunbookRegistryCrudBehavior
    - TestRunbookRegistryImmutabilityBehavior
    - TestRunbookRegistryDistributedSyncBehavior
    - TestRunbookRegistryThreadSafetyBehavior
    - TestActionPrimitiveRegistryBehavior
    - TestActionPrimitiveRegistrySchemaValidationBehavior
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, field_validator

from selfhealing.services.runbook.runbook_registry import (
    BUILTIN_CATEGORIES,
    ActionPrimitiveRegistry,
    RiskLevel,
    Runbook,
    RunbookRegistry,
    RunbookStep,
    RunbookStepContext,
    StepCondition,
    _TEMPLATE_VAR_RE,
)

# =============================================================================
# 테스트 헬퍼
# =============================================================================


def _make_minimal_step(name: str = "step_one", action: str = "config.set") -> RunbookStep:
    """최소 구성의 RunbookStep 생성."""
    return RunbookStep(name=name, action=action)


def _make_minimal_runbook(
    runbook_id: str = "rb_test",
    steps: list[RunbookStep] | None = None,
    enabled: bool = True,
    tags: list[str] | None = None,
) -> Runbook:
    """최소 구성의 Runbook 생성."""
    from selfhealing.services.runbook.models import PatternCondition

    return Runbook(
        id=runbook_id,
        name=f"Test Runbook {runbook_id}",
        description="테스트용 런북",
        trigger_condition=PatternCondition(),
        steps=steps or [_make_minimal_step()],
        enabled=enabled,
        tags=tags or [],
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
# 계약 검증 — RiskLevel
# =============================================================================


class TestRiskLevelContract:
    """RiskLevel enum 설계 계약값 검증."""

    def test_low_value_is_low_string(self):
        """RiskLevel.LOW의 문자열 값은 'low'."""
        assert RiskLevel.LOW == "low"

    def test_medium_value_is_medium_string(self):
        """RiskLevel.MEDIUM의 문자열 값은 'medium'."""
        assert RiskLevel.MEDIUM == "medium"

    def test_high_value_is_high_string(self):
        """RiskLevel.HIGH의 문자열 값은 'high'."""
        assert RiskLevel.HIGH == "high"

    def test_low_order_is_zero(self):
        """RiskLevel.LOW의 정수 우선순위는 0."""
        assert RiskLevel.LOW.order == 0

    def test_medium_order_is_one(self):
        """RiskLevel.MEDIUM의 정수 우선순위는 1."""
        assert RiskLevel.MEDIUM.order == 1

    def test_high_order_is_two(self):
        """RiskLevel.HIGH의 정수 우선순위는 2."""
        assert RiskLevel.HIGH.order == 2

    def test_risk_level_count_is_three(self):
        """RiskLevel enum 멤버는 3개이다."""
        assert len(RiskLevel) == 3

    def test_all_risk_levels_are_str_enum(self):
        """모든 RiskLevel 멤버는 str 타입이다."""
        for level in RiskLevel:
            assert isinstance(level, str)


# =============================================================================
# 계약 검증 — RunbookStep 기본값
# =============================================================================


class TestRunbookStepDefaultContract:
    """RunbookStep 필드 기본값 설계 계약 검증."""

    def test_params_default_empty_dict(self):
        """params 기본값은 빈 딕셔너리."""
        step = RunbookStep(name="s", action="a")
        assert step.params == {}

    def test_on_failure_action_default_none(self):
        """on_failure_action 기본값은 None."""
        step = RunbookStep(name="s", action="a")
        assert step.on_failure_action is None

    def test_on_failure_params_default_empty_dict(self):
        """on_failure_params 기본값은 빈 딕셔너리."""
        step = RunbookStep(name="s", action="a")
        assert step.on_failure_params == {}

    def test_validation_action_default_none(self):
        """validation_action 기본값은 None."""
        step = RunbookStep(name="s", action="a")
        assert step.validation_action is None

    def test_condition_default_none(self):
        """condition 기본값은 None (항상 실행)."""
        step = RunbookStep(name="s", action="a")
        assert step.condition is None

    def test_timeout_seconds_default_120(self):
        """timeout_seconds 기본값은 120초."""
        step = RunbookStep(name="s", action="a")
        assert step.timeout_seconds == 120

    def test_wait_after_seconds_default_zero(self):
        """wait_after_seconds 기본값은 0."""
        step = RunbookStep(name="s", action="a")
        assert step.wait_after_seconds == 0

    def test_idempotent_default_true(self):
        """idempotent 기본값은 True."""
        step = RunbookStep(name="s", action="a")
        assert step.idempotent is True


# =============================================================================
# 계약 검증 — Runbook 기본값
# =============================================================================


class TestRunbookDefaultContract:
    """Runbook 필드 기본값 설계 계약 검증."""

    def test_risk_level_default_low(self):
        """risk_level 기본값은 RiskLevel.LOW."""
        rb = _make_minimal_runbook()
        assert rb.risk_level == RiskLevel.LOW

    def test_enabled_default_true(self):
        """enabled 기본값은 True."""
        rb = _make_minimal_runbook()
        assert rb.enabled is True

    def test_priority_default_100(self):
        """priority 기본값은 100."""
        rb = _make_minimal_runbook()
        assert rb.priority == 100

    def test_cooldown_seconds_default_300(self):
        """cooldown_seconds 기본값은 300초 (5분)."""
        rb = _make_minimal_runbook()
        assert rb.cooldown_seconds == 300

    def test_version_default_1(self):
        """version 기본값은 1."""
        rb = _make_minimal_runbook()
        assert rb.version == 1

    def test_created_by_default_system(self):
        """created_by 기본값은 'system'."""
        rb = _make_minimal_runbook()
        assert rb.created_by == "system"

    def test_tags_default_empty_list(self):
        """tags 기본값은 빈 리스트."""
        rb = _make_minimal_runbook()
        assert rb.tags == []


# =============================================================================
# 계약 검증 — StepCondition 기본값
# =============================================================================


class TestStepConditionDefaultContract:
    """StepCondition 설계 계약값 검증."""

    def test_source_step_default_none(self):
        """source_step 기본값은 None."""
        sc = StepCondition(type="always")
        assert sc.source_step is None

    def test_field_default_none(self):
        """field 기본값은 None."""
        sc = StepCondition(type="always")
        assert sc.field is None

    def test_operator_default_none(self):
        """operator 기본값은 None."""
        sc = StepCondition(type="always")
        assert sc.operator is None

    def test_value_default_none(self):
        """value 기본값은 None."""
        sc = StepCondition(type="always")
        assert sc.value is None


# =============================================================================
# 계약 검증 — ActionPrimitiveRegistry
# =============================================================================


class TestActionPrimitiveRegistryContract:
    """ActionPrimitiveRegistry 설계 계약 검증."""

    def test_builtin_categories_contains_config(self):
        """내장 카테고리에 'config'가 포함되어 있다."""
        assert "config" in BUILTIN_CATEGORIES

    def test_builtin_categories_contains_assert(self):
        """내장 카테고리에 'assert'가 포함되어 있다."""
        assert "assert" in BUILTIN_CATEGORIES

    def test_builtin_categories_contains_notify(self):
        """내장 카테고리에 'notify'가 포함되어 있다."""
        assert "notify" in BUILTIN_CATEGORIES

    def test_builtin_categories_contains_recovery(self):
        """내장 카테고리에 'recovery'가 포함되어 있다."""
        assert "recovery" in BUILTIN_CATEGORIES

    def test_builtin_categories_contains_emergency(self):
        """내장 카테고리에 'emergency'가 포함되어 있다."""
        assert "emergency" in BUILTIN_CATEGORIES

    def test_builtin_categories_contains_wait(self):
        """내장 카테고리에 'wait'가 포함되어 있다."""
        assert "wait" in BUILTIN_CATEGORIES

    def test_builtin_categories_count_is_six(self):
        """내장 카테고리는 6개이다."""
        assert len(BUILTIN_CATEGORIES) == 6


# =============================================================================
# 계약 검증 — RunbookRegistry 상수
# =============================================================================


class TestRunbookRegistryConstantsContract:
    """RunbookRegistry 상수 설계 계약 검증."""

    def test_enabled_key_value(self):
        """_ENABLED_KEY는 설계 문서의 키 패턴과 일치한다."""
        assert RunbookRegistry._ENABLED_KEY == "selfhealing:runbook:registry:enabled"


# =============================================================================
# 계약 검증 — EventType.RUNBOOK_REGISTRY_UPDATED
# =============================================================================


class TestRunbookRegistryUpdatedEventContract:
    """EventType.RUNBOOK_REGISTRY_UPDATED 계약 검증."""

    def test_event_type_value(self):
        """RUNBOOK_REGISTRY_UPDATED의 값은 'runbook_registry_updated'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_REGISTRY_UPDATED == "runbook_registry_updated"

    def test_event_type_is_str_enum(self):
        """RUNBOOK_REGISTRY_UPDATED는 str 타입이다."""
        from selfhealing.services.event_bus.bus import EventType

        assert isinstance(EventType.RUNBOOK_REGISTRY_UPDATED, str)

    # NOTE: runbook_ 이벤트 개수 계약(== 10)은 test_runbook_event_types.py에서
    # 단일 책임으로 검증한다. DRY 원칙에 따라 여기서는 중복하지 않는다.


# =============================================================================
# 동작 검증 — RiskLevel 비교
# =============================================================================


class TestRiskLevelComparisonBehavior:
    """RiskLevel 비교 연산자 동작 검증."""

    def test_low_less_than_medium(self):
        """LOW < MEDIUM이다 (우선순위 순서)."""
        assert RiskLevel.LOW < RiskLevel.MEDIUM

    def test_medium_less_than_high(self):
        """MEDIUM < HIGH이다."""
        assert RiskLevel.MEDIUM < RiskLevel.HIGH

    def test_low_less_than_high(self):
        """LOW < HIGH이다."""
        assert RiskLevel.LOW < RiskLevel.HIGH

    def test_high_not_less_than_low(self):
        """HIGH는 LOW보다 작지 않다."""
        assert not (RiskLevel.HIGH < RiskLevel.LOW)

    def test_low_less_than_medium_order_int(self):
        """LOW < MEDIUM.order(int)이다 — 정수 비교 분기 동작 검증."""
        assert RiskLevel.LOW < RiskLevel.MEDIUM.order

    def test_high_greater_than_low_order_int(self):
        """HIGH > LOW.order(int)이다 — 정수 비교 분기 동작 검증."""
        assert RiskLevel.HIGH > RiskLevel.LOW.order

    def test_int_conversion_low(self):
        """int(RiskLevel.LOW)는 LOW.order와 같다 — __int__ 동작 검증."""
        assert int(RiskLevel.LOW) == RiskLevel.LOW.order

    def test_int_conversion_medium(self):
        """int(RiskLevel.MEDIUM)는 MEDIUM.order와 같다 — __int__ 동작 검증."""
        assert int(RiskLevel.MEDIUM) == RiskLevel.MEDIUM.order

    def test_int_conversion_high(self):
        """int(RiskLevel.HIGH)는 HIGH.order와 같다 — __int__ 동작 검증."""
        assert int(RiskLevel.HIGH) == RiskLevel.HIGH.order

    def test_sort_produces_low_medium_high_order(self):
        """sorted()로 정렬 시 LOW, MEDIUM, HIGH 순서가 된다."""
        shuffled = [RiskLevel.HIGH, RiskLevel.LOW, RiskLevel.MEDIUM]
        result = sorted(shuffled)
        assert result == [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]


# =============================================================================
# 동작 검증 — Runbook.validate()
# =============================================================================


class TestRunbookValidateBehavior:
    """Runbook.validate() 동작 검증."""

    def test_valid_runbook_returns_true_and_empty_error(self):
        """유효한 런북은 (True, '')을 반환한다."""
        rb = _make_minimal_runbook(steps=[_make_minimal_step("s1"), _make_minimal_step("s2")])
        valid, error = rb.validate()
        assert valid is True
        assert error == ""

    def test_empty_steps_returns_invalid(self):
        """steps가 비어있으면 (False, 에러 메시지)를 반환한다."""
        # Given
        from selfhealing.services.runbook.models import PatternCondition

        rb = Runbook(
            id="rb_empty",
            name="Empty",
            description="",
            trigger_condition=PatternCondition(),
            steps=[],
        )

        # When
        valid, error = rb.validate()

        # Then
        assert valid is False
        assert "step" in error.lower()

    def test_step_without_name_returns_invalid(self):
        """name이 없는 step이 있으면 (False, 에러 메시지)를 반환한다."""
        step = RunbookStep(name="", action="config.set")
        rb = _make_minimal_runbook(steps=[step])
        valid, error = rb.validate()
        assert valid is False
        assert "name" in error.lower()

    def test_step_without_action_returns_invalid(self):
        """action이 없는 step이 있으면 (False, 에러 메시지)를 반환한다."""
        step = RunbookStep(name="step_one", action="")
        rb = _make_minimal_runbook(steps=[step])
        valid, error = rb.validate()
        assert valid is False
        assert "action" in error.lower()

    def test_duplicate_step_names_returns_invalid(self):
        """step name이 중복되면 (False, 에러 메시지)를 반환한다."""
        steps = [
            RunbookStep(name="duplicate", action="config.set"),
            RunbookStep(name="duplicate", action="assert.metric"),
        ]
        rb = _make_minimal_runbook(steps=steps)
        valid, error = rb.validate()
        assert valid is False
        assert "중복" in error or "duplicate" in error.lower()

    def test_single_valid_step_returns_true(self):
        """단일 유효한 step이 있으면 (True, '')을 반환한다."""
        rb = _make_minimal_runbook(steps=[_make_minimal_step()])
        valid, error = rb.validate()
        assert valid is True
        assert error == ""


# =============================================================================
# 동작 검증 — RunbookRegistry.register()
# =============================================================================


class TestRunbookRegistryRegisterBehavior:
    """RunbookRegistry.register() 동작 검증."""

    def test_valid_runbook_registers_successfully(self):
        """유효한 런북 등록 시 get()으로 조회 가능해진다."""
        # Given
        registry = _make_registry()
        rb = _make_minimal_runbook("rb_001")

        # When
        registry.register(rb)

        # Then
        assert registry.get("rb_001") is not None

    def test_invalid_runbook_raises_value_error(self):
        """무효한 런북 등록 시 ValueError가 발생한다."""
        from selfhealing.services.runbook.models import PatternCondition

        registry = _make_registry()
        invalid_rb = Runbook(
            id="rb_invalid",
            name="Invalid",
            description="",
            trigger_condition=PatternCondition(),
            steps=[],  # 빈 steps — 무효
        )

        with pytest.raises(ValueError, match="Invalid runbook"):
            registry.register(invalid_rb)

    def test_duplicate_id_overwrites_existing_runbook(self):
        """동일 id로 재등록 시 기존 런북을 덮어쓴다."""
        # Given
        registry = _make_registry()
        rb_v1 = _make_minimal_runbook("rb_dup")
        rb_v1.description = "버전 1"
        rb_v2 = _make_minimal_runbook("rb_dup")
        rb_v2.description = "버전 2"

        # When
        registry.register(rb_v1)
        registry.register(rb_v2)

        # Then
        found = registry.get("rb_dup")
        assert found.description == "버전 2"

    def test_schema_validation_rejects_invalid_params(self):
        """Pydantic 스키마 위반 params를 가진 런북 등록 시 ValueError가 발생한다."""

        # Given: 스키마가 등록된 ActionPrimitiveRegistry
        class StrictParams(BaseModel):
            threshold: float

            @field_validator("threshold")
            @classmethod
            def threshold_must_be_positive(cls, v: float) -> float:
                if v <= 0:
                    raise ValueError("threshold는 양수여야 한다")
                return v

        primitive_reg = ActionPrimitiveRegistry()
        primitive_reg.register("assert.metric", lambda ctx: None, StrictParams)

        registry = _make_registry(primitive_registry=primitive_reg)

        steps = [RunbookStep(name="check", action="assert.metric", params={"threshold": -1.0})]
        rb = _make_minimal_runbook(steps=steps)

        # When / Then
        with pytest.raises(ValueError, match="params 검증 실패"):
            registry.register(rb)

    def test_template_variable_params_skip_schema_validation(self):
        """템플릿 변수({...})가 포함된 params는 정적 스키마 검증에서 제외된다."""

        class StrictParams(BaseModel):
            target_service: str

        primitive_reg = ActionPrimitiveRegistry()
        primitive_reg.register("notify.send", lambda ctx: None, StrictParams)

        registry = _make_registry(primitive_registry=primitive_reg)

        # 템플릿 변수 포함 — 정적 검증 대상 아님
        steps = [RunbookStep(name="alert", action="notify.send", params={"target_service": "{event_source}"})]
        rb = _make_minimal_runbook(steps=steps)

        # ValueError 없이 등록되어야 함
        registry.register(rb)
        assert registry.get(rb.id) is not None


# =============================================================================
# 동작 검증 — RunbookRegistry CRUD
# =============================================================================


class TestRunbookRegistryCrudBehavior:
    """RunbookRegistry CRUD 동작 검증."""

    def test_get_returns_none_when_not_registered(self):
        """등록되지 않은 runbook_id 조회 시 None을 반환한다."""
        registry = _make_registry()
        assert registry.get("nonexistent") is None

    def test_get_enabled_returns_only_enabled_runbooks(self):
        """get_enabled()는 enabled=True인 런북만 반환한다."""
        # Given
        registry = _make_registry()
        rb_on = _make_minimal_runbook("rb_on", enabled=True)
        rb_off = _make_minimal_runbook("rb_off", enabled=False)
        registry.register(rb_on)
        registry.register(rb_off)

        # When
        result = registry.get_enabled()

        # Then
        ids = [r.id for r in result]
        assert "rb_on" in ids
        assert "rb_off" not in ids

    def test_get_active_runbooks_equals_get_enabled(self):
        """get_active_runbooks()는 get_enabled()와 동일한 결과를 반환한다."""
        registry = _make_registry()
        rb = _make_minimal_runbook("rb_active")
        registry.register(rb)

        assert [r.id for r in registry.get_active_runbooks()] == [r.id for r in registry.get_enabled()]

    def test_get_all_includes_disabled_runbooks(self):
        """get_all()은 비활성화 런북도 포함하여 반환한다."""
        registry = _make_registry()
        rb_on = _make_minimal_runbook("rb_on", enabled=True)
        rb_off = _make_minimal_runbook("rb_off", enabled=False)
        registry.register(rb_on)
        registry.register(rb_off)

        result = registry.get_all()
        ids = [r.id for r in result]
        assert "rb_on" in ids
        assert "rb_off" in ids

    def test_set_enabled_false_disables_runbook(self):
        """set_enabled(False)는 런북을 비활성화한다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_disable"))

        result = registry.set_enabled("rb_disable", False)

        assert result is True
        assert registry.get("rb_disable").enabled is False

    def test_set_enabled_true_reactivates_runbook(self):
        """set_enabled(True)는 비활성화된 런북을 재활성화한다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_reactivate", enabled=False))

        registry.set_enabled("rb_reactivate", True)

        assert registry.get("rb_reactivate").enabled is True

    def test_set_enabled_returns_false_for_nonexistent_id(self):
        """존재하지 않는 runbook_id에 set_enabled 호출 시 False를 반환한다."""
        registry = _make_registry()
        result = registry.set_enabled("nonexistent", True)
        assert result is False

    def test_unregister_removes_runbook(self):
        """unregister() 후 get()은 None을 반환한다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_remove"))

        result = registry.unregister("rb_remove")

        assert result is True
        assert registry.get("rb_remove") is None

    def test_unregister_returns_false_for_nonexistent_id(self):
        """존재하지 않는 runbook_id 해제 시 False를 반환한다."""
        registry = _make_registry()
        assert registry.unregister("nonexistent") is False

    def test_get_by_tag_filters_correctly(self):
        """get_by_tag()는 해당 태그를 포함하는 런북만 반환한다."""
        registry = _make_registry()
        rb_db = _make_minimal_runbook("rb_db", tags=["database", "pool"])
        rb_net = _make_minimal_runbook("rb_net", tags=["network"])
        registry.register(rb_db)
        registry.register(rb_net)

        result = registry.get_by_tag("database")
        ids = [r.id for r in result]
        assert "rb_db" in ids
        assert "rb_net" not in ids

    def test_get_by_tag_returns_empty_list_when_no_match(self):
        """매칭되는 태그가 없으면 빈 리스트를 반환한다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_nag", tags=["other"]))

        assert registry.get_by_tag("nonexistent_tag") == []


# =============================================================================
# 동작 검증 — deepcopy 불변성
# =============================================================================


class TestRunbookRegistryImmutabilityBehavior:
    """RunbookRegistry get*() 반환값 불변성 검증 — 외부 수정이 내부 상태에 영향 없음."""

    def test_get_returns_deepcopy_mutation_does_not_affect_registry(self):
        """get()이 반환한 Runbook 수정이 레지스트리 내부에 영향 없다."""
        # Given
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_immut"))

        # When: 반환된 객체를 수정
        returned = registry.get("rb_immut")
        returned.name = "MUTATED"

        # Then: 내부 상태는 변경되지 않아야 함
        internal = registry.get("rb_immut")
        assert internal.name != "MUTATED"

    def test_get_enabled_returns_deepcopy_mutation_does_not_affect_registry(self):
        """get_enabled()가 반환한 리스트 수정이 내부에 영향 없다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_en", enabled=True))

        enabled_list = registry.get_enabled()
        enabled_list[0].enabled = False  # 반환 객체 수정

        # 내부 상태는 여전히 enabled=True
        assert registry.get("rb_en").enabled is True

    def test_get_all_returns_independent_copies(self):
        """get_all()이 반환한 개별 Runbook 수정이 내부에 영향 없다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_all"))

        all_list = registry.get_all()
        all_list[0].version = 999

        assert registry.get("rb_all").version != 999

    def test_get_by_tag_returns_deepcopy(self):
        """get_by_tag()가 반환한 Runbook 수정이 내부에 영향 없다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_tag", tags=["alpha"]))

        tag_list = registry.get_by_tag("alpha")
        tag_list[0].priority = 1

        assert registry.get("rb_tag").priority != 1


# =============================================================================
# 동작 검증 — 분산 동기화 (_on_registry_updated)
# =============================================================================


class TestRunbookRegistryDistributedSyncBehavior:
    """RunbookRegistry 분산 동기화 이벤트 처리 동작 검증."""

    def _make_event(self, source: str, data: dict) -> Any:
        """테스트용 SelfHealingEvent 스텁 생성."""
        event = MagicMock()
        event.source = source
        event.data = data
        return event

    def test_self_emitted_event_is_ignored(self):
        """source='runbook_registry'인 이벤트는 무시된다 (자기 이벤트 무한 루프 방지)."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_self"))
        registry._runbooks["rb_self"].enabled = True

        event = self._make_event("runbook_registry", {"action": "set_enabled", "runbook_id": "rb_self", "enabled": False})
        registry._on_registry_updated(event)

        # 자기 이벤트는 무시 → enabled는 변하지 않아야 함
        assert registry._runbooks["rb_self"].enabled is True

    def test_remote_set_enabled_false_syncs_local_state(self):
        """다른 노드의 set_enabled=False 이벤트 수신 시 로컬 enabled가 동기화된다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_remote"))

        event = self._make_event("other_node", {"action": "set_enabled", "runbook_id": "rb_remote", "enabled": False})
        registry._on_registry_updated(event)

        assert registry._runbooks["rb_remote"].enabled is False

    def test_remote_unregister_removes_local_runbook(self):
        """다른 노드의 unregister 이벤트 수신 시 로컬에서 런북이 제거된다."""
        registry = _make_registry()
        registry.register(_make_minimal_runbook("rb_del"))

        event = self._make_event("other_node", {"action": "unregister", "runbook_id": "rb_del"})
        registry._on_registry_updated(event)

        assert "rb_del" not in registry._runbooks

    def test_remote_unregister_for_nonexistent_id_does_not_raise(self):
        """등록되지 않은 runbook_id의 unregister 이벤트를 수신해도 예외가 없다."""
        registry = _make_registry()

        event = self._make_event("other_node", {"action": "unregister", "runbook_id": "nonexistent"})
        registry._on_registry_updated(event)  # 예외 없이 실행 확인


# =============================================================================
# 동작 검증 — 스레드 안전성
# =============================================================================


class TestRunbookRegistryThreadSafetyBehavior:
    """RunbookRegistry 멀티스레드 동시 접근 시 데이터 정합성 검증."""

    def test_concurrent_register_does_not_corrupt_registry(self):
        """여러 스레드가 동시에 register()를 호출해도 데이터 정합성이 유지된다."""
        # Given
        registry = _make_registry()
        errors: list[str] = []

        def register_runbook(i: int) -> None:
            try:
                registry.register(_make_minimal_runbook(f"rb_{i:04d}"))
            except Exception as e:
                errors.append(str(e))

        # When: 30개 스레드 동시 등록
        threads = [threading.Thread(target=register_runbook, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Then: 에러 없이 30개 등록
        assert len(errors) == 0
        assert len(registry._runbooks) == 30

    def test_concurrent_get_and_set_enabled_does_not_raise(self):
        """get()과 set_enabled() 동시 호출 시 예외가 없다."""
        registry = _make_registry()
        for i in range(5):
            registry.register(_make_minimal_runbook(f"rb_cs_{i}"))

        errors: list[str] = []

        def toggle(i: int) -> None:
            try:
                registry.set_enabled(f"rb_cs_{i % 5}", i % 2 == 0)
            except Exception as e:
                errors.append(str(e))

        def read(i: int) -> None:
            try:
                registry.get(f"rb_cs_{i % 5}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=toggle if i % 2 == 0 else read, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# =============================================================================
# 동작 검증 — ActionPrimitiveRegistry
# =============================================================================


class TestActionPrimitiveRegistryBehavior:
    """ActionPrimitiveRegistry 동작 검증."""

    def test_register_and_get_handler(self):
        """register()로 등록한 handler를 get()으로 조회할 수 있다."""
        reg = ActionPrimitiveRegistry()

        def handler(ctx):
            return None

        reg.register("custom.action", handler)

        assert reg.get("custom.action") is handler

    def test_get_returns_none_for_unregistered_action(self):
        """등록되지 않은 action 이름 조회 시 None을 반환한다."""
        reg = ActionPrimitiveRegistry()
        assert reg.get("nonexistent") is None

    def test_get_schema_returns_none_when_no_schema_registered(self):
        """스키마 없이 등록된 action의 get_schema()는 None을 반환한다."""
        reg = ActionPrimitiveRegistry()
        reg.register("action.no_schema", lambda ctx: None)
        assert reg.get_schema("action.no_schema") is None

    def test_register_with_schema_and_retrieve(self):
        """params_schema와 함께 등록하면 get_schema()로 조회할 수 있다."""

        class MyParams(BaseModel):
            key: str

        reg = ActionPrimitiveRegistry()
        reg.register("config.set", lambda ctx: None, MyParams)

        assert reg.get_schema("config.set") is MyParams

    def test_overwrite_handler_replaces_previous(self):
        """동일 action_name으로 재등록하면 handler가 교체된다."""
        reg = ActionPrimitiveRegistry()

        def handler_v1(ctx):
            return "v1"

        def handler_v2(ctx):
            return "v2"

        reg.register("my.action", handler_v1)
        reg.register("my.action", handler_v2)

        assert reg.get("my.action") is handler_v2

    def test_create_action_returns_action_with_correct_target(self):
        """create_action()이 반환하는 Action.target에 런북 ID가 포함된다."""
        from selfhealing.services.saga.models import StepResult

        reg = ActionPrimitiveRegistry()
        reg.register("notify.send", lambda ctx: StepResult(success=True))

        ctx = RunbookStepContext(
            runbook_id="rb_notify",
            step_name="notify",
            params={},
            prev_results={},
            execution_id="exec-001",
        )

        action = reg.create_action("notify.send", ctx)

        assert "rb_notify" in action.target

    def test_create_action_execute_fn_calls_handler_with_context(self):
        """create_action()의 execute_fn 호출 시 핸들러가 context와 함께 실행된다."""
        received_contexts: list[Any] = []

        def capture_handler(ctx: RunbookStepContext) -> None:
            received_contexts.append(ctx)

        reg = ActionPrimitiveRegistry()
        reg.register("test.action", capture_handler)

        ctx = RunbookStepContext(
            runbook_id="rb_cap",
            step_name="step",
            params={"x": 1},
            prev_results={},
            execution_id="exec-002",
        )

        action = reg.create_action("test.action", ctx)
        action.execute_fn()  # 클로저 실행

        assert len(received_contexts) == 1
        assert received_contexts[0] is ctx


# =============================================================================
# 동작 검증 — 스키마 검증 심층
# =============================================================================


class TestActionPrimitiveRegistrySchemaValidationBehavior:
    """_validate_all_step_params() 심층 동작 검증."""

    def test_no_schema_registered_skips_validation(self):
        """스키마가 등록되지 않은 action은 파라미터 검증을 건너뛴다."""
        registry = _make_registry()
        # 스키마 없는 action
        steps = [RunbookStep(name="step", action="db.kill_idle", params={"threshold": -999})]
        rb = _make_minimal_runbook(steps=steps)

        # 예외 없이 등록되어야 함
        registry.register(rb)
        assert registry.get(rb.id) is not None

    def test_missing_primitive_registry_skips_schema_validation(self):
        """primitive_registry가 None이면 스키마 검증을 건너뛴다."""
        registry = _make_registry(primitive_registry=None)

        class StrictParams(BaseModel):
            required_field: str  # 필수 필드

        steps = [RunbookStep(name="step", action="config.set", params={})]  # required_field 없음
        rb = _make_minimal_runbook(steps=steps)

        # primitive_registry가 없으므로 예외 없이 등록
        registry.register(rb)

    def test_static_params_validation_triggers_for_non_template_values(self):
        """템플릿 변수가 없는 정적 파라미터는 스키마 검증을 통과해야 한다."""

        class PositiveThreshold(BaseModel):
            threshold: float

            @field_validator("threshold")
            @classmethod
            def must_be_positive(cls, v: float) -> float:
                if v <= 0:
                    raise ValueError("양수여야 한다")
                return v

        primitive_reg = ActionPrimitiveRegistry()
        primitive_reg.register("assert.metric", lambda ctx: None, PositiveThreshold)
        registry = _make_registry(primitive_registry=primitive_reg)

        # 유효한 static param
        valid_steps = [RunbookStep(name="check", action="assert.metric", params={"threshold": 0.8})]
        rb_valid = _make_minimal_runbook("rb_valid_param", steps=valid_steps)
        registry.register(rb_valid)  # 예외 없음

        # 무효한 static param
        invalid_steps = [RunbookStep(name="check", action="assert.metric", params={"threshold": -0.1})]
        rb_invalid = _make_minimal_runbook("rb_invalid_param", steps=invalid_steps)
        with pytest.raises(ValueError):
            registry.register(rb_invalid)


# =============================================================================
# 계약 검증 — _TEMPLATE_VAR_RE 정규식
# =============================================================================


class TestTemplateVarRegexContract:
    """_TEMPLATE_VAR_RE 정규식 패턴 계약 검증."""

    def test_matches_simple_variable(self):
        """'{event_source}' 형태의 단순 변수명이 매칭된다."""
        assert _TEMPLATE_VAR_RE.search("{event_source}") is not None

    def test_matches_single_char_variable(self):
        """'{x}' 같은 단일 문자 변수명이 매칭된다."""
        assert _TEMPLATE_VAR_RE.search("{x}") is not None

    def test_matches_underscore_prefix(self):
        """'{_private}' 같은 밑줄 시작 변수명이 매칭된다."""
        assert _TEMPLATE_VAR_RE.search("{_private}") is not None

    def test_does_not_match_json_string(self):
        """'{\"key\": \"value\"}' 같은 JSON 문자열은 매칭되지 않는다."""
        assert _TEMPLATE_VAR_RE.search('{"key": "value"}') is None

    def test_does_not_match_numeric_start(self):
        """'{123abc}' 같은 숫자 시작 패턴은 매칭되지 않는다."""
        assert _TEMPLATE_VAR_RE.search("{123abc}") is None

    def test_does_not_match_empty_braces(self):
        """'{}'(빈 중괄호)는 매칭되지 않는다."""
        assert _TEMPLATE_VAR_RE.search("{}") is None

    def test_matches_variable_within_string(self):
        """문자열 중간에 포함된 변수도 매칭된다."""
        assert _TEMPLATE_VAR_RE.search("Service {service_name} is down") is not None


# =============================================================================
# 동작 검증 — register() enabled 복원
# =============================================================================


class TestRunbookRegistryEnabledRestorationBehavior:
    """register() 시점에 StateBackend 캐시에서 enabled 상태를 복원하는 동작 검증."""

    def test_register_restores_disabled_state_from_cache(self):
        """캐시에 False로 저장된 런북을 register()하면 enabled=False로 복원된다."""
        # Given: 이전에 set_enabled(False)된 상태가 캐시에 있음
        registry = _make_registry(cached_enabled_map={"rb_killed": False})
        rb = _make_minimal_runbook("rb_killed", enabled=True)

        # When
        registry.register(rb)

        # Then: Kill Switch 상태가 재시작 후에도 유지됨
        assert registry._runbooks["rb_killed"].enabled is False

    def test_register_restores_enabled_state_from_cache(self):
        """캐시에 True로 저장된 런북을 register()하면 enabled=True로 유지된다."""
        registry = _make_registry(cached_enabled_map={"rb_alive": True})
        rb = _make_minimal_runbook("rb_alive", enabled=False)

        registry.register(rb)

        assert registry._runbooks["rb_alive"].enabled is True

    def test_register_without_cache_entry_keeps_default(self):
        """캐시에 없는 런북은 Runbook 정의의 enabled 기본값을 유지한다."""
        registry = _make_registry(cached_enabled_map={})
        rb = _make_minimal_runbook("rb_new", enabled=True)

        registry.register(rb)

        # 캐시에 없으므로 원래 값 유지
        assert registry._runbooks["rb_new"].enabled is True

    def test_get_returns_restored_enabled_state(self):
        """get()으로 조회한 런북은 캐시에서 복원된 enabled 상태를 반영한다."""
        registry = _make_registry(cached_enabled_map={"rb_check": False})
        rb = _make_minimal_runbook("rb_check", enabled=True)

        registry.register(rb)

        found = registry.get("rb_check")
        assert found.enabled is False

    def test_get_enabled_excludes_cache_disabled_runbook(self):
        """캐시에서 disabled로 복원된 런북은 get_enabled()에 포함되지 않는다."""
        registry = _make_registry(cached_enabled_map={"rb_off": False})
        rb_off = _make_minimal_runbook("rb_off", enabled=True)
        rb_on = _make_minimal_runbook("rb_on", enabled=True)

        registry.register(rb_off)
        registry.register(rb_on)

        ids = [r.id for r in registry.get_enabled()]
        assert "rb_off" not in ids
        assert "rb_on" in ids


# =============================================================================
# 동작 검증 — 템플릿 감지 정밀화
# =============================================================================


class TestTemplateDetectionBehavior:
    """_validate_all_step_params()의 템플릿 변수 감지 정밀화 동작 검증."""

    def test_json_params_not_treated_as_template(self):
        """JSON 문자열 params는 템플릿으로 오인되지 않고 스키마 검증 대상이 된다."""

        class ConfigParams(BaseModel):
            config_json: str

        primitive_reg = ActionPrimitiveRegistry()
        primitive_reg.register("config.apply", lambda ctx: None, ConfigParams)
        registry = _make_registry(primitive_registry=primitive_reg)

        # JSON 문자열은 {로 시작하지만 템플릿 변수가 아님 → 정적 검증 대상
        steps = [
            RunbookStep(
                name="apply",
                action="config.apply",
                params={"config_json": '{"max_connections": 100}'},
            )
        ]
        rb = _make_minimal_runbook("rb_json", steps=steps)

        # Pydantic 스키마 통과 (config_json은 str 타입 → 유효)
        registry.register(rb)
        assert registry.get("rb_json") is not None

    def test_real_template_variable_skips_validation(self):
        """실제 템플릿 변수 '{event_source}'는 정적 검증에서 제외된다."""

        class StrictParams(BaseModel):
            target: str
            threshold: float

        primitive_reg = ActionPrimitiveRegistry()
        primitive_reg.register("notify.alert", lambda ctx: None, StrictParams)
        registry = _make_registry(primitive_registry=primitive_reg)

        # target은 템플릿 → 제외, threshold만 정적 검증
        steps = [
            RunbookStep(
                name="alert",
                action="notify.alert",
                params={"target": "{event_source}", "threshold": 0.5},
            )
        ]
        rb = _make_minimal_runbook("rb_tpl", steps=steps)

        # threshold=0.5은 유효하므로 등록 성공
        registry.register(rb)
        assert registry.get("rb_tpl") is not None
