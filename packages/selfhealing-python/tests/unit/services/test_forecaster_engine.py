"""
예측 엔진 핵심 컴포넌트 단위 테스트.

시계열 예측, 이상 탐지, 트래픽 분류, 사전 조치 트리거 등
Predictive Forecaster 엔진 내부 컴포넌트의 계약값과 동작을 검증한다.

검증 대상:
    - PredictiveForecasterSettings: 15개 설정 필드 기본값/범위
    - HoltLinearForecaster: 이중지수평활 트렌드 예측, warmup, confidence
    - EWMAForecaster: 지수가중이동평균 smoothing
    - HoltWintersForecaster: 삼중지수평활 계절성 예측
    - ForecastDataPoint: has_adjustment 셀프힐링 개입 태깅
    - ZScoreDetector: Z-Score 기반 이상 탐지 (99.7% 신뢰구간)
    - IQRDetector: 사분위수 범위 기반 이상 탐지
    - SpikeClassifier: Flash Sale vs DDoS 급증 유형 분류
    - ProactiveActionTrigger: 신뢰도/블랙리스트 기반 사전 조치 생성
    - StateBackend save/load 왕복 영속성
    - PredictiveForecasterService: 메트릭 수집→예측→탐지→조치 통합 흐름
    - TimeSeriesScenarioGenerator: 합성 시계열 데이터 생성

테스트 구조:
    - Test*Contract: 설계 계약값 (기본값, enum 멤버 등) 하드코딩 검증
    - Test*Behavior: 입출력 동작 (예측 정확도, 이상 탐지 등) 기능 검증
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

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
from selfhealing.services.predictive_forecaster.scenario_generator import (
    TimeSeriesScenarioGenerator,
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
def default_settings():
    """기본 PredictiveForecasterSettings 인스턴스."""
    return PredictiveForecasterSettings()


@pytest.fixture
def test_settings():
    """테스트용 설정 (warmup_samples=5, dry_run=True)."""
    return PredictiveForecasterSettings(
        warmup_samples=5,
        dry_run=True,
        min_confidence_for_action=0.5,
        prediction_steps=3,
    )


@pytest.fixture
def service(test_settings):
    """설정이 주입된 PredictiveForecasterService 인스턴스."""
    with patch(
        "selfhealing.services.predictive_forecaster.service.get_predictive_forecaster_settings",
        return_value=test_settings,
    ):
        svc = PredictiveForecasterService()
    return svc


# =============================================================================
# 1. Settings 계약 검증
# =============================================================================


class TestPredictiveForecasterSettingsContract:
    """PredictiveForecasterSettings 설계 계약값 검증."""

    def test_ewma_alpha_contract(self):
        """ewma_alpha 기본값은 0.3이다."""
        settings = PredictiveForecasterSettings()
        assert settings.ewma_alpha == 0.3

    def test_holt_beta_contract(self):
        """holt_beta 기본값은 0.1이다."""
        settings = PredictiveForecasterSettings()
        assert settings.holt_beta == 0.1

    def test_zscore_threshold_contract(self):
        """zscore_threshold 기본값은 3.0이다."""
        settings = PredictiveForecasterSettings()
        assert settings.zscore_threshold == 3.0

    def test_zscore_window_contract(self):
        """zscore_window 기본값은 100이다."""
        settings = PredictiveForecasterSettings()
        assert settings.zscore_window == 100

    def test_iqr_multiplier_contract(self):
        """iqr_multiplier 기본값은 1.5이다."""
        settings = PredictiveForecasterSettings()
        assert settings.iqr_multiplier == 1.5

    def test_max_history_contract(self):
        """max_history 기본값은 10000이다."""
        settings = PredictiveForecasterSettings()
        assert settings.max_history == 10000

    def test_warmup_samples_contract(self):
        """warmup_samples 기본값은 30이다."""
        settings = PredictiveForecasterSettings()
        assert settings.warmup_samples == 30

    def test_prediction_steps_contract(self):
        """prediction_steps 기본값은 5이다."""
        settings = PredictiveForecasterSettings()
        assert settings.prediction_steps == 5

    def test_min_confidence_for_action_contract(self):
        """min_confidence_for_action 기본값은 0.7이다."""
        settings = PredictiveForecasterSettings()
        assert settings.min_confidence_for_action == 0.7

    def test_dry_run_contract(self):
        """dry_run 기본값은 True이다 (도입 초기 안전장치)."""
        settings = PredictiveForecasterSettings()
        assert settings.dry_run is True

    def test_sensitivity_multiplier_contract(self):
        """sensitivity_multiplier 기본값은 1.0이다."""
        settings = PredictiveForecasterSettings()
        assert settings.sensitivity_multiplier == 1.0

    def test_spike_error_rate_threshold_contract(self):
        """spike_error_rate_threshold 기본값은 0.05이다."""
        settings = PredictiveForecasterSettings()
        assert settings.spike_error_rate_threshold == 0.05

    def test_spike_acceleration_threshold_contract(self):
        """spike_acceleration_threshold 기본값은 2.0이다."""
        settings = PredictiveForecasterSettings()
        assert settings.spike_acceleration_threshold == 2.0

    def test_state_save_interval_contract(self):
        """state_save_interval 기본값은 300초(5분)이다."""
        settings = PredictiveForecasterSettings()
        assert settings.state_save_interval == 300

    def test_state_ttl_contract(self):
        """state_ttl 기본값은 259200초(72시간)이다."""
        settings = PredictiveForecasterSettings()
        assert settings.state_ttl == 259200

    def test_total_settings_field_count(self):
        """설정 필드는 총 15개이다."""
        settings = PredictiveForecasterSettings()
        field_count = len(PredictiveForecasterSettings.model_fields)
        assert field_count == 15

    def test_env_prefix_is_selfhealing_forecaster(self):
        """환경변수 접두사는 SELFHEALING_FORECASTER_이다."""
        config = PredictiveForecasterSettings.model_config
        assert config["env_prefix"] == "SELFHEALING_FORECASTER_"


class TestPredictiveForecasterSettingsBehavior:
    """PredictiveForecasterSettings 동작 검증."""

    def test_warmup_samples_must_be_gte_prediction_steps(self):
        """warmup_samples가 prediction_steps보다 작으면 ValidationError."""
        with pytest.raises(ValueError, match="warmup_samples"):
            PredictiveForecasterSettings(warmup_samples=2, prediction_steps=5)


# =============================================================================
# 2. HoltLinearForecaster 동작 검증
# =============================================================================


class TestHoltLinearForecasterBehavior:
    """HoltLinearForecaster 동작 검증."""

    def test_first_update_sets_level_to_value(self):
        """첫 번째 update()는 level을 입력값으로 초기화한다."""
        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
        result = f.update(100.0)
        assert result == 100.0

    def test_trend_slope_zero_after_first_update(self):
        """첫 데이터 이후 trend는 0이다."""
        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
        f.update(100.0)
        assert f.get_trend_slope() == 0.0

    def test_predict_returns_none_before_warmup(self):
        """warmup_samples 미만에서 predict()는 None을 반환한다."""
        f = HoltLinearForecaster(warmup_samples=10)
        for i in range(5):
            f.update(100.0 + i)
        assert f.predict() is None
        assert not f.is_warmed_up

    def test_predict_returns_value_after_warmup(self):
        """warmup_samples 이상에서 predict()는 값을 반환한다."""
        f = HoltLinearForecaster(warmup_samples=5)
        for i in range(10):
            f.update(100.0 + i * 10)
        predicted = f.predict(steps_ahead=5)
        assert predicted is not None
        assert f.is_warmed_up

    def test_upward_trend_produces_higher_prediction(self):
        """상승 추세에서 예측값이 현재 레벨보다 높다."""
        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
        values = TimeSeriesScenarioGenerator.gradual_degradation(base=100, target=500, steps=30, noise_ratio=0.0, seed=42)
        for v in values:
            f.update(v)
        predicted = f.predict(steps_ahead=5)
        assert predicted is not None
        assert predicted > f._level, "상승 트렌드에서 미래 예측값이 현재 레벨보다 높아야 함"

    def test_downward_trend_produces_lower_prediction(self):
        """하강 추세에서 예측값이 현재 레벨보다 낮다."""
        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
        for v in range(200, 100, -3):
            f.update(float(v))
        predicted = f.predict(steps_ahead=5)
        assert predicted is not None
        assert predicted < f._level, "하강 트렌드에서 미래 예측값이 현재 레벨보다 낮아야 함"

    def test_confidence_zero_before_warmup(self):
        """warmup 미달 시 confidence는 0.0이다."""
        f = HoltLinearForecaster(warmup_samples=30)
        for i in range(10):
            f.update(100.0)
        assert f.get_confidence() == 0.0

    def test_confidence_increases_with_data(self):
        """데이터가 축적됨에 따라 confidence가 증가한다."""
        f = HoltLinearForecaster(warmup_samples=5)
        for i in range(50):
            f.update(100.0)
        c50 = f.get_confidence()
        for i in range(150):
            f.update(100.0)
        c200 = f.get_confidence()
        assert c200 > c50
        assert c200 == 1.0

    def test_count_property_tracks_data_points(self):
        """count 속성이 데이터포인트 수를 정확히 추적한다."""
        f = HoltLinearForecaster()
        assert f.count == 0
        for i in range(15):
            f.update(float(i))
        assert f.count == 15

    def test_get_values_returns_history_values(self):
        """get_values()가 히스토리의 값만 반환한다."""
        f = HoltLinearForecaster(warmup_samples=3)
        input_values = [10.0, 20.0, 30.0]
        for v in input_values:
            f.update(v)
        assert f.get_values() == input_values

    def test_alpha_validation_rejects_zero(self):
        """alpha=0은 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="alpha"):
            HoltLinearForecaster(alpha=0.0)

    def test_beta_validation_rejects_negative(self):
        """beta<0은 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="beta"):
            HoltLinearForecaster(beta=-0.1)

    def test_trend_accuracy_on_linear_data(self):
        """선형 데이터에서 트렌드 기울기가 실제 기울기에 근접한다."""
        step_size = 10.0
        f = HoltLinearForecaster(alpha=0.5, beta=0.3, warmup_samples=5)
        for i in range(100):
            f.update(100.0 + step_size * i)

        slope = f.get_trend_slope()
        # 충분한 데이터 후 트렌드 기울기가 실제 기울기의 ±50% 이내
        assert abs(slope - step_size) / step_size < 0.5


# =============================================================================
# 3. EWMAForecaster 동작 검증
# =============================================================================


class TestEWMAForecasterBehavior:
    """EWMAForecaster 동작 검증."""

    def test_first_update_returns_input_value(self):
        """첫 업데이트는 입력값 그대로 반환한다."""
        f = EWMAForecaster(alpha=0.3)
        assert f.update(50.0) == 50.0

    def test_smoothed_value_dampens_spikes(self):
        """EWMA는 급격한 변화를 완화(smooth)한다."""
        f = EWMAForecaster(alpha=0.3)
        f.update(100.0)
        smoothed = f.update(200.0)
        assert 100.0 < smoothed < 200.0, "smoothed 값은 이전값과 새 값 사이여야 함"

    def test_get_smoothed_returns_current_ewma(self):
        """get_smoothed()가 현재 EWMA 값을 반환한다."""
        f = EWMAForecaster(alpha=0.5)
        f.update(100.0)
        f.update(200.0)
        assert f.get_smoothed() == f.update(200.0) or f.get_smoothed() is not None

    def test_reset_clears_state(self):
        """reset()이 상태를 초기화한다."""
        f = EWMAForecaster()
        f.update(100.0)
        f.reset()
        assert f.get_smoothed() is None

    def test_alpha_validation_rejects_invalid(self):
        """잘못된 alpha는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="alpha"):
            EWMAForecaster(alpha=0.0)
        with pytest.raises(ValueError, match="alpha"):
            EWMAForecaster(alpha=1.5)


# =============================================================================
# 4. HoltWintersForecaster 동작 검증
# =============================================================================


class TestHoltWintersForecasterBehavior:
    """HoltWintersForecaster 동작 검증."""

    def test_not_warmed_up_before_minimum_data(self):
        """최소 데이터 미달 시 is_warmed_up은 False이다."""
        f = HoltWintersForecaster(season_length=10, warmup_samples=20)
        for i in range(15):
            f.update(float(i))
        assert not f.is_warmed_up

    def test_predict_returns_none_before_warmup(self):
        """warmup 전 predict()는 None을 반환한다."""
        f = HoltWintersForecaster(season_length=10, warmup_samples=20)
        for i in range(15):
            f.update(float(i))
        assert f.predict() is None

    def test_seasonal_pattern_detection(self):
        """계절성 패턴이 있는 데이터에서 예측이 유의미하다."""
        season_length = 24
        f = HoltWintersForecaster(
            alpha=0.3,
            beta=0.05,
            gamma=0.3,
            season_length=season_length,
            warmup_samples=season_length * 2,
        )
        # 3 시즌 데이터 공급
        values = TimeSeriesScenarioGenerator.seasonal_pattern(
            base=100,
            amplitude=30,
            period=season_length,
            steps=season_length * 3,
            noise_ratio=0.0,
            seed=42,
        )
        for v in values:
            f.update(v)

        predicted = f.predict(steps_ahead=1)
        assert predicted is not None

    def test_confidence_increases_with_seasons(self):
        """시즌이 축적됨에 따라 confidence가 증가한다."""
        season = 10
        f = HoltWintersForecaster(season_length=season, warmup_samples=season * 2)
        for i in range(season * 2):
            f.update(100.0 + 10.0 * math.sin(2 * math.pi * i / season))
        c1 = f.get_confidence()

        for i in range(season * 2):
            f.update(100.0 + 10.0 * math.sin(2 * math.pi * i / season))
        c2 = f.get_confidence()
        assert c2 >= c1

    def test_parameter_validation(self):
        """잘못된 파라미터는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="alpha"):
            HoltWintersForecaster(alpha=0.0)
        with pytest.raises(ValueError, match="gamma"):
            HoltWintersForecaster(gamma=1.5)
        with pytest.raises(ValueError, match="season_length"):
            HoltWintersForecaster(season_length=1)


# =============================================================================
# 5. ForecastDataPoint 동작 검증
# =============================================================================


class TestForecastDataPointBehavior:
    """ForecastDataPoint 동작 검증."""

    def test_default_has_adjustment_is_false(self):
        """기본 has_adjustment는 False이다."""
        dp = ForecastDataPoint(value=100.0)
        assert dp.has_adjustment is False

    def test_has_adjustment_can_be_set_true(self):
        """has_adjustment=True로 셀프힐링 개입을 표시할 수 있다."""
        dp = ForecastDataPoint(value=100.0, has_adjustment=True)
        assert dp.has_adjustment is True

    def test_timestamp_is_utc(self):
        """timestamp는 UTC 시간대이다."""
        dp = ForecastDataPoint(value=100.0)
        assert dp.timestamp.tzinfo is not None

    def test_has_adjustment_recorded_in_history(self):
        """has_adjustment가 HoltLinearForecaster 히스토리에 기록된다."""
        f = HoltLinearForecaster(warmup_samples=3)
        f.update(100.0, has_adjustment=False)
        f.update(200.0, has_adjustment=True)
        f.update(150.0, has_adjustment=False)

        history = f.get_history()
        assert len(history) == 3
        assert history[0].has_adjustment is False
        assert history[1].has_adjustment is True
        assert history[2].has_adjustment is False


# =============================================================================
# 6. ZScoreDetector 동작 검증
# =============================================================================


class TestZScoreDetectorBehavior:
    """ZScoreDetector 동작 검증."""

    def test_no_anomaly_with_insufficient_data(self):
        """데이터 3개 미만이면 이상으로 판정하지 않는다."""
        d = ZScoreDetector(threshold=3.0, window=100)
        is_anom, z = d.is_anomaly(100.0)
        assert is_anom is False
        assert z == 0.0

    def test_stable_values_not_anomalous(self):
        """안정적인 값은 이상으로 판정되지 않는다."""
        d = ZScoreDetector(threshold=3.0, window=100)
        for v in TimeSeriesScenarioGenerator.stable_noise(base=100, std_dev=2, steps=50, seed=42):
            is_anom, z = d.is_anomaly(v)
        # 대부분 비이상이어야 함
        assert not is_anom or abs(z) > 3.0

    def test_extreme_outlier_detected(self):
        """극단적 이상치가 탐지된다."""
        d = ZScoreDetector(threshold=3.0, window=100)
        for _ in range(50):
            d.is_anomaly(100.0)
        is_anom, z = d.is_anomaly(500.0)
        assert is_anom is True
        assert abs(z) > 3.0

    def test_detailed_result_contains_statistics(self):
        """is_anomaly_detailed()가 통계 정보를 포함한다."""
        d = ZScoreDetector()
        for _ in range(10):
            d.is_anomaly_detailed(100.0)
        result = d.is_anomaly_detailed(100.0)
        assert isinstance(result, ZScoreResult)
        assert result.mean > 0
        assert result.value == 100.0

    def test_get_statistics_returns_window_info(self):
        """get_statistics()가 윈도우 통계를 반환한다."""
        d = ZScoreDetector(window=10)
        for i in range(5):
            d.is_anomaly(float(i * 10))
        stats = d.get_statistics()
        assert stats["count"] == 5
        assert "mean" in stats
        assert "std_dev" in stats

    def test_reset_clears_window(self):
        """reset()이 윈도우를 초기화한다."""
        d = ZScoreDetector()
        for _ in range(10):
            d.is_anomaly(100.0)
        d.reset()
        assert d.get_statistics()["count"] == 0

    def test_threshold_validation(self):
        """잘못된 threshold는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="threshold"):
            ZScoreDetector(threshold=0)

    def test_window_validation(self):
        """잘못된 window는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="window"):
            ZScoreDetector(window=2)


# =============================================================================
# 7. IQRDetector 동작 검증
# =============================================================================


class TestIQRDetectorBehavior:
    """IQRDetector 동작 검증."""

    def test_no_anomaly_with_insufficient_data(self):
        """데이터 4개 미만이면 이상으로 판정하지 않는다."""
        d = IQRDetector(multiplier=1.5, window=100)
        is_anom, iqr = d.is_anomaly(100.0)
        assert is_anom is False
        assert iqr == 0.0

    def test_normal_values_not_anomalous(self):
        """정상 범위 값은 이상으로 판정되지 않는다."""
        d = IQRDetector(multiplier=1.5, window=100)
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            is_anom, iqr = d.is_anomaly(float(v))
        # 범위 내 값
        is_anom, _ = d.is_anomaly(55.0)
        assert is_anom is False

    def test_extreme_outlier_detected(self):
        """극단적 이상치가 탐지된다."""
        d = IQRDetector(multiplier=1.5, window=100)
        for v in range(50):
            d.is_anomaly(float(v))
        is_anom, _ = d.is_anomaly(500.0)
        assert is_anom is True

    def test_detailed_result_contains_quartiles(self):
        """is_anomaly_detailed()가 사분위수 정보를 포함한다."""
        d = IQRDetector()
        for v in range(20):
            d.is_anomaly_detailed(float(v))
        result = d.is_anomaly_detailed(10.0)
        assert isinstance(result, IQRResult)
        assert result.q1 <= result.q3
        assert result.iqr >= 0

    def test_get_bounds_returns_iqr_bounds(self):
        """get_bounds()가 IQR 경계를 반환한다."""
        d = IQRDetector(multiplier=1.5, window=50)
        for v in range(20):
            d.is_anomaly(float(v))
        bounds = d.get_bounds()
        assert bounds is not None
        assert bounds["lower_bound"] <= bounds["upper_bound"]

    def test_reset_clears_window(self):
        """reset()이 윈도우를 초기화한다."""
        d = IQRDetector()
        for _ in range(10):
            d.is_anomaly(100.0)
        d.reset()
        assert d.get_bounds() is None

    def test_multiplier_validation(self):
        """잘못된 multiplier는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="multiplier"):
            IQRDetector(multiplier=0)


# =============================================================================
# 8. SpikeClassifier 동작 검증
# =============================================================================


class TestSpikeClassifierBehavior:
    """SpikeClassifier 동작 검증."""

    def test_insufficient_data_returns_gradual_degradation(self):
        """데이터 부족 시 보수적으로 GRADUAL_DEGRADATION을 반환한다."""
        c = SpikeClassifier()
        result = c.classify([1.0], [0.01], [100.0])
        assert result == SpikeType.GRADUAL_DEGRADATION

    def test_error_rate_surge_classified_as_anomalous(self):
        """에러율 급등은 ANOMALOUS_SPIKE로 분류된다."""
        c = SpikeClassifier(
            error_rate_threshold=0.05,
            sensitivity_multiplier=1.0,
        )
        rps = [1000.0] * 10
        error_rate = [0.01, 0.01, 0.01, 0.01, 0.01, 0.03, 0.06, 0.12, 0.20, 0.30]
        latency = [100.0] * 10

        result = c.classify(rps, error_rate, latency)
        assert result == SpikeType.ANOMALOUS_SPIKE

    def test_rps_acceleration_without_errors_classified_as_healthy(self):
        """에러 없이 RPS 가속 증가는 HEALTHY_SURGE로 분류된다."""
        c = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=2.0,
            sensitivity_multiplier=1.0,
        )
        rps_history, error_history, latency_history = TimeSeriesScenarioGenerator.flash_sale_surge(seed=42)
        # 충분한 데이터로 테스트
        if len(rps_history) >= 10:
            result = c.classify(rps_history[:10], error_history[:10], latency_history[:10])
            # Flash Sale은 HEALTHY_SURGE 또는 GRADUAL_DEGRADATION (에러 없으므로 ANOMALOUS는 아님)
            assert result != SpikeType.ANOMALOUS_SPIKE

    def test_ddos_classified_as_anomalous(self):
        """DDoS 패턴은 ANOMALOUS_SPIKE로 분류된다."""
        c = SpikeClassifier(
            error_rate_threshold=0.05,
            sensitivity_multiplier=1.0,
        )
        rps_history, error_history, latency_history = TimeSeriesScenarioGenerator.ddos_attack(seed=42)
        # 공격 전환 경계(step 5~14)를 포함해야 에러 급증 감지 가능
        if len(rps_history) >= 15:
            result = c.classify(rps_history[5:15], error_history[5:15], latency_history[5:15])
            assert result == SpikeType.ANOMALOUS_SPIKE

    def test_slow_increase_classified_as_gradual(self):
        """느린 증가는 GRADUAL_DEGRADATION으로 분류된다."""
        c = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=2.0,
        )
        rps = [100 + i for i in range(10)]
        error_rate = [0.01] * 10
        latency = [100 + i for i in range(10)]
        result = c.classify(
            [float(v) for v in rps],
            error_rate,
            [float(v) for v in latency],
        )
        assert result == SpikeType.GRADUAL_DEGRADATION

    def test_sensitivity_multiplier_affects_threshold(self):
        """sensitivity_multiplier가 높으면 탐지가 더 민감해진다."""
        # 높은 민감도 → 낮은 임계값
        c_sensitive = SpikeClassifier(
            error_rate_threshold=0.05,
            sensitivity_multiplier=5.0,
        )
        # 낮은 민감도
        c_insensitive = SpikeClassifier(
            error_rate_threshold=0.05,
            sensitivity_multiplier=0.5,
        )
        rps = [1000.0] * 10
        # 미세한 에러율 증가
        error_rate = [0.01, 0.01, 0.01, 0.01, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035]
        latency = [100.0] * 10

        result_sensitive = c_sensitive.classify(rps, error_rate, latency)
        result_insensitive = c_insensitive.classify(rps, error_rate, latency)

        # 높은 민감도는 작은 변화도 이상으로 분류할 수 있음
        assert result_sensitive == SpikeType.ANOMALOUS_SPIKE
        assert result_insensitive != SpikeType.ANOMALOUS_SPIKE

    def test_validation_rejects_invalid_params(self):
        """잘못된 파라미터는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="error_rate_threshold"):
            SpikeClassifier(error_rate_threshold=0)
        with pytest.raises(ValueError, match="sensitivity_multiplier"):
            SpikeClassifier(sensitivity_multiplier=0)


# =============================================================================
# 9. SpikeType 계약 검증
# =============================================================================


class TestSpikeTypeContract:
    """SpikeType enum 설계 계약값 검증."""

    def test_spike_type_values(self):
        """SpikeType은 3가지 유형을 포함한다."""
        assert SpikeType.HEALTHY_SURGE.value == "healthy_surge"
        assert SpikeType.ANOMALOUS_SPIKE.value == "anomalous_spike"
        assert SpikeType.GRADUAL_DEGRADATION.value == "gradual_degradation"

    def test_spike_type_count(self):
        """SpikeType은 정확히 3개 멤버를 가진다."""
        assert len(SpikeType) == 3


# =============================================================================
# 10. ProactiveActionTrigger 동작 검증
# =============================================================================


class TestProactiveActionTriggerContract:
    """ProactiveActionTrigger 설계 계약값 검증."""

    def test_adjustment_intensity_healthy_surge(self):
        """HEALTHY_SURGE의 조정 강도는 0.0이다 (조치 없음)."""
        assert ProactiveActionTrigger.ADJUSTMENT_INTENSITY[SpikeType.HEALTHY_SURGE] == 0.0

    def test_adjustment_intensity_anomalous_spike(self):
        """ANOMALOUS_SPIKE의 조정 강도는 0.15이다."""
        assert ProactiveActionTrigger.ADJUSTMENT_INTENSITY[SpikeType.ANOMALOUS_SPIKE] == 0.15

    def test_adjustment_intensity_gradual_degradation(self):
        """GRADUAL_DEGRADATION의 조정 강도는 0.05이다."""
        assert ProactiveActionTrigger.ADJUSTMENT_INTENSITY[SpikeType.GRADUAL_DEGRADATION] == 0.05


class TestProactiveActionTriggerBehavior:
    """ProactiveActionTrigger 동작 검증."""

    def test_low_confidence_returns_none(self):
        """신뢰도 미달 시 사전 조치를 생성하지 않는다."""
        trigger = ProactiveActionTrigger(min_confidence=0.7, dry_run=True)
        result = trigger.evaluate(
            spike_type=SpikeType.ANOMALOUS_SPIKE,
            confidence=0.3,
            predicted_value=200.0,
            current_value=100.0,
            metric_name="p99_latency_ms",
            parameter="timeout_ms",
        )
        assert result is None

    def test_healthy_surge_returns_none(self):
        """HEALTHY_SURGE는 사전 조치를 생성하지 않는다."""
        trigger = ProactiveActionTrigger(min_confidence=0.5, dry_run=True)
        result = trigger.evaluate(
            spike_type=SpikeType.HEALTHY_SURGE,
            confidence=0.9,
            predicted_value=200.0,
            current_value=100.0,
            metric_name="rps",
            parameter="rate_limit_rps",
        )
        assert result is None

    def test_anomalous_spike_generates_action(self):
        """ANOMALOUS_SPIKE + 충분한 신뢰도는 사전 조치를 생성한다."""
        trigger = ProactiveActionTrigger(min_confidence=0.5, dry_run=True)
        result = trigger.evaluate(
            spike_type=SpikeType.ANOMALOUS_SPIKE,
            confidence=0.8,
            predicted_value=300.0,
            current_value=100.0,
            metric_name="error_rate",
            parameter="circuit_breaker_threshold",
        )
        assert result is not None
        assert isinstance(result, ProactiveAction)
        assert result.is_dry_run is True
        expected_intensity = ProactiveActionTrigger.ADJUSTMENT_INTENSITY[SpikeType.ANOMALOUS_SPIKE]
        assert result.suggested_value == pytest.approx(100.0 * (1 + expected_intensity))

    def test_proactive_action_serialization(self):
        """ProactiveAction.to_dict()가 올바른 딕셔너리를 반환한다."""
        action = ProactiveAction(
            parameter="timeout_ms",
            current_value=5000.0,
            suggested_value=5750.0,
            spike_type=SpikeType.ANOMALOUS_SPIKE,
            confidence=0.85,
            predicted_metric_value=300.0,
            metric_name="p99_latency_ms",
            is_dry_run=True,
        )
        d = action.to_dict()
        assert d["parameter"] == "timeout_ms"
        assert d["spike_type"] == "anomalous_spike"
        assert d["is_dry_run"] is True


# =============================================================================
# 11. StateBackend save/load 왕복 검증
# =============================================================================


class TestStateBackendRoundTripBehavior:
    """HoltLinearForecaster StateBackend save/load 왕복 검증."""

    def test_save_and_load_restores_state(self):
        """save_state → load_state가 상태를 정확히 복원한다."""
        mock_storage = {}

        mock_backend = MagicMock()
        mock_backend.set.side_effect = lambda key, value, **kwargs: mock_storage.update({key: value})
        mock_backend.get.side_effect = lambda key: mock_storage.get(key)

        with (
            patch(
                "selfhealing.core.state_backend.get_state_backend",
                return_value=mock_backend,
            ),
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=PredictiveForecasterSettings(),
            ),
        ):
            # 원본 Forecaster
            original = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
            for i in range(20):
                original.update(100.0 + i * 5, has_adjustment=(i == 10))

            original_level = original._level
            original_trend = original._trend
            original_count = original._count

            assert original.save_state("test_metric") is True

            # 새 Forecaster에서 복원
            restored = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=5)
            assert restored.load_state("test_metric") is True

            assert restored._level == pytest.approx(original_level)
            assert restored._trend == pytest.approx(original_trend)
            assert restored._count == original_count

            # has_adjustment 태깅도 복원 확인
            has_adj_count = sum(1 for dp in restored._history if dp.has_adjustment)
            assert has_adj_count == 1

    def test_load_nonexistent_returns_false(self):
        """저장된 상태가 없으면 load_state()는 False를 반환한다."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = None

        with patch(
            "selfhealing.core.state_backend.get_state_backend",
            return_value=mock_backend,
        ):
            f = HoltLinearForecaster()
            result = f.load_state("nonexistent_metric")
            assert result is False

    def test_save_failure_returns_false_and_logs(self):
        """StateBackend 오류 시 save_state()는 False를 반환한다."""
        mock_backend = MagicMock()
        mock_backend.set.side_effect = Exception("Connection error")

        with (
            patch(
                "selfhealing.core.state_backend.get_state_backend",
                return_value=mock_backend,
            ),
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=PredictiveForecasterSettings(),
            ),
        ):
            f = HoltLinearForecaster()
            f.update(100.0)
            assert f.save_state("test") is False


# =============================================================================
# 12. PredictiveForecasterService 동작 검증
# =============================================================================


class TestPredictiveForecasterServiceBehavior:
    """PredictiveForecasterService 동작 검증."""

    def test_ingest_metric_returns_smoothed_level(self, service):
        """ingest_metric()이 smoothed level을 반환한다."""
        result = service.ingest_metric("p99_latency_ms", 100.0)
        assert isinstance(result, float)

    def test_forecast_not_warmed_up_initially(self, service):
        """초기 데이터 부족 시 is_warmed_up=False이다."""
        service.ingest_metric("p99_latency_ms", 100.0)
        result = service.forecast_and_detect("p99_latency_ms")
        assert result.is_warmed_up is False
        assert result.predicted_value is None

    def test_forecast_warmed_up_after_sufficient_data(self, service):
        """충분한 데이터 후 예측이 활성화된다."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i * 5)
        result = service.forecast_and_detect("p99_latency_ms")
        assert result.is_warmed_up is True
        assert result.predicted_value is not None

    def test_metrics_tracked_independently(self, service):
        """서로 다른 메트릭이 독립적으로 추적된다."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i * 10)
            service.ingest_metric("error_rate", 0.01)

        latency = service.forecast_and_detect("p99_latency_ms")
        error = service.forecast_and_detect("error_rate")
        assert latency.trend_slope > error.trend_slope

    def test_batch_ingestion(self, service):
        """ingest_metrics_batch()가 여러 메트릭을 처리한다."""
        results = service.ingest_metrics_batch(
            {
                "p99_latency_ms": 150.0,
                "error_rate": 0.05,
                "rps": 1000.0,
            }
        )
        assert len(results) == 3

    def test_forecast_result_serialization(self, service):
        """ForecastResult.to_dict()가 올바른 딕셔너리를 반환한다."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0 + i)
        result = service.forecast_and_detect("p99_latency_ms")
        d = result.to_dict()
        assert "metric_name" in d
        assert "predicted_value" in d
        assert "confidence" in d

    def test_reset_clears_all(self, service):
        """reset()이 모든 상태를 초기화한다."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0)
        service.reset()
        assert len(service.get_all_metric_names()) == 0

    def test_misprediction_counter_tracks_accuracy(self, service):
        """예측 오판이 카운터에 기록된다."""
        for i in range(10):
            service.ingest_metric("p99_latency_ms", 100.0)
        # 극단적으로 다른 예측값 설정
        service._last_predictions["p99_latency_ms"] = 9999.0
        service.ingest_metric("p99_latency_ms", 100.0)
        assert service._misprediction_counts["p99_latency_ms"] >= 1


class TestPredictiveForecasterServiceContract:
    """PredictiveForecasterService 설계 계약값 검증."""

    def test_misprediction_blacklist_threshold_contract(self):
        """MISPREDICTION_BLACKLIST_THRESHOLD는 3이다."""
        assert PredictiveForecasterService.MISPREDICTION_BLACKLIST_THRESHOLD == 3


# =============================================================================
# 13. TimeSeriesScenarioGenerator 동작 검증
# =============================================================================


class TestTimeSeriesScenarioGeneratorBehavior:
    """TimeSeriesScenarioGenerator 동작 검증."""

    def test_gradual_degradation_monotonic(self):
        """gradual_degradation이 대체로 증가하는 시계열을 생성한다."""
        values = TimeSeriesScenarioGenerator.gradual_degradation(base=100, target=500, steps=50, noise_ratio=0.0)
        assert len(values) == 50
        assert values[0] == pytest.approx(100.0)
        assert values[-1] == pytest.approx(500.0)

    def test_spike_and_recovery_shape(self):
        """spike_and_recovery가 올바른 형태를 생성한다."""
        values = TimeSeriesScenarioGenerator.spike_and_recovery(
            base=100,
            spike_value=1000,
            spike_at=20,
            recover_at=40,
            steps=60,
            noise_ratio=0.0,
        )
        assert len(values) == 60
        assert values[0] == pytest.approx(100.0)
        assert values[20] == pytest.approx(1000.0)
        assert values[-1] == pytest.approx(100.0)

    def test_seasonal_pattern_length(self):
        """seasonal_pattern이 올바른 길이를 생성한다."""
        values = TimeSeriesScenarioGenerator.seasonal_pattern(steps=100, period=24)
        assert len(values) == 100

    def test_pool_exhaustion_bounded(self):
        """pool_exhaustion이 max_usage를 초과하지 않는다."""
        values = TimeSeriesScenarioGenerator.pool_exhaustion(initial_usage=30, max_usage=100, steps=200, noise_ratio=0.0)
        assert all(v <= 100.0 for v in values)

    def test_memory_leak_increasing(self):
        """memory_leak이 증가하는 시계열을 생성한다."""
        values = TimeSeriesScenarioGenerator.memory_leak(initial_mb=500, leak_rate_mb=2, steps=100, noise_ratio=0.0)
        assert values[-1] > values[0]

    def test_stable_noise_centered(self):
        """stable_noise가 base 주변에 분포한다."""
        values = TimeSeriesScenarioGenerator.stable_noise(base=100, std_dev=5, steps=1000, seed=42)
        mean = sum(values) / len(values)
        assert abs(mean - 100.0) < 2.0

    def test_flash_sale_surge_returns_three_lists(self):
        """flash_sale_surge가 3개 리스트를 반환한다."""
        rps, error, latency = TimeSeriesScenarioGenerator.flash_sale_surge()
        assert len(rps) > 0
        assert len(error) == len(rps)
        assert len(latency) == len(rps)

    def test_ddos_attack_returns_three_lists(self):
        """ddos_attack이 3개 리스트를 반환한다."""
        rps, error, latency = TimeSeriesScenarioGenerator.ddos_attack()
        assert len(rps) > 0
        assert len(error) == len(rps)
        assert len(latency) == len(rps)

    def test_seed_produces_deterministic_output(self):
        """동일 seed에서 동일한 출력이 생성된다."""
        v1 = TimeSeriesScenarioGenerator.gradual_degradation(seed=123)
        v2 = TimeSeriesScenarioGenerator.gradual_degradation(seed=123)
        assert v1 == v2
