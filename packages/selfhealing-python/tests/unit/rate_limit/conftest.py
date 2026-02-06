"""
Rate Limit 테스트 공통 fixtures.

분리된 테스트 파일들이 사용하는 공통 Mock, fixture, helper를 정의합니다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# 테스트 상수 (Config 기본값에서 파생)
# =============================================================================

# ThrottleConfig 기본값
DEFAULT_INITIAL_LIMIT = 100
DEFAULT_MIN_LIMIT = 10
DEFAULT_MAX_LIMIT = 500

# RateLimitCoordinatorConfig 기본값
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 60.0
DEFAULT_RETRY_AFTER = 5.0
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_DEBOUNCE_WINDOW = 5.0
DEFAULT_JITTER_PERCENT = 30.0

# RateLimitThrottleIntegrationSettings 기본값
REDUCTION_RATIO_1ST = 0.8  # 1회 429 → 80% 유지
REDUCTION_RATIO_2ND = 0.6  # 2회 연속 → 60% 유지
REDUCTION_RATIO_3RD = 0.5  # 3회+ 연속 → 50% 유지
SLA_WARNING_THRESHOLD = 3
DEFAULT_ESCALATION_THRESHOLD = 10


# =============================================================================
# Mock Storage
# =============================================================================


@dataclass
class MockRateLimitState:
    """Mock Rate Limit State."""

    key: str
    is_in_cooldown: bool = False
    remaining_cooldown: float = 0.0
    consecutive_429s: int = 0
    cooldown_until: float = 0.0


class MockInMemoryRateLimitStorage:
    """단위 테스트용 InMemory Rate Limit Storage Mock."""

    def __init__(self):
        self._states: dict[str, MockRateLimitState] = {}
        self._lock = threading.Lock()

    def get_state(self, key: str) -> MockRateLimitState:
        """현재 상태 조회."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)

            state = self._states[key]
            now = time.time()

            if state.cooldown_until > now:
                state.is_in_cooldown = True
                state.remaining_cooldown = state.cooldown_until - now
            else:
                state.is_in_cooldown = False
                state.remaining_cooldown = 0.0

            return state

    def set_cooldown(self, key: str, cooldown_until: float) -> None:
        """Cooldown 설정."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)
            self._states[key].cooldown_until = cooldown_until

    def increment_consecutive_429s(self, key: str) -> int:
        """연속 429 횟수 증가."""
        with self._lock:
            if key not in self._states:
                self._states[key] = MockRateLimitState(key=key)
            self._states[key].consecutive_429s += 1
            return self._states[key].consecutive_429s

    def reset_consecutive_429s(self, key: str) -> None:
        """연속 429 횟수 리셋."""
        with self._lock:
            if key in self._states:
                self._states[key].consecutive_429s = 0


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage() -> MockInMemoryRateLimitStorage:
    """Fresh MockInMemoryRateLimitStorage 인스턴스."""
    return MockInMemoryRateLimitStorage()


@pytest.fixture
def coordinator_no_jitter_no_debounce(mock_storage):
    """Jitter/Debounce 비활성화된 RateLimitCoordinator (정밀 테스트용)."""
    from selfhealing.services.rate_limit_coordinator import (
        RateLimitCoordinator,
        RateLimitCoordinatorConfig,
    )

    config = RateLimitCoordinatorConfig(
        jitter_percent=0.0,
        debounce_window_seconds=0.0,
    )
    return RateLimitCoordinator(storage=mock_storage, config=config)


@pytest.fixture
def throttle_config():
    """ThrottleConfig with well-known defaults."""
    from selfhealing.services.throttle.config import ThrottleConfig

    return ThrottleConfig(
        initial_limit=DEFAULT_INITIAL_LIMIT,
        min_limit=DEFAULT_MIN_LIMIT,
    )


@pytest.fixture
def adaptive_throttle(throttle_config):
    """Fresh AdaptiveThrottle 인스턴스 (테스트 전후 자동 리셋)."""
    from selfhealing.services.throttle.adaptive import (
        AdaptiveThrottle,
        reset_adaptive_throttle,
    )

    reset_adaptive_throttle()
    throttle = AdaptiveThrottle(config=throttle_config)
    yield throttle
    reset_adaptive_throttle()


# =============================================================================
# Helpers
# =============================================================================


def make_429_event(
    key: str = "test_api",
    consecutive_429s: int = 1,
    cooldown_seconds: float = 10.0,
) -> dict[str, Any]:
    """429 이벤트 데이터 dict 생성."""
    return {
        "key": key,
        "consecutive_429s": consecutive_429s,
        "cooldown_until": time.time() + cooldown_seconds,
    }


def make_cooldown_end_event(key: str = "test_api") -> dict[str, Any]:
    """COOLDOWN_END 이벤트 데이터 dict 생성."""
    return {
        "key": key,
        "cooldown_ended_at": time.time(),
    }


def make_mock_event_bus() -> tuple[MagicMock, list[dict]]:
    """Mock EventBus와 캡처된 이벤트 리스트를 반환.

    Returns:
        (mock_bus, emitted_events) tuple
    """
    emitted_events: list[dict] = []

    def capture_emit(event_type, data, source, priority):
        emitted_events.append(
            {
                "event_type": str(event_type),
                "data": data,
                "source": source,
            }
        )
        return 1

    mock_bus = MagicMock()
    mock_bus.emit = capture_emit
    return mock_bus, emitted_events


def compute_progressive_limits(
    initial: int = DEFAULT_INITIAL_LIMIT,
    min_limit: int = DEFAULT_MIN_LIMIT,
    ratios: tuple[float, ...] = (REDUCTION_RATIO_1ST, REDUCTION_RATIO_2ND, REDUCTION_RATIO_3RD),
) -> list[int]:
    """연속 429 시 점진적 감소 limit 시퀀스 계산.

    Returns:
        [initial, after_1st, after_2nd, after_3rd]
    """
    limits = [initial]
    current = initial
    for ratio in ratios:
        current = max(int(current * ratio), min_limit)
        limits.append(current)
    return limits
