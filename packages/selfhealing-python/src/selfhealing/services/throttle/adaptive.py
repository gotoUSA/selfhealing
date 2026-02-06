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
# 감사 로그 헬퍼 (Fail-Open)
# =============================================================================


def _record_audit_safe(
    action: str,
    **kwargs,
) -> None:
    """
    감사 로그 기록 (Fail-Open).

    실패해도 주요 기능에 영향 없음.
    """
    try:
        from selfhealing.services.throttle.audit import record_throttle_audit

        record_throttle_audit(action=action, **kwargs)
    except ImportError:
        logger.debug("[AdaptiveThrottle] Audit module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record audit: {e}")


def _record_limit_history(
    previous_limit: int,
    new_limit: int,
    reason: str,
    trigger_source: str | None = None,
) -> None:
    """
    Limit 변경 이력 기록 (Postmortem용).

    실패해도 주요 기능에 영향 없음.
    """
    try:
        from selfhealing.services.throttle.postmortem import get_throttle_history_collector

        collector = get_throttle_history_collector()
        collector.record_limit_change(
            previous_limit=previous_limit,
            new_limit=new_limit,
            reason=reason,
            trigger_source=trigger_source,
        )
    except ImportError:
        logger.debug("[AdaptiveThrottle] Postmortem module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record limit history: {e}")


# =============================================================================
# Prometheus 메트릭 헬퍼 (Fail-Open)
# =============================================================================


def _record_throttle_metrics(
    service: str,
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    denied_reason: str | None = None,
    emergency_level: int | None = None,
    cb_state: str | None = None,
) -> None:
    """
    Throttle 관련 Prometheus 메트릭 기록.

    메트릭:
    - selfhealing_throttle_limit: 현재 limit 값
    - selfhealing_throttle_rtt_ms: RTT 히스토그램
    - selfhealing_throttle_gradient: 현재 gradient 값
    - selfhealing_throttle_denied_total: 거부된 요청 카운터
    - selfhealing_throttle_emergency_adjustments_total: Emergency 조정 카운터
    - selfhealing_throttle_cb_adjustments_total: CB 조정 카운터
    """
    try:
        from selfhealing.services.metrics.definitions import (
            throttle_current_limit,
            throttle_rtt_ms as throttle_rtt_histogram,
            throttle_gradient as throttle_gradient_gauge,
            throttle_denied_total,
            throttle_emergency_adjustments_total,
            throttle_cb_adjustments_total,
        )

        if limit is not None:
            throttle_current_limit.labels(service=service).set(limit)

        if rtt_ms is not None:
            throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

        if gradient is not None:
            throttle_gradient_gauge.labels(service=service).set(gradient)

        if denied_reason is not None:
            throttle_denied_total.labels(service=service, reason=denied_reason).inc()

        if emergency_level is not None:
            throttle_emergency_adjustments_total.labels(level=str(emergency_level)).inc()

        if cb_state is not None:
            throttle_cb_adjustments_total.labels(service=service, cb_state=cb_state).inc()

    except ImportError:
        logger.debug("[AdaptiveThrottle] Metrics module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record metrics: {e}")


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

# 429 상황에서 보호할 티어 (CRITICAL 요청은 429 감소 전 limit 기준으로 검사)
PROTECTED_TIERS_ON_429: set[str] = {"critical"}


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
        self._full_stop_active: bool = False  # 3중 조건 충족 시 Full Stop

        # =====================================================================
        # 상태 동기화 (Check on Use 패턴)
        # =====================================================================
        self._emergency_cache_ttl_seconds: int = 30
        self._last_emergency_check_time: float = 0.0

        # =====================================================================
        # Recovery Dampening 상태
        # =====================================================================
        self._recovery_dampening_active: bool = False
        self._recovery_dampening_step: int = 0  # 0=80%, 1=90%, 2=100%
        self._recovery_dampening_last_time: float = 0.0
        self._recovery_dampening_interval_seconds: float = 30.0

        # =====================================================================
        # 429 Rate Limit 연동 상태
        # =====================================================================
        self._rate_limit_keys: dict[str, float] = {}  # key -> cooldown_until
        self._429_reduction_active: bool = False  # 429 감소 상태
        self._limit_before_429: int = self.config.initial_limit  # CRITICAL 보호용

        # Conservative Limit 상태 (Min-Winner 정책)
        self._rtt_suggested_limit: int = self.config.initial_limit
        self._429_suggested_limit: int = self.config.max_limit
        self._conservative_enabled: bool = True

        # EventBus 구독 등록
        self._subscribe_rate_limit_events()

    # =========================================================================
    # 429 Rate Limit EventBus 연동
    # =========================================================================

    def _subscribe_rate_limit_events(self) -> None:
        """Rate Limit 이벤트 구독 등록 (Fail-Open)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # 429 이벤트 구독
            bus.subscribe(EventType.RATE_LIMIT_429, self._handle_rate_limit_429)

            # Cooldown 종료 이벤트 구독
            bus.subscribe(EventType.RATE_LIMIT_COOLDOWN_END, self._handle_cooldown_end)

            logger.info("[AdaptiveThrottle] Subscribed to rate limit events")
        except ImportError:
            logger.debug("[AdaptiveThrottle] EventBus not available for subscription")
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to subscribe: {e}")

    def _handle_rate_limit_429(self, event) -> None:
        """
        429 이벤트 수신 시 limit 조정.

        전략:
        - consecutive_429s에 따른 단계별 감소
        - 1회: 20% 감소
        - 2회: 40% 감소
        - 3회 이상: 50% 감소 + SLA Warning 발행
        """
        # SelfHealingEvent에서 data 추출
        event_data = event.data if hasattr(event, "data") else event

        key = event_data.get("key", "unknown")
        consecutive = event_data.get("consecutive_429s", 1)
        cooldown_until = event_data.get("cooldown_until", 0)

        # Cooldown 상태 저장
        self._rate_limit_keys[key] = cooldown_until

        # 429 감소 전 limit 저장 (CRITICAL 보호용)
        if not self._429_reduction_active:
            self._limit_before_429 = self._current_limit

        self._429_reduction_active = True

        # 감소 비율 결정
        if consecutive >= 3:
            reduction_percent = 0.5  # 50%
        elif consecutive == 2:
            reduction_percent = 0.6  # 40%
        else:
            reduction_percent = 0.8  # 20%

        previous_limit = self._current_limit

        # 429 기반 limit 계산
        self._429_suggested_limit = max(
            int(self._current_limit * reduction_percent),
            self.config.min_limit,
        )

        # Conservative Limit 적용 (Min-Winner)
        new_limit = self.conservative_limit

        logger.warning(
            f"[AdaptiveThrottle] 429 response on '{key}', "
            f"reducing limit: {previous_limit} → {new_limit} "
            f"(consecutive={consecutive}, reduction={int((1-reduction_percent)*100)}%)"
        )

        self.current_limit = new_limit

        # Prometheus 메트릭 기록
        _record_throttle_metrics(
            service="default",
            limit=new_limit,
            denied_reason="rate_limit_429",
        )

        # SLA Warning 발행 (3회 이상)
        if consecutive >= 3:
            _emit_throttle_event(
                "THROTTLE_SLA_WARNING",
                {
                    "trigger": "rate_limit_429",
                    "key": key,
                    "consecutive_429s": consecutive,
                    "current_limit": new_limit,
                    "previous_limit": previous_limit,
                },
                priority_name="HIGH",
            )

        # Limit 변경 이벤트 발행
        _emit_throttle_event(
            "THROTTLE_LIMIT_CHANGED",
            {
                "previous_limit": previous_limit,
                "new_limit": new_limit,
                "reason": "rate_limit_429",
                "key": key,
                "consecutive_429s": consecutive,
            },
            priority_name="HIGH",
        )

    def _handle_cooldown_end(self, event) -> None:
        """
        Cooldown 종료 시 Recovery Dampening 시작.

        CRITICAL 보호 해제 및 기존 RECOVERY_DAMPENING_MULTIPLIERS 활용.
        """
        # SelfHealingEvent에서 data 추출
        event_data = event.data if hasattr(event, "data") else event

        key = event_data.get("key", "unknown")

        # 해당 Key의 429 상태 제거
        if key in self._rate_limit_keys:
            del self._rate_limit_keys[key]

        # CRITICAL 보호 해제
        self._429_reduction_active = False

        # Recovery Dampening 시작 (기존 메서드 활용)
        self.start_recovery_dampening()

        logger.info(f"[AdaptiveThrottle] Cooldown ended for '{key}', " f"starting recovery dampening (80% → 90% → 100%)")

    def is_rate_limited_for_key(self, key: str) -> bool:
        """특정 외부 API가 현재 cooldown 상태인지 확인."""
        cooldown_until = self._rate_limit_keys.get(key, 0)
        return time.time() < cooldown_until

    @property
    def conservative_limit(self) -> int:
        """
        Min-Winner 정책 적용한 보수적 limit.

        RTT 기반 limit과 429 기반 limit 중 낮은 값 반환.
        """
        if not self._conservative_enabled:
            return self._current_limit

        return min(self._rtt_suggested_limit, self._429_suggested_limit)

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

        # Prometheus 메트릭 기록: RTT, gradient, limit
        gradient = self._gradient_calculator.get_gradient()
        _record_throttle_metrics(
            service="default",
            limit=self._current_limit,
            rtt_ms=rtt_ms,
            gradient=gradient,
        )

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

    def check(self, key: str, tier_id: str = "standard") -> ThrottleResult:
        """
        Check if request is allowed with adaptive info and priority protection.

        Args:
            key: 요청 식별자
            tier_id: 요청 티어 (critical/standard/non_essential)

        Returns:
            ThrottleResult with adaptive info
        """
        # Check on Use 패턴: TTL 만료 시 Emergency 상태 동기화
        self.check_and_sync_emergency_state()

        # Recovery Dampening 진행 확인
        if self._recovery_dampening_active:
            self.advance_recovery_dampening()

        # 429 감소 상태에서 CRITICAL 티어 보호
        if self._429_reduction_active and tier_id in PROTECTED_TIERS_ON_429:
            # CRITICAL 요청은 429 감소 전 limit 기준으로 검사
            original_limit = self._current_limit
            self._current_limit = self._limit_before_429
            logger.debug(f"[AdaptiveThrottle] CRITICAL tier protected: " f"using pre-429 limit {self._limit_before_429}")
            result = super().check(key)
            self._current_limit = original_limit
        else:
            result = super().check(key)

        # Add adaptive info to result
        result.current_rtt_ms = self._gradient_calculator.get_current_rtt()
        result.rtt_gradient = self._gradient_calculator.get_gradient()

        # 거부 시 메트릭 기록
        if not result.allowed:
            _record_throttle_metrics(
                service="default",
                denied_reason=result.reason or "rate_limit_exceeded",
            )

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
                "full_stop_active": self._full_stop_active,
            },
            "recovery": {
                "dampening_active": self._recovery_dampening_active,
                "dampening_step": self._recovery_dampening_step,
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

            # Full Stop 해제 (활성화되어 있었다면)
            if self._full_stop_active:
                self.deactivate_full_stop()
                return  # Recovery Dampening이 limit 복구 처리

            # Recovery Dampening으로 점진적 복구
            self.start_recovery_dampening()
            logger.info(f"[AdaptiveThrottle] Emergency deactivated, " f"starting recovery dampening")
        else:
            # Emergency 활성화
            if not self._emergency_mode_active:
                # 최초 활성화 시 현재 limit 저장
                self._base_limit_before_emergency = self._current_limit
            self._emergency_mode_active = True

            # Recovery Dampening 중이라면 중단
            self._recovery_dampening_active = False

            if level >= 3:
                # LEVEL_3: min_limit 고정 + Gradient Freeze
                self._gradient_frozen = True
                new_limit = self.config.min_limit
                previous_limit = self._current_limit

                # Full Stop 3중 조건 확인
                is_full_stop, reason = self.check_full_stop_conditions()
                if is_full_stop:
                    self.activate_full_stop(reason)
                    return  # Full Stop이 limit을 0으로 설정

                logger.warning(
                    f"[AdaptiveThrottle] Emergency LEVEL_3, " f"limit frozen to min_limit={new_limit}, Gradient frozen"
                )
                self.current_limit = new_limit
                # Emergency 조정 메트릭 기록
                _record_throttle_metrics(
                    service="default",
                    limit=new_limit,
                    emergency_level=level,
                )
                # Emergency 조정 감사 로그 기록
                _record_audit_safe(
                    action="throttle_emergency_sync",
                    old_limit=previous_limit,
                    new_limit=new_limit,
                    emergency_level=level,
                    applied_multiplier=0.0,
                )
                # Postmortem용 이력 기록
                _record_limit_history(
                    previous_limit=previous_limit,
                    new_limit=new_limit,
                    reason=f"emergency_level_{level}",
                    trigger_source="emergency_mode",
                )
            else:
                # LEVEL_1, LEVEL_2: 배율 적용
                self._gradient_frozen = False
                multiplier = EMERGENCY_LEVEL_LIMIT_MULTIPLIERS.get(level, 1.0)
                previous_limit = self._current_limit
                new_limit = int(self._base_limit_before_emergency * multiplier)
                logger.info(
                    f"[AdaptiveThrottle] Emergency level {previous_level} → {level}, "
                    f"limit: {self._current_limit} → {new_limit} (×{multiplier})"
                )
                self.current_limit = new_limit
                # Emergency 조정 메트릭 기록
                _record_throttle_metrics(
                    service="default",
                    limit=new_limit,
                    emergency_level=level,
                )
                # Emergency 조정 감사 로그 기록
                _record_audit_safe(
                    action="throttle_emergency_sync",
                    old_limit=previous_limit,
                    new_limit=new_limit,
                    emergency_level=level,
                    applied_multiplier=multiplier,
                )
                # Postmortem용 이력 기록
                _record_limit_history(
                    previous_limit=previous_limit,
                    new_limit=new_limit,
                    reason=f"emergency_level_{level}",
                    trigger_source="emergency_mode",
                )

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
        self._full_stop_active = False
        # Recovery Dampening 초기화
        self._recovery_dampening_active = False
        self._recovery_dampening_step = 0
        self._recovery_dampening_last_time = 0.0
        # 상태 동기화 초기화
        self._last_emergency_check_time = 0.0

    # =========================================================================
    # Phase 4: Full Stop 조건 (3중 조건: LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED)
    # =========================================================================

    def check_full_stop_conditions(self) -> tuple[bool, str]:
        """
        Full Stop 3중 조건 확인.

        조건:
        1. Emergency LEVEL_3 상태
        2. 핵심 DB Circuit Breaker OPEN 상태
        3. Error Budget 소진 (0% 이하)

        Returns:
            (is_full_stop, reason): Full Stop 여부와 사유
        """
        reasons = []

        # 조건 1: Emergency LEVEL_3
        is_level_3 = self._emergency_level >= 3
        if is_level_3:
            reasons.append("EMERGENCY_LEVEL_3")

        # 조건 2: DB Circuit Breaker OPEN 확인
        db_cb_open = self._check_db_circuit_breaker_open()
        if db_cb_open:
            reasons.append("DB_CB_OPEN")

        # 조건 3: Error Budget 소진 확인
        budget_exhausted = self._check_error_budget_exhausted()
        if budget_exhausted:
            reasons.append("BUDGET_EXHAUSTED")

        # 3중 조건 모두 충족 시 Full Stop
        is_full_stop = is_level_3 and db_cb_open and budget_exhausted
        reason = " + ".join(reasons) if reasons else "NORMAL"

        return is_full_stop, reason

    def _check_db_circuit_breaker_open(self) -> bool:
        """
        핵심 DB Circuit Breaker OPEN 상태 확인.

        Returns:
            True if any DB circuit breaker is OPEN
        """
        try:
            from selfhealing.services.circuit_breaker_service import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()

            # 핵심 DB 서비스 목록 (설정 가능하도록 확장 가능)
            db_services = ["database", "db", "postgres", "mysql", "redis", "mongodb"]

            for service_name in db_services:
                try:
                    state = cb_service.get_state(service_name)
                    if state == "open":
                        logger.debug(f"[AdaptiveThrottle] DB CB OPEN detected: {service_name}")
                        return True
                except Exception:
                    # 서비스가 존재하지 않으면 스킵
                    pass

            return False
        except ImportError:
            logger.debug("[AdaptiveThrottle] CircuitBreakerService not available")
            return False
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to check DB CB: {e}")
            return False

    def _check_error_budget_exhausted(self) -> bool:
        """
        Error Budget 소진 상태 확인.

        Returns:
            True if error budget is exhausted (0% or less)
        """
        try:
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            status = service.get_budget_status()

            is_exhausted = status.budget_remaining_percent <= 0
            if is_exhausted:
                logger.debug(f"[AdaptiveThrottle] Budget exhausted: " f"{status.budget_remaining_percent:.1f}%")
            return is_exhausted
        except ImportError:
            logger.debug("[AdaptiveThrottle] ErrorBudgetService not available")
            return False
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to check error budget: {e}")
            return False

    def activate_full_stop(self, reason: str) -> None:
        """
        Full Stop 활성화: min_limit=0으로 모든 요청 차단.

        Args:
            reason: Full Stop 사유
        """
        if self._full_stop_active:
            return

        self._full_stop_active = True

        # min_limit을 0으로 설정하여 완전 차단
        previous_limit = self._current_limit
        self._current_limit = 0

        logger.critical(
            f"[AdaptiveThrottle] FULL STOP ACTIVATED: {reason}, " f"limit: {previous_limit} → 0 (all requests blocked)"
        )

        # KILL_SWITCH_ACTIVATED 이벤트 발행
        _emit_throttle_event(
            "THROTTLE_LIMIT_CHANGED",
            {
                "previous_limit": previous_limit,
                "new_limit": 0,
                "reason": f"full_stop:{reason}",
                "full_stop": True,
            },
            priority_name="CRITICAL",
        )

        # Kill Switch 이벤트도 발행하여 다른 컴포넌트에 알림
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.KILL_SWITCH_ACTIVATED,
                data={
                    "reason": f"throttle_full_stop:{reason}",
                    "activated_by": "throttle",
                    "conditions": reason,
                },
                source="throttle",
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to emit KILL_SWITCH: {e}")

    def deactivate_full_stop(self) -> None:
        """
        Full Stop 비활성화: limit 복구 시작.
        """
        if not self._full_stop_active:
            return

        self._full_stop_active = False

        # Recovery Dampening으로 복구 시작
        self.start_recovery_dampening()

        logger.warning(f"[AdaptiveThrottle] FULL STOP DEACTIVATED, " f"starting recovery dampening")

    def is_full_stop_active(self) -> bool:
        """Full Stop 활성화 여부."""
        return self._full_stop_active

    # =========================================================================
    # Phase 5: 상태 동기화 (Check on Use 패턴, TTL 캐싱, Drift 감지)
    # =========================================================================

    def sync_emergency_state_on_init(self) -> None:
        """
        Throttle 초기화 시 현재 Emergency Level 확인 및 동기화.

        애플리케이션 시작 시 또는 리셋 후 호출됩니다.
        """
        try:
            from selfhealing.services.emergency_mode.manager import (
                GracefulDegradationManager,
            )

            manager = GracefulDegradationManager()
            level = manager.get_current_level()

            if level.value > 0:
                logger.info(f"[AdaptiveThrottle] Syncing emergency state on init: " f"level={level.name}")
                self.adjust_for_emergency(level.value)

            self._last_emergency_check_time = time.time()

        except ImportError:
            logger.debug("[AdaptiveThrottle] EmergencyMode not available for sync")
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to sync emergency state: {e}")

    def check_and_sync_emergency_state(self) -> bool:
        """
        Check on Use 패턴: TTL 만료 시 Emergency 상태 재확인 및 동기화.

        Returns:
            True if state was synced (drift detected), False otherwise
        """
        now = time.time()

        # TTL 확인
        if now - self._last_emergency_check_time < self._emergency_cache_ttl_seconds:
            return False

        self._last_emergency_check_time = now

        try:
            from selfhealing.services.emergency_mode.manager import (
                GracefulDegradationManager,
            )

            manager = GracefulDegradationManager()
            current_level = manager.get_current_level().value

            # Drift 감지: 캐시된 레벨과 실제 레벨이 다른 경우
            if current_level != self._emergency_level:
                logger.warning(
                    f"[AdaptiveThrottle] Emergency state drift detected: "
                    f"cached={self._emergency_level}, actual={current_level}"
                )
                self.adjust_for_emergency(current_level)
                return True

            # Full Stop 조건 재확인
            if self._emergency_level >= 3:
                is_full_stop, reason = self.check_full_stop_conditions()
                if is_full_stop and not self._full_stop_active:
                    self.activate_full_stop(reason)
                elif not is_full_stop and self._full_stop_active:
                    self.deactivate_full_stop()

            return False

        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Emergency sync check failed: {e}")
            return False

    # =========================================================================
    # Phase 6: Recovery Dampening (80% → 90% → 100% 점진적 복구)
    # =========================================================================

    # Recovery Dampening 단계별 배율
    RECOVERY_DAMPENING_MULTIPLIERS: tuple[float, ...] = (0.8, 0.9, 1.0)

    def start_recovery_dampening(self) -> None:
        """
        Recovery Dampening 시작: 80%부터 점진적으로 복구.

        Emergency 비활성화 후 Thundering Herd 방지를 위해
        limit을 80% → 90% → 100%로 점진적으로 복구합니다.
        """
        self._recovery_dampening_active = True
        self._recovery_dampening_step = 0
        self._recovery_dampening_last_time = time.time()

        # 첫 단계: 80% 적용
        target_limit = int(self._base_limit_before_emergency * self.RECOVERY_DAMPENING_MULTIPLIERS[0])
        self.current_limit = target_limit

        logger.info(f"[AdaptiveThrottle] Recovery dampening started: " f"step=0 (80%), limit={target_limit}")

    def advance_recovery_dampening(self) -> bool:
        """
        Recovery Dampening 다음 단계로 진행.

        Returns:
            True if advanced to next step, False if already complete
        """
        if not self._recovery_dampening_active:
            return False

        now = time.time()
        elapsed = now - self._recovery_dampening_last_time

        # 인터벌 확인 (기본 30초)
        if elapsed < self._recovery_dampening_interval_seconds:
            return False

        self._recovery_dampening_step += 1
        self._recovery_dampening_last_time = now

        if self._recovery_dampening_step >= len(self.RECOVERY_DAMPENING_MULTIPLIERS):
            # 복구 완료
            self._recovery_dampening_active = False
            self._recovery_dampening_step = 0
            logger.info("[AdaptiveThrottle] Recovery dampening completed: 100%")
            return False

        # 다음 단계 적용
        multiplier = self.RECOVERY_DAMPENING_MULTIPLIERS[self._recovery_dampening_step]
        target_limit = int(self._base_limit_before_emergency * multiplier)
        self.current_limit = target_limit

        logger.info(
            f"[AdaptiveThrottle] Recovery dampening advanced: "
            f"step={self._recovery_dampening_step} ({int(multiplier * 100)}%), "
            f"limit={target_limit}"
        )

        return True

    def complete_recovery_dampening(self) -> None:
        """
        Recovery Dampening 즉시 완료: 100%로 복구.

        수동 복구 또는 테스트용.
        """
        if not self._recovery_dampening_active:
            return

        self._recovery_dampening_active = False
        self._recovery_dampening_step = 0

        # 100%로 복구
        self.current_limit = self._base_limit_before_emergency

        logger.info(
            f"[AdaptiveThrottle] Recovery dampening completed immediately: " f"limit={self._base_limit_before_emergency}"
        )

    def is_recovery_dampening_active(self) -> bool:
        """Recovery Dampening 활성화 여부."""
        return self._recovery_dampening_active

    def get_recovery_dampening_progress(self) -> dict:
        """
        Recovery Dampening 진행 상황 조회.

        Returns:
            진행 상황 정보
        """
        if not self._recovery_dampening_active:
            return {
                "active": False,
                "step": 0,
                "multiplier": 1.0,
                "percent": 100,
            }

        step = self._recovery_dampening_step
        multiplier = self.RECOVERY_DAMPENING_MULTIPLIERS[step]

        return {
            "active": True,
            "step": step,
            "multiplier": multiplier,
            "percent": int(multiplier * 100),
            "elapsed_seconds": time.time() - self._recovery_dampening_last_time,
            "interval_seconds": self._recovery_dampening_interval_seconds,
        }

    # =========================================================================
    # 롤백 시나리오 지원
    # =========================================================================

    def rollback_to_base_limit(self) -> int:
        """
        Emergency 이전 base limit으로 즉시 롤백.

        Returns:
            롤백된 limit
        """
        previous = self._current_limit

        # Emergency 상태 해제
        self._emergency_mode_active = False
        self._emergency_level = 0
        self._gradient_frozen = False
        self._full_stop_active = False
        self._recovery_dampening_active = False

        # base limit으로 복구
        self.current_limit = self._base_limit_before_emergency

        logger.warning(f"[AdaptiveThrottle] Rolled back to base limit: " f"{previous} → {self._base_limit_before_emergency}")

        return self._base_limit_before_emergency


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
