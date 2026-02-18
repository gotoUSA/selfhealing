"""
Predictive Anomaly Forecaster — 예측적 이상 탐지 엔진.

시계열 예측 모델(Holt Linear / Holt-Winters)로 5~15분 후 메트릭을 예측하여
사전 조치를 수행하는 예측 시스템.

기존 DecisionEngine의 반응형(reactive) 구조를 보완하여
임계치 도달 전에 트렌드 기반으로 사전 조치 시간을 확보한다.

모듈 구성:
    time_series.py       — HoltLinearForecaster, EWMAForecaster, HoltWintersForecaster
    anomaly_detector.py  — ZScoreDetector, IQRDetector
    proactive_action.py  — ProactiveActionTrigger, SpikeClassifier, SpikeType
    service.py           — PredictiveForecasterService (통합 서비스)

Usage:
    from selfhealing.services.predictive_forecaster import (
        PredictiveForecasterService,
        HoltLinearForecaster,
        EWMAForecaster,
        ZScoreDetector,
        IQRDetector,
        SpikeClassifier,
        SpikeType,
        ForecastResult,
    )

    service = PredictiveForecasterService()
    service.ingest_metric("p99_latency_ms", 150.0)
    result = service.forecast_and_detect("p99_latency_ms")

설정: selfhealing.settings.predictive_forecaster.PredictiveForecasterSettings
환경변수 접두사: SELFHEALING_FORECASTER_
"""

from selfhealing.services.predictive_forecaster.anomaly_detector import (
    IQRDetector,
    IQRResult,
    ZScoreDetector,
    ZScoreResult,
)
from selfhealing.services.predictive_forecaster.proactive_action import (
    ProactiveAction,
    ProactiveActionTrigger,
    SpikeClassifier,
    SpikeType,
)
from selfhealing.services.predictive_forecaster.service import (
    ForecastResult,
    PredictiveForecasterService,
)
from selfhealing.services.predictive_forecaster.time_series import (
    EWMAForecaster,
    ForecastDataPoint,
    HoltLinearForecaster,
    HoltWintersForecaster,
)

__all__ = [
    # time_series
    "HoltLinearForecaster",
    "EWMAForecaster",
    "HoltWintersForecaster",
    "ForecastDataPoint",
    # anomaly_detector
    "ZScoreDetector",
    "ZScoreResult",
    "IQRDetector",
    "IQRResult",
    # proactive_action
    "SpikeClassifier",
    "SpikeType",
    "ProactiveActionTrigger",
    "ProactiveAction",
    # service
    "PredictiveForecasterService",
    "ForecastResult",
]
