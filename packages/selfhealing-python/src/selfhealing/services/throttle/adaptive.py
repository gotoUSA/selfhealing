"""
Netflix Gradient Adaptive Throttling.

Dynamically adjusts rate limits based on response time trends.

Algorithm:
1. Sample RTT every 500ms (configurable)
2. Calculate RTT gradient (positive = slowing down, negative = speeding up)
3. Adjust limit:
   - If gradient > 0 (RTT increasing): limit = limit × 0.9
   - If gradient <= 0 (RTT stable/decreasing): limit = limit + 1

응답 시간 온도에 따른 동적 속도 제한 조절 기능을 제공합니다.

Usage:
    throttle = AdaptiveThrottle(config=ThrottleConfig(
        initial_limit=100,
        sla_warning_ms=200,
        sla_critical_ms=500,
    ))

    # On each request
    result = throttle.check("user_123")
    if result.allowed:
        response = make_request()
        throttle.record_response(response.elapsed_ms)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfhealing.services.throttle.base import SlidingWindowThrottle
from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

if TYPE_CHECKING:
    from selfhealing.services.event_bus import SelfHealingEventBus

logger = logging.getLogger(__name__)


# =============================================================================
# EventBus Integration Helper (Fail-Open)
# =============================================================================


def _get_event_bus_safe() -> "SelfHealingEventBus | None":
    """
    EventBus를 안전하게 가져옵니다. Import 실패 시 None 반환 (Fail-Open).
    """
    try:
        from selfhealing.services.event_bus import get_event_bus

        return get_event_bus()
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available, skipping event publishing")
        return None
    except Exception as e:
        logger.warning(f"[AdaptiveThrottle] Failed to get EventBus: {e}")
        return None


def _emit_throttle_event(
    event_type_name: str,
    data: dict,
    priority_name: str = "NORMAL",
) -> None:
    """
    Throttle 관련 이벤트를 EventBus에 발행합니다.

    Args:
        event_type_name: EventType 이름 (예: "THROTTLE_LIMIT_CHANGED")
        data: 이벤트 데이터
        priority_name: 우선순위 이름 (예: "HIGH", "CRITICAL")
    """
    bus = _get_event_bus_safe()
    if bus is None:
        return

    try:
        from selfhealing.services.event_bus import EventType, EventPriority

        event_type = getattr(EventType, event_type_name, None)
        if event_type is None:
            logger.warning(f"[AdaptiveThrottle] Unknown event type: {event_type_name}")
            return

        priority = getattr(EventPriority, priority_name, EventPriority.NORMAL)

        bus.emit(
            event_type=event_type,
            data=data,
            source="throttle",
            priority=priority,
        )
        logger.debug(f"[AdaptiveThrottle] Published {event_type_name} event")
    except Exception as e:
        logger.warning(f"[AdaptiveThrottle] Failed to emit {event_type_name}: {e}")


@dataclass
class RTTSample:
    """Single RTT sample."""

    timestamp: float
    rtt_ms: float


class GradientCalculator:
    """
    Calculates RTT gradient using exponential smoothing.

    Positive gradient = response times increasing (slow down)
    Negative gradient = response times decreasing (speed up)
    Zero gradient = stable
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


class AdaptiveThrottle(SlidingWindowThrottle):
    """
    Netflix Gradient-based Adaptive Throttle.

    Dynamically adjusts rate limits based on response time trends.
    Extends SlidingWindowThrottle with gradient-based limit adjustment.
    """

    def __init__(self, config: ThrottleConfig | None = None):
        super().__init__(config)

        self._gradient_calculator = GradientCalculator(
            smoothing_factor=self.config.smoothing_factor,
        )

        # Track last adjustment time
        self._last_adjustment_time: float = 0.0
        self._adjustment_lock = threading.Lock()

        # Adaptive statistics
        self._adaptive_stats = {
            "adjustments_up": 0,
            "adjustments_down": 0,
            "sla_warnings": 0,
            "sla_criticals": 0,
        }

        # Track recovery state (for THROTTLE_LIMIT_RECOVERED event)
        self._was_at_min_limit: bool = False

    def record_response(self, rtt_ms: float) -> None:
        """
        Record response time and potentially adjust limit.

        Call this after each successful request with the response time.

        Also emits THROTTLE_LIMIT_RECOVERED event when limit recovers from min_limit.

        Args:
            rtt_ms: Response time in milliseconds
        """
        previous_limit = self._current_limit
        was_at_min = self._was_at_min_limit

        self._gradient_calculator.add_sample(rtt_ms)
        self._maybe_adjust_limit(rtt_ms)

        # Check if limit was at min and has now recovered
        current_at_min = self._current_limit <= self.config.min_limit
        self._was_at_min_limit = current_at_min

        # Emit recovery event when limit increases from min_limit
        if was_at_min and self._current_limit > previous_limit:
            _emit_throttle_event(
                "THROTTLE_LIMIT_RECOVERED",
                {
                    "previous_limit": previous_limit,
                    "new_limit": self._current_limit,
                    "rtt_ms": rtt_ms,
                },
                priority_name="NORMAL",
            )

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """Adjust limit based on gradient and SLA thresholds."""
        now = time.time()

        with self._adjustment_lock:
            # Only adjust every sample_interval_ms
            interval_seconds = self.config.sample_interval_ms / 1000.0
            if now - self._last_adjustment_time < interval_seconds:
                return

            self._last_adjustment_time = now
            gradient = self._gradient_calculator.get_gradient()
            previous_limit = self._current_limit

            # SLA-based aggressive throttling
            if rtt_ms >= self.config.sla_critical_ms:
                # Critical: aggressive reduction
                self._adaptive_stats["sla_criticals"] += 1
                new_limit = int(self._current_limit * 0.7)  # -30%
                logger.warning(
                    f"[AdaptiveThrottle] CRITICAL RTT={rtt_ms:.1f}ms >= {self.config.sla_critical_ms}ms, "
                    f"limit: {self._current_limit} → {new_limit}"
                )
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_down"] += 1

                # SLA Critical 이벤트 발행
                _emit_throttle_event(
                    "THROTTLE_SLA_CRITICAL",
                    {
                        "current_rtt_ms": rtt_ms,
                        "threshold_ms": self.config.sla_critical_ms,
                        "current_limit": self._current_limit,
                        "previous_limit": previous_limit,
                        "reduction_percent": 30,
                        "gradient": gradient,
                    },
                    priority_name="CRITICAL",
                )

                # Limit 변경 이벤트 발행
                if self._current_limit != previous_limit:
                    _emit_throttle_event(
                        "THROTTLE_LIMIT_CHANGED",
                        {
                            "previous_limit": previous_limit,
                            "new_limit": self._current_limit,
                            "reason": "sla_critical",
                            "gradient": gradient,
                            "rtt_ms": rtt_ms,
                        },
                        priority_name="HIGH",
                    )
                return

            if rtt_ms >= self.config.sla_warning_ms:
                # Warning: moderate reduction
                self._adaptive_stats["sla_warnings"] += 1
                new_limit = int(self._current_limit * self.config.decrease_ratio)
                logger.info(
                    f"[AdaptiveThrottle] WARNING RTT={rtt_ms:.1f}ms >= {self.config.sla_warning_ms}ms, "
                    f"limit: {self._current_limit} → {new_limit}"
                )
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_down"] += 1

                # SLA Warning 이벤트 발행
                _emit_throttle_event(
                    "THROTTLE_SLA_WARNING",
                    {
                        "current_rtt_ms": rtt_ms,
                        "threshold_ms": self.config.sla_warning_ms,
                        "current_limit": self._current_limit,
                        "previous_limit": previous_limit,
                        "gradient": gradient,
                    },
                    priority_name="HIGH",
                )

                # Limit 변경 이벤트 발행
                if self._current_limit != previous_limit:
                    _emit_throttle_event(
                        "THROTTLE_LIMIT_CHANGED",
                        {
                            "previous_limit": previous_limit,
                            "new_limit": self._current_limit,
                            "reason": "sla_warning",
                            "gradient": gradient,
                            "rtt_ms": rtt_ms,
                        },
                        priority_name="NORMAL",
                    )
                return

            # Gradient-based adjustment
            if gradient > 0.1:  # RTT increasing more than 10%
                new_limit = int(self._current_limit * self.config.decrease_ratio)
                logger.debug(f"[AdaptiveThrottle] Gradient={gradient:.3f} > 0, " f"limit: {self._current_limit} → {new_limit}")
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_down"] += 1

                # Limit 변경 이벤트 발행 (Gradient 기반)
                if self._current_limit != previous_limit:
                    _emit_throttle_event(
                        "THROTTLE_LIMIT_CHANGED",
                        {
                            "previous_limit": previous_limit,
                            "new_limit": self._current_limit,
                            "reason": "gradient_increase",
                            "gradient": gradient,
                            "rtt_ms": rtt_ms,
                        },
                        priority_name="NORMAL",
                    )

            elif gradient < -0.05:  # RTT decreasing more than 5%
                new_limit = self._current_limit + self.config.increase_step
                logger.debug(f"[AdaptiveThrottle] Gradient={gradient:.3f} < 0, " f"limit: {self._current_limit} → {new_limit}")
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_up"] += 1

                # Limit 변경 이벤트 발행 (Gradient 기반)
                if self._current_limit != previous_limit:
                    _emit_throttle_event(
                        "THROTTLE_LIMIT_CHANGED",
                        {
                            "previous_limit": previous_limit,
                            "new_limit": self._current_limit,
                            "reason": "gradient_decrease",
                            "gradient": gradient,
                            "rtt_ms": rtt_ms,
                        },
                        priority_name="NORMAL",
                    )

    def check(self, key: str) -> ThrottleResult:
        """Check if request is allowed with adaptive info."""
        result = super().check(key)

        # Add adaptive info to result
        result.current_rtt_ms = self._gradient_calculator.get_current_rtt()
        result.rtt_gradient = self._gradient_calculator.get_gradient()

        return result

    def get_stats(self) -> dict:
        """Get adaptive throttle statistics."""
        base_stats = super().get_stats()
        gradient_stats = self._gradient_calculator.get_stats()

        return {
            **base_stats,
            "gradient": gradient_stats,
            "adaptive": self._adaptive_stats.copy(),
        }

    def reset_all(self) -> None:
        """Reset all state including gradient calculator."""
        super().reset_all()
        self._gradient_calculator.reset()
        self._adaptive_stats = {
            "adjustments_up": 0,
            "adjustments_down": 0,
            "sla_warnings": 0,
            "sla_criticals": 0,
        }


# =============================================================================
# Singleton Instance for Global Use
# =============================================================================

_global_adaptive_throttle: AdaptiveThrottle | None = None
_throttle_lock = threading.Lock()


def get_adaptive_throttle(config: ThrottleConfig | None = None) -> AdaptiveThrottle:
    """
    Get global adaptive throttle instance.

    Thread-safe singleton pattern.
    """
    global _global_adaptive_throttle

    with _throttle_lock:
        if _global_adaptive_throttle is None:
            _global_adaptive_throttle = AdaptiveThrottle(config)
        return _global_adaptive_throttle


def reset_adaptive_throttle() -> None:
    """Reset global adaptive throttle (for testing)."""
    global _global_adaptive_throttle

    with _throttle_lock:
        if _global_adaptive_throttle is not None:
            _global_adaptive_throttle.reset_all()
        _global_adaptive_throttle = None
