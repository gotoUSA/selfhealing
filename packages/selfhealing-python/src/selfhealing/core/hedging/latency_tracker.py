"""
Hedging Latency Tracker - ADAPTIVE 모드용 지연시간 추적.

과거 응답 지연시간을 슬라이딩 윈도우로 추적하고,
P50/P95 기반으로 동적 delay를 계산합니다.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

logger = logging.getLogger(__name__)


class HedgingLatencyTracker:
    """
    헷징 지연시간 추적기.

    ADAPTIVE 모드에서 과거 지연시간을 기반으로 동적 delay를 계산합니다.
    슬라이딩 윈도우(deque)로 최근 N개의 지연시간을 유지하고,
    P50 기반으로 delay를 결정합니다.

    P50 선택 이유:
        - P50(중앙값)은 이상치에 덜 민감
        - P95/P99는 너무 보수적이라 헷징 이점 감소

    Usage:
        tracker = HedgingLatencyTracker(window_size=100)

        # 지연시간 기록
        tracker.record(150.0)  # 150ms

        # ADAPTIVE delay 계산
        delay = tracker.get_p50_delay()
        if delay:
            config.delay = delay
    """

    def __init__(self, window_size: int = 100, base_delay: float = 0.1):
        """
        Args:
            window_size: 슬라이딩 윈도우 크기 (기본 100)
            base_delay: 기본 delay (초), 데이터 부족 시 사용
        """
        self._window: deque[float] = deque(maxlen=window_size)
        self._base_delay = base_delay
        self._lock = threading.Lock()
        self._min_samples = 10  # 최소 샘플 수

    def record(self, latency_ms: float) -> None:
        """
        지연시간 기록.

        Args:
            latency_ms: 응답 지연시간 (밀리초)
        """
        with self._lock:
            self._window.append(latency_ms)

    def get_p50_delay(self) -> float | None:
        """
        P50 기반 delay 반환 (초).

        Returns:
            P50 기반 delay (초) 또는 None (데이터 부족 시)
        """
        with self._lock:
            if len(self._window) < self._min_samples:
                return None

            sorted_latencies = sorted(self._window)
            p50_index = len(sorted_latencies) // 2
            p50_ms = sorted_latencies[p50_index]

            # 밀리초 → 초 변환, 최소 10ms
            return max(0.01, p50_ms / 1000.0)

    def get_p95_delay(self) -> float | None:
        """
        P95 기반 delay 반환 (초).

        Returns:
            P95 기반 delay (초) 또는 None (데이터 부족 시)
        """
        with self._lock:
            if len(self._window) < self._min_samples:
                return None

            sorted_latencies = sorted(self._window)
            p95_index = int(len(sorted_latencies) * 0.95)
            p95_ms = sorted_latencies[min(p95_index, len(sorted_latencies) - 1)]

            return max(0.01, p95_ms / 1000.0)

    def get_stats(self) -> dict:
        """
        지연시간 통계 반환.

        Returns:
            count, min_ms, max_ms, p50_ms, p95_ms를 포함한 딕셔너리
        """
        with self._lock:
            if not self._window:
                return {"count": 0}

            sorted_latencies = sorted(self._window)
            count = len(sorted_latencies)
            p95_index = min(int(count * 0.95), count - 1)

            return {
                "count": count,
                "min_ms": sorted_latencies[0],
                "max_ms": sorted_latencies[-1],
                "p50_ms": sorted_latencies[count // 2],
                "p95_ms": sorted_latencies[p95_index],
            }

    def clear(self) -> None:
        """윈도우 초기화."""
        with self._lock:
            self._window.clear()

    @property
    def sample_count(self) -> int:
        """현재 샘플 수."""
        with self._lock:
            return len(self._window)

    @property
    def has_enough_samples(self) -> bool:
        """P50 계산을 위한 충분한 샘플이 있는지 여부."""
        with self._lock:
            return len(self._window) >= self._min_samples
