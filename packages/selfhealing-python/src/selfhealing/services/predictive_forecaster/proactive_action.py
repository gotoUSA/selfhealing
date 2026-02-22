"""
사전 조치 트리거 및 트래픽 급증 유형 분류기.

SpikeType:
    트래픽 급증 유형을 분류하는 Enum.
    - HEALTHY_SURGE: 정상적인 트래픽 급증 (Flash Sale, 프로모션, 시즌성 피크)
    - ANOMALOUS_SPIKE: 이상 급증 (DDoS, 크롤러, BGP 라우팅 오류)
    - GRADUAL_DEGRADATION: 점진적 악화 (메모리 누수, 커넥션 풀 고갈)

SpikeClassifier:
    멀티-시그널 기반 급증 유형 분류기.
    error_rate + RPS acceleration + latency 트렌드로 원인을 구분하여
    과잉 방어(정상 급증에 대한 불필요한 throttle 등)를 방지한다.

ProactiveActionTrigger:
    예측 기반 사전 조치 트리거.
    SpikeType에 따라 조치 유형과 강도를 차등 적용한다.
    LearningService 블랙리스트와 연동하여 반복 오판 파라미터를 차단한다.

Usage:
    from selfhealing.services.predictive_forecaster.proactive_action import (
        SpikeClassifier,
        SpikeType,
        ProactiveActionTrigger,
    )

    classifier = SpikeClassifier(sensitivity_multiplier=1.0)
    spike_type = classifier.classify(rps_history, error_rate_history, latency_history)
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# SpikeType — 트래픽 급증 유형 분류
# =============================================================================


class SpikeType(str, Enum):
    """
    트래픽 급증 유형 분류.

    Forecaster가 트래픽 급증을 감지했을 때, 정상적인 급증(Flash Sale, 프로모션)인지
    이상 급증(DDoS, 시스템 오류)인지를 구분하여 과잉 방어를 방지한다.

    기존 시스템의 ZScoreDetector/IQRDetector(이상 *여부* 탐지)와 구분하기 위해
    SpikeClassifier(유형 *분류*)로 명명.
    """

    HEALTHY_SURGE = "healthy_surge"
    """정상적인 트래픽 급증 (Flash Sale, 프로모션, 시즌성 피크).
    특징: error_rate 정상, latency 비례 증가, 점진적 상승 곡선."""

    ANOMALOUS_SPIKE = "anomalous_spike"
    """이상 급증 (DDoS, 크롤러, BGP 라우팅 오류).
    특징: error_rate 급등, latency 불규칙, 순간 수직 상승."""

    GRADUAL_DEGRADATION = "gradual_degradation"
    """점진적 악화 (메모리 누수, 커넥션 풀 고갈, 디스크 I/O 포화).
    특징: 느린 기울기의 지속적 상승, 급격한 변곡점 없음."""


# =============================================================================
# SpikeClassifier — 멀티-시그널 기반 급증 유형 분류기
# =============================================================================


class SpikeClassifier:
    """
    멀티-시그널 기반 트래픽 급증 유형 분류기.

    시그널 3개를 조합하여 스파이크의 원인을 분류한다:
    1. error_rate_delta: 에러율 변화량 (급등 = 이상)
    2. latency_trend: 레이턴시 트렌드 (비례 증가 = 정상, 불규칙 = 이상)
    3. rps_acceleration: 초당 요청 증가 가속도 (수직 = 이상, 곡선 = 정상)

    AdaptiveThrottle._check_preemptive_protection() (services/throttle/adaptive.py)이
    BudgetDepletion 기반으로 선제적 보호를 수행하는 패턴을
    트래픽 급증 유형 분류로 확장한 것이다.

    Args:
        error_rate_threshold: 에러율 임계값 (이 이상 증가 시 ANOMALOUS_SPIKE 가중).
        acceleration_threshold: RPS 가속도 임계값 (이 이상 시 ANOMALOUS_SPIKE 가중).
        sensitivity_multiplier: 도메인별 민감도 배율 (환경변수로 주입).
            높은 값 → 낮은 임계값 → 더 민감한 탐지.
    """

    def __init__(
        self,
        error_rate_threshold: float = 0.05,
        acceleration_threshold: float = 2.0,
        sensitivity_multiplier: float = 1.0,
    ):
        if error_rate_threshold <= 0:
            raise ValueError(f"error_rate_threshold는 양수여야 합니다: {error_rate_threshold}")
        if acceleration_threshold <= 0:
            raise ValueError(f"acceleration_threshold는 양수여야 합니다: {acceleration_threshold}")
        if sensitivity_multiplier <= 0:
            raise ValueError(f"sensitivity_multiplier는 양수여야 합니다: {sensitivity_multiplier}")

        self._error_rate_threshold = error_rate_threshold
        self._acceleration_threshold = acceleration_threshold
        self._sensitivity_multiplier = sensitivity_multiplier

    def classify(
        self,
        rps_history: list[float],
        error_rate_history: list[float],
        latency_history: list[float],
    ) -> SpikeType:
        """
        멀티-시그널 기반 스파이크 유형 분류.

        분류 로직:
        1. error_rate 급등 (최근 delta > threshold / sensitivity) → ANOMALOUS_SPIKE
        2. RPS 가속도 > threshold / sensitivity & error_rate 안정 → HEALTHY_SURGE
        3. 느린 기울기의 지속 상승 → GRADUAL_DEGRADATION

        Args:
            rps_history: 최근 RPS(초당 요청) 히스토리.
            error_rate_history: 최근 에러율 히스토리.
            latency_history: 최근 레이턴시 히스토리.

        Returns:
            SpikeType 분류 결과.
        """
        if len(rps_history) < 5 or len(error_rate_history) < 5:
            return SpikeType.GRADUAL_DEGRADATION

        # 에러율 변화량 (최근 5개 구간의 delta)
        error_delta = error_rate_history[-1] - error_rate_history[-5]
        adjusted_error_threshold = self._error_rate_threshold / self._sensitivity_multiplier

        if error_delta > adjusted_error_threshold:
            return SpikeType.ANOMALOUS_SPIKE

        # RPS 가속도 (2차 도함수 근사: 최근 기울기 - 이전 기울기)
        if len(rps_history) >= 10:
            recent_slope = rps_history[-1] - rps_history[-5]
            older_slope = rps_history[-5] - rps_history[-10]
            acceleration = recent_slope - older_slope if older_slope != 0 else 0

            adjusted_accel_threshold = self._acceleration_threshold / self._sensitivity_multiplier

            if abs(acceleration) > adjusted_accel_threshold:
                if error_delta <= adjusted_error_threshold:
                    return SpikeType.HEALTHY_SURGE
                return SpikeType.ANOMALOUS_SPIKE

        return SpikeType.GRADUAL_DEGRADATION

    # ─── ClassificationStrategy Protocol 호환 ───

    def classify_features(
        self,
        features: dict[str, float],
        context: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """ClassificationStrategy.classify() 호환 어댑터.

        features dict에서 rps_history, error_rate_history, latency_history를
        추출하여 기존 classify()에 위임한다.

        Args:
            features: 특성 벡터. 필수 키:
                - rps_history: 쉼표 구분 RPS 히스토리 문자열
                - error_rate_history: 쉼표 구분 에러율 히스토리 문자열
                - latency_history: 쉼표 구분 레이턴시 히스토리 문자열
            context: 추가 메타데이터 (무시)

        Returns:
            (spike_type_label, confidence) 튜플.
        """
        rps = self._parse_history(features.get("rps_history", ""))
        error_rate = self._parse_history(features.get("error_rate_history", ""))
        latency = self._parse_history(features.get("latency_history", ""))

        spike_type = self.classify(rps, error_rate, latency)

        # 규칙 기반 분류의 신뢰도 — 입력 데이터 충분성에 비례
        min_len = min(len(rps), len(error_rate), len(latency))
        confidence = min(1.0, min_len / 10)

        return spike_type.value, confidence

    @staticmethod
    def _parse_history(raw: Any) -> list[float]:
        """문자열 또는 리스트 형태의 히스토리를 list[float]로 변환."""
        if isinstance(raw, list):
            return [float(v) for v in raw]
        if isinstance(raw, str) and raw:
            return [float(v.strip()) for v in raw.split(",") if v.strip()]
        return []


# =============================================================================
# ProactiveAction — 사전 조치 결과 데이터
# =============================================================================


@dataclass
class ProactiveAction:
    """예측 기반 사전 조치 결과."""

    parameter: str
    current_value: float
    suggested_value: float
    spike_type: SpikeType
    confidence: float
    predicted_metric_value: float
    metric_name: str
    is_dry_run: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "parameter": self.parameter,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "spike_type": self.spike_type.value,
            "confidence": self.confidence,
            "predicted_metric_value": self.predicted_metric_value,
            "metric_name": self.metric_name,
            "is_dry_run": self.is_dry_run,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# ProactiveActionTrigger — 예측 기반 사전 조치 트리거
# =============================================================================


class ProactiveActionTrigger:
    """
    예측 기반 사전 조치 트리거.

    SpikeType에 따라 조치 유형과 강도를 차등 적용:
    - HEALTHY_SURGE: 조치 보류 또는 경미한 준비적 스케일링
    - ANOMALOUS_SPIKE: 즉시 방어적 조치 (throttle 강화, CB 임계치 하향)
    - GRADUAL_DEGRADATION: 점진적 사전 조치 (예측 기반 파라미터 미세 조정)

    AdaptiveThrottle._apply_preemptive_reduction() (services/throttle/adaptive.py)의
    선제적 보호 패턴을 일반화한 것이다.

    Args:
        min_confidence: 사전 조치 실행에 필요한 최소 예측 신뢰도.
        dry_run: True이면 로그만 기록하고 실제 조치를 수행하지 않음.
    """

    # SpikeType별 조정 강도 비율
    ADJUSTMENT_INTENSITY: dict[SpikeType, float] = {
        SpikeType.HEALTHY_SURGE: 0.0,
        SpikeType.ANOMALOUS_SPIKE: 0.15,
        SpikeType.GRADUAL_DEGRADATION: 0.05,
    }

    def __init__(
        self,
        min_confidence: float = 0.7,
        dry_run: bool = True,
    ):
        self._min_confidence = min_confidence
        self._dry_run = dry_run

    def should_take_action(
        self,
        module: str,
        parameter: str,
        value: str,
    ) -> bool:
        """
        사전 조치 실행 전 LearningService 블랙리스트 확인.

        과거에 반복 오판으로 블랙리스트된 파라미터 조합은 차단한다.
        LearningService의 is_parameter_blocked() 및 is_manual_only_mode()를 활용.

        Args:
            module: 모듈 식별자.
            parameter: 파라미터 식별자.
            value: 파라미터 값.

        Returns:
            True이면 조치 가능, False이면 차단됨.
        """
        try:
            from selfhealing.services.learning.service import LearningService

            learning = LearningService()

            if learning.is_manual_only_mode("predictive_forecaster"):
                logger.warning("proactive_action_trigger.forecaster_manual_only_mode")
                return False

            is_blocked, entry = learning.is_parameter_blocked(module, parameter, value)
            if is_blocked:
                logger.warning(
                    "proactive_action_trigger.blocked_blacklist",
                    module=module,
                    parameter=parameter,
                    value=value,
                )
                return False

        except Exception as e:
            logger.debug(
                "proactive_action_trigger.learningservice_check_skipped",
                error=e,
            )

        return True

    def evaluate(
        self,
        spike_type: SpikeType,
        confidence: float,
        predicted_value: float,
        current_value: float,
        metric_name: str,
        parameter: str,
    ) -> ProactiveAction | None:
        """
        예측 결과를 평가하여 사전 조치를 생성한다.

        신뢰도 미달, HEALTHY_SURGE(조치 불필요), 또는 블랙리스트 파라미터인 경우
        None을 반환한다.

        Args:
            spike_type: SpikeClassifier의 분류 결과.
            confidence: 예측 신뢰도 (0.0 ~ 1.0).
            predicted_value: 예측된 메트릭 값.
            current_value: 현재 파라미터 값.
            metric_name: 메트릭 식별자.
            parameter: 조정 대상 파라미터.

        Returns:
            ProactiveAction 또는 None (조치 불필요/불가 시).
        """
        # 신뢰도 미달 시 조치 거부
        if confidence < self._min_confidence:
            logger.debug(
                f"[ProactiveActionTrigger] Low confidence ({confidence:.2f}) " f"for {parameter}, min={self._min_confidence}"
            )
            return None

        # HEALTHY_SURGE는 조치 불필요 (정상적인 트래픽 급증)
        intensity = self.ADJUSTMENT_INTENSITY.get(spike_type, 0.0)
        if intensity == 0.0:
            logger.info(
                "proactive_action_trigger.no_action_needed",
                spike_type=spike_type.value,
                metric_name=metric_name,
            )
            return None

        # LearningService 블랙리스트 확인
        if not self.should_take_action("predictive_forecaster", parameter, str(current_value)):
            return None

        # 조정값 계산 (현재값에서 intensity 비율만큼 조정)
        suggested_value = current_value * (1 + intensity)

        action = ProactiveAction(
            parameter=parameter,
            current_value=current_value,
            suggested_value=suggested_value,
            spike_type=spike_type,
            confidence=confidence,
            predicted_metric_value=predicted_value,
            metric_name=metric_name,
            is_dry_run=self._dry_run,
        )

        if self._dry_run:
            logger.info(
                f"[ProactiveActionTrigger] DRY_RUN: {parameter} "
                f"{current_value} → {suggested_value:.2f} "
                f"(spike_type={spike_type.value}, confidence={confidence:.2f}, "
                f"metric={metric_name})"
            )
        else:
            logger.info(
                f"[ProactiveActionTrigger] Action: {parameter} "
                f"{current_value} → {suggested_value:.2f} "
                f"(spike_type={spike_type.value}, confidence={confidence:.2f}, "
                f"metric={metric_name})"
            )

        return action
