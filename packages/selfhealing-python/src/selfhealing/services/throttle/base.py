"""
Base Throttle Implementation.

Framework-agnostic throttle logic with sliding window algorithm.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict

from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

logger = logging.getLogger(__name__)


# =============================================================================
# O(1) Bucket-based Sliding Window (100K TPS optimized)
# =============================================================================


class BucketSlidingWindow:
    """
    O(1) 고정 버킷 기반 슬라이딩 윈도우.

    시간을 bucket_size_seconds(기본 1초) 단위로 분할하여
    각 버킷에 요청 수만 저장. O(n) 리스트 순회 대신
    O(1) 인덱스 접근으로 100K TPS 환경을 지원합니다.

    기존 SlidingWindowThrottle의 리스트 기반 윈도우를 대체합니다.
    """

    __slots__ = (
        "_window_seconds",
        "_bucket_size",
        "_num_buckets",
        "_buckets",
        "_last_write_time",
        "_num_shards",
        "_shard_locks",
    )

    def __init__(
        self,
        window_seconds: int = 60,
        bucket_size_seconds: int = 1,
        num_shards: int = 64,
    ) -> None:
        self._window_seconds = window_seconds
        self._bucket_size = bucket_size_seconds
        self._num_buckets = window_seconds // bucket_size_seconds + 1
        self._buckets: dict[str, list[int]] = defaultdict(lambda: [0] * self._num_buckets)
        self._last_write_time: dict[str, int] = defaultdict(int)
        self._num_shards = num_shards
        self._shard_locks = [threading.Lock() for _ in range(num_shards)]

    def _get_shard_lock(self, key: str) -> threading.Lock:
        """키 해싱으로 shard Lock 선택 — O(1)."""
        return self._shard_locks[hash(key) % self._num_shards]

    def _clear_stale_buckets(self, key: str, now_sec: int) -> None:
        """현재 시간과 마지막 기록 시간 사이 비활성 버킷을 0으로 초기화."""
        last = self._last_write_time[key]
        if last == 0:
            return
        gap = now_sec - last
        if gap <= 0:
            return
        buckets = self._buckets[key]
        # gap이 윈도우 크기보다 크면 전체 초기화
        if gap >= self._num_buckets:
            for i in range(self._num_buckets):
                buckets[i] = 0
        else:
            for offset in range(1, gap + 1):
                idx = (last + offset) % self._num_buckets
                buckets[idx] = 0

    def record(self, key: str) -> int:
        """
        요청 1건 기록 + 현재 윈도우 내 총 요청 수 반환.

        Returns:
            현재 윈도우 내 총 요청 count
        """
        now_sec = int(time.time())
        bucket_idx = now_sec % self._num_buckets

        lock = self._get_shard_lock(key)
        with lock:
            self._clear_stale_buckets(key, now_sec)
            self._buckets[key][bucket_idx] += 1
            self._last_write_time[key] = now_sec
            total = sum(self._buckets[key])
            return total

    def get_count(self, key: str) -> int:
        """현재 윈도우 내 총 요청 수 조회 — O(window_seconds)."""
        now_sec = int(time.time())
        lock = self._get_shard_lock(key)
        with lock:
            self._clear_stale_buckets(key, now_sec)
            self._last_write_time[key] = now_sec
            return sum(self._buckets[key])

    def cleanup_stale_buckets(self, key: str) -> None:
        """
        주기적 호출로 비활성 버킷 정리.
        sample_interval (500ms) 콜백에서 호출 권장.
        """
        now_sec = int(time.time())
        lock = self._get_shard_lock(key)
        with lock:
            self._clear_stale_buckets(key, now_sec)
            self._last_write_time[key] = now_sec


# =============================================================================
# Base Throttle
# =============================================================================


class BaseThrottle(ABC):
    """Abstract base class for throttle implementations."""

    def __init__(self, config: ThrottleConfig | None = None):
        self.config = config or ThrottleConfig()
        self._current_limit = self.config.initial_limit

    @property
    def current_limit(self) -> int:
        """Get current rate limit."""
        return self._current_limit

    @current_limit.setter
    def current_limit(self, value: int):
        """Set current rate limit with bounds checking."""
        self._current_limit = max(self.config.min_limit, min(self.config.max_limit, value))

    @abstractmethod
    def check(self, key: str) -> ThrottleResult:
        """
        Check if request is allowed.

        Args:
            key: Unique identifier (user_id, ip, etc.)

        Returns:
            ThrottleResult with allowed status and metadata
        """
        pass

    @abstractmethod
    def reset(self, key: str) -> None:
        """Reset throttle state for a key."""
        pass

    def get_stats(self) -> dict:
        """Get throttle statistics."""
        return {
            "current_limit": self._current_limit,
            "min_limit": self.config.min_limit,
            "max_limit": self.config.max_limit,
        }


class SlidingWindowThrottle(BaseThrottle):
    """
    In-memory sliding window throttle.

    Thread-safe implementation for single-process use.
    For distributed systems, use Redis-based implementation.
    """

    def __init__(self, config: ThrottleConfig | None = None):
        super().__init__(config)
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

        # Statistics
        self._stats = {
            "total_requests": 0,
            "allowed_requests": 0,
            "rejected_requests": 0,
        }

    def check(self, key: str) -> ThrottleResult:
        """Check if request is allowed using sliding window."""
        now = time.time()
        window_start = now - self.config.window_seconds

        with self._lock:
            # Clean old entries
            self._windows[key] = [ts for ts in self._windows[key] if ts > window_start]

            current_count = len(self._windows[key])
            self._stats["total_requests"] += 1

            if current_count >= self._current_limit:
                self._stats["rejected_requests"] += 1
                return ThrottleResult(
                    allowed=False,
                    current_count=current_count,
                    limit=self._current_limit,
                    remaining=0,
                    reset_at=window_start + self.config.window_seconds,
                    reason="rate_limit_exceeded",
                )

            # Add current request
            self._windows[key].append(now)
            self._stats["allowed_requests"] += 1

            return ThrottleResult(
                allowed=True,
                current_count=current_count + 1,
                limit=self._current_limit,
                remaining=self._current_limit - current_count - 1,
                reset_at=window_start + self.config.window_seconds,
            )

    def reset(self, key: str) -> None:
        """Reset throttle state for a key."""
        with self._lock:
            self._windows.pop(key, None)

    def reset_all(self) -> None:
        """Reset all throttle state (for testing)."""
        with self._lock:
            self._windows.clear()
            self._stats = {
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0,
            }

    def get_stats(self) -> dict:
        """Get throttle statistics."""
        base_stats = super().get_stats()
        with self._lock:
            return {
                **base_stats,
                **self._stats,
                "active_keys": len(self._windows),
            }
