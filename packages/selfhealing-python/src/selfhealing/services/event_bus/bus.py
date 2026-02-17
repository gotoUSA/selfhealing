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


def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    """
    CB OPEN 시 알림 발송을 Celery Task로 위임.

    Slack Webhook HTTP 호출 등 네트워크 I/O를 포함한 알림 발송을
    Celery Worker에서 비동기로 처리하여 발행자 스레드 차단을 제거한다.
    Celery 미설치 환경에서는 ImportError fallback으로 안전하게 스킵한다.
    """
    try:
        from selfhealing.adapters.celery.tasks import send_cb_open_notification

        send_cb_open_notification.delay(
            service_name=event.data.get("service_name", "unknown"),
            trace_id=event.data.get("trace_id"),
            trace_url=event.data.get("trace_url"),
            timestamp=event.data.get("timestamp", ""),
        )
    except ImportError:
        logger.debug("[EventHandler] Celery tasks not available, skipping CB notification")
    except Exception as e:
        logger.warning(f"[Notification] Failed to enqueue CB notification: {e}")


def _collect_web_server_metrics() -> dict | None:
    """Web Server의 캐시된 시스템 메트릭을 수집 (~0ms). 실패 시 None 반환."""
    try:
        from selfhealing.services.system_metrics_cache import get_system_metrics_cache

        cache = get_system_metrics_cache()
        if cache.is_running():
            return cache.get_snapshot_dict()
    except Exception:
        pass
    return None


def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    """
    CB OPEN 시 시스템 스냅샷 수집을 Celery Task로 위임.

    psutil.cpu_percent(interval=0.1)의 100ms 블로킹과 Redis HSET를
    Celery Worker에서 비동기로 처리하여 발행자 스레드 차단을 제거한다.
    Celery 미설치 환경에서는 ImportError fallback으로 안전하게 스킵한다.
    """
    service_name = event.data.get("service_name", "unknown")
    try:
        from selfhealing.adapters.celery.tasks import collect_cb_open_snapshot

        web_metrics = _collect_web_server_metrics()

        collect_cb_open_snapshot.delay(
            service_name=service_name,
            event_timestamp=event.timestamp.isoformat(),
            web_server_metrics=web_metrics,
        )
    except ImportError:
        logger.debug("[EventHandler] Celery tasks not available, skipping CB snapshot")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to enqueue CB snapshot: {e}")


def _send_postmortem_notification(
    settings,
    postmortem: dict,
    incident_id: str,
    service_name: str,
    duration: int | None,
    affected_services: list[str],
) -> None:
    """
    Post-mortem 생성 완료 알림 발송.

    Settings에서 notification_enabled가 True이고,
    duration이 notification_min_duration 이상인 경우에만 발송합니다.

    알림 우선순위 결정:
    - duration >= 300초 (5분) 또는 affected_services >= 3: HIGH
    - 그 외: MEDIUM
    """
    try:
        # 알림 활성화 여부 확인
        if not settings.notification_enabled:
            logger.debug(f"[Notification] Postmortem notification disabled for {incident_id}")
            return

        # 최소 duration 확인
        notification_min_duration = settings.notification_min_duration
        if duration is not None and duration < notification_min_duration:
            logger.debug(
                f"[Notification] Postmortem notification skipped for {incident_id}: "
                f"duration {duration}s < min {notification_min_duration}s"
            )
            return

        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            UnifiedNotificationManager,
        )

        # 우선순위 결정: 5분 이상 또는 3개 이상 서비스 영향 → HIGH
        affected_count = len(affected_services) if affected_services else 0
        if (duration is not None and duration >= 300) or affected_count >= 3:
            priority = NotificationPriority.HIGH
        else:
            priority = NotificationPriority.MEDIUM

        # 알림 본문 생성
        resolved_at = postmortem.get("resolved_at", "N/A")
        started_at = postmortem.get("started_at", "N/A")
        recommendations = postmortem.get("recommendations", [])
        recommendations_summary = ", ".join(recommendations[:3]) if recommendations else "없음"

        message = (
            f"인시던트 시작: {started_at}\n"
            f"인시던트 종료: {resolved_at}\n"
            f"지속 시간: {duration}초\n"
            f"영향 서비스: {', '.join(affected_services) if affected_services else '없음'}\n"
            f"권장 조치: {recommendations_summary}"
        )

        payload = NotificationPayload(
            title=f"📋 Post-mortem 생성: {incident_id}",
            message=message,
            priority=priority,
            category=NotificationCategory.OPERATIONS,
            source="EventHandler.Postmortem",
            metadata={
                "incident_id": incident_id,
                "service_name": service_name,
                "duration_seconds": duration,
                "affected_services": affected_services,
                "resolved_at": resolved_at,
                "postmortem_url": f"/api/xtest/incidents/{incident_id}/",
            },
            dedup_key=f"postmortem:{incident_id}",
        )

        manager = UnifiedNotificationManager()
        result = manager.notify(payload)

        if result.success and not result.suppressed:
            logger.info(f"[Notification] Postmortem notification sent for {incident_id}")
        elif result.suppressed:
            logger.debug(
                f"[Notification] Postmortem notification suppressed for {incident_id}: " f"{result.suppression_reason}"
            )

    except Exception as e:
        # 알림 실패가 시스템에 영향을 주지 않도록 함
        logger.warning(f"[Notification] Failed to send postmortem notification: {e}")


def _on_circuit_breaker_closed(event: SelfHealingEvent):
    """
    CB 복구 시 자동 Replay 트리거 (Track 1).

    CRITICAL 우선순위의 PostRecoveryIntegrityGate
    (integrity_gate.py)가 이 핸들러보다 먼저 실행되어
    event.data[INTEGRITY_FAILED_KEY] 플래그를 설정합니다.
    플래그가 True인 경우 리플레이를 차단합니다.

    RuntimeConfig에서 track1_enabled 설정을 확인하고,
    활성화된 경우 conditional_replay_on_circuit_close 태스크를 트리거합니다.
    """
    service_name = event.data.get("service_name", "unknown")

    # IntegrityGate 결과 확인 (상수 import로 오타 방지)
    try:
        from selfhealing.services.event_bus.integrity_gate import INTEGRITY_FAILED_KEY

        if event.data.get(INTEGRITY_FAILED_KEY, False):
            logger.critical(
                f"[EventHandler] Replay BLOCKED for {service_name}: "
                f"integrity gate failed. "
                f"Details: {event.data.get('integrity_gate_result', {})}"
            )
            return  # 리플레이 중단
    except ImportError:
        pass  # integrity_gate 모듈 미설치 시 무시

    # RuntimeConfig에서 replay_automation 설정 로드
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager._get_config("replay_automation")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to get replay_automation config: {e}")
        config = {}

    # Track 1 활성화 여부 확인 (기본값: True)
    track1_enabled = config.get("track1_enabled", True)

    if not track1_enabled:
        logger.info(f"[EventHandler] Circuit breaker closed for {service_name}, " f"Track 1 disabled - skipping auto replay")
        return

    max_items = config.get("track1_max_items", 50)

    # Celery 태스크 트리거
    try:
        from selfhealing.adapters.celery.tasks import (
            conditional_replay_on_circuit_close,
        )

        conditional_replay_on_circuit_close.delay(
            service_name=service_name,
            max_items=max_items,
        )
        logger.info(
            f"[EventHandler] Circuit breaker closed for {service_name}, "
            f"triggered Track 1 auto replay (max_items={max_items})"
        )
    except ImportError:
        logger.debug(f"[EventHandler] Celery tasks not available, " f"skipping Track 1 replay for {service_name}")
    except Exception as e:
        logger.error(f"[EventHandler] Failed to trigger Track 1 replay for {service_name}: {e}")


def _on_circuit_breaker_closed_postmortem(event: SelfHealingEvent):
    """
    CB 복구 시 자동 Post-mortem 생성.

    Settings에서 auto_enabled가 True인 경우에만 동작합니다.
    incident_group_enabled가 True이면 IncidentGroupManager를 통해 그룹화합니다.
    그룹화된 경우 그룹 종료 시 통합 Postmortem이 생성됩니다.
    """
    service_name = event.data.get("service_name", "unknown")

    # Settings에서 자동 생성 활성화 여부 확인
    try:
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.auto_enabled:
            logger.debug(f"[EventHandler] Auto postmortem disabled, skipping for {service_name}")
            return

        min_duration = settings.auto_min_duration
        history_limit = settings.history_limit

        # 인시던트 그룹핑 활성화 여부 확인
        incident_group_enabled = getattr(settings, "incident_group_enabled", True)
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to get postmortem settings: {e}")
        return

    # 인시던트 그룹핑 처리
    if incident_group_enabled:
        try:
            _handle_incident_group(event, service_name, settings)
            return  # 그룹핑 시 즉시 Postmortem 생성 안함 (close_incident_group task에서 처리)
        except Exception as e:
            logger.warning(f"[EventHandler] Incident grouping failed, fallback to individual: {e}")
            # Fallback: Celery task로 개별 Postmortem 위임

    # 개별 Post-mortem 생성을 Celery task로 위임
    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        # bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        web_metrics = _collect_web_server_metrics()

        # event.to_dict()로 직렬화 — Celery JSON serializer 호환
        process_individual_postmortem.delay(
            service_name=service_name,
            event_data=event.to_dict(),
            event_type="circuit_breaker_closed",
            event_bus_history=event_bus_history,
            web_server_metrics=web_metrics,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback
        _create_individual_postmortem(event, service_name, settings, min_duration, history_limit)
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to enqueue postmortem: {e}")


def _handle_incident_group(event: SelfHealingEvent, service_name: str, settings) -> None:
    """
    CB CLOSED 이벤트를 IncidentGroup에 추가.

    새 그룹 생성 시 종료 타이머를 스케줄링합니다.
    """
    from selfhealing.services.postmortem.incident_group import get_incident_group_manager

    manager = get_incident_group_manager()
    namespace = event.data.get("namespace", "default")

    # 그룹에 인시던트 추가
    group_id, is_new_group = manager.add_incident(
        service_name=service_name,
        event=event,
        namespace=namespace,
    )

    if is_new_group:
        # 새 그룹 생성 시 종료 타이머 스케줄링
        _schedule_group_close(group_id, namespace, settings)
        logger.info(f"[EventHandler] New incident group created: {group_id} " f"(service={service_name})")
    else:
        logger.info(f"[EventHandler] Incident added to existing group: {group_id} " f"(service={service_name})")


def _schedule_group_close(group_id: str, namespace: str, settings) -> None:
    """그룹 종료 Celery 태스크 스케줄링."""
    try:
        from selfhealing.adapters.celery.tasks import close_incident_group

        window_seconds = getattr(settings, "incident_group_window_seconds", 600)

        # 윈도우 종료 후 그룹 종료 태스크 실행
        close_incident_group.apply_async(
            kwargs={
                "group_id": group_id,
                "namespace": namespace,
            },
            countdown=window_seconds,
        )

        logger.debug(f"[EventHandler] Scheduled group close: {group_id} " f"(delay={window_seconds}s)")

    except ImportError:
        logger.debug("[EventHandler] Celery tasks not available, skipping group close scheduling")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to schedule group close: {e}")


def _create_individual_postmortem(
    event: SelfHealingEvent,
    service_name: str,
    settings,
    min_duration: int,
    history_limit: int,
) -> None:
    """개별 Post-mortem 생성 (그룹핑 비활성화 또는 Fallback 시)."""
    try:
        from selfhealing.api.django.views.xtest.base import (
            collect_system_snapshot,
            get_healing_events,
        )
        from selfhealing.services.postmortem_store import (
            add_healing_incident,
            build_timeline as _build_timeline,
            collect_service_states as _collect_service_states,
            generate_postmortem_data as _generate_postmortem_data,
        )
        from selfhealing.services.circuit_breaker_service import (
            get_circuit_breaker_service,
        )

        # 히스토리 및 상태 수집
        bus = get_event_bus()
        history = bus.get_history(limit=history_limit)
        cb_service = get_circuit_breaker_service()
        affected, unaffected = _collect_service_states(cb_service)
        local_events = get_healing_events(20)
        timeline = _build_timeline(history, local_events)
        snapshot = collect_system_snapshot()

        # Fast fail 카운트
        fast_fail_count = len([e for e in history if e.get("data", {}).get("fast_fail")])

        # 인시던트 ID 생성
        from django.utils import timezone

        incident_id = f"AUTO-{service_name}-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

        # Post-mortem 생성
        postmortem = _generate_postmortem_data(incident_id, timeline, affected, unaffected, fast_fail_count, snapshot)

        # 최소 duration 확인
        duration = postmortem.get("duration_seconds")
        if duration is not None and duration < min_duration:
            logger.debug(
                f"[EventHandler] Auto postmortem skipped for {service_name}: "
                f"duration {duration:.0f}s < min {min_duration}s"
            )
            return

        # 무결성 봉인
        try:
            from selfhealing.services.postmortem.integrity_sealer import get_integrity_sealer

            sealer = get_integrity_sealer()
            postmortem = sealer.seal(postmortem)
        except Exception as seal_error:
            logger.warning(f"[EventHandler] Integrity seal failed: {seal_error}")

        # 저장
        add_healing_incident(postmortem)

        logger.info(f"[EventHandler] Auto postmortem generated: {incident_id} " f"(duration={duration}s)")

        # Post-mortem 알림 발송
        _send_postmortem_notification(settings, postmortem, incident_id, service_name, duration, affected)

        # WAL Audit 기록 - 자동 Post-mortem 생성 이벤트
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="POSTMORTEM_AUTO_GENERATED",
                source="EventHandler.Postmortem",
                details={
                    "incident_id": incident_id,
                    "service_name": service_name,
                    "duration_seconds": duration,
                    "affected_services": affected,
                    "trigger_event": event.event_type.value,
                },
                success=True,
                domain="selfhealing",
                target_id=incident_id,
            )
        except Exception as audit_error:
            logger.warning(f"[EventHandler] Failed to log postmortem audit: {audit_error}")

    except ImportError:
        logger.debug("[EventHandler] Postmortem module not available, skipping auto generation")
    except Exception as e:
        logger.error(f"[EventHandler] Failed to generate auto postmortem: {e}")


def _build_emergency_timeline(event_bus_history: list) -> list:
    """Emergency 관련 타임라인 구성."""
    timeline = []
    emergency_event_types = [
        "emergency_activated",
        "emergency_recovery_started",
        "emergency_recovery_completed",
        "emergency_level_changed",
    ]

    for event in event_bus_history:
        event_type = event.get("event_type", "").lower()
        if any(etype in event_type for etype in emergency_event_types):
            timeline.append(
                {
                    "timestamp": event.get("timestamp"),
                    "event_type": event.get("event_type"),
                    "details": event.get("data", {}),
                }
            )

    # CB 이벤트도 포함 (Emergency 중 발생한 것)
    cb_events = [e for e in event_bus_history if "circuit_breaker" in e.get("event_type", "").lower()]
    for event in cb_events[:10]:
        timeline.append(
            {
                "timestamp": event.get("timestamp"),
                "event_type": event.get("event_type"),
                "details": event.get("data", {}),
            }
        )

    # 시간순 정렬
    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
    return timeline


def _build_recovery_steps(steps_executed: int) -> list:
    """복구 단계 정보 추출."""
    step_types = ["BUDGET_RESET", "HEALTH_CHECK", "CANARY_RESUME", "GOVERNANCE_NORMAL"]
    recovery_steps = []
    for i in range(min(steps_executed, len(step_types))):
        recovery_steps.append(
            {
                "step_order": i + 1,
                "step_type": step_types[i] if i < len(step_types) else f"STEP_{i+1}",
                "status": "COMPLETED",
            }
        )
    return recovery_steps


def _build_emergency_actions(
    trigger_level: str,
    steps_executed: int,
    requires_approval: bool,
    approved_by: str | None,
) -> tuple[list, list]:
    """Emergency 동적 Action Items 및 권장사항 생성."""
    auto_actions = []
    recommendations = []

    if trigger_level == "LEVEL_3":
        auto_actions.append(
            {
                "action": "GOVERNANCE_NORMALIZED",
                "description": "자동화 재활성화 (STRICT → NORMAL)",
                "status": "completed",
            }
        )
        recommendations.append("LEVEL_3 장애 원인 분석 및 재발 방지 대책 수립")

    if steps_executed > 0:
        auto_actions.append(
            {
                "action": "BUDGET_RESET",
                "description": "Crisis Multiplier 정상화 (1.0x)",
                "status": "completed",
            }
        )

    if requires_approval:
        auto_actions.append(
            {
                "action": "MANUAL_APPROVAL",
                "description": f"수동 승인 완료 (승인자: {approved_by or 'unknown'})",
                "status": "completed",
            }
        )
        recommendations.append("수동 승인 프로세스 검토 및 자동화 가능 여부 평가")

    recommendations.append(f"Emergency {trigger_level} 발생 원인 분석")
    recommendations.append("복구 프로세스 시간 단축 방안 검토")

    return auto_actions, recommendations


def _collect_emergency_cascade_event_data(namespace: str) -> tuple[str | None, list[str], str | None]:
    """Emergency CascadeEvent 감사 증적 수집."""
    cascade_event_id = None
    causation_chain: list[str] = []
    evidence_hash = None
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()
        recent_events = auditor.get_recent_events(namespace=namespace, limit=50)

        for event in recent_events:
            if "EMERGENCY" in event.trigger.trigger_type:
                cascade_event_id = event.id
                causation_chain = event.get_causation_chain()
                evidence_hash = event.current_hash
                break
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Failed to collect cascade event data: {e}")
    return cascade_event_id, causation_chain, evidence_hash


def _build_emergency_deep_links(
    incident_id: str,
    namespace: str,
    started_at: str | None,
    completed_at: str | None,
    cascade_event_id: str | None,
    evidence_hash: str | None,
) -> dict:
    """Emergency 딥링크 생성."""
    try:
        from selfhealing.services.postmortem.deep_links import get_postmortem_deep_link_builder

        deep_link_builder = get_postmortem_deep_link_builder()
        postmortem_links = deep_link_builder.build_postmortem_links(
            incident_id=incident_id,
            service_name=f"emergency-{namespace}",
            start_time=started_at,
            end_time=completed_at,
            namespace=namespace,
            cascade_event_id=cascade_event_id,
            evidence_hash=evidence_hash,
        )
        return postmortem_links.to_dict()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Failed to build deep links: {e}")
    return {}


def _generate_emergency_postmortem_data(
    session_data: dict,
    event_bus_history: list,
    snapshot: dict,
) -> dict:
    """
    Emergency 복구 완료 시 Postmortem 데이터 생성.

    CB Postmortem과 달리 Emergency Postmortem은 리전/글로벌 장애에 대한
    복구 세션 정보를 기반으로 생성됩니다.

    Args:
        session_data: EMERGENCY_RECOVERY_COMPLETED 이벤트에서 전달된 세션 정보
        event_bus_history: EventBus 히스토리
        snapshot: 시스템 스냅샷

    Returns:
        Emergency Postmortem 데이터 딕셔너리
    """
    from datetime import datetime, timezone as dt_timezone

    # 세션 데이터 추출
    session_id = session_data.get("session_id", "unknown")
    namespace = session_data.get("namespace", "global")
    trigger_level = session_data.get("trigger_level", "UNKNOWN")
    started_at = session_data.get("started_at")
    completed_at = session_data.get("completed_at")
    duration_seconds = session_data.get("duration_seconds")
    steps_executed = session_data.get("steps_executed", 0)
    total_steps = session_data.get("total_steps", 0)
    requires_approval = session_data.get("requires_approval", False)
    approved_by = session_data.get("approved_by")

    now = datetime.now(dt_timezone.utc)
    current_time = now.isoformat()
    incident_id = f"EMERGENCY-{namespace}-{now.strftime('%Y%m%d-%H%M%S')}"

    # 헬퍼 함수들을 사용하여 데이터 수집
    timeline = _build_emergency_timeline(event_bus_history)
    recovery_steps = _build_recovery_steps(steps_executed)
    auto_actions, recommendations = _build_emergency_actions(trigger_level, steps_executed, requires_approval, approved_by)
    cascade_event_id, causation_chain, evidence_hash = _collect_emergency_cascade_event_data(namespace)
    deep_links = _build_emergency_deep_links(incident_id, namespace, started_at, completed_at, cascade_event_id, evidence_hash)

    return {
        "incident_id": incident_id,
        "generated_at": current_time,
        "started_at": started_at,
        "resolved_at": completed_at,
        "duration_seconds": duration_seconds,
        # Emergency 전용 필드
        "recovery_type": "emergency",
        "namespace": namespace,
        "trigger_level": trigger_level,
        "recovery_session_id": session_id,
        "recovery_steps": recovery_steps,
        "requires_approval": requires_approval,
        "approved_by": approved_by,
        # 공통 필드
        "summary": {
            "affected_services": [],
            "unaffected_services": [],
            "fast_fail_count": 0,
            "total_events": len(timeline),
            "steps_executed": steps_executed,
            "total_steps": total_steps,
        },
        "timeline": timeline[:30],
        "system_snapshot": snapshot,
        "auto_actions": auto_actions,
        "recommendations": recommendations,
        "deep_links": deep_links,
        "cascade_event_id": cascade_event_id,
        "causation_chain": causation_chain,
        "evidence_hash": evidence_hash,
    }


def _on_emergency_recovery_completed_postmortem(event: SelfHealingEvent):
    """
    Emergency 복구 완료 시 Postmortem 생성을 Celery Task로 위임.

    RecoveryCoordinator가 복구를 완료하면 EMERGENCY_RECOVERY_COMPLETED 이벤트가
    발행되고, 스냅샷 수집/DB 저장/WAL 기록/알림 발송을 Celery Worker에 위임한다.

    Settings 검증과 min_duration 체크만 동기로 수행 (빠름).
    Celery 미설치 환경에서는 기존 동기 방식으로 자동 fallback.
    """
    session_id = event.data.get("session_id", "unknown")
    namespace = event.data.get("namespace", "global")
    trigger_level = event.data.get("trigger_level", "UNKNOWN")
    duration = event.data.get("duration_seconds")

    # Settings에서 자동 생성 활성화 여부 확인
    try:
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.auto_enabled:
            logger.debug(f"[EventHandler] Auto postmortem disabled, " f"skipping for Emergency session {session_id}")
            return

        min_duration = settings.auto_min_duration
        history_limit = settings.history_limit
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to get postmortem settings: {e}")
        return

    # 최소 duration 확인 (빠른 체크, I/O 없음)
    if duration is not None and duration < min_duration:
        logger.debug(
            f"[EventHandler] Emergency postmortem skipped for {session_id}: " f"duration {duration:.0f}s < min {min_duration}s"
        )
        return

    # Celery Task로 위임
    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        # bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        web_metrics = _collect_web_server_metrics()

        process_individual_postmortem.delay(
            service_name=f"emergency-{namespace}",
            event_data=event.to_dict(),
            event_type="emergency_recovery_completed",
            event_bus_history=event_bus_history,
            web_server_metrics=web_metrics,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback
        _create_emergency_postmortem_sync(event, namespace, history_limit)
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to enqueue emergency postmortem: {e}")


def _create_emergency_postmortem_sync(
    event: SelfHealingEvent,
    namespace: str,
    history_limit: int,
) -> None:
    """Emergency Postmortem 동기 생성 (Celery 미설치 환경 Fallback)."""
    session_id = event.data.get("session_id", "unknown")
    trigger_level = event.data.get("trigger_level", "UNKNOWN")
    duration = event.data.get("duration_seconds")

    try:
        from selfhealing.api.django.views.xtest.base import collect_system_snapshot
        from selfhealing.services.postmortem_store import add_healing_incident

        # 히스토리 및 스냅샷 수집
        bus = get_event_bus()
        history = bus.get_history(limit=history_limit)
        snapshot = collect_system_snapshot()

        # Emergency Postmortem 데이터 생성
        postmortem = _generate_emergency_postmortem_data(
            session_data=event.data,
            event_bus_history=history,
            snapshot=snapshot,
        )

        # 저장
        add_healing_incident(postmortem)

        incident_id = postmortem.get("incident_id")
        logger.info(
            f"[EventHandler] Emergency postmortem generated: {incident_id} "
            f"(session={session_id}, level={trigger_level}, duration={duration}s)"
        )

        # WAL Audit 기록
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="EMERGENCY_POSTMORTEM_AUTO_GENERATED",
                source="EventHandler.EmergencyPostmortem",
                details={
                    "incident_id": incident_id,
                    "session_id": session_id,
                    "namespace": namespace,
                    "trigger_level": trigger_level,
                    "duration_seconds": duration,
                    "requires_approval": event.data.get("requires_approval", False),
                    "approved_by": event.data.get("approved_by"),
                },
                success=True,
                domain="selfhealing",
                target_id=incident_id,
            )
        except Exception as audit_error:
            logger.warning(f"[EventHandler] Failed to log emergency postmortem audit: {audit_error}")

        # Postmortem 알림 발송
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            settings = get_postmortem_settings()
            _send_postmortem_notification(
                settings=settings,
                postmortem=postmortem,
                incident_id=incident_id,
                service_name=f"emergency-{namespace}",
                duration=duration,
                affected_services=[],
            )
        except Exception as notify_error:
            logger.warning(f"[EventHandler] Failed to send emergency postmortem notification: " f"{notify_error}")

    except ImportError as e:
        logger.debug(f"[EventHandler] Module not available for Emergency postmortem: {e}")
    except Exception as e:
        logger.error(f"[EventHandler] Failed to generate Emergency postmortem: {e}")


# =============================================================================
# Throttle Event Handlers
# =============================================================================


def _on_emergency_level_changed_throttle(event: SelfHealingEvent) -> None:
    """
    Emergency 레벨 변경 시 Throttle limit 자동 조정.

    AdaptiveThrottle.adjust_for_emergency() 메서드를 호출하여
    Emergency Level에 따른 limit 배율 및 Gradient Freeze를 처리합니다.

    Emergency Level별 limit 배율:
    - NORMAL (0): 1.0 (전체 용량, 복구)
    - LEVEL_1 (1): 0.8 (80% 용량)
    - LEVEL_2 (2): 0.5 (50% 용량)
    - LEVEL_3 (3): min_limit 고정 + Gradient Freeze
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    level = event.data.get("level", 0)
    previous_level = event.data.get("previous_level", 0)

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # adjust_for_emergency 메서드로 통합 처리
        throttle.adjust_for_emergency(level)

        logger.info(
            f"[Throttle] Emergency level {previous_level} → {level}, " f"limit: {previous_limit} → {throttle.current_limit}"
        )
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for emergency: {e}")


def _on_emergency_deactivated_throttle(event: SelfHealingEvent) -> None:
    """
    Emergency 비활성화 시 Throttle limit 복구.

    Emergency Mode가 완전히 비활성화될 때 호출됩니다.
    adjust_for_emergency(0)을 호출하여 limit을 복구합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # level 0으로 복구
        throttle.adjust_for_emergency(0)

        logger.info(f"[Throttle] Emergency deactivated, " f"limit restored: {previous_limit} → {throttle.current_limit}")
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to restore throttle after emergency: {e}")


def _on_circuit_breaker_opened_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker OPEN 시 해당 서비스 limit을 min_limit으로 고정.

    CB가 열리면 해당 서비스가 불안정한 상태이므로
    Throttle limit을 min_limit으로 즉시 강등합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            _record_throttle_metrics,
            _record_audit_safe,
        )

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # min_limit으로 강등
        throttle.current_limit = throttle.config.min_limit

        # CB 조정 메트릭 기록
        _record_throttle_metrics(
            service=service_name,
            limit=throttle.current_limit,
            cb_state="open",
        )

        # CB 조정 감사 로그 기록
        _record_audit_safe(
            action="throttle_cb_sync",
            old_limit=previous_limit,
            new_limit=throttle.current_limit,
            service_name=service_name,
            cb_state="open",
        )

        logger.info(f"[Throttle] CB OPEN for {service_name}, " f"limit: {previous_limit} → {throttle.current_limit}")
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for CB OPEN: {e}")


def _on_circuit_breaker_half_opened_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker HALF_OPEN 시 limit을 initial_limit의 50%로 설정.

    CB가 OPEN에서 HALF_OPEN으로 전이되면 서비스 복구를 테스트하는 단계이므로
    제한적인 트래픽만 허용합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # initial_limit × 0.5 (제한적 트래픽 허용)
        half_open_limit = int(throttle.config.initial_limit * 0.5)
        throttle.current_limit = half_open_limit

        # CB 조정 메트릭 기록
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            _record_throttle_metrics(
                service=service_name,
                limit=throttle.current_limit,
                cb_state="half_open",
            )
        except ImportError:
            pass

        logger.info(
            f"[Throttle] CB HALF_OPEN for {service_name}, "
            f"limit: {previous_limit} → {throttle.current_limit} (recovery test mode)"
        )
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for CB HALF_OPEN: {e}")


def _on_circuit_breaker_closed_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker CLOSED 시 limit 제한 해제.

    CB가 닫히면 서비스가 정상화되었으므로
    limit 제한을 해제하고 점진적 복구를 시작합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # initial_limit으로 복원 (점진적 증가 시작점)
        throttle.current_limit = throttle.config.initial_limit

        # CB 조정 메트릭 기록
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            _record_throttle_metrics(
                service=service_name,
                limit=throttle.current_limit,
                cb_state="closed",
            )
        except ImportError:
            pass

        logger.info(
            f"[Throttle] CB CLOSED for {service_name}, " f"limit: {previous_limit} → {throttle.current_limit} (recovery mode)"
        )
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for CB CLOSED: {e}")


def _on_error_budget_critical_throttle(event: SelfHealingEvent) -> None:
    """
    Error Budget Critical 시 limit을 보수적으로 조정 (×0.5).

    Error Budget이 임계치 이하로 떨어지면
    추가 오류 발생을 방지하기 위해 limit을 절반으로 줄입니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    budget_percent = event.data.get("budget_percent", 0)

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # limit × 0.5 (보수적 조정)
        new_limit = int(previous_limit * 0.5)
        throttle.current_limit = new_limit

        logger.warning(
            f"[Throttle] Error budget critical ({budget_percent:.1f}%), "
            f"limit: {previous_limit} → {throttle.current_limit} (×0.5)"
        )
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for error budget: {e}")


def _on_error_budget_recovered_throttle(event: SelfHealingEvent) -> None:
    """
    Error Budget 회복 시 Recovery Dampening 시작.

    즉시 initial_limit으로 복구하지 않고
    Recovery Dampening을 통해 점진적으로 복구합니다.
    이는 '요요 현상'을 방지하고 안정적인 복구를 보장합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()

        # Recovery Dampening 시작 (Jitter 적용)
        throttle.start_recovery_dampening(apply_jitter=True)

        logger.info(f"[Throttle] Error budget recovered, " f"starting recovery dampening from {throttle.current_limit}")
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to start recovery dampening: {e}")


def _on_kill_switch_activated_throttle(event: SelfHealingEvent) -> None:
    """
    Kill Switch 활성화 시 Throttle 기능 일시 중지.

    Kill Switch가 활성화되면 모든 자동화 기능이 중지되어야 하므로
    Throttle도 min_limit으로 고정합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # min_limit으로 고정
        throttle.current_limit = throttle.config.min_limit

        logger.warning(f"[Throttle] Kill switch activated, " f"limit: {previous_limit} → {throttle.current_limit}")
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to adjust throttle for kill switch: {e}")


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
