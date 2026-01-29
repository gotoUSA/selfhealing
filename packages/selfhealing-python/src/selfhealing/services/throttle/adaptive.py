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


# =============================================================================
# Emergency Level → Throttle Limit 배율 매핑
# =============================================================================

# 문서 기준: NORMAL=1.0, LEVEL_1=0.8, LEVEL_2=0.5, LEVEL_3=min_limit
EMERGENCY_LEVEL_LIMIT_MULTIPLIERS: dict[int, float] = {
    0: 1.0,  # NORMAL: 전체 용량
    1: 0.8,  # LEVEL_1: 80% 용량
    2: 0.5,  # LEVEL_2: 50% 용량
    3: 0.0,  # LEVEL_3: min_limit 고정 (배율 0은 min_limit 사용 표시)
}


class AdaptiveThrottle(SlidingWindowThrottle):
    """
    Netflix Gradient-based Adaptive Throttle.

    Dynamically adjusts rate limits based on response time trends.
    Extends SlidingWindowThrottle with gradient-based limit adjustment.

    Emergency Mode 연동:
    - Emergency Level에 따라 limit 자동 조정
    - LEVEL_3에서 Gradient 계산은 유지하되 적용만 Freeze
    - Hard-Cap으로 Emergency 배율 최종 적용
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

        # =====================================================================
        # Emergency Mode 연동 상태
        # =====================================================================
        self._emergency_mode_active: bool = False
        self._emergency_level: int = 0
        self._gradient_frozen: bool = False  # LEVEL_3 시 Gradient 적용 Freeze
        self._base_limit_before_emergency: int = self.config.initial_limit
        self._emergency_tier_multipliers: dict[str, float] = {}  # 티어별 배율 캐시

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
        """Adjust limit based on gradient and SLA thresholds.

        LEVEL_3 Emergency 상태에서는 Gradient 계산은 유지하되 limit 적용만 Freeze.
        """
        now = time.time()

        with self._adjustment_lock:
            # LEVEL_3 Freeze: Gradient 계산은 유지하되 limit 적용 스킵
            if self._gradient_frozen:
                logger.debug(
                    "[AdaptiveThrottle] Gradient frozen (LEVEL_3), " "skipping limit adjustment but RTT data collected"
                )
                return

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
            "emergency": {
                "active": self._emergency_mode_active,
                "level": self._emergency_level,
                "gradient_frozen": self._gradient_frozen,
                "base_limit_before_emergency": self._base_limit_before_emergency,
                "tier_multipliers": self._emergency_tier_multipliers.copy(),
            },
        }

    # =========================================================================
    # Emergency Mode 연동 메서드
    # =========================================================================

    def adjust_for_emergency(self, level: int) -> None:
        """
        Emergency Level에 따라 limit을 자동 조정.

        배율 매핑:
        - NORMAL (0): 1.0 (전체 용량)
        - LEVEL_1 (1): 0.8 (80% 용량)
        - LEVEL_2 (2): 0.5 (50% 용량)
        - LEVEL_3 (3): min_limit 고정 + Gradient Freeze

        Args:
            level: Emergency Level (0-3)
        """
        previous_level = self._emergency_level
        self._emergency_level = level

        if level == 0:
            # NORMAL: Emergency 모드 해제
            self._emergency_mode_active = False
            self._gradient_frozen = False
            # 이전 base_limit으로 복구
            new_limit = self._base_limit_before_emergency
            logger.info(f"[AdaptiveThrottle] Emergency deactivated, " f"restoring limit to {new_limit}")
        else:
            # Emergency 활성화
            if not self._emergency_mode_active:
                # 최초 활성화 시 현재 limit 저장
                self._base_limit_before_emergency = self._current_limit
            self._emergency_mode_active = True

            if level >= 3:
                # LEVEL_3: min_limit 고정 + Gradient Freeze
                self._gradient_frozen = True
                new_limit = self.config.min_limit
                logger.warning(
                    f"[AdaptiveThrottle] Emergency LEVEL_3, " f"limit frozen to min_limit={new_limit}, Gradient frozen"
                )
            else:
                # LEVEL_1, LEVEL_2: 배율 적용
                self._gradient_frozen = False
                multiplier = EMERGENCY_LEVEL_LIMIT_MULTIPLIERS.get(level, 1.0)
                new_limit = int(self._base_limit_before_emergency * multiplier)
                logger.info(
                    f"[AdaptiveThrottle] Emergency level {previous_level} → {level}, "
                    f"limit: {self._current_limit} → {new_limit} (×{multiplier})"
                )

        self.current_limit = new_limit
        self._cache_emergency_tier_multipliers(level)

    def _cache_emergency_tier_multipliers(self, level: int) -> None:
        """
        Emergency Level에 대응하는 티어별 배율을 캐싱.

        EMERGENCY_LEVEL_RULES에서 티어별 배율을 가져와 캐시합니다.

        Args:
            level: Emergency Level (0-3)
        """
        try:
            from selfhealing.services.emergency_mode.enums import (
                EMERGENCY_LEVEL_RULES,
                EmergencyLevel,
            )

            level_enum = EmergencyLevel(level)
            rules = EMERGENCY_LEVEL_RULES.get(level_enum, EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL])
            self._emergency_tier_multipliers = rules.copy()
            logger.debug(f"[AdaptiveThrottle] Cached tier multipliers for level {level}: {rules}")
        except (ImportError, ValueError) as e:
            logger.debug(f"[AdaptiveThrottle] Could not cache tier multipliers: {e}")
            self._emergency_tier_multipliers = {}

    def _apply_emergency_cap(self, gradient_limit: int, tier_id: str = "standard") -> int:
        """
        Gradient limit에 Emergency 배율을 Hard-Cap으로 적용.

        공식: EffectiveLimit = min(gradient_limit, CB_min) × EmergencyMultiplier

        Args:
            gradient_limit: Gradient 계산으로 결정된 limit
            tier_id: 티어 ID (critical, standard, non_essential)

        Returns:
            Emergency 배율이 적용된 최종 limit
        """
        if not self._emergency_mode_active:
            return gradient_limit

        # 티어별 배율 조회 (기본값 1.0)
        multiplier = self._emergency_tier_multipliers.get(tier_id, 1.0)

        # Hard-Cap 적용
        effective_limit = int(gradient_limit * multiplier)

        # min_limit 이상 보장
        effective_limit = max(effective_limit, self.config.min_limit)

        logger.debug(
            f"[AdaptiveThrottle] Hard-Cap applied: "
            f"gradient_limit={gradient_limit}, tier={tier_id}, "
            f"multiplier={multiplier}, effective_limit={effective_limit}"
        )

        return effective_limit

    def get_effective_limit(self, tier_id: str = "standard") -> int:
        """
        티어별 실효 limit 조회.

        Emergency 모드 시 티어별 배율이 적용된 limit을 반환합니다.

        Args:
            tier_id: 티어 ID (critical, standard, non_essential)

        Returns:
            실효 limit
        """
        return self._apply_emergency_cap(self._current_limit, tier_id)

    def is_emergency_active(self) -> bool:
        """Emergency 모드 활성화 여부."""
        return self._emergency_mode_active

    def is_gradient_frozen(self) -> bool:
        """Gradient 적용이 Freeze 상태인지 여부."""
        return self._gradient_frozen

    def get_emergency_level(self) -> int:
        """현재 Emergency Level 조회."""
        return self._emergency_level

    def reset_all(self) -> None:
        """Reset all state including gradient calculator and emergency state."""
        super().reset_all()
        self._gradient_calculator.reset()
        self._adaptive_stats = {
            "adjustments_up": 0,
            "adjustments_down": 0,
            "sla_warnings": 0,
            "sla_criticals": 0,
        }
        # Emergency 상태 초기화
        self._emergency_mode_active = False
        self._emergency_level = 0
        self._gradient_frozen = False
        self._base_limit_before_emergency = self.config.initial_limit
        self._emergency_tier_multipliers = {}


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
