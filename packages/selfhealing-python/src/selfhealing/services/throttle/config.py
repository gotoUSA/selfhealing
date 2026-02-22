"""
Throttle Configuration (하위 호환 re-export).

실제 정의: selfhealing.settings.throttle

기존 ThrottleConfig(dataclass) → ThrottleSettings(BaseSettings) 으로 통합.
ThrottleConfig 이름은 하위 호환을 위해 alias 로 유지.
ThrottleResult 는 순수 데이터 객체이므로 이 파일에 그대로 유지.
"""

from __future__ import annotations

from dataclasses import dataclass

from selfhealing.settings.throttle import (  # noqa: F401
    ThrottleSettings as ThrottleConfig,
)
from selfhealing.settings.throttle import (
    get_throttle_settings,
    reset_throttle_settings,
)

__all__ = [
    "ThrottleConfig",
    "ThrottleResult",
    "get_throttle_settings",
    "reset_throttle_settings",
]


@dataclass
class ThrottleResult:
    """Result of throttle check."""

    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_at: float  # Unix timestamp
    reason: str | None = None

    # Adaptive info
    current_rtt_ms: float | None = None
    rtt_gradient: float | None = None

    def to_headers(self) -> dict:
        """Convert to rate limit response headers."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
