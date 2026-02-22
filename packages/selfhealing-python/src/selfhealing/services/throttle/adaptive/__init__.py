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

import threading
import time
from typing import TYPE_CHECKING

import structlog

from selfhealing.services.governance.checks import GovernanceCheckMixin
from selfhealing.services.throttle.adaptive_dlq_replay import ThrottleDLQReplayMixin
from selfhealing.services.throttle.base import SlidingWindowThrottle
from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

if TYPE_CHECKING:
    from selfhealing.services.event_bus import SelfHealingEventBus

logger = structlog.get_logger()


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
        logger.debug("adaptive_throttle.audit_module_available")
    except Exception as e:
        logger.debug(
            "adaptive_throttle.failed_record_audit",
            error=e,
        )


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
        from selfhealing.services.throttle.postmortem import (
            get_throttle_history_collector,
        )

        collector = get_throttle_history_collector()
        collector.record_limit_change(
            previous_limit=previous_limit,
            new_limit=new_limit,
            reason=reason,
            trigger_source=trigger_source,
        )
    except ImportError:
        logger.debug("adaptive_throttle.postmortem_module_available")
    except Exception as e:
        logger.debug(
            "adaptive_throttle.failed_record_limit_history",
            error=e,
        )


# =============================================================================
# Prometheus 메트릭 — Top-Level Import + 확장 기록 함수
# =============================================================================

_METRICS_AVAILABLE = False
_throttle_current_limit = None
_throttle_rtt_histogram = None
_throttle_gradient_gauge = None
_throttle_denied_total = None
_throttle_emergency_adjustments_total = None
_throttle_cb_adjustments_total = None
# 확장 메트릭
_throttle_requests_total = None
_throttle_allowed_total = None
_throttle_sla_warnings_total = None
_throttle_sla_criticals_total = None
_throttle_emergency_level_gauge = None
_throttle_gradient_frozen_gauge = None
_recovery_active_gauge = None
_recovery_step_gauge = None
_full_stop_gauge = None
_throttle_full_stop_activations_total = None
_throttle_limit_changes_total = None
_throttle_limit_change_magnitude = None
_throttle_saturation_ratio = None
_throttle_max_limit_gauge = None
# Error Budget 연동 메트릭
_throttle_error_budget_adjustments_total = None
_throttle_error_budget_multiplier_gauge = None
_throttle_error_budget_reduction_active_gauge = None
_throttle_error_budget_preemptive_total = None

try:
    from selfhealing.services.metrics.definitions import (
        throttle_allowed_total as _throttle_allowed_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_cb_adjustments_total as _throttle_cb_adjustments_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_current_limit as _throttle_current_limit,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_denied_total as _throttle_denied_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_emergency_adjustments_total as _throttle_emergency_adjustments_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_emergency_level as _throttle_emergency_level_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_error_budget_adjustments_total as _throttle_error_budget_adjustments_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_error_budget_multiplier as _throttle_error_budget_multiplier_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_error_budget_preemptive_total as _throttle_error_budget_preemptive_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_error_budget_reduction_active as _throttle_error_budget_reduction_active_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_full_stop_activations_total as _throttle_full_stop_activations_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_full_stop_active as _full_stop_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_gradient as _throttle_gradient_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_gradient_frozen as _throttle_gradient_frozen_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_limit_change_magnitude as _throttle_limit_change_magnitude,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_limit_changes_total as _throttle_limit_changes_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_max_limit as _throttle_max_limit_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_recovery_dampening_active as _recovery_active_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_recovery_dampening_step as _recovery_step_gauge,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_requests_total as _throttle_requests_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_rtt_ms as _throttle_rtt_histogram,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_saturation_ratio as _throttle_saturation_ratio,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_sla_criticals_total as _throttle_sla_criticals_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_sla_warnings_total as _throttle_sla_warnings_total,
    )

    _METRICS_AVAILABLE = True
except ImportError:
    pass


def _record_core_metrics(
    service: str,
    limit: int | None,
    rtt_ms: float | None,
    gradient: float | None,
    exemplar: dict | None,
) -> None:
    """Core 메트릭 기록 (limit, rtt, gradient)."""
    if limit is not None:
        _throttle_current_limit.labels(service=service).set(limit)

    if rtt_ms is not None:
        try:
            _throttle_rtt_histogram.labels(service=service).observe(rtt_ms, exemplar=exemplar)
        except TypeError:
            _throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

    if gradient is not None:
        _throttle_gradient_gauge.labels(service=service).set(gradient)


def _record_request_metrics(
    service: str,
    request_result: str | None,
    denied_reason: str | None,
    exemplar: dict | None,
) -> None:
    """Request 허용/거부 메트릭 기록."""
    if request_result is None:
        return

    _throttle_requests_total.labels(service=service, result=request_result).inc()
    if request_result == "allowed":
        try:
            _throttle_allowed_total.labels(service=service).inc(exemplar=exemplar)
        except TypeError:
            _throttle_allowed_total.labels(service=service).inc()
    elif request_result == "denied" and denied_reason:
        _throttle_denied_total.labels(service=service, reason=denied_reason).inc()


def _record_sla_metrics(service: str, sla_event: str | None) -> None:
    """SLA 경고/위험 메트릭 기록."""
    if sla_event == "warning":
        _throttle_sla_warnings_total.labels(service=service).inc()
    elif sla_event == "critical":
        _throttle_sla_criticals_total.labels(service=service).inc()


def _record_emergency_cb_metrics(
    service: str,
    emergency_level: int | None,
    gradient_frozen: bool | None,
    cb_state: str | None,
) -> None:
    """Emergency / Circuit Breaker 메트릭 기록."""
    if emergency_level is not None:
        _throttle_emergency_level_gauge.labels(service=service).set(emergency_level)
        _throttle_emergency_adjustments_total.labels(level=str(emergency_level)).inc()

    if gradient_frozen is not None:
        _throttle_gradient_frozen_gauge.labels(service=service).set(1 if gradient_frozen else 0)

    if cb_state is not None:
        _throttle_cb_adjustments_total.labels(service=service, cb_state=cb_state).inc()


def _record_recovery_full_stop_metrics(
    service: str,
    recovery_dampening_active: bool | None,
    recovery_dampening_step: int | None,
    full_stop_active: bool | None,
    full_stop_reason: str | None,
) -> None:
    """Recovery 및 Full Stop 메트릭 기록."""
    if recovery_dampening_active is not None:
        _recovery_active_gauge.labels(service=service).set(1 if recovery_dampening_active else 0)

    if recovery_dampening_step is not None:
        _recovery_step_gauge.labels(service=service).set(recovery_dampening_step)

    if full_stop_active is not None:
        _full_stop_gauge.labels(service=service).set(1 if full_stop_active else 0)

    if full_stop_reason is not None:
        _throttle_full_stop_activations_total.labels(service=service, reason=full_stop_reason).inc()


def _record_limit_change_metrics(
    service: str,
    limit_change_direction: str | None,
    limit_change_trigger: str | None,
    limit_change_percent: float | None,
) -> None:
    """Limit 변경 메트릭 기록."""
    if not (limit_change_direction and limit_change_trigger):
        return

    _throttle_limit_changes_total.labels(
        service=service,
        direction=limit_change_direction,
        trigger=limit_change_trigger,
    ).inc()

    if limit_change_percent is not None:
        _throttle_limit_change_magnitude.labels(
            service=service,
            direction=limit_change_direction,
        ).observe(abs(limit_change_percent))


def _record_saturation_metrics(service: str, limit: int | None, max_limit: int | None) -> None:
    """Saturation 비율 메트릭 기록."""
    if limit is not None and max_limit is not None and max_limit > 0:
        saturation = limit / max_limit
        _throttle_saturation_ratio.labels(service=service).set(saturation)
        _throttle_max_limit_gauge.labels(service=service).set(max_limit)


def _record_error_budget_metrics(
    service: str,
    error_budget_status: str | None,
    error_budget_multiplier: float | None,
    error_budget_reduction_active: bool | None,
    error_budget_preemptive_risk_level: str | None,
) -> None:
    """Error Budget 연동 메트릭 기록."""
    if error_budget_status is not None and _throttle_error_budget_adjustments_total:
        _throttle_error_budget_adjustments_total.labels(
            service=service,
            budget_status=error_budget_status,
        ).inc()

    if error_budget_multiplier is not None and _throttle_error_budget_multiplier_gauge:
        _throttle_error_budget_multiplier_gauge.labels(service=service).set(error_budget_multiplier)

    if error_budget_reduction_active is not None and _throttle_error_budget_reduction_active_gauge:
        _throttle_error_budget_reduction_active_gauge.labels(service=service).set(1 if error_budget_reduction_active else 0)

    if error_budget_preemptive_risk_level is not None and _throttle_error_budget_preemptive_total:
        _throttle_error_budget_preemptive_total.labels(
            service=service,
            risk_level=error_budget_preemptive_risk_level,
        ).inc()


def _record_throttle_metrics(
    service: str,
    # 기존 파라미터 (하위호환)
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    denied_reason: str | None = None,
    emergency_level: int | None = None,
    cb_state: str | None = None,
    # 확장 파라미터
    request_result: str | None = None,
    sla_event: str | None = None,
    gradient_frozen: bool | None = None,
    recovery_dampening_active: bool | None = None,
    recovery_dampening_step: int | None = None,
    full_stop_active: bool | None = None,
    full_stop_reason: str | None = None,
    limit_change_direction: str | None = None,
    limit_change_trigger: str | None = None,
    limit_change_percent: float | None = None,
    max_limit: int | None = None,
    trace_id: str | None = None,
    # Error Budget 연동 파라미터
    error_budget_status: str | None = None,
    error_budget_multiplier: float | None = None,
    error_budget_reduction_active: bool | None = None,
    error_budget_preemptive_risk_level: str | None = None,
) -> None:
    """
    Throttle Prometheus 메트릭 기록 (확장 버전).

    기존 시그니처를 확장하여 하위호환 유지.
    Exemplar는 Fail-Open: 첨부 실패 시 exemplar 없이 기록 계속.
    """
    if not _METRICS_AVAILABLE:
        return

    try:
        exemplar = {"trace_id": trace_id} if trace_id else None

        _record_core_metrics(service, limit, rtt_ms, gradient, exemplar)
        _record_request_metrics(service, request_result, denied_reason, exemplar)
        _record_sla_metrics(service, sla_event)
        _record_emergency_cb_metrics(service, emergency_level, gradient_frozen, cb_state)
        _record_recovery_full_stop_metrics(
            service,
            recovery_dampening_active,
            recovery_dampening_step,
            full_stop_active,
            full_stop_reason,
        )
        _record_limit_change_metrics(
            service,
            limit_change_direction,
            limit_change_trigger,
            limit_change_percent,
        )
        _record_saturation_metrics(service, limit, max_limit)
        _record_error_budget_metrics(
            service,
            error_budget_status,
            error_budget_multiplier,
            error_budget_reduction_active,
            error_budget_preemptive_risk_level,
        )

    except Exception as e:
        logger.debug(
            "adaptive_throttle.metrics_failed",
            error=e,
        )


# =============================================================================
# Trace ID Helper (Fail-Open)
# =============================================================================


def _get_trace_id_safe() -> str | None:
    """현재 Trace ID를 안전하게 가져옵니다. 실패 시 None 반환."""
    try:
        from selfhealing.observability import get_current_trace_id_from_otel

        return get_current_trace_id_from_otel()
    except Exception:
        return None


# =============================================================================
# EventBus Integration Helper (Fail-Open)
# =============================================================================


def _get_event_bus_safe() -> SelfHealingEventBus | None:
    """
    EventBus를 안전하게 가져옵니다. Import 실패 시 None 반환 (Fail-Open).
    """
    try:
        from selfhealing.services.event_bus import get_event_bus

        return get_event_bus()
    except ImportError:
        logger.debug("adaptive_throttle.eventbus_available_skipping_event")
        return None
    except Exception as e:
        logger.warning(
            "adaptive_throttle.failed_get_eventbus",
            error=e,
        )
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
        from selfhealing.services.event_bus import EventPriority, EventType

        event_type = getattr(EventType, event_type_name, None)
        if event_type is None:
            logger.warning(
                "adaptive_throttle.unknown_event_type",
                event_type_name=event_type_name,
            )
            return

        priority = getattr(EventPriority, priority_name, EventPriority.NORMAL)

        bus.emit(
            event_type=event_type,
            data=data,
            source="throttle",
            priority=priority,
        )
        logger.debug(
            "adaptive_throttle.event_published",
            event_type_name=event_type_name,
        )
    except Exception as e:
        logger.warning(
            "adaptive_throttle.failed_emit",
            event_type_name=event_type_name,
            error=e,
        )


# GradientCalculator, RTTSample은 gradient.py로 추출됨.
# 하위 호환을 위해 re-export 유지.
from selfhealing.services.throttle.gradient import (  # noqa: F401
    GradientCalculator,
    RTTSample,
)

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




from selfhealing.services.throttle.adaptive._emergency import EmergencyModeMixin
from selfhealing.services.throttle.adaptive._error_budget import ErrorBudgetHandlerMixin
from selfhealing.services.throttle.adaptive._full_stop import FullStopMixin
from selfhealing.services.throttle.adaptive._governance import GovernanceEventMixin
from selfhealing.services.throttle.adaptive._rate_limit import RateLimitHandlerMixin
from selfhealing.services.throttle.adaptive._recovery import RecoveryDampeningMixin


class AdaptiveThrottle(
    RateLimitHandlerMixin,
    GovernanceEventMixin,
    ErrorBudgetHandlerMixin,
    EmergencyModeMixin,
    FullStopMixin,
    RecoveryDampeningMixin,
    GovernanceCheckMixin,
    ThrottleDLQReplayMixin,
    SlidingWindowThrottle,
):
    """
    Netflix Gradient-based Adaptive Throttle.

    Dynamically adjusts rate limits based on response time trends.
    Extends SlidingWindowThrottle with gradient-based limit adjustment.
    Includes DLQ integration for throttle rejection storage and recovery replay.

    Emergency Mode 연동:
    - Emergency Level에 따라 limit 자동 조정
    - LEVEL_3에서 Gradient 계산은 유지하되 적용만 Freeze
    - Hard-Cap으로 Emergency 배율 최종 적용

    Governance 연동:
    - GovernanceCheckMixin 상속으로 Kill Switch / Emergency / Error Budget / Break Glass 체크
    - Kill Switch EventBus 구독으로 gradient freeze 즉시 반영
    - Break Glass 활성화 시 Full Stop 해제 후 Recovery Dampening 시작
    - _maybe_adjust_limit() Control Plane에서 is_automation_allowed() Safety Net 체크
    """

    # GovernanceCheckMixin 설정
    _governance_service_name: str | None = "adaptive_throttle"
    _governance_domain: str | None = "throttle"


    def __init__(self, config: ThrottleConfig | None = None):
        super().__init__(config)

        self._gradient_calculator = GradientCalculator(
            smoothing_factor=self.config.smoothing_factor,
        )

        # Prometheus 라벨 동적화 — sanitize_label_value()로 안전한 값 보장
        from selfhealing.services.metrics.registry import sanitize_label_value

        self._service_name: str = sanitize_label_value(self.config.service_name)

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

        # =====================================================================
        # Load Shedding 연동 상태
        # =====================================================================
        self._shedding_suggested_limit: int = self.config.max_limit
        self._shedding_affected_services: set[str] = set()

        # =====================================================================
        # Error Budget 연동 상태
        # =====================================================================
        self._error_budget_limit_reduction_active: bool = False
        self._error_budget_multiplier: float = 1.0
        self._limit_before_error_budget_reduction: int = self.config.initial_limit

        # SLO 필터링 설정 (기본: 전역 예산 반응)
        self._target_slo_patterns: list[str] = ["availability"]

        # Recovery Jitter 설정 (Thundering Herd 방지)
        self._recovery_jitter_max_seconds: int = 10

        # =====================================================================
        # Governance 연동 상태
        # =====================================================================
        self._kill_switch_active: bool = False  # Kill Switch → Gradient Freeze
        self._break_glass_active: bool = False  # Break Glass → Full Stop 해제

        # EventBus 구독 등록
        self._subscribe_rate_limit_events()
        self._subscribe_error_budget_events()
        self._subscribe_load_shedding_events()
        self._subscribe_kill_switch_events()

        # DLQ Replay 연동 초기화 (Fail-Open)
        try:
            self._init_dlq_replay_integration()
        except Exception:
            logger.debug("adaptive_throttle.dlq_replay_integration_init")

    @property
    def conservative_limit(self) -> int:
        """
        Min-Winner 정책 적용한 보수적 limit.

        RTT 기반 limit, 429 기반 limit, Error Budget 기반 limit 중
        가장 낮은 값 반환.
        """
        if not self._conservative_enabled:
            return self._current_limit

        # Error Budget 기반 limit 계산
        error_budget_limit = self.config.max_limit
        if self._error_budget_limit_reduction_active:
            error_budget_limit = int(self._limit_before_error_budget_reduction * self._error_budget_multiplier)

        return min(
            self._rtt_suggested_limit,
            self._429_suggested_limit,
            error_budget_limit,
            self._shedding_suggested_limit,
        )

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

        # Lock 통합: get_snapshot() 단일 호출로 RTT + gradient 취득
        current_rtt, gradient = self._gradient_calculator.get_snapshot()

        # Exemplar 취득 (Fail-Open)
        trace_id = None
        try:
            from selfhealing.observability import get_current_trace_id_from_otel

            trace_id = get_current_trace_id_from_otel()
        except Exception:
            pass

        # 확장 메트릭 기록 — 동적 라벨 + exemplar + saturation
        _record_throttle_metrics(
            service=self._service_name,
            limit=self._current_limit,
            rtt_ms=rtt_ms,
            gradient=gradient,
            emergency_level=self._emergency_level,
            gradient_frozen=self._gradient_frozen,
            recovery_dampening_active=self._recovery_dampening_active,
            recovery_dampening_step=(self._recovery_dampening_step if self._recovery_dampening_active else None),
            full_stop_active=self._full_stop_active,
            max_limit=self.config.max_limit,
            trace_id=trace_id,
        )

        # Limit 변경 메트릭
        if self._current_limit != previous_limit:
            direction = "up" if self._current_limit > previous_limit else "down"
            change_percent = abs(self._current_limit - previous_limit) / max(previous_limit, 1) * 100
            _record_throttle_metrics(
                service=self._service_name,
                limit_change_direction=direction,
                limit_change_trigger="gradient",
                limit_change_percent=change_percent,
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

        LEVEL_3 Emergency 또는 Kill Switch 상태에서는 Gradient 계산은 유지하되 limit 적용만 Freeze.
        Governance Safety Net으로 EventBus 이벤트 유실 시 30초 내 Drift 교정.
        """
        now = time.time()

        with self._adjustment_lock:
            # 로컬 플래그 조기 반환 (LEVEL_3 또는 Kill Switch에 의해 즉시 설정됨)
            if self._gradient_frozen:
                logger.debug("adaptive_throttle.gradient_frozen_skipping_limit")
                return

            # Governance Safety Net: EventBus 이벤트 유실 대비 Drift 교정 (30초 TTL 캐시)
            try:
                if not self.is_automation_allowed(
                    operation_name="adaptive_throttle:limit_adjustment",
                ):
                    self._gradient_frozen = True
                    logger.warning("adaptive_throttle.governance_blocked")
                    return
            except Exception:
                # Fail-Open: Governance 체크 실패 시 기존 동작 유지
                pass

            # Break Glass 상태 동기화 (Settings 기반, 주기적 polling)
            self._sync_break_glass_state()

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
                    "adaptive_throttle.critical_ms_ms_limit",
                    rtt_ms=rtt_ms,
                    _self=self.config.sla_critical_ms,
                    self_2=self._current_limit,
                    new_limit=new_limit,
                )
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_down"] += 1

                # SLA Critical 이벤트 발행
                rtt_change_percent = gradient * 100.0
                _emit_throttle_event(
                    "THROTTLE_SLA_CRITICAL",
                    {
                        "current_rtt_ms": rtt_ms,
                        "threshold_ms": self.config.sla_critical_ms,
                        "current_limit": self._current_limit,
                        "previous_limit": previous_limit,
                        "reduction_percent": 30,
                        "gradient": gradient,
                        "rtt_change_percent": rtt_change_percent,
                        "service_name": "default",
                    },
                    priority_name="CRITICAL",
                )

                # SLA Critical Prometheus 메트릭 기록
                _record_throttle_metrics(
                    service=self._service_name,
                    sla_event="critical",
                    limit_change_direction="down",
                    limit_change_trigger="sla_critical",
                    limit_change_percent=30,
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

                # 감사 로깅 (SLA Critical → CascadeEvent 포함)
                smoothed_rtt, _ = self._gradient_calculator.get_snapshot()
                _record_audit_safe(
                    action="throttle_sla_critical",
                    old_limit=previous_limit,
                    new_limit=self._current_limit,
                    rtt_ms=rtt_ms,
                    threshold_ms=self.config.sla_critical_ms,
                    smoothed_rtt_ms=smoothed_rtt,
                    gradient=gradient,
                    trigger_source="sla_critical",
                    extra_data={"reduction_percent": 30},
                )
                return

            if rtt_ms >= self.config.sla_warning_ms:
                # Warning: moderate reduction
                self._adaptive_stats["sla_warnings"] += 1
                new_limit = int(self._current_limit * self.config.decrease_ratio)
                logger.info(
                    "adaptive_throttle.warning_ms_ms_limit",
                    rtt_ms=rtt_ms,
                    _self=self.config.sla_warning_ms,
                    self_2=self._current_limit,
                    new_limit=new_limit,
                )
                self.current_limit = new_limit
                self._adaptive_stats["adjustments_down"] += 1

                # SLA Warning 이벤트 발행
                rtt_change_percent = gradient * 100.0
                _emit_throttle_event(
                    "THROTTLE_SLA_WARNING",
                    {
                        "current_rtt_ms": rtt_ms,
                        "threshold_ms": self.config.sla_warning_ms,
                        "current_limit": self._current_limit,
                        "previous_limit": previous_limit,
                        "gradient": gradient,
                        "rtt_change_percent": rtt_change_percent,
                        "service_name": "default",
                    },
                    priority_name="HIGH",
                )

                # SLA Warning Prometheus 메트릭 기록
                _record_throttle_metrics(
                    service=self._service_name,
                    sla_event="warning",
                    limit_change_direction="down",
                    limit_change_trigger="sla_warning",
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

                # 감사 로깅 (SLA Warning)
                smoothed_rtt, _ = self._gradient_calculator.get_snapshot()
                _record_audit_safe(
                    action="throttle_sla_warning",
                    old_limit=previous_limit,
                    new_limit=self._current_limit,
                    rtt_ms=rtt_ms,
                    threshold_ms=self.config.sla_warning_ms,
                    smoothed_rtt_ms=smoothed_rtt,
                    gradient=gradient,
                    trigger_source="sla_warning",
                )
                return

            # Gradient-based adjustment
            if gradient > 0.1:  # RTT increasing more than 10%
                new_limit = int(self._current_limit * self.config.decrease_ratio)
                logger.debug(
                    "adaptive_throttle.limit",
                    gradient=gradient,
                    _self=self._current_limit,
                    new_limit=new_limit,
                )
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

                    # 감사 로깅 (Gradient 기반 limit 감소)
                    smoothed_rtt, _ = self._gradient_calculator.get_snapshot()
                    _record_audit_safe(
                        action="throttle_limit_adjusted",
                        old_limit=previous_limit,
                        new_limit=self._current_limit,
                        reason="gradient_increase",
                        trigger_source="gradient",
                        rtt_ms=rtt_ms,
                        smoothed_rtt_ms=smoothed_rtt,
                        gradient=gradient,
                    )

            elif gradient < -0.05:  # RTT decreasing more than 5%
                new_limit = self._current_limit + self.config.increase_step
                logger.debug(
                    "adaptive_throttle.limit",
                    gradient=gradient,
                    _self=self._current_limit,
                    new_limit=new_limit,
                )
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

                    # 감사 로깅 (Gradient 기반 limit 증가)
                    smoothed_rtt, _ = self._gradient_calculator.get_snapshot()
                    _record_audit_safe(
                        action="throttle_limit_adjusted",
                        old_limit=previous_limit,
                        new_limit=self._current_limit,
                        reason="gradient_decrease",
                        trigger_source="gradient",
                        rtt_ms=rtt_ms,
                        smoothed_rtt_ms=smoothed_rtt,
                        gradient=gradient,
                    )

    def _execute_tiered_check(
        self,
        key: str,
        tier_id: str,
        context: dict | None,
    ) -> ThrottleResult:
        """티어/429/Load Shedding 상태를 반영한 limit 검사 실행."""
        if self._429_reduction_active and tier_id in PROTECTED_TIERS_ON_429:
            original_limit = self._current_limit
            self._current_limit = self._limit_before_429
            logger.debug(
                "adaptive_throttle.critical_tier_protected_using",
                _self=self._limit_before_429,
            )
            result = super().check(key)
            self._current_limit = original_limit
            return result

        effective_service_id = (context.get("service_id") if context else None) or self._service_name
        if (
            effective_service_id
            and self._shedding_affected_services
            and effective_service_id in self._shedding_affected_services
        ):
            original_limit = self._current_limit
            self._current_limit = min(self._current_limit, self._shedding_suggested_limit)
            result = super().check(key)
            self._current_limit = original_limit
            return result

        return super().check(key)

    def check(
        self,
        key: str,
        tier_id: str = "standard",
        context: dict | None = None,
        store_rejection: bool = True,
    ) -> ThrottleResult:
        """
        Check if request is allowed with adaptive info and priority protection.

        거부 시 DLQ 자동 저장:
        - context가 제공되고 store_rejection=True이면 거부 요청을 DLQ에 저장
        - store_rejection=False이면 DLQ 저장 생략 (Replay 거부 등 순환 방지)

        Error Budget Gate 통합:
        - ERROR_BUDGET_CRITICAL 상태 시 non_essential 티어 추가 거부

        Args:
            key: 요청 식별자
            tier_id: 요청 티어 (critical/standard/non_essential)
            context: 요청 컨텍스트 (DLQ 저장용, domain/tier_id/trace_id 등)
            store_rejection: 거부 시 DLQ 저장 여부 (기본 True)

        Returns:
            ThrottleResult with adaptive info
        """
        # Break Glass 활성 + Full Stop → Full Stop 해제 후 Recovery Dampening 시작
        if self._break_glass_active and self._full_stop_active:
            logger.warning("adaptive_throttle.break_glass_overriding_full")
            self.deactivate_full_stop()

        # Governance 통합 상태 동기화 (Emergency + Kill Switch + Break Glass, 30초 TTL)
        self._sync_governance_state()

        # Recovery Dampening 진행 확인
        if self._recovery_dampening_active:
            self.advance_recovery_dampening()

        # Error Budget Critical 상태에서 non_essential 티어 거부
        budget_block = self._check_error_budget_block(tier_id, store_rejection, context)
        if budget_block is not None:
            return budget_block

        # 티어/429/Load Shedding 반영 검사
        result = self._execute_tiered_check(key, tier_id, context)

        # Add adaptive info to result
        result.current_rtt_ms = self._gradient_calculator.get_current_rtt()
        result.rtt_gradient = self._gradient_calculator.get_gradient()

        # Exemplar 취득 (Fail-Open) + 메트릭 기록
        trace_id = _get_trace_id_safe()
        _record_throttle_metrics(
            service=self._service_name,
            request_result="allowed" if result.allowed else "denied",
            denied_reason=result.reason if not result.allowed else None,
            trace_id=trace_id,
        )

        # 거부 시 DLQ 자동 저장
        if not result.allowed and store_rejection and context:
            self._auto_store_rejection_to_dlq(context, result.reason or self.get_rejection_reason())

        return result

    def _auto_store_rejection_to_dlq(
        self,
        context: dict,
        rejection_reason: str,
    ) -> None:
        """
        check() 거부 시 DLQ 자동 저장 (Fail-Open).

        ThrottleDLQReplayMixin.store_throttle_rejection_to_dlq() 위임.
        DLQ 서비스 미사용 또는 예외 시 무시하고 Throttle 동작 계속.
        """
        try:
            self.store_throttle_rejection_to_dlq(context, rejection_reason)
        except Exception as e:
            logger.debug(
                "adaptive_throttle.dlq_auto_store_failed",
                error=e,
            )

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
            "governance": {
                "kill_switch_active": self._kill_switch_active,
                "break_glass_active": self._break_glass_active,
            },
            "recovery": {
                "dampening_active": self._recovery_dampening_active,
                "dampening_step": self._recovery_dampening_step,
            },
        }

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
        # Load Shedding 연동 상태 초기화
        self._shedding_suggested_limit = self.config.max_limit
        self._shedding_affected_services = set()
        # 상태 동기화 초기화
        self._last_emergency_check_time = 0.0
        # Governance 연동 상태 초기화
        self._kill_switch_active = False
        self._break_glass_active = False

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
        # Governance 연동 상태 해제
        self._kill_switch_active = False
        self._break_glass_active = False

        # base limit으로 복구
        self.current_limit = self._base_limit_before_emergency

        logger.warning(
            "adaptive_throttle.rolled_back_base_limit",
            previous=previous,
            _self=self._base_limit_before_emergency,
        )

        return self._base_limit_before_emergency

    # =========================================================================
    # 설정 스냅샷 (감사 로깅용)
    # =========================================================================

    def get_config_snapshot(self) -> dict:
        """
        현재 Throttle 설정 상태 스냅샷 반환.

        감사 로깅 및 설정 변경 추적에 사용됩니다.

        Returns:
            현재 설정 상태 딕셔너리
        """
        # GradientCalculator에서 RTT와 Gradient 가져오기
        smoothed_rtt, current_gradient = self._gradient_calculator.get_snapshot()

        return {
            "current_limit": self._current_limit,
            "initial_limit": self.config.initial_limit,
            "min_limit": self.config.min_limit,
            "max_limit": self.config.max_limit,
            "emergency_mode_active": self._emergency_mode_active,
            "emergency_level": self._emergency_level,
            "full_stop_active": self._full_stop_active,
            "recovery_dampening_active": self._recovery_dampening_active,
            "gradient_frozen": self._gradient_frozen,
            "smoothed_rtt_ms": smoothed_rtt,
            "current_gradient": current_gradient,
            "429_reduction_active": self._429_reduction_active,
            "service_name": self._service_name,
            "sla_warning_ms": self.config.sla_warning_ms,
            "sla_critical_ms": self.config.sla_critical_ms,
            "kill_switch_active": self._kill_switch_active,
            "break_glass_active": self._break_glass_active,
        }

    def swap_config(self, new_config: ThrottleConfig) -> ThrottleConfig:
        """
        Config 객체를 Atomic Swap으로 교체.

        GIL에 의해 참조 대입은 atomic이므로,
        _maybe_adjust_limit() 실행 중에도 안전하다.
        _current_limit, _base_limit_before_emergency 등
        파생 상태는 변경하지 않는다 (SLA 값만 교체 용도).

        Args:
            new_config: 새 ThrottleConfig (model_copy 등으로 생성)

        Returns:
            교체 전 이전 config 객체 (롤백용 보관)
        """
        old_config = self.config
        self.config = new_config
        logger.info(
            "adaptive_throttle.config_swapped",
            old_config=old_config.sla_warning_ms,
            new_config=new_config.sla_warning_ms,
            old_config_2=old_config.sla_critical_ms,
            new_config_3=new_config.sla_critical_ms,
        )
        return old_config




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


__all__ = [
    "AdaptiveThrottle",
    "get_adaptive_throttle",
    "reset_adaptive_throttle",
    "GradientCalculator",
    "RTTSample",
    "EMERGENCY_LEVEL_LIMIT_MULTIPLIERS",
    "PROTECTED_TIERS_ON_429",
]
