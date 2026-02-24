"""
ConditionOperator, LabelFilter, MetricCondition, EventCondition, PatternCondition
데이터 모델의 계약값 및 동작 검증.

테스트 대상: selfhealing.services.runbook.models
"""

from __future__ import annotations

import pytest

from selfhealing.services.runbook.models import (
    ConditionOperator,
    EventCondition,
    LabelFilter,
    MatchResult,
    MatchSelectionResult,
    MetricCondition,
    PatternCondition,
)


# =============================================================================
# Fixtures & Helpers
# =============================================================================


def _make_metric_condition(
    metric_name: str = "error_rate",
    operator: ConditionOperator = ConditionOperator.GTE,
    threshold: float | str | list[str] = 0.05,
    **kwargs,
) -> MetricCondition:
    return MetricCondition(
        metric_name=metric_name,
        operator=operator,
        threshold=threshold,
        **kwargs,
    )


def _make_event_condition(
    event_type: str = "circuit_breaker_opened",
    source_filter: str | None = None,
    data_filter: dict | None = None,
) -> EventCondition:
    return EventCondition(
        event_type=event_type,
        source_filter=source_filter,
        data_filter=data_filter or {},
    )


def _make_pattern_condition(
    metric_conditions: list[MetricCondition] | None = None,
    event_conditions: list[EventCondition] | None = None,
    min_duration_seconds: int = 0,
) -> PatternCondition:
    return PatternCondition(
        metric_conditions=metric_conditions or [],
        event_conditions=event_conditions or [],
        min_duration_seconds=min_duration_seconds,
    )


# =============================================================================
# Contract Tests — 설계 문서에 명시된 구체적 값 검증
# =============================================================================


class TestConditionOperatorContract:
    """ConditionOperator enum 문자열 값 계약 검증."""

    def test_gt_value(self):
        """GT 연산자의 값은 'gt'."""
        assert ConditionOperator.GT == "gt"

    def test_gte_value(self):
        """GTE 연산자의 값은 'gte'."""
        assert ConditionOperator.GTE == "gte"

    def test_lt_value(self):
        """LT 연산자의 값은 'lt'."""
        assert ConditionOperator.LT == "lt"

    def test_lte_value(self):
        """LTE 연산자의 값은 'lte'."""
        assert ConditionOperator.LTE == "lte"

    def test_eq_value(self):
        """EQ 연산자의 값은 'eq'."""
        assert ConditionOperator.EQ == "eq"

    def test_neq_value(self):
        """NEQ 연산자의 값은 'neq'."""
        assert ConditionOperator.NEQ == "neq"

    def test_in_value(self):
        """IN 연산자의 값은 'in'."""
        assert ConditionOperator.IN == "in"

    def test_contains_value(self):
        """CONTAINS 연산자의 값은 'contains'."""
        assert ConditionOperator.CONTAINS == "contains"

    def test_regex_value(self):
        """REGEX 연산자의 값은 'regex'."""
        assert ConditionOperator.REGEX == "regex"

    def test_operator_count(self):
        """ConditionOperator는 9개 멤버를 가진다."""
        assert len(ConditionOperator) == 9

    def test_all_operators_are_str_enum(self):
        """모든 연산자는 str 타입이다."""
        for op in ConditionOperator:
            assert isinstance(op, str)


class TestMetricConditionDefaultContract:
    """MetricCondition 기본값 계약 검증."""

    def test_labels_default_empty_list(self):
        """labels 기본값은 빈 리스트."""
        mc = _make_metric_condition()
        assert mc.labels == []

    def test_clear_threshold_default_none(self):
        """clear_threshold 기본값은 None."""
        mc = _make_metric_condition()
        assert mc.clear_threshold is None

    def test_grace_period_seconds_default_zero(self):
        """grace_period_seconds 기본값은 0."""
        mc = _make_metric_condition()
        assert mc.grace_period_seconds == 0


class TestEventConditionDefaultContract:
    """EventCondition 기본값 계약 검증."""

    def test_source_filter_default_none(self):
        """source_filter 기본값은 None."""
        ec = _make_event_condition()
        assert ec.source_filter is None

    def test_data_filter_default_empty_dict(self):
        """data_filter 기본값은 빈 딕셔너리."""
        ec = _make_event_condition()
        assert ec.data_filter == {}


class TestPatternConditionDefaultContract:
    """PatternCondition 기본값 계약 검증."""

    def test_metric_conditions_default_empty(self):
        """metric_conditions 기본값은 빈 리스트."""
        pc = PatternCondition()
        assert pc.metric_conditions == []

    def test_event_conditions_default_empty(self):
        """event_conditions 기본값은 빈 리스트."""
        pc = PatternCondition()
        assert pc.event_conditions == []

    def test_min_duration_seconds_default_zero(self):
        """min_duration_seconds 기본값은 0."""
        pc = PatternCondition()
        assert pc.min_duration_seconds == 0


class TestMatchResultDefaultContract:
    """MatchResult 기본값 계약 검증."""

    def test_event_context_default_empty_dict(self):
        """event_context 기본값은 빈 딕셔너리."""
        mr = MatchResult(
            runbook_id="r1",
            confidence=0.9,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        assert mr.event_context == {}

    def test_runner_up_runbook_ids_default_empty_list(self):
        """runner_up_runbook_ids 기본값은 빈 리스트."""
        mr = MatchResult(
            runbook_id="r1",
            confidence=0.9,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        assert mr.runner_up_runbook_ids == []


class TestRunbookSkippedCooldownEventContract:
    """RUNBOOK_SKIPPED_COOLDOWN EventType 계약값 검증."""

    def test_skipped_cooldown_value(self):
        """RUNBOOK_SKIPPED_COOLDOWN의 값은 'runbook_skipped_cooldown'."""
        from selfhealing.services.event_bus.bus import EventType

        assert EventType.RUNBOOK_SKIPPED_COOLDOWN == "runbook_skipped_cooldown"

    def test_skipped_cooldown_is_str(self):
        """RUNBOOK_SKIPPED_COOLDOWN은 str 타입."""
        from selfhealing.services.event_bus.bus import EventType

        assert isinstance(EventType.RUNBOOK_SKIPPED_COOLDOWN, str)


# =============================================================================
# Behavior Tests — 동작 검증
# =============================================================================


class TestMetricConditionEvaluateBehavior:
    """MetricCondition.evaluate() 동작 검증."""

    def test_gt_above_threshold_returns_true(self):
        """GT 연산자: 현재값이 임계치 초과이면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.GT, threshold=5.0)
        assert mc.evaluate(5.1) is True

    def test_gt_equal_threshold_returns_false(self):
        """GT 연산자: 현재값이 임계치와 같으면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.GT, threshold=5.0)
        assert mc.evaluate(5.0) is False

    def test_gt_below_threshold_returns_false(self):
        """GT 연산자: 현재값이 임계치 미만이면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.GT, threshold=5.0)
        assert mc.evaluate(4.9) is False

    def test_gte_equal_threshold_returns_true(self):
        """GTE 연산자: 현재값이 임계치와 같으면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.GTE, threshold=5.0)
        assert mc.evaluate(5.0) is True

    def test_gte_below_threshold_returns_false(self):
        """GTE 연산자: 현재값이 임계치 미만이면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.GTE, threshold=5.0)
        assert mc.evaluate(4.99) is False

    def test_lt_below_threshold_returns_true(self):
        """LT 연산자: 현재값이 임계치 미만이면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.LT, threshold=5.0)
        assert mc.evaluate(4.9) is True

    def test_lt_equal_threshold_returns_false(self):
        """LT 연산자: 현재값이 임계치와 같으면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.LT, threshold=5.0)
        assert mc.evaluate(5.0) is False

    def test_lte_equal_threshold_returns_true(self):
        """LTE 연산자: 현재값이 임계치와 같으면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.LTE, threshold=5.0)
        assert mc.evaluate(5.0) is True

    def test_eq_same_value_returns_true(self):
        """EQ 연산자: 값이 같으면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.EQ, threshold=5.0)
        assert mc.evaluate(5.0) is True

    def test_eq_different_value_returns_false(self):
        """EQ 연산자: 값이 다르면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.EQ, threshold=5.0)
        assert mc.evaluate(5.1) is False

    def test_neq_different_value_returns_true(self):
        """NEQ 연산자: 값이 다르면 True."""
        mc = _make_metric_condition(operator=ConditionOperator.NEQ, threshold=5.0)
        assert mc.evaluate(5.1) is True

    def test_neq_same_value_returns_false(self):
        """NEQ 연산자: 값이 같으면 False."""
        mc = _make_metric_condition(operator=ConditionOperator.NEQ, threshold=5.0)
        assert mc.evaluate(5.0) is False

    def test_in_matching_value_returns_true(self):
        """IN 연산자: 값이 리스트에 포함되면 True."""
        mc = _make_metric_condition(
            operator=ConditionOperator.IN,
            threshold=["5xx", "4xx"],
        )
        assert mc.evaluate("5xx") is True

    def test_in_non_matching_value_returns_false(self):
        """IN 연산자: 값이 리스트에 없으면 False."""
        mc = _make_metric_condition(
            operator=ConditionOperator.IN,
            threshold=["5xx", "4xx"],
        )
        assert mc.evaluate("2xx") is False

    def test_contains_substring_returns_true(self):
        """CONTAINS 연산자: 문자열이 포함되면 True."""
        mc = _make_metric_condition(
            operator=ConditionOperator.CONTAINS,
            threshold="error",
        )
        assert mc.evaluate("timeout_error_rate") is True

    def test_contains_no_substring_returns_false(self):
        """CONTAINS 연산자: 문자열 미포함이면 False."""
        mc = _make_metric_condition(
            operator=ConditionOperator.CONTAINS,
            threshold="error",
        )
        assert mc.evaluate("success_rate") is False

    def test_regex_matching_pattern_returns_true(self):
        """REGEX 연산자: 정규식 매칭되면 True."""
        mc = _make_metric_condition(
            operator=ConditionOperator.REGEX,
            threshold=r"/api/v[12]/.*",
        )
        assert mc.evaluate("/api/v1/checkout") is True

    def test_regex_non_matching_pattern_returns_false(self):
        """REGEX 연산자: 정규식 미매칭이면 False."""
        mc = _make_metric_condition(
            operator=ConditionOperator.REGEX,
            threshold=r"/api/v[12]/.*",
        )
        assert mc.evaluate("/api/v3/checkout") is False

    def test_regex_invalid_pattern_returns_false(self):
        """REGEX 연산자: 잘못된 정규식은 False."""
        mc = _make_metric_condition(
            operator=ConditionOperator.REGEX,
            threshold="[invalid",
        )
        assert mc.evaluate("anything") is False


class TestPatternConditionEvaluateBehavior:
    """PatternCondition.evaluate() AND 게이트 동작 검증."""

    def test_empty_conditions_returns_true(self):
        """조건이 없으면 True (Proactive 경로)."""
        pc = _make_pattern_condition()
        assert pc.evaluate({}) is True

    def test_single_metric_satisfied_returns_true(self):
        """단일 메트릭 조건 충족 시 True."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
        )
        assert pc.evaluate({"error_rate": 0.06}) is True

    def test_single_metric_unsatisfied_returns_false(self):
        """단일 메트릭 조건 미충족 시 False."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
        )
        assert pc.evaluate({"error_rate": 0.04}) is False

    def test_missing_metric_returns_false(self):
        """메트릭이 스냅샷에 없으면 False."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(metric_name="error_rate")],
        )
        assert pc.evaluate({"latency": 100}) is False

    def test_multiple_metrics_all_satisfied_returns_true(self):
        """복수 메트릭 조건이 모두 충족되면 True (AND 게이트)."""
        pc = _make_pattern_condition(
            metric_conditions=[
                _make_metric_condition(metric_name="error_rate", threshold=0.05),
                _make_metric_condition(
                    metric_name="db_pool_usage",
                    operator=ConditionOperator.GTE,
                    threshold=0.90,
                ),
            ],
        )
        assert pc.evaluate({"error_rate": 0.06, "db_pool_usage": 0.95}) is True

    def test_multiple_metrics_one_unsatisfied_returns_false(self):
        """복수 메트릭 중 하나라도 미충족이면 False (AND 게이트, 부분 매칭 없음)."""
        pc = _make_pattern_condition(
            metric_conditions=[
                _make_metric_condition(metric_name="error_rate", threshold=0.05),
                _make_metric_condition(
                    metric_name="db_pool_usage",
                    operator=ConditionOperator.GTE,
                    threshold=0.90,
                ),
            ],
        )
        assert pc.evaluate({"error_rate": 0.06, "db_pool_usage": 0.80}) is False

    def test_event_or_trigger_matching_event_returns_true(self):
        """이벤트 OR 트리거: 매칭 이벤트가 있으면 True."""
        pc = _make_pattern_condition(
            event_conditions=[
                _make_event_condition("circuit_breaker_opened"),
                _make_event_condition("emergency_activated"),
            ],
        )
        assert pc.evaluate({}, "circuit_breaker_opened") is True

    def test_event_or_trigger_non_matching_event_returns_false(self):
        """이벤트 OR 트리거: 매칭 이벤트가 없으면 False."""
        pc = _make_pattern_condition(
            event_conditions=[
                _make_event_condition("circuit_breaker_opened"),
            ],
        )
        assert pc.evaluate({}, "emergency_activated") is False

    def test_metrics_and_event_both_satisfied_returns_true(self):
        """메트릭 AND 게이트 + 이벤트 OR 트리거 동시 충족 시 True."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
            event_conditions=[_make_event_condition("circuit_breaker_opened")],
        )
        assert pc.evaluate({"error_rate": 0.06}, "circuit_breaker_opened") is True

    def test_metrics_satisfied_event_not_matching_returns_false(self):
        """메트릭 충족이나 이벤트 미매칭이면 False."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
            event_conditions=[_make_event_condition("circuit_breaker_opened")],
        )
        assert pc.evaluate({"error_rate": 0.06}, "emergency_activated") is False

    def test_proactive_no_event_no_event_conditions_returns_true(self):
        """이벤트 조건 없고 이벤트 없으면 메트릭만으로 매칭 (Proactive 경로)."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
        )
        assert pc.evaluate({"error_rate": 0.06}, None) is True

    def test_has_event_conditions_but_no_triggered_event_returns_true(self):
        """이벤트 조건이 있지만 triggered_event가 None이면 메트릭만으로 매칭."""
        pc = _make_pattern_condition(
            metric_conditions=[_make_metric_condition(threshold=0.05)],
            event_conditions=[_make_event_condition("circuit_breaker_opened")],
        )
        # triggered_event=None이면 event_conditions 무시, 메트릭만으로 판단
        assert pc.evaluate({"error_rate": 0.06}, None) is True


class TestMatchResultDataIntegrityBehavior:
    """MatchResult 데이터 무결성 동작 검증."""

    def test_metric_snapshot_preserved_as_copy(self):
        """metric_snapshot은 원본 dict와 독립적이다."""
        original = {"error_rate": 0.06}
        mr = MatchResult(
            runbook_id="r1",
            confidence=0.9,
            matched_conditions=["error_rate gte 0.05"],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot=dict(original),
        )
        original["error_rate"] = 0.01
        assert mr.metric_snapshot["error_rate"] == 0.06

    def test_event_context_stores_arbitrary_data(self):
        """event_context에 임의의 이벤트 데이터를 저장할 수 있다."""
        mr = MatchResult(
            runbook_id="r1",
            confidence=0.9,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event="circuit_breaker_opened",
            metric_snapshot={},
            event_context={"service_name": "payment_api", "new_state": "open"},
        )
        assert mr.event_context["service_name"] == "payment_api"
        assert mr.event_context["new_state"] == "open"


class TestMatchSelectionResultBehavior:
    """MatchSelectionResult 동작 검증."""

    def test_selection_result_holds_selected_and_candidates(self):
        """선택 결과는 1위와 전체 후보를 모두 보존한다."""
        # Given
        selected = MatchResult(
            runbook_id="r1",
            confidence=0.9,
            matched_conditions=["cond1"],
            historical_success_rate=0.8,
            similar_pattern_count=3,
            triggered_by_event=None,
            metric_snapshot={},
        )
        runner_up = MatchResult(
            runbook_id="r2",
            confidence=0.7,
            matched_conditions=["cond2"],
            historical_success_rate=0.5,
            similar_pattern_count=1,
            triggered_by_event=None,
            metric_snapshot={},
        )

        # When
        result = MatchSelectionResult(
            selected=selected,
            all_candidates=[selected, runner_up],
            selection_reason="highest confidence",
        )

        # Then
        assert result.selected.runbook_id == "r1"
        assert len(result.all_candidates) == 2
        assert result.all_candidates[1].runbook_id == "r2"
        assert result.selection_reason == "highest confidence"
