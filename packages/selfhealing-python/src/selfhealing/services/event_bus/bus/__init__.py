"""
Self-Healing Event Bus - Component Decoupling System.

이벤트 기반 아키텍처를 통해 컴포넌트 간 느슨한 결합을 제공합니다.

Features:
- In-memory event bus (single-process)
- Thread-safe event handling
- Priority-based event processing
- Async handler support
- Event history for debugging

Events:
- EmergencyLevelChanged: 비상 모드 레벨 변경 시 발행
- ErrorBudgetCritical: 에러 예산이 임계치 이하로 떨어질 때 발행
- CircuitBreakerStateChanged: CB 상태 변경 시 발행
- ConfigUpdated: 런타임 설정 변경 시 발행

Usage:
    from selfhealing.services.event_bus import (
        get_event_bus,
        SelfHealingEvent,
        EventType,
    )

    # Subscribe to events
    bus = get_event_bus()
    bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, my_handler)

    # Publish events
    bus.publish(SelfHealingEvent(
        event_type=EventType.EMERGENCY_LEVEL_CHANGED,
        data={"level": 3, "previous_level": 0},
        source="emergency_manager",
    ))
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Event Types
# =============================================================================


class EventType(str, Enum):
    """Self-Healing 시스템 이벤트 타입."""

    # Emergency Mode Events
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
    EMERGENCY_ACTIVATED = "emergency_activated"
    EMERGENCY_DEACTIVATED = "emergency_deactivated"
    EMERGENCY_RECOVERY_STARTED = "emergency_recovery_started"
    EMERGENCY_RECOVERY_COMPLETED = "emergency_recovery_completed"

    # Error Budget Events
    ERROR_BUDGET_CRITICAL = "error_budget_critical"
    ERROR_BUDGET_WARNING = "error_budget_warning"
    ERROR_BUDGET_RECOVERED = "error_budget_recovered"

    # Circuit Breaker Events
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CIRCUIT_BREAKER_HALF_OPENED = "circuit_breaker_half_opened"

    # Config Events
    CONFIG_UPDATED = "config_updated"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"
    KILL_SWITCH_DEACTIVATED = "kill_switch_deactivated"

    # DLQ Events
    DLQ_REPLAY_BLOCKED = "dlq_replay_blocked"
    DLQ_REPLAY_COMPLETED = "dlq_replay_completed"

    # Chaos Events
    CHAOS_EXPERIMENT_BLOCKED = "chaos_experiment_blocked"
    CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
    CHAOS_EXPERIMENT_STOPPED = "chaos_experiment_stopped"

    # ═══════════════════════════════════════════════════════════════════════════
    # Security Violation Events
    # ═══════════════════════════════════════════════════════════════════════════
    SECURITY_VIOLATION_DETECTED = "security_violation_detected"
    """보안 위반 감지됨."""

    SECURITY_VIOLATION_CRITICAL = "security_violation_critical"
    """CRITICAL 보안 위반 - Emergency Mode 및 Error Budget 연동 트리거."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Adaptive Throttle Events
    # ═══════════════════════════════════════════════════════════════════════════
    THROTTLE_LIMIT_CHANGED = "throttle_limit_changed"
    """Throttle limit 변경됨 (previous_limit, new_limit, reason 포함)."""

    THROTTLE_SLA_WARNING = "throttle_sla_warning"
    """SLA Warning 임계값 도달 (current_rtt_ms, threshold_ms, current_limit 포함)."""

    THROTTLE_SLA_CRITICAL = "throttle_sla_critical"
    """SLA Critical 임계값 도달 (current_rtt_ms, threshold_ms, current_limit 포함)."""

    THROTTLE_LIMIT_RECOVERED = "throttle_limit_recovered"
    """Throttle limit 정상 범위 회복 (previous_limit, new_limit 포함)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Rate Limit Coordinator Events (429 통합 대응)
    # ═══════════════════════════════════════════════════════════════════════════
    RATE_LIMIT_429 = "rate_limit_429"
    """외부 API 429 응답 수신 (key, consecutive_429s, cooldown_until 포함)."""

    RATE_LIMIT_COOLDOWN_START = "rate_limit_cooldown_start"
    """Rate Limit Cooldown 시작 (key, delay, cooldown_until 포함)."""

    RATE_LIMIT_COOLDOWN_END = "rate_limit_cooldown_end"
    """Rate Limit Cooldown 종료 (key, cooldown_ended_at 포함)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Throttle + DLQ 연동 이벤트
    # ═══════════════════════════════════════════════════════════════════════════
    THROTTLE_REJECTION_STORED = "throttle_rejection_stored"
    """Throttle 거부 요청이 DLQ에 저장됨 (entry_id, reason, domain, tier_id 포함)."""

    THROTTLE_REJECTION_REPLAY_STARTED = "throttle_rejection_replay_started"
    """Throttle Recovery 시 DLQ Replay 시작 (recovery_percent 포함)."""

    THROTTLE_REJECTION_REPLAY_COMPLETED = "throttle_rejection_replay_completed"
    """Throttle Recovery DLQ Replay 완료 (replayed, failed, remaining 포함)."""

    THROTTLE_REJECTION_REPLAY_FAILED = "throttle_rejection_replay_failed"
    """Throttle Recovery DLQ Replay 실패 (error 포함)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Multi-Region Events
    # ═══════════════════════════════════════════════════════════════════════════
    REGION_INSTANCE_STOPPING = "region_instance_stopping"
    """리전 인스턴스 정상 종료 시작 (region, reason, timestamp 포함)."""

    REGION_HEARTBEAT_EXPIRED = "region_heartbeat_expired"
    """리전 하트비트 TTL 만료 — 비정상 종료 감지 (region 포함)."""

    REGION_PRIMARY_CHANGED = "region_primary_changed"
    """Primary 리전 변경됨 (from_region, to_region 포함)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Load Shedding Events
    # ═══════════════════════════════════════════════════════════════════════════
    LOAD_SHEDDING_LEVEL_CHANGED = "load_shedding_level_changed"
    """Load Shedding 레벨 변경 (new_level, previous_level, traffic_limit, affected_services 포함)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Saga Orchestrator Events
    # ═══════════════════════════════════════════════════════════════════════════
    SAGA_STARTED = "saga_started"
    """Saga 실행 시작."""

    SAGA_STEP_COMPLETED = "saga_step_completed"
    """Forward Step 실행 성공."""

    SAGA_STEP_FAILED = "saga_step_failed"
    """Forward Step 실행 실패."""

    SAGA_COMPLETED = "saga_completed"
    """모든 Forward Step 성공, Saga 완료."""

    SAGA_COMPENSATING = "saga_compensating"
    """역순 보상 Step 완료 (개별 Step 보상 성공마다 발행)."""

    SAGA_COMPENSATED = "saga_compensated"
    """모든 보상 완료."""

    SAGA_COMPENSATION_FAILED = "saga_compensation_failed"
    """보상 실패. DLQ에 저장됨."""

    SAGA_SUSPENDED = "saga_suspended"
    """서킷브레이커 OPEN으로 일시 중지."""

    SAGA_RESUMED = "saga_resumed"
    """중단된 Saga 재개."""

    SAGA_TIMED_OUT = "saga_timed_out"
    """전체 Saga 타임아웃 초과."""


class EventPriority(IntEnum):
    """이벤트 처리 우선순위."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


# =============================================================================
# Event Data Classes
# =============================================================================


@dataclass
class SelfHealingEvent:
    """Self-Healing 이벤트 데이터 클래스."""

    event_type: EventType
    data: dict[str, Any]
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "priority": self.priority.value,
            "correlation_id": self.correlation_id,
        }


@dataclass
class EventSubscription:
    """이벤트 구독 정보."""

    event_type: EventType
    handler: Callable[[SelfHealingEvent], None]
    handler_name: str
    priority: EventPriority = EventPriority.NORMAL
    enabled: bool = True

    def __hash__(self):
        return hash((self.event_type, self.handler_name))


# =============================================================================
# Event Bus
# =============================================================================


class SelfHealingEventBus:
    """
    Self-Healing 이벤트 버스 - 컴포넌트 간 느슨한 결합.

    Thread-safe 싱글톤으로 구현.

    Usage:
        bus = SelfHealingEventBus()

        # Subscribe
        bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, my_handler)

        # Publish
        bus.publish(SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 3},
            source="emergency_manager",
        ))

        # Unsubscribe
        bus.unsubscribe(EventType.EMERGENCY_LEVEL_CHANGED, my_handler)
    """

    _instance: SelfHealingEventBus | None = None
    _lock = threading.Lock()

    def __new__(cls) -> SelfHealingEventBus:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self):
        """초기화."""
        self._subscriptions: dict[EventType, list[EventSubscription]] = {}
        self._subscription_lock = threading.RLock()
        self._event_history: list[dict[str, Any]] = []
        self._max_history = 1000
        self._history_lock = threading.Lock()
        self._enabled = True
        self._handlers_registered = False

    # -------------------------------------------------------------------------
    # Subscription Management
    # -------------------------------------------------------------------------

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[SelfHealingEvent], None],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> EventSubscription:
        """
        이벤트 타입에 핸들러 구독.

        Args:
            event_type: 구독할 이벤트 타입
            handler: 이벤트 핸들러 함수
            priority: 핸들러 우선순위 (높을수록 먼저 실행)

        Returns:
            EventSubscription: 구독 정보
        """
        handler_name = getattr(handler, "__name__", str(handler))

        subscription = EventSubscription(
            event_type=event_type,
            handler=handler,
            handler_name=handler_name,
            priority=priority,
        )

        with self._subscription_lock:
            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []

            # 중복 구독 방지
            existing = [s for s in self._subscriptions[event_type] if s.handler_name == handler_name]
            if not existing:
                self._subscriptions[event_type].append(subscription)
                # 우선순위로 정렬 (높은 것 먼저)
                self._subscriptions[event_type].sort(
                    key=lambda s: s.priority.value,
                    reverse=True,
                )
                logger.debug(f"[EventBus] Subscribed {handler_name} to {event_type.value} " f"(priority={priority.name})")
            else:
                logger.debug(f"[EventBus] Handler {handler_name} already subscribed to {event_type.value}")
                return existing[0]

        return subscription

    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[SelfHealingEvent], None],
    ) -> bool:
        """
        이벤트 구독 해제.

        Args:
            event_type: 구독 해제할 이벤트 타입
            handler: 핸들러 함수

        Returns:
            bool: 해제 성공 여부
        """
        handler_name = getattr(handler, "__name__", str(handler))

        with self._subscription_lock:
            if event_type not in self._subscriptions:
                return False

            original_count = len(self._subscriptions[event_type])
            self._subscriptions[event_type] = [s for s in self._subscriptions[event_type] if s.handler_name != handler_name]

            removed = original_count > len(self._subscriptions[event_type])
            if removed:
                logger.debug(f"[EventBus] Unsubscribed {handler_name} from {event_type.value}")

            return removed

    def unsubscribe_all(self, event_type: EventType | None = None):
        """
        모든 구독 해제.

        Args:
            event_type: 특정 이벤트 타입만 해제 (None이면 전체)
        """
        with self._subscription_lock:
            if event_type is None:
                self._subscriptions.clear()
                logger.info("[EventBus] All subscriptions cleared")
            elif event_type in self._subscriptions:
                del self._subscriptions[event_type]
                logger.info(f"[EventBus] Subscriptions cleared for {event_type.value}")

    # -------------------------------------------------------------------------
    # Event Publishing
    # -------------------------------------------------------------------------

    def publish(self, event: SelfHealingEvent) -> int:
        """
        이벤트 발행.

        Args:
            event: 발행할 이벤트

        Returns:
            int: 호출된 핸들러 수
        """
        if not self._enabled:
            logger.debug(f"[EventBus] Event bus disabled, ignoring {event.event_type.value}")
            return 0

        # 히스토리에 기록
        self._record_event(event)

        handlers_called = 0

        with self._subscription_lock:
            subscriptions = self._subscriptions.get(event.event_type, [])
            if not subscriptions:
                logger.debug(f"[EventBus] No subscribers for {event.event_type.value}")
                return 0

            # 복사본으로 작업 (실행 중 구독 변경 방지)
            subscriptions = list(subscriptions)

        for subscription in subscriptions:
            if not subscription.enabled:
                continue

            try:
                subscription.handler(event)
                handlers_called += 1
                logger.debug(f"[EventBus] Handler {subscription.handler_name} " f"executed for {event.event_type.value}")
            except Exception as e:
                logger.error(
                    f"[EventBus] Handler {subscription.handler_name} failed "
                    f"for {event.event_type.value}: {e}\n{traceback.format_exc()}"
                )

        logger.info(
            f"[EventBus] Published {event.event_type.value} from {event.source}, " f"{handlers_called} handlers called"
        )

        return handlers_called

    def emit(
        self,
        event_type: EventType,
        data: dict[str, Any],
        source: str = "unknown",
        priority: EventPriority = EventPriority.NORMAL,
        correlation_id: str | None = None,
    ) -> int:
        """
        간편 이벤트 발행.

        Args:
            event_type: 이벤트 타입
            data: 이벤트 데이터
            source: 이벤트 소스
            priority: 우선순위
            correlation_id: 상관 ID

        Returns:
            int: 호출된 핸들러 수
        """
        event = SelfHealingEvent(
            event_type=event_type,
            data=data,
            source=source,
            priority=priority,
            correlation_id=correlation_id,
        )
        return self.publish(event)

    # -------------------------------------------------------------------------
    # Event History
    # -------------------------------------------------------------------------

    def _record_event(self, event: SelfHealingEvent):
        """이벤트 히스토리에 기록."""
        with self._history_lock:
            self._event_history.append(event.to_dict())

            # 최대 개수 유지
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history :]

    def get_history(
        self,
        event_type: EventType | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        이벤트 히스토리 조회.

        Args:
            event_type: 필터링할 이벤트 타입
            limit: 최대 조회 개수

        Returns:
            List[Dict]: 이벤트 히스토리
        """
        with self._history_lock:
            history = list(self._event_history)

        if event_type:
            history = [e for e in history if e["event_type"] == event_type.value]

        return history[-limit:]

    def clear_history(self):
        """히스토리 초기화."""
        with self._history_lock:
            self._event_history.clear()

    # -------------------------------------------------------------------------
    # Control
    # -------------------------------------------------------------------------

    def enable(self):
        """이벤트 버스 활성화."""
        self._enabled = True
        logger.info("[EventBus] Enabled")

    def disable(self):
        """이벤트 버스 비활성화."""
        self._enabled = False
        logger.info("[EventBus] Disabled")

    def is_enabled(self) -> bool:
        """활성화 여부."""
        return self._enabled

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """이벤트 버스 통계."""
        with self._subscription_lock:
            subscriptions_count = sum(len(subs) for subs in self._subscriptions.values())
            event_types_with_subs = len(self._subscriptions)

        with self._history_lock:
            history_count = len(self._event_history)

        return {
            "enabled": self._enabled,
            "subscriptions_count": subscriptions_count,
            "event_types_with_subscribers": event_types_with_subs,
            "history_count": history_count,
            "max_history": self._max_history,
        }

    def get_subscriptions(
        self,
        event_type: EventType | None = None,
    ) -> list[dict[str, Any]]:
        """구독 정보 조회."""
        with self._subscription_lock:
            if event_type:
                subs = self._subscriptions.get(event_type, [])
            else:
                subs = [s for subs in self._subscriptions.values() for s in subs]

            return [
                {
                    "event_type": s.event_type.value,
                    "handler_name": s.handler_name,
                    "priority": s.priority.name,
                    "enabled": s.enabled,
                }
                for s in subs
            ]

    # -------------------------------------------------------------------------
    # Reset (Testing)
    # -------------------------------------------------------------------------

    def reset(self):
        """상태 초기화 (테스트용)."""
        with self._subscription_lock:
            self._subscriptions.clear()
        with self._history_lock:
            self._event_history.clear()
        self._enabled = True
        self._handlers_registered = False
        logger.info("[EventBus] Reset to defaults")


# =============================================================================
# Default Event Handlers
# =============================================================================


def _on_emergency_level_changed(event: SelfHealingEvent):
    """
    비상 모드 레벨 변경 시 CB/DLQ 자동 조정.

    - LEVEL_3 이상: Non-essential 서비스 CB 강제 Open
    - LEVEL_2: Standard 서비스 트래픽 제한
    """
    level = event.data.get("level", 0)
    previous_level = event.data.get("previous_level", 0)
    is_escalation = level > previous_level

    logger.info(f"[EventHandler] Emergency level changed: {previous_level} → {level} " f"(escalation={is_escalation})")

    # LEVEL_3 이상이면 추가 조치
    if level >= 3 and is_escalation:
        logger.warning("[EventHandler] LEVEL_3 emergency - blocking non-essential automation")
        # Circuit Breaker 자동 Open은 개별 서비스에서 처리
        # 여기서는 로깅만 수행


def _on_error_budget_critical(event: SelfHealingEvent):
    """
    에러 예산 임계치 도달 시 자동화 제한.

    - Chaos 실험 자동 차단
    - 자동 Replay 일시 중지
    """
    budget_percent = event.data.get("budget_percent", 0)
    threshold = event.data.get("threshold", 20)

    logger.warning(f"[EventHandler] Error budget critical: {budget_percent:.1f}% < {threshold}% threshold")



# =============================================================================
# Throttle Event Handlers
# =============================================================================



# Handler sub-module imports
from ._cb_handlers import (  # noqa: E402
    _on_circuit_breaker_opened_notify,
    _collect_web_server_metrics,
    _on_circuit_breaker_opened_snapshot,
    _send_postmortem_notification,
    _on_circuit_breaker_closed,
    _on_circuit_breaker_closed_postmortem,
)
from ._emergency_postmortem import (  # noqa: E402
    _handle_incident_group,
    _schedule_group_close,
    _create_individual_postmortem,
    _build_emergency_timeline,
    _build_recovery_steps,
    _build_emergency_actions,
    _collect_emergency_cascade_event_data,
    _build_emergency_deep_links,
    _generate_emergency_postmortem_data,
    _on_emergency_recovery_completed_postmortem,
    _create_emergency_postmortem_sync,
)
from ._throttle_handlers import (  # noqa: E402
    _on_emergency_level_changed_throttle,
    _on_emergency_deactivated_throttle,
    _on_circuit_breaker_opened_throttle,
    _on_circuit_breaker_half_opened_throttle,
    _on_circuit_breaker_closed_throttle,
    _on_error_budget_critical_throttle,
    _on_error_budget_recovered_throttle,
    _on_kill_switch_activated_throttle,
)

def register_default_handlers():
    """
    기본 이벤트 핸들러 등록.

    앱 초기화 시 호출됩니다.
    """
    bus = get_event_bus()

    if bus._handlers_registered:
        return

    # Emergency events
    bus.subscribe(
        EventType.EMERGENCY_LEVEL_CHANGED,
        _on_emergency_level_changed,
        priority=EventPriority.HIGH,
    )

    # Error Budget events
    bus.subscribe(
        EventType.ERROR_BUDGET_CRITICAL,
        _on_error_budget_critical,
        priority=EventPriority.CRITICAL,
    )

    # Circuit Breaker events

    # 무결성 게이트 (CRITICAL: Replay보다 먼저 실행)
    try:
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
    except ImportError:
        pass  # integrity_gate 모듈 미설치 시 무시

    bus.subscribe(
        EventType.CIRCUIT_BREAKER_CLOSED,
        _on_circuit_breaker_closed,
        priority=EventPriority.NORMAL,
    )

    # Circuit Breaker 자동 Post-mortem 핸들러 (낮은 우선순위)
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_CLOSED,
        _on_circuit_breaker_closed_postmortem,
        priority=EventPriority.LOW,
    )

    # Circuit Breaker 알림 핸들러
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_OPENED,
        _on_circuit_breaker_opened_notify,
        priority=EventPriority.HIGH,  # 지연 없이 처리
    )

    # Circuit Breaker OPEN 시점 스냅샷 저장 핸들러
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_OPENED,
        _on_circuit_breaker_opened_snapshot,
        priority=EventPriority.NORMAL,  # 알림 이후에 실행
    )

    # Emergency Recovery 완료 시 자동 Postmortem 핸들러 (낮은 우선순위)
    bus.subscribe(
        EventType.EMERGENCY_RECOVERY_COMPLETED,
        _on_emergency_recovery_completed_postmortem,
        priority=EventPriority.LOW,
    )

    # =========================================================================
    # Throttle Event Handlers
    # =========================================================================

    # Emergency 레벨 변경 시 Throttle limit 자동 조정
    bus.subscribe(
        EventType.EMERGENCY_LEVEL_CHANGED,
        _on_emergency_level_changed_throttle,
        priority=EventPriority.HIGH,
    )

    # Emergency 비활성화 시 Throttle limit 복구
    bus.subscribe(
        EventType.EMERGENCY_DEACTIVATED,
        _on_emergency_deactivated_throttle,
        priority=EventPriority.HIGH,
    )

    # Circuit Breaker OPEN 시 Throttle limit 강등
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_OPENED,
        _on_circuit_breaker_opened_throttle,
        priority=EventPriority.HIGH,
    )

    # Circuit Breaker CLOSED 시 Throttle limit 복원
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_CLOSED,
        _on_circuit_breaker_closed_throttle,
        priority=EventPriority.NORMAL,
    )

    # Circuit Breaker HALF_OPEN 시 Throttle limit 제한적 허용
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_HALF_OPENED,
        _on_circuit_breaker_half_opened_throttle,
        priority=EventPriority.HIGH,
    )

    # Error Budget Critical 시 Throttle limit 보수적 조정
    bus.subscribe(
        EventType.ERROR_BUDGET_CRITICAL,
        _on_error_budget_critical_throttle,
        priority=EventPriority.HIGH,
    )

    # Error Budget 회복 시 Throttle limit 제한 해제
    bus.subscribe(
        EventType.ERROR_BUDGET_RECOVERED,
        _on_error_budget_recovered_throttle,
        priority=EventPriority.NORMAL,
    )

    # Kill Switch 활성화 시 Throttle 기능 일시 중지
    bus.subscribe(
        EventType.KILL_SWITCH_ACTIVATED,
        _on_kill_switch_activated_throttle,
        priority=EventPriority.CRITICAL,
    )

    bus._handlers_registered = True
    logger.info("[EventBus] Default handlers registered")


# =============================================================================
# Singleton & Factory Functions
# =============================================================================


# Global instance
_event_bus: SelfHealingEventBus | None = None


def get_event_bus() -> SelfHealingEventBus:
    """이벤트 버스 싱글톤 획득."""
    global _event_bus
    if _event_bus is None:
        _event_bus = SelfHealingEventBus()
    return _event_bus


# =============================================================================
# Convenience Functions
# =============================================================================


def emit_emergency_level_changed(
    level: int,
    previous_level: int,
    reason: str = "",
    source: str = "emergency_manager",
) -> int:
    """
    비상 모드 레벨 변경 이벤트 발행 (간편 함수).
    """
    return get_event_bus().emit(
        event_type=EventType.EMERGENCY_LEVEL_CHANGED,
        data={
            "level": level,
            "previous_level": previous_level,
            "reason": reason,
            "is_escalation": level > previous_level,
        },
        source=source,
        priority=EventPriority.HIGH,
    )


def emit_error_budget_critical(
    budget_percent: float,
    threshold: float = 20.0,
    source: str = "error_budget_gate",
) -> int:
    """
    에러 예산 임계치 도달 이벤트 발행 (간편 함수).
    """
    return get_event_bus().emit(
        event_type=EventType.ERROR_BUDGET_CRITICAL,
        data={
            "budget_percent": budget_percent,
            "threshold": threshold,
        },
        source=source,
        priority=EventPriority.CRITICAL,
    )


def emit_circuit_breaker_state_changed(
    service_name: str,
    new_state: str,
    previous_state: str,
    source: str = "circuit_breaker_service",
) -> int:
    """
    CB 상태 변경 이벤트 발행 (간편 함수).
    """
    # 상태에 따라 이벤트 타입 결정
    if new_state.upper() == "OPEN":
        event_type = EventType.CIRCUIT_BREAKER_OPENED
    elif new_state.upper() == "CLOSED":
        event_type = EventType.CIRCUIT_BREAKER_CLOSED
    elif new_state.upper() in ("HALF_OPEN", "HALF-OPEN"):
        event_type = EventType.CIRCUIT_BREAKER_HALF_OPENED
    else:
        # 기타 상태 변경은 CLOSED로 처리
        event_type = EventType.CIRCUIT_BREAKER_CLOSED

    return get_event_bus().emit(
        event_type=event_type,
        data={
            "service_name": service_name,
            "new_state": new_state,
            "previous_state": previous_state,
        },
        source=source,
    )