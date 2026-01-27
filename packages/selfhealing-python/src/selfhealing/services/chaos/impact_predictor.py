"""
Chaos Impact Predictor.

LearningService의 과거 패턴을 활용하여
Dry Run 시 예상 결과를 예측합니다.

Related Modules:
- LearningService.get_patterns(): services/learning/service.py:309-328
- PatternType enum: services/learning/models.py:10-15

Design Principle:
- ExpectedRecoveryScenario를 활용한 예측
- 예측 정확도를 LearningService에 피드백
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class PredictedOutcome:
    """Dry Run 예측 결과."""

    # 예측된 CB 상태
    predicted_cb_state: str = "OPEN"

    # 예측된 복구 시간 (초)
    predicted_recovery_time_seconds: float = 30.0

    # 예상 에러율 증가
    predicted_error_rate_increase_percent: float = 5.0

    # Canary 복구 예상 여부
    predicted_canary_recovery: bool = True

    # 예측 신뢰도 (0.0 ~ 1.0)
    confidence_score: float = 0.5

    # 예측에 사용된 패턴 수
    patterns_used: int = 0

    # 유사 실험 ID 목록
    similar_experiment_ids: list[str] = field(default_factory=list)

    # 권장 사항
    recommendations: list[str] = field(default_factory=list)

    # 승인 필요 여부
    requires_approval: bool = False
    approval_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "predicted_cb_state": self.predicted_cb_state,
            "predicted_recovery_time_seconds": self.predicted_recovery_time_seconds,
            "predicted_error_rate_increase_percent": self.predicted_error_rate_increase_percent,
            "predicted_canary_recovery": self.predicted_canary_recovery,
            "confidence_score": self.confidence_score,
            "patterns_used": self.patterns_used,
            "similar_experiment_ids": self.similar_experiment_ids,
            "recommendations": self.recommendations,
            "requires_approval": self.requires_approval,
            "approval_reason": self.approval_reason,
        }


@dataclass
class ServiceImpact:
    """서비스 영향도 예측."""

    service_name: str
    impact_level: str  # "low", "medium", "high", "critical"
    predicted_latency_increase_ms: float = 0.0
    predicted_error_rate_percent: float = 0.0
    predicted_availability_drop_percent: float = 0.0
    is_direct_target: bool = False
    dependency_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "service_name": self.service_name,
            "impact_level": self.impact_level,
            "predicted_latency_increase_ms": self.predicted_latency_increase_ms,
            "predicted_error_rate_percent": self.predicted_error_rate_percent,
            "predicted_availability_drop_percent": self.predicted_availability_drop_percent,
            "is_direct_target": self.is_direct_target,
            "dependency_chain": self.dependency_chain,
        }


# =============================================================================
# Impact Predictor
# =============================================================================


class ImpactPredictor:
    """
    Chaos 실험 영향도 예측기.

    LearningService의 과거 FAILURE 패턴을 분석하여
    유사한 실험의 예상 결과를 예측합니다.

    Usage:
        predictor = ImpactPredictor()
        prediction = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service="payment-api",
            config={"latency_ms": 500},
        )
    """

    def __init__(self, min_confidence: float = 0.6):
        """
        Args:
            min_confidence: 패턴 필터링 최소 신뢰도
        """
        self._min_confidence = min_confidence

    def predict_outcome(
        self,
        experiment_type: str,
        target_service: str,
        config: dict[str, Any] | None = None,
    ) -> PredictedOutcome:
        """
        실험 결과 예측.

        Args:
            experiment_type: 실험 유형 (latency_injection, failure_injection, etc.)
            target_service: 대상 서비스
            config: 실험 설정

        Returns:
            PredictedOutcome: 예측 결과
        """
        try:
            from selfhealing.services.learning.models import PatternType
            from selfhealing.services.learning.service import LearningService

            learning = LearningService()

            # 과거 FAILURE 패턴 조회
            failure_patterns = learning.get_patterns(
                pattern_type=PatternType.FAILURE,
                min_confidence=self._min_confidence,
            )

            # 유사 패턴 필터링
            similar_patterns = self._find_similar_patterns(
                patterns=failure_patterns,
                experiment_type=experiment_type,
                target_service=target_service,
            )

            if not similar_patterns:
                logger.info(
                    f"[ImpactPredictor] No similar patterns found for "
                    f"{experiment_type} on {target_service}"
                )
                # 기본 예측 반환 (패턴 없음)
                return self._generate_default_prediction(
                    experiment_type=experiment_type,
                    target_service=target_service,
                    config=config,
                )

            # 패턴 기반 예측
            return self._calculate_prediction(
                patterns=similar_patterns,
                experiment_type=experiment_type,
                target_service=target_service,
                config=config,
            )

        except Exception as e:
            logger.warning(f"[ImpactPredictor] Prediction failed: {e}")
            return PredictedOutcome(
                confidence_score=0.1,
                recommendations=["예측 실패 - 수동 검토 권장"],
            )

    def predict_service_impact(
        self,
        target_service: str,
        experiment_type: str,
        config: dict[str, Any] | None = None,
    ) -> list[ServiceImpact]:
        """
        서비스 영향도 예측.

        Args:
            target_service: 대상 서비스
            experiment_type: 실험 유형
            config: 실험 설정

        Returns:
            List[ServiceImpact]: 영향받는 서비스 목록
        """
        impacts = []

        # 대상 서비스 직접 영향
        direct_impact = self._calculate_direct_impact(
            target_service=target_service,
            experiment_type=experiment_type,
            config=config,
        )
        impacts.append(direct_impact)

        # 종속 서비스 간접 영향 분석
        dependent_services = self._get_dependent_services(target_service)
        for dep_service in dependent_services:
            indirect_impact = self._calculate_indirect_impact(
                service_name=dep_service,
                source_service=target_service,
                direct_impact=direct_impact,
            )
            impacts.append(indirect_impact)

        return impacts

    def _find_similar_patterns(
        self,
        patterns: list,
        experiment_type: str,
        target_service: str,
    ) -> list:
        """유사 패턴 검색."""
        similar = []

        for pattern in patterns:
            features = pattern.features or {}

            # 동일 실험 유형
            if features.get("experiment_type") == experiment_type:
                similar.append(pattern)
                continue

            # 동일 서비스
            if features.get("target_service") == target_service:
                similar.append(pattern)

        return similar

    def _calculate_prediction(
        self,
        patterns: list,
        experiment_type: str,
        target_service: str,
        config: dict[str, Any] | None = None,
    ) -> PredictedOutcome:
        """패턴 기반 예측 계산."""
        if not patterns:
            return PredictedOutcome()

        # 복구 시간 평균
        recovery_times = [
            p.features.get("recovery_time_seconds", 30)
            for p in patterns
            if p.features.get("recovery_time_seconds")
        ]
        avg_recovery = (
            sum(recovery_times) / len(recovery_times) if recovery_times else 30.0
        )

        # 에러율 평균
        error_rates = [
            p.features.get("error_rate_during_experiment", 5.0)
            for p in patterns
            if p.features.get("error_rate_during_experiment")
        ]
        avg_error_rate = sum(error_rates) / len(error_rates) if error_rates else 5.0

        # 신뢰도 (패턴 수 및 개별 신뢰도 기반)
        confidence = min(0.95, 0.5 + (len(patterns) * 0.1))
        avg_pattern_confidence = sum(p.confidence for p in patterns) / len(patterns)
        final_confidence = (confidence + avg_pattern_confidence) / 2

        # 유사 실험 ID
        similar_ids = [
            p.features.get("experiment_id", p.name) for p in patterns[:5]  # 최대 5개
        ]

        # 권장 사항 생성
        recommendations = self._generate_recommendations(
            avg_recovery=avg_recovery,
            avg_error_rate=avg_error_rate,
            experiment_type=experiment_type,
            config=config,
        )

        # 승인 필요 여부 결정
        requires_approval, approval_reason = self._check_approval_requirement(
            avg_error_rate=avg_error_rate,
            avg_recovery=avg_recovery,
            experiment_type=experiment_type,
        )

        return PredictedOutcome(
            predicted_cb_state="OPEN",
            predicted_recovery_time_seconds=avg_recovery,
            predicted_error_rate_increase_percent=avg_error_rate,
            predicted_canary_recovery=True,
            confidence_score=final_confidence,
            patterns_used=len(patterns),
            similar_experiment_ids=similar_ids,
            recommendations=recommendations,
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )

    def _generate_default_prediction(
        self,
        experiment_type: str,
        target_service: str,
        config: dict[str, Any] | None = None,
    ) -> PredictedOutcome:
        """기본 예측 생성 (과거 패턴 없을 때)."""
        # 실험 유형별 기본값
        defaults = {
            "latency_injection": {
                "recovery_time": 30.0,
                "error_rate": 5.0,
                "cb_state": "HALF_OPEN",
            },
            "failure_injection": {
                "recovery_time": 60.0,
                "error_rate": 20.0,
                "cb_state": "OPEN",
            },
            "resource_exhaustion": {
                "recovery_time": 120.0,
                "error_rate": 15.0,
                "cb_state": "OPEN",
            },
        }

        default = defaults.get(
            experiment_type,
            {"recovery_time": 45.0, "error_rate": 10.0, "cb_state": "HALF_OPEN"},
        )

        # 승인 필요 여부 결정 (기본 예측에서도 적용)
        requires_approval, approval_reason = self._check_approval_requirement(
            avg_error_rate=default["error_rate"],
            avg_recovery=default["recovery_time"],
            experiment_type=experiment_type,
        )

        return PredictedOutcome(
            predicted_cb_state=default["cb_state"],
            predicted_recovery_time_seconds=default["recovery_time"],
            predicted_error_rate_increase_percent=default["error_rate"],
            confidence_score=0.3,  # 낮은 신뢰도 (과거 데이터 없음)
            patterns_used=0,
            recommendations=[
                f"'{target_service}' 서비스에 대한 과거 실험 데이터가 없습니다.",
                "첫 실험은 INSTANCE 레벨에서 시작하는 것을 권장합니다.",
                "실험 후 결과를 모니터링하고 패턴을 학습시키세요.",
            ],
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )

    def _generate_recommendations(
        self,
        avg_recovery: float,
        avg_error_rate: float,
        experiment_type: str,
        config: dict[str, Any] | None = None,
    ) -> list[str]:
        """권장 사항 생성."""
        recommendations = []

        if avg_recovery > 60:
            recommendations.append(
                f"평균 복구 시간이 {avg_recovery:.1f}초로 길습니다. "
                "Circuit Breaker timeout 설정 검토를 권장합니다."
            )

        if avg_error_rate > 10:
            recommendations.append(
                f"평균 에러율 증가가 {avg_error_rate:.1f}%로 높습니다. "
                "점진적 트래픽 증가를 권장합니다."
            )

        if experiment_type == "failure_injection" and config:
            failure_rate = config.get("failure_rate", 100)
            if failure_rate > 50:
                recommendations.append(
                    f"failure_rate {failure_rate}%는 높은 편입니다. "
                    "25% 이하로 시작하는 것을 권장합니다."
                )

        if not recommendations:
            recommendations.append("과거 실험 결과가 양호합니다. 실험 진행 가능합니다.")

        return recommendations

    def _check_approval_requirement(
        self,
        avg_error_rate: float,
        avg_recovery: float,
        experiment_type: str,
    ) -> tuple[bool, str]:
        """승인 필요 여부 결정."""
        if avg_error_rate > 30:
            return True, f"예상 에러율 {avg_error_rate:.1f}%가 30%를 초과합니다."

        if avg_recovery > 120:
            return True, f"예상 복구 시간 {avg_recovery:.1f}초가 2분을 초과합니다."

        if experiment_type == "resource_exhaustion":
            return True, "resource_exhaustion 실험은 승인이 필요합니다."

        return False, ""

    def _calculate_direct_impact(
        self,
        target_service: str,
        experiment_type: str,
        config: dict[str, Any] | None = None,
    ) -> ServiceImpact:
        """직접 영향 계산."""
        # 실험 유형별 영향도 계산
        if experiment_type == "latency_injection":
            latency_ms = config.get("latency_ms", 500) if config else 500
            impact_level = (
                "high" if latency_ms > 1000 else "medium" if latency_ms > 300 else "low"
            )
            return ServiceImpact(
                service_name=target_service,
                impact_level=impact_level,
                predicted_latency_increase_ms=float(latency_ms),
                predicted_error_rate_percent=5.0 if latency_ms > 1000 else 2.0,
                is_direct_target=True,
            )

        elif experiment_type == "failure_injection":
            failure_rate = config.get("failure_rate", 100) if config else 100
            impact_level = (
                "critical"
                if failure_rate > 75
                else (
                    "high"
                    if failure_rate > 50
                    else "medium" if failure_rate > 25 else "low"
                )
            )
            return ServiceImpact(
                service_name=target_service,
                impact_level=impact_level,
                predicted_error_rate_percent=float(failure_rate),
                is_direct_target=True,
            )

        else:
            return ServiceImpact(
                service_name=target_service,
                impact_level="medium",
                is_direct_target=True,
            )

    def _calculate_indirect_impact(
        self,
        service_name: str,
        source_service: str,
        direct_impact: ServiceImpact,
    ) -> ServiceImpact:
        """간접 영향 계산."""
        # 간접 영향은 직접 영향의 50% 감쇠
        dampening = 0.5

        return ServiceImpact(
            service_name=service_name,
            impact_level=(
                "low" if direct_impact.impact_level in ["low", "medium"] else "medium"
            ),
            predicted_latency_increase_ms=direct_impact.predicted_latency_increase_ms
            * dampening,
            predicted_error_rate_percent=direct_impact.predicted_error_rate_percent
            * dampening,
            is_direct_target=False,
            dependency_chain=[source_service],
        )

    def _get_dependent_services(self, target_service: str) -> list[str]:
        """종속 서비스 조회."""
        # TODO: ServiceDependencyGraph 연동
        # 현재는 간단한 맵 사용
        dependency_map = {
            "payment-api": ["order-service", "notification-service"],
            "order-service": ["inventory-service", "shipping-service"],
            "user-service": ["notification-service", "analytics-service"],
        }

        return dependency_map.get(target_service, [])


# =============================================================================
# Singleton
# =============================================================================

_instance: ImpactPredictor | None = None


def get_impact_predictor() -> ImpactPredictor:
    """싱글톤 인스턴스 반환."""
    global _instance
    if _instance is None:
        _instance = ImpactPredictor()
    return _instance
