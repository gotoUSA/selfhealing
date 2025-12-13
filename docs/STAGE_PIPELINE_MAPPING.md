# Stage-by-Stage Pipeline Mapping

> **Version**: 1.0
> **Created**: 2025-12-13
> **Purpose**: 각 스테이지별 파이프라인 칸 매핑 및 검증 체크리스트

---

## 📊 Stage → Pipeline 매핑 요약

| Stage | I | V | CC | SC | AB | C | ED | EO | Primary Focus |
|-------|---|---|----|----|----|----|----|----|---------------|
| 0 Smoke | ✅ | ✅ | - | ✅ | - | - | - | ✅ | 환경 검증 |
| 1 Happy | ✅ | ✅ | - | ✅ | - | - | - | ✅ | 성능 기준선 |
| 2 Idempotent | - | ✅ | ✅ | ✅ | - | - | - | - | 멱등성 |
| 3 Latency | - | - | - | - | ✅ | - | ✅ | - | 타임아웃 |
| 4 Cancel | - | - | ✅ | ✅ | - | - | - | - | 취소 레이스 |
| 5 Rollback | - | - | ✅ | ✅ | - | - | - | - | 롤백 검증 |
| 6 Chaos | ✅ | ✅ | - | - | - | - | ✅ | ✅ | 랜덤 실패 |
| 7 Race | - | - | ✅ | ✅ | - | - | - | - | 동시성 레이스 |
| 8 Webhook | - | ✅ | - | - | ✅ | - | ✅ | - | 웹훅 신뢰성 |
| 9 Soak | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 장기 안정성 |
| 10 Self-Healing | ✅ | ✅ | - | - | - | - | - | ✅ | Control API |
| 11 Ramp | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 임계점 발견 |
| 12 Spike | - | - | ✅ | - | - | - | ✅ | ✅ | 스파이크 복구 |
| 13 Repeated | - | - | ✅ | - | - | - | ✅ | - | Backoff 축적 |
| 14 DLQ | - | - | - | ✅ | ✅ | - | - | - | DLQ 재처리 |
| 15 CB | - | - | ✅ | - | - | - | ✅ | - | CB 상태 전이 |
| 16 DB Lock | - | - | ✅ | ✅ | - | - | - | - | 데드락 복구 |
| 17 Cache TTL | - | - | - | ✅ | - | ✅ | - | - | 캐시 레이스 |
| 18 Chain | - | ✅ | - | ✅ | ✅ | - | ✅ | - | 체인 장애 |
| 19 Rollback Fail | - | - | - | ✅ | ✅ | - | - | - | 이중 실패 |
| 20 Webhook Delay | - | ✅ | - | ✅ | ✅ | - | - | - | 지연 웹훅 |
| 21 False Positive | - | - | - | - | - | - | ✅ | ✅ | 오탐 방지 |
| 22 Rate Limit | - | - | ✅ | - | - | - | ✅ | - | Self-DDoS |
| 23 Clock Skew | - | ✅ | ✅ | - | - | - | - | - | 시간 불일치 |
| 24 Partition | - | - | - | - | - | ✅ | ✅ | - | 부분 단절 |
| 25 TLS | - | - | - | - | - | - | ✅ | - | 인증서 실패 |
| 26 Pool | - | - | ✅ | ✅ | - | - | - | - | 풀 고갈 |
| 27 Shutdown | ✅ | - | - | - | - | - | - | ✅ | 정상 종료 |
| 28 Multi-Region | - | - | ✅ | - | - | - | ✅ | - | 멀티리전 |
| 29 Bulk DLQ | - | - | - | ✅ | ✅ | - | - | - | 대량 재처리 |
| 30 Schedule | - | - | ✅ | - | ✅ | - | - | - | 스케줄 드리프트 |
| 31 Cascade | - | - | ✅ | ✅ | - | ✅ | ✅ | - | 연쇄 장애 |
| 32 Retry Storm | - | ✅ | ✅ | - | ✅ | - | - | - | 재시도 폭풍 |
| 33 JWT | - | ✅ | - | - | ✅ | - | ✅ | - | JWT Stampede |
| 34 Deadlock | - | - | ✅ | ✅ | - | - | - | - | 대규모 데드락 |
| 35 Stampede | - | - | - | - | - | ✅ | - | - | 캐시 스탬피드 |
| 36 Memory | ✅ | - | - | - | - | - | - | ✅ | 메모리 압박 |

**Legend:**
- I = Ingress
- V = Validation
- CC = Concurrency Control
- SC = State Change
- AB = Async Boundary
- C = Caching
- ED = External Dependencies
- EO = Egress + Observability

---

## 📋 파이프라인 칸별 체크리스트

### 1️⃣ INGRESS 체크리스트

```yaml
# 각 항목: [ ] Control | [ ] Signal | [ ] Test
ingress_checklist:
  rate_limiting:
    - [x] Control: 429 응답 + Retry-After
    - [x] Signal: rate_limit_hits counter
    - [x] Test: Stage 22
    
  authentication:
    - [x] Control: JWT 검증 + 재발급
    - [x] Signal: auth_failures counter
    - [x] Test: Stage 33
    
  authorization:
    - [x] Control: RBAC 403 응답
    - [ ] Signal: authz_denied counter  # GAP
    - [ ] Test: 역할별 부하 테스트     # GAP
    
  request_validation:
    - [x] Control: 400 응답
    - [x] Signal: validation_errors
    - [x] Test: Stage 0, 6
    
  graceful_shutdown:
    - [x] Control: 503 + Connection Drain
    - [x] Signal: shutdown_in_progress
    - [x] Test: Stage 27
    
  spike_protection:
    - [x] Control: Rate Limit + Queue
    - [x] Signal: request_rate, queue_depth
    - [x] Test: Stage 12
```

### 2️⃣ VALIDATION 체크리스트

```yaml
validation_checklist:
  schema_validation:
    - [x] Control: 400/422 응답
    - [x] Signal: schema_errors
    - [x] Test: Stage 0
    
  business_rules:
    - [x] Control: 422 응답
    - [x] Signal: rule_violations
    - [ ] Test: 정책 변경 시나리오  # GAP
    
  timestamp_validation:
    - [x] Control: 허용 범위 ±30초
    - [x] Signal: clock_skew_detected
    - [x] Test: Stage 23
    
  jwt_validation:
    - [x] Control: 401 + 재발급
    - [x] Signal: jwt_expired, jwt_invalid
    - [x] Test: Stage 33
    
  signature_validation:
    - [x] Control: 401 응답
    - [x] Signal: signature_invalid
    - [x] Test: Stage 8 (Webhook)
    
  config_change_safety:
    - [ ] Control: 버전 관리 + 롤백  # GAP
    - [ ] Signal: config_version      # GAP
    - [ ] Test: 정책 롤백 시나리오   # GAP
```

### 3️⃣ CONCURRENCY CONTROL 체크리스트

```yaml
concurrency_checklist:
  idempotency:
    - [x] Control: 멱등키 기반 중복 방지
    - [x] Signal: duplicates_total
    - [x] Test: Stage 2
    
  distributed_locking:
    - [x] Control: Redis Lock + TTL
    - [x] Signal: lock_acquired, lock_timeout
    - [x] Test: Stage 35 v2
    
  deadlock_prevention:
    - [x] Control: Lock ordering + Timeout
    - [x] Signal: deadlock_detected
    - [x] Test: Stage 16, 34
    
  race_condition:
    - [x] Control: Atomic 연산 + Optimistic Lock
    - [x] Signal: race_detected
    - [x] Test: Stage 7
    
  ordering_guarantee:
    - [x] Control: Sequence number
    - [ ] Signal: out_of_order_count  # 부분
    - [x] Test: Stage 4 (Cancel)
    
  partial_success_reentry:
    - [ ] Control: Checkpoint + Resume  # GAP
    - [ ] Signal: partial_success_count # GAP
    - [ ] Test: 재진입 안전성         # GAP
```

### 4️⃣ STATE CHANGE 체크리스트

```yaml
state_change_checklist:
  transaction_atomicity:
    - [x] Control: DB Transaction
    - [x] Signal: tx_commit, tx_rollback
    - [x] Test: Stage 5
    
  partial_commit_recovery:
    - [x] Control: Saga 패턴 / 보상 트랜잭션
    - [x] Signal: saga_step_failed
    - [x] Test: Stage 18
    
  rollback_failure_handling:
    - [x] Control: DLQ + 알림
    - [x] Signal: rollback_failed
    - [x] Test: Stage 19
    
  stock_consistency:
    - [x] Control: Atomic 차감 + 복구
    - [x] Signal: stock_mismatch
    - [x] Test: Stage 5, 4
    
  point_consistency:
    - [x] Control: Atomic 차감 + 복구
    - [x] Signal: point_mismatch
    - [x] Test: Stage 5
    
  connection_pool:
    - [x] Control: Pool Watchdog
    - [x] Signal: pool_exhausted, pool_available
    - [x] Test: Stage 26
    
  schema_compatibility:
    - [ ] Control: Migration 버전 관리  # GAP
    - [ ] Signal: schema_mismatch       # GAP
    - [ ] Test: 부분 배포 시나리오     # GAP (Critical!)
```

### 5️⃣ ASYNC BOUNDARY 체크리스트

```yaml
async_boundary_checklist:
  queue_backpressure:
    - [ ] Control: 큐 용량 제한 + 거부  # GAP
    - [x] Signal: queue_depth
    - [ ] Test: 과부하 거부 시나리오   # GAP
    
  dlq_processing:
    - [x] Control: DLQ + Replay
    - [x] Signal: dlq_size, replay_count
    - [x] Test: Stage 14, 29
    
  webhook_reliability:
    - [x] Control: Idempotent + Timeout
    - [x] Signal: webhook_received, webhook_delay
    - [x] Test: Stage 8, 20
    
  replay_idempotency:
    - [x] Control: 멱등키 기반
    - [x] Signal: replay_duplicates
    - [x] Test: Stage 14
    
  schedule_drift:
    - [x] Control: 중복 실행 방지 Lock
    - [x] Signal: schedule_drift_detected
    - [x] Test: Stage 30
    
  outbox_pattern:
    - [ ] Control: Outbox 테이블 + Poller  # GAP
    - [ ] Signal: outbox_pending           # GAP
    - [ ] Test: 이벤트 손실 방지          # GAP (Critical!)
    
  worker_crash_recovery:
    - [x] Control: 자동 재시작 + Ack
    - [x] Signal: worker_restart_count
    - [ ] Test: 강제 종료 복구 시나리오  # GAP
```

### 6️⃣ CACHING 체크리스트

```yaml
caching_checklist:
  stampede_prevention:
    - [x] Control: Probabilistic Early Refresh
    - [x] Signal: stampede_count
    - [x] Test: Stage 35
    
  ttl_race:
    - [x] Control: TTL Jitter
    - [x] Signal: stale_reads
    - [x] Test: Stage 17
    
  cache_invalidation_event:
    - [ ] Control: Event-based Invalidation  # GAP
    - [ ] Signal: invalidation_events        # GAP
    - [ ] Test: 이벤트 기반 무효화         # GAP
    
  cache_fallback:
    - [x] Control: DB Fallback
    - [x] Signal: cache_fallback_count
    - [x] Test: Stage 24 (부분)
    
  hot_key:
    - [x] Control: Local Cache Layer
    - [x] Signal: hot_key_detected
    - [x] Test: Stage 35 (간접)
    
  cache_corruption:
    - [ ] Control: Checksum + 자동 무효화  # GAP
    - [ ] Signal: cache_corruption_detected # GAP
    - [ ] Test: 캐시 오염 시나리오        # GAP (Critical!)
    
  cache_dead_db_overload:
    - [ ] Control: Rate Limit to DB        # GAP
    - [ ] Signal: db_fallback_rate         # GAP
    - [ ] Test: 캐시 완전 장애 시 DB 보호 # GAP
```

### 7️⃣ EXTERNAL DEPENDENCIES 체크리스트

```yaml
external_deps_checklist:
  timeout_handling:
    - [x] Control: Timeout + Retry
    - [x] Signal: external_timeout
    - [x] Test: Stage 3
    
  circuit_breaker:
    - [x] Control: CB Open/Close
    - [x] Signal: cb_state, cb_trips
    - [x] Test: Stage 15
    
  tls_failure:
    - [x] Control: Alert + Fallback
    - [x] Signal: tls_failure
    - [x] Test: Stage 25
    
  partial_partition:
    - [x] Control: Fallback 경로
    - [x] Signal: partition_detected
    - [x] Test: Stage 24
    
  external_rate_limit:
    - [x] Control: Backoff + Queue
    - [x] Signal: external_rate_limit
    - [x] Test: Stage 22
    
  multi_region_failover:
    - [x] Control: 자동 Failover
    - [x] Signal: region_failover
    - [x] Test: Stage 28
    
  dependency_version_mismatch:
    - [ ] Control: 버전 협상        # GAP
    - [ ] Signal: version_mismatch  # GAP
    - [ ] Test: API 버전 호환성    # GAP
```

### 8️⃣ EGRESS + OBSERVABILITY 체크리스트

```yaml
egress_observability_checklist:
  response_timeout:
    - [x] Control: Response Timeout
    - [x] Signal: response_timeout
    - [x] Test: Stage 3
    
  structured_logging:
    - [x] Control: JSON 로그 형식
    - [ ] Signal: log_parse_errors      # GAP
    - [ ] Test: 로그 형식 검증         # GAP
    
  metrics_collection:
    - [x] Control: Prometheus
    - [ ] Signal: scrape_failures       # GAP
    - [ ] Test: Prometheus 장애 시     # GAP
    
  memory_pressure:
    - [x] Control: Memory Throttle
    - [x] Signal: memory_usage, oom_count
    - [x] Test: Stage 36
    
  oom_prevention:
    - [x] Control: Memory Limit + Throttle
    - [x] Signal: oom_kill_count
    - [x] Test: Stage 36
    
  observability_contract:
    - [ ] Control: Request Tracing        # GAP
    - [ ] Signal: pipeline_stage_failures # GAP
    - [ ] Test: 단계별 실패 추적        # GAP
    
  audit_logging:
    - [x] Control: Audit Log
    - [x] Signal: audit_events
    - [x] Test: Stage 10 (간접)
```

---

## 🎯 Gap 통합 목록

### Critical (P0) - 반드시 추가

| ID | Pipeline | Item | Action |
|----|----------|------|--------|
| GAP-01 | State Change | Schema Compatibility | 신규 stage37 |
| GAP-02 | Caching | Cache Corruption | stage35 variant |
| GAP-03 | Async Boundary | Outbox Pattern | stage14 확장 |

### High (P1) - 권장 추가

| ID | Pipeline | Item | Action |
|----|----------|------|--------|
| GAP-04 | Async Boundary | Backpressure | stage29 확장 |
| GAP-05 | Async Boundary | Worker Crash | stage9 확장 |
| GAP-06 | Caching | Event Invalidation | stage17 확장 |
| GAP-07 | Caching | Cache Dead → DB | stage24 확장 |
| GAP-08 | Egress | Observability Contract | 전체 적용 |

### Medium (P2) - 선택적 추가

| ID | Pipeline | Item | Action |
|----|----------|------|--------|
| GAP-09 | Ingress | RBAC Load Test | stage10 확장 |
| GAP-10 | Validation | Config Rollback | 신규 or stage10 |
| GAP-11 | Concurrency | Partial Re-entry | stage19 확장 |
| GAP-12 | External | Version Mismatch | stage25 확장 |

---

## 📝 다음 단계

1. **즉시**: GAP-01, GAP-02, GAP-03 해결 계획 수립
2. **1주 내**: Critical Gap에 대한 테스트 시나리오 작성
3. **2주 내**: 기존 스테이지 확장으로 P1 Gap 해결
4. **월간**: Coverage Matrix 재검토 및 업데이트
