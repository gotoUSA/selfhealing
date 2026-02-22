"""
Predictive Anomaly Forecaster 서비스.

시계열 예측 모델(Holt Linear / Holt-Winters)로 5~15분 후 메트릭을 예측하여
사전 조치를 수행하는 예측적 이상 탐지 엔진.

기존 DecisionEngine의 반응형(reactive) 구조를 보완하여
임계치 도달 전에 트렌드 기반으로 사전 조치 시간을 확보한다.

핵심 컴포넌트:
- HoltLinearForecaster: 이중지수평활 시계열 예측
- ZScoreDetector / IQRDetector: 이상 탐지
- SpikeClassifier: Flash Sale vs DDoS 급증 유형 분류
- ProactiveActionTrigger: 사전 조치 트리거 + LearningService 연동

Usage:
    from selfhealing.services.predictive_forecaster.service import (
        PredictiveForecasterService,
    )

    service = PredictiveForecasterService()
    service.ingest_metric("p99_latency_ms", 150.0)
    result = service.forecast_and_detect("p99_latency_ms")
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from selfhealing.services.predictive_forecaster.anomaly_detector import (
    IQRDetector,
    ZScoreDetector,
)
from selfhealing.services.predictive_forecaster.proactive_action import (
    ProactiveAction,
    ProactiveActionTrigger,
    SpikeClassifier,
    SpikeType,
)
from selfhealing.services.predictive_forecaster.time_series import (
    EWMAForecaster,
    HoltLinearForecaster,
)
from selfhealing.settings.predictive_forecaster import (
    get_predictive_forecaster_settings,
)

logger = structlog.get_logger()


# =============================================================================
# ForecastResult — 예측 + 이상 탐지 결합 결과
# =============================================================================


@dataclass
class ForecastResult:
    """예측 및 이상 탐지의 결합 결과."""

    metric_name: str
    current_value: float | None
    predicted_value: float | None
    confidence: float
    trend_slope: float
    is_anomaly_zscore: bool
    is_anomaly_iqr: bool
    z_score: float
    spike_type: SpikeType | None
    proactive_action: ProactiveAction | None
    is_warmed_up: bool
    data_point_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "predicted_value": self.predicted_value,
            "confidence": self.confidence,
            "trend_slope": self.trend_slope,
            "is_anomaly_zscore": self.is_anomaly_zscore,
            "is_anomaly_iqr": self.is_anomaly_iqr,
            "z_score": self.z_score,
            "spike_type": self.spike_type.value if self.spike_type else None,
            "proactive_action": (self.proactive_action.to_dict() if self.proactive_action else None),
            "is_warmed_up": self.is_warmed_up,
            "data_point_count": self.data_point_count,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# PredictiveForecasterService
# =============================================================================


class PredictiveForecasterService:
    """
    예측적 이상 탐지 엔진 서비스.

    메트릭별로 HoltLinearForecaster, ZScoreDetector, IQRDetector를 개별 관리하고,
    SpikeClassifier로 급증 유형을 분류한 뒤 ProactiveActionTrigger로 사전 조치를 생성한다.

    기존 시스템 연동:
    - DecisionEngine._calculate_confidence(): 예측 트렌드 기울기를 stability_factor에 반영
    - LearningService: 예측 이상 패턴 학습 + 블랙리스트 연동 + 정확도 메트릭 기록
    - StateBackend: Cold Start 방지를 위한 Forecaster 상태 영속화

    이 서비스 자체는 기존 RuntimeFeedbackLoop의 관찰-조정 루프와
    독립적으로 동작하며, 예측 결과를 DecisionEngine에 컨텍스트로 주입하는 방식이다.
    """

    # 예측 정확도 평가를 위한 오판 카운터 임계값
    MISPREDICTION_BLACKLIST_THRESHOLD = 3

    def __init__(self) -> None:
        settings = get_predictive_forecaster_settings()

        # 메트릭별 Forecaster 인스턴스
        self._forecasters: dict[str, HoltLinearForecaster] = {}
        # 메트릭별 EWMA smoothing 인스턴스
        self._smoothers: dict[str, EWMAForecaster] = {}
        # 메트릭별 Z-Score 탐지기
        self._zscore_detectors: dict[str, ZScoreDetector] = {}
        # 메트릭별 IQR 탐지기
        self._iqr_detectors: dict[str, IQRDetector] = {}

        # SpikeClassifier (전체 서비스에서 하나)
        self._spike_classifier = SpikeClassifier(
            error_rate_threshold=settings.spike_error_rate_threshold,
            acceleration_threshold=settings.spike_acceleration_threshold,
            sensitivity_multiplier=settings.sensitivity_multiplier,
        )

        # ProactiveActionTrigger (전체 서비스에서 하나)
        self._action_trigger = ProactiveActionTrigger(
            min_confidence=settings.min_confidence_for_action,
            dry_run=settings.dry_run,
        )

        # 멀티-시그널 분류를 위한 RPS/에러율/레이턴시 히스토리
        self._rps_history: list[float] = []
        self._error_rate_history: list[float] = []
        self._latency_history: list[float] = []

        # 예측 정확도 추적 (메트릭별 연속 오판 카운터)
        self._misprediction_counts: dict[str, int] = defaultdict(int)
        # 이전 예측값 저장 (정확도 평가용)
        self._last_predictions: dict[str, float] = {}

        # 사전 조치 이력
        self._action_history: list[ProactiveAction] = []

        # 설정 캐시
        self._settings = settings

    def _get_or_create_forecaster(self, metric_name: str) -> HoltLinearForecaster:
        """메트릭별 HoltLinearForecaster 인스턴스 반환 (없으면 생성)."""
        if metric_name not in self._forecasters:
            settings = self._settings
            forecaster = HoltLinearForecaster(
                alpha=settings.ewma_alpha,
                beta=settings.holt_beta,
                warmup_samples=settings.warmup_samples,
                max_history=settings.max_history,
            )
            # StateBackend에서 이전 상태 복원 시도
            forecaster.load_state(metric_name)
            self._forecasters[metric_name] = forecaster
        return self._forecasters[metric_name]

    def _get_or_create_smoother(self, metric_name: str) -> EWMAForecaster:
        """메트릭별 EWMAForecaster smoothing 인스턴스 반환."""
        if metric_name not in self._smoothers:
            self._smoothers[metric_name] = EWMAForecaster(alpha=self._settings.ewma_alpha)
        return self._smoothers[metric_name]

    def _get_or_create_zscore(self, metric_name: str) -> ZScoreDetector:
        """메트릭별 ZScoreDetector 인스턴스 반환."""
        if metric_name not in self._zscore_detectors:
            self._zscore_detectors[metric_name] = ZScoreDetector(
                threshold=self._settings.zscore_threshold,
                window=self._settings.zscore_window,
            )
        return self._zscore_detectors[metric_name]

    def _get_or_create_iqr(self, metric_name: str) -> IQRDetector:
        """메트릭별 IQRDetector 인스턴스 반환."""
        if metric_name not in self._iqr_detectors:
            self._iqr_detectors[metric_name] = IQRDetector(
                multiplier=self._settings.iqr_multiplier,
                window=self._settings.zscore_window,
            )
        return self._iqr_detectors[metric_name]

    # =========================================================================
    # 메트릭 수집
    # =========================================================================

    def ingest_metric(
        self,
        metric_name: str,
        value: float,
        has_adjustment: bool = False,
    ) -> float:
        """
        메트릭 데이터포인트를 수집하여 예측기와 이상 탐지기를 업데이트한다.

        Args:
            metric_name: 메트릭 식별자 (예: "p99_latency_ms", "error_rate").
            value: 관측값.
            has_adjustment: 이 관측 직전에 셀프힐링 개입이 발생했는지 여부.

        Returns:
            현재 smoothed level.
        """
        forecaster = self._get_or_create_forecaster(metric_name)
        level = forecaster.update(value, has_adjustment=has_adjustment)

        # EWMA smoothing도 병렬 업데이트
        smoother = self._get_or_create_smoother(metric_name)
        smoother.update(value)

        # 멀티-시그널 히스토리 업데이트 (SpikeClassifier용)
        if metric_name == "rps" or metric_name == "requests_per_second":
            self._rps_history.append(value)
            if len(self._rps_history) > 200:
                self._rps_history = self._rps_history[-200:]
        elif metric_name == "error_rate":
            self._error_rate_history.append(value)
            if len(self._error_rate_history) > 200:
                self._error_rate_history = self._error_rate_history[-200:]
        elif metric_name in ("p99_latency_ms", "latency_ms"):
            self._latency_history.append(value)
            if len(self._latency_history) > 200:
                self._latency_history = self._latency_history[-200:]

        # 이전 예측값이 있으면 정확도 평가
        if metric_name in self._last_predictions:
            self._evaluate_prediction_accuracy(
                metric_name,
                predicted_value=self._last_predictions[metric_name],
                actual_value=value,
            )

        return level

    def ingest_metrics_batch(
        self,
        metrics: dict[str, float],
        has_adjustment: bool = False,
    ) -> dict[str, float]:
        """
        여러 메트릭을 한 번에 수집.

        Args:
            metrics: {메트릭명: 값} 딕셔너리.
            has_adjustment: 셀프힐링 개입 발생 여부.

        Returns:
            {메트릭명: smoothed_level} 딕셔너리.
        """
        results = {}
        for name, value in metrics.items():
            results[name] = self.ingest_metric(name, value, has_adjustment)
        return results

    # =========================================================================
    # 예측 + 이상 탐지
    # =========================================================================

    def forecast_and_detect(
        self,
        metric_name: str,
        steps_ahead: int | None = None,
        parameter: str | None = None,
        current_parameter_value: float | None = None,
    ) -> ForecastResult:
        """
        메트릭의 미래 값을 예측하고, 이상 여부를 탐지하며,
        필요 시 사전 조치를 생성한다.

        Args:
            metric_name: 메트릭 식별자.
            steps_ahead: 예측 스텝 수 (None이면 설정의 기본값 사용).
            parameter: 조정 대상 파라미터 (사전 조치 생성 시).
            current_parameter_value: 현재 파라미터 값 (사전 조치 생성 시).

        Returns:
            ForecastResult 예측 + 탐지 + 사전 조치 결합 결과.
        """
        if steps_ahead is None:
            steps_ahead = self._settings.prediction_steps

        forecaster = self._get_or_create_forecaster(metric_name)
        zscore_detector = self._get_or_create_zscore(metric_name)
        iqr_detector = self._get_or_create_iqr(metric_name)

        # 예측
        predicted = forecaster.predict(steps_ahead=steps_ahead)
        confidence = forecaster.get_confidence()
        trend_slope = forecaster.get_trend_slope()

        # 이전 예측값 저장 (다음 수집 시 정확도 평가용)
        if predicted is not None:
            self._last_predictions[metric_name] = predicted

        # 현재 값에 대한 이상 탐지
        current_value = None
        is_anomaly_z = False
        z_score = 0.0
        is_anomaly_iqr = False

        if forecaster.count > 0:
            history = forecaster.get_history()
            current_value = history[-1].value
            is_anomaly_z, z_score = zscore_detector.is_anomaly(current_value)
            is_anomaly_iqr, _ = iqr_detector.is_anomaly(current_value)

        # SpikeClassifier 분류
        spike_type = None
        if is_anomaly_z or is_anomaly_iqr:
            spike_type = self._spike_classifier.classify(
                rps_history=self._rps_history,
                error_rate_history=self._error_rate_history,
                latency_history=self._latency_history,
            )
            # LearningService에 이상 패턴 보고
            if predicted is not None:
                self._report_anomaly_to_learning(metric_name, spike_type, predicted, confidence)

        # ProactiveActionTrigger 사전 조치 평가
        proactive_action = None
        if spike_type is not None and predicted is not None and parameter is not None and current_parameter_value is not None:
            proactive_action = self._action_trigger.evaluate(
                spike_type=spike_type,
                confidence=confidence,
                predicted_value=predicted,
                current_value=current_parameter_value,
                metric_name=metric_name,
                parameter=parameter,
            )
            if proactive_action is not None:
                self._action_history.append(proactive_action)

        return ForecastResult(
            metric_name=metric_name,
            current_value=current_value,
            predicted_value=predicted,
            confidence=confidence,
            trend_slope=trend_slope,
            is_anomaly_zscore=is_anomaly_z,
            is_anomaly_iqr=is_anomaly_iqr,
            z_score=z_score,
            spike_type=spike_type,
            proactive_action=proactive_action,
            is_warmed_up=forecaster.is_warmed_up,
            data_point_count=forecaster.count,
        )

    # =========================================================================
    # LearningService 연동
    # =========================================================================

    def _report_anomaly_to_learning(
        self,
        metric_name: str,
        spike_type: SpikeType,
        predicted_value: float,
        confidence: float,
    ) -> None:
        """
        감지된 이상을 LearningService에 패턴으로 등록.

        LearningService._check_and_generate_suggestions()가 3회 이상 발생 시
        자동으로 사전 조치 Suggestion을 생성한다.
        """
        try:
            from selfhealing.services.learning.models import PatternType
            from selfhealing.services.learning.service import LearningService

            learning = LearningService()
            learning.learn_pattern(
                pattern_type=PatternType.ANOMALY,
                name=f"PredictedAnomaly:{metric_name}:{spike_type.value}",
                description=(f"Forecaster predicted {spike_type.value} for {metric_name}"),
                features={
                    "metric_name": metric_name,
                    "spike_type": spike_type.value,
                    "predicted_value": predicted_value,
                },
                confidence=confidence,
                metadata={"source": "predictive_forecaster"},
            )
        except Exception as e:
            logger.debug(
                "predictive_forecaster_service.learningservice_report_skipped",
                error=e,
            )

    def _evaluate_prediction_accuracy(
        self,
        metric_name: str,
        predicted_value: float,
        actual_value: float,
    ) -> None:
        """
        예측 정확도를 LearningService에 메트릭으로 기록.

        LearningService._detect_anomaly()가 정확도 급락 시 자동 패턴 학습.
        """
        accuracy = 1.0 - abs(predicted_value - actual_value) / max(abs(actual_value), 1e-10)
        accuracy = max(0.0, min(1.0, accuracy))

        try:
            from selfhealing.services.learning.service import LearningService

            learning = LearningService()
            learning.record_metric(
                metric_name=f"forecaster_accuracy:{metric_name}",
                value=accuracy,
                stage_name="predictive_forecaster",
                tags={
                    "metric": metric_name,
                    "predicted": str(predicted_value),
                },
            )
        except Exception as e:
            logger.debug(
                "predictive_forecaster_service.accuracy_recording_skipped",
                error=e,
            )

        # 연속 오판 추적 (정확도 0.5 미만 = 오판)
        if accuracy < 0.5:
            self._misprediction_counts[metric_name] += 1
            self._handle_repeated_misprediction(
                metric_name=metric_name,
                parameter=metric_name,
                misprediction_count=self._misprediction_counts[metric_name],
            )
        else:
            self._misprediction_counts[metric_name] = 0

    def _handle_repeated_misprediction(
        self,
        metric_name: str,
        parameter: str,
        misprediction_count: int,
    ) -> None:
        """
        동일 메트릭/파라미터에서 연속 오판 시
        LearningService 블랙리스트에 자동 등록.
        """
        if misprediction_count < self.MISPREDICTION_BLACKLIST_THRESHOLD:
            return

        try:
            from selfhealing.services.learning.models import BlacklistReason
            from selfhealing.services.learning.service import LearningService

            learning = LearningService()
            learning.register_dangerous_parameter(
                module="predictive_forecaster",
                parameter=parameter,
                blocked_values={metric_name},
                reason=BlacklistReason.FLAPPING,
                ttl_hours=168,
            )
            logger.warning(
                "predictive_forecaster_service.blacklisted_consecutive_mispredictions",
                parameter=parameter,
                metric_name=metric_name,
                misprediction_count=misprediction_count,
            )
        except Exception as e:
            logger.debug(
                "predictive_forecaster_service.blacklist_registration_skipped",
                error=e,
            )

    # =========================================================================
    # StateBackend 영속성
    # =========================================================================

    def save_all_states(self) -> dict[str, bool]:
        """
        모든 Forecaster 상태를 StateBackend에 저장.

        state_save_interval(기본 5분)마다 호출하거나,
        graceful shutdown 시 호출한다.

        Returns:
            {메트릭명: 성공여부} 딕셔너리.
        """
        results = {}
        for metric_name, forecaster in self._forecasters.items():
            results[metric_name] = forecaster.save_state(metric_name)
        return results

    def load_all_states(self, metric_names: list[str]) -> dict[str, bool]:
        """
        지정된 메트릭들의 Forecaster 상태를 StateBackend에서 복원.

        프로세스 시작 시 호출하여 Cold Start를 방지한다.

        Args:
            metric_names: 복원할 메트릭 식별자 목록.

        Returns:
            {메트릭명: 성공여부} 딕셔너리.
        """
        results = {}
        for metric_name in metric_names:
            forecaster = self._get_or_create_forecaster(metric_name)
            results[metric_name] = forecaster.load_state(metric_name)
        return results

    # =========================================================================
    # 조회 및 유틸리티
    # =========================================================================

    def get_action_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """사전 조치 이력 반환."""
        return [a.to_dict() for a in self._action_history[-limit:]]

    def get_forecaster_status(self, metric_name: str) -> dict[str, Any] | None:
        """특정 메트릭의 Forecaster 상태 반환."""
        forecaster = self._forecasters.get(metric_name)
        if forecaster is None:
            return None

        return {
            "metric_name": metric_name,
            "is_warmed_up": forecaster.is_warmed_up,
            "data_point_count": forecaster.count,
            "confidence": forecaster.get_confidence(),
            "trend_slope": forecaster.get_trend_slope(),
            "misprediction_count": self._misprediction_counts.get(metric_name, 0),
        }

    def get_all_metric_names(self) -> list[str]:
        """현재 추적 중인 모든 메트릭 목록 반환."""
        return list(self._forecasters.keys())

    def reset(self) -> None:
        """모든 상태 초기화 (테스트 용도)."""
        self._forecasters.clear()
        self._smoothers.clear()
        self._zscore_detectors.clear()
        self._iqr_detectors.clear()
        self._rps_history.clear()
        self._error_rate_history.clear()
        self._latency_history.clear()
        self._misprediction_counts.clear()
        self._last_predictions.clear()
        self._action_history.clear()
