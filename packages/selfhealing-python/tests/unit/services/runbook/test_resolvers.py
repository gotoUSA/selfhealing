"""
DotPathResolver, resolve_params 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.resolvers

계약 검증 클래스 (Test*Contract):
    - TestDotPathResolverSupportedRootContract — 지원하는 루트 접두사 계약

동작 검증 클래스 (Test*Behavior):
    - TestDotPathResolverTriggerBehavior  — ${trigger.*} 경로 해석
    - TestDotPathResolverStepBehavior     — ${step.*} 경로 해석
    - TestDotPathResolverEdgeBehavior     — None/빈값/미지원 루트 처리
    - TestResolveParamsBehavior           — resolve_params 함수 동작
    - TestResolveParamsDataImmutabilityBehavior — 원본 params 불변성
"""

from __future__ import annotations

import pytest

from selfhealing.services.runbook.execution_models import (
    RunbookExecutionContext,
    RunbookStepResult,
)
from selfhealing.services.runbook.resolvers import (
    DotPathResolver,
    ParamResolver,
    resolve_params,
)


# =============================================================================
# 공통 픽스처
# =============================================================================


def _make_ctx(
    trigger_event: dict | None = None,
    step_results: dict | None = None,
) -> RunbookExecutionContext:
    return RunbookExecutionContext(
        execution_id="e1",
        runbook_id="rb1",
        namespace="ns",
        trigger_event=trigger_event or {},
        step_results=step_results or {},
    )


def _make_step_result(result_data: dict) -> RunbookStepResult:
    return RunbookStepResult(
        step_name="prev",
        action_name="some.action",
        success=True,
        executed=True,
        result_data=result_data,
    )


# =============================================================================
# 계약 검증 — DotPathResolver 지원 루트
# =============================================================================


class TestDotPathResolverSupportedRootContract:
    """DotPathResolver가 지원하는 루트 접두사 계약."""

    def test_trigger_root_resolves_trigger_event(self):
        """§9.3 설계 계약: 'trigger' 루트는 trigger_event에서 값을 읽는다."""
        # Given
        ctx = _make_ctx(trigger_event={"service_name": "payment"})
        resolver = DotPathResolver()
        # When / Then
        assert resolver.resolve("trigger.service_name", ctx) == "payment"

    def test_step_root_resolves_step_result(self):
        """§9.3 설계 계약: 'step' 루트는 step_results에서 값을 읽는다."""
        # Given
        ctx = _make_ctx(step_results={"check": _make_step_result({"error_rate": 0.9})})
        resolver = DotPathResolver()
        # When / Then
        assert resolver.resolve("step.check.result_data.error_rate", ctx) == pytest.approx(0.9)

    def test_unsupported_root_returns_none(self):
        """§9.3 설계 계약: 미지원 루트는 None을 반환한다."""
        # Given
        ctx = _make_ctx()
        resolver = DotPathResolver()
        # When / Then
        assert resolver.resolve("env.MY_VAR", ctx) is None


# =============================================================================
# 동작 검증 — trigger 경로
# =============================================================================


class TestDotPathResolverTriggerBehavior:
    """${trigger.*} 경로 해석 동작 검증."""

    def test_resolves_nested_trigger_field(self):
        """중첩된 trigger 필드를 정확히 읽어야 한다."""
        # Given
        ctx = _make_ctx(trigger_event={"meta": {"region": "ap-northeast-2"}})
        resolver = DotPathResolver()
        # When / Then
        assert resolver.resolve("trigger.meta.region", ctx) == "ap-northeast-2"

    def test_returns_none_for_missing_trigger_field(self):
        """trigger에 없는 필드는 None을 반환해야 한다."""
        ctx = _make_ctx(trigger_event={"service_name": "payment"})
        resolver = DotPathResolver()
        assert resolver.resolve("trigger.nonexistent_field", ctx) is None

    def test_returns_list_type_from_trigger(self):
        """trigger에서 list 타입을 타입 손실 없이 반환해야 한다."""
        ctx = _make_ctx(trigger_event={"affected_ids": [1, 2, 3]})
        resolver = DotPathResolver()
        result = resolver.resolve("trigger.affected_ids", ctx)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_returns_float_type_from_trigger(self):
        """trigger에서 float를 str로 변환하지 않고 그대로 반환해야 한다."""
        ctx = _make_ctx(trigger_event={"threshold": 0.85})
        resolver = DotPathResolver()
        result = resolver.resolve("trigger.threshold", ctx)
        assert result == pytest.approx(0.85)
        assert isinstance(result, float)


# =============================================================================
# 동작 검증 — step 경로
# =============================================================================


class TestDotPathResolverStepBehavior:
    """${step.*} 경로 해석 동작 검증."""

    def test_resolves_step_result_data_field(self):
        """step.result_data 내 필드를 정확히 읽어야 한다."""
        # Given
        ctx = _make_ctx(step_results={"check": _make_step_result({"current_error_rate": 0.75})})
        resolver = DotPathResolver()
        # When / Then
        assert resolver.resolve("step.check.result_data.current_error_rate", ctx) == pytest.approx(0.75)

    def test_returns_none_for_nonexistent_step(self):
        """존재하지 않는 step 이름은 None을 반환해야 한다."""
        ctx = _make_ctx()
        resolver = DotPathResolver()
        assert resolver.resolve("step.nonexistent.result_data.value", ctx) is None

    def test_resolves_step_success_field(self):
        """step.success 필드(bool)를 타입 손실 없이 반환해야 한다."""
        ctx = _make_ctx(step_results={"s1": _make_step_result({})})
        resolver = DotPathResolver()
        result = resolver.resolve("step.s1.success", ctx)
        assert result is True

    def test_resolves_deeply_nested_step_result_data(self):
        """깊이 중첩된 result_data 경로를 정확히 읽어야 한다."""
        ctx = _make_ctx(step_results={"collect": _make_step_result({"metrics": {"p99_latency_ms": 350}})})
        resolver = DotPathResolver()
        assert resolver.resolve("step.collect.result_data.metrics.p99_latency_ms", ctx) == 350


# =============================================================================
# 동작 검증 — edge cases
# =============================================================================


class TestDotPathResolverEdgeBehavior:
    """None/빈값/미지원 루트 처리 동작 검증."""

    def test_empty_expression_returns_none(self):
        """빈 표현식 문자열은 None을 반환해야 한다."""
        ctx = _make_ctx()
        resolver = DotPathResolver()
        assert resolver.resolve("", ctx) is None

    def test_traverse_none_midpath_returns_none(self):
        """중간 경로에 None이 있으면 None을 반환해야 한다."""
        ctx = _make_ctx(trigger_event={"parent": None})
        resolver = DotPathResolver()
        assert resolver.resolve("trigger.parent.child", ctx) is None

    def test_traverse_dict_missing_key_returns_none(self):
        """dict에 없는 키는 None을 반환해야 한다."""
        ctx = _make_ctx(trigger_event={"a": {"b": 1}})
        resolver = DotPathResolver()
        assert resolver.resolve("trigger.a.c", ctx) is None

    def test_only_root_token_trigger_returns_event_dict(self):
        """'trigger'만 경로로 입력하면 전체 trigger_event dict를 반환해야 한다."""
        event = {"service_name": "payment"}
        ctx = _make_ctx(trigger_event=event)
        resolver = DotPathResolver()
        assert resolver.resolve("trigger", ctx) == event

    def test_step_root_only_returns_none(self):
        """'step'만 있고 step 이름이 없으면 None을 반환해야 한다."""
        ctx = _make_ctx()
        resolver = DotPathResolver()
        assert resolver.resolve("step", ctx) is None


# =============================================================================
# 동작 검증 — resolve_params 함수
# =============================================================================


class TestResolveParamsBehavior:
    """resolve_params 함수 동작 검증."""

    def test_replaces_variable_template_with_resolved_value(self):
        """${...} 패턴을 DotPathResolver로 치환해야 한다."""
        # Given
        ctx = _make_ctx(trigger_event={"service_name": "payment"})
        params = {"target": "${trigger.service_name}"}
        # When
        resolved = resolve_params(params, ctx)
        # Then
        assert resolved["target"] == "payment"

    def test_leaves_literal_values_unchanged(self):
        """${} 패턴이 없는 값은 리터럴로 그대로 전달되어야 한다."""
        ctx = _make_ctx()
        params = {"threshold": 0.9, "name": "fixed_name", "enabled": True}
        resolved = resolve_params(params, ctx)
        assert resolved == {"threshold": 0.9, "name": "fixed_name", "enabled": True}

    def test_partial_template_not_substituted(self):
        """'prefix_${...}_suffix' 같은 부분 치환은 리터럴로 그대로 전달되어야 한다."""
        ctx = _make_ctx(trigger_event={"val": "X"})
        params = {"label": "prefix_${trigger.val}_suffix"}
        resolved = resolve_params(params, ctx)
        # 전체 값이 "${...}" 형태가 아니므로 치환하지 않는다
        assert resolved["label"] == "prefix_${trigger.val}_suffix"

    def test_mixed_params_resolve_selectively(self):
        """${} 패턴 값만 치환하고 나머지는 그대로 보존해야 한다."""
        ctx = _make_ctx(trigger_event={"ns": "prod"})
        params = {
            "namespace": "${trigger.ns}",
            "timeout": 30,
            "label": "static",
        }
        resolved = resolve_params(params, ctx)
        assert resolved["namespace"] == "prod"
        assert resolved["timeout"] == 30
        assert resolved["label"] == "static"

    def test_preserves_python_type_from_step_result(self):
        """step 결과에서 float 타입을 str로 변환하지 않고 그대로 반환해야 한다."""
        ctx = _make_ctx(step_results={"check": _make_step_result({"ratio": 0.75})})
        params = {"threshold": "${step.check.result_data.ratio}"}
        resolved = resolve_params(params, ctx)
        assert resolved["threshold"] == pytest.approx(0.75)
        assert isinstance(resolved["threshold"], float)

    def test_nonexistent_variable_resolves_to_none(self):
        """존재하지 않는 변수는 None으로 치환되어야 한다."""
        ctx = _make_ctx()
        params = {"service": "${trigger.missing_field}"}
        resolved = resolve_params(params, ctx)
        assert resolved["service"] is None


# =============================================================================
# 동작 검증 — 원본 params 불변성
# =============================================================================


class TestResolveParamsDataImmutabilityBehavior:
    """resolve_params가 원본 params를 변경하지 않는지 검증."""

    def test_original_params_unchanged_after_resolve(self):
        """resolve_params 호출 후 원본 params dict가 변경되지 않아야 한다."""
        # Given
        ctx = _make_ctx(trigger_event={"svc": "auth"})
        original_params = {"service": "${trigger.svc}", "timeout": 60}
        params_copy = dict(original_params)

        # When
        _ = resolve_params(original_params, ctx)

        # Then
        assert original_params == params_copy  # 원본 불변


# =============================================================================
# 동작 검증 — ParamResolver Protocol 구현체 검증
# =============================================================================


class TestParamResolverProtocolBehavior:
    """DotPathResolver가 ParamResolver Protocol을 구현하는지 검증."""

    def test_dot_path_resolver_satisfies_param_resolver_protocol(self):
        """DotPathResolver는 ParamResolver Protocol을 만족해야 한다."""
        resolver = DotPathResolver()
        assert isinstance(resolver, ParamResolver)
