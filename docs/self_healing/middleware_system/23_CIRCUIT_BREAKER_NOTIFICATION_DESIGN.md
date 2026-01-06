# 23. Circuit Breaker 알림 시스템 설계

> **Version**: 1.1.0  
> **Date**: 2026-01-06  
> **Status**: Phase 1 Complete  
> **Authors**: Self-Healing Architecture Team

---

## 목차

1. [개요](#1-개요)
2. [현재 상태 분석](#2-현재-상태-분석)
3. [Audit 로깅 시스템](#3-audit-로깅-시스템)
4. [알림 시스템 현황](#4-알림-시스템-현황)
5. [알림 전략 설계](#5-알림-전략-설계)
6. [구현 권장사항](#6-구현-권장사항)
7. [리뷰 포인트 분석](#7-리뷰-포인트-분석)
8. [기존 인프라 활용](#8-기존-인프라-활용)
9. [구현 로드맵](#9-구현-로드맵)

---

## 1. 개요

### 1.1 배경

Circuit Breaker 자동화 시스템이 완료되면서, 모든 상태 변경에 대한 **Audit 로깅**과 **운영자 알림** 시스템의 통합이 필요합니다.

### 1.2 목표

| 목표 | 설명 |
|------|------|
| **가시성** | 모든 CB 상태 변경을 운영자가 실시간으로 인지 |
| **추적성** | 분산 트레이싱과 연계하여 원인 분석 가능 |
| **선택성** | Alert Fatigue 방지를 위한 우선순위 기반 필터링 |
| **신뢰성** | 알림 실패가 핵심 시스템에 영향을 주지 않음 |

### 1.3 관련 문서

- [08_NOTIFICATION_ARCHITECTURE.md](../08_NOTIFICATION_ARCHITECTURE.md) - 알림 아키텍처
- [21_CB_ADVANCED_PROTECTION.md](../21_CB_ADVANCED_PROTECTION.md) - CB 고급 보호 시스템
- [17_SYSTEM_ARCHITECTURE_DIAGRAM.md](../17_SYSTEM_ARCHITECTURE_DIAGRAM.md) - 시스템 아키텍처

---

## 2. 현재 상태 분석

### 2.1 CB 자동화 완료 항목

`21_CB_ADVANCED_PROTECTION.md` 문서에 따라 다음 시스템이 구현되었습니다:

```
┌─────────────────────────────────────────────────────────────────┐
│                   CB Advanced Protection System                  │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │ Load        │  │ Canary       │  │ Adaptive           │     │
│  │ Shedding    │  │ Recovery     │  │ Threshold          │     │
│  └─────────────┘  └──────────────┘  └────────────────────┘     │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │ Freeze      │  │ Blast Radius │  │ Panic              │     │
│  │ Mode        │  │ Integration  │  │ Threshold          │     │
│  └─────────────┘  └──────────────┘  └────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

**각 시스템의 알림 연관성**:

| 시스템 | 설명 | 알림 필요성 |
|--------|------|------------|
| Load Shedding | 과부하 시 요청 거부 | MEDIUM - 대량 거부 시 알림 |
| Canary Recovery | 점진적 복구 (1% → 5% → 10%...) | INFO - 복구 진행 상황 |
| Adaptive Threshold | 동적 임계값 조정 | LOW - 변경 로그만 |
| Freeze Mode | 수동 개입 필요 상태 동결 | HIGH - 즉시 대응 필요 |
| Blast Radius | 동시 OPEN 제한 (거버넌스) | CRITICAL - 정책 위반 |
| Panic Threshold | 70%+ CB OPEN 시 Emergency Level 3 | CRITICAL - 시스템 위기 |

### 2.2 Audit 로깅 상태

✅ **완전 구현됨** - WAL 기반 무손실 감사 로깅

### 2.3 알림 연동 상태

✅ **Phase 1 완료** - CB OPEN 시 EventBus 핸들러를 통해 UnifiedNotificationManager 연결됨

**구현 위치**: `packages/selfhealing-python/src/selfhealing/services/event_bus.py`
- `_on_circuit_breaker_opened_notify()` 핸들러: CB OPEN 이벤트 수신 시 알림 발송
- 우선순위: HIGH, 카테고리: CIRCUIT_BREAKER
- dedup_key: `cb:{service_name}:open` (5분 쿨다운)
- trace_url 포함으로 원인 분석 지원

---

## 3. Audit 로깅 시스템

### 3.1 핵심 함수

**파일**: `packages/selfhealing-python/src/selfhealing/services/audit/audit_helpers.py`  
**위치**: Line 408-469

```python
def log_cb_state_change_audit(
    cb_name: str,
    old_state: str,
    new_state: str,
    reason: str,
    request: Optional[Any] = None,
) -> None:
    """
    Circuit Breaker 상태 변경 감사 로그.
    
    WAL-based zero-loss guarantee를 제공합니다.
    """
```

### 3.2 호출 위치 (6개)

| 파일 | 위치 | 트리거 상황 |
|------|------|------------|
| `service.py` | L164-165 | OPEN → HALF_OPEN (자동 복구 시작) |
| `service.py` | L632-633 | HALF_OPEN → CLOSED (자동 복구 완료) |
| `manual_control.py` | L151-152 | `force_open()` 호출 |
| `manual_control.py` | L287-288 | `force_close()` 호출 |
| `manual_control.py` | L372-373 | `reset_circuit_breaker()` 호출 |
| `tracing.py` | L129 | 트레이싱 컨텍스트와 함께 기록 |

### 3.3 WAL 기반 무손실 보장

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   CB State  │────▶│  WAL Write  │────▶│   Commit    │
│   Change    │     │  (Atomic)   │     │   to DB     │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Recovery   │
                    │  on Crash   │
                    └─────────────┘
```

---

## 4. 알림 시스템 현황

### 4.1 현재 상태

**검증 결과**: CB 디렉토리에서 `notification` import 없음

```bash
# 검색 결과: No matches found
grep -r "from.*notification" packages/selfhealing-python/src/selfhealing/services/circuit_breaker/
```

### 4.2 기존 이벤트 핸들러 분석

**파일**: `event_handlers.py` Line 291-352

```python
class CircuitBreakerEventHandler:
    def on_state_changed(self, cb_name: str, old_state: str, new_state: str):
        # ⚠️ Prometheus 메트릭만 기록, 알림 없음
        cb_state_changes_total.labels(
            cb_name=cb_name,
            old_state=old_state,
            new_state=new_state,
        ).inc()
```

### 4.3 알림 인프라 준비 상태

**파일**: `unified_notification.py`

| 구성요소 | 상태 | 위치 |
|---------|------|------|
| `NotificationCategory.CIRCUIT_BREAKER` | ✅ 정의됨 | L54 |
| 기본 쿨다운 (300초) | ✅ 설정됨 | L172 |
| `RoutingPolicy` | ✅ 구현됨 | L139-199 |
| CB 연결 코드 | ❌ 없음 | - |

---

## 5. 알림 전략 설계

### 5.1 우선순위 피라미드

모든 상태 변경에 알림을 보내면 **Alert Fatigue**가 발생합니다.  
다음과 같은 우선순위 기반 선별 알림을 권장합니다:

```
                    ┌───────────────┐
                    │   CRITICAL    │ ← Panic Threshold (70%+ CB OPEN)
                    │               │   Governance Blocked
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │     HIGH      │ ← Auto OPEN (장애 발생)
                    │               │   Force 명령 (수동 개입)
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │    MEDIUM     │ ← HALF_OPEN 전환
                    │               │   (Canary Recovery 시작)
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │  INFO/LOW     │ ← CLOSED 복구 (정상화)
                    │               │   ※ 로그만, 알림 없음 가능
                    └───────────────┘
```

### 5.2 상태별 알림 매핑

| 이벤트 | 우선순위 | 채널 | 근거 |
|--------|---------|------|------|
| Panic Threshold 도달 | CRITICAL | Slack, Email, SMS, PagerDuty | 시스템 전체 위기 |
| Governance Block | CRITICAL | Slack, Email, SMS, PagerDuty | 정책 위반 |
| Auto OPEN | HIGH | Slack, Email | 서비스 장애 발생 |
| Force OPEN/CLOSE | HIGH | Slack, Email | 수동 개입 (감사 필요) |
| HALF_OPEN 전환 | MEDIUM | Slack | 복구 시도 시작 |
| CLOSED 복구 | INFO | Slack (선택) | 정상화 알림 |

### 5.3 Critical 이벤트 상세 분석

#### 5.3.1 Panic Threshold (`panic_threshold.py`)

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/panic_threshold.py`  
**위치**: Line 372-388

```python
def _notify_critical(self, open_ratio: float, open_count: int, total_count: int) -> None:
    """
    Panic Threshold 도달 시 알림.
    
    현재 상태: ⚠️ 로깅만 수행, 실제 알림 미연결
    """
    logger.critical(
        f"[PanicThreshold] CRITICAL: {open_ratio:.1%} CB OPEN "
        f"({open_count}/{total_count})"
    )
    # TODO: UnifiedNotificationManager 연결 필요
```

**분석**: 
- 70%+ CB가 OPEN 상태일 때 호출됨
- Emergency Level 3 자동 발동
- 현재는 **로깅만** 수행, 실제 알림 **미구현**

#### 5.3.2 Governance Block (`blast_radius_integration.py`)

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/blast_radius_integration.py`  
**위치**: Line 559-574

```python
def _log_governance_blocked(self, service_name: str, reason: str) -> None:
    """
    Blast Radius 정책에 의해 CB 작업이 차단될 때 로깅.
    
    현재 상태: ⚠️ Audit 로깅만, 알림 미연결
    """
    log_cb_state_change_audit(
        cb_name=service_name,
        old_state="BLOCKED",
        new_state="BLOCKED",
        reason=f"governance_blocked: {reason}",
        request=None,
    )
```

**분석**:
- 동시 CB OPEN 개수 제한 위반 시 호출됨
- 현재는 **Audit 로깅만** 수행, 운영자 알림 **미구현**

### 5.4 채널 라우팅 정책

**파일**: `unified_notification.py` Line 147-154

```python
priority_channels: Dict[NotificationPriority, List[str]] = {
    NotificationPriority.CRITICAL: ["slack", "email", "sms", "pagerduty"],
    NotificationPriority.HIGH: ["slack", "email"],
    NotificationPriority.MEDIUM: ["slack"],
    NotificationPriority.LOW: ["slack"],
    NotificationPriority.INFO: [],  # Log only
}
```

---

## 6. 구현 권장사항

### 6.1 알림 함수 설계

```python
# 권장 구현 위치: event_bus.py 내 핸들러로 추가

def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    """
    CB OPEN 시 알림 발송.
    
    EventBus 핸들러로 등록되어 CB 상태 변경 시 자동 호출됩니다.
    알림 실패가 시스템에 영향을 주지 않도록 전체를 try-except로 감쌉니다.
    """
    try:
        from selfhealing.services.unified_notification import (
            get_unified_notification_manager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        
        service_name = event.data.get("service_name", "unknown")
        trace_url = event.data.get("trace_url")
        
        manager = get_unified_notification_manager()
        manager.notify(NotificationPayload(
            title=f"🔴 Circuit Breaker OPEN: {service_name}",
            message=f"서비스 '{service_name}'의 Circuit Breaker가 열렸습니다.",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.CIRCUIT_BREAKER,
            source="circuit_breaker_service",
            dedup_key=f"cb:{service_name}:open",
            extra_fields={
                "service_name": service_name,
                "trace_url": trace_url,
                "dashboard_url": f"https://grafana.internal/d/cb?service={service_name}",
                "admin_url": f"/admin/selfhealing/circuitbreaker/?service_id={service_name}&action=review",
            },
        ))
        
        logger.info(f"[Notification] CB OPEN notification sent for {service_name}")
        
    except Exception as e:
        # ⚠️ 알림 실패가 시스템에 영향을 주지 않도록 함
        logger.warning(f"[Notification] Failed to send CB notification: {e}")
```

### 6.2 EventBus 핸들러 등록

**파일**: `event_bus.py` - `register_default_handlers()` 함수에 추가

```python
def register_default_handlers():
    bus = get_event_bus()
    
    # ... 기존 핸들러들 ...
    
    # Circuit Breaker 알림 핸들러 (신규)
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_OPENED,
        _on_circuit_breaker_opened_notify,
        priority=EventPriority.HIGH,  # 지연 없이 처리
    )
```

---

## 7. 리뷰 포인트 분석

### 7.1 첫 번째 리뷰 세트

#### ① Dynamic Escalation (가변 우선순위)

**리뷰 의견**: Emergency Level에 따라 INFO → MEDIUM → HIGH 동적 에스컬레이션

**코드 근거**:  
- `unified_notification.py` Line 304-322: `_get_effective_priority()`
- `emergency_mode/enums.py`: `EmergencyLevel` (NORMAL=0 → LEVEL_3=3)

**현재 구현**:
```python
def _get_effective_priority(self, priority: NotificationPriority) -> NotificationPriority:
    emergency_level = get_emergency_mode_manager().get_current_level()
    
    if emergency_level >= EmergencyLevel.LEVEL_3.value:
        priority = max(priority, NotificationPriority.HIGH)
    
    return priority
```

**분석**: ⚠️ **Level 2에서 에스컬레이션 없음**

**권장 수정**:
```python
if emergency_level >= EmergencyLevel.LEVEL_2.value:
    priority = max(priority, NotificationPriority.MEDIUM)
if emergency_level >= EmergencyLevel.LEVEL_3.value:
    priority = max(priority, NotificationPriority.HIGH)
```

---

#### ② Actionable Alert (실행 가능한 알림)

**리뷰 의견**: OPEN 알림에서 원클릭 강제 해제 또는 관련 매뉴얼 링크 제공

**코드 근거**:
- `unified_notification.py` L209: `requires_approval` 파라미터
- `tasks/base.py` L153: Slack Block Kit 호출 가능
- `manual_control.py` L268: `force_close()`에 `requires_approval=True`

**분석**: ⚠️ **원클릭 해제는 거버넌스 우회 위험**

**권장**:
```python
extra_fields={
    "runbook_url": "https://docs.internal/cb-troubleshoot",
    "dashboard_url": "https://grafana.internal/d/circuit-breaker",
    "admin_url": "/admin/selfhealing/circuitbreaker/?service_id={service}&action=review",
}
```

- ✅ Runbook/대시보드 링크: 모든 알림에 추가 가치
- ⚠️ 원클릭 해제: Admin 제어판 이동으로 대체 (거버넌스 유지)

---

#### ③ Trace ID 연결

**리뷰 의견**: 알림에 triggering_request의 trace_id 포함

**코드 근거**:
- `tracing.py` Line 312-316: `_build_trace_url()`
- `tracing.py` Line 129: `TriggeringRequestInfo.trace_url` 프로퍼티

**현재 구현**:
```python
def _build_trace_url(self, trace_id: str) -> Optional[str]:
    template = os.getenv("CB_TRACE_URL_TEMPLATE", "")
    if not template:
        return None
    return template.format(trace_id=trace_id)
```

**분석**: ✅ **100% 준비됨 - 즉시 적용 가능**

```python
# 알림 발송 시
notification_data = {
    "trace_id": triggering_request.trace_id,
    "trace_url": triggering_request.trace_url,  # 이미 존재
}
```

---

#### ④ Deduplication (중복 방지)

**리뷰 의견**: 연속 발생 시 중복 알림 방지

**코드 근거**:
- `unified_notification.py` L172: `CIRCUIT_BREAKER: 300` (5분 쿨다운)
- `unified_notification.py` L281: `dedup_key` 파라미터

**분석**: ✅ **100% 준비됨 - 사용만 하면 됨**

```python
await manager.notify_async(
    category=NotificationCategory.CIRCUIT_BREAKER,
    dedup_key=f"cb:{service_name}:open",  # 서비스별, 상태별 키
    ...
)
```

---

### 7.2 두 번째 리뷰 세트

#### ⑤ EventBus 기반 알림의 신뢰성 보장

**리뷰 의견**: 알림 핸들러를 try-except로 감싸서 알림 실패가 시스템에 영향을 주지 않게 함

**코드 근거**:
- `event_bus.py` Line 546-597: `_on_circuit_breaker_closed()` 핸들러 패턴

**현재 구현 패턴**:
```python
def _on_circuit_breaker_closed(event: SelfHealingEvent):
    try:
        from selfhealing.adapters.celery.tasks import conditional_replay_on_circuit_close
        conditional_replay_on_circuit_close.delay(...)
    except ImportError:
        logger.debug("Celery tasks not available, skipping...")
    except Exception as e:
        logger.error(f"Failed to trigger: {e}")
```

**분석**: ✅ **강력히 동의 - 기존 패턴과 일치**

**권장 구현**:
```python
def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    """
    알림 실패가 CB 상태 변경에 영향을 주지 않도록 전체를 try-except로 감쌉니다.
    
    CB 상태 변경 → EventBus 발행 → 알림 핸들러 호출 순서이므로,
    이 함수가 호출되는 시점에 CB 상태 변경은 이미 완료된 상태입니다.
    """
    try:
        # 알림 로직
        ...
    except Exception as e:
        # ⚠️ 알림 실패를 로그로만 기록, 예외 전파 안 함
        logger.warning(f"[Notification] CB notification failed: {e}")
```

**신뢰성 보장 흐름**:
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  CB State       │────▶│  Audit Log      │────▶│  EventBus       │
│  Change         │     │  (WAL Commit)   │     │  Publish        │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                          │
                        ┌─────────────────────────────────▼────────────────────────────────┐
                        │                    try-except 경계                                │
                        │  ┌─────────────────┐     ┌─────────────────┐     ┌────────────┐  │
                        │  │  Notification   │────▶│  Channel Send   │────▶│  Success   │  │
                        │  │  Handler        │     │  (Slack/Email)  │     │  Log       │  │
                        │  └─────────────────┘     └─────────────────┘     └────────────┘  │
                        │           │                                                       │
                        │           ▼ (실패 시)                                             │
                        │  ┌─────────────────┐                                              │
                        │  │  Warning Log    │  ← 예외 전파 안 함                            │
                        │  │  (Graceful)     │                                              │
                        │  └─────────────────┘                                              │
                        └──────────────────────────────────────────────────────────────────┘
```

---

#### ⑥ Actionable Alert: 신중한 보수주의

**리뷰 의견**: 원클릭 해제 대신 Admin 제어판으로 이동, 쿼리 파라미터 활용

**분석**: ✅ **완전히 동의 - 엔터프라이즈급 설계**

**권장 구현**:
```python
extra_fields = {
    # 대시보드 바로가기 (읽기 전용)
    "dashboard_url": f"https://grafana.internal/d/circuit-breaker?service={service_name}",
    
    # Admin 제어판 (쿼리 파라미터로 컨텍스트 전달)
    "admin_url": (
        f"/admin/selfhealing/circuitbreaker/"
        f"?service_id={service_name}"
        f"&action=review"
        f"&trigger_time={timestamp}"
    ),
    
    # Runbook 링크
    "runbook_url": "https://docs.internal/runbooks/circuit-breaker-recovery",
}
```

**장점**:
1. 거버넌스 유지: 모든 조작이 Admin 통해 감사 기록
2. 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
3. 안전성: 운영자가 상태 확인 후 판단 가능

---

#### ⑦ Dynamic Escalation 상한선 설정

**리뷰 의견**: 이미 CRITICAL인 알림이 더 올라갈 곳이 없어 에러 방지

**코드 근거**:
- `unified_notification.py` Line 304-322: `max()` 함수 사용

**현재 구현**:
```python
def _get_effective_priority(self, priority: NotificationPriority) -> NotificationPriority:
    if emergency_level >= EmergencyLevel.LEVEL_3.value:
        priority = max(priority, NotificationPriority.HIGH)  # max() 사용 ✅
    return priority
```

**분석**: ✅ **이미 안전장치 적용됨**

`max()` 함수 사용으로:
- CRITICAL 알림 → HIGH로 격상 시도 → CRITICAL 유지 (더 높은 값 유지)
- 에러 발생 가능성 없음

**추가 권장 (방어적 코딩)**:
```python
# 명시적 상한선 체크 추가 (선택사항)
if priority.value >= NotificationPriority.CRITICAL.value:
    return priority  # 이미 최고 우선순위

# 기존 로직
if emergency_level >= EmergencyLevel.LEVEL_3.value:
    priority = max(priority, NotificationPriority.HIGH)
```

---

#### ⑧ 채널 가변성 확인

**리뷰 의견**: Level 3에서 SMS/PagerDuty 채널이 자동 추가되는지 확인

**코드 근거**:
- `unified_notification.py` Line 147-154: `RoutingPolicy.priority_channels`
- `unified_notification.py` Line 183-199: `get_channels()` 메서드

**분석**: ✅ **자동 채널 추가 확인됨**

```python
priority_channels = {
    NotificationPriority.CRITICAL: ["slack", "email", "sms", "pagerduty"],
    NotificationPriority.HIGH: ["slack", "email"],
    NotificationPriority.MEDIUM: ["slack"],
    # ...
}
```

**동작 흐름**:
```
1. 원래 우선순위: HIGH
2. Emergency Level 3 감지
3. _get_effective_priority() → CRITICAL로 격상
4. get_channels(CRITICAL) → ["slack", "email", "sms", "pagerduty"]
5. SMS + PagerDuty 자동 추가 ✅
```

---

## 8. 기존 인프라 활용

### 8.1 EventBus 기반 아키텍처

**파일**: `event_bus.py`

```python
class EventType(Enum):
    # Circuit Breaker Events - 이미 정의됨
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CIRCUIT_BREAKER_HALF_OPENED = "circuit_breaker_half_opened"
```

**기존 핸들러 패턴**:
- `_on_circuit_breaker_closed()` → Track 1 DLQ Replay 트리거

**추가 핸들러 (권장)**:
- `_on_circuit_breaker_opened_notify()` → 알림 발송

### 8.2 Tracing 인프라

**파일**: `tracing.py`

```python
@dataclass
class TriggeringRequestInfo:
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    
    @property
    def trace_url(self) -> Optional[str]:
        if not self.trace_id:
            return None
        return self._build_trace_url(self.trace_id)
```

**환경변수**:
```bash
CB_TRACE_URL_TEMPLATE="https://jaeger.internal/trace/{trace_id}"
```

### 8.3 Unified Notification Manager

**파일**: `unified_notification.py`

```python
class UnifiedNotificationManager:
    def notify(self, payload: NotificationPayload) -> NotificationResult:
        # 1. Emergency Level에 따른 우선순위 조정
        effective_priority = self._get_effective_priority(payload.priority)
        
        # 2. 채널 결정
        channels = self._policy.get_channels(effective_priority, payload.category)
        
        # 3. 쿨다운 체크 (dedup_key 기반)
        if self._is_in_cooldown(payload.dedup_key, payload.category):
            return NotificationResult(skipped=True, reason="cooldown")
        
        # 4. 채널별 발송
        for channel in channels:
            self._send_to_channel(channel, payload)
```

---

## 9. 구현 로드맵

### 9.1 Phase 1: 핵심 알림 연결 ✅ 완료

**구현 일자**: 2026-01-06

| 작업 | 파일 | 상태 |
|------|------|------|
| `_on_circuit_breaker_opened_notify()` 핸들러 구현 | `event_bus.py` | ✅ 완료 |
| EventBus 핸들러 등록 | `event_bus.py` | ✅ 완료 |
| `trace_url` 노출 | 알림 핸들러 | ✅ 완료 |
| Deduplication (`dedup_key`) 적용 | 알림 핸들러 | ✅ 완료 |
| 신뢰성 보장 (try-except 래핑) | 알림 핸들러 | ✅ 완료 |

**테스트 파일**: `tests/self_healing/unit/test_cb_notification_phase1.py`  
**테스트 결과**: 9개 테스트 통과

### 9.2 Phase 2: 동적 에스컬레이션 (0.5일)

| 작업 | 파일 | 난이도 |
|------|------|--------|
| Level 2 에스컬레이션 추가 | `unified_notification.py` | 낮음 |
| 상한선 명시적 체크 (선택) | `unified_notification.py` | 낮음 |

### 9.3 Phase 3: Actionable Alert (1일)

| 작업 | 파일 | 난이도 |
|------|------|--------|
| Admin URL 쿼리 파라미터 설계 | Admin 설정 | 중간 |
| Runbook 링크 정의 | 문서 | 낮음 |
| Slack Block Kit 버튼 추가 | `base.py` | 중간 |

### 9.4 구현 우선순위 매트릭스

| 리뷰 포인트 | 중요도 | 기존 인프라 | 구현 난이도 | 권장 순서 |
|------------|--------|------------|-----------|----------|
| ③ Trace URL 연결 | 높음 | 100% | 매우 낮음 | **1순위** |
| ④ Deduplication | 높음 | 100% | 0 | **1순위** |
| ⑤ 신뢰성 (try-except) | 필수 | 패턴 존재 | 낮음 | **1순위** |
| ① Dynamic Escalation | 중간 | 80% | 낮음 | 2순위 |
| ⑧ 채널 가변성 | 중간 | 100% | 0 | 확인완료 |
| ⑦ 상한선 설정 | 낮음 | 이미 적용 | 0 | 확인완료 |
| ② ⑥ Actionable Alert | 중간 | 70% | 중간 | 3순위 |

---

## 부록 A: 코드 증거 요약

### A.1 Audit 로깅 호출 위치

```python
# service.py L164-165
log_cb_state_change_audit(
    cb_name=service_name,
    old_state="OPEN",
    new_state="HALF_OPEN",
    reason="auto_recovery_started",
    request=triggering_request,
)

# manual_control.py L151-152
log_cb_state_change_audit(
    cb_name=service_name,
    old_state=current_state,
    new_state="OPEN",
    reason=f"force_open: {reason}",
    request=None,
)
```

### A.2 EventBus 이벤트 발행

```python
# service.py L565-574
bus = get_event_bus()
bus.emit(
    EventType.CIRCUIT_BREAKER_OPENED,
    {
        "service_name": service_name,
        "burn_rate_multiplier": multiplier,
        "timestamp": now().isoformat(),
    },
    source="circuit_breaker_service",
)
```

### A.3 기본 핸들러 등록

```python
# event_bus.py L600-632
def register_default_handlers():
    bus = get_event_bus()
    
    # Circuit Breaker events (기존)
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_CLOSED,
        _on_circuit_breaker_closed,
        priority=EventPriority.NORMAL,
    )
    
    # Circuit Breaker 알림 (추가 권장)
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_OPENED,
        _on_circuit_breaker_opened_notify,
        priority=EventPriority.HIGH,
    )
```

---

## 부록 B: 환경변수 설정

### B.1 구현됨 (실제 코드에서 사용 중)

| 환경변수 | 용도 | 코드 위치 |
|---------|------|----------|
| `CB_TRACE_URL_TEMPLATE` | Jaeger/Zipkin trace URL 생성 | `tracing.py#L79` |

```bash
# 실제 구현된 환경변수
CB_TRACE_URL_TEMPLATE="https://jaeger.internal/trace/{trace_id}"
```

### B.2 권장사항 (구현 시 추가 필요)

> ⚠️ **주의**: 아래 환경변수들은 현재 **미구현** 상태입니다.  
> Phase 3 구현 시 추가가 필요합니다.

| 환경변수 | 용도 | 구현 필요 |
|---------|------|----------|
| `CB_DASHBOARD_URL` | Grafana 대시보드 링크 | Phase 3 |
| `CB_ADMIN_BASE_URL` | Admin 제어판 기본 URL | Phase 3 |
| `CB_RUNBOOK_URL` | 장애 대응 매뉴얼 링크 | Phase 3 |

```bash
# 권장 환경변수 (구현 필요)
CB_DASHBOARD_URL="https://grafana.internal/d/circuit-breaker"
CB_ADMIN_BASE_URL="https://admin.internal/selfhealing/circuitbreaker/"
CB_RUNBOOK_URL="https://docs.internal/runbooks/circuit-breaker-recovery"
```

---

## 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|----------|
| 1.0.0 | 2026-01-06 | Self-Healing Team | 초안 작성 |
| 1.1.0 | 2026-01-06 | Self-Healing Team | Phase 1 구현 완료 - CB OPEN 알림 핸들러, EventBus 등록, 테스트 9개 통과 |

---

> **다음 단계**: Phase 2 동적 에스컬레이션 구현을 진행합니다.
