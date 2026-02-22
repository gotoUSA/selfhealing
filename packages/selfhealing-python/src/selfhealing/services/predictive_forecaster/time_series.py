"""
시계열 예측기: HoltLinearForecaster, EWMAForecaster, HoltWintersForecaster.

HoltLinearForecaster (이중지수평활):
    레벨(level)과 트렌드(trend)를 분리 추적하여
    5~15분 후 메트릭의 방향성을 예측한다.
    파라미터 2개(α, β), 상태 O(1), 외부 의존성 없음.

EWMAForecaster (지수가중이동평균):
    노이즈 제거 / smoothing 전처리 전용 유틸리티.
    단독 예측에는 사용하지 않음 (수평 예측만 가능).

HoltWintersForecaster (삼중지수평활):
    계절성(seasonality) 패턴 감지 및 예측.
    충분한 히스토리(2~3 시즌) 축적 후 활성화.

Usage:
    from selfhealing.services.predictive_forecaster.time_series import (
        HoltLinearForecaster,
        EWMAForecaster,
        ForecastDataPoint,
    )

    forecaster = HoltLinearForecaster(alpha=0.3, beta=0.1)
    for value in metric_values:
        forecaster.update(value)
    predicted = forecaster.predict(steps_ahead=5)
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# 데이터 모델
# =============================================================================


@dataclass
class ForecastDataPoint:
    """
    예측 히스토리의 개별 데이터포인트.

    has_adjustment 플래그는 Self-Fulfilling Prophecy 방지용 마커.
    셀프힐링 시스템이 개입(adjustment)한 직후의 메트릭은 자연적 트렌드가 아닌
    인위적 변동이므로, 추후 필터링/가중치 조정의 기반으로 사용한다.
    현재는 기록만 수행하며, 향후 가중치 차등 적용 검토 대상이다.
    """

    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    has_adjustment: bool = False


# =============================================================================
# HoltLinearForecaster (이중지수평활)
# =============================================================================


class HoltLinearForecaster:
    """
    Holt's Linear (Double Exponential Smoothing) 예측기.

    EWMAForecaster는 수평 예측만 가능한 반면, HoltLinearForecaster는
    레벨(level)과 트렌드(trend)를 분리 추적하여 방향성 있는 예측을 제공한다.

    외부 의존성 없이 표준 라이브러리만으로 구현.
    파라미터 2개(α, β), 상태 O(1), 메모리 불변.

    수식::

        level_t = α * value_t + (1 - α) * (level_{t-1} + trend_{t-1})
        trend_t = β * (level_t - level_{t-1}) + (1 - β) * trend_{t-1}
        forecast_{t+h} = level_t + h * trend_t

    BudgetDepletionForecaster (services/error_budget/forecaster.py)가
    선형 외삽을 사용하는데, HoltLinearForecaster는 이를
    지수평활로 자연 확장한 것이다.
    AdaptiveThrottle._check_preemptive_protection()
    (services/throttle/adaptive.py)의 사전조치 아키텍처와 동일한 패턴.

    Args:
        alpha: 레벨 평활 계수 (0 < α ≤ 1). 기본 0.3.
        beta: 트렌드 평활 계수 (0 < β ≤ 1). 기본 0.1.
            작을수록 트렌드 변화에 보수적 (0.01~0.05: 장기 트렌드),
            클수록 트렌드 변화에 민감 (0.1~0.3: 단기 트렌드).
        warmup_samples: 최소 데이터포인트 수 (미달 시 confidence=0).
        max_history: ring buffer 최대 크기.
    """

    STORAGE_KEY_PREFIX = "forecaster"

    def __init__(
        self,
        alpha: float = 0.3,
        beta: float = 0.1,
        warmup_samples: int = 30,
        max_history: int = 10_000,
    ):
        if not (0 < alpha <= 1):
            raise ValueError(f"alpha는 (0, 1] 범위여야 합니다: {alpha}")
        if not (0 < beta <= 1):
            raise ValueError(f"beta는 (0, 1] 범위여야 합니다: {beta}")

        self._alpha = alpha
        self._beta = beta
        self._warmup_samples = warmup_samples

        self._level: float | None = None
        self._trend: float = 0.0
        self._count: int = 0

        self._history: collections.deque[ForecastDataPoint] = collections.deque(maxlen=max_history)

        # Phase 3: has_adjustment 가중치 차등 적용 (Option A)
        # 셀프힐링 개입 직후 3~5 스텝 동안 alpha를 일시적으로 낮춤
        # 인위적 변동의 영향을 줄여 트렌드 왜곡을 방지
        self._adjustment_cooldown: int = 0
        self._adjustment_cooldown_steps: int = 3  # 개입 후 보수적 스텝 수
        self._adjustment_alpha_ratio: float = 0.5  # 쿨다운 중 alpha 감소 비율

    @property
    def is_warmed_up(self) -> bool:
        """warmup_samples 이상 데이터가 축적되었는지 여부."""
        return self._count >= self._warmup_samples

    @property
    def count(self) -> int:
        """현재까지 수집된 데이터포인트 수."""
        return self._count

    def update(self, value: float, has_adjustment: bool = False) -> float:
        """
        새 데이터포인트 추가 및 현재 레벨 반환.

        Args:
            value: 관측값.
            has_adjustment: 이 관측 직전에 셀프힐링 개입이 발생했는지 여부.
                Phase 3 (Option A): True이면 이후 3스텝 동안 alpha를 낮춰서
                인위적 변동의 영향을 줄인다.

        Returns:
            현재 smoothed level.
        """
        self._count += 1
        self._history.append(ForecastDataPoint(value=value, has_adjustment=has_adjustment))

        # Phase 3: has_adjustment → 쿨다운 시작
        if has_adjustment:
            self._adjustment_cooldown = self._adjustment_cooldown_steps

        # 쿨다운 중이면 alpha를 일시적으로 낮춤 (Option A)
        if self._adjustment_cooldown > 0:
            effective_alpha = self._alpha * self._adjustment_alpha_ratio
            self._adjustment_cooldown -= 1
        else:
            effective_alpha = self._alpha

        if self._level is None:
            self._level = value
            self._trend = 0.0
        else:
            prev_level = self._level
            self._level = effective_alpha * value + (1 - effective_alpha) * (prev_level + self._trend)
            self._trend = self._beta * (self._level - prev_level) + (1 - self._beta) * self._trend

        return self._level

    def predict(self, steps_ahead: int = 5) -> float | None:
        """
        N스텝 후 예측값 반환.

        warmup_samples 미달 시 None 반환 (Cold Start 보호).

        Args:
            steps_ahead: 예측할 미래 스텝 수.

        Returns:
            예측값 또는 None (데이터 부족 시).
        """
        if self._level is None or not self.is_warmed_up:
            return None
        return self._level + steps_ahead * self._trend

    def get_confidence(self) -> float:
        """
        현재 예측 신뢰도 (0.0 ~ 1.0).

        warmup_samples 미만 시 0.0 반환.
        이후 데이터량에 비례하여 증가, 200개 이상에서 1.0.

        DecisionEngine._calculate_confidence() (core/decision_engine.py)의
        CV 기반 stability_factor와 동일한 패턴.
        """
        if not self.is_warmed_up:
            return 0.0
        return min(1.0, self._count / 200)

    def get_trend_slope(self) -> float:
        """현재 트렌드 기울기 반환 (양수=상승, 음수=하강)."""
        return self._trend

    def get_history(self) -> list[ForecastDataPoint]:
        """전체 히스토리 반환 (복사본)."""
        return list(self._history)

    def get_values(self) -> list[float]:
        """히스토리의 값(value)만 추출하여 반환."""
        return [dp.value for dp in self._history]

    # =================================================================
    # StateBackend 영속성 (Cold Start 해결)
    # =================================================================

    def save_state(self, metric_name: str) -> bool:
        """
        현재 Forecaster 상태를 StateBackend에 저장.

        Cold Start 시 이전 학습 상태를 복원하기 위해 사용.
        ParameterBlacklist._save_to_storage() (services/learning/service.py)
        패턴과 동일.

        StateBackend ABC: core/state_backend.py
        - FileStateBackend: atomic JSON + tmp rename, 재시작 시에도 유지
        - RedisStateBackend: TTL 지원, 분산 환경 공유

        Args:
            metric_name: 메트릭 식별자 (저장 키에 사용).

        Returns:
            저장 성공 여부.
        """
        try:
            from selfhealing.core.state_backend import get_state_backend
            from selfhealing.settings.predictive_forecaster import (
                get_predictive_forecaster_settings,
            )

            settings = get_predictive_forecaster_settings()
            backend = get_state_backend()
            key = f"{self.STORAGE_KEY_PREFIX}:{metric_name}"

            state: dict[str, Any] = {
                "alpha": self._alpha,
                "beta": self._beta,
                "level": self._level,
                "trend": self._trend,
                "count": self._count,
                "warmup_samples": self._warmup_samples,
                "history": [
                    {
                        "value": dp.value,
                        "timestamp": dp.timestamp.isoformat(),
                        "has_adjustment": dp.has_adjustment,
                    }
                    for dp in self._history
                ],
            }

            backend.set(key, state, ttl_seconds=settings.state_ttl)
            logger.info(
                "holt_linear_forecaster.saved_state_points",
                metric_name=metric_name,
                _self=self._count,
                self_2=self._level,
            )
            return True
        except Exception as e:
            logger.warning(
                "holt_linear_forecaster.failed_save_state",
                error=e,
            )
            return False

    def load_state(self, metric_name: str) -> bool:
        """
        StateBackend에서 이전 상태를 복원.

        프로세스 재시작 시 Cold Start 없이 즉시 예측 가능.
        ParameterBlacklist._load_from_storage() (services/learning/service.py)
        패턴과 동일.

        Args:
            metric_name: 메트릭 식별자.

        Returns:
            복원 성공 여부.
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            key = f"{self.STORAGE_KEY_PREFIX}:{metric_name}"

            state = backend.get(key)
            if state is None:
                logger.debug(
                    "holt_linear_forecaster.no_saved_state",
                    metric_name=metric_name,
                )
                return False

            self._alpha = state["alpha"]
            self._beta = state["beta"]
            self._level = state["level"]
            self._trend = state["trend"]
            self._count = state["count"]
            self._warmup_samples = state["warmup_samples"]

            self._history.clear()
            for dp_dict in state.get("history", []):
                self._history.append(
                    ForecastDataPoint(
                        value=dp_dict["value"],
                        timestamp=datetime.fromisoformat(dp_dict["timestamp"]),
                        has_adjustment=dp_dict.get("has_adjustment", False),
                    )
                )

            logger.info(
                "holt_linear_forecaster.restored_state_points",
                metric_name=metric_name,
                _self=self._count,
                self_2=self._level,
            )
            return True
        except Exception as e:
            logger.warning(
                "holt_linear_forecaster.failed_load_state",
                error=e,
            )
            return False


# =============================================================================
# EWMAForecaster (지수가중이동평균 — smoothing 유틸리티)
# =============================================================================


class EWMAForecaster:
    """
    Exponentially Weighted Moving Average — smoothing 유틸리티.

    HoltLinearForecaster가 트렌드를 포함한 예측을 담당하며,
    EWMAForecaster는 노이즈 제거 / smoothing 전처리에 사용한다.

    용도:
    - BudgetDepletionForecaster 선형 외삽의 입력 smoothing
    - ZScoreDetector의 입력 전 노이즈 제거
    - 단독 예측에는 사용하지 않음 (수평 예측만 가능)

    Args:
        alpha: 평활 계수 (0 < α ≤ 1). 클수록 최근 데이터에 가중.
    """

    def __init__(self, alpha: float = 0.3):
        if not (0 < alpha <= 1):
            raise ValueError(f"alpha는 (0, 1] 범위여야 합니다: {alpha}")
        self._alpha = alpha
        self._ewma: float | None = None

    def update(self, value: float) -> float:
        """새 데이터포인트 추가 및 현재 EWMA 반환."""
        if self._ewma is None:
            self._ewma = value
        else:
            self._ewma = self._alpha * value + (1 - self._alpha) * self._ewma
        return self._ewma

    def get_smoothed(self) -> float | None:
        """현재 smoothed 값 반환."""
        return self._ewma

    def reset(self) -> None:
        """상태 초기화."""
        self._ewma = None


# =============================================================================
# HoltWintersForecaster (삼중지수평활 — 계절성)
# =============================================================================


class HoltWintersForecaster:
    """
    Holt-Winters 삼중지수평활 (Triple Exponential Smoothing).

    계절성(seasonality) 패턴 감지 및 예측.
    HoltLinearForecaster의 레벨+트렌드에 계절성(gamma) 파라미터를 추가.

    충분한 히스토리(2~3 시즌, 일간 패턴 기준 2~3일) 축적 후 활성화한다.

    외부 의존성 없이 표준 라이브러리만으로 구현.
    파라미터 3개(α, β, γ), 상태 O(season_length).

    수식 (additive model)::

        level_t     = α * (value_t - seasonal_{t-L}) + (1 - α) * (level_{t-1} + trend_{t-1})
        trend_t     = β * (level_t - level_{t-1}) + (1 - β) * trend_{t-1}
        seasonal_t  = γ * (value_t - level_t) + (1 - γ) * seasonal_{t-L}
        forecast_{t+h} = level_t + h * trend_t + seasonal_{t-L+((h-1) mod L)+1}

    Args:
        alpha: 레벨 평활 (0.1~0.3).
        beta: 트렌드 평활 (0.01~0.1).
        gamma: 계절성 평활 (0.1~0.3).
        season_length: 시즌 길이 (일간 패턴 = 1440 if 60초 간격).
        warmup_samples: 최소 데이터포인트 수 (최소 2 시즌).
    """

    def __init__(
        self,
        alpha: float = 0.2,
        beta: float = 0.05,
        gamma: float = 0.2,
        season_length: int = 1440,
        warmup_samples: int | None = None,
    ):
        if not (0 < alpha <= 1):
            raise ValueError(f"alpha는 (0, 1] 범위여야 합니다: {alpha}")
        if not (0 < beta <= 1):
            raise ValueError(f"beta는 (0, 1] 범위여야 합니다: {beta}")
        if not (0 < gamma <= 1):
            raise ValueError(f"gamma는 (0, 1] 범위여야 합니다: {gamma}")
        if season_length < 2:
            raise ValueError(f"season_length는 2 이상이어야 합니다: {season_length}")

        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma
        self._season_length = season_length
        self._warmup_samples = warmup_samples or (season_length * 2)

        self._level: float | None = None
        self._trend: float = 0.0
        self._seasonal: list[float] = [0.0] * season_length
        self._count: int = 0
        self._initialized: bool = False
        self._init_buffer: list[float] = []

    @property
    def is_warmed_up(self) -> bool:
        """최소 데이터 축적 여부."""
        return self._count >= self._warmup_samples

    def _initialize_components(self, values: list[float]) -> None:
        """
        첫 번째 시즌 데이터로 레벨, 트렌드, 계절성 성분 초기화.

        초기화 전략:
        - 레벨: 첫 시즌 평균
        - 트렌드: (두 번째 시즌 평균 - 첫 번째 시즌 평균) / season_length
        - 계절성: 각 시점의 값 - 첫 시즌 평균
        """
        L = self._season_length
        first_season = values[:L]
        self._level = sum(first_season) / L

        if len(values) >= 2 * L:
            second_season = values[L : 2 * L]
            second_avg = sum(second_season) / L
            self._trend = (second_avg - self._level) / L
        else:
            self._trend = 0.0

        for i in range(L):
            self._seasonal[i] = first_season[i] - self._level

        self._initialized = True

    def update(self, value: float) -> float:
        """
        새 데이터포인트 추가 및 현재 레벨 반환.

        첫 번째 시즌 동안은 초기화 버퍼에 축적하고,
        시즌 완료 후 Holt-Winters 업데이트를 시작한다.

        Args:
            value: 관측값.

        Returns:
            현재 smoothed level.
        """
        self._count += 1
        L = self._season_length

        if not self._initialized:
            self._init_buffer.append(value)
            if len(self._init_buffer) >= L:
                self._initialize_components(self._init_buffer)
            else:
                return value

        idx = (self._count - 1) % L
        prev_level = self._level or value
        prev_trend = self._trend

        self._level = self._alpha * (value - self._seasonal[idx]) + (1 - self._alpha) * (prev_level + prev_trend)
        self._trend = self._beta * (self._level - prev_level) + (1 - self._beta) * prev_trend
        self._seasonal[idx] = self._gamma * (value - self._level) + (1 - self._gamma) * self._seasonal[idx]

        return self._level

    def predict(self, steps_ahead: int = 5) -> float | None:
        """
        N스텝 후 예측값 반환 (계절성 포함).

        Args:
            steps_ahead: 예측할 미래 스텝 수.

        Returns:
            예측값 또는 None (데이터 부족 시).
        """
        if self._level is None or not self.is_warmed_up:
            return None

        L = self._season_length
        seasonal_idx = (self._count + steps_ahead - 1) % L
        return self._level + steps_ahead * self._trend + self._seasonal[seasonal_idx]

    def get_confidence(self) -> float:
        """현재 예측 신뢰도 (0.0 ~ 1.0)."""
        if not self.is_warmed_up:
            return 0.0
        full_confidence_at = self._season_length * 3
        return min(1.0, self._count / full_confidence_at)

    def get_trend_slope(self) -> float:
        """현재 트렌드 기울기 반환."""
        return self._trend

    # =========================================================================
    # Phase 4: 계절성 자동 감지 (auto-detect season_length)
    # =========================================================================

    @staticmethod
    def detect_season_length(
        values: list[float],
        min_period: int = 2,
        max_period: int | None = None,
    ) -> int | None:
        """
        자기상관(autocorrelation) 기반 계절성 주기 자동 감지.

        시계열 데이터의 자기상관 함수를 계산하여 가장 강한
        주기적 패턴의 길이를 반환한다.

        외부 의존성 없이 표준 라이브러리만으로 구현 (0 dependency 원칙).

        알고리즘:
        1. 데이터 평균 제거 (centering)
        2. 각 lag에 대해 자기상관 계수 계산
        3. 첫 번째 유의미한 피크(peak)의 lag를 season_length로 반환

        Args:
            values: 시계열 데이터 (최소 2*max_period 권장).
            min_period: 탐색할 최소 주기 (기본 2).
            max_period: 탐색할 최대 주기 (기본: len(values)//3).

        Returns:
            감지된 계절성 주기. None = 유의미한 계절성 없음.

        사용 예시::

            values = TimeSeriesScenarioGenerator.seasonal_pattern(period=24, steps=240)
            detected = HoltWintersForecaster.detect_season_length(values)
            # detected ≈ 24
        """
        n = len(values)
        if n < min_period * 4:
            return None

        if max_period is None:
            max_period = n // 3

        max_period = min(max_period, n // 2)
        if max_period < min_period:
            return None

        # 평균 제거
        mean = sum(values) / n
        centered = [v - mean for v in values]

        # 분산 (lag=0 자기상관 = 1.0의 분모)
        variance = sum(c * c for c in centered)
        if variance == 0:
            return None

        # 각 lag에 대해 자기상관 계산
        autocorrelations: list[float] = []
        for lag in range(min_period, max_period + 1):
            acf = sum(centered[i] * centered[i + lag] for i in range(n - lag))
            autocorrelations.append(acf / variance)

        if not autocorrelations:
            return None

        # 첫 번째 유의미한 피크 찾기
        # 피크: 이전 값보다 크고 다음 값보다 큰 지점
        # 유의미: 자기상관 > 0.3 (노이즈 제거)
        best_lag = None
        best_acf = 0.3  # 최소 임계값

        for i in range(1, len(autocorrelations) - 1):
            acf = autocorrelations[i]
            if acf > best_acf and acf > autocorrelations[i - 1] and acf > autocorrelations[i + 1]:
                best_acf = acf
                best_lag = min_period + i
                break  # 첫 번째 의미 있는 피크를 선택

        return best_lag
