# 188. Unified Notification - SLA 위반 알림 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/unified_notification.py`, `selfhealing/services/throttle/adaptive.py`

## 1. 개요

본 문서는 `AdaptiveThrottle`의 SLA 위반 시 `UnifiedNotificationManager`를 통한 알림 발송 연동을 정의합니다.

### 1.1 문제 정의

현재 `AdaptiveThrottle`은 SLA 위반 시 **EventBus 이벤트만 발행**:
- `THROTTLE_SLA_WARNING`: 경고 임계값 도달
- `THROTTLE_SLA_CRITICAL`: 위험 임계값 도달

**문제점**: 운영팀에 대한 직접적인 알림(Slack, Email, PagerDuty) 연동 부재

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
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        SLA 위반 알림 아키텍처                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐                                                          │
│  │ AdaptiveThrottle  │                                                          │
│  │                   │                                                          │
│  │ record_response() │                                                          │
│  │       │           │                                                          │
│  │       ▼           │                                                          │
│  │ _maybe_adjust_    │                                                          │
│  │   limit()         │                                                          │
│  └─────────┬─────────┘                                                          │
│            │                                                                    │
│            │ SLA 위반 감지                                                       │
│            ▼                                                                    │
│  ┌───────────────────┐    emit()     ┌───────────────────┐                      │
│  │ _emit_throttle_   │ ───────────► │     EventBus       │                      │
│  │   event()         │               └─────────┬─────────┘                      │
│  └───────────────────┘                         │                                │
│                                                │ subscribe                      │
│                                                ▼                                │
│                          ┌─────────────────────────────────────┐                │
│                          │   SLA Notification Handler          │                │
│                          │                                     │                │
│                          │   _handle_sla_warning()            │                │
│                          │   _handle_sla_critical()           │                │
│                          └──────────────┬──────────────────────┘                │
│                                         │                                       │
│                                         │ notify()                              │
│                                         ▼                                       │
│                          ┌─────────────────────────────────────┐                │
│                          │  UnifiedNotificationManager         │                │
│                          │                                     │                │
│                          │  - Cooldown Check (30분)            │                │
│                          │  - Priority Escalation              │                │
│                          │  - Channel Routing                  │                │
│                          └──────────────┬──────────────────────┘                │
│                                         │                                       │
│                  ┌──────────────────────┼──────────────────────┐               │
│                  │                      │                      │               │
│                  ▼                      ▼                      ▼               │
│           ┌──────────┐          ┌──────────┐          ┌──────────┐            │
│           │  Slack   │          │  Email   │          │ PagerDuty│            │
│           └──────────┘          └──────────┘          └──────────┘            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 알림 매핑 규칙

| Throttle 이벤트 | Notification Priority | Channels | Cooldown |
|----------------|----------------------|----------|----------|
| `THROTTLE_SLA_WARNING` | HIGH | Slack, Email | 30분 |
| `THROTTLE_SLA_CRITICAL` | CRITICAL | Slack, Email, SMS, PagerDuty | 30분 |
| `THROTTLE_LIMIT_RECOVERED` | MEDIUM | Slack | 없음 |

---

## 4. 구현 코드

### 4.1 SLA Notification Handler

**신규 파일**: `selfhealing/services/throttle/sla_notification.py`

```python
"""
SLA 위반 시 UnifiedNotification 연동 모듈.

코드 근거:
- unified_notification.py의 notify_sla() 함수
- adaptive.py의 _emit_throttle_event() 패턴
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
            subscriber_id="sla_notification_handler",
        )
        bus.subscribe(
            EventType.THROTTLE_SLA_CRITICAL,
            _handle_sla_critical,
            subscriber_id="sla_notification_handler",
        )
        bus.subscribe(
            EventType.THROTTLE_LIMIT_RECOVERED,
            _handle_limit_recovered,
            subscriber_id="sla_notification_handler",
        )

        logger.info("[SLANotification] Subscribed to throttle SLA events")
    except ImportError:
        logger.debug("[SLANotification] EventBus not available")
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to subscribe: {e}")


def _handle_sla_warning(event_data: dict[str, Any]) -> None:
    """
    SLA Warning 이벤트 처리.

    경고 수준: HIGH priority → Slack + Email
    """
    try:
        from selfhealing.services.unified_notification import (
            notify_sla,
            NotificationPriority,
        )

        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        gradient = event_data.get("gradient", 0)

        result = notify_sla(
            title="⚠️ SLA Warning: Response Time Threshold Exceeded",
            message=(
                f"Response time ({rtt_ms:.1f}ms) exceeded warning threshold ({threshold_ms}ms).\n"
                f"Throttle limit reduced: {previous_limit} → {current_limit}\n"
                f"Current gradient: {gradient:.3f}"
            ),
            domain="throttle",
            priority="high",
            source="adaptive_throttle",
            metadata={
                "rtt_ms": rtt_ms,
                "threshold_ms": threshold_ms,
                "current_limit": current_limit,
                "previous_limit": previous_limit,
                "gradient": gradient,
                "event_type": "sla_warning",
            },
        )

        if result.success:
            logger.info(
                f"[SLANotification] Warning notification sent: "
                f"channels={result.channels_sent}"
            )
        elif result.suppressed:
            logger.debug(
                f"[SLANotification] Warning suppressed: "
                f"reason={result.suppression_reason}"
            )
        else:
            logger.warning(f"[SLANotification] Warning failed: {result.error}")

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.warning(f"[SLANotification] Failed to send warning: {e}")


def _handle_sla_critical(event_data: dict[str, Any]) -> None:
    """
    SLA Critical 이벤트 처리.

    위험 수준: CRITICAL priority → Slack + Email + SMS + PagerDuty
    """
    try:
        from selfhealing.services.unified_notification import (
            notify_sla,
            NotificationPriority,
        )

        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        reduction_percent = event_data.get("reduction_percent", 0)
        gradient = event_data.get("gradient", 0)

        result = notify_sla(
            title="🔴 SLA CRITICAL: Severe Response Time Degradation",
            message=(
                f"CRITICAL: Response time ({rtt_ms:.1f}ms) exceeded critical threshold ({threshold_ms}ms).\n"
                f"Aggressive throttling applied: {previous_limit} → {current_limit} (-{reduction_percent}%)\n"
                f"Current gradient: {gradient:.3f}\n\n"
                f"Immediate action may be required."
            ),
            domain="throttle",
            priority="critical",
            source="adaptive_throttle",
            metadata={
                "rtt_ms": rtt_ms,
                "threshold_ms": threshold_ms,
                "current_limit": current_limit,
                "previous_limit": previous_limit,
                "reduction_percent": reduction_percent,
                "gradient": gradient,
                "event_type": "sla_critical",
                "requires_action": True,
            },
        )

        if result.success:
            logger.warning(
                f"[SLANotification] CRITICAL notification sent: "
                f"channels={result.channels_sent}"
            )
        else:
            logger.error(f"[SLANotification] CRITICAL notification failed: {result.error}")

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.error(f"[SLANotification] Failed to send critical: {e}")


def _handle_limit_recovered(event_data: dict[str, Any]) -> None:
    """
    Limit 복구 이벤트 처리.

    복구 알림: MEDIUM priority → Slack only
    """
    try:
        from selfhealing.services.unified_notification import notify_sla

        previous_limit = event_data.get("previous_limit", 0)
        new_limit = event_data.get("new_limit", 0)
        rtt_ms = event_data.get("rtt_ms", 0)

        result = notify_sla(
            title="✅ SLA Recovered: Throttle Limit Restored",
            message=(
                f"Throttle limit recovered: {previous_limit} → {new_limit}\n"
                f"Current RTT: {rtt_ms:.1f}ms"
            ),
            domain="throttle",
            priority="medium",
            source="adaptive_throttle",
            metadata={
                "previous_limit": previous_limit,
                "new_limit": new_limit,
                "rtt_ms": rtt_ms,
                "event_type": "limit_recovered",
            },
        )

        if result.success:
            logger.info(
                f"[SLANotification] Recovery notification sent: "
                f"channels={result.channels_sent}"
            )

    except ImportError:
        logger.debug("[SLANotification] UnifiedNotification not available")
    except Exception as e:
        logger.debug(f"[SLANotification] Failed to send recovery: {e}")


# 모듈 초기화 시 자동 구독
def initialize_sla_notifications() -> None:
    """SLA 알림 시스템 초기화."""
    _subscribe_sla_events()
```

### 4.2 Actionable Alert 포맷

**기존 코드 활용**: [unified_notification.py](../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py#L700-L800)

```python
def format_sla_slack_blocks(
    payload: NotificationPayload,
    priority: NotificationPriority,
) -> dict[str, Any]:
    """
    SLA 알림용 Slack Block Kit 메시지 포맷.

    Actionable Alert 설계 원칙 (기존 CB 알림 패턴 활용):
    - 거버넌스 유지: 원클릭 해제 대신 Admin 제어판으로 이동
    - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
    - 안전성: 운영자가 상태 확인 후 판단 가능
    """
    severity_emoji = {
        NotificationPriority.CRITICAL: "🔴",
        NotificationPriority.HIGH: "🟠",
        NotificationPriority.MEDIUM: "🟡",
        NotificationPriority.LOW: "🔵",
        NotificationPriority.INFO: "⚪",
    }.get(priority, "⚪")

    metadata = payload.metadata or {}
    rtt_ms = metadata.get("rtt_ms", 0)
    threshold_ms = metadata.get("threshold_ms", 0)
    current_limit = metadata.get("current_limit", 0)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {payload.title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*RTT:*\n{rtt_ms:.1f}ms"},
                {"type": "mrkdwn", "text": f"*Threshold:*\n{threshold_ms}ms"},
                {"type": "mrkdwn", "text": f"*Current Limit:*\n{current_limit}"},
                {"type": "mrkdwn", "text": f"*Priority:*\n{priority.value.upper()}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Details:*\n{payload.message}",
            },
        },
    ]

    # Actionable 버튼 섹션
    action_elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "📊 Grafana Dashboard", "emoji": True},
            "url": f"/grafana/d/throttle?var-service=default",
            "action_id": "view_dashboard",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "⚙️ Throttle Admin", "emoji": True},
            "url": f"/admin/throttle/status",
            "action_id": "view_admin",
            "style": "primary",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "📖 SLA Runbook", "emoji": True},
            "url": "/docs/runbooks/sla-degradation",
            "action_id": "view_runbook",
        },
    ]

    blocks.append({"type": "actions", "elements": action_elements})

    return {"blocks": blocks}
```

---

## 5. Cooldown 및 중복 방지

### 5.1 기존 메커니즘 활용

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

### 5.2 dedup_key 사용

```python
# notify_sla() 호출 시 자동 설정
result = notify_sla(
    title="...",
    domain="throttle",
    # dedup_key 자동 생성: "sla:throttle"
)
```

---

## 6. Emergency Level 연동

### 6.1 Priority Escalation

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

## 7. 테스트 시나리오

### 7.1 단위 테스트

```python
class TestSLANotificationIntegration:
    """SLA 알림 연동 테스트."""

    def test_sla_warning_sends_notification(self):
        """SLA Warning 시 Slack+Email 발송 확인."""
        with patch("selfhealing.services.unified_notification.notify_sla") as mock_notify:
            mock_notify.return_value = NotificationResult(
                success=True,
                channels_sent=["slack", "email"],
            )

            # SLA Warning 이벤트 시뮬레이션
            _handle_sla_warning({
                "current_rtt_ms": 250.0,
                "threshold_ms": 200,
                "current_limit": 80,
                "previous_limit": 100,
            })

            mock_notify.assert_called_once()
            call_args = mock_notify.call_args
            assert call_args.kwargs["priority"] == "high"
            assert call_args.kwargs["domain"] == "throttle"

    def test_sla_critical_sends_all_channels(self):
        """SLA Critical 시 모든 채널 발송 확인."""
        with patch("selfhealing.services.unified_notification.notify_sla") as mock_notify:
            mock_notify.return_value = NotificationResult(
                success=True,
                channels_sent=["slack", "email", "sms", "pagerduty"],
            )

            _handle_sla_critical({
                "current_rtt_ms": 600.0,
                "threshold_ms": 500,
                "reduction_percent": 30,
            })

            call_args = mock_notify.call_args
            assert call_args.kwargs["priority"] == "critical"

    def test_cooldown_suppresses_repeated_warnings(self):
        """30분 이내 반복 Warning 억제 확인."""
        manager = UnifiedNotificationManager()

        # 첫 번째 알림
        result1 = manager.notify(NotificationPayload(
            title="SLA Warning",
            message="Test",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle",
        ))

        # 즉시 두 번째 알림
        result2 = manager.notify(NotificationPayload(
            title="SLA Warning",
            message="Test",
            category=NotificationCategory.SLA,
            dedup_key="sla:throttle",
        ))

        assert not result1.suppressed
        assert result2.suppressed
        assert result2.suppression_reason == "cooldown"
```

---

## 8. 설정

### 8.1 LayeredSettings 통합

```python
class ThrottleSLANotificationSettings(BaseModel):
    """Throttle SLA 알림 설정."""

    # 알림 활성화 여부
    enabled: bool = True

    # Warning/Critical 알림 별도 제어
    warning_enabled: bool = True
    critical_enabled: bool = True
    recovery_enabled: bool = True

    # Cooldown 오버라이드 (초)
    warning_cooldown_seconds: int = 1800  # 30분
    critical_cooldown_seconds: int = 900   # 15분 (더 짧음)

    # 채널 오버라이드
    warning_channels: list[str] | None = None  # None이면 기본 사용
    critical_channels: list[str] | None = None
```

---

## 9. 모니터링 대시보드

### 9.1 Prometheus 메트릭 추가

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

### 9.2 Grafana 패널

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

## 10. 참조

- [UnifiedNotificationManager 소스](../../packages/selfhealing-python/src/selfhealing/services/unified_notification.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md](23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md)
- [08_NOTIFICATION_ARCHITECTURE.md](08_NOTIFICATION_ARCHITECTURE.md)
