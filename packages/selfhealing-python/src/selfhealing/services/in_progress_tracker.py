"""
In-Progress Operation Tracker.

설정 적용 시 진행 중인 작업을 추적하여,
그레이스풀 설정 변경이 안전하게 적용될 수 있도록 합니다.

Usage:
    tracker = get_in_progress_tracker()
    count = tracker.count_in_progress("runtime_config")
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

import structlog

logger = structlog.get_logger()


class InProgressTracker:
    """
    진행 중인 작업 추적기.

    config_type별로 현재 진행 중인 작업 수를 추적합니다.
    ConfigApplyService에서 그레이스풀 설정 변경 시 사용됩니다.
    """

    _instance: InProgressTracker | None = None

    def __new__(cls) -> InProgressTracker:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)

    def count_in_progress(self, config_type: str) -> int:
        """
        특정 config_type의 진행 중인 작업 수를 반환합니다.

        Args:
            config_type: 설정 유형 (e.g., "runtime_config", "feature_flag")

        Returns:
            진행 중인 작업 수
        """
        with self._lock:
            return self._counters.get(config_type, 0)

    def increment(self, config_type: str) -> int:
        """진행 중인 작업 수를 1 증가시킵니다."""
        with self._lock:
            self._counters[config_type] += 1
            return self._counters[config_type]

    def decrement(self, config_type: str) -> int:
        """진행 중인 작업 수를 1 감소시킵니다."""
        with self._lock:
            self._counters[config_type] = max(0, self._counters[config_type] - 1)
            return self._counters[config_type]

    def reset(self, config_type: str | None = None) -> None:
        """카운터를 리셋합니다."""
        with self._lock:
            if config_type is None:
                self._counters.clear()
            else:
                self._counters.pop(config_type, None)


# =============================================================================
# Factory
# =============================================================================

_instance: InProgressTracker | None = None


def get_in_progress_tracker() -> InProgressTracker:
    """InProgressTracker 싱글톤 인스턴스를 반환합니다."""
    global _instance
    if _instance is None:
        _instance = InProgressTracker()
    return _instance


__all__ = [
    "InProgressTracker",
    "get_in_progress_tracker",
]
