# 155. Adaptive Throttling P0 연동 마스터 플랜

## 개요

이 문서는 Adaptive Throttling을 Self-Healing 시스템의 P0 우선순위 컴포넌트들과
연동하기 위한 마스터 플랜입니다.

## P0 컴포넌트 연동 문서

| 문서 번호 | 컴포넌트 | 연동 목적 |
|----------|---------|---------|
| 152 | EventBus | 이벤트 기반 아키텍처 통합 |
| 153 | Circuit Breaker | CB 상태와 Throttle 동기화 |
| 154 | Emergency Mode | 비상 레벨별 Throttle 자동 조정 |

---

## 연동 아키텍처 전체도

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Adaptive Throttling Integration Architecture            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                           ┌───────────────────┐                             │
│                           │    EventBus       │                             │
│                           │  (Central Hub)    │                             │
│                           └─────────┬─────────┘                             │
│                                     │                                        │
│         ┌───────────────────────────┼───────────────────────────┐           │
│         │                           │                           │           │
│         ▼                           ▼                           ▼           │
│  ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐     │
│  │ Emergency Mode  │      │ Circuit Breaker │      │ Adaptive        │     │
│  │ Manager         │      │ Service         │      │ Throttle        │     │
│  └────────┬────────┘      └────────┬────────┘      └────────┬────────┘     │
│           │                        │                        │               │
│           │ EMERGENCY_LEVEL_       │ CIRCUIT_BREAKER_       │ THROTTLE_    │
│           │ CHANGED                │ OPENED/CLOSED          │ LIMIT_CHANGED│
│           │                        │                        │               │
│           └────────────────────────┼────────────────────────┘               │
│                                    │                                         │
│                                    ▼                                         │
│                           ┌───────────────────┐                             │
│                           │ Throttle Handler  │                             │
│                           │ (Event Listener)  │                             │
│                           └───────────────────┘                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 신규 EventType 정의

### Throttle 이벤트 (152번 문서)

| EventType | 발행 시점 | 우선순위 |
|-----------|---------|---------|
| `THROTTLE_LIMIT_CHANGED` | limit 값 변경 시 | NORMAL |
| `THROTTLE_SLA_WARNING` | RTT >= sla_warning_ms | HIGH |
| `THROTTLE_SLA_CRITICAL` | RTT >= sla_critical_ms | CRITICAL |
| `THROTTLE_LIMIT_RECOVERED` | limit 정상 복구 시 | NORMAL |

---

## 핸들러 등록 계획

### Throttle이 구독할 이벤트

| 이벤트 | 핸들러 | 우선순위 | 동작 |
|-------|--------|---------|------|
| `EMERGENCY_LEVEL_CHANGED` | `_on_emergency_level_changed_throttle` | HIGH | Level별 limit 조정 |
| `CIRCUIT_BREAKER_OPENED` | `_on_cb_opened_throttle` | HIGH | 서비스별 limit 최소화 |
| `CIRCUIT_BREAKER_CLOSED` | `_on_cb_closed_throttle` | NORMAL | limit 복구 |
| `CIRCUIT_BREAKER_HALF_OPENED` | `_on_cb_half_opened_throttle` | NORMAL | limit 50% 복구 |
| `KILL_SWITCH_ACTIVATED` | `_on_kill_switch_throttle` | CRITICAL | Throttle 비활성화 |

### 다른 컴포넌트가 구독할 Throttle 이벤트

| 이벤트 | 잠재적 구독자 | 동작 |
|-------|------------|------|
| `THROTTLE_SLA_CRITICAL` | Unified Notification | 알림 발송 |
| `THROTTLE_SLA_CRITICAL` | Emergency Mode | 자동 레벨 상승 고려 |
| `THROTTLE_LIMIT_CHANGED` | Prometheus Metrics | 메트릭 업데이트 |

---

## 상태 연동 매핑

### Emergency Level → Throttle Limit

| Emergency Level | Throttle Limit | 계산 |
|-----------------|---------------|------|
| NORMAL (0) | 100% | `initial_limit × 1.0` |
| LEVEL_1 (1) | 80% | `initial_limit × 0.8` |
| LEVEL_2 (2) | 50% | `initial_limit × 0.5` |
| LEVEL_3 (3) | min_limit | `min_limit` 고정 |

### CB State → Throttle Limit

| CB State | Throttle Limit | 적용 범위 |
|----------|---------------|----------|
| CLOSED | 정상 (Gradient 알고리즘) | 해당 서비스 |
| HALF_OPEN | 50% | 해당 서비스 |
| OPEN | min_limit | 해당 서비스 |

### 우선순위 결정

여러 조건이 동시에 적용될 때:

```
최종 limit = min(
    Emergency Level 기반 limit,
    CB State 기반 limit,
    Gradient 알고리즘 limit
)
```

---

## 구현 로드맵 (확장)

### Phase 1: 기반 작업 (Week 1)

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| EventType 추가 | `event_bus.py` | Throttle 관련 이벤트 4개 추가 |
| 이벤트 데이터 스키마 | (신규) | 각 이벤트별 데이터 구조 정의 |
| 핸들러 스켈레톤 | `event_bus.py` | 핸들러 함수 틀 추가 |
| ThrottleConfig 확장 | `throttle/config.py` | EM/CB 연동 설정 필드 |

### Phase 2: EventBus 연동 (Week 2) - 152번

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| 이벤트 발행 로직 | `throttle/adaptive.py` | limit 변경 시 이벤트 발행 |
| 핸들러 등록 | `event_bus.py` | `register_default_handlers()`에 추가 |
| **분산 EventBus 채널** | `event_bus_redis.py` | `THROTTLE` 채널 추가 |
| **순환 참조 방지** | `throttle/adaptive.py` | `event.source` 체크 로직 |
| 단위 테스트 | (신규) | 이벤트 발행/구독 테스트 |

### Phase 3: Circuit Breaker 연동 (Week 3) - 153번

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| CB 이벤트 핸들러 | `throttle/adaptive.py` | CB 상태별 limit 조정 |
| 서비스별 Throttle 구조 | (신규) | `ThrottleRegistry` 설계 |
| RTT 피드백 인터페이스 | `throttle/adaptive.py` | CB에 RTT 데이터 제공 |
| **Lua 원자적 업데이트** | `throttle/lua_atomic.py` | Redis Lua 스크립트 |

### Phase 4: Emergency Mode 연동 (Week 4) - 154번

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| Emergency 핸들러 | `throttle/adaptive.py` | Level별 limit 조정 |
| 상태 동기화 | `throttle/adaptive.py` | 초기화 시 Level 확인 |
| **Gradient Freeze** | `throttle/adaptive.py` | LEVEL_3에서 Gradient 적용 중단 |
| **Hard-Cap 로직** | `throttle/adaptive.py` | EM/CB/Gradient 최소값 적용 |
| **Full Stop 조건** | `throttle/adaptive.py` | 3중 조건 시 min_limit=0 |

### Phase 5: Recovery Dampening (Week 5)

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| Gradual Recovery 로직 | `throttle/adaptive.py` | 80%→90%→100% 단계 |
| Recovery 스케줄러 | `throttle/adaptive.py` | 30초 간격 step-up |
| 메트릭 기반 검증 | `throttle/adaptive.py` | RTT 확인 후 다음 단계 |

### Phase 6: 안정성 강화 (Week 6)

| 작업 | 담당 파일 | 설명 |
|-----|---------|------|
| **Cold Start 복구** | `throttle/adaptive.py` | Redis에서 마지막 안전 limit 로드 |
| **Safe-Open 폴백** | `throttle/adaptive.py` | Redis 다운 시 `_last_known_safe_limit` 유지 |
| **Sync 콜백 구현** | `circuit_breaker/service.py` | CB OPEN 시 로컬 Throttle 즉시 강등 |
| Admin Override 연동 | `throttle/adaptive.py` | 수동 limit 우선 적용 |
| Drift 감지 | `throttle/adaptive.py` | Redis 동기화 주기 검증 |

### Phase 7: 통합 테스트 (Week 7)

| 작업 | 설명 |
|-----|------|
| 통합 테스트 | 3개 컴포넌트 연동 시나리오 |
| **X-Test (EM 레벨)** | EM 레벨 강제 상승 시뮬레이션 |
| **X-Test (CB OPEN)** | CB OPEN 강제 시뮬레이션 |
| 부하 테스트 | 이벤트 폭풍 상황 테스트 |
| Chaos 테스트 | 장애 상황 시뮬레이션 |
| **Postmortem 연동** | Throttle limit history 박제 검증 |

---

## 설정 확장

### ThrottleConfig 추가 필드

| 필드 | 타입 | 기본값 | 설명 |
|-----|------|-------|------|
| `emergency_level_multipliers` | dict | `{0: 1.0, 1: 0.8, 2: 0.5, 3: 0.0}` | Level별 배율 |
| `cb_open_limit_percent` | float | 0.0 | CB OPEN 시 limit 비율 |
| `cb_half_open_limit_percent` | float | 0.5 | CB HALF_OPEN 시 limit 비율 |
| `enable_event_integration` | bool | True | 이벤트 연동 활성화 |
| `sync_on_startup` | bool | True | 시작 시 상태 동기화 |
| `recovery_dampening_enabled` | bool | True | Recovery Dampening 활성화 |
| `recovery_steps` | list | `[0.8, 0.9, 1.0]` | 복구 단계 비율 |
| `recovery_step_interval_ms` | int | 30000 | 복구 단계 간격 (ms) |
| `gradient_freeze_on_level_3` | bool | True | LEVEL_3에서 Gradient 중단 |
| `full_stop_conditions_enabled` | bool | True | 3중 조건 Full Stop |
| `redis_last_safe_limit_key` | str | `throttle:last_safe_limit:{service}` | Cold Start 복구 키 |
| `safe_open_fallback_enabled` | bool | True | Redis 다운 시 Safe-Open 활성화 |
| `static_safe_limit_percent` | float | 0.5 | Safe-Open 시 용량 50% |
| `sync_callback_enabled` | bool | True | CB OPEN Sync 콜백 활성화 |

---

## 설계 결정 요약

| 번호 | 항목 | 결정 | 근거 |
|-----|------|------|------|
| 1 | Gradient Freeze | 백그라운드 계산 O, 적용 X | RecoveryGate 패턴 |
| 2 | Netflix Min RTT | Phase 2 연기 | 현재 EMA 충분 |
| 3 | Admin Override | 최우선 적용 | escalation_audit 패턴 |
| 4 | EventBus 분산 | RedisEventBus 사용 | 이미 구현됨 |
| 5 | Race Condition | Lua 스크립트 | lua_atomic 패턴 |
| 6 | Cold Start | Redis 마지막 limit | recovery_gate 패턴 |
| 7 | Full Stop | 3중 조건 (LEVEL_3+DB_CB+Budget) | 최악 상황만 차단 |
| 8 | CascadeEvent | EM/CB 강등만 기록 | cascade_load_shedding 패턴 |
| 9 | 순환 참조 | event.source 필터링 | coordinator 패턴 |
| 10 | Postmortem | limit history 박제 | TimelineSnapshot 패턴 |
| 11 | X-Test 우선순위 | EM 레벨 먼저 | P0 목표 우선 |
| 12 | CB 연동 Sync/Async | 로컬 동기 + 전역 비동기 | LocalMemoryRateLimiter 패턴 |
| 13 | Redis 다운 | Safe-Open (_last_known_safe_limit) | get_rate_limit_config() 폴백 |
| 14 | SLA 동적배포 | Phase 2 연기 (RuntimeConfig 패턴) | get_rate_limit_config() |
| 15 | 메모리 관리 | Phase 2 연기 (Time-Bucketed) | sample_window_seconds 활용 |

---

## 메트릭 추가 계획

### Prometheus 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-----------|------|-------|------|
| `selfhealing_throttle_limit` | Gauge | `service` | 현재 limit |
| `selfhealing_throttle_rtt_ms` | Histogram | `service` | RTT 분포 |
| `selfhealing_throttle_gradient` | Gauge | `service` | 현재 gradient |
| `selfhealing_throttle_denied_total` | Counter | `service`, `reason` | 거부된 요청 수 |
| `selfhealing_throttle_emergency_adjustments` | Counter | `level` | Emergency 조정 횟수 |
| `selfhealing_throttle_cb_adjustments` | Counter | `service`, `state` | CB 조정 횟수 |

---

## 감사 로그 추가 계획

### 감사 이벤트

| 이벤트 | 트리거 | 기록 데이터 |
|-------|-------|-----------|
| `throttle_limit_adjusted` | limit 변경 시 | old_limit, new_limit, reason, trigger_source |
| `throttle_emergency_sync` | Emergency 연동 시 | emergency_level, applied_multiplier |
| `throttle_cb_sync` | CB 연동 시 | service_name, cb_state, applied_limit |
| `throttle_sla_breach` | SLA 위반 시 | rtt_ms, threshold, current_limit |

### CascadeEvent 기록 정책 (설계 결정)

**결정: "비상 상황으로 인한 강제 강등"만 CascadeEvent 기록**

| 기록 대상 | CascadeEvent 기록 | 메트릭만 |
|----------|-----------------|---------|
| 일반 Rejected (Rate Limit) | ❌ | ✅ |
| EM 연동 강등 | ✅ | ✅ |
| CB OPEN 강등 | ✅ | ✅ |
| Kill Switch 활성화 | ✅ | ✅ |

근거: `cascade_load_shedding.py`의 우선순위 기반 드롭 로직 - CRITICAL 이벤트만 기록

---

## Postmortem 연동 (설계 결정)

### Throttle Limit 변화 박제

Postmortem 생성 시 Throttle 상태 포함:

| 필드 | 타입 | 설명 |
|-----|------|------|
| `throttle_limit_history` | list[dict] | 인시던트 기간 limit 변화 |
| `throttle_min_limit` | int | 기간 중 최저 limit |
| `throttle_adjustment_count` | int | 조정 횟수 |

근거: 기존 `TimelineSnapshot`에 `metrics_at_open`, `peak_metrics` 필드 패턴

---

## 테스트 시나리오

### 시나리오 1: Emergency Level 상승

```
Given: NORMAL 상태, Throttle limit = 100
When: Emergency LEVEL_2 발생
Then: Throttle limit = 50 (100 × 0.5)
And: Gradient 계산 계속, 적용만 Hard-Cap
```

### 시나리오 2: CB OPEN

```
Given: payment-api CB CLOSED, Throttle limit = 100
When: payment-api CB OPEN 발생
Then: payment-api Throttle limit = min_limit (10)
```

### 시나리오 3: 복합 상황 (Hard-Cap 적용)

```
Given: Emergency LEVEL_1 (limit = 80)
When: payment-api CB OPEN
Then: payment-api limit = min(80, min_limit) × EM_multiplier = min_limit
```

### 시나리오 4: Recovery Dampening

```
Given: Emergency LEVEL_3 (limit = min_limit), CB OPEN
When: CB CLOSE → Emergency NORMAL
Then:
  - 0초: limit = initial_limit × 0.8 (80)
  - 30초: limit = initial_limit × 0.9 (90)
  - 60초: limit = initial_limit × 1.0 (100)
```

### 시나리오 5: LEVEL_3 Gradient Freeze

```
Given: Emergency LEVEL_3, RTT 개선 중 (gradient < 0)
When: Gradient 알고리즘이 limit 증가 시도
Then: limit 변경 안 됨 (Freeze 상태)
And: RTT 샘플 수집은 계속됨
```

### 시나리오 6: Full Stop 조건

```
Given: LEVEL_3 + DB CB OPEN + Error Budget Exhausted
When: 3중 조건 충족
Then: min_limit = 0 (완전 차단)
And: KILL_SWITCH 수준 알림 발송
```

---

## X-Test 시뮬레이션 (설계 결정)

### 우선순위: EM 레벨 상승 먼저

| 순서 | 시나리오 | 이유 |
|-----|---------|------|
| 1순위 | EM 레벨 강제 상승 | P0 핵심 목표, 상위 계층 검증 |
| 2순위 | CB OPEN 시뮬레이션 | P0 연동 검증 |
| 3순위 | RTT 지연 주입 | Phase 2, Netflix Gradient 정확도 검증 |

---

## 위험 요소 및 완화 방안

| 위험 | 영향 | 완화 방안 |
|-----|------|---------|
| 이벤트 폭풍 | CPU 과부하 | 이벤트 디바운싱, 배치 처리 |
| 순환 의존 | 무한 루프 | **event.source 필터링 (표준 도입)** |
| 상태 불일치 | 잘못된 limit | Check on Use 패턴, Drift 감지 |
| 핸들러 예외 | 연동 실패 | Fail-Open, 예외 격리 |
| Thundering Herd | 복구 시 폭주 | **Recovery Dampening (80→90→100%)** |
| Race Condition | limit 불일치 | **Redis Lua 스크립트 원자적 업데이트** |
| Cold Start | 폭주 위험 | **Redis에서 마지막 안전 limit 복구** |
| Redis 다운 | 제어 불능 | **Safe-Open: _last_known_safe_limit 유지** |
| CB 반응 지연 | 죽은 서비스 폭주 | **Sync 콜백 (로컬 즉시), Async 전파 (분산)** |

---

## Phase 2 연기 항목

| 항목 | 이유 | 대안 | 코드 근거 |
|-----|------|------|----------|
| Netflix Min RTT (24h 최저) | Redis 저장소 설계 필요 | 현재 EMA 충분 | `deque(maxlen=100)` |
| SLA 임계치 Canary Rollout | 71번 문서와 별도 연동 | settings 기반 | `get_rate_limit_config()` |
| **SLA 동적 배포 (DynamicConfig)** | RuntimeConfig 패턴 적용 필요 | settings 기반 | `RuntimeConfigManager` |
| **Time-Bucketed Window** | 메모리 관리 개선 필요 | maxlen=100 유지 | `sample_window_seconds` |

---

## Phase 2 상세 설계

### 2번 리뷰: SLA 동적 배포 (DynamicConfig)

**문제**: `sla_critical_ms`, `sla_warning_ms`가 `ThrottleConfig`에 하드코딩

**해결 방향**: `RuntimeConfigManager` 패턴 적용 (이미 구현됨)

| 우선순위 | 소스 | 근거 코드 |
|---------|------|----------|
| 1 | `RuntimeConfigManager.get_throttle_config()` | 런타임 동적 설정 |
| 2 | `ThrottleSettings` (환경변수) | 정적 설정 |
| 3 | 하드코딩 fallback 상수 | 최후 폴백 |

**근거 코드**: `get_rate_limit_config()` - 3단계 폴백 패턴 이미 구현

**Phase 2 작업**:
- [ ] `get_throttle_config()` 함수 구현
- [ ] Canary Rollout 연동 (71번 문서)
- [ ] SLA 임계치 변경 시 자동 적용

---

### 8번 리뷰: Time-Bucketed Window (메모리 관리)

**문제**: `maxlen=100`은 10k TPS에서 10ms 데이터만 담음

**현재 구현**:
```
_samples: deque[RTTSample] = deque(maxlen=100)  # 개수 기반
sample_window_seconds = 10.0  # 시간 기반 (get_stats()에서 사용)
```

**Phase 2 개선 방향**: Time-Bucketed 접근

| 방식 | 장점 | 단점 |
|------|------|------|
| 현재 (maxlen=100) | 단순, 메모리 고정 | 고TPS에서 샘플 부족 |
| maxlen=1000 | 샘플 충분 | 메모리 증가 |
| **Time-Bucket** | 시간 기반 정확도 | 구현 복잡도 |

**Phase 2 작업**:
- [ ] 초당 1개 버킷 (1초 평균 RTT 저장)
- [ ] 10초 윈도우 = 10개 버킷
- [ ] `array.array('f')` 사용으로 메모리 절감

---

## 참조 문서

| 문서 번호 | 제목 |
|----------|------|
| 152 | Adaptive Throttle - EventBus 연동 설계 |
| 153 | Adaptive Throttle - Circuit Breaker 연동 설계 |
| 154 | Adaptive Throttle - Emergency Mode 연동 설계 |
| 23 | Adaptive Throttling (Netflix Gradient Algorithm) |
| 21 | CB Advanced Protection |
| 72 | Emergency Coordination Layer |
| 76 | Cascade Event Audit (CascadeEvent 기록 정책) |
| 130 | Postmortem Generation |

---

**작성일**: 2026-01-29
**수정일**: 2026-01-29 (15개 설계 결정, Sync/Async, Safe-Open, Phase 2 상세 추가)
**버전**: 1.3
**상태**: P0 구현 완료

---

## 구현 현황

### 구현 완료 항목 (2026-01-29)

| 항목 | 상태 | 구현 파일 | 테스트 파일 |
|------|------|----------|------------|
| ThrottleSettings 확장 필드 (14개) | ✅ 완료 | `settings/throttle.py` | `test_throttle_settings_extension.py` |
| Prometheus 메트릭 (6개) | ✅ 완료 | `services/metrics/definitions.py`, `services/throttle/adaptive.py` | `test_throttle_metrics.py` |
| 감사 로그/CascadeEvent (4개) | ✅ 완료 | `services/throttle/audit.py` | `test_throttle_audit.py` |
| Postmortem 연동 | ✅ 완료 | `services/throttle/postmortem.py` | `test_throttle_postmortem.py` |

### ThrottleSettings 확장 필드 상세

| 필드 | 기본값 | 용도 |
|------|--------|------|
| `emergency_level_0_multiplier` | 1.0 | NORMAL 상태 배율 |
| `emergency_level_1_multiplier` | 0.8 | LEVEL_1 상태 배율 |
| `emergency_level_2_multiplier` | 0.5 | LEVEL_2 상태 배율 |
| `emergency_level_3_multiplier` | 0.0 | LEVEL_3 상태 배율 (Full Stop) |
| `cb_open_limit_percent` | 0.0 | CB OPEN 시 limit 비율 |
| `cb_half_open_limit_percent` | 0.5 | CB HALF_OPEN 시 limit 비율 |
| `enable_event_integration` | True | 이벤트 연동 활성화 |
| `sync_on_startup` | True | 시작 시 상태 동기화 |
| `recovery_dampening_enabled` | True | Recovery Dampening 활성화 |
| `recovery_step_1_percent` | 0.8 | 복구 1단계 비율 |
| `recovery_step_2_percent` | 0.9 | 복구 2단계 비율 |
| `recovery_step_3_percent` | 1.0 | 복구 3단계 비율 |
| `recovery_step_interval_seconds` | 30.0 | 복구 단계 간격 (초) |
| `gradient_freeze_on_level_3` | True | LEVEL_3에서 Gradient 중단 |
| `full_stop_conditions_enabled` | True | 3중 조건 Full Stop |
| `safe_open_fallback_enabled` | True | Redis 다운 시 Safe-Open |
| `static_safe_limit_percent` | 0.5 | Safe-Open 시 용량 50% |
| `redis_last_safe_limit_key_pattern` | `throttle:last_safe_limit:{service}` | Cold Start 복구 키 |
| `sync_callback_enabled` | True | CB OPEN Sync 콜백 활성화 |

### Prometheus 메트릭 상세

| 메트릭 이름 | 타입 | 레이블 |
|-----------|------|--------|
| `throttle_current_limit` | Gauge | `service` |
| `throttle_rtt_ms` | Histogram | `service` |
| `throttle_gradient` | Gauge | `service` |
| `throttle_denied_total` | Counter | `service`, `reason` |
| `throttle_emergency_adjustments_total` | Counter | `level` |
| `throttle_cb_adjustments_total` | Counter | `service`, `cb_state` |

### 감사 로그 이벤트 상세

| 이벤트 | 함수 | CascadeEvent 기록 |
|--------|------|------------------|
| `throttle_limit_adjusted` | `record_throttle_limit_adjusted()` | ❌ (메트릭만) |
| `throttle_emergency_sync` | `record_throttle_emergency_sync()` | ✅ |
| `throttle_cb_sync` | `record_throttle_cb_sync()` | ✅ |
| `throttle_sla_breach` | `record_throttle_sla_breach()` | ❌ (메트릭만) |

### Postmortem 데이터 상세

| 필드 | 타입 | 설명 |
|------|------|------|
| `throttle_limit_history` | `list[dict]` | 인시던트 기간 limit 변화 이력 |
| `throttle_min_limit` | `int` | 기간 중 최저 limit |
| `throttle_max_limit` | `int` | 기간 중 최고 limit |
| `throttle_adjustment_count` | `int` | 조정 횟수 |
| `throttle_current_limit` | `int` | 현재 limit |
| `throttle_emergency_adjustments` | `int` | Emergency 조정 횟수 |
| `throttle_cb_adjustments` | `int` | CB 조정 횟수 |
| `throttle_sla_warnings` | `int` | SLA 경고 횟수 |
| `throttle_sla_criticals` | `int` | SLA 위험 횟수 |
