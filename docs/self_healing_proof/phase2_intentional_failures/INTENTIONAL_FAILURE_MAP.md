# Phase 2: Intentional Failure Map

> **문서 목적**: Phase 2 테스트에서 Self-Healing 레이어의 **경계 분리 이후 정합성**을 검증하기 위해  
> 쇼핑 시스템을 **Phase 1보다 더 파괴적으로** 실패시키는 방법을 매핑한다.

---

## 목차

1. [Phase 2 파괴 철학](#1-phase-2-파괴-철학)
2. [Phase 1과의 차이점](#2-phase-1과의-차이점)
3. [시스템 경계 분리 이후 취약점](#3-시스템-경계-분리-이후-취약점)
4. [실패 주입 후보 지점](#4-실패-주입-후보-지점)
5. [예상 Self-Healing 신호](#5-예상-self-healing-신호)
6. [파괴 우선순위 매트릭스](#6-파괴-우선순위-매트릭스)

---

## 1. Phase 2 파괴 철학

### 1.1 핵심 가정

쇼핑 시스템은 **극도로 안정적**이다. Phase 1에서 이미 검증되었다.

따라서 Phase 2는:
- 정상적인 실패 핸들링을 테스트하지 **않는다**
- "이런 일은 절대 일어나지 않는다"는 가정을 **파괴한다**
- 시스템 분리로 인해 **새롭게 노출된** 취약점을 공격한다
- 암묵적 결합(implicit coupling)을 **강제로 깨뜨린다**

### 1.2 파괴 대상

| 대상 | Phase 1 접근 | Phase 2 접근 |
|------|-------------|-------------|
| 트랜잭션 경계 | 단일 실패 주입 | 경계 사이에서 실패 |
| 타이밍 | 지연 주입 | 순서 역전, 중첩 실패 |
| 상태 일관성 | 롤백 검증 | 고아 상태 생성 |
| 외부 의존성 | 타임아웃 | 성공 후 내부 실패 (orphan) |
| 멱등성 | TTL 내 중복 | TTL 만료 경계 공격 |
| DLQ 라우팅 | 생성 확인 | 라우팅 누락 탐지 |

### 1.3 적대적 사고

Phase 2에서 우리는:
- **악의적인 환경**으로 행동한다
- **퇴화된 의존성**을 시뮬레이션한다
- **타이밍 적대자**가 된다
- **분산 시스템 악몽**을 구현한다

---

## 2. Phase 1과의 차이점

### 2.1 Phase 1 (완료)

```
목적: 실패 핸들링이 존재하는지 확인
방법: 단일 컴포넌트에 실패 주입
결과: 롤백, DLQ 생성, CB 상태 전이 확인
```

### 2.2 Phase 2 (현재)

```
목적: 시스템 분리 후에도 실패 핸들링이 작동하는지 확인
방법: 경계 간 불일치, 순서 역전, 부분 성공 후 실패
결과: 고아 상태 감지, 교차 서비스 복구, 포렌식 컨텍스트 완전성
```

### 2.3 파괴 강도 비교

| 차원 | Phase 1 | Phase 2 |
|------|---------|---------|
| **실패 지점** | 단일 | 다중/중첩 |
| **타이밍** | 순차적 | 병렬/역전 |
| **복구 경로** | 명시적 | 암묵적/누락 가능 |
| **상태 불일치** | 일시적 | 지속적/영구적 |
| **DLQ 라우팅** | 보장됨 | 보장 안 됨 (검증 대상) |

---

## 3. 시스템 경계 분리 이후 취약점

### 3.1 selfhealing 패키지 분리

```
[분리 전]
shopping/
├── services/
│   ├── payment_service.py
│   └── self_healing/
│       ├── dlq_service.py      ← 직접 참조
│       ├── retry_handler.py    ← 직접 참조
│       └── circuit_breaker_service.py

[분리 후]
shopping/                       packages/selfhealing-python/
├── services/                   ├── src/selfhealing/
│   ├── payment_service.py      │   ├── services/
│   └── self_healing/           │   │   ├── dlq_service.py
│       ├── adapters/           │   │   ├── retry_handler.py
│       └── (re-exports)        │   │   └── circuit_breaker/
```

### 3.2 분리로 인한 잠재적 문제

| 문제 영역 | 설명 | 위험도 |
|----------|------|--------|
| **어댑터 미연결** | ProviderRegistry가 초기화되지 않으면 fallback 사용 | HIGH |
| **예외 라우팅 누락** | Chaos 예외 → DLQ 라우팅 코드 없음 | CRITICAL |
| **ForensicContext 불완전** | 필드가 기본값으로 남음 | MEDIUM |
| **CB↔DLQ 연동 단절** | force_close() 시 replay 미트리거 | HIGH |
| **Celery 태스크 DLQ 누락** | 재시도 소진 후 무음 실패 | CRITICAL |

### 3.3 공유 상태 경계

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SHOPPING SYSTEM                              │
├─────────────────────────────────────────────────────────────────────┤
│  Order      Payment     Product.stock    User.points    Cart        │
│    ↓           ↓              ↓              ↓            ↓         │
│  status     is_paid      sold_count      balance     is_active      │
└─────────────────────────────────────────────────────────────────────┘
        │           │              │              │
        ▼           ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       SELF-HEALING LAYER                             │
├─────────────────────────────────────────────────────────────────────┤
│  FailedOperation    CircuitBreakerState    ForensicContext          │
│       (DLQ)              (Redis)            (JSON blob)              │
└─────────────────────────────────────────────────────────────────────┘
```

**공격 표면**: 두 레이어 사이의 **모든 호출 지점**

---

## 4. 실패 주입 후보 지점

### BP-21: PG 성공 후 내부 실패 (Orphaned PG Transaction)

**위치**: `shopping/services/payment_service.py` → `confirm_payment_sync()` (Line 208-215)

**실패 방식**:
- Toss API 호출 **성공** 후
- 내부 처리 중 `PartialFailureException` 강제 발생
- DB 트랜잭션 롤백되지만 **PG 트랜잭션은 성공 상태**

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 외부 실패 → 내부 롤백 (일관성 유지)
- Phase 2: 외부 성공 → 내부 실패 (불일치 발생)

**경계**:
- 쇼핑 시스템: `Payment.is_paid = False`
- PG 시스템: 실제 결제 완료
- Self-Healing: 불일치 감지 필요

**예상 Self-Healing 신호**:
- DLQ 엔트리: `failure_type=PARTIAL_FAILURE_POST_PG`
- ForensicContext: `pg_response` 필드에 성공 응답 보존
- `recommended_action=MANUAL_CHECK` (환불 필요)

---

### BP-22: 롤백의 롤백 실패 (Rollback Failure)

**위치**: `shopping/tasks/payment_tasks.py` → `rollback_payment_failure()`

**실패 방식**:
- 결제 실패로 롤백 태스크 실행
- 재고 복구 중 **2차 실패** 발생
- Celery 재시도 소진

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 실패 → 롤백 성공
- Phase 2: 실패 → 롤백 실패 → 2차 DLQ

**경계**:
- 쇼핑 시스템: `Order.status = pending` (stuck)
- Self-Healing: 2차 실패 감지 필요

**예상 Self-Healing 신호**:
- DLQ 엔트리: `domain=inventory`, `failure_type=ROLLBACK_FAILURE`
- ForensicContext: `stock_before`, `stock_after` 스냅샷
- `recommended_action=ESCALATE`

---

### BP-23: Celery 태스크 무음 실패 (Silent Task Failure)

**위치**: `shopping/tasks/payment_tasks.py` → `finalize_payment_confirm()`

**실패 방식**:
- Celery 태스크 5회 재시도 소진
- `MaxRetriesExceededError` 발생
- **DLQ 호출 코드 없음** (의심)

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 동기 함수 실패 → 명시적 예외 처리
- Phase 2: 비동기 태스크 실패 → 무음 소멸 가능

**경계**:
- 쇼핑 시스템: `Payment.status = in_progress` (stuck)
- Self-Healing: **신호 없음** (문제!)

**예상 Self-Healing 신호**:
- DLQ 엔트리: `failure_type=ASYNC_TASK_FAILURE` (생성되어야 함)
- ForensicContext: `task_id`, `task_name`, `queue_name`

**검증 목적**: DLQ 라우팅 누락 탐지

---

### BP-24: 웹훅 순서 역전 (Webhook Order Inversion)

**위치**: `shopping/webhooks/toss_webhook_view.py` + `TossWebhookService`

**실패 방식**:
- `PAYMENT.CANCELED` 웹훅이 `PAYMENT.DONE`보다 **먼저** 도착
- 또는 동시 도착하여 처리 순서 역전

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 순차적 웹훅 처리
- Phase 2: 분산 시스템 현실 (순서 보장 없음)

**경계**:
- 외부 시스템: 이벤트 순서 역전 가능
- 쇼핑 시스템: 상태 머신 불변성 필요

**예상 Self-Healing 신호**:
- 로그: "Payment in final state, ignoring"
- WebhookEvent 테이블: 순서 기록

---

### BP-25: 멱등성 TTL 경계 공격 (Idempotency TTL Boundary)

**위치**: `shopping/services/payment_service.py` → `_check_idempotency_key()`

**실패 방식**:
- 첫 요청: 멱등성 키 저장 (Redis TTL 60초)
- 처리 중 **61초 지연** 주입
- 두 번째 동일 요청: TTL 만료로 멱등성 우회

**왜 Phase 1보다 파괴적인가**:
- Phase 1: TTL 내 중복 차단 확인
- Phase 2: TTL **경계**에서 공격

**경계**:
- Redis: TTL 만료
- DB: `select_for_update` 2차 방어

**예상 Self-Healing 신호**:
- 정상: DB 락으로 2차 방어 성공
- 비정상: `IdempotencyViolation` DLQ 생성

---

### BP-26: Circuit Breaker → DLQ Replay 단절

**위치**: `selfhealing/services/circuit_breaker/service.py` + `dlq_replay_tasks.py`

**실패 방식**:
1. DLQ에 `PG_TIMEOUT` 실패 축적
2. CB `force_close(trigger_replay=True)` 호출
3. Replay 태스크 **미실행** (연동 단절 의심)

**왜 Phase 1보다 파괴적인가**:
- Phase 1: CB 상태 전이 확인
- Phase 2: CB와 DLQ 간 **실제 연동** 검증

**경계**:
- Circuit Breaker: 상태 변경 완료
- DLQ: replay 미트리거 가능

**예상 Self-Healing 신호**:
- DLQ 상태: `pending → replayed` (정상)
- DLQ 상태: `pending` 유지 (연동 단절)

---

### BP-27: Race Window 극대화 (Confirm + Cancel 교차)

**위치**: `shopping/services/payment_service.py`

**실패 방식**:
- `confirm_payment_sync()` 시작
- Chaos 지연 3초 주입 (`inject_confirm_race_delay`)
- 동시에 `cancel_payment()` 호출
- 두 작업이 **동시에 부분 성공**

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 락 경쟁 → 하나만 성공
- Phase 2: 락 **사이**에서 상태 변경

**경계**:
- Payment: `is_paid=True` AND `is_canceled=True` (불가능한 상태)

**예상 Self-Healing 신호**:
- 로그: `possible_lock_contention`
- DLQ: `RACE_CONDITION_DETECTED`

---

### BP-28: ForensicContext 필드 누락

**위치**: `shopping/services/self_healing/forensic_context.py`

**실패 방식**:
- 실패 발생
- DLQ 저장 호출
- ForensicContext **필드 미채움**

**왜 Phase 1보다 파괴적인가**:
- Phase 1: DLQ 생성 확인
- Phase 2: DLQ **품질** 검증

**경계**:
- DLQ 엔트리 존재
- ForensicContext 빈 필드

**예상 Self-Healing 신호**:
- `forensic_context.retry_history = []` (비어있음)
- `forensic_context.state_before = null`

---

### BP-29: 포인트 적립 부분 실패 (Point Accumulation Orphan)

**위치**: `shopping/tasks/point_tasks.py` → `add_points_after_payment`

**실패 방식**:
- 결제 성공 (`Payment.is_paid = True`)
- 포인트 적립 태스크 **실패**
- 재시도 소진

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 원자적 트랜잭션 내 롤백
- Phase 2: 성공 후 부수 작업 실패 (롤백 불가)

**경계**:
- Payment: 성공
- Points: 미적립

**예상 Self-Healing 신호**:
- DLQ: `domain=point`, `failure_type=POINT_ACCUMULATION_FAILURE`

---

### BP-30: 캐시 무효화 후 DB 방어 검증 (Cache Invalidation)

**위치**: `TossWebhookService.is_webhook_duplicate()`

**실패 방식**:
- Redis `FLUSHDB`
- 동일 웹훅 재전송
- Redis 중복 체크 우회

**왜 Phase 1보다 파괴적인가**:
- Phase 1: 캐시 작동 확인
- Phase 2: 캐시 **실패** 시 2차 방어 검증

**경계**:
- Redis: 캐시 미스
- DB: `is_paid` 2차 방어

**예상 Self-Healing 신호**:
- 로그: "Payment already processed"
- 중복 처리 없음

---

## 5. 예상 Self-Healing 신호

### 5.1 DLQ 엔트리

| BP | domain | failure_type | 생성 보장 |
|----|--------|--------------|----------|
| BP-21 | payment | PARTIAL_FAILURE_POST_PG | ❓ 검증 필요 |
| BP-22 | inventory | ROLLBACK_FAILURE | ❓ 검증 필요 |
| BP-23 | payment | ASYNC_TASK_FAILURE | ❌ 누락 의심 |
| BP-27 | payment | RACE_CONDITION_DETECTED | ❓ 검증 필요 |
| BP-29 | point | POINT_ACCUMULATION_FAILURE | ❓ 검증 필요 |

### 5.2 ForensicContext 필수 필드

| BP | 필수 필드 | 현재 보장 |
|----|----------|----------|
| BP-21 | pg_response | ❓ |
| BP-22 | stock_before, stock_after | ❓ |
| BP-23 | task_id, task_name | ❓ |
| BP-27 | state_before, state_after | ❓ |

### 5.3 Circuit Breaker 상태

| BP | 예상 상태 전이 |
|----|--------------|
| BP-21 | failure_count 증가 |
| BP-26 | closed → open → closed (replay 트리거) |

---

## 6. 파괴 우선순위 매트릭스

### 6.1 Critical Path (즉시 실행)

| BP | 이름 | 위험도 | 이유 |
|----|------|--------|------|
| **BP-21** | Orphaned PG | CRITICAL | 실제 금전 손실 가능 |
| **BP-23** | Silent Task Failure | CRITICAL | DLQ 라우팅 누락 = 데이터 손실 |
| **BP-22** | Rollback Failure | HIGH | 재고 불일치 지속 |

### 6.2 Boundary Validation (경계 검증)

| BP | 이름 | 위험도 | 이유 |
|----|------|--------|------|
| **BP-26** | CB↔DLQ 연동 | HIGH | 핵심 자가치유 흐름 |
| **BP-28** | ForensicContext | MEDIUM | 복구 품질 저하 |

### 6.3 Edge Cases (엣지 케이스)

| BP | 이름 | 위험도 | 이유 |
|----|------|--------|------|
| **BP-27** | Race Window | MEDIUM | 타이밍 의존 |
| **BP-25** | TTL Boundary | MEDIUM | 경계 조건 |
| **BP-24** | Webhook Order | LOW | 방어 메커니즘 존재 |
| **BP-29** | Point Orphan | MEDIUM | 비금전적 손실 |
| **BP-30** | Cache Miss | LOW | 2차 방어 존재 |

---

## 7. 구현 제약 조건

### 7.1 절대 금지

- Self-Healing 로직 수정 금지
- 테스트 전용 숏컷 추가 금지
- 실패 주입 상시 활성화 금지

### 7.2 필수 요건

- 모든 실패는 **조건부**
- 모든 실패는 **재현 가능**
- 모든 실패는 **설명 가능**

### 7.3 환경 격리

```bash
# Phase 2 전용 환경 변수
CHAOS_PHASE2_MODE=off|orphan_pg|rollback_fail|silent_task|webhook_order|...
CHAOS_PHASE2_DELAY_MS=3000
CHAOS_PHASE2_PROBABILITY=1.0  # 테스트 시 100%
```

---

## Phase 2 Chaos / Load Stage Mapping

Phase 2 Breakpoints(BP-21 ~ BP-30)는 이 문서에서 **설계**되지만, 
실제 **관측 및 검증**은 Stage 6–13 시나리오를 통해 수행된다.
이 문서는 파괴 설계서이며, 실행 방법이나 운영 절차를 기술하지 않는다.

각 Stage는 특정 부하/혼돈 조건 하에서 Breakpoint 현상을 **관측하는 렌즈** 역할을 한다.

### Stage-Breakpoint 매핑 테이블

| Breakpoint | Primary Stage | Stage Type | Observation Purpose |
|------------|---------------|------------|---------------------|
| BP-21 | stage6_chaos_random | Chaos | 무작위 혼돈 주입 하 Orphaned PG 발생 관측 |
| BP-22 | stage9_soak | Soak | 장기 부하에서 롤백 실패 누적 관측 |
| BP-23 | stage6_chaos_random | Chaos | 랜덤 실패 환경에서 Silent Task 누락 감지 |
| BP-24 | stage12_spike_recovery | Spike | 급격한 부하 변동 시 Webhook 순서 역전 관측 |
| BP-25 | stage9_soak | Soak | 지속 부하에서 TTL 경계 조건 노출 |
| BP-26 | stage11_ramp_threshold | Ramp | 임계치 도달 시 CB↔DLQ 연동 단절 검증 |
| BP-27 | stage13_repeated_spike | Repeated Spike | 반복 스파이크에서 Race Window 확대 관측 |
| BP-28 | stage9_soak | Soak | 장기 운영에서 ForensicContext 품질 저하 탐지 |
| BP-29 | stage12_spike_recovery | Spike | 스파이크 복구 과정에서 Point 적립 누락 관측 |
| BP-30 | stage6_chaos_random | Chaos | 캐시 혼돈 주입 시 2차 방어 작동 확인 |

### Stage 역할 요약

| Stage | 파일 | 관측 렌즈 |
|-------|------|----------|
| stage6_chaos_random | `stage6_chaos_random.py` | 무작위 혼돈 환경에서의 예외 경로 |
| stage9_soak | `stage9_soak.py` | 장기 지속 부하에서의 누적 실패 |
| stage11_ramp_threshold | `stage11_ramp_threshold.py` | 점진적 부하 증가 시 임계점 도달 |
| stage12_spike_recovery | `stage12_spike_recovery.py` | 급격한 부하 변동과 복구 경로 |
| stage13_repeated_spike | `stage13_repeated_spike.py` | 반복 스파이크에서의 레이스 컨디션 |

---

**다음 문서**: [BREAKPOINT_DETAILS_1.md](./BREAKPOINT_DETAILS_1.md)
