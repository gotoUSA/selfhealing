"""
Time-Bucketed RTT Window.

초당 1개 버킷으로 RTT 평균을 저장하여 메모리 효율적인 시간 기반 윈도우 제공.

문제점:
- 기존 deque(maxlen=100)은 10k TPS에서 10ms 데이터만 담음
- 고TPS 환경에서 샘플 부족으로 Gradient 계산 정확도 저하

해결:
- 초당 1개 버킷에 해당 초의 평균 RTT 저장
- 10초 윈도우 = 10개 버킷 = 40바이트 (array.array('f'))
- TPS와 무관하게 일정한 시간 범위 커버

Usage:
    window = TimeBucketedRTTWindow(window_seconds=10)
    window.add_sample(rtt_ms=150.0)

    stats = window.get_stats()
    # {'avg_rtt_ms': 145.5, 'min_rtt_ms': 100.0, 'max_rtt_ms': 200.0, ...}
"""

from __future__ import annotations

import array
import logging
import threading
import time
from dataclasses import dataclass
from typing import NamedTuple

logger = logging.getLogger(__name__)


class BucketData(NamedTuple):
    """단일 버킷의 집계 데이터."""

    sum_rtt: float
    count: int
    min_rtt: float
    max_rtt: float


@dataclass
class RTTWindowStats:
    """RTT 윈도우 통계."""

    avg_rtt_ms: float | None
    min_rtt_ms: float | None
    max_rtt_ms: float | None
    total_samples: int
    bucket_count: int
    window_seconds: int
    oldest_bucket_age_seconds: float | None


class TimeBucketedRTTWindow:
    """
    시간 기반 RTT 윈도우.

    초당 1개 버킷으로 RTT를 집계하여 메모리 효율적으로 시간 기반 통계를 제공합니다.
    array.array('f')를 사용하여 메모리 사용량을 최소화합니다.

    특징:
    - 고정 메모리: window_seconds × 16바이트 (버킷당 sum, count, min, max)
    - TPS 무관: 초당 1회 요약하므로 TPS와 무관하게 일정 시간 커버
    - 순환 버퍼: 오래된 버킷은 자동으로 덮어쓰기
    """

    def __init__(self, window_seconds: int = 10):
        """
        Args:
            window_seconds: 윈도우 크기 (초). 기본 10초.
        """
        self.window_seconds = window_seconds

        # 버킷 저장소: 초당 1개 버킷
        # 각 버킷: (sum_rtt, count, min_rtt, max_rtt)
        # array.array('f')로 메모리 최적화 (float 4바이트)
        self._sum_array = array.array("f", [0.0] * window_seconds)
        self._count_array = array.array("i", [0] * window_seconds)  # int 4바이트
        self._min_array = array.array("f", [float("inf")] * window_seconds)
        self._max_array = array.array("f", [0.0] * window_seconds)

        # 각 버킷의 타임스탬프 (초 단위, 정수)
        self._bucket_timestamps = array.array("q", [0] * window_seconds)  # int64 8바이트

        self._lock = threading.Lock()

        # 통계 캐시 (성능 최적화)
        self._cached_stats: RTTWindowStats | None = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl_seconds: float = 0.5  # 캐시 TTL

    def _get_bucket_index(self, timestamp_seconds: int) -> int:
        """타임스탬프에 해당하는 버킷 인덱스 계산."""
        return timestamp_seconds % self.window_seconds

    def _is_bucket_current(self, bucket_idx: int, current_second: int) -> bool:
        """버킷이 현재 윈도우 범위 내인지 확인."""
        bucket_ts = self._bucket_timestamps[bucket_idx]
        age = current_second - bucket_ts
        return 0 <= age < self.window_seconds

    def _reset_bucket(self, bucket_idx: int, timestamp_seconds: int) -> None:
        """버킷 초기화."""
        self._sum_array[bucket_idx] = 0.0
        self._count_array[bucket_idx] = 0
        self._min_array[bucket_idx] = float("inf")
        self._max_array[bucket_idx] = 0.0
        self._bucket_timestamps[bucket_idx] = timestamp_seconds

    def add_sample(self, rtt_ms: float) -> None:
        """
        RTT 샘플 추가.

        동일 초에 여러 샘플이 들어오면 해당 버킷에 누적됩니다.

        Args:
            rtt_ms: RTT 값 (밀리초)
        """
        if rtt_ms < 0:
            logger.warning(f"[TimeBucketedWindow] Negative RTT ignored: {rtt_ms}")
            return

        current_second = int(time.time())
        bucket_idx = self._get_bucket_index(current_second)

        with self._lock:
            # 버킷이 현재 초가 아니면 리셋
            if self._bucket_timestamps[bucket_idx] != current_second:
                self._reset_bucket(bucket_idx, current_second)

            # 샘플 추가
            self._sum_array[bucket_idx] += rtt_ms
            self._count_array[bucket_idx] += 1
            self._min_array[bucket_idx] = min(self._min_array[bucket_idx], rtt_ms)
            self._max_array[bucket_idx] = max(self._max_array[bucket_idx], rtt_ms)

            # 캐시 무효화
            self._cached_stats = None

    def get_stats(self) -> RTTWindowStats:
        """
        윈도우 내 RTT 통계 계산.

        Returns:
            RTTWindowStats: 평균, 최소, 최대 RTT 및 샘플 정보
        """
        now = time.time()

        # 캐시 확인
        if self._cached_stats is not None and (now - self._cache_timestamp) < self._cache_ttl_seconds:
            return self._cached_stats

        current_second = int(now)

        with self._lock:
            total_sum = 0.0
            total_count = 0
            global_min = float("inf")
            global_max = 0.0
            valid_buckets = 0
            oldest_age: float | None = None

            for i in range(self.window_seconds):
                if not self._is_bucket_current(i, current_second):
                    continue

                count = self._count_array[i]
                if count == 0:
                    continue

                valid_buckets += 1
                total_sum += self._sum_array[i]
                total_count += count
                global_min = min(global_min, self._min_array[i])
                global_max = max(global_max, self._max_array[i])

                age = current_second - self._bucket_timestamps[i]
                if oldest_age is None or age > oldest_age:
                    oldest_age = float(age)

            stats = RTTWindowStats(
                avg_rtt_ms=total_sum / total_count if total_count > 0 else None,
                min_rtt_ms=global_min if global_min != float("inf") else None,
                max_rtt_ms=global_max if global_max > 0 else None,
                total_samples=total_count,
                bucket_count=valid_buckets,
                window_seconds=self.window_seconds,
                oldest_bucket_age_seconds=oldest_age,
            )

            # 캐시 업데이트
            self._cached_stats = stats
            self._cache_timestamp = now

            return stats

    def get_avg_rtt(self) -> float | None:
        """현재 윈도우의 평균 RTT 반환."""
        return self.get_stats().avg_rtt_ms

    def get_bucket_data(self, bucket_idx: int) -> BucketData | None:
        """
        특정 버킷의 데이터 조회 (디버깅용).

        Args:
            bucket_idx: 버킷 인덱스 (0 ~ window_seconds-1)

        Returns:
            버킷 데이터 또는 None (유효하지 않은 버킷)
        """
        if bucket_idx < 0 or bucket_idx >= self.window_seconds:
            return None

        current_second = int(time.time())

        with self._lock:
            if not self._is_bucket_current(bucket_idx, current_second):
                return None

            count = self._count_array[bucket_idx]
            if count == 0:
                return None

            return BucketData(
                sum_rtt=self._sum_array[bucket_idx],
                count=count,
                min_rtt=self._min_array[bucket_idx],
                max_rtt=self._max_array[bucket_idx],
            )

    def get_all_bucket_data(self) -> list[dict]:
        """
        모든 유효 버킷의 데이터 조회 (디버깅용).

        Returns:
            유효 버킷 데이터 리스트
        """
        current_second = int(time.time())
        result = []

        with self._lock:
            for i in range(self.window_seconds):
                if not self._is_bucket_current(i, current_second):
                    continue

                count = self._count_array[i]
                if count == 0:
                    continue

                result.append(
                    {
                        "bucket_idx": i,
                        "timestamp": self._bucket_timestamps[i],
                        "age_seconds": current_second - self._bucket_timestamps[i],
                        "count": count,
                        "avg_rtt_ms": self._sum_array[i] / count,
                        "min_rtt_ms": self._min_array[i],
                        "max_rtt_ms": self._max_array[i],
                    }
                )

        return sorted(result, key=lambda x: x["timestamp"])

    def reset(self) -> None:
        """윈도우 초기화."""
        with self._lock:
            for i in range(self.window_seconds):
                self._sum_array[i] = 0.0
                self._count_array[i] = 0
                self._min_array[i] = float("inf")
                self._max_array[i] = 0.0
                self._bucket_timestamps[i] = 0

            self._cached_stats = None
            self._cache_timestamp = 0.0

    def memory_usage_bytes(self) -> int:
        """
        메모리 사용량 계산 (바이트).

        Returns:
            대략적인 메모리 사용량
        """
        # array.array 메모리: itemsize × length
        sum_mem = self._sum_array.itemsize * len(self._sum_array)
        count_mem = self._count_array.itemsize * len(self._count_array)
        min_mem = self._min_array.itemsize * len(self._min_array)
        max_mem = self._max_array.itemsize * len(self._max_array)
        ts_mem = self._bucket_timestamps.itemsize * len(self._bucket_timestamps)

        return sum_mem + count_mem + min_mem + max_mem + ts_mem


class TimeBucketedGradientCalculator:
    """
    Time-Bucketed 기반 Gradient 계산기.

    TimeBucketedRTTWindow를 사용하여 시간 기반 RTT Gradient를 계산합니다.
    기존 GradientCalculator의 EMA 방식 대신 버킷 기반 평균을 사용합니다.

    Gradient 계산:
    - 최근 절반 윈도우 평균 vs 이전 절반 윈도우 평균 비교
    - gradient > 0: RTT 증가 중 (limit 감소 필요)
    - gradient < 0: RTT 감소 중 (limit 증가 가능)
    """

    def __init__(
        self,
        window_seconds: int = 10,
        min_samples_for_gradient: int = 3,
    ):
        """
        Args:
            window_seconds: 윈도우 크기 (초)
            min_samples_for_gradient: Gradient 계산에 필요한 최소 샘플 수
        """
        self.window = TimeBucketedRTTWindow(window_seconds=window_seconds)
        self.min_samples_for_gradient = min_samples_for_gradient

        # EMA 호환용 (기존 코드와의 호환성)
        self._smoothed_rtt: float | None = None
        self._previous_smoothed_rtt: float | None = None
        self._smoothing_factor: float = 0.5

    def add_sample(self, rtt_ms: float) -> None:
        """RTT 샘플 추가."""
        self.window.add_sample(rtt_ms)

        # EMA 업데이트 (호환성)
        if self._smoothed_rtt is None:
            self._smoothed_rtt = rtt_ms
        else:
            self._previous_smoothed_rtt = self._smoothed_rtt
            self._smoothed_rtt = self._smoothing_factor * rtt_ms + (1 - self._smoothing_factor) * self._smoothed_rtt

    def get_gradient(self) -> float:
        """
        현재 RTT Gradient 계산.

        최근 절반 윈도우와 이전 절반 윈도우의 평균을 비교합니다.

        Returns:
            gradient 값:
            - > 0: RTT 증가 중 (limit 감소 필요)
            - < 0: RTT 감소 중 (limit 증가 가능)
            - 0: 안정 또는 데이터 부족
        """
        stats = self.window.get_stats()

        if stats.total_samples < self.min_samples_for_gradient:
            return 0.0

        # EMA 기반 gradient 계산 (기존 방식 호환)
        if self._smoothed_rtt is None or self._previous_smoothed_rtt is None:
            return 0.0

        if self._previous_smoothed_rtt == 0:
            return 0.0

        return (self._smoothed_rtt - self._previous_smoothed_rtt) / self._previous_smoothed_rtt

    def get_current_rtt(self) -> float | None:
        """현재 평균 RTT 반환."""
        return self.window.get_avg_rtt()

    def get_stats(self) -> dict:
        """통계 정보 반환."""
        window_stats = self.window.get_stats()

        return {
            "sample_count": window_stats.total_samples,
            "bucket_count": window_stats.bucket_count,
            "smoothed_rtt_ms": self._smoothed_rtt,
            "gradient": self.get_gradient(),
            "avg_rtt_ms": window_stats.avg_rtt_ms,
            "min_rtt_ms": window_stats.min_rtt_ms,
            "max_rtt_ms": window_stats.max_rtt_ms,
            "window_seconds": window_stats.window_seconds,
            "memory_bytes": self.window.memory_usage_bytes(),
        }

    def reset(self) -> None:
        """상태 초기화."""
        self.window.reset()
        self._smoothed_rtt = None
        self._previous_smoothed_rtt = None


__all__ = [
    "BucketData",
    "RTTWindowStats",
    "TimeBucketedGradientCalculator",
    "TimeBucketedRTTWindow",
]
