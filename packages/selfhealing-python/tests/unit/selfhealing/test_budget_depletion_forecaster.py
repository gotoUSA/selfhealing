"""
BudgetDepletionForecaster 단위 테스트.

테스트 대상:
1. forecast() - 예산 소진 예측 결과 반환
2. should_preemptive_throttle() - 선제적 Throttle 필요 여부 판단
3. get_preemptive_multiplier() - 적용할 Throttle 배율 반환
4. _determine_risk_level() - 위험 수준 판단 로직
"""

import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass

from selfhealing.services.error_budget.forecaster import (
    BudgetDepletionForecaster,
    DepletionForecast,
)


@dataclass
class MockErrorBudgetStatus:
    """테스트용 ErrorBudgetStatus Mock."""

    burn_rate_1h: float = 1.0
    burn_rate_6h: float = 1.0
    budget_remaining_percent: float = 100.0


class TestDepletionForecastDataclass:
    """DepletionForecast 데이터 클래스 테스트."""

    def test_forecast_creation(self):
        """DepletionForecast 생성 검증."""
        forecast = DepletionForecast(
            estimated_depletion_hours=5.0,
            burn_rate_1h=10.0,
            burn_rate_6h=8.0,
            is_accelerating=True,
            risk_level="high",
            recommended_action="사전 조정 권장",
        )

        assert forecast.estimated_depletion_hours == 5.0
        assert forecast.burn_rate_1h == 10.0
        assert forecast.burn_rate_6h == 8.0
        assert forecast.is_accelerating is True
        assert forecast.risk_level == "high"
        assert forecast.recommended_action == "사전 조정 권장"

    def test_forecast_with_none_depletion(self):
        """정상 상태 시 estimated_depletion_hours가 None."""
        forecast = DepletionForecast(
            estimated_depletion_hours=None,
            burn_rate_1h=0.5,
            burn_rate_6h=0.5,
            is_accelerating=False,
            risk_level="low",
            recommended_action="정상 운영 유지",
        )

        assert forecast.estimated_depletion_hours is None
        assert forecast.risk_level == "low"


class TestBudgetDepletionForecasterRiskThresholds:
    """BudgetDepletionForecaster 위험 임계치 상수 테스트."""

    def test_risk_thresholds_exist(self):
        """RISK_THRESHOLDS 상수 존재 확인."""
        assert hasattr(BudgetDepletionForecaster, "RISK_THRESHOLDS")

        thresholds = BudgetDepletionForecaster.RISK_THRESHOLDS
        assert "critical" in thresholds
        assert "high" in thresholds
        assert "medium" in thresholds

    def test_risk_thresholds_order(self):
        """위험 임계치 순서 (critical < high < medium)."""
        thresholds = BudgetDepletionForecaster.RISK_THRESHOLDS

        assert thresholds["critical"] < thresholds["high"]
        assert thresholds["high"] < thresholds["medium"]

    def test_acceleration_threshold_exists(self):
        """ACCELERATION_THRESHOLD 상수 존재 확인."""
        assert hasattr(BudgetDepletionForecaster, "ACCELERATION_THRESHOLD")
        assert BudgetDepletionForecaster.ACCELERATION_THRESHOLD > 1.0


class TestBudgetDepletionForecasterForecast:
    """BudgetDepletionForecaster.forecast() 메서드 테스트."""

    def setup_method(self):
        self.forecaster = BudgetDepletionForecaster()

    def test_low_burn_rate_returns_low_risk(self):
        """Burn Rate <= 1.0 시 low 위험 수준."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=1.0,
            burn_rate_6h=1.0,
            budget_remaining_percent=50.0,
        )

        forecast = self.forecaster.forecast(status)

        assert forecast.risk_level == "low"
        assert forecast.estimated_depletion_hours is None
        assert "정상 운영" in forecast.recommended_action

    def test_critical_burn_rate_returns_critical_risk(self):
        """높은 Burn Rate + 낮은 잔여 예산 시 critical 위험."""
        # 1시간 이내 소진 예상: burn_rate=15.0, remaining=10%
        # hourly_depletion = (15 - 1) * 0.1 = 1.4%
        # estimated = 10 / 1.4 ≈ 7.14h (아직 critical이 아님)

        # critical을 만들려면:
        # estimated < 1h → remaining / ((burn_rate - 1) * 0.1) < 1
        # remaining < (burn_rate - 1) * 0.1
        # remaining=0.5%, burn_rate=15 → 0.5 / 1.4 ≈ 0.36h (critical)
        status = MockErrorBudgetStatus(
            burn_rate_1h=15.0,
            burn_rate_6h=10.0,
            budget_remaining_percent=0.5,
        )

        forecast = self.forecaster.forecast(status)

        assert forecast.risk_level == "critical"
        assert forecast.estimated_depletion_hours is not None
        assert forecast.estimated_depletion_hours < BudgetDepletionForecaster.RISK_THRESHOLDS["critical"]

    def test_high_burn_rate_returns_high_risk(self):
        """6시간 이내 소진 예상 시 high 위험."""
        # estimated 1-6h 범위: high
        # remaining=5%, burn_rate=10 → 5 / 0.9 ≈ 5.55h (high)
        status = MockErrorBudgetStatus(
            burn_rate_1h=10.0,
            burn_rate_6h=8.0,
            budget_remaining_percent=5.0,
        )

        forecast = self.forecaster.forecast(status)

        thresholds = BudgetDepletionForecaster.RISK_THRESHOLDS
        assert forecast.risk_level == "high"
        assert forecast.estimated_depletion_hours is not None
        assert thresholds["critical"] <= forecast.estimated_depletion_hours < thresholds["high"]

    def test_medium_burn_rate_returns_medium_risk(self):
        """24시간 이내 소진 예상 시 medium 위험."""
        # estimated 6-24h 범위: medium
        # remaining=10%, burn_rate=5 → 10 / 0.4 = 25h (low가 됨)
        # remaining=5%, burn_rate=5 → 5 / 0.4 = 12.5h (medium)
        status = MockErrorBudgetStatus(
            burn_rate_1h=5.0,
            burn_rate_6h=4.0,
            budget_remaining_percent=5.0,
        )

        forecast = self.forecaster.forecast(status)

        thresholds = BudgetDepletionForecaster.RISK_THRESHOLDS
        assert forecast.risk_level == "medium"
        assert forecast.estimated_depletion_hours is not None
        assert thresholds["high"] <= forecast.estimated_depletion_hours < thresholds["medium"]

    def test_acceleration_detection(self):
        """Burn Rate 가속 감지 (1h > 6h * 1.2)."""
        threshold = BudgetDepletionForecaster.ACCELERATION_THRESHOLD

        # 가속 상태
        status_accelerating = MockErrorBudgetStatus(
            burn_rate_1h=15.0,
            burn_rate_6h=10.0,  # 15 > 10 * 1.2 = 12
            budget_remaining_percent=50.0,
        )
        forecast = self.forecaster.forecast(status_accelerating)
        assert forecast.is_accelerating is True

        # 비가속 상태
        status_not_accelerating = MockErrorBudgetStatus(
            burn_rate_1h=11.0,
            burn_rate_6h=10.0,  # 11 <= 10 * 1.2 = 12
            budget_remaining_percent=50.0,
        )
        forecast = self.forecaster.forecast(status_not_accelerating)
        assert forecast.is_accelerating is False


class TestBudgetDepletionForecasterPreemptive:
    """BudgetDepletionForecaster 선제적 Throttle 판단 테스트."""

    def setup_method(self):
        self.forecaster = BudgetDepletionForecaster()

    def test_should_preemptive_throttle_critical(self):
        """critical 위험 시 선제적 Throttle 필요."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=20.0,
            burn_rate_6h=15.0,
            budget_remaining_percent=0.5,  # 매우 낮은 잔여 예산
        )

        assert self.forecaster.should_preemptive_throttle(status) is True

    def test_should_preemptive_throttle_high_with_acceleration(self):
        """high 위험 + 가속 시 선제적 Throttle 필요."""
        # high risk + accelerating
        status = MockErrorBudgetStatus(
            burn_rate_1h=12.0,
            burn_rate_6h=8.0,  # 12 > 8 * 1.2 = 9.6 → accelerating
            budget_remaining_percent=5.0,  # high risk 범위
        )

        forecast = self.forecaster.forecast(status)
        assert forecast.risk_level == "high"
        assert forecast.is_accelerating is True
        assert self.forecaster.should_preemptive_throttle(status) is True

    def test_should_not_preemptive_throttle_high_without_acceleration(self):
        """high 위험이지만 가속이 아니면 선제적 Throttle 불필요."""
        # high risk + not accelerating
        status = MockErrorBudgetStatus(
            burn_rate_1h=10.0,
            burn_rate_6h=10.0,  # 10 <= 10 * 1.2 = 12 → not accelerating
            budget_remaining_percent=5.0,  # high risk 범위
        )

        forecast = self.forecaster.forecast(status)
        assert forecast.risk_level == "high"
        assert forecast.is_accelerating is False
        assert self.forecaster.should_preemptive_throttle(status) is False

    def test_should_not_preemptive_throttle_low(self):
        """low 위험 시 선제적 Throttle 불필요."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=0.5,
            burn_rate_6h=0.5,
            budget_remaining_percent=80.0,
        )

        assert self.forecaster.should_preemptive_throttle(status) is False

    def test_get_preemptive_multiplier_critical(self):
        """critical 위험 시 0.5 배율 반환."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=20.0,
            burn_rate_6h=15.0,
            budget_remaining_percent=0.5,
        )

        multiplier = self.forecaster.get_preemptive_multiplier(status)
        assert multiplier == 0.5

    def test_get_preemptive_multiplier_high(self):
        """high 위험 시 0.8 배율 반환."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=10.0,
            burn_rate_6h=8.0,
            budget_remaining_percent=5.0,
        )

        forecast = self.forecaster.forecast(status)
        assert forecast.risk_level == "high"

        multiplier = self.forecaster.get_preemptive_multiplier(status)
        assert multiplier == 0.8

    def test_get_preemptive_multiplier_low(self):
        """low 위험 시 1.0 배율 반환 (변경 없음)."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=0.5,
            burn_rate_6h=0.5,
            budget_remaining_percent=80.0,
        )

        multiplier = self.forecaster.get_preemptive_multiplier(status)
        assert multiplier == 1.0


class TestBudgetDepletionForecasterEdgeCases:
    """BudgetDepletionForecaster 경계 조건 테스트."""

    def setup_method(self):
        self.forecaster = BudgetDepletionForecaster()

    def test_zero_remaining_budget(self):
        """잔여 예산 0% 시 즉시 critical 반환."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=5.0,
            burn_rate_6h=5.0,
            budget_remaining_percent=0.0,
        )

        forecast = self.forecaster.forecast(status)

        assert forecast.risk_level == "critical"
        assert forecast.estimated_depletion_hours == 0.0
        assert "소진" in forecast.recommended_action

    def test_negative_remaining_budget(self):
        """잔여 예산 음수 (초과 소진) 시 critical 반환."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=5.0,
            burn_rate_6h=5.0,
            budget_remaining_percent=-5.0,
        )

        forecast = self.forecaster.forecast(status)

        assert forecast.risk_level == "critical"
        assert forecast.estimated_depletion_hours == 0.0

    def test_burn_rate_exactly_one(self):
        """Burn Rate 정확히 1.0 시 low 위험."""
        status = MockErrorBudgetStatus(
            burn_rate_1h=1.0,
            burn_rate_6h=1.0,
            budget_remaining_percent=10.0,
        )

        forecast = self.forecaster.forecast(status)
        assert forecast.risk_level == "low"
        assert forecast.estimated_depletion_hours is None
