"""
Emergency Decision Engine — RootCauseAnalysis 기반 비상 레벨 제안.

CorrelationEngine의 근본 원인 분석 결과(RootCauseAnalysis)를 입력으로,
EmergencyLevel 결정을 위한 정보 기반 권고를 생성한다.

역할:
    - 분석 결과의 심각도(score, confidence)와 영향 범위(cascade_depth, affected_services)를
      조합하여 EmergencyLevel을 제안한다.
    - 최종 비상 모드 활성화는 GracefulDegradationManager가 별도로 수행한다.
    - 이 모듈은 "정보 기반 의사결정 보조" 역할만 수행하며,
      직접 비상 모드를 제어하지 않는다.

임계값:
    - LEVEL_3: score ≥ 0.8 AND affected_services ≥ 3 AND cascade_depth ≥ 3
    - LEVEL_2: score ≥ 0.6 AND affected_services ≥ 2
    - LEVEL_1: score ≥ 0.4 AND confidence ≥ 0.5
    - NORMAL: 그 외 (분석 결과가 활성화를 정당화하지 않음)

Usage:
    from selfhealing.services.correlation_engine.emergency_decision import (
        EmergencyDecisionEngine,
        EmergencyDecisionResult,
    )

    engine = EmergencyDecisionEngine()
    decision = engine.evaluate(root_cause_analysis)
    if decision.suggested_level >= EmergencyLevel.LEVEL_1:
        # GracefulDegradationManager에 권고 전달
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel

logger = structlog.get_logger()


# =============================================================================
# 결정 결과 자료구조
# =============================================================================


@dataclass(frozen=True)
class EmergencyDecisionResult:
    """비상 레벨 권고 결과.

    Attributes:
        suggested_level: 권고 EmergencyLevel.
        reason: 사람이 읽을 수 있는 권고 근거.
        primary_service: 근본 원인 서비스 이름.
        primary_score: 근본 원인 확률 점수.
        analysis_confidence: 전체 분석 신뢰도.
        affected_service_count: 영향받은 서비스 수.
        cascade_depth: 최대 연쇄 깊이.
        factors: 의사결정에 사용된 상세 지표.
    """

    suggested_level: EmergencyLevel
    reason: str
    primary_service: str
    primary_score: float
    analysis_confidence: float
    affected_service_count: int
    cascade_depth: int
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "suggested_level": self.suggested_level.value,
            "suggested_level_name": self.suggested_level.name,
            "reason": self.reason,
            "primary_service": self.primary_service,
            "primary_score": self.primary_score,
            "analysis_confidence": self.analysis_confidence,
            "affected_service_count": self.affected_service_count,
            "cascade_depth": self.cascade_depth,
            "factors": self.factors,
        }


# =============================================================================
# Emergency Decision Engine
# =============================================================================

# 레벨별 임계값 상수
LEVEL_3_MIN_SCORE = 0.8
LEVEL_3_MIN_SERVICES = 3
LEVEL_3_MIN_CASCADE_DEPTH = 3

LEVEL_2_MIN_SCORE = 0.6
LEVEL_2_MIN_SERVICES = 2

LEVEL_1_MIN_SCORE = 0.4
LEVEL_1_MIN_CONFIDENCE = 0.5


class EmergencyDecisionEngine:
    """RootCauseAnalysis → EmergencyLevel 권고 엔진.

    분석 결과의 다차원 지표를 조합하여 비상 레벨을 결정한다.
    GracefulDegradationManager를 직접 호출하지 않으며,
    결정 결과를 반환하여 호출부가 활성화 여부를 판단한다.

    Args:
        score_threshold_override: 레벨별 score 임계값 오버라이드.
            테스트 시 임계값을 동적으로 조정하는 데 사용.
    """

    def __init__(
        self,
        score_threshold_override: dict[str, float] | None = None,
    ) -> None:
        self._overrides = score_threshold_override or {}

    def evaluate(self, analysis: RootCauseAnalysis) -> EmergencyDecisionResult:
        """RootCauseAnalysis를 평가하여 비상 레벨을 권고한다.

        Args:
            analysis: 근본 원인 분석 결과.

        Returns:
            EmergencyDecisionResult 권고 결과.
        """
        primary = analysis.primary_cause
        score = primary.score
        confidence = analysis.confidence
        affected_count = len(primary.affected_services)
        depth = primary.cascade_depth

        factors = {
            "primary_score": score,
            "confidence": confidence,
            "affected_services": primary.affected_services,
            "cascade_depth": depth,
            "candidate_count": len(analysis.candidates),
        }

        # LEVEL_3: 심각한 장애 — 높은 확률 + 넓은 영향 범위 + 깊은 연쇄
        l3_score = self._overrides.get("level_3_score", LEVEL_3_MIN_SCORE)
        if score >= l3_score and affected_count >= LEVEL_3_MIN_SERVICES and depth >= LEVEL_3_MIN_CASCADE_DEPTH:
            reason = (
                f"[{primary.event_node.service_name}] 근본 원인 확률 {score:.0%}, "
                f"{affected_count}개 서비스 영향, cascade 깊이 {depth}단계 "
                f"→ LEVEL_3 (심각) 권고"
            )
            return self._build_result(
                EmergencyLevel.LEVEL_3,
                reason,
                analysis,
                factors,
            )

        # LEVEL_2: 중간 장애 — 상당한 확률 + 복수 서비스 영향
        l2_score = self._overrides.get("level_2_score", LEVEL_2_MIN_SCORE)
        if score >= l2_score and affected_count >= LEVEL_2_MIN_SERVICES:
            reason = (
                f"[{primary.event_node.service_name}] 근본 원인 확률 {score:.0%}, "
                f"{affected_count}개 서비스 영향 → LEVEL_2 (중간) 권고"
            )
            return self._build_result(
                EmergencyLevel.LEVEL_2,
                reason,
                analysis,
                factors,
            )

        # LEVEL_1: 경미한 장애 — 중간 확률 + 최소 신뢰도
        l1_score = self._overrides.get("level_1_score", LEVEL_1_MIN_SCORE)
        if score >= l1_score and confidence >= LEVEL_1_MIN_CONFIDENCE:
            reason = (
                f"[{primary.event_node.service_name}] 근본 원인 확률 {score:.0%}, "
                f"분석 신뢰도 {confidence:.0%} → LEVEL_1 (경미) 권고"
            )
            return self._build_result(
                EmergencyLevel.LEVEL_1,
                reason,
                analysis,
                factors,
            )

        # NORMAL: 비상 모드 불필요
        reason = (
            f"분석 결과가 비상 모드 활성화를 정당화하지 않음 "
            f"(score={score:.2f}, confidence={confidence:.2f}, "
            f"affected={affected_count})"
        )
        return self._build_result(
            EmergencyLevel.NORMAL,
            reason,
            analysis,
            factors,
        )

    @staticmethod
    def _build_result(
        level: EmergencyLevel,
        reason: str,
        analysis: RootCauseAnalysis,
        factors: dict[str, Any],
    ) -> EmergencyDecisionResult:
        """EmergencyDecisionResult 생성 헬퍼."""
        primary = analysis.primary_cause
        return EmergencyDecisionResult(
            suggested_level=level,
            reason=reason,
            primary_service=primary.event_node.service_name,
            primary_score=primary.score,
            analysis_confidence=analysis.confidence,
            affected_service_count=len(primary.affected_services),
            cascade_depth=primary.cascade_depth,
            factors=factors,
        )
