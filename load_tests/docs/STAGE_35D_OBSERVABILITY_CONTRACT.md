# Stage 35D: Cache Stampede Observability Contract

> 작성일: 2025-12-13
> 목적: Stampede 이벤트의 **즉각적 원인 파악**을 위한 관측 가능성 계약

---

## 🎯 목표

**"문제가 발생해도 원인 파악에 시간을 쓰지 않는다"**

Stampede 관련 이슈 발생 시 운영자가 **30초 이내** 원인 위치를 특정할 수 있어야 함.

---

## 📊 필수 메트릭 (Prometheus)

### 1. 핵심 카운터 (Counter)

```yaml
# Cache 접근
cache_stampede_total_requests:
  type: counter
  labels: [cache_key, service, instance]
  description: "총 캐시 조회 요청 수"

cache_stampede_hits:
  type: counter
  labels: [cache_key, service, instance]
  description: "캐시 히트 수"

cache_stampede_misses:
  type: counter
  labels: [cache_key, service, instance]
  description: "캐시 미스 수"

# DB 쿼리 (핵심 지표)
cache_stampede_db_queries:
  type: counter
  labels: [cache_key, service, instance, trigger]
  # trigger: "lock_acquired" | "lock_wait_timeout" | "fallback"
  description: "실제 DB 쿼리 수 (trigger별 분류)"

cache_stampede_duplicate_queries:
  type: counter
  labels: [cache_key, service, instance]
  description: "중복 DB 쿼리 수 (Stampede 발생 지표)"

# 분산 락
cache_stampede_lock_acquired:
  type: counter
  labels: [cache_key, service, instance]
  description: "락 획득 성공 수"

cache_stampede_lock_failed:
  type: counter
  labels: [cache_key, service, instance]
  description: "락 획득 실패 수"

cache_stampede_lock_wait_success:
  type: counter
  labels: [cache_key, service, instance]
  description: "락 대기 후 캐시 히트 수"

cache_stampede_lock_wait_timeout:
  type: counter
  labels: [cache_key, service, instance]
  description: "락 대기 타임아웃 수 (fallback 트리거)"
```

### 2. 지연 시간 (Histogram)

```yaml
cache_stampede_response_time_ms:
  type: histogram
  labels: [cache_key, service, instance, result]
  # result: "hit" | "miss_lock_acquired" | "miss_lock_wait" | "miss_fallback"
  buckets: [5, 10, 25, 50, 100, 250, 500, 1000]
  description: "요청별 응답 시간 분포"

cache_stampede_lock_wait_time_ms:
  type: histogram
  labels: [cache_key, service, instance]
  buckets: [10, 25, 50, 100, 200, 300, 500]
  description: "락 대기 시간 분포"

cache_stampede_db_query_time_ms:
  type: histogram
  labels: [cache_key, service, instance]
  buckets: [10, 25, 50, 100, 250, 500, 1000]
  description: "DB 쿼리 실행 시간"
```

### 3. 게이지 (Gauge)

```yaml
cache_stampede_active_lock_holders:
  type: gauge
  labels: [service, instance]
  description: "현재 락을 보유 중인 요청 수"

cache_stampede_waiting_requests:
  type: gauge
  labels: [cache_key, service, instance]
  description: "락 대기 중인 요청 수"

cache_stampede_cache_ttl_remaining_seconds:
  type: gauge
  labels: [cache_key]
  description: "캐시 남은 TTL (Early Refresh 트리거 모니터링)"
```

---

## 🚨 알림 규칙 (AlertManager)

### Critical (즉시 대응)

```yaml
- alert: CacheStampedeDetected
  expr: rate(cache_stampede_duplicate_queries[1m]) > 0
  for: 10s
  labels:
    severity: critical
  annotations:
    summary: "Cache Stampede 발생!"
    description: "{{ $labels.cache_key }}에서 중복 DB 쿼리 {{ $value }}/min 감지"
    runbook: "https://wiki/runbooks/cache-stampede"

- alert: LockWaitTimeoutSpike
  expr: rate(cache_stampede_lock_wait_timeout[1m]) > 10
  for: 30s
  labels:
    severity: critical
  annotations:
    summary: "락 대기 타임아웃 급증"
    description: "{{ $labels.cache_key }}에서 락 대기 타임아웃 {{ $value }}/min"
```

### Warning (주의 관찰)

```yaml
- alert: HighLockContention
  expr: >
    cache_stampede_lock_failed / 
    (cache_stampede_lock_acquired + cache_stampede_lock_failed) > 0.5
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "락 경합률 50% 초과"
    description: "{{ $labels.cache_key }} 락 획득 실패율이 높음"

- alert: CacheMissRateHigh
  expr: >
    rate(cache_stampede_misses[5m]) / 
    rate(cache_stampede_total_requests[5m]) > 0.3
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "캐시 미스율 30% 초과"
    description: "TTL 또는 Early Refresh 설정 확인 필요"

- alert: DBQueryTimeIncreased
  expr: histogram_quantile(0.95, cache_stampede_db_query_time_ms) > 200
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "DB 쿼리 P95 200ms 초과"
    description: "Lock TTL 증가 검토 필요"
```

---

## 📈 Grafana 대시보드 패널

### Row 1: 요약 (4 패널)

| 패널 | 쿼리 | 타입 |
|------|------|------|
| 총 요청 | `sum(rate(cache_stampede_total_requests[1m]))` | Stat |
| 캐시 히트율 | `sum(rate(cache_stampede_hits[1m])) / sum(rate(cache_stampede_total_requests[1m])) * 100` | Gauge |
| 실제 DB 쿼리 | `sum(rate(cache_stampede_db_queries[1m]))` | Stat |
| **Stampede 발생** | `sum(cache_stampede_duplicate_queries)` | Stat (RED if > 0) |

### Row 2: 락 상태 (3 패널)

| 패널 | 쿼리 | 타입 |
|------|------|------|
| 락 획득 vs 실패 | `rate(cache_stampede_lock_acquired[1m])` vs `rate(cache_stampede_lock_failed[1m])` | Time Series (Stacked) |
| 락 대기 결과 | `rate(cache_stampede_lock_wait_success[1m])` vs `rate(cache_stampede_lock_wait_timeout[1m])` | Pie Chart |
| 현재 대기자 | `cache_stampede_waiting_requests` | Gauge |

### Row 3: 지연 시간 (2 패널)

| 패널 | 쿼리 | 타입 |
|------|------|------|
| 응답 시간 분포 | `histogram_quantile(0.5/0.95/0.99, cache_stampede_response_time_ms)` | Time Series |
| 락 대기 시간 | `histogram_quantile(0.95, cache_stampede_lock_wait_time_ms)` | Time Series |

### Row 4: 인스턴스별 (2 패널)

| 패널 | 쿼리 | 타입 |
|------|------|------|
| 인스턴스별 DB 쿼리 | `rate(cache_stampede_db_queries[1m]) by (instance)` | Time Series |
| 인스턴스별 락 경합 | `rate(cache_stampede_lock_failed[1m]) by (instance)` | Table |

---

## 📝 구조화된 로그

### 필수 필드

```json
{
  "timestamp": "2025-12-13T12:34:56.789Z",
  "level": "INFO",
  "service": "shopping-api",
  "instance": "web-1",
  "event": "cache_stampede_db_query",
  "cache_key": "product:123:detail",
  "trigger": "lock_acquired",
  "db_query_number": 1,
  "is_duplicate": false,
  "lock_wait_ms": 0,
  "total_elapsed_ms": 45.2,
  "trace_id": "abc123",
  "span_id": "def456"
}
```

### 로그 레벨 정의

| 이벤트 | 레벨 | 조건 |
|--------|------|------|
| 정상 캐시 히트 | DEBUG | 항상 |
| 락 획득 + DB 쿼리 | INFO | 항상 |
| 락 대기 후 히트 | INFO | 항상 |
| 락 대기 타임아웃 | WARN | 항상 |
| **중복 DB 쿼리** | **ERROR** | `is_duplicate: true` |
| 락 해제 실패 | WARN | 토큰 불일치 |

### 로그 예시 (중복 쿼리 발생 시)

```json
{
  "timestamp": "2025-12-13T12:34:56.789Z",
  "level": "ERROR",
  "service": "shopping-api",
  "instance": "web-2",
  "event": "cache_stampede_duplicate_query",
  "cache_key": "product:123:detail",
  "trigger": "lock_wait_timeout",
  "db_query_number": 3,
  "is_duplicate": true,
  "expected_query_count": 1,
  "actual_query_count": 3,
  "trace_id": "abc123",
  "message": "STAMPEDE DETECTED: product:123:detail received 3 DB queries (expected 1)"
}
```

---

## 🔍 트레이스 (OpenTelemetry)

### Span 구조

```
[request]
  └── [cache_lookup] (cache_key, result=hit/miss)
        ├── [lock_acquire] (acquired=true/false, wait_ms)
        │     └── [db_query] (query_number, is_duplicate)
        │           └── [cache_set] (ttl, jitter_applied)
        └── [lock_release] (success=true/false)
```

### Span Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `cache.key` | string | 캐시 키 |
| `cache.result` | string | "hit", "miss" |
| `cache.ttl_remaining_ms` | int | 남은 TTL |
| `lock.acquired` | bool | 락 획득 여부 |
| `lock.wait_ms` | int | 락 대기 시간 |
| `db.query_number` | int | 이 키에 대한 N번째 쿼리 |
| `db.is_duplicate` | bool | 중복 쿼리 여부 |
| `stampede.detected` | bool | Stampede 발생 여부 |

---

## ✅ 검증 체크리스트

| # | 항목 | 검증 방법 |
|---|------|-----------|
| 1 | Stampede 발생 시 **30초 내** 알림 | AlertManager 규칙 테스트 |
| 2 | 중복 쿼리 발생 위치 **즉시 특정** | 로그에 `cache_key`, `instance`, `trace_id` 포함 확인 |
| 3 | 락 경합률 **실시간 모니터링** | Grafana 대시보드 패널 확인 |
| 4 | DB 쿼리 트리거 분류 | `trigger` 라벨로 원인 구분 (lock_acquired vs timeout vs fallback) |
| 5 | 분산 환경 인스턴스별 분석 | `instance` 라벨로 문제 노드 식별 |

---

## 🔗 관련 문서

- [Stage 35: Cache Stampede Prevention](../load_tests/scenarios/stage35_cache_stampede.py)
- [Stage 35 Redis Implementation](../load_tests/scenarios/stage35_redis_stampede.py)
- [Stage 35 v2 Distributed Test](../load_tests/scenarios/stage35_distributed_test_v2.py)
- [Stage 31: Cascade Failure (TC-31-4)](../load_tests/scenarios/stage31_cascade_extended.py)
- [Stage 26: Connection Pool (TC-26-4)](../load_tests/scenarios/stage26_connection_pool.py)
