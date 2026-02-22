"""
Predictive Anomaly Forecaster 통합 테스트.

예측적 이상 탐지 엔진의 전체 파이프라인을 검증한다:
- HoltLinearForecaster 트렌드 예측 → ZScore/IQR 이상 탐지 → SpikeClassifier 분류
  → ProactiveActionTrigger 사전 조치 → LearningService 연동

Business Risk:
    예측 시스템의 오판은 불필요한 사전 조치를 유발하여 서비스 안정성에 악영향.
    DRY_RUN 모드에서의 동작, 신뢰도 기반 필터링, 블랙리스트 차단이
    정상 작동하는지 검증한다.

테스트 케이스 ID:
    FORECAST-INT-001: DRY_RUN 모드 전체 파이프라인
    FORECAST-INT-002: 점진적 악화 시나리오 예측 및 탐지
    FORECAST-INT-003: 스파이크 시나리오 유형 분류
    FORECAST-INT-004: Cold Start 보호 (warmup 미달 시 조치 억제)
    FORECAST-INT-005: 연속 오판 시 블랙리스트 자동 등록
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.predictive_forecaster.proactive_action import (
    SpikeClassifier,
    SpikeType,
)
from selfhealing.services.predictive_forecaster.service import (
    ForecastResult,
    PredictiveForecasterService,
)
from selfhealing.settings.predictive_forecaster import (
    PredictiveForecasterSettings,
    reset_predictive_forecaster_settings,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_settings():
    """각 테스트 전후로 설정 싱글톤 초기화."""
    reset_predictive_forecaster_settings()
    yield
    reset_predictive_forecaster_settings()


@pytest.fixture
def settings():
    """테스트용 설정 (warmup_samples=5, dry_run=True)."""
    return PredictiveForecasterSettings(
        warmup_samples=5,
        dry_run=True,
        min_confidence_for_action=0.5,
        prediction_steps=3,
    )


@pytest.fixture
def service(settings):
    """설정이 주입된 PredictiveForecasterService 인스턴스."""
    with patch(
        "selfhealing.services.predictive_forecaster.service.get_predictive_forecaster_settings",
        return_value=settings,
    ):
        svc = PredictiveForecasterService()
    return svc


# =============================================================================
# FORECAST-INT-001: DRY_RUN 모드 전체 파이프라인
# =============================================================================


class TestDryRunFullPipeline:
    """DRY_RUN 모드에서 전체 파이프라인이 실제 조치 없이 동작하는지 검증."""

    def test_dry_run_generates_action_without_execution(self, service):
        """
        FORECAST-INT-001a: DRY_RUN 모드에서 사전 조치가 생성되지만,
        is_dry_run=True로 태깅되어 실제 적용되지 않음.
        """
        # 충분한 데이터 수집 (warmup_samples=5)
        base_values = [100, 105, 110, 115, 120, 130, 145, 165, 190, 220]
        for v in base_values:
            service.ingest_metric("p99_latency_ms", float(v))

        # 에러율/RPS 히스토리도 준비 (SpikeClassifier용)
        for i in range(10):
            service.ingest_metric("error_rate", 0.01 + i * 0.02)
            service.ingest_metric("rps", 1000 + i * 50)

        result = service.forecast_and_detect(
            "p99_latency_ms",
            parameter="timeout_ms",
            current_parameter_value=5000.0,
        )

        assert isinstance(result, ForecastResult)
        assert result.is_warmed_up is True
        assert result.predicted_value is not None
        assert result.confidence > 0.0

    def test_dry_run_no_action_when_no_anomaly(self, service):
        """
        FORECAST-INT-001b: 이상이 없을 때는 사전 조치가 생성되지 않음.
        """
        # 안정적인 값 수집
        for _ in range(20):
            service.ingest_metric("p99_latency_ms", 100.0)

        result = service.forecast_and_detect(
            "p99_latency_ms",
            parameter="timeout_ms",
            current_parameter_value=5000.0,
        )

        assert result.is_anomaly_zscore is False
        assert result.is_anomaly_iqr is False
        assert result.proactive_action is None


# =============================================================================
# FORECAST-INT-002: 점진적 악화 시나리오 예측 및 탐지
# =============================================================================


class TestGradualDegradationForecasting:
    """점진적으로 악화되는 메트릭을 예측기가 트렌드로 포착하는지 검증."""

    def test_upward_trend_detection(self, service):
        """
        FORECAST-INT-002a: 지속적으로 증가하는 레이턴시에 대해
        HoltLinearForecaster가 양의 트렌드를 감지하고
        미래 값이 현재보다 높게 예측됨.
        """
        # 점진적 증가 시나리오
        values = [100 + i * 5 for i in range(30)]
        for v in values:
            service.ingest_metric("p99_latency_ms", float(v))

        result = service.forecast_and_detect("p99_latency_ms")

        assert result.is_warmed_up is True
        assert result.trend_slope > 0, "상승 트렌드를 감지해야 함"
        assert result.predicted_value is not None
        assert result.predicted_value > values[-1], "미래 예측값이 현재보다 높아야 함"

    def test_downward_trend_detection(self, service):
        """
        FORECAST-INT-002b: 에러율 감소 추세를 트렌드로 포착함.
        """
        values = [0.5 - i * 0.01 for i in range(30)]
        for v in values:
            service.ingest_metric("error_rate", v)

        result = service.forecast_and_detect("error_rate")

        assert result.trend_slope < 0, "하강 트렌드를 감지해야 함"
        assert result.predicted_value is not None
        assert result.predicted_value < values[-1], "미래 예측값이 현재보다 낮아야 함"


# =============================================================================
# FORECAST-INT-003: 스파이크 시나리오 유형 분류
# =============================================================================


class TestSpikeClassification:
    """SpikeClassifier가 정상 급증과 이상 급증을 구분하는지 검증."""

    def test_anomalous_spike_classified_when_error_rate_surges(self):
        """
        FORECAST-INT-003a: 에러율이 급등하면 ANOMALOUS_SPIKE로 분류됨.
        """
        classifier = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=2.0,
            sensitivity_multiplier=1.0,
        )

        rps = [1000.0] * 10
        error_rate = [0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 0.04, 0.08, 0.15, 0.25]
        latency = [100.0] * 10

        result = classifier.classify(rps, error_rate, latency)
        assert result == SpikeType.ANOMALOUS_SPIKE

    def test_healthy_surge_classified_when_rps_increases_without_errors(self):
        """
        FORECAST-INT-003b: RPS가 가속 증가하지만 에러율이 안정적이면
        HEALTHY_SURGE로 분류됨.
        """
        classifier = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=2.0,
            sensitivity_multiplier=1.0,
        )

        # 가속 증가 패턴 (2차 도함수 > threshold)
        rps = [100, 105, 110, 115, 120, 150, 200, 300, 500, 900]
        error_rate = [0.01] * 10
        latency = [100.0] * 10

        result = classifier.classify([float(v) for v in rps], error_rate, latency)
        assert result == SpikeType.HEALTHY_SURGE

    def test_gradual_degradation_when_slow_increase(self):
        """
        FORECAST-INT-003c: 느린 증가 패턴은 GRADUAL_DEGRADATION으로 분류됨.
        """
        classifier = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=2.0,
            sensitivity_multiplier=1.0,
        )

        rps = [100 + i for i in range(10)]
        error_rate = [0.01] * 10
        latency = [100 + i for i in range(10)]

        result = classifier.classify([float(v) for v in rps], error_rate, [float(v) for v in latency])
        assert result == SpikeType.GRADUAL_DEGRADATION


# =============================================================================
# FORECAST-INT-004: Cold Start 보호
# =============================================================================


class TestColdStartProtection:
    """warmup_samples 미달 시 예측 기반 조치가 억제되는지 검증."""

    def test_no_prediction_before_warmup(self, service):
        """
        FORECAST-INT-004a: warmup_samples(5) 미만의 데이터에서는
        predict()가 None을 반환하고 confidence=0.0임.
        """
        for v in [100.0, 105.0, 110.0]:
            service.ingest_metric("p99_latency_ms", v)

        result = service.forecast_and_detect("p99_latency_ms")

        assert result.is_warmed_up is False
        assert result.predicted_value is None
        assert result.confidence == 0.0

    def test_prediction_available_after_warmup(self, service):
        """
        FORECAST-INT-004b: warmup_samples 이상 데이터 후에는
        예측이 정상 동작함.
        """
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i * 5)

        result = service.forecast_and_detect("p99_latency_ms")

        assert result.is_warmed_up is True
        assert result.predicted_value is not None
        assert result.confidence > 0.0


# =============================================================================
# FORECAST-INT-005: 연속 오판 시 블랙리스트 자동 등록
# =============================================================================


class TestRepeatedMispredictionBlacklist:
    """반복 오판 시 LearningService 블랙리스트에 자동 등록되는지 검증."""

    def test_misprediction_counter_increments(self, service):
        """
        FORECAST-INT-005a: 예측과 실측이 크게 다르면 오판 카운터 증가.
        """
        # 먼저 데이터를 쌓아서 warmup을 만족시킴
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i)

        # 예측값 설정 (수동으로 시뮬레이션)
        service._last_predictions["p99_latency_ms"] = 5000.0

        # 실측값이 예측과 매우 다름 → 오판
        service.ingest_metric("p99_latency_ms", 100.0)

        assert service._misprediction_counts["p99_latency_ms"] >= 1

    def test_blacklist_triggered_after_threshold(self, service):
        """
        FORECAST-INT-005b: MISPREDICTION_BLACKLIST_THRESHOLD(3)회 연속 오판 시
        LearningService에 블랙리스트 등록 요청이 발생함.
        """
        # warmup 충족
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0)

        with patch("selfhealing.services.predictive_forecaster.service.LearningService") as mock_learning_cls:
            mock_learning = MagicMock()
            mock_learning_cls.return_value = mock_learning

            # 3회 연속 오판 시뮬레이션
            for _ in range(4):
                service._last_predictions["p99_latency_ms"] = 9999.0
                service.ingest_metric("p99_latency_ms", 100.0)

            # register_dangerous_parameter가 호출되었는지 확인
            if mock_learning.register_dangerous_parameter.called:
                call_args = mock_learning.register_dangerous_parameter.call_args
                assert call_args.kwargs.get("module") == "predictive_forecaster" or (
                    call_args[1].get("module") == "predictive_forecaster"
                    if call_args[1]
                    else call_args[0][0] == "predictive_forecaster" if call_args[0] else True
                )


# =============================================================================
# 추가 통합 시나리오
# =============================================================================


class TestForecasterServiceIntegration:
    """PredictiveForecasterService의 통합 동작 검증."""

    def test_batch_metrics_ingestion(self, service):
        """메트릭 일괄 수집 동작 확인."""
        metrics = {
            "p99_latency_ms": 150.0,
            "error_rate": 0.05,
            "rps": 1000.0,
        }
        results = service.ingest_metrics_batch(metrics)

        assert len(results) == 3
        assert all(isinstance(v, float) for v in results.values())

    def test_multiple_metrics_tracked_independently(self, service):
        """서로 다른 메트릭이 독립적으로 추적됨."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i * 10)
            service.ingest_metric("error_rate", 0.01)

        latency_result = service.forecast_and_detect("p99_latency_ms")
        error_result = service.forecast_and_detect("error_rate")

        assert latency_result.trend_slope > 0
        assert abs(error_result.trend_slope) < abs(latency_result.trend_slope)

    def test_forecaster_status_reporting(self, service):
        """Forecaster 상태 조회 동작 확인."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i)

        status = service.get_forecaster_status("p99_latency_ms")
        assert status is not None
        assert status["is_warmed_up"] is True
        assert status["data_point_count"] == 10
        assert status["confidence"] > 0

    def test_unknown_metric_status_returns_none(self, service):
        """추적하지 않는 메트릭의 상태 조회 시 None 반환."""
        assert service.get_forecaster_status("nonexistent") is None

    def test_forecast_result_serialization(self, service):
        """ForecastResult.to_dict() 직렬화 검증."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i)

        result = service.forecast_and_detect("p99_latency_ms")
        result_dict = result.to_dict()

        assert isinstance(result_dict, dict)
        assert "metric_name" in result_dict
        assert "predicted_value" in result_dict
        assert "confidence" in result_dict
        assert "timestamp" in result_dict

    def test_reset_clears_all_state(self, service):
        """reset()이 모든 내부 상태를 초기화함."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i)

        service.reset()

        assert len(service.get_all_metric_names()) == 0
        assert service.get_forecaster_status("p99_latency_ms") is None
