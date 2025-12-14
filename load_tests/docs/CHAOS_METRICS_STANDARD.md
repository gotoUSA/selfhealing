# Chaos Engineering Metrics Standard

> **Version**: 1.0
> **Created**: 2025-12-13
> **Purpose**: 카오스 테스트 공통 계측 기준 및 Stage 종료 템플릿

---

## 📊 공통 계측 메트릭 (10개 Core + 2개 Optional)

### Core Metrics (필수 10개)

| Category | Metric | Description | Target |
|----------|--------|-------------|--------|
| **Traffic** | `total_requests` | 전체 요청 수 | - |
| **Traffic** | `success_2xx` | 성공 응답 수 | SLA 기준 |
| **Expected Fail** | `expected_fail_4xx` | 예상된 클라이언트 에러 (400, 409, 429) | 시나리오별 |
| **Expected Fail** | `expected_fail_5xx` | 예상된 서버 에러 (503 CB-open) | 시나리오별 |
| **Unexpected** | `unexpected_errors_total` | 예상치 못한 에러 | **0 목표** |
| **Latency** | `p95_latency_ms` | 95th 백분위 레이턴시 (2xx only) | < 500ms |
| **Latency** | `p99_latency_ms` | 99th 백분위 레이턴시 (2xx only) | < 1000ms |
| **Retry** | `retries_total` / `retry_success` | 재시도 횟수 및 성공률 | success > 80% |
| **Integrity** | `db_queries_total` / `duplicates_total` | DB 쿼리 및 중복 발생 | duplicates = 0 |
| **Cache** | `cache_hits` / `cache_misses` | 캐시 히트율 | hits > 80% |
| **System** | `oom_kill_count` | OOM Kill 발생 횟수 | **0** |
| **System** | `worker_restart_count` | Worker 비정상 재시작 | **0** |

### Optional Metrics (선택 2개)

| Metric | Description | When to Use |
|--------|-------------|-------------|
| `circuit_breaker_trips` | CB 트립 횟수 | Self-Healing 스테이지 |
| `recovery_time_seconds` | 장애→정상 복구 시간 | Resilience 스테이지 |

---

## 📋 Stage 종료 보고서 템플릿

```yaml
# ============================================
# Stage Closure Report Template
# ============================================

stage_id: "stage_XX_name"
execution_date: "YYYY-MM-DD HH:MM"
executor: "CI / Manual"

# 1. Tested Scope (테스트 범위)
tested_scope:
  environment: "docker-compose / k8s / local"
  version: "commit SHA or tag"
  limits:
    users: 100
    spawn_rate: 20
    duration: "5m"
  workload: "payment_flow / cache_stampede / etc"

# 2. Invariants (불변 조건 - 이 Stage가 보장해야 하는 것)
invariants:
  - name: "No Duplicate Payments"
    assertion: "duplicates_total == 0"
    result: PASS | FAIL
  - name: "No Unexpected Errors"
    assertion: "unexpected_errors_total == 0"
    result: PASS | FAIL
  - name: "Latency SLA"
    assertion: "p99 < 500ms under 100 users"
    result: PASS | FAIL
  - name: "Recovery Time"
    assertion: "recovery_time < 30s after CB trip"
    result: PASS | FAIL

# 3. Failure Classification (실패 분류)
expected_failures:
  allowed_types:
    - "429 rate-limit"
    - "503 CB-open"
    - "timeout after 5s"
  threshold: "< 10% of total"
  actual: "X.X%"
  status: WITHIN_THRESHOLD | EXCEEDED

unexpected_failures:
  target: "0%"
  actual: "X.X%"
  details:
    - error_type: "500 Internal Server Error"
      count: 5
      sample_trace: "..."
      root_cause: "..."
      action_taken: "..."

# 4. Closure Criteria (종료 기준)
closure_criteria:
  A_cause_evidence:
    description: "로그/카운터가 예상 원인과 일치"
    evidence_source: "prometheus / app-log / trace"
    status: VERIFIED | NOT_VERIFIED
  
  B_observability_consistency:
    description: "메트릭 출처 명시 및 정합성"
    sources:
      - "prometheus:payment_success_total"
      - "app-log:order_completed"
      - "trace:span.duration"
    status: CONSISTENT | INCONSISTENT
  
  C_defense_mechanism:
    description: "대응 로직 생존 확인 (강제 트리거)"
    tested_mechanisms:
      - "Circuit Breaker open/close"
      - "Retry backoff"
      - "DLQ capture"
    status: SURVIVED | FAILED

# 5. Final Verdict (최종 판정)
verdict: PASS | PASS_WITH_KNOWN_ISSUES | FAIL

known_issues:
  - issue: "..."
    severity: LOW | MEDIUM | HIGH
    ticket: "JIRA-XXX"

notes: |
  추가 메모...

# 6. Metrics Summary (메트릭 요약)
metrics_summary:
  total_requests: 10000
  success_2xx: 9500
  expected_fail_4xx: 300
  expected_fail_5xx: 150
  unexpected_errors: 0
  p95_latency_ms: 245
  p99_latency_ms: 412
  retries_total: 200
  retry_success: 180
  duplicates_total: 0
  cache_hit_rate: 0.85
  oom_kill_count: 0
  worker_restart_count: 0
```

---

## 📊 Metrics Collection Points

### 1. Application Level

```python
# Django/FastAPI metrics
from prometheus_client import Counter, Histogram, Gauge

# Traffic
total_requests = Counter('total_requests', 'Total HTTP requests', ['method', 'endpoint'])
success_2xx = Counter('success_2xx', 'Successful responses')
expected_fail_4xx = Counter('expected_fail_4xx', 'Expected 4xx errors', ['status_code'])
expected_fail_5xx = Counter('expected_fail_5xx', 'Expected 5xx errors', ['status_code'])
unexpected_errors = Counter('unexpected_errors', 'Unexpected errors', ['error_type'])

# Latency (2xx only)
latency_histogram = Histogram(
    'request_latency_seconds',
    'Request latency',
    buckets=[.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10]
)

# Retry
retries_total = Counter('retries_total', 'Total retry attempts')
retry_success = Counter('retry_success', 'Successful retries')

# Integrity
db_queries_total = Counter('db_queries_total', 'Total DB queries')
duplicates_total = Counter('duplicates_total', 'Duplicate operations detected')

# Cache
cache_hits = Counter('cache_hits', 'Cache hits')
cache_misses = Counter('cache_misses', 'Cache misses')
```

### 2. System Level

```yaml
# Prometheus scrape targets
- job_name: 'cadvisor'
  static_configs:
    - targets: ['cadvisor:8080']
  metrics:
    - container_oom_events_total
    - container_memory_usage_bytes
    - container_cpu_usage_seconds_total

- job_name: 'celery'
  static_configs:
    - targets: ['celery-exporter:9808']
  metrics:
    - celery_worker_up
    - celery_task_failed_total
```

---

## 🎯 Stage별 Required Invariants

### Foundation (Stage 0-9)

| Stage | Core Invariants |
|-------|-----------------|
| 0 Smoke | `success_rate > 99%`, `latency < 1s` |
| 1 Happy | `error_rate < 1%`, `p99 < SLA` |
| 2 Idempotent | `duplicates_total == 0` |
| 3 Latency | `timeout_handling == true` |
| 4 Cancel | `stock_restored == true` |
| 5 Rollback | `before_state == after_state on failure` |
| 6 Chaos | `recovery_after_random_failure` |
| 7 Race | `only_one_payment_succeeds` |
| 8 Webhook | `idempotent_webhook_handling` |
| 9 Soak | `no_memory_leak`, `no_connection_leak` |

### Self-Healing (Stage 10-22)

| Stage | Core Invariants |
|-------|-----------------|
| 10 Control API | `RBAC_enforced`, `actions_logged` |
| 11 Ramp | `threshold_discovered`, `graceful_degradation` |
| 12 Spike | `recovery_time < 60s`, `data_consistency` |
| 13 Repeated | `no_backoff_accumulation` |
| 14 DLQ | `replay_accuracy == 100%`, `no_duplicates` |
| 15 CB | `state_transitions_correct` |
| 16 DB Lock | `deadlock_auto_recovery`, `no_stuck_orders` |
| 17 Cache TTL | `no_stale_reads_after_ttl` |
| 18 Chain | `rollback_on_any_failure`, `no_orphans` |
| 19 Rollback Fail | `secondary_recovery_exists` |
| 20 Webhook Delay | `idempotent_on_delay`, `no_resurrection` |
| 21 False Positive | `no_cb_on_slow_only` |
| 22 Rate Limit | `no_self_ddos`, `backoff_on_429` |

### Advanced (Stage 23-36)

| Stage | Core Invariants |
|-------|-----------------|
| 23 Clock | `tolerance_30s_skew` |
| 24 Partition | `fallback_on_partial_failure` |
| 25 TLS | `alert_on_cert_failure` |
| 26 Pool | `watchdog_recovery < 10s` |
| 27 Shutdown | `drain_complete > 99%` |
| 28 Multi-Region | `failover < 5s` |
| 29 Bulk DLQ | `throughput > 1000/s`, `memory < 1GB` |
| 30 Schedule | `no_duplicate_execution` |
| 31 Cascade | `isolation < 30s` |
| 32 Retry Storm | `memory_peak < 500MB` |
| 33 JWT | `refresh_throttle_100/s` |
| 34 Deadlock | `detection < 3s`, `retry_success > 95%` |
| 35 Stampede | `db_query_per_key == 1` |
| 36 Memory | `oom_count == 0`, `gc_pause < 100ms` |

---

## 📈 Grafana Dashboard Template

```json
{
  "dashboard": {
    "title": "Chaos Test Metrics",
    "panels": [
      {
        "title": "Request Overview",
        "type": "stat",
        "targets": [
          {"expr": "sum(total_requests)"},
          {"expr": "sum(success_2xx)"},
          {"expr": "sum(unexpected_errors)"}
        ]
      },
      {
        "title": "Latency Distribution",
        "type": "heatmap",
        "targets": [
          {"expr": "histogram_quantile(0.95, request_latency_seconds_bucket)"},
          {"expr": "histogram_quantile(0.99, request_latency_seconds_bucket)"}
        ]
      },
      {
        "title": "Failure Classification",
        "type": "piechart",
        "targets": [
          {"expr": "sum(expected_fail_4xx) by (status_code)"},
          {"expr": "sum(expected_fail_5xx) by (status_code)"},
          {"expr": "sum(unexpected_errors)"}
        ]
      },
      {
        "title": "System Health",
        "type": "timeseries",
        "targets": [
          {"expr": "container_oom_events_total"},
          {"expr": "celery_worker_up"}
        ]
      }
    ]
  }
}
```

---

## ✅ Checklist: Stage 실행 전/후

### 실행 전
- [ ] 환경 버전 기록 (commit SHA)
- [ ] 리소스 제한 설정 확인
- [ ] Prometheus/Grafana 연결 확인
- [ ] 이전 테스트 데이터 정리

### 실행 후
- [ ] Invariants 전체 검증
- [ ] Expected vs Unexpected 분류 완료
- [ ] Closure Criteria A/B/C 확인
- [ ] 종료 보고서 작성 및 저장
- [ ] 실패 시 티켓 생성
