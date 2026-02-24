"""
런북 패턴 매칭 데이터 모델.

트리거 조건(PatternCondition), 매칭 결과(MatchResult), 최종 선택(MatchSelectionResult)
등 패턴 매칭에 필요한 모든 데이터 구조를 정의한다.

Reference:
    docs/self_healing/middleware_system/273_RUNBOOK_PATTERN_MATCHER.md §3
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# 조건 비교 연산자
# =============================================================================


class ConditionOperator(str, Enum):
    """조건 비교 연산자."""

    GT = "gt"
    """초과 (>)"""

    GTE = "gte"
    """이상 (>=)"""

    LT = "lt"
    """미만 (<)"""

    LTE = "lte"
    """이하 (<=)"""

    EQ = "eq"
    """동등 (==)"""

    NEQ = "neq"
    """부등 (!=)"""

    IN = "in"
    """포함 (in [...])"""

    CONTAINS = "contains"
    """문자열 포함"""

    REGEX = "regex"
    """정규식 매칭 (=~ 연산자, PromQL 호환)"""


# =============================================================================
# 라벨 필터
# =============================================================================


@dataclass
class LabelFilter:
    """메트릭 라벨 필터 — MSA 환경에서 대상 서비스/엔드포인트를 특정.

    PromQL의 라벨 셀렉터 모델과 일치하도록 설계.

    Example:
        # 결제 서비스의 checkout 엔드포인트 에러율만 필터링
        LabelFilter(key="service", operator=ConditionOperator.EQ, value="payment")
        LabelFilter(key="endpoint", operator=ConditionOperator.REGEX, value="/api/v[12]/.*")

        # 여러 리전을 하나로 묶어 평가
        LabelFilter(key="region", operator=ConditionOperator.IN, value=["ap-northeast-2", "us-west-2"])
    """

    key: str
    """라벨 키 (예: "service", "endpoint", "region")"""

    operator: ConditionOperator
    """EQ, NEQ, IN, REGEX 등"""

    value: str | list[str]
    """값 또는 정규식 패턴"""


# =============================================================================
# 단일 메트릭 조건
# =============================================================================


def _compare(operator: ConditionOperator, current: float | str, threshold: float | str | list[str]) -> bool:
    """연산자에 따른 값 비교 수행."""
    if operator == ConditionOperator.GT:
        return float(current) > float(threshold)
    elif operator == ConditionOperator.GTE:
        return float(current) >= float(threshold)
    elif operator == ConditionOperator.LT:
        return float(current) < float(threshold)
    elif operator == ConditionOperator.LTE:
        return float(current) <= float(threshold)
    elif operator == ConditionOperator.EQ:
        return current == threshold
    elif operator == ConditionOperator.NEQ:
        return current != threshold
    elif operator == ConditionOperator.IN:
        if isinstance(threshold, list):
            return str(current) in threshold
        return False
    elif operator == ConditionOperator.CONTAINS:
        return str(threshold) in str(current)
    elif operator == ConditionOperator.REGEX:
        try:
            return bool(re.search(str(threshold), str(current)))
        except re.error:
            logger.warning("regex_pattern_invalid", pattern=threshold)
            return False
    return False


@dataclass
class MetricCondition:
    """단일 메트릭 조건 — state-based 조건으로 현재 상태를 판단한다."""

    metric_name: str
    """메트릭 이름 (예: "error_rate", "db_pool_usage", "latency_p99_ms")"""

    operator: ConditionOperator
    """비교 연산자"""

    threshold: float | str | list[str]
    """임계치"""

    labels: list[LabelFilter] = field(default_factory=list)
    """MSA 라벨 필터링"""

    # Hysteresis — Flapping/Chattering 방지
    clear_threshold: float | None = None
    """해제 조건 임계치. None이면 threshold와 동일."""

    grace_period_seconds: int = 0
    """짧은 단절 허용 시간 (초)"""

    def evaluate(self, current_value: float | str) -> bool:
        """현재 값이 조건을 만족하는지 평가."""
        return _compare(self.operator, current_value, self.threshold)


# =============================================================================
# 이벤트 조건
# =============================================================================


@dataclass
class EventCondition:
    """특정 EventType 발생 조건."""

    event_type: str
    """EventType.value (예: "circuit_breaker_opened")"""

    source_filter: str | None = None
    """이벤트 소스 필터 (예: "payment_service")"""

    data_filter: dict[str, Any] = field(default_factory=dict)
    """이벤트 data 필드 필터"""


# =============================================================================
# 트리거 조건 (AND 조합)
# =============================================================================


@dataclass
class PatternCondition:
    """런북 트리거 조건 — AND 조합.

    metric_conditions: 모든 메트릭 조건이 동시 만족해야 함 (AND)
    event_conditions: 하나 이상의 이벤트가 발생해야 함 (OR trigger)

    메트릭 조건이 AND이므로 조건이 하나라도 미충족이면 매칭 실패(False).
    부분 매칭(Partial Match)은 허용하지 않는다.
    """

    metric_conditions: list[MetricCondition] = field(default_factory=list)
    """메트릭 조건 목록 (AND 결합)"""

    event_conditions: list[EventCondition] = field(default_factory=list)
    """이벤트 조건 목록 (OR 트리거)"""

    min_duration_seconds: int = 0
    """조건 지속 시간 (짧은 스파이크 필터링)"""

    def evaluate(self, metrics: dict[str, float], triggered_event: str | None = None) -> bool:
        """모든 조건을 평가하여 매칭 여부 반환.

        AND 게이트: 모든 메트릭 조건이 충족되어야 True.
        OR 트리거: event_conditions 중 하나라도 매칭되면 트리거 인정.
        """
        # 1단계: 메트릭 AND 게이트 — 하나라도 미충족이면 즉시 False
        for mc in self.metric_conditions:
            value = metrics.get(mc.metric_name)
            if value is None or not mc.evaluate(value):
                return False

        # 2단계: 이벤트 OR 트리거
        if self.event_conditions and triggered_event is not None:
            return any(ec.event_type == triggered_event for ec in self.event_conditions)

        # event_conditions가 비어있으면 메트릭만으로 매칭 (Proactive 경로)
        return not self.event_conditions or triggered_event is None


# =============================================================================
# 매칭 결과
# =============================================================================


@dataclass
class MatchResult:
    """패턴 매칭 결과.

    event_context 필드는 원본 SelfHealingEvent의 데이터를 보존하여
    Executor에서 런북 Step 파라미터 치환에 사용한다.
    """

    runbook_id: str
    """매칭된 런북 ID"""

    confidence: float
    """신뢰도 (0.0 ~ 1.0)"""

    matched_conditions: list[str]
    """매칭된 조건 설명 목록"""

    historical_success_rate: float | None
    """과거 실행 성공률"""

    similar_pattern_count: int
    """LearningService에서 유사 패턴 수"""

    triggered_by_event: str | None
    """트리거 이벤트 타입"""

    metric_snapshot: dict[str, float]
    """매칭 시점 메트릭 스냅샷"""

    event_context: dict[str, Any] = field(default_factory=dict)
    """원본 이벤트 data + source (Executor 파라미터 치환용)"""

    runner_up_runbook_ids: list[str] = field(default_factory=list)
    """탈락 후보 런북 ID 목록"""


# =============================================================================
# 최종 선택 결과
# =============================================================================


@dataclass
class MatchSelectionResult:
    """최종 런북 선택 결과 — 1위 + Runner-up 전체 보존.

    RootCauseAnalysis가 primary_cause + candidates 리스트를 모두 포함하듯,
    런북 선택도 1위와 전체 후보를 함께 보존한다.
    """

    selected: MatchResult
    """1위 런북 (confidence 최고)"""

    all_candidates: list[MatchResult]
    """전체 후보 (confidence 내림차순)"""

    selection_reason: str
    """선택 근거 설명"""
