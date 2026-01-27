"""
Decision Engine - 메트릭 기반 조정 결정

실시간 메트릭을 분석하여 파라미터 조정을 제안합니다.

Netflix Hystrix, Google Autopilot 스타일의 자율 조정 엔진

설정값은 DecisionEngineSettings를 통해 환경변수로 오버라이드 가능:
- SELFHEALING_DECISION_MIN_CHANGE_RATIO
- SELFHEALING_DECISION_CONFIDENCE_* (신뢰도 매핑)
- SELFHEALING_DECISION_STABILITY_* (안정성 계수)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from selfhealing.settings.decision_engine import get_decision_engine_settings

logger = logging.getLogger(__name__)


class AdjustmentPriority(str, Enum):
    """조정 우선순위"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AdjustmentDecision:
    """조정 결정"""

    parameter: str
    current_value: float
    suggested_value: float
    reason: str
    confidence: float  # 0.0 ~ 1.0
    priority: AdjustmentPriority = AdjustmentPriority.MEDIUM
    metric_snapshot: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AdjustmentRule:
    """조정 규칙"""

    parameter: str
    metric: str
    condition: Callable[
        [float, float], bool
    ]  # (current_value, metric_value) -> should_adjust
    adjustment: Callable[
        [float, float], float
    ]  # (current_value, metric_value) -> new_value
    reason: str
    priority: AdjustmentPriority = AdjustmentPriority.MEDIUM
    min_confidence: float = 0.5


class ConfigProvider(Protocol):
    """설정 제공자 프로토콜"""

    def get(self, key: str, default: Any = None) -> Any:
        """설정 값 조회"""
        ...


class DecisionEngine:
    """
    조정 결정 엔진

    메트릭 패턴을 분석하여 파라미터 조정 제안

    기본 규칙:
    - timeout_ms: P99 레이턴시가 타임아웃의 80% 이상이면 상향
    - retry_count: 재시도 소진율이 10% 이상이면 증가
    - circuit_breaker_threshold: 에러율이 CB 임계값에 근접하면 상향
    - jitter_range: 재시도 충돌율이 높으면 확대
    """

    # 기본 조정 규칙
    DEFAULT_RULES: list[AdjustmentRule] = [
        AdjustmentRule(
            parameter="timeout_ms",
            metric="p99_latency_ms",
            condition=lambda current, metric: metric > current * 0.8,
            adjustment=lambda current, metric: min(current * 1.2, 10000),
            reason="P99 레이턴시가 타임아웃의 80% 이상 → 타임아웃 상향",
            priority=AdjustmentPriority.MEDIUM,
        ),
        AdjustmentRule(
            parameter="retry_count",
            metric="retry_exhausted_rate",
            condition=lambda current, metric: metric > 0.1,  # 10% 이상 재시도 소진
            adjustment=lambda current, metric: min(current + 1, 5),
            reason="재시도 소진율 10% 이상 → 재시도 횟수 증가",
            priority=AdjustmentPriority.MEDIUM,
        ),
        AdjustmentRule(
            parameter="circuit_breaker_threshold",
            metric="error_rate",
            condition=lambda current, metric: metric > current * 0.9,
            adjustment=lambda current, metric: min(current * 1.1, 0.8),
            reason="에러율이 CB 임계값에 근접 → 임계값 상향 조정",
            priority=AdjustmentPriority.HIGH,
        ),
        AdjustmentRule(
            parameter="jitter_range",
            metric="retry_collision_rate",
            condition=lambda current, metric: metric > 0.05,  # 5% 이상 충돌
            adjustment=lambda current, metric: current * 1.5,
            reason="재시도 충돌율 높음 → 지터 범위 확대",
            priority=AdjustmentPriority.LOW,
        ),
        AdjustmentRule(
            parameter="rate_limit_rps",
            metric="throttle_rate",
            condition=lambda current, metric: metric < 0.01
            and current < 5000,  # 거의 스로틀링 없음
            adjustment=lambda current, metric: current * 1.1,
            reason="스로틀링 발생 낮음 → Rate Limit 상향 가능",
            priority=AdjustmentPriority.LOW,
        ),
    ]

    @property
    def MIN_CHANGE_RATIO(self) -> float:
        """변경이 의미있으려면 최소 이 비율 이상 변경 필요 (기본 5%)"""
        return get_decision_engine_settings().min_change_ratio

    def __init__(
        self,
        config_provider: ConfigProvider,
        custom_rules: list[AdjustmentRule] | None = None,
        enabled: bool = True,
    ):
        self.config_provider = config_provider
        self.rules = list(self.DEFAULT_RULES)
        if custom_rules:
            self.rules.extend(custom_rules)
        self.enabled = enabled

        # 분석 이력
        self._history: list[dict[str, Any]] = []

        logger.info(f"[DecisionEngine] Initialized with {len(self.rules)} rules")

    def analyze(self, metrics: dict[str, float]) -> list[AdjustmentDecision]:
        """
        메트릭 분석 및 조정 결정

        Args:
            metrics: 수집된 메트릭 (error_rate, p99_latency_ms 등)

        Returns:
            조정 결정 목록
        """
        if not self.enabled:
            return []

        decisions = []

        for rule in self.rules:
            decision = self._evaluate_rule(rule, metrics)
            if decision:
                decisions.append(decision)

        # 우선순위별 정렬
        decisions.sort(key=lambda d: self._priority_order(d.priority), reverse=True)

        # 분석 이력 저장
        self._record_analysis(metrics, decisions)

        return decisions

    def _evaluate_rule(
        self, rule: AdjustmentRule, metrics: dict[str, float]
    ) -> AdjustmentDecision | None:
        """단일 규칙 평가"""
        metric_value = metrics.get(rule.metric)

        if metric_value is None:
            return None

        try:
            current_value = self.config_provider.get(rule.parameter)
            if current_value is None:
                logger.debug(f"[DecisionEngine] No current value for {rule.parameter}")
                return None

            current_value = float(current_value)
        except (TypeError, ValueError) as e:
            logger.warning(
                f"[DecisionEngine] Invalid current value for {rule.parameter}: {e}"
            )
            return None

        # 조건 평가
        try:
            should_adjust = rule.condition(current_value, metric_value)
        except Exception as e:
            logger.warning(f"[DecisionEngine] Condition evaluation failed: {e}")
            return None

        if not should_adjust:
            return None

        # 새 값 계산
        try:
            suggested_value = rule.adjustment(current_value, metric_value)
        except Exception as e:
            logger.warning(f"[DecisionEngine] Adjustment calculation failed: {e}")
            return None

        # 변경이 의미있는지 확인
        if current_value > 0:
            change_ratio = abs(suggested_value - current_value) / current_value
            if change_ratio < self.MIN_CHANGE_RATIO:
                return None

        # 신뢰도 계산
        confidence = self._calculate_confidence(metrics, rule)

        if confidence < rule.min_confidence:
            logger.debug(
                f"[DecisionEngine] Low confidence ({confidence:.2f}) for {rule.parameter}"
            )
            return None

        return AdjustmentDecision(
            parameter=rule.parameter,
            current_value=current_value,
            suggested_value=suggested_value,
            reason=rule.reason,
            confidence=confidence,
            priority=rule.priority,
            metric_snapshot=metrics.copy(),
        )

    def _calculate_confidence(
        self, metrics: dict[str, float], rule: AdjustmentRule
    ) -> float:
        """
        신뢰도 계산

        샘플 수, 메트릭 변동성 등을 고려.
        DecisionEngineSettings에서 임계값 및 계수 로드.
        """
        settings = get_decision_engine_settings()

        # 기본 신뢰도 (샘플 수 기반 - settings에서 조회)
        sample_count = metrics.get("sample_count", 10)
        sample_confidence = settings.get_sample_confidence(int(sample_count))

        # 메트릭 변동성이 낮을수록 신뢰도 증가 (settings에서 조회)
        variance = metrics.get(f"{rule.metric}_variance", 0)
        mean = metrics.get(rule.metric, 1)
        if mean > 0 and variance > 0:
            cv = (variance**0.5) / mean  # Coefficient of variation
            stability_factor = settings.get_stability_factor(cv)
        else:
            # 변동성 정보 없으면 기본값 유지
            stability_factor = settings.stability_factor_stable

        confidence = sample_confidence * stability_factor
        return min(1.0, max(0.0, confidence))

    def _priority_order(self, priority: AdjustmentPriority) -> int:
        """우선순위 정렬용 숫자 변환"""
        order = {
            AdjustmentPriority.LOW: 1,
            AdjustmentPriority.MEDIUM: 2,
            AdjustmentPriority.HIGH: 3,
            AdjustmentPriority.CRITICAL: 4,
        }
        return order.get(priority, 0)

    def _record_analysis(
        self, metrics: dict[str, float], decisions: list[AdjustmentDecision]
    ):
        """분석 이력 기록"""
        self._history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": metrics,
                "decisions_count": len(decisions),
                "decisions": [
                    {
                        "parameter": d.parameter,
                        "current": d.current_value,
                        "suggested": d.suggested_value,
                        "confidence": d.confidence,
                    }
                    for d in decisions
                ],
            }
        )

        # 최근 100개만 유지
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def add_rule(self, rule: AdjustmentRule) -> None:
        """규칙 추가"""
        self.rules.append(rule)
        logger.info(f"[DecisionEngine] Added rule for {rule.parameter}")

    def remove_rule(self, parameter: str) -> bool:
        """규칙 제거"""
        original_count = len(self.rules)
        self.rules = [r for r in self.rules if r.parameter != parameter]
        removed = len(self.rules) < original_count
        if removed:
            logger.info(f"[DecisionEngine] Removed rule for {parameter}")
        return removed

    def get_rules(self) -> list[dict[str, Any]]:
        """규칙 목록 조회"""
        return [
            {
                "parameter": r.parameter,
                "metric": r.metric,
                "reason": r.reason,
                "priority": r.priority.value,
                "min_confidence": r.min_confidence,
            }
            for r in self.rules
        ]

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """분석 이력 조회"""
        return self._history[-limit:]


__all__ = [
    "DecisionEngine",
    "AdjustmentDecision",
    "AdjustmentRule",
    "AdjustmentPriority",
]
