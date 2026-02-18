"""
Gradient Calculator — RTT Gradient 계산기.

AdaptiveThrottle에서 추출한 독립 모듈.
RTT 추적 및 gradient 계산을 제공하여, Deadline Context의
Dynamic Fast-Fail 판정에도 사용할 수 있습니다.

사용처:
1. AdaptiveThrottle: 원래 사용처 (limit 동적 조절)
2. TrafficGate/AdmissionControl: Dynamic Fast-Fail 예상 처리시간 제공
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class RTTSample:
    """Single RTT sample."""

    timestamp: float
    rtt_ms: float


class GradientCalculator:
    """
    RTT Gradient 계산기 — Exponential Moving Average 기반.

    사용처:
    1. AdaptiveThrottle: 원래 사용처 (limit 동적 조절)
    2. TrafficGate/AdmissionControl: Dynamic Fast-Fail 예상 처리시간 제공

    Positive gradient = RTT 증가 (과부하)
    Negative gradient = RTT 감소 (회복)
    Zero gradient = 안정
    """

    def __init__(
        self,
        smoothing_factor: float = 0.5,
        sample_window_seconds: float = 10.0,
        min_samples: int = 3,
    ):
        """
        Args:
            smoothing_factor: Weight for new samples (0-1, higher = more reactive)
            sample_window_seconds: How far back to look for samples
            min_samples: Minimum samples needed for gradient calculation
        """
        self.smoothing_factor = smoothing_factor
        self.sample_window_seconds = sample_window_seconds
        self.min_samples = min_samples

        self._samples: deque[RTTSample] = deque(maxlen=100)
        self._smoothed_rtt: float | None = None
        self._previous_smoothed_rtt: float | None = None
        self._lock = threading.Lock()

    def add_sample(self, rtt_ms: float) -> None:
        """Add a new RTT sample."""
        now = time.time()

        with self._lock:
            self._samples.append(RTTSample(timestamp=now, rtt_ms=rtt_ms))

            # Update smoothed RTT using exponential moving average
            if self._smoothed_rtt is None:
                self._smoothed_rtt = rtt_ms
            else:
                self._previous_smoothed_rtt = self._smoothed_rtt
                self._smoothed_rtt = self.smoothing_factor * rtt_ms + (1 - self.smoothing_factor) * self._smoothed_rtt

    def get_gradient(self) -> float:
        """
        Calculate current RTT gradient.

        Returns:
            Gradient value:
            - > 0: RTT increasing (should decrease limit)
            - < 0: RTT decreasing (can increase limit)
            - 0: Stable or insufficient data
        """
        with self._lock:
            if self._smoothed_rtt is None or self._previous_smoothed_rtt is None:
                return 0.0

            # Gradient = (current - previous) / previous
            if self._previous_smoothed_rtt == 0:
                return 0.0

            return (self._smoothed_rtt - self._previous_smoothed_rtt) / self._previous_smoothed_rtt

    def get_current_rtt(self) -> float | None:
        """Get current smoothed RTT."""
        with self._lock:
            return self._smoothed_rtt

    def get_snapshot(self) -> tuple[float | None, float]:
        """
        현재 RTT + gradient를 단일 Lock 내에서 반환.

        Lock을 2회 → 1회로 줄여 핫 패스 성능을 개선합니다.

        Returns:
            (current_rtt_ms, gradient) 튜플
        """
        with self._lock:
            rtt = self._smoothed_rtt
            if self._smoothed_rtt is None or self._previous_smoothed_rtt is None:
                return rtt, 0.0
            if self._previous_smoothed_rtt == 0:
                return rtt, 0.0
            grad = (self._smoothed_rtt - self._previous_smoothed_rtt) / self._previous_smoothed_rtt
            return rtt, grad

    def get_stats(self) -> dict:
        """Get calculator statistics."""
        with self._lock:
            recent_samples = [s for s in self._samples if s.timestamp > time.time() - self.sample_window_seconds]

            # Calculate gradient inline to avoid deadlock (self.get_gradient also uses self._lock)
            gradient = 0.0
            if self._smoothed_rtt is not None and self._previous_smoothed_rtt is not None:
                if self._previous_smoothed_rtt != 0:
                    gradient = (self._smoothed_rtt - self._previous_smoothed_rtt) / self._previous_smoothed_rtt

            return {
                "sample_count": len(self._samples),
                "recent_sample_count": len(recent_samples),
                "smoothed_rtt_ms": self._smoothed_rtt,
                "gradient": gradient,
            }

    def reset(self) -> None:
        """Reset calculator state."""
        with self._lock:
            self._samples.clear()
            self._smoothed_rtt = None
            self._previous_smoothed_rtt = None


# =============================================================================
# Named GradientCalculator 싱글톤 레지스트리
# =============================================================================

_calculators: dict[str, GradientCalculator] = {}
_calculators_lock = threading.Lock()


def get_gradient_calculator(
    name: str = "default",
    smoothing_factor: float = 0.5,
    sample_window_seconds: float = 10.0,
) -> GradientCalculator:
    """
    Named GradientCalculator 싱글톤 반환.

    Double-Checked Locking 패턴으로 초기 생성 시에만 Lock을 사용합니다.
    이후 읽기는 Lock-free입니다.

    Args:
        name: 계산기 이름 (서비스/엔드포인트 식별자)
        smoothing_factor: EMA 가중치
        sample_window_seconds: 샘플 윈도우

    Returns:
        GradientCalculator 인스턴스
    """
    if name not in _calculators:
        with _calculators_lock:
            if name not in _calculators:
                _calculators[name] = GradientCalculator(
                    smoothing_factor=smoothing_factor,
                    sample_window_seconds=sample_window_seconds,
                )
    return _calculators[name]


def reset_gradient_calculators() -> None:
    """테스트용 초기화. 모든 Named GradientCalculator 인스턴스를 제거합니다."""
    with _calculators_lock:
        _calculators.clear()
