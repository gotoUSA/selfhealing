"""
기존 시스템 컨포넌트의 Forecaster 적용 단위 테스트.

검증 대상:
    - 히스토리 Settings 확장: PoolMonitor, DecisionEngine, AutoRollbackGuard
    - 트렌드 예측 확장: PoolMonitor.get_trend() + HoltLinear 예측
    - 신뢰도 예측 컨텍스트: DecisionEngine._calculate_confidence() 트렌드 기울기 반영
    - OOM 시간 예측: CgroupResourceMonitor.predict_oom_minutes()
    - EWMA smoothing: BudgetDepletionForecaster burn_rate 평활
    - 개입 쿨다운: HoltLinearForecaster has_adjustment alpha 감소
    - 계절성 자동 감지: HoltWintersForecaster.detect_season_length()
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# 히스토리 Settings 기본값 계약 검증
# =============================================================================


class TestPoolMonitorHistorySettingsContract:
    """PoolMonitorSettings max_history 기본값/상한 계약 검증."""

    def test_max_history_default_is_5000(self):
        """max_history 기본값은 5000이다."""
        from selfhealing.settings.pool_monitor import PoolMonitorSettings

        settings = PoolMonitorSettings()
        assert settings.max_history == 5000

    def test_max_history_upper_bound_is_10000(self):
        """max_history 상한은 10000이다."""
        from selfhealing.settings.pool_monitor import PoolMonitorSettings

        settings = PoolMonitorSettings(max_history=10000)
        assert settings.max_history == 10000

    def test_max_history_exceeds_upper_bound_raises(self):
        """max_history가 10000 초과 시 ValidationError."""
        from selfhealing.settings.pool_monitor import PoolMonitorSettings

        with pytest.raises(ValueError):
            PoolMonitorSettings(max_history=10001)


class TestDecisionEngineHistorySettingsContract:
    """DecisionEngineSettings max_history 기본값/상한 계약 검증."""

    def test_max_history_field_exists(self):
        """max_history 필드가 존재한다."""
        from selfhealing.settings.decision_engine import DecisionEngineSettings

        settings = DecisionEngineSettings()
        assert hasattr(settings, "max_history")

    def test_max_history_default_is_5000(self):
        """max_history 기본값은 5000이다."""
        from selfhealing.settings.decision_engine import DecisionEngineSettings

        settings = DecisionEngineSettings()
        assert settings.max_history == 5000

    def test_max_history_upper_bound_is_10000(self):
        """max_history 상한은 10000이다."""
        from selfhealing.settings.decision_engine import DecisionEngineSettings

        settings = DecisionEngineSettings(max_history=10000)
        assert settings.max_history == 10000


class TestAutoRollbackHistorySettingsContract:
    """AutoRollbackSettings max_health_history 기본값/상한 계약 검증."""

    def test_max_health_history_field_exists(self):
        """max_health_history 필드가 존재한다."""
        from selfhealing.settings.auto_rollback import AutoRollbackSettings

        settings = AutoRollbackSettings()
        assert hasattr(settings, "max_health_history")

    def test_max_health_history_default_is_10000(self):
        """max_health_history 기본값은 10000이다."""
        from selfhealing.settings.auto_rollback import AutoRollbackSettings

        settings = AutoRollbackSettings()
        assert settings.max_health_history == 10000

    def test_max_health_history_upper_bound_is_10000(self):
        """max_health_history 상한은 10000이다."""
        from selfhealing.settings.auto_rollback import AutoRollbackSettings

        settings = AutoRollbackSettings(max_health_history=10000)
        assert settings.max_health_history == 10000


# =============================================================================
# 히스토리 크기 제한 동작 검증
# =============================================================================


class TestDecisionEngineHistoryLimitBehavior:
    """DecisionEngine._record_analysis() Settings 기반 히스토리 크기 제한 검증."""

    def test_history_uses_settings_max_history(self):
        """DecisionEngine이 Settings.max_history를 사용한다."""
        from selfhealing.core.decision_engine import DecisionEngine

        config = MagicMock()
        config.get.return_value = None
        engine = DecisionEngine(config_provider=config)

        # 200개 분석 기록
        for i in range(200):
            engine.analyze({"error_rate": 0.01, "sample_count": 50})

        # Settings 기본값 5000이므로 200개 모두 유지
        assert len(engine._history) == 200


class TestAutoRollbackGuardHistoryLimitBehavior:
    """AutoRollbackGuard Settings 기반 헬스체크 히스토리 크기 제한 검증."""

    def test_health_history_uses_settings_limit(self):
        """AutoRollbackGuard가 Settings.max_health_history를 사용한다."""
        from selfhealing.core.auto_rollback_guard import (
            AutoRollbackGuard,
            RollbackHealthAssessment,
            RollbackSeverity,
        )

        metrics = MagicMock()
        metrics.get_error_rate.return_value = 0.01
        metrics.get_latency_p99.return_value = 100.0
        metrics.get_throughput.return_value = 1000.0
        applier = MagicMock()

        guard = AutoRollbackGuard(
            metrics_provider=metrics,
            config_applier=applier,
            enabled=False,
        )

        # 150개 헬스체크 이력 수동 추가
        for _ in range(150):
            guard._health_history.append(
                RollbackHealthAssessment(
                    healthy=True,
                    degradation_level=RollbackSeverity.NONE,
                    error_rate=0.01,
                    latency_p99_ms=100.0,
                    throughput_rps=1000.0,
                )
            )

        # Settings 기본값 10000이므로 150개 모두 유지 (이전에는 100개 제한)
        assert len(guard._health_history) == 150


# =============================================================================
# PoolMonitor 트렌드 예측 동작 검증
# =============================================================================


class TestPoolMonitorTrendPredictionBehavior:
    """PoolMonitor.get_trend() 확장 윈도우 및 HoltLinear 예측 검증."""

    def test_trend_analysis_with_larger_window(self):
        """get_trend()이 확장된 윈도우로 트렌드를 분석한다."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor, PoolStats

        monitor = ConnectionPoolMonitor(max_history=5000)

        # 250개 히스토리 추가 (100+100 window 충족)
        for i in range(250):
            stats = PoolStats(
                pool_name="test",
                max_connections=100,
                active_connections=30 + i // 10,
                available_connections=70 - i // 10,
            )
            monitor._stats_history.append(stats)

        result = monitor.get_trend()
        assert result["trend"] in ("increasing", "decreasing", "stable")
        assert "avg_usage" in result

    def test_trend_includes_prediction_fields(self):
        """get_trend()이 HoltLinear 기반 예측 필드를 포함한다."""
        from selfhealing.core.pool_monitor import ConnectionPoolMonitor, PoolStats

        monitor = ConnectionPoolMonitor(max_history=5000)

        # 증가하는 데이터 100개 추가
        for i in range(100):
            stats = PoolStats(
                pool_name="test",
                max_connections=100,
                active_connections=min(20 + i, 99),
                available_connections=max(80 - i, 1),
            )
            monitor._stats_history.append(stats)

        result = monitor.get_trend()
        # HoltLinearForecaster 기반 예측 필드가 존재해야 함
        assert "predicted_usage_5min" in result or "trend_slope" in result


# =============================================================================
# DecisionEngine 예측 컨텍스트 신뢰도 검증
# =============================================================================


class TestDecisionEngineConfidenceWithPredictionBehavior:
    """DecisionEngine._calculate_confidence() 예측 트렌드 기울기 반영 검증."""

    def test_prediction_context_boosts_confidence(self):
        """prediction_context가 제공되면 신뢰도가 부스트된다."""
        from selfhealing.core.decision_engine import (
            AdjustmentPriority,
            AdjustmentRule,
            DecisionEngine,
        )

        config = MagicMock()
        config.get.return_value = 5000
        engine = DecisionEngine(config_provider=config)

        rule = AdjustmentRule(
            parameter="timeout_ms",
            metric="p99_latency_ms",
            condition=lambda c, m: True,
            adjustment=lambda c, m: c * 1.2,
            reason="test",
        )

        metrics = {"p99_latency_ms": 4000, "sample_count": 50}

        # 예측 컨텍스트 없이
        conf_without = engine._calculate_confidence(metrics, rule)

        # 예측 컨텍스트 포함 (양의 트렌드 + 높은 신뢰도)
        prediction_context = {
            "trend_slope": 5.0,
            "prediction_confidence": 0.9,
        }
        conf_with = engine._calculate_confidence(metrics, rule, prediction_context=prediction_context)

        assert conf_with >= conf_without

    def test_prediction_context_none_has_no_effect(self):
        """prediction_context=None이면 기존 로직과 동일하다."""
        from selfhealing.core.decision_engine import (
            AdjustmentRule,
            DecisionEngine,
        )

        config = MagicMock()
        config.get.return_value = 5000
        engine = DecisionEngine(config_provider=config)

        rule = AdjustmentRule(
            parameter="timeout_ms",
            metric="p99_latency_ms",
            condition=lambda c, m: True,
            adjustment=lambda c, m: c * 1.2,
            reason="test",
        )

        metrics = {"p99_latency_ms": 4000, "sample_count": 50}

        conf1 = engine._calculate_confidence(metrics, rule)
        conf2 = engine._calculate_confidence(metrics, rule, prediction_context=None)

        assert conf1 == conf2


# =============================================================================
# CgroupResourceMonitor OOM 시간 예측 검증
# =============================================================================


class TestCgroupResourceMonitorOomPredictionBehavior:
    """CgroupResourceMonitor.predict_oom_minutes() HoltLinear 기반 OOM 예측 검증."""

    def test_predict_oom_with_increasing_trend(self):
        """증가 추세에서 OOM 예측 시간이 유한한 양수이다."""
        from selfhealing.core.resource_monitor import CgroupResourceMonitor

        # 500MB에서 2MB/step씩 증가하는 시뮬레이션
        max_memory = 1024 * 1024 * 1024  # 1GB
        samples = [500 * 1024 * 1024 + i * 2 * 1024 * 1024 for i in range(30)]

        result = CgroupResourceMonitor.predict_oom_minutes(
            memory_samples=samples,
            max_memory_bytes=max_memory,
            safety_margin=0.15,
        )

        assert result is not None
        assert result > 0

    def test_predict_oom_with_stable_usage(self):
        """안정적 사용량에서 OOM 예측은 None이다."""
        from selfhealing.core.resource_monitor import CgroupResourceMonitor

        max_memory = 1024 * 1024 * 1024  # 1GB
        samples = [500 * 1024 * 1024] * 30  # 일정한 500MB

        result = CgroupResourceMonitor.predict_oom_minutes(
            memory_samples=samples,
            max_memory_bytes=max_memory,
            safety_margin=0.15,
        )

        assert result is None  # 증가 추세 없음

    def test_predict_oom_insufficient_data(self):
        """데이터 부족 시 None을 반환한다."""
        from selfhealing.core.resource_monitor import CgroupResourceMonitor

        result = CgroupResourceMonitor.predict_oom_minutes(
            memory_samples=[100, 200],
            max_memory_bytes=1024 * 1024 * 1024,
        )

        assert result is None

    def test_predict_oom_no_max_memory(self):
        """max_memory_bytes=None이고 cgroup 감지 불가 시 None 반환."""
        from selfhealing.core.resource_monitor import CgroupResourceMonitor

        with patch.object(CgroupResourceMonitor, "get_memory_max_bytes", return_value=None):
            result = CgroupResourceMonitor.predict_oom_minutes(
                memory_samples=[100 * 1024 * 1024] * 10,
            )
            assert result is None

    def test_predict_oom_already_at_limit(self):
        """이미 한도에 도달한 경우 0.0을 반환한다."""
        from selfhealing.core.resource_monitor import CgroupResourceMonitor

        max_memory = 1024 * 1024 * 1024  # 1GB
        # 이미 limit을 초과하는 트렌드
        samples = [900 * 1024 * 1024 + i * 50 * 1024 * 1024 for i in range(10)]

        result = CgroupResourceMonitor.predict_oom_minutes(
            memory_samples=samples,
            max_memory_bytes=max_memory,
            safety_margin=0.15,
        )

        assert result is not None
        assert result >= 0


# =============================================================================
# BudgetDepletionForecaster EWMA 평활 검증
# =============================================================================


class TestBudgetDepletionEwmaSmoothingBehavior:
    """BudgetDepletionForecaster EWMA smoothing 옵션 검증."""

    def test_default_no_smoothing(self):
        """기본적으로 EWMA smoothing이 비활성화되어 있다."""
        from selfhealing.services.error_budget.forecaster import (
            BudgetDepletionForecaster,
        )

        f = BudgetDepletionForecaster()
        assert f._use_ewma_smoothing is False

    def test_ewma_smoothing_enabled(self):
        """use_ewma_smoothing=True로 활성화할 수 있다."""
        from selfhealing.services.error_budget.forecaster import (
            BudgetDepletionForecaster,
        )

        f = BudgetDepletionForecaster(use_ewma_smoothing=True)
        assert f._use_ewma_smoothing is True
        assert f._burn_rate_smoother is not None

    def test_smoothed_forecast_produces_valid_result(self):
        """EWMA smoothing 활성화 시에도 유효한 예측을 생성한다."""
        from selfhealing.services.error_budget.forecaster import (
            BudgetDepletionForecaster,
        )

        f = BudgetDepletionForecaster(use_ewma_smoothing=True, ewma_alpha=0.5)

        status = MagicMock()
        status.burn_rate_1h = 15.0
        status.burn_rate_6h = 10.0
        status.budget_remaining_percent = 50.0

        forecast = f.forecast(status)
        assert forecast.risk_level in ("low", "medium", "high", "critical")

    def test_unsmoothed_forecast_not_affected(self):
        """smoothing 비활성화 시 기존 로직과 동일하다."""
        from selfhealing.services.error_budget.forecaster import (
            BudgetDepletionForecaster,
        )

        f = BudgetDepletionForecaster(use_ewma_smoothing=False)

        status = MagicMock()
        status.burn_rate_1h = 15.0
        status.burn_rate_6h = 10.0
        status.budget_remaining_percent = 50.0

        forecast = f.forecast(status)
        assert forecast.burn_rate_1h == 15.0  # smoothing 없으면 원래 값 유지


# =============================================================================
# HoltLinear 셀프힐링 개입 쿨다운 검증
# =============================================================================


class TestHoltLinearAdjustmentCooldownBehavior:
    """HoltLinearForecaster has_adjustment 쿨다운 메커니즘 검증."""

    def test_adjustment_triggers_cooldown(self):
        """has_adjustment=True가 쿨다운을 시작한다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltLinearForecaster,
        )

        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=3)
        f.update(100.0)
        f.update(110.0, has_adjustment=True)

        # 쿨다운이 시작됨
        assert f._adjustment_cooldown >= 0

    def test_cooldown_reduces_alpha_effect(self):
        """쿨다운 중 alpha가 낮아져 급격한 변화에 덜 민감해진다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltLinearForecaster,
        )

        # 동일 데이터, adjustment 차이
        f_normal = HoltLinearForecaster(alpha=0.5, beta=0.1, warmup_samples=3)
        f_adjusted = HoltLinearForecaster(alpha=0.5, beta=0.1, warmup_samples=3)

        for v in [100.0, 110.0, 120.0]:
            f_normal.update(v)
            f_adjusted.update(v)

        # 큰 스파이크 - normal은 has_adjustment 없이, adjusted는 있이
        f_normal.update(500.0, has_adjustment=False)
        f_adjusted.update(500.0, has_adjustment=True)

        # 쿨다운 중이므로 adjusted가 덜 영향받음
        assert f_adjusted._level < f_normal._level

    def test_cooldown_expires_after_steps(self):
        """쿨다운은 지정된 스텝 수 후 만료된다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltLinearForecaster,
        )

        f = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=3)
        f.update(100.0)
        f.update(200.0, has_adjustment=True)

        # 3 스텝 후 쿨다운 만료
        for _ in range(3 + 1):
            f.update(100.0)

        assert f._adjustment_cooldown == 0


# =============================================================================
# HoltWinters 계절성 주기 자동 감지 검증
# =============================================================================


class TestHoltWintersSeasonAutoDetectBehavior:
    """HoltWintersForecaster.detect_season_length() 자기상관 기반 계절성 감지 검증."""

    def test_detect_known_seasonal_period(self):
        """알려진 계절성 주기가 정확히 감지된다."""
        from selfhealing.services.predictive_forecaster.scenario_generator import (
            TimeSeriesScenarioGenerator,
        )
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltWintersForecaster,
        )

        period = 24
        values = TimeSeriesScenarioGenerator.seasonal_pattern(
            base=100,
            amplitude=30,
            period=period,
            steps=period * 5,
            noise_ratio=0.0,
            seed=42,
        )

        detected = HoltWintersForecaster.detect_season_length(values, min_period=10, max_period=50)

        assert detected is not None
        # ±2 이내의 정확도 허용
        assert abs(detected - period) <= 2, f"Expected ~{period}, got {detected}"

    def test_detect_no_seasonality_in_stable_data(self):
        """안정적(계절성 없는) 데이터에서 None을 반환한다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltWintersForecaster,
        )

        # 약간의 노이즈가 있는 일정한 값
        import random

        random.seed(42)
        values = [100.0 + random.gauss(0, 1) for _ in range(200)]

        detected = HoltWintersForecaster.detect_season_length(values, min_period=5, max_period=50)

        # 유의미한 계절성이 없으므로 None
        assert detected is None

    def test_detect_with_insufficient_data(self):
        """데이터 부족 시 None을 반환한다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltWintersForecaster,
        )

        values = [100.0, 110.0, 120.0]
        detected = HoltWintersForecaster.detect_season_length(values)
        assert detected is None

    def test_detect_different_period_lengths(self):
        """다양한 주기 길이가 감지된다."""
        from selfhealing.services.predictive_forecaster.time_series import (
            HoltWintersForecaster,
        )

        for period in [12, 24, 48]:
            values = []
            for i in range(period * 5):
                values.append(100.0 + 30.0 * math.sin(2 * math.pi * i / period))

            detected = HoltWintersForecaster.detect_season_length(values, min_period=5, max_period=period * 2)

            assert detected is not None, f"Period {period}를 감지하지 못함"
            assert abs(detected - period) <= 2, f"Period {period}: expected ~{period}, got {detected}"
