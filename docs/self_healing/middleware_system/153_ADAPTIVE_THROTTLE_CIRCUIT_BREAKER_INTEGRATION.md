# 153. Adaptive Throttling - Circuit Breaker 연동 설계

## 개요

Adaptive Throttling과 Circuit Breaker를 연동하여 외부 서비스 장애 시 트래픽을 자동으로 제어합니다.
CB가 OPEN되면 해당 서비스로의 요청을 Throttle에서도 제한하고, CLOSE되면 정상 모드로 복귀합니다.

## 현재 Circuit Breaker 구조 분석

### CircuitBreakerService 핵심 기능

파일 위치: `selfhealing/services/circuit_breaker/service.py`

| 메서드 | 기능 | 연동 가능성 |
|--------|------|-----------|
| `should_allow(service_name)` | 요청 허용 여부 확인 | Throttle 체크 전 호출 |
| `record_failure(service_name)` | 실패 기록 | RTT 기록과 연계 |
| `record_success(service_name)` | 성공 기록 | 정상 RTT 기록 |
| `force_open(service_name, reason)` | 수동 OPEN | Throttle limit 강제 감소 |
| `force_close(service_name, reason)` | 수동 CLOSE | Throttle limit 복구 |

### CB 상태 전이

```
CLOSED ──(failures >= threshold)──► OPEN
   ▲                                  │
   │                                  │
   │                    (recovery_timeout 경과)
   │                                  │
   │                                  ▼
   └────(successes >= threshold)─── HALF_OPEN
```

### CircuitBreakerConfig 주요 설정

| 설정 | 기본값 | 설명 |
|-----|-------|------|
| `failure_threshold` | 5 | OPEN 트리거 실패 횟수 |
| `minimum_calls` | 10 | 최소 호출 수 (false positive 방지) |
| `failure_rate_threshold` | 50.0 | 실패율 기반 임계값 (%) |
| `recovery_timeout` | 60 | OPEN → HALF_OPEN 대기 시간 (초) |
| `success_threshold` | 3 | HALF_OPEN → CLOSED 성공 횟수 |
| `cb_open_burn_rate_multiplier` | 2.0 | CB OPEN 시 Error Budget 소진 배율 |

---

## 현재 CB와 RTT의 관계

### Rate Limit Cascade 감지

`selfhealing/services/circuit_breaker/protection.py`에서 Rate Limit Cascade 감지:

| 설정 | 기본값 | 설명 |
|-----|-------|------|
| `rate_limit_cascade_threshold` | 10 | 429 응답 임계값 |
| `rate_limit_cascade_window_seconds` | 60 | 감지 윈도우 |

동작 흐름:
1. `record_rate_limit_response(service_name)` 호출
2. RateLimitTracker가 429 응답 카운트
3. 임계값 초과 시 `force_open()` 자동 호출
4. CB OPEN → Self-DDoS 방지

### Rate Limit Tracker

`selfhealing/services/circuit_breaker/rate_limit_tracker.py`:
- 서비스별 429 응답 기록
- 윈도우 기반 카운트
- Backoff 레벨 관리

---

## Throttle ↔ CB 연동 지점

### 1. CB OPEN 시 Throttle 동작

| CB 상태 | Throttle 동작 | 근거 |
|--------|-------------|------|
| OPEN | 해당 서비스 limit = min_limit 고정 | 추가 요청 차단으로 복구 지원 |
| OPEN | 새 요청 자동 거부 (Fast-Fail) | CB의 should_allow()와 동기화 |

### 2. CB HALF_OPEN 시 Throttle 동작

| CB 상태 | Throttle 동작 | 근거 |
|--------|-------------|------|
| HALF_OPEN | limit = initial_limit × 0.5 | 제한적 트래픽 허용 |
| HALF_OPEN | RTT 모니터링 강화 | 복구 여부 판단 |

### 3. CB CLOSED 시 Throttle 동작

| CB 상태 | Throttle 동작 | 근거 |
|--------|-------------|------|
| CLOSED | limit 제한 해제 | 정상 모드 복귀 |
| CLOSED | Gradient 알고리즘 재활성화 | 자동 조정 재개 |

---

## 이벤트 기반 연동

### CB → Throttle 동기/비동기 분리 (설계 결정)

**결정: "로컬 제동은 동기(Sync), 전역 전파는 비동기(Async)"**

| 모드 | 대상 | 지연 | 근거 |
|------|------|------|------|
| **Sync (In-process)** | 동일 워커 | 0ms | 직접 함수 호출으로 즉시 반영 |
| **Async (Redis Pub/Sub)** | 타 리전/인스턴스 | ~수백ms | EventBus를 통해 전파 |

**문제 인식**: CB OPEN 후 수백 ms 동안 죽은 서비스에 요청 폭주 → 장애 심화

**구현 흐름**:
```
CB OPEN 발생 (워커 A)
    │
    ├─[Sync] 동일 프로세스: CBManager._on_open_callback() → AdaptiveThrottle.set_limit(min_limit)
    │        (직접 함수 호출, 이벤트 버스 대기 없음)
    │
    └─[Async] 타 인스턴스: EventBus.publish(CIRCUIT_BREAKER_OPENED)
             → Redis Pub/Sub → 워커 B, C 핸들러 호출
```

**근거 코드**: `LocalMemoryRateLimiter`가 Redis 장애 시 로컬 폴백 패턴으로 이미 구현됨

---

### CB → Throttle 이벤트 (Async 전파)

CB 상태 변경 시 EventBus를 통해 Throttle에 전파:

| 이벤트 | 발행 시점 | Throttle 핸들러 동작 |
|-------|---------|-------------------|
| `CIRCUIT_BREAKER_OPENED` | CB OPEN 시 | limit = min_limit, 해당 서비스 키 차단 |
| `CIRCUIT_BREAKER_HALF_OPENED` | OPEN → HALF_OPEN 전이 | limit = initial_limit × 0.5 |
| `CIRCUIT_BREAKER_CLOSED` | CB CLOSE 시 | limit 제한 해제, 정상 모드 |

### 이벤트 데이터 구조

CB 이벤트에 포함되는 데이터 (현재 코드 기반):

| 필드 | 타입 | 설명 |
|-----|------|------|
| `service_name` | str | 대상 서비스 이름 |
| `burn_rate_multiplier` | float | Error Budget 배율 |
| `timestamp` | str | ISO 형식 타임스탬프 |

Throttle 연동을 위해 추가 필요한 데이터:

| 필드 | 타입 | 설명 |
|-----|------|------|
| `failure_count` | int | 누적 실패 횟수 |
| `failure_rate_percent` | float | 실패율 |
| `previous_state` | str | 이전 상태 |

---

## RTT 데이터 공유

### Throttle → CB RTT 피드백

AdaptiveThrottle의 RTT 데이터를 CB의 장애 감지에 활용:

| RTT 상태 | CB 연동 동작 | 근거 |
|---------|------------|------|
| RTT >= SLA_CRITICAL (500ms) | CB failure 카운트 증가 고려 | 응답 지연 = 잠재적 장애 |
| RTT 급격히 증가 (gradient > 0.3) | CB 경고 알림 트리거 | 장애 조짐 사전 감지 |
| RTT 정상 유지 | CB success 카운트에 반영 | 서비스 정상 확인 |

### CB → Throttle 상태 피드백

CB 상태 정보를 Throttle 조정에 활용:

| CB 상태 정보 | Throttle 활용 | 근거 |
|------------|-------------|------|
| `failure_count` | limit 감소 비율 결정 | 장애 심각도 반영 |
| `state` | 모드 결정 (정상/제한/차단) | CB 상태와 동기화 |
| `opened_at` | 복구 시간 추정 | HALF_OPEN 전이 예측 |

---

## 서비스별 Throttle 분리

### 현재 Throttle 구조의 한계

현재 `AdaptiveThrottle`은 글로벌 싱글톤:
- 모든 서비스에 동일한 limit 적용
- 서비스별 CB 상태 반영 불가

### 서비스별 Throttle 확장 설계

```
┌─────────────────────────────────────────────────────────────────────┐
│                   ThrottleRegistry (신규)                            │
├─────────────────────────────────────────────────────────────────────┤
│  service_throttles: dict[str, AdaptiveThrottle]                     │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ payment_api     │  │ external_api    │  │ notification    │     │
│  │ Throttle        │  │ Throttle        │  │ Throttle        │     │
│  │ limit=50        │  │ limit=100       │  │ limit=200       │     │
│  │ (CB: OPEN)      │  │ (CB: CLOSED)    │  │ (CB: CLOSED)    │     │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘     │
│                                                                      │
│  get_throttle(service_name) → AdaptiveThrottle                      │
│  on_cb_state_changed(service_name, state) → void                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## CB Fallback과 Throttle 연계

### 현재 CB Fallback 전략

`should_allow_with_fallback()` 메서드에서 지원하는 전략:

| 전략 | 동작 | Throttle 연계 |
|-----|------|-------------|
| `cache` | 캐시된 데이터 반환 | Throttle 체크 스킵 |
| `dlq` | DLQ에 저장 후 나중에 재시도 | Throttle deny 시에도 DLQ 저장 |
| `default_response` | 기본 응답 반환 | Throttle 체크 스킵 |
| `block` (기본) | 요청 거부 | Throttle deny와 동일 |

### Throttle Deny 시 DLQ 연계

Throttle에서 요청이 거부될 때 CB의 DLQ 전략 활용:

1. Throttle deny 발생
2. CB fallback_strategy가 `dlq`인 경우
3. `_enqueue_to_dlq()` 호출하여 저장
4. CB CLOSE 또는 limit 회복 시 replay

---

## Recovery Dampening (회복 지연)

### 설계 결정

CB CLOSE 또는 Emergency NORMAL 복귀 시 **즉시 100% 복구하지 않음** (Thundering Herd 방지)

| 단계 | 복구 비율 | 대기 시간 | 근거 |
|-----|---------|---------|------|
| 1단계 | 이전 제한의 80% | 0초 | 급격한 트래픽 증가 방지 |
| 2단계 | 이전 제한의 90% | 30초 | 안정화 확인 |
| 3단계 | 100% | 60초 | 완전 복구 |

### 근거 코드

기존 `RecoveryGate` 패턴 활용:
- `recovery_gate.py`: `check_recovery_allowed()` 메트릭 기반 복구 허용 확인
- `manager.py`: `start_gradual_recovery()` 점진적 복구 메서드

### 구현 방식

1. CB CLOSE 이벤트 수신
2. `_recovery_dampening_active = True` 플래그 설정
3. 30초간 `recovery_dampening_multiplier = 0.8` 적용
4. Gradient 알고리즘은 백그라운드에서 계속 계산 (적용만 지연)

---

## Redis Lua 스크립트 기반 원자적 업데이트

### 설계 결정

분산 환경에서 다수의 워커가 동시에 limit 업데이트 시 **Lua 스크립트로 Race Condition 방지**

### 근거 코드

기존 `lua_atomic.py` 패턴:
- `LUA_ATOMIC_ADD_INTEGRITY`: 원자적 sequence 할당 + state 업데이트
- `evalsha` / `eval` 폴백 패턴

### Throttle Limit 업데이트 Lua 스크립트 설계

| 키 | 용도 |
|---|------|
| `throttle:limit:{service}` | 현재 limit 값 |
| `throttle:rtt:{service}` | 최근 RTT 샘플 (ZSET) |
| `throttle:last_safe_limit:{service}` | 마지막 안전 limit (Cold Start 복구용) |

---

## Redis 다운 시 Safe-Open 전략 (설계 결정)

**결정: "보수적 Fail-Open (Safe-Open)"**

| 상황 | 동작 | 근거 |
|------|------|------|
| Redis 정상 | 분산 limit 동기화 | 정상 모드 |
| Redis 다운 | `_last_known_safe_limit` 유지 | 완전 차단도, 완전 허용도 아님 |
| Cold Start (Redis 복구) | Redis에서 마지막 안전 limit 로드 | `throttle:last_safe_limit:{service}` |

**문제 인식**:
- 완전 Fail-Open: "제동 장치 고장 → 속도 제한 없이 달림" = 서비스 붕괴
- 완전 Fail-Closed: 비즈니스 손실 큼

**근거 코드**: `LocalMemoryRateLimiter` 패턴이 이미 구현됨
- `_FALLBACK_EMERGENCY_RATE_LIMIT = 10` 상수
- Redis 장애 시 `_check_local_limit()` 폴백 호출

---

## 구현 체크리스트

### Phase 1: 이벤트 핸들러 구현
- [x] `_on_circuit_breaker_opened_throttle()` 핸들러
- [x] `_on_circuit_breaker_closed_throttle()` 핸들러
- [x] `_on_circuit_breaker_half_opened_throttle()` 핸들러
- [x] `register_default_handlers()`에 등록

### Phase 1.5: Sync 콜백 구현
- [x] `CircuitBreakerService.register_state_change_callback()` 인터페이스 추가
- [x] `ThrottleRegistry.on_circuit_breaker_state_changed()` 동기 콜백 메서드
- [x] CB OPEN 시 로컬 Throttle 즉시 강등 (EventBus 우회)

### Phase 2: 서비스별 Throttle
- [x] `ThrottleRegistry` 클래스 설계
- [x] 서비스별 `ServiceThrottleConfig` 지원
- [x] CB 상태별 limit 자동 조정 로직

### Phase 3: RTT 데이터 공유
- [x] Throttle RTT → CB 피드백 인터페이스 (`ThrottleCircuitBreakerBridge`)
- [x] CB 상태 → Throttle 조정 로직
- [x] 공유 메트릭 정의 (`RTTMetrics`, `ServiceHealthMetrics`)

### Phase 4: DLQ 연계
- [x] Throttle deny 시 DLQ 저장 옵션
- [x] CB CLOSE 시 Throttle deny 항목 replay

### Phase 5: Recovery Dampening
- [x] `_recovery_dampening_active` 플래그 추가
- [x] `recovery_dampening_multiplier` 설정 (기본 0.8)
- [x] 30초 댐핑 구간 타이머 구현
- [x] Gradient 계산 유지, 적용만 지연

### Phase 6: Redis 원자적 업데이트
- [x] Throttle Limit 업데이트 Lua 스크립트 작성
- [x] `throttle:last_safe_limit:{service}` 키 저장
- [x] Cold Start 시 마지막 안전 limit 복구 로직

### Phase 7: Safe-Open 폴백
- [x] `_last_known_safe_limit` 필드 추가
- [x] Redis 연결 정상 시 limit 주기적 저장
- [x] Redis 장애 감지 시 `_last_known_safe_limit` 사용
- [x] `LocalMemoryRateLimiter` 패턴 적용

### Phase 8: 테스트
- [x] CB OPEN → Throttle limit 감소 테스트
- [x] CB CLOSE → Throttle limit 복구 테스트
- [x] Recovery Dampening 테스트 (30초 댐핑)
- [x] 통합 시나리오 테스트
- [x] 분산 환경 Race Condition 테스트

---

## 시퀀스 다이어그램

### CB OPEN 시 Throttle 연동

```
┌─────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────────────┐
│ Client  │    │ CB Service  │    │  EventBus   │    │ Throttle     │
└────┬────┘    └──────┬──────┘    └──────┬──────┘    └──────┬───────┘
     │                │                   │                  │
     │  failures++    │                   │                  │
     │───────────────►│                   │                  │
     │                │                   │                  │
     │                │ threshold 도달    │                  │
     │                │ CB → OPEN         │                  │
     │                │                   │                  │
     │                │  publish          │                  │
     │                │  CB_OPENED        │                  │
     │                │──────────────────►│                  │
     │                │                   │                  │
     │                │                   │  handler 호출    │
     │                │                   │─────────────────►│
     │                │                   │                  │
     │                │                   │                  │ limit = min_limit
     │                │                   │                  │ 서비스 차단 플래그
     │                │                   │                  │
```

---

## 참조 파일

| 파일 | 역할 |
|-----|------|
| `services/circuit_breaker/service.py` | CircuitBreakerService 메인 클래스, 동기 콜백 지원 |
| `services/circuit_breaker/config.py` | CircuitBreakerConfig 설정 |
| `services/circuit_breaker/protection.py` | Rate Limit Cascade 감지 |
| `services/circuit_breaker/rate_limit_tracker.py` | 429 응답 추적 |
| `services/throttle/adaptive.py` | AdaptiveThrottle 클래스 |
| `services/throttle/registry.py` | ThrottleRegistry, 서비스별 Throttle 관리 |
| `services/throttle/cb_bridge.py` | ThrottleCircuitBreakerBridge, RTT 데이터 공유 |
| `services/throttle/dlq_integration.py` | Throttle DLQ 연계, deny 요청 저장 및 replay |
| `services/throttle/recovery_dampening.py` | Recovery Dampening, 점진적 복구 관리 |
| `services/throttle/redis_lua.py` | Redis Lua 스크립트, 원자적 limit 업데이트 |
| `services/throttle/safe_open_fallback.py` | Safe-Open 폴백, Redis 장애 시 로컬 캐시 사용 |
| `services/event_bus.py` | CB 이벤트 핸들러, HALF_OPENED 핸들러 포함 |
| `services/emergency_mode/recovery_gate.py` | RecoveryGate (복구 허용 체크 패턴) |
| `audit/performance/lua_atomic.py` | Lua 스크립트 원자적 연산 패턴 |

---

**작성일**: 2026-01-29
**수정일**: 2026-01-29 (Phase 4-7 구현 완료)
**관련 문서**: 23_ADAPTIVE_THROTTLING.md, 21_CB_ADVANCED_PROTECTION.md
