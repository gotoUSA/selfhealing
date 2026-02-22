"""
Multiplier Smoother.

Emergency Level 변경 시 가중치를 점진적으로 전환하여
급격한 버짓 소진율 변화로 인한 알람 폭주를 방지합니다.

Features:
- Exponential smoothing 알고리즘
- 상승/하강 양방향 평활화
- 전환 진행률 조회

Usage:
    from selfhealing.services.error_budget.smoother import (
        MultiplierSmoother,
        MultiplierSmootherConfig,
    )

    smoother = MultiplierSmoother()
    smoother.set_target(5.0)

    # 점진적으로 목표값에 접근
    while smoother.is_transitioning():
        current = smoother.get_smoothed_value()
        print(f"Current: {current}")

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.2
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


# =============================================================================
# Smoother Config
# =============================================================================


@dataclass
class MultiplierSmootherConfig:
    """
    가중치 평활화 설정.

    Attributes:
        smoothing_factor: 평활화 계수 (0.0 ~ 1.0)
        min_transition_seconds: 최소 전환 시간 (초)
        max_transition_seconds: 최대 전환 시간 (초)
        sample_interval_seconds: 샘플링 간격 (초)
        convergence_threshold: 수렴 임계값
        enabled: 평활화 활성화 여부
    """

    smoothing_factor: float = 0.3
    """
    평활화 계수 (0.0 ~ 1.0).

    낮을수록 변화가 완만함:
    - 0.1: 매우 완만 (10번 샘플링 후 90% 도달)
    - 0.3: 적당 (5번 샘플링 후 83% 도달) ← 기본값
    - 0.5: 빠름 (3번 샘플링 후 87% 도달)
    - 1.0: 즉시 (평활화 없음)
    """

    min_transition_seconds: float = 10.0
    """최소 전환 시간 (초)."""

    max_transition_seconds: float = 60.0
    """최대 전환 시간 (초)."""

    sample_interval_seconds: float = 5.0
    """샘플링 간격 (초)."""

    convergence_threshold: float = 0.01
    """수렴 임계값 (목표값과의 차이가 이 값 미만이면 도달로 간주)."""

    enabled: bool = True
    """평활화 활성화 여부."""


# =============================================================================
# Multiplier Smoother
# =============================================================================


class MultiplierSmoother:
    """
    가중치 전환 평활화기.

    Emergency Level 변경 시 가중치를 점진적으로 전환하여
    급격한 버짓 소진율 변화로 인한 알람 폭주를 방지합니다.

    Exponential smoothing 알고리즘 사용:
        smoothed = α × target + (1 - α) × current

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.2
    """

    def __init__(
        self,
        config: MultiplierSmootherConfig | None = None,
        time_func: callable | None = None,
    ):
        """
        MultiplierSmoother 초기화.

        Args:
            config: 평활화 설정
            time_func: 시간 함수 (테스트용, 기본 time.time)
        """
        self.config = config or MultiplierSmootherConfig()
        self._time_func = time_func or time.time

        self._current_value: float = 1.0
        self._target_value: float = 1.0
        self._last_sample_time: float = 0
        self._transition_start_time: float | None = None
        self._transition_start_value: float = 1.0

    def set_target(self, target_multiplier: float) -> None:
        """
        목표 가중치 설정.

        Args:
            target_multiplier: 목표 가중치
        """
        if target_multiplier != self._target_value:
            self._target_value = target_multiplier
            self._transition_start_time = self._time_func()
            self._transition_start_value = self._current_value

            logger.debug(
                "smoother.target_changed",
                self=self._current_value,
                target_multiplier=target_multiplier,
            )

    def get_smoothed_value(self) -> float:
        """
        평활화된 가중치 반환.

        Returns:
            현재 평활화된 가중치
        """
        if not self.config.enabled:
            self._current_value = self._target_value
            return self._target_value

        now = self._time_func()

        # 샘플링 간격 확인
        if now - self._last_sample_time < self.config.sample_interval_seconds:
            return self._current_value

        self._last_sample_time = now

        # 목표값에 도달했으면 조기 종료
        if self._has_converged():
            self._current_value = self._target_value
            return self._current_value

        # Exponential smoothing 적용
        alpha = self.config.smoothing_factor
        self._current_value = (
            alpha * self._target_value + (1 - alpha) * self._current_value
        )

        # 수렴 확인
        if self._has_converged():
            self._current_value = self._target_value

        return self._current_value

    def get_current_value(self) -> float:
        """
        현재 가중치 (샘플링 없이).

        Returns:
            현재 가중치 값
        """
        return self._current_value

    def get_target_value(self) -> float:
        """
        목표 가중치.

        Returns:
            목표 가중치 값
        """
        return self._target_value

    def is_transitioning(self) -> bool:
        """
        전환 중 여부.

        Returns:
            전환 중이면 True
        """
        return not self._has_converged()

    def _has_converged(self) -> bool:
        """수렴 여부 확인."""
        return (
            abs(self._current_value - self._target_value)
            < self.config.convergence_threshold
        )

    def get_transition_progress(self) -> float:
        """
        전환 진행률 (0.0 ~ 1.0).

        Returns:
            진행률 (1.0이면 완료)
        """
        if self._has_converged():
            return 1.0

        total_distance = abs(self._target_value - self._transition_start_value)
        if total_distance < self.config.convergence_threshold:
            return 1.0

        traveled_distance = abs(self._current_value - self._transition_start_value)
        return min(traveled_distance / total_distance, 1.0)

    def get_estimated_time_remaining(self) -> float:
        """
        남은 전환 시간 추정 (초).

        Returns:
            남은 시간 (초), 완료됐으면 0
        """
        if self._has_converged():
            return 0.0

        progress = self.get_transition_progress()
        if progress <= 0 or self._transition_start_time is None:
            return self.config.max_transition_seconds

        elapsed = self._time_func() - self._transition_start_time
        estimated_total = elapsed / progress
        remaining = max(0, estimated_total - elapsed)

        return min(remaining, self.config.max_transition_seconds)

    def force_converge(self) -> None:
        """강제 수렴 (현재 값을 목표 값으로 즉시 설정)."""
        self._current_value = self._target_value
        logger.debug(
            "smoother.forced_convergence",
            self=self._target_value,
        )

    def reset(self) -> None:
        """상태 초기화."""
        self._current_value = 1.0
        self._target_value = 1.0
        self._last_sample_time = 0
        self._transition_start_time = None
        self._transition_start_value = 1.0


# =============================================================================
# Singleton
# =============================================================================

_multiplier_smoother: MultiplierSmoother | None = None


def get_multiplier_smoother() -> MultiplierSmoother:
    """MultiplierSmoother 싱글톤 반환."""
    global _multiplier_smoother
    if _multiplier_smoother is None:
        _multiplier_smoother = MultiplierSmoother()
    return _multiplier_smoother


def configure_multiplier_smoother(
    config: MultiplierSmootherConfig,
) -> MultiplierSmoother:
    """MultiplierSmoother 설정 및 반환."""
    global _multiplier_smoother
    _multiplier_smoother = MultiplierSmoother(config=config)
    return _multiplier_smoother


def reset_multiplier_smoother() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _multiplier_smoother
    _multiplier_smoother = None
