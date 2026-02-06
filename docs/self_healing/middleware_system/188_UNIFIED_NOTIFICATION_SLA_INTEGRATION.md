# 188. Unified Notification - SLA 위반 알림 연동 구현

> **문서 버전**: 2.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/unified_notification.py`, `selfhealing/services/throttle/adaptive.py`, `selfhealing/services/circuit_breaker/actionable_alert_urls.py`, `selfhealing/core/cluster_identity.py`, `selfhealing/meta/fallback_escalation.py`

## 1. 개요

본 문서는 `AdaptiveThrottle`의 SLA 위반 시 `UnifiedNotificationManager`를 통한 알림 발송 연동을 정의합니다.

### 1.1 문제 정의

현재 `AdaptiveThrottle`은 SLA 위반 시 **EventBus 이벤트만 발행**:
- `THROTTLE_SLA_WARNING`: 경고 임계값 도달
- `THROTTLE_SLA_CRITICAL`: 위험 임계값 도달

**문제점**: 운영팀에 대한 직접적인 알림(Slack, Email, PagerDuty) 연동 부재

### 1.2 v2.0 개선 범위

| # | 개선 항목 | 근거 |
|---|---------|------|
| 1 | 동적 URL 빌더 (`ThrottleSlaAlertUrlBuilder`) | CB/Chaos `os.getenv` 기반 URL 빌더 패턴 (`actionable_alert_urls.py`) |
| 2 | 리전 정보 자동 주입 | `ClusterIdentity.region` (`cluster_identity.py`) |
| 3 | 서비스 단위 Dedup 키 세분화 | CB 패턴 `cb:{service_name}:open` (`event_bus.py` L611) |
| 4 | 쿨다운 Redis 영속성 | `RegionalIsolationGate` Redis persist 패턴 (`regional_gate.py`) |
| 5 | 발송 실패 폴백 파이프라인 | `FallbackEscalationHandler` 3단 폴백 (`fallback_escalation.py`) |
| 6 | Celery 비동기 전송 | `close_incident_group.apply_async` 패턴 (`event_bus.py` L901) |
| 7 | 메시지 템플릿 분리 | `recovery_notifications.py` 순수 함수 패턴 |
| 8 | RTT 변화율(%) 정보 강화 | `GradientCalculator._smoothed_rtt` (`adaptive.py`) |
| 9 | Meta-Watchdog 경계 문서화 | `EscalationManager` vs `UnifiedNotificationManager` 역할 분리 |
| 10 | Graceful Shutdown (Celery warm shutdown 종속) | `RecoveryAwareShutdownHook` 패턴 (`recovery_shutdown.py`) |

---

## 2. 현재 구현 분석

### 2.1 AdaptiveThrottle SLA 이벤트 발행

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) (Line 458-523)

```python
def _maybe_adjust_limit(self, rtt_ms: float) -> None:
    """Adjust limit based on gradient and SLA thresholds."""
    # ...

    # SLA-based aggressive throttling
    if rtt_ms >= self.config.sla_critical_ms:
        # Critical: aggressive reduction
        self._adaptive_stats["sla_criticals"] += 1
        new_limit = int(self._current_limit * 0.7)  # -30%

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
        return

    if rtt_ms >= self.config.sla_warning_ms:
        # Warning: moderate reduction
        self._adaptive_stats["sla_warnings"] += 1

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
```

### 2.2 UnifiedNotificationManager 구조

**코드 위치**: [unified_notification.py](../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py)

```python
class NotificationPriority(str, Enum):
    """Notification priority levels."""
    CRITICAL = "critical"  # Immediate: all channels
    HIGH = "high"          # Urgent: Slack + Email
    MEDIUM = "medium"      # Normal: Slack only
    LOW = "low"            # Can be batched
    INFO = "info"          # Log only unless configured

class NotificationCategory(str, Enum):
    """Notification categories for routing and filtering."""
    SECURITY = "security"
    OPERATIONS = "operations"
    SLA = "sla"               # ← SLA 관련 알림용 카테고리
    CIRCUIT_BREAKER = "circuit_breaker"
    # ...

@dataclass
class NotificationPayload:
    """Unified notification payload."""
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    category: NotificationCategory = NotificationCategory.OPERATIONS
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    dedup_key: str | None = None  # Cooldown dedup용
```

**RoutingPolicy 기본 설정**:

```python
@dataclass
class RoutingPolicy:
    """Notification routing policy."""

    # Channel mapping by priority
    priority_channels: dict[NotificationPriority, list[str]] = field(
        default_factory=lambda: {
            NotificationPriority.CRITICAL: ["slack", "email", "sms", "pagerduty"],
            NotificationPriority.HIGH: ["slack", "email"],
            NotificationPriority.MEDIUM: ["slack"],
            NotificationPriority.LOW: ["slack"],
            NotificationPriority.INFO: [],  # Log only
        }
    )

    # Category-specific overrides
    category_channels: dict[NotificationCategory, list[str]] = field(
        default_factory=lambda: {
            NotificationCategory.SECURITY: ["slack", "email"],
            NotificationCategory.APPROVAL: ["slack", "email"],
            NotificationCategory.REPORT: ["slack", "email"],
        }
    )

    # Cooldown settings by category
    cooldown_seconds: dict[NotificationCategory, int] = field(
        default_factory=lambda: {
            NotificationCategory.SLA: 1800,  # 30분 cooldown
            # ...
        }
    )
```

---

## 3. 연동 설계

### 3.1 아키텍처

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                        SLA 위반 알림 아키텍처 (v2.0)                                      │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  ┌───────────────────┐                                                                   │
│  │ AdaptiveThrottle  │                                                                   │
│  │                   │                                                                   │
│  │ record_response() │  rtt_change_percent 계산 포함                                      │
│  │       │           │                                                                   │
│  │       ▼           │                                                                   │
│  │ _maybe_adjust_    │                                                                   │
│  │   limit()         │                                                                   │
│  └─────────┬─────────┘                                                                   │
│            │                                                                             │
│            │ SLA 위반 감지                                                                │
│            ▼                                                                             │
│  ┌───────────────────┐    emit()     ┌───────────────────┐                               │
│  │ _emit_throttle_   │ ───────────► │     EventBus       │  (동기 in-process)             │
│  │   event()         │               └─────────┬─────────┘                               │
│  └───────────────────┘                         │                                         │
│                                                │ subscribe                               │
│                                                ▼                                         │
│                          ┌─────────────────────────────────────┐                         │
│                          │   SLA Notification Handler          │                         │
│                          │                                     │                         │
│                          │   _handle_sla_warning()             │                         │
│                          │   _handle_sla_critical()            │                         │
│                          │   + ClusterIdentity.region 주입     │                         │
│                          └──────────────┬──────────────────────┘                         │
│                                         │                                                │
│                                         │ apply_async (비동기 위임)                       │
│                                         ▼                                                │
│                          ┌─────────────────────────────────────┐                         │
│                          │     Celery Task Layer               │                         │
│                          │   send_sla_notification.delay()     │                         │
│                          │   - autoretry (max_retries=3)       │                         │
│                          │   - Warm Shutdown 보장              │                         │
│                          └──────────────┬──────────────────────┘                         │
│                                         │                                                │
│                                         │ notify()                                       │
│                                         ▼                                                │
│                          ┌─────────────────────────────────────┐                         │
│                          │  UnifiedNotificationManager         │                         │
│                          │                                     │                         │
│                          │  - Redis Cooldown (영속적)           │                         │
│                          │  - Priority Escalation              │                         │
│                          │  - Channel Routing                  │                         │
│                          │  - ThrottleSlaAlertUrlBuilder       │                         │
│                          └──────────────┬──────────────────────┘                         │
│                                         │                                                │
│                  ┌──────────────────────┼──────────────────────┐                        │
│                  │                      │                      │                        │
│                  ▼                      ▼                      ▼                        │
│           ┌──────────┐          ┌──────────┐          ┌──────────┐                     │
│           │  Slack   │          │  Email   │          │ PagerDuty│                     │
│           └──────────┘          └──────────┘          └──────────┘                     │
│                  │                                                                      │
│                  ▼  (전송 실패 시)                                                       │
│           ┌────────────────────────────────────────┐                                    │
│           │  NotificationFallbackRecorder          │                                    │
│           │  emergency_notifications.jsonl          │                                    │
│           └────────────────────────────────────────┘                                    │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 책임 분리 원칙 (Meta-Watchdog vs UnifiedNotification)

두 계층은 구조적으로 명확히 분리되며, 중복 구현을 방지합니다.

**코드 근거**:
- Meta-Watchdog `_escalate()` ([watchdog.py](../../packages/selfhealing-python/src/selfhealing/meta/watchdog.py) L527-560): 자체 `EscalationManager` 사용
- CB 알림 핸들러 ([event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) L600-625): `get_unified_notification_manager().notify()` 사용

| 구분 | Meta-Watchdog | UnifiedNotificationManager |
|------|--------------|---------------------------|
| **역할** | Self-Healing 인프라 감시 + 복구 실패 에스컬레이션 | 비즈니스 이벤트 알림 라우팅 |
| **대상** | CB/DLQ/Redis/RecoveryPipeline 등 **인프라 컴포넌트** | SLA/보안/CB상태변경 등 **비즈니스 이벤트** |
| **전송 주체** | `EscalationManager` (자체, [escalation.py](../../packages/selfhealing-python/src/selfhealing/meta/escalation.py)) | `SecurityNotificationService` (공용) |
| **폴백** | `FallbackEscalationHandler` → 디스크 JSONL → 메모리 | `NotificationFallbackRecorder` → 디스크 JSONL (본 문서에서 신규) |
| **판단 주체** | Watchdog 프로브가 자체 판단 | 도메인 모듈(AdaptiveThrottle 등)이 판단, UNM은 전송만 |

> **원칙**: 향후 유사 알림 기능 도입 시, "인프라 자체 장애"는 Meta-Watchdog 계열에, "비즈니스 도메인 이벤트"는 UnifiedNotification 계열에 구현합니다.

### 3.3 알림 매핑 규칙

| Throttle 이벤트 | Notification Priority | Channels | Cooldown |
|----------------|----------------------|----------|----------|
| `THROTTLE_SLA_WARNING` | HIGH | Slack, Email | 30분 |
| `THROTTLE_SLA_CRITICAL` | CRITICAL | Slack, Email, SMS, PagerDuty | 30분 |
| `THROTTLE_LIMIT_RECOVERED` | MEDIUM | Slack | 없음 |

---

## 4. 구현 코드

### 4.1 SLA Notification Handler (비동기 + 리전 주입)

**신규 파일**: `selfhealing/services/throttle/sla_notification.py`

v2.0 변경사항:
- **Celery 비동기 전송**: EventBus 동기 핸들러에서 `apply_async`로 위임하여 EventBus 루프 블로킹 방지
- **리전 자동 주입**: `ClusterIdentity.region`을 매 알림에 자동 포함하여 Multi-Region 환경 지원
- **서비스 단위 Dedup**: `sla:throttle:{service_name}`으로 세분화하여 서비스별 독립 쿨다운
- **RTT 변화율(%)**: 이벤트 데이터의 `rtt_change_percent` 필드를 메시지에 포함

**코드 근거**:
- Celery `apply_async` 패턴: [event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) L901 `close_incident_group.apply_async()`
- 리전 정보: [cluster_identity.py](../../packages/selfhealing-python/src/selfhealing/core/cluster_identity.py) L35-48 `ClusterIdentity.region`
- Dedup 패턴: [event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) L611 `dedup_key=f"cb:{service_name}:open"`

```python
"""
SLA 위반 시 UnifiedNotification 비동기 연동 모듈.

코드 근거:
- event_bus.py L901: close_incident_group.apply_async() 패턴
- cluster_identity.py: ClusterIdentity.region
- event_bus.py L611: dedup_key=f"cb:{service_name}:open" 패턴
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_region_safe() -> str | None:
    """
    현재 클러스터의 리전 정보를 안전하게 가져옵니다.

    코드 근거: cluster_identity.py get_cluster_identity()
    - 환경변수 SELFHEALING_REGION에서 읽음
    - region이 None이면 Quarantine Mode (단일 리전 배포)
    """
    try:
        from selfhealing.core.cluster_identity import get_cluster_identity

        identity = get_cluster_identity(skip_validation=True)
        return identity.region
    except ImportError:
        logger.debug("[SLANotification] ClusterIdentity not available")
        return None
    except Exception as e:
        logger.debug(f"[SLANotification] Failed to get region: {e}")
        return None


def _subscribe_sla_events() -> None:
    """
    SLA 이벤트 구독 등록.

    애플리케이션 시작 시 호출 필요.
    """
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus

        bus = get_event_bus()

        bus.subscribe(
            EventType.THROTTLE_SLA_WARNING,
            _handle_sla_warning,
        )
        bus.subscribe(
            EventType.THROTTLE_SLA_CRITICAL,
            _handle_sla_critical,
        )
        bus.subscribe(
            EventType.THROTTLE_LIMIT_RECOVERED,
            _handle_limit_recovered,
        )

        logger.info("[SLANotification] Subscribed to throttle SLA events")
    except ImportError:
        logger.debug("[SLANotification] EventBus not available")
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to subscribe: {e}")


def _handle_sla_warning(event) -> None:
    """
    SLA Warning 이벤트 처리 (비동기 위임).

    EventBus.publish()는 동기 순차 호출(event_bus.py L360-370)이므로,
    알림 HTTP 전송이 다른 핸들러를 블로킹하지 않도록 Celery에 위임합니다.

    코드 근거: event_bus.py L901 close_incident_group.apply_async() 패턴
    """
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "warning",
            },
        )
    except ImportError:
        # Celery 미사용 환경: 동기 fallback
        logger.debug("[SLANotification] Celery not available, using sync fallback")
        _send_sla_warning_sync(event.data)
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to dispatch warning: {e}")
        _send_sla_warning_sync(event.data)


def _handle_sla_critical(event) -> None:
    """
    SLA Critical 이벤트 처리 (비동기 위임).
    """
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "critical",
            },
        )
    except ImportError:
        logger.debug("[SLANotification] Celery not available, using sync fallback")
        _send_sla_critical_sync(event.data)
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to dispatch critical: {e}")
        _send_sla_critical_sync(event.data)


def _handle_limit_recovered(event) -> None:
    """
    Limit 복구 이벤트 처리 (비동기 위임).
    """
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "recovered",
            },
        )
    except ImportError:
        _send_limit_recovered_sync(event.data)
    except Exception as e:
        _send_limit_recovered_sync(event.data)


def _send_sla_warning_sync(event_data: dict[str, Any]) -> None:
    """
    SLA Warning 동기 전송.

    리전 자동 주입 + 서비스 단위 dedup_key 세분화 포함.
    """
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")
        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        gradient = event_data.get("gradient", 0)
        rtt_change_percent = event_data.get("rtt_change_percent")

        template = build_sla_warning_message(
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            current_limit=current_limit,
            previous_limit=previous_limit,
            gradient=gradient,
            rtt_change_percent=rtt_change_percent,
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            # dedup_key 서비스 단위 세분화 (CB 패턴: "cb:{service_name}:open")
            domain=f"throttle:{service_name}",
            priority="high",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
            },
        )

        if result.success:
            logger.info(f"[SLANotification] Warning sent: channels={result.channels_sent}")
        elif result.suppressed:
            logger.debug(f"[SLANotification] Warning suppressed: {result.suppression_reason}")
        else:
            logger.warning(f"[SLANotification] Warning failed: {result.error}")

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to send warning: {e}")


def _send_sla_critical_sync(event_data: dict[str, Any]) -> None:
    """
    SLA Critical 동기 전송.
    """
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")
        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        reduction_percent = event_data.get("reduction_percent", 0)
        gradient = event_data.get("gradient", 0)
        rtt_change_percent = event_data.get("rtt_change_percent")

        template = build_sla_critical_message(
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            current_limit=current_limit,
            previous_limit=previous_limit,
            reduction_percent=reduction_percent,
            gradient=gradient,
            rtt_change_percent=rtt_change_percent,
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            domain=f"throttle:{service_name}",
            priority="critical",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
                "requires_action": True,
            },
        )

        if result.success:
            logger.warning(f"[SLANotification] CRITICAL sent: channels={result.channels_sent}")
        else:
            logger.error(f"[SLANotification] CRITICAL failed: {result.error}")

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.error(f"[SLANotification] Failed to send critical: {e}")


def _send_limit_recovered_sync(event_data: dict[str, Any]) -> None:
    """
    Limit 복구 동기 전송.
    """
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")

        template = build_sla_recovered_message(
            previous_limit=event_data.get("previous_limit", 0),
            new_limit=event_data.get("new_limit", 0),
            rtt_ms=event_data.get("rtt_ms", 0),
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            domain=f"throttle:{service_name}",
            priority="medium",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
            },
        )

        if result.success:
            logger.info(f"[SLANotification] Recovery sent: channels={result.channels_sent}")

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.debug(f"[SLANotification] Failed to send recovery: {e}")


# 모듈 초기화 시 자동 구독
def initialize_sla_notifications() -> None:
    """SLA 알림 시스템 초기화."""
    _subscribe_sla_events()
```

### 4.2 Celery Task 정의

**신규 추가**: `selfhealing/adapters/celery/tasks.py` 내

**코드 근거**: 기존 `close_incident_group` 태스크 ([event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) L895-905)

```python
@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,                    # NotificationChannelSettings.max_retry=3
    default_retry_delay=30,           # NotificationChannelSettings.retry_delay_seconds=30
    acks_late=True,                   # Graceful Shutdown: Worker 종료 시 미완료 태스크 재큐잉
)
def send_sla_notification(
    self,
    event_data: dict,
    notification_type: str,
) -> dict:
    """
    SLA 알림 비동기 전송 태스크.

    autoretry_for: 전송 실패 시 자동 재시도 (max 3회, 30초 간격)
    acks_late: Pod 종료 시 미완료 태스크를 브로커에 반환 (Graceful Shutdown)

    코드 근거:
    - NotificationChannelSettings.max_retry=3 (notification_channel.py L73)
    - NotificationChannelSettings.retry_delay_seconds=30 (notification_channel.py L80)
    """
    from selfhealing.services.throttle.sla_notification import (
        _send_sla_critical_sync,
        _send_sla_warning_sync,
        _send_limit_recovered_sync,
    )

    dispatch = {
        "warning": _send_sla_warning_sync,
        "critical": _send_sla_critical_sync,
        "recovered": _send_limit_recovered_sync,
    }

    handler = dispatch.get(notification_type)
    if handler:
        handler(event_data)

    return {"status": "sent", "type": notification_type}
```

> **Graceful Shutdown (리뷰 10)**:
> `acks_late=True` 설정으로 Celery Worker가 SIGTERM을 수신하면, 현재 실행 중인 태스크가 완료될 때까지 대기 후 안전하게 종료합니다. 미처 처리하지 못한 태스크는 브로커(Redis/RabbitMQ)에 자동 반환되어 다른 Worker가 처리합니다.
> 이는 기존 `RecoveryAwareShutdownHook` ([recovery_shutdown.py](../../packages/selfhealing-python/src/selfhealing/services/coordination/recovery_shutdown.py) L382-461)의 K8s preStop 패턴과 동일한 안전 보장을 Celery 레이어에서 제공합니다.

### 4.3 ThrottleSlaAlertUrlBuilder (리뷰 1)

**신규 파일**: `selfhealing/services/throttle/throttle_sla_alert_urls.py`

기존 CB URL 빌더 패턴을 그대로 따릅니다.

**코드 근거**:
- [ActionableAlertUrlBuilder](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/actionable_alert_urls.py) L66-87: `os.getenv()` 패턴 + 싱글톤
- [ChaosActionableAlertUrlBuilder](../../packages/selfhealing-python/src/selfhealing/services/chaos/actionable_alert_urls.py) L80-140: `os.getenv()` + dataclass 반환 패턴

```python
"""
Throttle SLA Actionable Alert URL Builder.

환경변수에서 기본 URL을 읽어 서비스별 대시보드/Admin/Runbook 링크를 생성합니다.

코드 근거: ActionableAlertUrlBuilder (circuit_breaker/actionable_alert_urls.py)
동일한 os.getenv() + 싱글톤 + dataclass 반환 패턴 적용.

Environment Variables:
- THROTTLE_SLA_DASHBOARD_URL: Grafana Throttle 대시보드 URL
- THROTTLE_SLA_ADMIN_BASE_URL: Throttle Admin 제어판 URL
- THROTTLE_SLA_RUNBOOK_URL: SLA 장애 대응 Runbook URL
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


@dataclass
class ThrottleSlaActionableUrls:
    """
    SLA 알림에 포함될 Actionable URL 모음.

    CB의 ActionableUrls (actionable_alert_urls.py L30-55)와 동일 구조.
    """

    dashboard_url: str | None = None
    admin_url: str | None = None
    runbook_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "dashboard_url": self.dashboard_url,
            "admin_url": self.admin_url,
            "runbook_url": self.runbook_url,
        }

    def has_any_url(self) -> bool:
        return any([self.dashboard_url, self.admin_url, self.runbook_url])


class ThrottleSlaAlertUrlBuilder:
    """
    Throttle SLA Actionable Alert URL 빌더.

    설계 원칙 (CB ActionableAlertUrlBuilder와 동일):
    - 거버넌스 유지: Admin 제어판으로 이동 (원클릭 해제 불가)
    - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
    - 안전성: 운영자가 상태 확인 후 판단

    코드 근거: ActionableAlertUrlBuilder.__init__() (actionable_alert_urls.py L83-87)
    """

    def __init__(self):
        self._dashboard_base_url = os.getenv("THROTTLE_SLA_DASHBOARD_URL", "")
        self._admin_base_url = os.getenv("THROTTLE_SLA_ADMIN_BASE_URL", "")
        self._runbook_base_url = os.getenv("THROTTLE_SLA_RUNBOOK_URL", "")

        logger.debug(
            f"[ThrottleSlaAlertUrlBuilder] Initialized: "
            f"dashboard={bool(self._dashboard_base_url)}, "
            f"admin={bool(self._admin_base_url)}, "
            f"runbook={bool(self._runbook_base_url)}"
        )

    def build_sla_alert_urls(
        self,
        service_name: str,
        event_type: str = "sla_warning",
        rtt_ms: float | None = None,
    ) -> ThrottleSlaActionableUrls:
        """
        SLA 이벤트에 대한 Actionable URL을 생성.

        CB의 build_cb_open_urls() (actionable_alert_urls.py L100-130)와 동일 패턴.
        """
        return ThrottleSlaActionableUrls(
            dashboard_url=self._build_dashboard_url(service_name, rtt_ms),
            admin_url=self._build_admin_url(service_name, event_type),
            runbook_url=self._build_runbook_url(event_type),
        )

    def _build_dashboard_url(
        self, service_name: str, rtt_ms: float | None
    ) -> str | None:
        if not self._dashboard_base_url:
            return None
        params = {"var-service": service_name}
        if rtt_ms is not None:
            params["var-rtt"] = str(int(rtt_ms))
        return f"{self._dashboard_base_url}?{urlencode(params)}"

    def _build_admin_url(
        self, service_name: str, event_type: str
    ) -> str | None:
        if not self._admin_base_url:
            return None
        params = {"service": service_name, "event": event_type}
        return f"{self._admin_base_url}?{urlencode(params)}"

    def _build_runbook_url(self, event_type: str) -> str | None:
        if not self._runbook_base_url:
            return None
        anchor = event_type.replace("_", "-")
        return f"{self._runbook_base_url}#{anchor}"


# --- 싱글톤 (CB get_actionable_alert_url_builder() 패턴과 동일) ---
_builder_instance: ThrottleSlaAlertUrlBuilder | None = None


def get_throttle_sla_alert_url_builder() -> ThrottleSlaAlertUrlBuilder:
    """싱글톤 인스턴스 반환."""
    global _builder_instance
    if _builder_instance is None:
        _builder_instance = ThrottleSlaAlertUrlBuilder()
    return _builder_instance
```

### 4.4 Actionable Alert Slack 포맷 (URL 빌더 연동)

v1.0에서 하드코딩되었던 URL을 `ThrottleSlaAlertUrlBuilder`로 교체합니다.

**코드 근거**:
- [format_cb_slack_blocks()](../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py) L700-800: `payload.metadata`에서 URL을 읽어 Slack 버튼 생성
- [ActionableAlertUrlBuilder.build_cb_open_urls()](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/actionable_alert_urls.py) L100-130

```python
def format_sla_slack_blocks(
    payload: NotificationPayload,
    priority: NotificationPriority,
) -> dict[str, Any]:
    """
    SLA 알림용 Slack Block Kit 메시지 포맷.

    v2.0 변경:
    - URL을 ThrottleSlaAlertUrlBuilder에서 동적 생성
    - Region 정보 표시
    - RTT 변화율(%) 표시
    """
    from selfhealing.services.throttle.throttle_sla_alert_urls import (
        get_throttle_sla_alert_url_builder,
    )

    metadata = payload.metadata or {}
    service_name = metadata.get("service_name", "default")
    region = metadata.get("region")
    rtt_ms = metadata.get("rtt_ms", 0)
    rtt_change_percent = metadata.get("rtt_change_percent")

    # URL 빌더에서 동적 생성 (하드코딩 제거)
    builder = get_throttle_sla_alert_url_builder()
    urls = builder.build_sla_alert_urls(
        service_name=service_name,
        event_type=metadata.get("event_type", "sla_warning"),
        rtt_ms=rtt_ms,
    )

    severity_emoji = {
        NotificationPriority.CRITICAL: "🔴",
        NotificationPriority.HIGH: "🟠",
        NotificationPriority.MEDIUM: "🟡",
    }.get(priority, "⚪")

    fields = [
        {"type": "mrkdwn", "text": f"*RTT:*\n{rtt_ms:.1f}ms"},
        {"type": "mrkdwn", "text": f"*Threshold:*\n{metadata.get('threshold_ms', 0)}ms"},
        {"type": "mrkdwn", "text": f"*Current Limit:*\n{metadata.get('current_limit', 0)}"},
        {"type": "mrkdwn", "text": f"*Service:*\n{service_name}"},
    ]

    # RTT 변화율이 있으면 추가 (리뷰 8)
    if rtt_change_percent is not None:
        fields.append(
            {"type": "mrkdwn", "text": f"*RTT Change:*\n{rtt_change_percent:+.1f}%"}
        )

    # Region이 있으면 추가 (리뷰 2)
    if region:
        fields.append(
            {"type": "mrkdwn", "text": f"*Region:*\n{region}"}
        )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {payload.title}",
                "emoji": True,
            },
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Details:*\n{payload.message}"},
        },
    ]

    # Actionable 버튼: URL이 설정된 것만 포함
    action_elements = []
    if urls.dashboard_url:
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "📊 Grafana Dashboard", "emoji": True},
            "url": urls.dashboard_url,
            "action_id": "view_throttle_dashboard",
        })
    if urls.admin_url:
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "⚙️ Throttle Admin", "emoji": True},
            "url": urls.admin_url,
            "action_id": "view_throttle_admin",
            "style": "primary",
        })
    if urls.runbook_url:
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "📖 SLA Runbook", "emoji": True},
            "url": urls.runbook_url,
            "action_id": "view_sla_runbook",
        })

    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})

    return {"blocks": blocks}
```

### 4.5 SLA Notification Templates (리뷰 7)

**신규 파일**: `selfhealing/services/throttle/sla_notification_templates.py`

`recovery_notifications.py`의 순수 함수 패턴을 그대로 따릅니다.

**코드 근거**: [recovery_notifications.py](../../packages/selfhealing-python/src/selfhealing/services/coordination/recovery_notifications.py) L15-80 — 순수 함수가 `{title, severity, message, details, actions}` dict 반환

```python
"""
SLA 알림 메시지 템플릿 모듈.

코드 근거: recovery_notifications.py — 순수 함수 패턴.
각 함수는 상태 비의존적(stateless)이며, dict를 반환합니다.
i18n은 Phase 2에서 도입 예정 (현재는 영어/한국어 혼합).
"""
from __future__ import annotations

from typing import Any


def build_sla_warning_message(
    rtt_ms: float,
    threshold_ms: float,
    current_limit: int,
    previous_limit: int,
    gradient: float,
    rtt_change_percent: float | None = None,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """
    SLA Warning 메시지 생성.

    Returns:
        {title, severity, message, details, actions}
    """
    change_info = ""
    if rtt_change_percent is not None:
        change_info = f" (변화율: {rtt_change_percent:+.1f}%)"

    region_info = f" [{region}]" if region else ""

    return {
        "title": f"⚠️ SLA Warning: Response Time Threshold Exceeded{region_info}",
        "severity": "high",
        "message": (
            f"Response time ({rtt_ms:.1f}ms) exceeded warning threshold "
            f"({threshold_ms}ms){change_info}.\n"
            f"Throttle limit reduced: {previous_limit} → {current_limit}\n"
            f"Service: {service_name} | Gradient: {gradient:.3f}"
        ),
        "details": {
            "rtt_ms": rtt_ms,
            "threshold_ms": threshold_ms,
            "current_limit": current_limit,
            "previous_limit": previous_limit,
            "gradient": gradient,
            "rtt_change_percent": rtt_change_percent,
            "event_type": "sla_warning",
        },
        "actions": ["check_dashboard", "review_throttle_config"],
    }


def build_sla_critical_message(
    rtt_ms: float,
    threshold_ms: float,
    current_limit: int,
    previous_limit: int,
    reduction_percent: float,
    gradient: float,
    rtt_change_percent: float | None = None,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """SLA Critical 메시지 생성."""
    change_info = ""
    if rtt_change_percent is not None:
        change_info = f" (변화율: {rtt_change_percent:+.1f}%)"

    region_info = f" [{region}]" if region else ""

    return {
        "title": f"🔴 SLA CRITICAL: Severe Response Time Degradation{region_info}",
        "severity": "critical",
        "message": (
            f"CRITICAL: Response time ({rtt_ms:.1f}ms) exceeded critical threshold "
            f"({threshold_ms}ms){change_info}.\n"
            f"Aggressive throttling: {previous_limit} → {current_limit} "
            f"(-{reduction_percent}%)\n"
            f"Service: {service_name} | Gradient: {gradient:.3f}\n\n"
            f"Immediate action may be required."
        ),
        "details": {
            "rtt_ms": rtt_ms,
            "threshold_ms": threshold_ms,
            "current_limit": current_limit,
            "previous_limit": previous_limit,
            "reduction_percent": reduction_percent,
            "gradient": gradient,
            "rtt_change_percent": rtt_change_percent,
            "event_type": "sla_critical",
        },
        "actions": ["check_dashboard", "review_throttle_config", "escalate_oncall"],
    }


def build_sla_recovered_message(
    previous_limit: int,
    new_limit: int,
    rtt_ms: float,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """SLA 복구 메시지 생성."""
    region_info = f" [{region}]" if region else ""

    return {
        "title": f"✅ SLA Recovered: Throttle Limit Restored{region_info}",
        "severity": "medium",
        "message": (
            f"Throttle limit recovered: {previous_limit} → {new_limit}\n"
            f"Current RTT: {rtt_ms:.1f}ms | Service: {service_name}"
        ),
        "details": {
            "previous_limit": previous_limit,
            "new_limit": new_limit,
            "rtt_ms": rtt_ms,
            "event_type": "limit_recovered",
        },
        "actions": ["verify_recovery"],
    }
```

---

## 5. Cooldown, 중복 방지, 실패 폴백

### 5.1 기존 Cooldown 메커니즘

`UnifiedNotificationManager`의 Cooldown 메커니즘 활용:

```python
# RoutingPolicy에서 SLA 카테고리 cooldown
cooldown_seconds = {
    NotificationCategory.SLA: 1800,  # 30분
}
```

**Cooldown 동작**:
1. 첫 번째 SLA Warning → 즉시 발송
2. 30분 이내 추가 Warning → suppressed (cooldown)
3. 30분 경과 후 → 다시 발송 가능

### 5.2 서비스 단위 dedup_key (리뷰 3)

v1.0의 `dedup_key=f"sla:throttle"` (도메인 단위)를 서비스 단위로 세분화합니다.

**코드 근거**: [event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) L611 `dedup_key=f"cb:{service_name}:open"` — CB는 이미 서비스 단위 dedup을 적용

```python
# v1.0: 도메인 단위 (모든 서비스가 같은 key 공유)
dedup_key = "sla:throttle"

# v2.0: 서비스 단위 (서비스별 독립 cooldown)
# notify_sla()에서 domain=f"throttle:{service_name}" → dedup_key=f"sla:throttle:{service_name}"
dedup_key = f"sla:throttle:{service_name}"
```

| 비교 | v1.0 | v2.0 |
|------|------|------|
| dedup_key | `sla:throttle` | `sla:throttle:{service_name}` |
| Cooldown 범위 | 전체 서비스 공유 | 서비스별 독립 |
| CB 패턴과 일치 | ❌ | ✅ `cb:{service_name}:open` |

### 5.3 Redis 기반 Cooldown 영속성 (리뷰 4)

v1.0의 `_cooldown_cache: dict[str, datetime]` (인메모리)는 Pod 재시작 시 쿨다운 상태를 잃습니다.
Redis TTL을 활용하여 영속적 쿨다운을 보장합니다.

**코드 근거**: [regional_gate.py](../../packages/selfhealing-python/src/selfhealing/coordination/regional_gate.py) L200-230 — `self._redis.set(key, json.dumps(data), ex=duration_seconds)` TTL 패턴

```python
class RedisCooldownStore:
    """
    Redis 기반 Cooldown 영속 저장소.

    코드 근거: RegionalGate._redis.set(key, data, ex=duration) 패턴
    DiskBuffer는 영속 스토리지이지만 TTL 자동 만료를 지원하지 않으므로 부적절.

    키 형식: selfhealing:notification:cooldown:{dedup_key}
    값: 마지막 전송 시간 (ISO 8601)
    TTL: cooldown_seconds (자동 만료)
    """

    KEY_PREFIX = "selfhealing:notification:cooldown"

    def __init__(self, redis_client=None, cooldown_seconds: int = 1800):
        self._redis = redis_client
        self._cooldown_seconds = cooldown_seconds
        # 메모리 폴백 (Redis 미사용 또는 장애 시)
        self._memory_cache: dict[str, float] = {}

    def is_cooled_down(self, dedup_key: str) -> bool:
        """
        해당 dedup_key가 쿨다운 중인지 확인.

        Returns:
            True이면 쿨다운 중 (알림 억제)
        """
        if self._redis is not None:
            try:
                key = f"{self.KEY_PREFIX}:{dedup_key}"
                return self._redis.exists(key) > 0
            except Exception:
                pass  # Redis 장애 시 메모리 폴백

        # 메모리 폴백
        import time

        last_sent = self._memory_cache.get(dedup_key)
        if last_sent is None:
            return False
        return (time.time() - last_sent) < self._cooldown_seconds

    def mark_sent(self, dedup_key: str) -> None:
        """
        알림 전송 완료를 기록 (쿨다운 시작).

        Redis: SET key value EX cooldown_seconds (TTL 자동 만료)
        """
        import time

        now = time.time()

        if self._redis is not None:
            try:
                key = f"{self.KEY_PREFIX}:{dedup_key}"
                self._redis.set(key, str(now), ex=self._cooldown_seconds)
                return
            except Exception:
                pass  # Redis 장애 시 메모리 폴백

        self._memory_cache[dedup_key] = now
```

### 5.4 알림 실패 폴백 파이프라인 (리뷰 5)

알림 전송 실패 시 3단계 폴백을 적용합니다.

**코드 근거**: [FallbackEscalationHandler](../../packages/selfhealing-python/src/selfhealing/services/fallback_escalation.py) L30-200 — 디스크 JSONL + 메모리 버퍼 3-tier 폴백 패턴

```
┌────────────────────────────────────────────────────────────────┐
│ 전송 실패 시 폴백 순서                                        │
│                                                                │
│ 1. Celery autoretry (max 3회, 30초 간격)                      │
│    └─ 실패 → 2단계                                            │
│ 2. NotificationFallbackRecorder: 디스크 JSONL 기록            │
│    └─ 디스크 실패 → 3단계                                     │
│ 3. 메모리 버퍼 (최대 1000건)                                  │
└────────────────────────────────────────────────────────────────┘
```

**Celery Task 재시도는 Section 4.2의 `autoretry_for`로 자동 처리됩니다.**

최종 실패분 기록을 위한 `NotificationFallbackRecorder`:

```python
"""
알림 전송 최종 실패 기록기.

코드 근거: FallbackEscalationHandler (fallback_escalation.py L30-200)
동일한 디스크 JSONL + 메모리 버퍼 패턴 적용.
"""
import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# FallbackEscalationHandler와 동일한 경로 규칙
DEFAULT_FALLBACK_PATH = "/var/log/selfhealing/notification_fallback.jsonl"


class NotificationFallbackRecorder:
    """
    알림 전송 실패 기록기.

    FallbackEscalationHandler (fallback_escalation.py)와 동일한 패턴:
    1. JSONL 파일 기록 시도
    2. 파일 기록 실패 시 메모리 버퍼에 저장
    """

    def __init__(
        self,
        file_path: str = DEFAULT_FALLBACK_PATH,
        max_memory_entries: int = 1000,
    ):
        self._file_path = Path(file_path)
        self._memory_buffer: deque[dict] = deque(maxlen=max_memory_entries)
        self._lock = threading.RLock()

    def record_failed_notification(
        self,
        dedup_key: str,
        notification_type: str,
        event_data: dict[str, Any],
        error: str,
    ) -> None:
        """실패한 알림을 기록합니다."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dedup_key": dedup_key,
            "notification_type": notification_type,
            "event_data": event_data,
            "error": str(error),
        }

        with self._lock:
            if not self._write_to_file(entry):
                self._write_to_memory(entry)

    def _write_to_file(self, entry: dict) -> bool:
        """JSONL 파일에 기록 (FallbackEscalationHandler._write_to_file 패턴)."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file_path, "a") as f:
                json.dump(entry, f, ensure_ascii=False)
                f.write("\n")
            return True
        except Exception as e:
            logger.warning(f"[NotificationFallback] File write failed: {e}")
            return False

    def _write_to_memory(self, entry: dict) -> None:
        """메모리 버퍼에 저장 (최종 폴백)."""
        self._memory_buffer.append(entry)
        logger.debug(
            f"[NotificationFallback] Stored in memory buffer "
            f"({len(self._memory_buffer)} entries)"
        )

    def get_pending_notifications(self) -> list[dict]:
        """미처리 알림 목록을 반환 (재시도용)."""
        with self._lock:
            return list(self._memory_buffer)
```

---

## 6. RTT 변화율 계산 (리뷰 8)

### 6.1 GradientCalculator의 기존 구조

**코드 근거**: [GradientCalculator.get_gradient()](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) L277-295

현재 `GradientCalculator.get_gradient()`는 이미 **RTT 변화율**을 계산하고 있습니다:

```python
# adaptive.py L290-295 (현재 코드)
def get_gradient(self) -> float:
    # Gradient = (current - previous) / previous
    if self._previous_smoothed_rtt == 0:
        return 0.0
    return (self._smoothed_rtt - self._previous_smoothed_rtt) / self._previous_smoothed_rtt
```

이 gradient 값은 이미 **비율(ratio)**이므로, `× 100`만 하면 퍼센트 변화율이 됩니다.

### 6.2 이벤트 데이터에 rtt_change_percent 추가

`_maybe_adjust_limit()` 에서 SLA 이벤트 발행 시 `rtt_change_percent` 필드를 추가합니다.

**코드 근거**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) L639-665 — `_emit_throttle_event()` 호출부

```python
# adaptive.py _maybe_adjust_limit() 내 변경 사항
# SLA Critical 이벤트 발행 (기존 + rtt_change_percent 추가)
gradient = self._gradient_calculator.get_gradient()
rtt_change_percent = gradient * 100.0  # gradient는 이미 비율

_emit_throttle_event(
    "THROTTLE_SLA_CRITICAL",
    {
        "current_rtt_ms": rtt_ms,
        "threshold_ms": self.config.sla_critical_ms,
        "current_limit": self._current_limit,
        "previous_limit": previous_limit,
        "reduction_percent": 30,
        "gradient": gradient,
        # v2.0 추가 필드
        "rtt_change_percent": rtt_change_percent,
        "service_name": "default",  # Phase 2: 서비스별 AdaptiveThrottle
    },
    priority_name="CRITICAL",
)

# SLA Warning 이벤트도 동일하게 추가
_emit_throttle_event(
    "THROTTLE_SLA_WARNING",
    {
        "current_rtt_ms": rtt_ms,
        "threshold_ms": self.config.sla_warning_ms,
        "current_limit": self._current_limit,
        "previous_limit": previous_limit,
        "gradient": gradient,
        # v2.0 추가 필드
        "rtt_change_percent": rtt_change_percent,
        "service_name": "default",
    },
    priority_name="HIGH",
)
```

> **설계 결정**: RTT 변화율 계산은 **핸들러가 아닌 AdaptiveThrottle** 내에서 수행합니다.
> 이유: `GradientCalculator._smoothed_rtt`는 `AdaptiveThrottle` 내부 상태이며, 핸들러에서 접근할 수 없습니다.
> 핸들러는 전달받은 `rtt_change_percent`를 메시지에 포함하기만 합니다.

---

## 7. Emergency Level 연동

### 7.1 Priority Escalation

`UnifiedNotificationManager._get_effective_priority()` 메서드 활용:

```python
def _get_effective_priority(self, payload: NotificationPayload) -> NotificationPriority:
    """
    Get effective priority considering emergency level.

    Dynamic escalation rules:
    - Level 2+: LOW/INFO → MEDIUM
    - Level 3+: LOW/INFO/MEDIUM → HIGH
    """
    priority = payload.priority

    try:
        from selfhealing.services.emergency_mode import get_emergency_manager

        manager = get_emergency_manager()
        level = manager.get_current_level()

        # Emergency Level 2+: Escalate LOW/INFO to MEDIUM
        if level >= 2 and priority in (NotificationPriority.LOW, NotificationPriority.INFO):
            priority = NotificationPriority.MEDIUM

        # Emergency Level 3+: Escalate to HIGH
        if level >= 3 and priority in (
            NotificationPriority.LOW,
            NotificationPriority.INFO,
            NotificationPriority.MEDIUM,
        ):
            priority = NotificationPriority.HIGH
    except ImportError:
        pass

    return priority
```

**결과**:
- Emergency Level 2에서 SLA Warning(HIGH) → HIGH 유지
- Emergency Level 3에서 SLA Warning(HIGH) → HIGH 유지
- Emergency Level 3에서 일반 알림(MEDIUM) → HIGH로 에스컬레이션

---

## 8. 테스트 시나리오

### 8.1 단위 테스트

v2.0에서 변경된 사항을 반영한 테스트 시나리오입니다.

```python
class TestSLANotificationIntegrationV2:
    """SLA 알림 연동 v2.0 테스트."""

    def test_celery_async_dispatch(self):
        """리뷰 6: Warning 시 Celery apply_async 호출 확인."""
        with patch(
            "selfhealing.adapters.celery.tasks.send_sla_notification"
        ) as mock_task:
            _handle_sla_warning(
                MockEvent(data={
                    "current_rtt_ms": 250.0,
                    "threshold_ms": 200,
                    "service_name": "payment",
                })
            )

            mock_task.apply_async.assert_called_once()
            kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
            assert kwargs["notification_type"] == "warning"
            assert kwargs["event_data"]["service_name"] == "payment"

    def test_celery_fallback_to_sync(self):
        """리뷰 6: Celery 미사용 환경에서 동기 fallback 확인."""
        with patch(
            "selfhealing.adapters.celery.tasks.send_sla_notification",
            side_effect=ImportError,
        ):
            with patch(
                "selfhealing.services.throttle.sla_notification._send_sla_warning_sync"
            ) as mock_sync:
                _handle_sla_warning(
                    MockEvent(data={"current_rtt_ms": 250.0})
                )
                mock_sync.assert_called_once()

    def test_region_injected_in_metadata(self):
        """리뷰 2: ClusterIdentity.region이 metadata에 포함 확인."""
        with patch(
            "selfhealing.core.cluster_identity.get_cluster_identity"
        ) as mock_identity:
            mock_identity.return_value = MockClusterIdentity(region="ap-northeast-2")

            with patch(
                "selfhealing.services.unified_notification.notify_sla"
            ) as mock_notify:
                mock_notify.return_value = NotificationResult(success=True)

                _send_sla_warning_sync({
                    "current_rtt_ms": 250.0,
                    "threshold_ms": 200,
                    "service_name": "payment",
                })

                call_kwargs = mock_notify.call_args.kwargs
                assert call_kwargs["metadata"]["region"] == "ap-northeast-2"

    def test_service_level_dedup_key(self):
        """리뷰 3: dedup_key가 서비스 단위인지 확인."""
        with patch(
            "selfhealing.services.unified_notification.notify_sla"
        ) as mock_notify:
            mock_notify.return_value = NotificationResult(success=True)

            _send_sla_warning_sync({
                "current_rtt_ms": 250.0,
                "service_name": "payment",
            })

            # domain=f"throttle:{service_name}" → dedup_key = "sla:throttle:payment"
            call_kwargs = mock_notify.call_args.kwargs
            assert call_kwargs["domain"] == "throttle:payment"

    def test_rtt_change_percent_in_template(self):
        """리뷰 8: RTT 변화율이 메시지에 포함 확인."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        template = build_sla_warning_message(
            rtt_ms=250.0,
            threshold_ms=200,
            current_limit=80,
            previous_limit=100,
            gradient=0.25,
            rtt_change_percent=25.0,
            service_name="payment",
        )

        assert "25.0%" in template["message"]
        assert template["details"]["rtt_change_percent"] == 25.0

    def test_url_builder_env_vars(self):
        """리뷰 1: URL 빌더가 환경변수에서 URL을 읽는지 확인."""
        with patch.dict(os.environ, {
            "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
            "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
            "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
        }):
            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="payment",
                event_type="sla_critical",
            )

            assert "payment" in urls.dashboard_url
            assert "payment" in urls.admin_url
            assert "sla-critical" in urls.runbook_url

    def test_redis_cooldown_persistence(self):
        """리뷰 4: Redis 쿨다운 SET/GET 확인."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        store = RedisCooldownStore(
            redis_client=mock_redis,
            cooldown_seconds=1800,
        )

        # 쿨다운 중이 아님
        assert not store.is_cooled_down("sla:throttle:payment")

        # 전송 기록
        store.mark_sent("sla:throttle:payment")
        mock_redis.set.assert_called_once_with(
            "selfhealing:notification:cooldown:sla:throttle:payment",
            ANY,
            ex=1800,
        )

    def test_redis_fallback_to_memory(self):
        """리뷰 4: Redis 장애 시 메모리 폴백 확인."""
        mock_redis = MagicMock()
        mock_redis.exists.side_effect = Exception("Redis down")

        store = RedisCooldownStore(
            redis_client=mock_redis,
            cooldown_seconds=1800,
        )

        # Redis 장애 → 메모리 폴백 (쿨다운 아님)
        assert not store.is_cooled_down("sla:throttle:payment")

    def test_notification_fallback_recorder(self):
        """리뷰 5: 전송 실패 시 JSONL 기록 확인."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            recorder = NotificationFallbackRecorder(file_path=f.name)

            recorder.record_failed_notification(
                dedup_key="sla:throttle:payment",
                notification_type="critical",
                event_data={"current_rtt_ms": 600.0},
                error="ConnectionError: Slack API timeout",
            )

            with open(f.name) as rf:
                entry = json.loads(rf.readline())
                assert entry["dedup_key"] == "sla:throttle:payment"
                assert "ConnectionError" in entry["error"]
```

### 8.2 통합 테스트 시나리오

```python
class TestSLANotificationE2E:
    """End-to-End 알림 연동 테스트."""

    def test_full_pipeline_warning_to_slack(self):
        """EventBus → Celery → notify_sla → Slack 전체 파이프라인."""
        # 1. AdaptiveThrottle이 SLA Warning 이벤트 발행
        # 2. _handle_sla_warning()이 Celery에 위임
        # 3. Celery 태스크가 notify_sla() 호출
        # 4. notify_sla()가 Slack 채널 전송
        # 5. format_sla_slack_blocks()가 URL 빌더로 URL 생성
        pass  # 구현은 Phase 2

    def test_cooldown_suppresses_repeated_warnings(self):
        """30분 이내 반복 Warning 억제 확인."""
        manager = UnifiedNotificationManager()

        result1 = manager.notify(NotificationPayload(
            title="SLA Warning",
            message="Test",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle:payment",  # v2.0: 서비스 단위
        ))

        result2 = manager.notify(NotificationPayload(
            title="SLA Warning",
            message="Test",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle:payment",
        ))

        assert not result1.suppressed
        assert result2.suppressed
        assert result2.suppression_reason == "cooldown"

    def test_different_services_independent_cooldown(self):
        """서비스별 독립 쿨다운 확인 (리뷰 3)."""
        manager = UnifiedNotificationManager()

        # payment 서비스 알림
        result1 = manager.notify(NotificationPayload(
            title="SLA Warning",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle:payment",
        ))

        # order 서비스 알림 (독립 쿨다운)
        result2 = manager.notify(NotificationPayload(
            title="SLA Warning",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle:order",  # 다른 서비스
        ))

        # 둘 다 발송되어야 함 (서비스별 독립 쿨다운)
        assert not result1.suppressed
        assert not result2.suppressed
```

---

## 9. 설정

### 9.1 LayeredSettings 통합

**신규 파일**: `selfhealing/settings/throttle_sla_notification.py`

**코드 근거**:
- [SLASettings](../../packages/selfhealing-python/src/selfhealing/settings/sla.py) L26-47: `BaseSettings` + `env_prefix` + `SettingsConfigDict` 패턴
- [NotificationChannelSettings](../../packages/selfhealing-python/src/selfhealing/settings/notification_channel.py) L29-88: `max_retry`, `retry_delay_seconds`, `cooldown_seconds` 설정

```python
"""
Throttle SLA Notification Settings - Pydantic v2.

코드 근거: SLASettings (settings/sla.py), NotificationChannelSettings (settings/notification_channel.py)
동일한 BaseSettings + env_prefix + 싱글톤 + reset 패턴 적용.

Environment Variables:
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_ENABLED=true
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS=1800
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS=900
    THROTTLE_SLA_DASHBOARD_URL=https://grafana.internal/d/throttle-sla
    THROTTLE_SLA_ADMIN_BASE_URL=/admin/throttle/status
    THROTTLE_SLA_RUNBOOK_URL=https://docs.internal/runbooks/sla-degradation
"""
import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ThrottleSLANotificationSettings(BaseSettings):
    """Throttle SLA 알림 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THROTTLE_SLA_NOTIFICATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # 알림 활성화 여부
    enabled: bool = Field(default=True, description="SLA 알림 전체 활성화")

    # Warning/Critical 알림 별도 제어
    warning_enabled: bool = Field(default=True, description="Warning 알림 활성화")
    critical_enabled: bool = Field(default=True, description="Critical 알림 활성화")
    recovery_enabled: bool = Field(default=True, description="Recovery 알림 활성화")

    # Cooldown 오버라이드 (초)
    warning_cooldown_seconds: int = Field(
        default=1800, ge=60, le=7200,
        description="Warning 쿨다운 (초). NotificationChannelSettings.cooldown_seconds=300 대비 SLA는 30분",
    )
    critical_cooldown_seconds: int = Field(
        default=900, ge=60, le=3600,
        description="Critical 쿨다운 (초). Critical은 더 짧음",
    )

    # Redis Cooldown 설정 (리뷰 4)
    redis_cooldown_enabled: bool = Field(
        default=True,
        description="Redis 기반 영속적 쿨다운 사용 (False면 인메모리 전용)",
    )

    # 채널 오버라이드
    warning_channels: list[str] | None = Field(
        default=None, description="Warning 채널 (None이면 priority 기본 사용)",
    )
    critical_channels: list[str] | None = Field(
        default=None, description="Critical 채널 (None이면 priority 기본 사용)",
    )


# --- 싱글톤 + reset (SLASettings 패턴과 동일) ---
_settings: ThrottleSLANotificationSettings | None = None


def get_throttle_sla_notification_settings() -> ThrottleSLANotificationSettings:
    global _settings
    if _settings is None:
        _settings = ThrottleSLANotificationSettings()
    return _settings


def reset_throttle_sla_notification_settings() -> None:
    global _settings
    _settings = None
```

### 9.2 URL 환경변수 (리뷰 1)

URL은 Settings 클래스에 포함하지 않고, `ThrottleSlaAlertUrlBuilder`가 직접 `os.getenv()`로 읽습니다.
이는 CB/Chaos URL 빌더와 동일한 패턴입니다.

**코드 근거**: [ActionableAlertUrlBuilder](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/actionable_alert_urls.py) L83-87 — `os.getenv("CB_DASHBOARD_URL", "")`

| 환경변수 | 설명 | 기본값 | CB 대응 변수 |
|---------|------|-------|-------------|
| `THROTTLE_SLA_DASHBOARD_URL` | Grafana Throttle 대시보드 | `""` (빈 문자열) | `CB_DASHBOARD_URL` |
| `THROTTLE_SLA_ADMIN_BASE_URL` | Admin 제어판 | `""` | `CB_ADMIN_BASE_URL` |
| `THROTTLE_SLA_RUNBOOK_URL` | SLA Runbook | `""` | `CB_RUNBOOK_URL` |

### 9.3 Graceful Shutdown (리뷰 10)

별도의 `NotificationShutdownHandler` 없이 Celery의 `acks_late=True` + Worker warm shutdown으로 처리합니다.

**코드 근거**: [RecoveryAwareShutdownHook](../../packages/selfhealing-python/src/selfhealing/services/coordination/recovery_shutdown.py) L382-461 — K8s preStop 패턴

```yaml
# K8s Deployment에서 terminationGracePeriodSeconds 설정
spec:
  terminationGracePeriodSeconds: 30  # Celery Worker 종료 대기
  containers:
    - name: celery-worker
      lifecycle:
        preStop:
          exec:
            command: ["celery", "-A", "myproject", "control", "shutdown"]
```

---

## 10. 모니터링 대시보드

### 10.1 Prometheus 메트릭 추가

```python
# services/metrics/definitions.py

sla_notification_sent_total = get_or_create_counter(
    "selfhealing_sla_notification_sent_total",
    "Total SLA notifications sent",
    ["event_type", "priority", "channel"],
)

sla_notification_suppressed_total = get_or_create_counter(
    "selfhealing_sla_notification_suppressed_total",
    "Total SLA notifications suppressed by cooldown",
    ["event_type"],
)

sla_notification_failed_total = get_or_create_counter(
    "selfhealing_sla_notification_failed_total",
    "Total SLA notification failures",
    ["event_type", "error_type"],
)
```

### 10.2 Grafana 패널

```yaml
# SLA 알림 발송률
- title: "SLA Notification Rate"
  query: |
    rate(selfhealing_sla_notification_sent_total[5m])

# Cooldown 억제율
- title: "SLA Notification Suppression Rate"
  query: |
    rate(selfhealing_sla_notification_suppressed_total[5m]) /
    (rate(selfhealing_sla_notification_sent_total[5m]) +
     rate(selfhealing_sla_notification_suppressed_total[5m]))
```

---

## 11. 참조

### 기존 소스
- [UnifiedNotificationManager](../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py) — 알림 라우팅, Cooldown, Priority Escalation
- [AdaptiveThrottle](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) — SLA 이벤트 발행, GradientCalculator
- [EventBus](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) — Celery apply_async 패턴 (L901), CB dedup 패턴 (L611)

### 패턴 근거 소스
- [ActionableAlertUrlBuilder](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/actionable_alert_urls.py) — URL 빌더 패턴 (os.getenv + 싱글톤)
- [ChaosActionableAlertUrlBuilder](../../packages/selfhealing-python/src/selfhealing/services/chaos/actionable_alert_urls.py) — URL 빌더 패턴 (Chaos 도메인)
- [ClusterIdentity](../../packages/selfhealing-python/src/selfhealing/core/cluster_identity.py) — 리전 정보 (SELFHEALING_REGION)
- [FallbackEscalationHandler](../../packages/selfhealing-python/src/selfhealing/meta/fallback_escalation.py) — 디스크 JSONL + 메모리 폴백 패턴
- [RecoveryNotifications](../../packages/selfhealing-python/src/selfhealing/services/coordination/recovery_notifications.py) — 순수 함수 템플릿 패턴
- [RegionalIsolationGate](../../packages/selfhealing-python/src/selfhealing/coordination/regional_gate.py) — Redis TTL 영속 패턴
- [RecoveryAwareShutdownHook](../../packages/selfhealing-python/src/selfhealing/services/coordination/recovery_shutdown.py) — Graceful Shutdown 패턴
- [NotificationChannelSettings](../../packages/selfhealing-python/src/selfhealing/settings/notification_channel.py) — max_retry=3, retry_delay_seconds=30
- [SLASettings](../../packages/selfhealing-python/src/selfhealing/settings/sla.py) — BaseSettings + 싱글톤 + reset 패턴

### v2.0 신규 파일 (구현 완료)
- `selfhealing/services/throttle/sla_notification.py` — SLA 알림 핸들러 (Section 4.1) ✅
- `selfhealing/services/throttle/throttle_sla_alert_urls.py` — URL 빌더 (Section 4.3) ✅
- `selfhealing/services/throttle/sla_notification_templates.py` — 메시지 템플릿 (Section 4.5) ✅
- `selfhealing/services/throttle/notification_fallback_recorder.py` — 실패 폴백 (Section 5.4) ✅
- `selfhealing/services/throttle/redis_cooldown_store.py` — Redis 쿨다운 (Section 5.3) ✅
- `selfhealing/settings/throttle_sla_notification.py` — 설정 (Section 9.1) ✅
- `selfhealing/adapters/celery/tasks/sla_notification.py` — Celery 태스크 (Section 4.2) ✅

### v2.0 수정 파일 (구현 완료)
- `selfhealing/services/throttle/adaptive.py` — rtt_change_percent, service_name 이벤트 데이터 추가 (Section 6.2) ✅
- `selfhealing/services/unified_notification.py` — format_sla_slack_blocks 추가 (Section 4.4) ✅
- `selfhealing/adapters/celery/tasks/__init__.py` — send_sla_notification 태스크 등록 ✅

### v2.0 단위 테스트 (179/179 통과, 커버리지 93.33%)

테스트 파일을 모듈별로 분리하여 7개 파일, 179개 테스트로 확장하였습니다.

| 테스트 파일 | 대상 모듈 | 테스트 수 | 커버리지 |
|------------|----------|----------|---------|
| `test_sla_notification_templates.py` | `sla_notification_templates.py` | 43 | 100% |
| `test_throttle_sla_alert_urls.py` | `throttle_sla_alert_urls.py` | 16 | 100% |
| `test_redis_cooldown_store.py` | `redis_cooldown_store.py` | 17 | 100% |
| `test_notification_fallback_recorder.py` | `notification_fallback_recorder.py` | 12 | 92.50% |
| `test_sla_notification_handler.py` | `sla_notification.py` (핸들러) | 28 | 84.68% |
| `test_throttle_sla_notification_settings.py` | `throttle_sla_notification.py` (설정) | 28 | 100% |
| `test_format_sla_slack_blocks.py` | `unified_notification.py` (Slack 블록) | 22 | — |
| `test_sla_celery_task.py` | `sla_notification.py` (Celery 태스크) | 13 | 100% |
| **합계** | **7개 모듈** | **179** | **93.33%** |

테스트 경로: `packages/selfhealing-python/tests/unit/throttle/` ✅

### 관련 설계 문서
- [23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md](23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md)
- [08_NOTIFICATION_ARCHITECTURE.md](08_NOTIFICATION_ARCHITECTURE.md)
