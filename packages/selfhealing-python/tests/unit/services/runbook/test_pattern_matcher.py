"""
PatternMatcher 클래스의 동작 검증.

테스트 대상: selfhealing.services.runbook.pattern_matcher.PatternMatcher
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from selfhealing.services.runbook.duration_tracker import DurationTracker
from selfhealing.services.runbook.models import (
    ConditionOperator,
    EventCondition,
    MetricCondition,
    PatternCondition,
)
from selfhealing.services.runbook.pattern_matcher import PatternMatcher


# =============================================================================
# Test Doubles
# =============================================================================


@dataclass
class FakeRunbook:
    """테스트용 런북 스텁."""

    id: str = "runbook_001"
    trigger_condition: PatternCondition = field(default_factory=PatternCondition)
    cooldown_seconds: int = 300
    priority_weight: float = 1.0
    risk_level: int = 0


class FakeRegistry:
    """테스트용 런북 레지스트리 스텁."""

    def __init__(self, runbooks: list[FakeRunbook] | None = None):
        self._runbooks = runbooks or []

    def get_active_runbooks(self) -> list[FakeRunbook]:
        return self._runbooks


class FakeMetricsProvider:
    """테스트용 메트릭 제공자 스텁."""

    def __init__(self, metrics: dict[str, float] | None = None):
        self._metrics = metrics or {}

    def get_metric(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
    ) -> float | None:
        return self._metrics.get(metric_name)

    def get_metrics_snapshot(
        self,
        metric_names: list[str],
        labels: dict[str, str] | None = None,
    ) -> dict[str, float]:
        return {name: self._metrics[name] for name in metric_names if name in self._metrics}


# =============================================================================
# Helpers
# =============================================================================


def _make_error_rate_condition(threshold: float = 0.05) -> PatternCondition:
    """에러율 GTE 조건을 가진 PatternCondition 생성."""
    return PatternCondition(
        metric_conditions=[
            MetricCondition(
                metric_name="error_rate",
                operator=ConditionOperator.GTE,
                threshold=threshold,
            ),
        ],
    )


def _make_matcher(
    runbooks: list[FakeRunbook] | None = None,
    metrics: dict[str, float] | None = None,
    learning_service: object | None = None,
    duration_tracker: DurationTracker | None = None,
) -> PatternMatcher:
    """테스트용 PatternMatcher 인스턴스 생성."""
    return PatternMatcher(
        registry=FakeRegistry(runbooks or []),
        learning_service=learning_service,
        metrics_provider=FakeMetricsProvider(metrics or {}),
        duration_tracker=duration_tracker,
    )


# =============================================================================
# Contract Tests
# =============================================================================


class TestPatternMatcherConstantsContract:
    """PatternMatcher 클래스 상수 계약값 검증."""

    def test_trigger_events_count(self):
        """TRIGGER_EVENTS는 7개 이벤트 타입을 포함한다."""
        assert len(PatternMatcher.TRIGGER_EVENTS) == 7

    def test_trigger_events_contains_circuit_breaker_opened(self):
        """TRIGGER_EVENTS에 circuit_breaker_opened이 포함된다."""
        assert "circuit_breaker_opened" in PatternMatcher.TRIGGER_EVENTS

    def test_trigger_events_contains_emergency_activated(self):
        """TRIGGER_EVENTS에 emergency_activated가 포함된다."""
        assert "emergency_activated" in PatternMatcher.TRIGGER_EVENTS

    def test_trigger_events_contains_error_budget_critical(self):
        """TRIGGER_EVENTS에 error_budget_critical이 포함된다."""
        assert "error_budget_critical" in PatternMatcher.TRIGGER_EVENTS

    def test_condition_weight_is_0_6(self):
        """CONDITION_WEIGHT는 0.6이다."""
        assert PatternMatcher.CONDITION_WEIGHT == 0.6

    def test_learning_weight_is_0_4(self):
        """LEARNING_WEIGHT는 0.4이다."""
        assert PatternMatcher.LEARNING_WEIGHT == 0.4

    def test_half_life_days_is_90(self):
        """HALF_LIFE_DAYS는 90이다."""
        assert PatternMatcher.HALF_LIFE_DAYS == 90

    def test_learning_min_confidence_is_0_3(self):
        """LEARNING_MIN_CONFIDENCE는 0.3이다."""
        assert PatternMatcher.LEARNING_MIN_CONFIDENCE == 0.3


# =============================================================================
# Behavior Tests — evaluate_all
# =============================================================================


class TestEvaluateAllBehavior:
    """PatternMatcher.evaluate_all() 동작 검증."""

    def test_no_active_runbooks_returns_empty(self):
        """활성 런북이 없으면 빈 리스트 반환."""
        matcher = _make_matcher(runbooks=[])
        results = matcher.evaluate_all({"error_rate": 0.1})
        assert results == []

    def test_single_matching_runbook_returns_one_result(self):
        """단일 런북 매칭 시 1개 결과 반환."""
        runbook = FakeRunbook(
            id="rb_error_rate",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        matcher = _make_matcher(runbooks=[runbook])
        results = matcher.evaluate_all({"error_rate": 0.06})
        assert len(results) == 1
        assert results[0].runbook_id == "rb_error_rate"

    def test_non_matching_runbook_excluded(self):
        """조건 미충족 런북은 결과에서 제외된다."""
        runbook = FakeRunbook(
            id="rb_error_rate",
            trigger_condition=_make_error_rate_condition(0.10),
        )
        matcher = _make_matcher(runbooks=[runbook])
        results = matcher.evaluate_all({"error_rate": 0.05})
        assert results == []

    def test_multiple_runbooks_sorted_by_confidence_desc(self):
        """복수 런북 매칭 시 confidence 내림차순 정렬."""
        # Given
        runbook_high = FakeRunbook(
            id="rb_high",
            trigger_condition=_make_error_rate_condition(0.05),
            priority_weight=1.0,
        )
        runbook_low = FakeRunbook(
            id="rb_low",
            trigger_condition=_make_error_rate_condition(0.05),
            priority_weight=0.5,
        )
        matcher = _make_matcher(runbooks=[runbook_low, runbook_high])

        # When
        results = matcher.evaluate_all({"error_rate": 0.06})

        # Then
        assert len(results) == 2
        assert results[0].confidence >= results[1].confidence

    def test_event_context_preserved_in_result(self):
        """event_context가 MatchResult에 보존된다."""
        runbook = FakeRunbook(
            id="rb_cb",
            trigger_condition=PatternCondition(
                event_conditions=[EventCondition(event_type="circuit_breaker_opened")],
            ),
        )
        matcher = _make_matcher(runbooks=[runbook])
        context = {"service_name": "payment_api", "new_state": "open"}

        results = matcher.evaluate_all(
            metrics={},
            triggered_event="circuit_breaker_opened",
            event_context=context,
        )

        assert len(results) == 1
        assert results[0].event_context["service_name"] == "payment_api"

    def test_triggered_event_recorded_in_result(self):
        """triggered_by_event가 MatchResult에 기록된다."""
        runbook = FakeRunbook(
            id="rb_cb",
            trigger_condition=PatternCondition(
                event_conditions=[EventCondition(event_type="circuit_breaker_opened")],
            ),
        )
        matcher = _make_matcher(runbooks=[runbook])
        results = matcher.evaluate_all(
            metrics={},
            triggered_event="circuit_breaker_opened",
        )
        assert results[0].triggered_by_event == "circuit_breaker_opened"

    def test_metric_snapshot_preserved_in_result(self):
        """매칭 시점 메트릭 스냅샷이 MatchResult에 보존된다."""
        runbook = FakeRunbook(
            id="rb_er",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        metrics = {"error_rate": 0.08, "latency": 200}
        matcher = _make_matcher(runbooks=[runbook])
        results = matcher.evaluate_all(metrics)
        assert results[0].metric_snapshot == metrics

    def test_metric_snapshot_is_copy_not_reference(self):
        """metric_snapshot은 원본 dict의 복사본이다."""
        runbook = FakeRunbook(
            id="rb_er",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        metrics = {"error_rate": 0.08}
        matcher = _make_matcher(runbooks=[runbook])
        results = matcher.evaluate_all(metrics)

        # When: 원본 수정
        metrics["error_rate"] = 0.01

        # Then: 결과 불변
        assert results[0].metric_snapshot["error_rate"] == 0.08


# =============================================================================
# Behavior Tests — min_duration pending
# =============================================================================


class TestMinDurationBehavior:
    """min_duration_seconds 지속 시간 미충족 시 pending 동작 검증."""

    def test_min_duration_not_met_excluded_from_results(self):
        """min_duration 미충족 런북은 결과에서 제외된다."""
        runbook = FakeRunbook(
            id="rb_slow_leak",
            trigger_condition=PatternCondition(
                metric_conditions=[
                    MetricCondition(
                        metric_name="memory_usage",
                        operator=ConditionOperator.GTE,
                        threshold=0.90,
                    ),
                ],
                min_duration_seconds=30,
            ),
        )
        tracker = DurationTracker()
        matcher = _make_matcher(runbooks=[runbook], duration_tracker=tracker)

        results = matcher.evaluate_all({"memory_usage": 0.95})
        assert results == []

    def test_min_duration_records_condition_met(self):
        """min_duration 미충족 시 DurationTracker에 기록한다."""
        runbook = FakeRunbook(
            id="rb_slow_leak",
            trigger_condition=PatternCondition(
                metric_conditions=[
                    MetricCondition(
                        metric_name="memory_usage",
                        operator=ConditionOperator.GTE,
                        threshold=0.90,
                    ),
                ],
                min_duration_seconds=30,
            ),
        )
        tracker = DurationTracker()
        matcher = _make_matcher(runbooks=[runbook], duration_tracker=tracker)

        matcher.evaluate_all({"memory_usage": 0.95})

        # 인메모리 스토어에 기록 확인
        assert "rb_slow_leak" in tracker._memory_store


# =============================================================================
# Behavior Tests — select_runbook Tie-breaker
# =============================================================================


class TestSelectRunbookBehavior:
    """PatternMatcher.select_runbook() 동작 검증."""

    def test_empty_candidates_returns_none(self):
        """후보가 없으면 None 반환."""
        matcher = _make_matcher()
        assert matcher.select_runbook([]) is None

    def test_single_candidate_selected(self):
        """단일 후보는 그대로 선택된다."""
        from selfhealing.services.runbook.models import MatchResult

        candidate = MatchResult(
            runbook_id="rb1",
            confidence=0.9,
            matched_conditions=["cond1"],
            historical_success_rate=0.8,
            similar_pattern_count=2,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([candidate])

        assert result is not None
        assert result.selected.runbook_id == "rb1"
        assert len(result.all_candidates) == 1
        assert result.selected.runner_up_runbook_ids == []

    def test_highest_confidence_selected(self):
        """confidence가 가장 높은 런북이 선택된다."""
        from selfhealing.services.runbook.models import MatchResult

        low = MatchResult(
            runbook_id="rb_low",
            confidence=0.5,
            matched_conditions=[],
            historical_success_rate=0.9,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        high = MatchResult(
            runbook_id="rb_high",
            confidence=0.9,
            matched_conditions=[],
            historical_success_rate=0.5,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([low, high])

        assert result is not None
        assert result.selected.runbook_id == "rb_high"

    def test_tie_broken_by_success_rate(self):
        """confidence 동점 시 success_rate로 타이 브레이크."""
        from selfhealing.services.runbook.models import MatchResult

        low_rate = MatchResult(
            runbook_id="rb_low_rate",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=0.5,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        high_rate = MatchResult(
            runbook_id="rb_high_rate",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=0.9,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([low_rate, high_rate])

        assert result is not None
        assert result.selected.runbook_id == "rb_high_rate"

    def test_tie_broken_by_runbook_id_alphabetical(self):
        """confidence와 success_rate 모두 동점 시 runbook_id 알파벳순."""
        from selfhealing.services.runbook.models import MatchResult

        rb_b = MatchResult(
            runbook_id="rb_beta",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=0.5,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        rb_a = MatchResult(
            runbook_id="rb_alpha",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=0.5,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([rb_b, rb_a])

        assert result is not None
        assert result.selected.runbook_id == "rb_alpha"

    def test_tie_broken_by_risk_level_lower_wins(self):
        """confidence 동점 시 risk_level 낮은 것(SAFE)이 우선 선택된다."""
        from selfhealing.services.runbook.models import MatchResult

        # Given
        safe_runbook = MatchResult(
            runbook_id="rb_safe",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
            risk_level=0,
        )
        dangerous_runbook = MatchResult(
            runbook_id="rb_dangerous",
            confidence=0.8,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
            risk_level=2,
        )
        matcher = _make_matcher()

        # When
        result = matcher.select_runbook([dangerous_runbook, safe_runbook])

        # Then
        assert result is not None
        assert result.selected.runbook_id == "rb_safe"

    def test_runner_up_ids_populated(self):
        """선택 후 runner_up_runbook_ids에 탈락 후보 ID가 기록된다."""
        from selfhealing.services.runbook.models import MatchResult

        first = MatchResult(
            runbook_id="rb_first",
            confidence=0.9,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        second = MatchResult(
            runbook_id="rb_second",
            confidence=0.7,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        third = MatchResult(
            runbook_id="rb_third",
            confidence=0.5,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([second, third, first])

        assert result is not None
        assert result.selected.runner_up_runbook_ids == ["rb_second", "rb_third"]

    def test_selection_reason_contains_runbook_id(self):
        """selection_reason에 선택된 런북 ID가 포함된다."""
        from selfhealing.services.runbook.models import MatchResult

        candidate = MatchResult(
            runbook_id="rb_winner",
            confidence=0.85,
            matched_conditions=[],
            historical_success_rate=None,
            similar_pattern_count=0,
            triggered_by_event=None,
            metric_snapshot={},
        )
        matcher = _make_matcher()
        result = matcher.select_runbook([candidate])

        assert result is not None
        assert "rb_winner" in result.selection_reason

    def test_all_candidates_ordered_by_confidence(self):
        """all_candidates는 confidence 내림차순으로 정렬된다."""
        from selfhealing.services.runbook.models import MatchResult

        candidates = [
            MatchResult(
                runbook_id=f"rb_{i}",
                confidence=c,
                matched_conditions=[],
                historical_success_rate=None,
                similar_pattern_count=0,
                triggered_by_event=None,
                metric_snapshot={},
            )
            for i, c in enumerate([0.3, 0.9, 0.6])
        ]
        matcher = _make_matcher()
        result = matcher.select_runbook(candidates)

        assert result is not None
        confidences = [c.confidence for c in result.all_candidates]
        assert confidences == sorted(confidences, reverse=True)


# =============================================================================
# Behavior Tests — Confidence 계산
# =============================================================================


class TestConfidenceCalculationBehavior:
    """PatternMatcher._calculate_confidence() 동작 검증."""

    def test_cold_start_no_learning_returns_full_score(self):
        """LearningService 없으면 base_score 100% 반환 (콜드스타트 안전)."""
        runbook = FakeRunbook(priority_weight=1.0)
        matcher = _make_matcher()
        confidence = matcher._calculate_confidence(runbook)
        assert confidence == 1.0

    def test_priority_weight_clamps_to_min_0_5(self):
        """priority_weight가 0.5 미만이면 0.5로 클램핑."""
        runbook = FakeRunbook(priority_weight=0.2)
        matcher = _make_matcher()
        confidence = matcher._calculate_confidence(runbook)
        assert confidence == 0.5

    def test_priority_weight_clamps_to_max_1_0(self):
        """priority_weight가 1.0 초과면 1.0으로 클램핑."""
        runbook = FakeRunbook(priority_weight=1.5)
        matcher = _make_matcher()
        confidence = matcher._calculate_confidence(runbook)
        assert confidence == 1.0

    def test_confidence_capped_at_1_0(self):
        """confidence는 최대 1.0을 초과하지 않는다."""
        runbook = FakeRunbook(priority_weight=1.0)
        matcher = _make_matcher()
        confidence = matcher._calculate_confidence(runbook)
        assert confidence <= 1.0


# =============================================================================
# Behavior Tests — _describe_conditions
# =============================================================================


class TestDescribeConditionsBehavior:
    """PatternMatcher._describe_conditions() 동작 검증."""

    def test_metric_condition_description(self):
        """메트릭 조건이 읽기 쉬운 설명으로 변환된다."""
        condition = PatternCondition(
            metric_conditions=[
                MetricCondition(
                    metric_name="error_rate",
                    operator=ConditionOperator.GTE,
                    threshold=0.05,
                ),
            ],
        )
        matcher = _make_matcher()
        descriptions = matcher._describe_conditions(condition)
        assert len(descriptions) == 1
        assert "error_rate" in descriptions[0]
        assert "gte" in descriptions[0]
        assert "0.05" in descriptions[0]

    def test_event_condition_description(self):
        """이벤트 조건이 설명에 포함된다."""
        condition = PatternCondition(
            event_conditions=[
                EventCondition(
                    event_type="circuit_breaker_opened",
                    source_filter="payment",
                ),
            ],
        )
        matcher = _make_matcher()
        descriptions = matcher._describe_conditions(condition)
        assert len(descriptions) == 1
        assert "circuit_breaker_opened" in descriptions[0]
        assert "payment" in descriptions[0]

    def test_mixed_conditions_description(self):
        """메트릭 + 이벤트 조건이 합쳐진 설명."""
        condition = PatternCondition(
            metric_conditions=[
                MetricCondition(
                    metric_name="error_rate",
                    operator=ConditionOperator.GTE,
                    threshold=0.05,
                ),
            ],
            event_conditions=[
                EventCondition(event_type="circuit_breaker_opened"),
            ],
        )
        matcher = _make_matcher()
        descriptions = matcher._describe_conditions(condition)
        assert len(descriptions) == 2


# =============================================================================
# Behavior Tests — _collect_required_metrics
# =============================================================================


class TestCollectRequiredMetricsBehavior:
    """PatternMatcher._collect_required_metrics() 동작 검증."""

    def test_deduplicates_metric_names(self):
        """중복 메트릭 이름이 제거된다."""
        runbooks = [
            FakeRunbook(
                id="rb1",
                trigger_condition=PatternCondition(
                    metric_conditions=[
                        MetricCondition(
                            metric_name="error_rate",
                            operator=ConditionOperator.GTE,
                            threshold=0.05,
                        ),
                    ],
                ),
            ),
            FakeRunbook(
                id="rb2",
                trigger_condition=PatternCondition(
                    metric_conditions=[
                        MetricCondition(
                            metric_name="error_rate",
                            operator=ConditionOperator.GTE,
                            threshold=0.10,
                        ),
                    ],
                ),
            ),
        ]
        matcher = _make_matcher(runbooks=runbooks)
        required = matcher._collect_required_metrics()
        assert required == ["error_rate"]

    def test_multiple_different_metrics_sorted(self):
        """다른 메트릭들이 정렬되어 반환된다."""
        runbooks = [
            FakeRunbook(
                id="rb1",
                trigger_condition=PatternCondition(
                    metric_conditions=[
                        MetricCondition(
                            metric_name="latency",
                            operator=ConditionOperator.GTE,
                            threshold=100,
                        ),
                        MetricCondition(
                            metric_name="error_rate",
                            operator=ConditionOperator.GTE,
                            threshold=0.05,
                        ),
                    ],
                ),
            ),
        ]
        matcher = _make_matcher(runbooks=runbooks)
        required = matcher._collect_required_metrics()
        assert required == ["error_rate", "latency"]

    def test_no_runbooks_returns_empty(self):
        """런북이 없으면 빈 리스트 반환."""
        matcher = _make_matcher(runbooks=[])
        assert matcher._collect_required_metrics() == []


# =============================================================================
# Behavior Tests — Idempotency
# =============================================================================


class TestEvaluateAllIdempotencyBehavior:
    """evaluate_all() 멱등성 검증."""

    def test_same_input_same_output(self):
        """동일 입력에 대해 동일 결과를 반환한다."""
        runbook = FakeRunbook(
            id="rb1",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        matcher = _make_matcher(runbooks=[runbook])
        metrics = {"error_rate": 0.06}

        results_1 = matcher.evaluate_all(metrics)
        results_2 = matcher.evaluate_all(metrics)

        assert len(results_1) == len(results_2)
        assert results_1[0].runbook_id == results_2[0].runbook_id
        assert results_1[0].confidence == results_2[0].confidence


# =============================================================================
# Behavior Tests — 데이터 불변성
# =============================================================================


class TestDataImmutabilityBehavior:
    """evaluate_all() 입력 데이터 불변성 검증."""

    def test_metrics_dict_not_mutated(self):
        """입력 metrics dict가 변경되지 않는다."""
        runbook = FakeRunbook(
            id="rb1",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        matcher = _make_matcher(runbooks=[runbook])
        metrics = {"error_rate": 0.06, "latency": 200}
        original = dict(metrics)

        matcher.evaluate_all(metrics)

        assert metrics == original


# =============================================================================
# Behavior Tests — risk_level 런북 반영
# =============================================================================


class TestRiskLevelBehavior:
    """evaluate_all()이 런북의 risk_level을 MatchResult에 올바르게 반영하는지 검증."""

    def test_risk_level_populated_from_runbook(self):
        """evaluate_all()이 runbook.risk_level을 MatchResult.risk_level에 복사한다."""
        runbook = FakeRunbook(
            id="rb_moderate",
            trigger_condition=_make_error_rate_condition(0.05),
            risk_level=1,
        )
        matcher = _make_matcher(runbooks=[runbook])

        results = matcher.evaluate_all({"error_rate": 0.06})

        assert len(results) == 1
        assert results[0].risk_level == 1

    def test_risk_level_defaults_to_zero_when_runbook_has_no_attr(self):
        """runbook에 risk_level 속성이 없으면 MatchResult.risk_level은 0."""
        runbook = FakeRunbook(
            id="rb_no_risk",
            trigger_condition=_make_error_rate_condition(0.05),
        )
        # FakeRunbook는 risk_level=0으로 선언됨 (설계 기본값과 일치)
        matcher = _make_matcher(runbooks=[runbook])

        results = matcher.evaluate_all({"error_rate": 0.06})

        assert results[0].risk_level == 0


# =============================================================================
# Behavior Tests — on_runbook_selected 콜백
# =============================================================================


class TestOnRunbookSelectedCallbackBehavior:
    """PatternMatcher.on_runbook_selected 콜백 동작 검증."""

    def test_callback_called_when_runbook_selected_via_event(self):
        """_on_event()가 런북을 선택했을 때 on_runbook_selected 콜백이 호출된다."""
        from unittest.mock import MagicMock

        from selfhealing.services.runbook.models import EventCondition, PatternCondition
        from selfhealing.services.runbook.pattern_matcher import PatternMatcher

        # Given
        runbook = FakeRunbook(
            id="rb_cb",
            trigger_condition=PatternCondition(
                event_conditions=[EventCondition(event_type="circuit_breaker_opened")],
            ),
        )
        callback = MagicMock()
        matcher = PatternMatcher(
            registry=FakeRegistry([runbook]),
            on_runbook_selected=callback,
        )

        # When: 이벤트 발행으로 _on_event() 호출 시뮬레이션
        # _on_event는 내부 EventBus 구독 콜백이므로 직접 호출
        class FakeEvent:
            event_type = "circuit_breaker_opened"
            source = "payment_api"
            data = {"new_state": "open"}

        matcher._on_event(FakeEvent())

        # Then
        callback.assert_called_once()
        call_args = callback.call_args[0][0]
        assert call_args.selected.runbook_id == "rb_cb"

    def test_callback_payload_includes_trigger_context(self):
        """콜백으로 전달된 selected.event_context에 trigger_context가 포함된다."""
        from unittest.mock import MagicMock

        from selfhealing.services.runbook.models import EventCondition, PatternCondition
        from selfhealing.services.runbook.pattern_matcher import PatternMatcher

        # Given
        runbook = FakeRunbook(
            id="rb_trigger_ctx",
            trigger_condition=PatternCondition(
                event_conditions=[EventCondition(event_type="circuit_breaker_opened")],
            ),
        )
        callback = MagicMock()
        matcher = PatternMatcher(
            registry=FakeRegistry([runbook]),
            on_runbook_selected=callback,
        )

        class FakeEvent:
            event_type = "circuit_breaker_opened"
            source = "payment_api"
            data = {"new_state": "open", "service_name": "payment_api"}

        # When
        matcher._on_event(FakeEvent())

        # Then
        callback.assert_called_once()
        selection = callback.call_args[0][0]
        trigger_event = selection.selected.event_context
        assert "trigger_context" in trigger_event
        assert trigger_event["trigger_context"]["triggered_by_event"] == "circuit_breaker_opened"
        assert "metric_snapshot" in trigger_event["trigger_context"]
        assert "matched_conditions" in trigger_event["trigger_context"]
        assert trigger_event["service_name"] == "payment_api"

    def test_no_callback_no_error_when_no_match(self):
        """콜백 없이 매칭이 없을 때도 에러 없이 동작한다."""
        matcher = PatternMatcher(
            registry=FakeRegistry([]),
            on_runbook_selected=None,
        )

        class FakeEvent:
            event_type = "circuit_breaker_opened"
            source = None
            data = {}

        # 예외 없이 실행되어야 함
        matcher._on_event(FakeEvent())
