# Pipeline Coverage Matrix & Gap Analysis

> **Version**: 1.0
> **Created**: 2025-12-13
> **Purpose**: 8칸 파이프라인 기준 테스트 커버리지 분석 및 Gap 식별

---

## 📊 8-Stage Pipeline Definition

```
┌────────────────────────────────────────────────────────────────────────────┐
│  1. INGRESS          │  2. VALIDATION       │  3. CONCURRENCY CONTROL     │
│  요청 진입           │  입력 검증           │  락/멱등성/순서             │
│  인증/권한           │  정책 결정           │                             │
│  Rate Limit          │                      │                             │
├──────────────────────┼──────────────────────┼─────────────────────────────┤
│  4. STATE CHANGE     │  5. ASYNC BOUNDARY   │  6. CACHING                 │
│  DB 트랜잭션         │  큐/Celery/Webhook   │  Stampede/Refresh           │
│  Commit/Rollback     │  이벤트              │  Invalidation               │
├──────────────────────┼──────────────────────┼─────────────────────────────┤
│  7. EXTERNAL DEPS    │  8. EGRESS +         │                             │
│  결제/이메일/S3      │  OBSERVABILITY       │                             │
│  외부 API            │  응답/로그/메트릭    │                             │
└──────────────────────┴──────────────────────┴─────────────────────────────┘
```

---

## 📋 Coverage Matrix

### Legend
- **C** = Control (대응책 존재)
- **S** = Signal (지표/로그/카운터 존재)
- **T** = Test (테스트/카오스 검증 존재)
- **✅** = 완전 커버
- **⚠️** = 부분 커버 (개선 필요)
- **❌** = 미커버 (Gap)

---

### 1️⃣ INGRESS (요청 진입)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| Rate Limit 초과 | ✅ 429 응답 | ✅ rate_limit_hits | ✅ Stage 22 | ✅ | - |
| 인증 실패 | ✅ 401 응답 | ✅ auth_failures | ✅ Stage 33 | ✅ | JWT cascade |
| 권한 부족 | ✅ 403 응답 | ⚠️ | ⚠️ | ⚠️ | **RBAC 부하 테스트 부족** |
| 잘못된 헤더 | ✅ 400 응답 | ✅ validation_errors | ✅ Stage 0 | ✅ | - |
| Graceful Shutdown | ✅ 503 + Drain | ✅ shutdown_requests | ✅ Stage 27 | ✅ | - |
| DDoS/Spike | ✅ Rate Limit | ✅ request_rate | ✅ Stage 12 | ✅ | - |

**Gap 발견:**
- ⚠️ **RBAC 권한 기반 부하 테스트** - 역할별 권한 검증 시나리오 부족

---

### 2️⃣ VALIDATION (입력 검증)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| 스키마 위반 | ✅ 400 응답 | ✅ validation_errors | ✅ Stage 0,6 | ✅ | - |
| 비즈니스 규칙 위반 | ✅ 422 응답 | ✅ business_rule_violations | ⚠️ | ⚠️ | **정책 변경 테스트 부족** |
| Timestamp 검증 (Clock Skew) | ✅ 허용 범위 | ✅ clock_skew_detected | ✅ Stage 23 | ✅ | - |
| JWT 만료 | ✅ 재발급 | ✅ jwt_expired | ✅ Stage 33 | ✅ | - |
| Signature 검증 | ✅ 400/401 | ⚠️ | ⚠️ Stage 8 | ⚠️ | Webhook 서명만 |

**Gap 발견:**
- ⚠️ **Config/Policy Change Safety** - 정책 변경 롤백 테스트 부족
- ⚠️ **Request Signature 검증** - 일반 요청 HMAC 검증 테스트 없음

---

### 3️⃣ CONCURRENCY CONTROL (동시성 제어)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| 중복 요청 (Idempotency) | ✅ 멱등키 | ✅ duplicates_total | ✅ Stage 2 | ✅ | - |
| 락 경합 | ✅ Timeout+Retry | ✅ lock_contention | ✅ Stage 16 | ✅ | - |
| 데드락 | ✅ 자동 복구 | ✅ deadlock_count | ✅ Stage 34 | ✅ | - |
| 레이스 컨디션 | ✅ Atomic 연산 | ✅ race_detected | ✅ Stage 7 | ✅ | - |
| 순서 보장 (Ordering) | ✅ Sequence | ⚠️ | ⚠️ Stage 4 | ⚠️ | 취소 순서만 |
| 부분 성공 후 재진입 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | **명시적 테스트 부족** |
| 분산 락 | ✅ Redis Lock | ✅ distributed_lock | ✅ Stage 35 v2 | ✅ | - |

**Gap 발견:**
- ⚠️ **Ordering Key 전략** - 순서 보장 시나리오 확장 필요
- ⚠️ **Partial Success Re-entry** - 부분 성공 후 재진입 안전성 테스트 부족

---

### 4️⃣ STATE CHANGE (상태 변경)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| 트랜잭션 실패 | ✅ Rollback | ✅ tx_rollback | ✅ Stage 5 | ✅ | - |
| Partial Commit | ✅ Saga/보상 | ✅ partial_commit | ✅ Stage 18 | ✅ | Chain failure |
| Rollback 실패 | ✅ DLQ+알림 | ✅ rollback_failed | ✅ Stage 19 | ✅ | - |
| 재고 정합성 | ✅ 재고 복구 | ✅ stock_mismatch | ✅ Stage 5,4 | ✅ | - |
| 포인트 정합성 | ✅ 포인트 복구 | ✅ point_mismatch | ✅ Stage 5 | ✅ | - |
| Connection Pool 고갈 | ✅ Watchdog | ✅ pool_exhausted | ✅ Stage 26 | ✅ | - |
| 스키마 호환성 (배포) | ✅ Converter | ✅ schema_mismatch | ✅ Stage 37 | ✅ | GAP-01 해결 |

**Gap 발견:**
- ✅ **Partial Deployment Schema Compatibility** - Stage 37에서 해결 (V1/V2 혼재 검증)

---

### 5️⃣ ASYNC BOUNDARY (비동기 경계)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| 큐 적체 (Backpressure) | ⚠️ | ✅ queue_depth | ⚠️ Stage 29 | ⚠️ | Bulk만 테스트 |
| DLQ 재처리 | ✅ Replay | ✅ dlq_size | ✅ Stage 14,29 | ✅ | - |
| Webhook 순서 역전 | ✅ Idempotent | ✅ webhook_order | ✅ Stage 20 | ✅ | - |
| Webhook 지연 | ✅ Timeout | ✅ webhook_delay | ✅ Stage 20 | ✅ | - |
| 재처리 멱등성 | ✅ Idempotent | ✅ replay_duplicates | ✅ Stage 14 | ✅ | - |
| 스케줄 드리프트 | ✅ 중복 방지 | ✅ schedule_drift | ✅ Stage 30 | ✅ | - |
| Outbox 패턴 | ✅ Outbox Table | ✅ event_published | ✅ Stage 14 Ext | ✅ | GAP-03 해결 |
| Celery Worker 크래시 | ✅ 재시작 | ✅ worker_restart | ✅ Stage 9 Ext | ✅ | GAP-05 해결 |

**Gap 발견:**
- ✅ **Outbox/Inbox 패턴 검증** - Stage 14 확장 (GAP-03 해결)
- ✅ **Backpressure 정책** - Stage 29 확장 (GAP-04 해결)
- ✅ **Worker Crash Recovery** - Stage 9 확장 (GAP-05 해결)

---

### 6️⃣ CACHING (캐싱)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| Cache Stampede | ✅ Probabilistic | ✅ stampede_count | ✅ Stage 35 | ✅ | - |
| TTL Race | ✅ Jitter | ✅ stale_reads | ✅ Stage 17 | ✅ | - |
| 캐시 무효화 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | **이벤트 기반 무효화 테스트 부족** |
| Redis 장애 Fallback | ✅ DB Fallback | ✅ cache_fallback | ⚠️ Stage 24 | ⚠️ | Partial만 |
| Hot Key | ⚠️ | ⚠️ | ⚠️ Stage 35 | ⚠️ | 간접 테스트 |
| 캐시 데이터 손상 | ✅ Checksum | ✅ poison_detected | ✅ Stage 35P | ✅ | GAP-02 해결 |

**Gap 발견:**
- ✅ **Event-based Cache Invalidation** - Stage 17 확장 (GAP-06 해결)
- ✅ **Cache Corruption/Poison** - Stage 35 Cache Poison 테스트 구현 (GAP-02 해결)
- ✅ **Cache-dead → DB Overload** - Stage 24 확장 (GAP-07 해결)

---

### 7️⃣ EXTERNAL DEPENDENCIES (외부 의존성)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| PG 타임아웃 | ✅ Timeout | ✅ pg_timeout | ✅ Stage 3 | ✅ | - |
| PG 장애 | ✅ CB | ✅ pg_failure | ✅ Stage 15 | ✅ | - |
| TLS 실패 | ✅ Alert | ✅ tls_failure | ✅ Stage 25 | ✅ | - |
| 부분 네트워크 단절 | ✅ Fallback | ✅ partition_detected | ✅ Stage 24 | ✅ | - |
| Rate Limit (외부) | ✅ Backoff | ✅ external_rate_limit | ✅ Stage 22 | ✅ | - |
| Multi-Region Failover | ✅ Failover | ✅ region_failover | ✅ Stage 28 | ✅ | - |
| 의존성 버전 불일치 | ⚠️ | ⚠️ | ❌ | ❌ | **미테스트** |

**Gap 발견:**
- ❌ **Dependency Version Mismatch** - 외부 API 버전 변경 시 호환성 미테스트

---

### 8️⃣ EGRESS + OBSERVABILITY (송신 + 관찰가능성)

| Failure Type | Control | Signal | Test | Status | Notes |
|--------------|---------|--------|------|--------|-------|
| 응답 타임아웃 | ✅ Timeout | ✅ response_timeout | ✅ Stage 3 | ✅ | - |
| 로그 누락 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | **Structured Log 검증 부족** |
| 메트릭 수집 실패 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | **Prometheus 장애 시나리오 없음** |
| 메모리 압박 | ✅ Throttle | ✅ memory_usage | ✅ Stage 36 | ✅ | - |
| OOM | ✅ 예방 | ✅ oom_count | ✅ Stage 36 | ✅ | - |
| Observability Contract | ✅ trace_id | ✅ pipeline_stage | ✅ All Stages | ✅ | GAP-08 해결 |

**Gap 발견:**
- ✅ **Observability Contract** - 전체 스테이지에 적용 (GAP-08 해결)
- ⚠️ **Structured Logging Validation** - 로그 형식/필드 일관성 테스트 부족

---

## ❌ Gap Summary (빠진 부분)

### Critical Gaps (반드시 추가)

| ID | Pipeline | Gap | Priority | Status | 추천 Stage |
|----|----------|-----|----------|--------|------------|
| **GAP-01** | State Change | Partial Deployment Schema Compatibility | P0 | ✅ 해결 | stage37_schema_compat |
| **GAP-02** | Caching | Cache Corruption Detection | P0 | ✅ 해결 | stage35_cache_poison |
| **GAP-03** | Async Boundary | Outbox Pattern Verification | P0 | ✅ 해결 | Stage 14 확장 |

### High Gaps (해결 완료)

| ID | Pipeline | Gap | Priority | Status | 추천 Action |
|----|----------|-----|----------|--------|-------------|
| **GAP-04** | Async | Backpressure Policy | P1 | ✅ 해결 | Stage 29 확장 |
| **GAP-05** | Async | Worker Crash Recovery | P1 | ✅ 해결 | Stage 9 확장 |
| **GAP-06** | Caching | Event-based Invalidation | P1 | ✅ 해결 | Stage 17 확장 |
| **GAP-07** | Caching | Cache-dead DB Overload | P1 | ✅ 해결 | Stage 24 확장 |
| **GAP-08** | Egress | Observability Contract | P1 | ✅ 해결 | 전체 스테이지에 적용 |

### Medium Gaps (권장 추가)

| ID | Pipeline | Gap | Priority | Status | 추천 Action |
|----|----------|-----|----------|--------|-------------|
| **GAP-09** | Ingress | RBAC Load Test | P2 | ⏳ 예정 | Stage 10 확장 |
| **GAP-10** | Validation | Config Change Rollback | P2 | ⏳ 예정 | 신규 or Stage 10 확장 |
| **GAP-11** | Concurrency | Partial Success Re-entry | P2 | ⏳ 예정 | Stage 19 확장 |
| **GAP-12** | External Deps | Dependency Version Mismatch | P2 | ⏳ 예정 | stage25 확장 |

### Low Gaps (선택적)

| ID | Pipeline | Gap | Priority | Notes |
|----|----------|-----|----------|-------|
| **GAP-13** | Validation | Request HMAC Signature | P3 | API Gateway에서 처리 가능 |
| **GAP-14** | Concurrency | Ordering Key Strategy | P3 | Stage 4 확장으로 충분 |
| **GAP-15** | Egress | Prometheus Failure | P3 | Infra 레벨 모니터링으로 대체 가능 |

---

## 📊 Pipeline Coverage Summary

```
┌────────────────────┬──────────────────────────────────────────────┐
│ Pipeline Stage     │ Coverage                                     │
├────────────────────┼──────────────────────────────────────────────┤
│ 1. Ingress         │ ████████████████████████░░ 92%               │
│ 2. Validation      │ ██████████████████████░░░░ 85%               │
│ 3. Concurrency     │ █████████████████████████░ 95%               │
│ 4. State Change    │ █████████████████████████░ 98% ✅ GAP-01     │
│ 5. Async Boundary  │ █████████████████████████░ 95% ✅ GAP-03~05  │
│ 6. Caching         │ ████████████████████████░░ 95% ✅ GAP-02,06,07│
│ 7. External Deps   │ ████████████████████████░░ 92%               │
│ 8. Egress+Obs      │ █████████████████████████░ 98% ✅ GAP-08     │
└────────────────────┴──────────────────────────────────────────────┘
```

**Overall Coverage: ~94% (Week 3 Validation Passed)**

---

## 🎯 Gap 해결 Action Plan

### Phase 1: Critical (1주 내)

```yaml
GAP-01: Schema Compatibility Test
  action: 신규 stage37_schema_compat.py 생성
  scenario: |
    1. V1 스키마로 Worker 50% 실행
    2. V2 스키마로 Worker 50% 실행
    3. 동시 트랜잭션 발생
    4. 데이터 일관성 검증
  invariants:
    - "data_corruption == 0"
    - "schema_mismatch_errors == 0"

GAP-02: Cache Poison Detection
  action: stage35_variants/cache_poison.py 생성
  scenario: |
    1. 캐시에 의도적으로 잘못된 데이터 주입
    2. 읽기 시 감지 여부 확인
    3. 자동 무효화 동작 확인
  invariants:
    - "poison_detected == poison_injected"
    - "poison_served == 0"
```

### Phase 2: Medium (2주 내)

```yaml
GAP-03: Outbox Pattern Verification
  action: Stage 14 확장 또는 신규 stage14_outbox.py
  scenario: |
    1. DB commit 성공 + 이벤트 발행 실패 시뮬레이션
    2. Outbox 폴러가 재발행하는지 확인
    3. 중복 발행 방지 확인
  invariants:
    - "event_loss == 0"
    - "duplicate_events == 0"

GAP-08: Backpressure Policy
  action: Stage 29 확장
  scenario: |
    1. 큐 용량 80% 도달 시 신규 요청 거부
    2. 503 + Retry-After 헤더 반환
    3. 큐 여유 생기면 자동 수락 재개
  invariants:
    - "queue_overflow == 0"
    - "graceful_reject_rate > 0"
```

### Phase 3: Enhancement (4주 내)

```yaml
GAP-12: Observability Contract
  action: 모든 스테이지에 공통 검증 추가
  implementation: |
    각 요청에 trace_id 포함
    실패 시 "어느 파이프라인 단계"인지 로그에 명시
    종료 리포트에 "단계별 실패 분포" 포함
  invariants:
    - "untraced_failures == 0"
    - "unknown_stage_failures == 0"
```

---

## ✅ 업계 "알려진 이슈" 체크리스트

### 반드시 테스트해야 하는 7가지

| # | Issue | 현재 상태 | Gap ID |
|---|-------|----------|--------|
| 1 | Time Budget (단계별 타임아웃 합계 ≤ 전체 SLA) | ⚠️ 암묵적 | - |
| 2 | Config/Policy Change Rollback | ⚠️ 부족 | GAP-06 |
| 3 | Cache Invalidation (Event-based) | ⚠️ 부족 | GAP-10 |
| 4 | Async Boundary Guarantee (Outbox) | ⚠️ 부족 | GAP-03 |
| 5 | Backpressure (우아한 거부) | ⚠️ 부족 | GAP-08 |
| 6 | Partial Deployment Safety | ❌ 없음 | GAP-01 |
| 7 | Observability Contract | ⚠️ 부족 | GAP-12 |

---

## 📝 결론

현재 테스트 스위트는 **36개 스테이지로 86% 커버리지**를 달성하고 있습니다.

**Critical Gap 4개:**
1. Schema Compatibility (배포 안전성)
2. Cache Poison Detection (캐시 무결성)
3. Outbox Pattern (이벤트 보장)
4. Dependency Version Mismatch (외부 API 호환성)

**추천 우선순위:**
1. GAP-01, GAP-03 즉시 해결 (프로덕션 안전성)
2. GAP-02, GAP-12 1주 내 해결 (운영 가시성)
3. 나머지는 기존 스테이지 확장으로 점진 개선
