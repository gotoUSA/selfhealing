"""
Budget Depletion Forecaster.

Burn Rate 기반 예산 소진 시점 예측.
ErrorBudgetStatus의 burn_rate_1h, burn_rate_6h를 사용하여
예산 소진을 사전에 예측하고 선제적 보호를 지원합니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.error_budget.models import ErrorBudgetStatus

logger = logging.getLogger(__name__)


@dataclass
class DepletionForecast:
    """예산 소진 예측 결과."""

    estimated_depletion_hours: float | None
    """예상 소진까지 시간 (None = 정상 범위)"""

    burn_rate_1h: float
    """1시간 Burn Rate"""

    burn_rate_6h: float
    """6시간 Burn Rate"""

    is_accelerating: bool
    """Burn Rate 가속 여부 (1h > 6h * 1.2)"""

    risk_level: str
    """위험 수준: low, medium, high, critical"""

    recommended_action: str
    """권장 조치"""


class BudgetDepletionForecaster:
    """
    예산 소진 예측기.

    Burn Rate를 기반으로 예산 소진 시점을 예측하고
    사전 경고를 제공합니다.

    Phase 3 (238_PREDICTIVE_ANOMALY_FORECASTER):
    선택적으로 EWMAForecaster를 사용하여 burn_rate 입력을
    smoothing한 후 예측 정확도를 향상시킬 수 있습니다.

    사용 예시:
        forecaster = BudgetDepletionForecaster()
        forecast = forecaster.forecast(status)
        if forecast.risk_level == "critical":
            # 즉시 조치 필요
            pass

        # EWMA smoothing 활성화
        forecaster = BudgetDepletionForecaster(use_ewma_smoothing=True)
    """

    # 위험 수준 임계치 (예상 소진 시간 기준, 시간 단위)
    RISK_THRESHOLDS = {
        "critical": 1.0,  # 1시간 이내 소진 예상
        "high": 6.0,  # 6시간 이내 소진 예상
        "medium": 24.0,  # 24시간 이내 소진 예상
    }

    # Burn Rate 가속 판단 기준 (1h burn rate가 6h의 120% 초과 시)
    ACCELERATION_THRESHOLD = 1.2

    # SLO 에러 예산 비율 (99.9% SLO 가정 = 0.1% 에러 허용)
    DEFAULT_SLO_ERROR_BUDGET_PERCENT = 0.1

    def __init__(self, use_ewma_smoothing: bool = False, ewma_alpha: float = 0.3):
        """
        Args:
            use_ewma_smoothing: True면 burn_rate_1h를 EWMA로 smoothing.
                노이즈가 많은 환경에서 예측 안정성을 향상시킴.
            ewma_alpha: EWMA 평활 계수 (0 < α ≤ 1).
        """
        self._use_ewma_smoothing = use_ewma_smoothing
        self._burn_rate_smoother = None
        if use_ewma_smoothing:
            try:
                from selfhealing.services.predictive_forecaster.time_series import (
                    EWMAForecaster,
                )

                self._burn_rate_smoother = EWMAForecaster(alpha=ewma_alpha)
            except ImportError:
                logger.debug("[BudgetDepletionForecaster] EWMAForecaster not available, " "falling back to raw burn rate")
                self._use_ewma_smoothing = False

    def forecast(self, status: ErrorBudgetStatus) -> DepletionForecast:
        """
        예산 소진 예측.

        Args:
            status: 현재 ErrorBudgetStatus

        Returns:
            DepletionForecast: 예측 결과
        """
        burn_rate_1h = status.burn_rate_1h
        burn_rate_6h = status.burn_rate_6h
        remaining_percent = status.budget_remaining_percent

        # Phase 3: EWMA smoothing (선택적)
        if self._use_ewma_smoothing and self._burn_rate_smoother is not None:
            burn_rate_1h = self._burn_rate_smoother.update(burn_rate_1h)

        # Burn Rate 가속 여부 (단기 burn rate가 장기보다 20% 이상 높음)
        is_accelerating = burn_rate_1h > burn_rate_6h * self.ACCELERATION_THRESHOLD

        # 예산이 이미 소진된 경우 (0% 이하)
        if remaining_percent <= 0:
            return DepletionForecast(
                estimated_depletion_hours=0.0,
                burn_rate_1h=burn_rate_1h,
                burn_rate_6h=burn_rate_6h,
                is_accelerating=is_accelerating,
                risk_level="critical",
                recommended_action="예산 이미 소진 - 즉시 Throttle 강화 및 on-call 알림",
            )

        # 소진 시간 예측 (1시간 Burn Rate 기준)
        if burn_rate_1h <= 1.0:
            # 정상 범위 (SLO 내 소진 속도)
            estimated_hours = None
            risk_level = "low"
            action = "정상 운영 유지"
        else:
            # 비정상 소진 속도
            # 1시간당 소진율 = (burn_rate - 1.0) * slo_error_budget
            # 예: burn_rate=14.4, slo_error_budget=0.1% → 1시간당 1.34% 소진
            hourly_depletion = (burn_rate_1h - 1.0) * self.DEFAULT_SLO_ERROR_BUDGET_PERCENT

            if hourly_depletion > 0:
                estimated_hours = remaining_percent / hourly_depletion
            else:
                estimated_hours = None

            # 위험 수준 판단
            risk_level, action = self._determine_risk_level(estimated_hours)

        return DepletionForecast(
            estimated_depletion_hours=estimated_hours,
            burn_rate_1h=burn_rate_1h,
            burn_rate_6h=burn_rate_6h,
            is_accelerating=is_accelerating,
            risk_level=risk_level,
            recommended_action=action,
        )

    def _determine_risk_level(self, estimated_hours: float | None) -> tuple[str, str]:
        """
        예상 소진 시간으로 위험 수준 판단.

        Args:
            estimated_hours: 예상 소진까지 시간

        Returns:
            (risk_level, recommended_action) 튜플
        """
        if estimated_hours is None:
            return "low", "정상 운영 유지"

        if estimated_hours < self.RISK_THRESHOLDS["critical"]:
            return "critical", "즉시 Throttle 강화 및 on-call 알림"

        if estimated_hours < self.RISK_THRESHOLDS["high"]:
            return "high", "Throttle WARNING 레벨로 사전 조정 권장"

        if estimated_hours < self.RISK_THRESHOLDS["medium"]:
            return "medium", "모니터링 강화 및 원인 분석 시작"

        return "low", "정상 운영 유지"

    def should_preemptive_throttle(self, status: ErrorBudgetStatus) -> bool:
        """
        선제적 Throttle 조정이 필요한지 판단.

        Returns:
            True if:
            - 1시간 이내 소진 예상 (CRITICAL), 또는
            - 6시간 이내 소진 예상 (HIGH) + Burn Rate 가속 중
        """
        forecast = self.forecast(status)

        if forecast.risk_level == "critical":
            return True

        if forecast.risk_level == "high" and forecast.is_accelerating:
            return True

        return False

    def get_preemptive_multiplier(self, status: ErrorBudgetStatus) -> float:
        """
        선제적 보호 시 적용할 Throttle 배율 반환.

        Args:
            status: 현재 ErrorBudgetStatus

        Returns:
            적용할 배율 (1.0 = 변경 없음, 0.5 = 50% 감소)
        """
        forecast = self.forecast(status)

        if forecast.risk_level == "critical":
            return 0.5  # 50% 감소

        if forecast.risk_level == "high":
            return 0.8  # 20% 감소

        return 1.0  # 변경 없음
