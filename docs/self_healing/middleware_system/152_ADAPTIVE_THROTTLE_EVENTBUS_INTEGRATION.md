# 152. Adaptive Throttling - EventBus 연동 설계

## 개요

Adaptive Throttling을 Self-Healing 시스템의 EventBus와 연동하여 이벤트 기반 아키텍처로 통합합니다.
EventBus는 컴포넌트 간 느슨한 결합을 제공하며, Throttle 상태 변경을 다른 컴포넌트에 전파하는 핵심 인프라입니다.

## 현재 EventBus 구조 분석

### 지원 이벤트 타입

현재 `selfhealing/services/event_bus.py`에 정의된 EventType:

| 카테고리 | 이벤트 타입 | 설명 |
|---------|-----------|------|
| Emergency | `EMERGENCY_LEVEL_CHANGED` | 비상 레벨 변경 |
| Emergency | `EMERGENCY_ACTIVATED` | 비상 모드 활성화 |
| Emergency | `EMERGENCY_DEACTIVATED` | 비상 모드 비활성화 |
| Emergency | `EMERGENCY_RECOVERY_STARTED` | 복구 시작 |
| Emergency | `EMERGENCY_RECOVERY_COMPLETED` | 복구 완료 |
| Error Budget | `ERROR_BUDGET_CRITICAL` | 에러 예산 위험 |
| Error Budget | `ERROR_BUDGET_WARNING` | 에러 예산 경고 |
| Error Budget | `ERROR_BUDGET_RECOVERED` | 에러 예산 회복 |
| Circuit Breaker | `CIRCUIT_BREAKER_OPENED` | CB OPEN |
| Circuit Breaker | `CIRCUIT_BREAKER_CLOSED` | CB CLOSE |
| Circuit Breaker | `CIRCUIT_BREAKER_HALF_OPENED` | CB HALF_OPEN |
| Config | `CONFIG_UPDATED` | 설정 변경 |
| Config | `KILL_SWITCH_ACTIVATED` | Kill Switch 활성화 |
| Config | `KILL_SWITCH_DEACTIVATED` | Kill Switch 비활성화 |
| DLQ | `DLQ_REPLAY_BLOCKED` | Replay 차단됨 |
| DLQ | `DLQ_REPLAY_COMPLETED` | Replay 완료 |
| Chaos | `CHAOS_EXPERIMENT_BLOCKED` | 카오스 실험 차단 |
| Chaos | `CHAOS_EXPERIMENT_STARTED` | 카오스 실험 시작 |
| Chaos | `CHAOS_EXPERIMENT_STOPPED` | 카오스 실험 중지 |
| Security | `SECURITY_VIOLATION_DETECTED` | 보안 위반 감지 |
| Security | `SECURITY_VIOLATION_CRITICAL` | CRITICAL 보안 위반 |

### EventBus 핵심 기능

1. **Subscribe/Publish 패턴**: 핸들러 등록 및 이벤트 발행
2. **Priority 기반 실행**: `EventPriority.CRITICAL` > `HIGH` > `NORMAL` > `LOW`
3. **Thread-Safe**: `_subscription_lock`을 통한 동시성 제어
4. **Fail-Safe 핸들러 실행**: 핸들러 예외가 다른 핸들러 실행을 차단하지 않음

---

## Throttle 전용 EventType 추가

### 신규 이벤트 타입 정의

```
Throttle 이벤트 카테고리:
├── THROTTLE_LIMIT_CHANGED      # limit 변경 시 발행
├── THROTTLE_SLA_WARNING        # SLA Warning 임계값 도달
├── THROTTLE_SLA_CRITICAL       # SLA Critical 임계값 도달
└── THROTTLE_LIMIT_RECOVERED    # limit이 정상 범위로 회복
```

### 이벤트 데이터 구조

| 이벤트 | 필수 데이터 | 선택 데이터 |
|--------|-----------|------------|
| `THROTTLE_LIMIT_CHANGED` | `previous_limit`, `new_limit`, `reason` | `gradient`, `rtt_ms` |
| `THROTTLE_SLA_WARNING` | `current_rtt_ms`, `threshold_ms`, `current_limit` | `gradient` |
| `THROTTLE_SLA_CRITICAL` | `current_rtt_ms`, `threshold_ms`, `current_limit` | `reduction_percent` |
| `THROTTLE_LIMIT_RECOVERED` | `previous_limit`, `new_limit` | `recovery_duration_ms` |

---

## 연동 지점

### 1. Throttle → EventBus (이벤트 발행)

AdaptiveThrottle이 이벤트를 발행해야 하는 시점:

| 시점 | 발행 이벤트 | 트리거 조건 |
|-----|-----------|-----------|
| `_maybe_adjust_limit()` | `THROTTLE_LIMIT_CHANGED` | limit 값이 실제로 변경될 때 |
| `_maybe_adjust_limit()` | `THROTTLE_SLA_WARNING` | `rtt_ms >= sla_warning_ms` |
| `_maybe_adjust_limit()` | `THROTTLE_SLA_CRITICAL` | `rtt_ms >= sla_critical_ms` |
| `record_response()` | `THROTTLE_LIMIT_RECOVERED` | limit이 min_limit에서 증가할 때 |

### 2. EventBus → Throttle (이벤트 구독)

AdaptiveThrottle이 구독해야 하는 기존 이벤트:

| 구독 이벤트 | 핸들러 동작 | 우선순위 |
|-----------|-----------|---------|
| `EMERGENCY_LEVEL_CHANGED` | Emergency Level에 따라 limit 자동 조정 | HIGH |
| `CIRCUIT_BREAKER_OPENED` | 해당 서비스 limit을 min_limit으로 고정 | HIGH |
| `CIRCUIT_BREAKER_CLOSED` | limit 제한 해제, 정상 모드 복귀 | NORMAL |
| `ERROR_BUDGET_CRITICAL` | limit을 보수적으로 조정 (×0.5) | HIGH |
| `ERROR_BUDGET_RECOVERED` | limit 제한 해제 | NORMAL |
| `KILL_SWITCH_ACTIVATED` | Throttle 기능 일시 중지 | CRITICAL |

---

## 핸들러 등록 위치

### 기존 패턴 분석

`register_default_handlers()` 함수에서 기본 핸들러들을 일괄 등록:

- Emergency 이벤트 → `_on_emergency_level_changed` (HIGH)
- Error Budget 이벤트 → `_on_error_budget_critical` (CRITICAL)
- CB 이벤트 → `_on_circuit_breaker_closed`, `_on_circuit_breaker_opened_notify` 등

### Throttle 핸들러 등록 전략

1. **기존 패턴 준수**: `register_default_handlers()`에 Throttle 핸들러 추가
2. **Lazy Import**: Throttle 모듈 import 시점 지연
3. **Fail-Open**: Throttle 모듈 없으면 핸들러 등록 생략

---

## 이벤트 흐름 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Event Flow                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [RTT 증가 감지]                                                         │
│       │                                                                  │
│       ▼                                                                  │
│  ┌──────────────────┐                                                   │
│  │ AdaptiveThrottle │                                                   │
│  │ record_response()│                                                   │
│  └────────┬─────────┘                                                   │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────┐    publish()   ┌──────────────────┐              │
│  │ _maybe_adjust_   │───────────────►│    EventBus      │              │
│  │ limit()          │                │                  │              │
│  └──────────────────┘                └────────┬─────────┘              │
│                                               │                         │
│           ┌───────────────────────────────────┼─────────────────┐      │
│           │                                   │                 │      │
│           ▼                                   ▼                 ▼      │
│  ┌──────────────────┐              ┌────────────────┐  ┌────────────┐ │
│  │ Notification     │              │ Emergency Mode │  │ Prometheus │ │
│  │ Handler          │              │ Handler        │  │ Metrics    │ │
│  └──────────────────┘              └────────────────┘  └────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 이벤트 전파 방식: Sync vs Async (설계 결정)

### 배경

외부 컴포넌트(예: CB)의 상태 변경을 Throttle에 전파하는 방식:

| 모드 | 대상 | 지연 | 사용 시점 |
|------|------|------|----------|
| **Sync (직접 호출)** | 동일 프로세스 | 0ms | CB OPEN 즉시 로컬 Throttle 강등 |
| **Async (EventBus)** | 타 리전/인스턴스 | ~수백ms | Redis Pub/Sub 전파 |

### 결정: "로컬 제동은 동기, 전역 전파는 비동기"

**근거 코드**: `LocalMemoryRateLimiter`가 Redis 장애 시 로컬 폴백 패턴으로 이미 구현됨

```
외부 상태 변경 (예: CB OPEN)
    │
    ├─[Sync] 동일 프로세스: 콜백 직접 호출 → Throttle.set_limit(min_limit)
    │        (이벤트 버스 우회, 지연 0)
    │
    └─[Async] 타 인스턴스: EventBus.publish() → Redis Pub/Sub
             → 분산 핸들러 호출
```

이 패턴은 153번 문서(CB 연동)에서 상세히 다룸.

---

## 분산 환경 지원 (RedisEventBus)

### 기존 구현 확인

`services/event_bus_redis.py`에 `RedisEventBus`가 **이미 구현되어 있음**:

| 기능 | 상태 | 설명 |
|-----|------|------|
| Redis Pub/Sub | ✅ 구현됨 | `_redis_client.publish()` / `pubsub.subscribe()` |
| 다중 채널 | ✅ 구현됨 | `EMERGENCY`, `CIRCUIT_BREAKER`, `CONFIG`, `GLOBAL` 등 |
| Fallback | ✅ 구현됨 | Redis 연결 실패 시 로컬 버스로 폴백 |
| 팩토리 함수 | ✅ 구현됨 | `get_event_bus(distributed=True)` |

### Throttle 채널 추가

`EVENT_TYPE_TO_CHANNEL` 매핑에 Throttle 이벤트 추가 필요:

| EventType | Channel | 이유 |
|-----------|---------|------|
| `THROTTLE_LIMIT_CHANGED` | `THROTTLE` (신규) | 워커 간 limit 동기화 |
| `THROTTLE_SLA_CRITICAL` | `GLOBAL` | 전체 클러스터 알림 |
| `THROTTLE_LIMIT_RECOVERED` | `THROTTLE` | 워커 간 복구 동기화 |

### 순환 참조 방지 (설계 결정)

**표준: `event.source` 기반 필터링 도입**

| 패턴 | 근거 |
|-----|------|
| 기존 코드 | `_on_external_level_changed()`에서 `if event.source != "emergency_manager"` 체크 |
| Throttle 적용 | 핸들러 시작 시 `if event.source == "throttle": return` |

Throttle 핸들러 표준 패턴:
```
def _on_xxx_throttle(event):
    if event.source == "throttle":
        return  # 자기 이벤트 무시 (순환 방지)
    # 핸들러 로직
```

---

## 구현 체크리스트

### Phase 1: EventType 추가
- [x] `EventType` Enum에 Throttle 관련 이벤트 4개 추가
- [x] `EVENT_TYPE_TO_CHANNEL`에 Throttle → THROTTLE 채널 매핑 추가
- [x] 이벤트별 데이터 스키마 문서화

### Phase 2: 분산 EventBus 연동
- [x] `SELFHEALING_EVENT_CHANNELS`에 `throttle` 채널 추가
- [x] `EventChannel` Enum에 `THROTTLE` 추가
- [x] 분산 모드 활성화 시 `get_event_bus(distributed=True)` 사용

### Phase 3: Throttle 발행 로직
- [x] `AdaptiveThrottle._maybe_adjust_limit()`에 이벤트 발행 추가
- [x] `AdaptiveThrottle.record_response()`에 회복 이벤트 발행 추가
- [x] EventBus import 실패 시 Fail-Open 처리
- [x] 이벤트 발행 시 `source="throttle"` 명시

### Phase 4: Throttle 구독 핸들러
- [x] `_on_emergency_level_changed_throttle()` 핸들러 구현
- [x] `_on_circuit_breaker_opened_throttle()` 핸들러 구현
- [x] `_on_circuit_breaker_closed_throttle()` 핸들러 구현
- [x] `_on_error_budget_critical_throttle()` 핸들러 구현
- [x] `_on_error_budget_recovered_throttle()` 핸들러 구현
- [x] `_on_kill_switch_activated_throttle()` 핸들러 구현
- [x] **순환 참조 방지**: 모든 핸들러에 `event.source` 체크 추가
- [x] `register_default_handlers()`에 등록

### Phase 5: 테스트
- [x] 이벤트 발행 단위 테스트
- [x] 핸들러 동작 단위 테스트
- [ ] 통합 테스트 (EventBus ↔ Throttle)
- [ ] 분산 환경 테스트 (Redis Pub/Sub 전파)
- [x] 순환 참조 방지 테스트

---

## 참조 파일

| 파일 | 역할 |
|-----|------|
| `services/event_bus.py` | EventType 정의, SelfHealingEventBus 클래스 |
| `services/event_bus_redis.py` | RedisEventBus 분산 이벤트 버스 (이미 구현됨) |
| `services/throttle/adaptive.py` | AdaptiveThrottle 클래스 |
| `services/throttle/config.py` | ThrottleConfig, ThrottleResult |

---

**작성일**: 2026-01-29
**수정일**: 2026-01-29 (Phase 3, 4 구현 완료)
**관련 문서**: 23_ADAPTIVE_THROTTLING.md
