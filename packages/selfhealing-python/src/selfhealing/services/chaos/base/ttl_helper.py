"""
Monotonic TTL Helper.

Provides clock-skew resistant TTL tracking using monotonic time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class MonotonicTTLHelper:
    """
    Monotonic clock 기반 TTL 헬퍼.

    ClockSkewExperiment 등 시스템 시간 조작 실험에서,
    실험 엔진 자신의 TTL 타이머가 영향받지 않도록 보호합니다.

    time.monotonic()는 시스템 시간(timezone.now())과 달리
    시스템 시간 변경에 영향받지 않는 상대 시간을 반환합니다.

    Example:
        # ClockSkewExperiment에서 사용
        helper = MonotonicTTLHelper(ttl_seconds=300)
        helper.start()

        # 실험 도중 (시스템 시간이 미래로 변경되어도)
        if helper.is_expired():
            # 실제로 300초가 경과한 경우에만 True
            experiment.rollback()

        # 남은 시간 확인
        remaining = helper.remaining_seconds()  # 실제 경과 시간 기준
    """

    ttl_seconds: float
    """TTL 시간 (초)."""

    _start_time: float = field(default=0.0, repr=False)
    """Monotonic 시작 시간."""

    _started: bool = field(default=False, repr=False)
    """시작 여부."""

    def start(self) -> None:
        """
        Monotonic 타이머 시작.

        이 메서드 호출 시점부터 TTL 카운트가 시작됩니다.
        """
        self._start_time = time.monotonic()
        self._started = True
        logger.debug(
            "monotonic_ttl.timer_started",
            ttl_seconds=self.ttl_seconds,
            start_time=self._start_time,
        )

    def is_started(self) -> bool:
        """타이머가 시작되었는지 확인."""
        return self._started

    def elapsed_seconds(self) -> float:
        """
        경과 시간 (초) 반환.

        시스템 시간과 무관하게 실제 경과 시간을 반환합니다.

        Returns:
            시작 후 경과한 시간 (초). 시작 전이면 0.0 반환.
        """
        if not self._started:
            return 0.0
        return time.monotonic() - self._start_time

    def remaining_seconds(self) -> float:
        """
        남은 시간 (초) 반환.

        Returns:
            TTL까지 남은 시간 (초). 만료되었으면 0.0 또는 음수 반환.
        """
        if not self._started:
            return self.ttl_seconds
        remaining = self.ttl_seconds - self.elapsed_seconds()
        return max(0.0, remaining)

    def is_expired(self) -> bool:
        """
        TTL 만료 여부 확인.

        시스템 시간 조작(ClockSkew)에 영향받지 않습니다.

        Returns:
            True if TTL 만료됨, False otherwise.
        """
        if not self._started:
            return False
        return self.elapsed_seconds() >= self.ttl_seconds

    def reset(self) -> None:
        """타이머 리셋 (재시작)."""
        self._start_time = time.monotonic()
        logger.debug(
            "monotonic_ttl.timer_reset",
            start_time=self._start_time,
        )

    def to_dict(self) -> dict[str, Any]:
        """직렬화용 딕셔너리 반환."""
        return {
            "ttl_seconds": self.ttl_seconds,
            "started": self._started,
            "elapsed_seconds": self.elapsed_seconds() if self._started else 0.0,
            "remaining_seconds": self.remaining_seconds(),
            "is_expired": self.is_expired(),
        }
