"""
현재 시스템 증상을 런북 트리거 조건과 대조하여 실행할 런북을 자동 선택하는 매처.

Reactive (EventBus 이벤트 수신) + Proactive (주기적 메트릭 평가) 하이브리드 경로로
운영하며, 중복 트리거 방지를 위해 쿨다운/멱등성 키를 관리한다.

Reference:
    docs/self_healing/middleware_system/273_RUNBOOK_PATTERN_MATCHER.md §4
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp
from typing import Any, Protocol, runtime_checkable

import structlog

from selfhealing.services.runbook.duration_tracker import DurationTracker
from selfhealing.services.runbook.metrics_provider import RunbookMetricsProvider
from selfhealing.services.runbook.models import (
    EventCondition,
    MatchResult,
    MatchSelectionResult,
    MetricCondition,
    PatternCondition,
)
from selfhealing.utils.jitter import with_jitter

logger = structlog.get_logger()


# =============================================================================
# 의존성 Protocol — 274 RunbookRegistry 구현 전까지 인터페이스만 정의
# =============================================================================


@runtime_checkable
class RunbookLike(Protocol):
    """PatternMatcher가 참조하는 런북 최소 인터페이스.

    274 RunbookRegistry 구현 시 Runbook 데이터클래스가 이 Protocol을 만족한다.
    """

    @property
    def id(self) -> str: ...

    @property
    def trigger_condition(self) -> PatternCondition: ...

    @property
    def cooldown_seconds(self) -> int: ...


@runtime_checkable
class RunbookRegistryLike(Protocol):
    """PatternMatcher가 참조하는 런북 레지스트리 최소 인터페이스.

    274 RunbookRegistry 구현 시 해당 클래스가 이 Protocol을 만족한다.
    """

    def get_active_runbooks(self) -> list[Any]: ...


@runtime_checkable
class LearningServiceLike(Protocol):
    """PatternMatcher가 참조하는 학습 서비스 최소 인터페이스."""

    def get_patterns(
        self,
        pattern_type: Any = None,
        min_confidence: float = 0.0,
    ) -> list[Any]: ...


# =============================================================================
# PatternMatcher
# =============================================================================


class PatternMatcher:
    """현재 증상을 런북 트리거 조건과 대조하여 실행할 런북을 선택한다.

    두 가지 평가 경로:
    1. Reactive: EventBus 이벤트 구독 → _on_event() → 즉시 평가
    2. Proactive: Celery Beat/타이머 → evaluate_all_proactive() → 주기적 평가
    """

    # 구독할 이벤트 타입 목록 (trigger 평가 시작점)
    TRIGGER_EVENTS: list[str] = [
        "circuit_breaker_opened",
        "emergency_activated",
        "error_budget_critical",
        "error_budget_warning",
        "throttle_sla_critical",
        "load_shedding_level_changed",
        "rate_limit_429",
    ]

    # --- Confidence 가중치 ---
    CONDITION_WEIGHT: float = 0.6
    """룰 기반 조건 매칭 60%"""

    LEARNING_WEIGHT: float = 0.4
    """학습 보강 최대 40%"""

    # --- Time Decay ---
    HALF_LIFE_DAYS: int = 90
    """반감기 90일: 90일 전 패턴은 confidence 50%로 감쇠"""

    # --- LearningService 최소 confidence ---
    LEARNING_MIN_CONFIDENCE: float = 0.3
    """학습 패턴 조회 시 최소 confidence 필터"""

    def __init__(
        self,
        registry: RunbookRegistryLike,
        learning_service: LearningServiceLike | None = None,
        metrics_provider: RunbookMetricsProvider | None = None,
        duration_tracker: DurationTracker | None = None,
    ):
        """
        Args:
            registry: 런북 레지스트리 (활성 런북 목록 조회)
            learning_service: 패턴 학습 서비스 (과거 사례 조회용, optional)
            metrics_provider: 범용 메트릭 제공자
            duration_tracker: min_duration 추적기 (None이면 인메모리 생성)
        """
        self._registry = registry
        self._learning_service = learning_service
        self._metrics_provider = metrics_provider
        self._duration_tracker = duration_tracker or DurationTracker()

    # ------------------------------------------------------------------
    # 초기화 — EventBus 구독 등록
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """EventBus에 이벤트 구독 등록.

        SelfHealingEventBus.subscribe(event_type, handler, priority)를 사용.
        priority=EventPriority.NORMAL — 다른 핸들러와 동등.
        """
        try:
            from selfhealing.services.event_bus.bus import (
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            event_type_map = {e.value: e for e in EventType}

            for event_value in self.TRIGGER_EVENTS:
                event_type = event_type_map.get(event_value)
                if event_type is not None:
                    bus.subscribe(event_type, self._on_event)
                    logger.info(
                        "pattern_matcher.subscribed",
                        event_type=event_value,
                    )
                else:
                    logger.warning(
                        "pattern_matcher.event_type_not_found",
                        event_value=event_value,
                    )
        except ImportError:
            logger.warning("pattern_matcher.event_bus_unavailable")

    # ------------------------------------------------------------------
    # Reactive 경로 — 이벤트 구독 콜백
    # ------------------------------------------------------------------

    def _on_event(self, event: Any) -> None:
        """이벤트 수신 시 콜백 — Reactive 경로.

        1. 현재 메트릭 수집 (RunbookMetricsProvider.get_metrics_snapshot)
        2. 등록된 모든 활성 런북의 trigger 조건 평가 (AND 게이트)
        3. min_duration > 0: DurationTracker에 first_met_at 기록, "pending" 상태
        4. min_duration == 0: 즉시 매칭 → MatchResult 생성
        5. event.data를 MatchResult.event_context에 보존 (컨텍스트 전달)
        6. 최고 confidence 런북 선택 → MatchSelectionResult 반환
        """
        triggered_event = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)

        # 이벤트 컨텍스트 보존
        event_context: dict[str, Any] = {}
        if hasattr(event, "data") and isinstance(event.data, dict):
            event_context = dict(event.data)
        if hasattr(event, "source"):
            event_context["_source"] = event.source

        # 메트릭 수집
        metrics = self._collect_metrics()

        # 전체 런북 평가
        results = self.evaluate_all(
            metrics=metrics,
            triggered_event=triggered_event,
            event_context=event_context,
        )

        if results:
            selection = self.select_runbook(results)
            if selection is not None:
                logger.info(
                    "pattern_matcher.runbook_selected",
                    runbook_id=selection.selected.runbook_id,
                    confidence=selection.selected.confidence,
                    triggered_by=triggered_event,
                    candidates_count=len(selection.all_candidates),
                )

    # ------------------------------------------------------------------
    # Proactive 경로 — 주기적 평가
    # ------------------------------------------------------------------

    @with_jitter(max_delay_seconds=10.0, min_delay_seconds=0.0)
    def evaluate_all_proactive(self) -> list[MatchResult]:
        """Proactive 경로 — Celery Beat에서 주기적으로 호출.

        @with_jitter 데코레이터로 워커별 랜덤 지연을 적용하여
        Thundering Herd를 방지한다.
        """
        metrics = self._collect_metrics()
        return self.evaluate_all(metrics, triggered_event=None)

    # ------------------------------------------------------------------
    # 핵심 평가 로직
    # ------------------------------------------------------------------

    def evaluate_all(
        self,
        metrics: dict[str, float],
        triggered_event: str | None = None,
        event_context: dict[str, Any] | None = None,
    ) -> list[MatchResult]:
        """등록된 모든 런북의 트리거 조건을 평가.

        AND 게이트 기반 필터링:
        - PatternCondition.evaluate()가 True인 런북만 후보로 진입
        - 부분 매칭(Partial Match)은 허용하지 않음
        - 후보 진입 후 confidence는 별도 Factor로 산정

        Args:
            metrics: 현재 메트릭 스냅샷
            triggered_event: 트리거 이벤트 타입 (optional)
            event_context: 원본 이벤트 데이터 (optional, Executor 파라미터 치환용)

        Returns:
            매칭된 런북 목록 (confidence 내림차순)
        """
        results: list[MatchResult] = []

        for runbook in self._registry.get_active_runbooks():
            condition: PatternCondition = runbook.trigger_condition

            # AND 게이트 — 모든 메트릭 조건 동시 충족 필수
            if not condition.evaluate(metrics, triggered_event):
                continue

            # min_duration 확인
            if condition.min_duration_seconds > 0:
                if not self._duration_tracker.check_duration_met(
                    runbook.id,
                    condition.min_duration_seconds,
                ):
                    self._duration_tracker.record_condition_met(
                        runbook.id,
                        condition.min_duration_seconds,
                    )
                    logger.debug(
                        "pattern_matcher.pending_duration",
                        runbook_id=runbook.id,
                        min_duration=condition.min_duration_seconds,
                    )
                    continue

            # Confidence 산정
            confidence = self._calculate_confidence(runbook)

            results.append(
                MatchResult(
                    runbook_id=runbook.id,
                    confidence=confidence,
                    matched_conditions=self._describe_conditions(condition),
                    historical_success_rate=self._get_historical_success_rate(runbook.id),
                    similar_pattern_count=self._count_similar_patterns(runbook.id),
                    triggered_by_event=triggered_event,
                    metric_snapshot=dict(metrics),
                    event_context=event_context or {},
                )
            )

        # Confidence 내림차순 정렬
        results.sort(key=lambda r: -r.confidence)
        return results

    # ------------------------------------------------------------------
    # 런북 선택 — Deterministic Tie-breaker
    # ------------------------------------------------------------------

    def select_runbook(
        self,
        candidates: list[MatchResult],
    ) -> MatchSelectionResult | None:
        """다중 런북 매칭 시 최종 1개 선택 — Deterministic Tie-breaker.

        RootCauseRanker의 4단계 Tie-breaker 패턴을 적용:
        1차: confidence 높은 순
        2차: historical_success_rate 높은 순
        3차: runbook_id 알파벳순 (결정론적)
        """
        if not candidates:
            return None

        candidates.sort(
            key=lambda m: (
                -m.confidence,
                -(m.historical_success_rate or 0.0),
                m.runbook_id,
            )
        )

        selected = candidates[0]
        selected.runner_up_runbook_ids = [c.runbook_id for c in candidates[1:]]

        return MatchSelectionResult(
            selected=selected,
            all_candidates=candidates,
            selection_reason=self._build_selection_reason(selected, candidates),
        )

    # ------------------------------------------------------------------
    # Confidence 계산 — AND 게이트 통과 후 품질 점수
    # ------------------------------------------------------------------

    def _calculate_confidence(self, runbook: Any) -> float:
        """AND 게이트 통과 후 confidence 산정.

        룰 기반 60% + 학습 보강 40% 가중합.
        학습 데이터가 없으면 룰 기반 기본 점수 100% (콜드스타트 안전).
        """
        # 1단계: 룰 기반 기본 점수 (AND 게이트 통과 = 1.0)
        base_score = 1.0

        # 런북에 우선순위 가중치가 정의되어 있으면 적용 (0.5 ~ 1.0)
        if hasattr(runbook, "priority_weight"):
            base_score = max(0.5, min(1.0, runbook.priority_weight))

        # 2단계: 학습 보강 (optional)
        learning_boost = 0.0
        if self._learning_service is not None:
            learning_boost = self._calculate_learning_boost(runbook.id)

        # 3단계: 가중 합성
        if learning_boost > 0:
            final = self.CONDITION_WEIGHT * base_score + self.LEARNING_WEIGHT * learning_boost
        else:
            final = base_score  # 콜드스타트: 룰 기반 100%

        return min(final, 1.0)

    # ------------------------------------------------------------------
    # LearningService 연동 — Time Decay 적용
    # ------------------------------------------------------------------

    def _calculate_learning_boost(self, runbook_id: str) -> float:
        """LearningService 패턴 기반 confidence 보강 — Time Decay 적용.

        Time Decay 공식: decay = exp(-0.693 * age_days / HALF_LIFE_DAYS)
        - 반감기 90일: 90일 전 패턴은 confidence 50%로 감쇠
        - 180일 전 패턴은 25%로 감쇠
        """
        if self._learning_service is None:
            return 0.0

        try:
            from selfhealing.services.learning.models import PatternType

            patterns = self._learning_service.get_patterns(
                pattern_type=PatternType.FAILURE,
                min_confidence=self.LEARNING_MIN_CONFIDENCE,
            )
        except (ImportError, Exception):
            logger.debug("pattern_matcher.learning_unavailable", runbook_id=runbook_id)
            return 0.0

        similar = self._find_similar_patterns(runbook_id, patterns)
        if not similar:
            return 0.0

        weighted_sum = 0.0
        total_weight = 0.0

        for pattern in similar:
            last_seen = getattr(pattern, "last_seen", None)
            if last_seen is None:
                continue

            age_days = (datetime.now(timezone.utc) - last_seen).days
            decay_factor = exp(-0.693 * age_days / self.HALF_LIFE_DAYS)
            occurrence = getattr(pattern, "occurrence_count", 1)
            weight = occurrence * decay_factor
            confidence = getattr(pattern, "confidence", 0.5)
            weighted_sum += confidence * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _find_similar_patterns(self, runbook_id: str, patterns: list[Any]) -> list[Any]:
        """런북 ID 기반 유사 패턴 필터링.

        LearningPattern.features의 키-값이 현재 런북와 매칭되는 패턴을 반환한다.
        features에 "runbook_id" 키가 있으면 직접 매칭하고,
        없으면 metadata에서 연관성을 확인한다.
        """
        similar = []
        for pattern in patterns:
            features = getattr(pattern, "features", {}) or {}
            metadata = getattr(pattern, "metadata", {}) or {}

            # features.runbook_id 직접 매칭
            if features.get("runbook_id") == runbook_id:
                similar.append(pattern)
                continue

            # metadata에서 연관 런북 확인
            if metadata.get("runbook_id") == runbook_id:
                similar.append(pattern)

        return similar

    # ------------------------------------------------------------------
    # 메트릭 수집
    # ------------------------------------------------------------------

    def _collect_metrics(self) -> dict[str, float]:
        """현재 메트릭을 수집하여 스냅샷으로 반환."""
        if self._metrics_provider is None:
            return {}

        required = self._collect_required_metrics()
        if not required:
            return {}

        try:
            return self._metrics_provider.get_metrics_snapshot(
                metric_names=required,
            )
        except Exception:
            logger.warning("pattern_matcher.metrics_collection_failed", exc_info=True)
            return {}

    def _collect_required_metrics(self) -> list[str]:
        """활성 런북들이 필요로 하는 메트릭 이름 목록 수집."""
        metric_names: set[str] = set()
        for runbook in self._registry.get_active_runbooks():
            condition: PatternCondition = runbook.trigger_condition
            for mc in condition.metric_conditions:
                metric_names.add(mc.metric_name)
        return sorted(metric_names)

    # ------------------------------------------------------------------
    # 헬퍼 메서드
    # ------------------------------------------------------------------

    def _describe_conditions(self, condition: PatternCondition) -> list[str]:
        """매칭된 조건을 사람이 읽을 수 있는 설명 목록으로 변환."""
        descriptions: list[str] = []
        for mc in condition.metric_conditions:
            descriptions.append(f"{mc.metric_name} {mc.operator.value} {mc.threshold}")
        for ec in condition.event_conditions:
            desc = f"event={ec.event_type}"
            if ec.source_filter:
                desc += f" source={ec.source_filter}"
            descriptions.append(desc)
        return descriptions

    def _get_historical_success_rate(self, runbook_id: str) -> float | None:
        """과거 실행 성공률 조회. LearningService가 없으면 None."""
        if self._learning_service is None:
            return None

        try:
            from selfhealing.services.learning.models import PatternType

            patterns = self._learning_service.get_patterns(
                pattern_type=PatternType.RECOVERY,
                min_confidence=0.0,
            )
            matching = [p for p in patterns if (getattr(p, "features", {}) or {}).get("runbook_id") == runbook_id]
            if not matching:
                return None

            total = sum(getattr(p, "occurrence_count", 1) for p in matching)
            success = sum(getattr(p, "occurrence_count", 1) for p in matching if getattr(p, "confidence", 0) >= 0.5)
            return success / total if total > 0 else None
        except Exception:
            return None

    def _count_similar_patterns(self, runbook_id: str) -> int:
        """LearningService에서 유사 패턴 수 조회."""
        if self._learning_service is None:
            return 0

        try:
            from selfhealing.services.learning.models import PatternType

            patterns = self._learning_service.get_patterns(
                pattern_type=PatternType.FAILURE,
                min_confidence=self.LEARNING_MIN_CONFIDENCE,
            )
            return len(self._find_similar_patterns(runbook_id, patterns))
        except Exception:
            return 0

    def _build_selection_reason(
        self,
        selected: MatchResult,
        candidates: list[MatchResult],
    ) -> str:
        """선택 근거 설명 문자열 생성."""
        parts = [f"confidence={selected.confidence:.3f}"]

        if selected.historical_success_rate is not None:
            parts.append(f"success_rate={selected.historical_success_rate:.2f}")

        if len(candidates) > 1:
            runner_up = candidates[1]
            parts.append(f"runner_up={runner_up.runbook_id}(confidence={runner_up.confidence:.3f})")

        return f"Selected {selected.runbook_id}: {', '.join(parts)}"
