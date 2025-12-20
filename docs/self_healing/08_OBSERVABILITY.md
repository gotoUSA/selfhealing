# Self-Healing Observability & Metrics

## 개요

Self-Healing 시스템의 관측성(Observability) 계층은 **Prometheus 메트릭**과 **Grafana 대시보드**를 통해 실시간 모니터링, 알림, 시각화를 제공합니다.

### 핵심 원칙

1. **모든 상태 변화는 측정 가능해야 함**
2. **SLA 위반은 즉시 감지되어야 함**
3. **장애 원인 분석을 위한 충분한 컨텍스트 제공**
4. **운영자가 시스템 상태를 한눈에 파악할 수 있어야 함**

---

## 메트릭 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    Django Application                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         shopping/services/self_healing/metrics.py     │  │
│  │                                                       │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐ │  │
│  │  │ Counter │  │  Gauge  │  │Histogram│  │ Summary │ │  │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘ │  │
│  │       └────────────┴────────────┴────────────┘      │  │
│  │                         │                            │  │
│  │              /metrics endpoint                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Prometheus                              │
│  ┌──────────────────┐  ┌──────────────────────────────┐    │
│  │  scrape_interval │  │      Alerting Rules          │    │
│  │      15s         │  │  (docker/prometheus/rules/)  │    │
│  └──────────────────┘  └──────────────────────────────┘    │
│                              │                               │
│              ┌───────────────┴───────────────┐              │
│              ▼                               ▼              │
│     ┌─────────────┐                 ┌─────────────┐        │
│     │ Alertmanager│                 │   Grafana   │        │
│     │  (Alerts)   │                 │ (Dashboard) │        │
│     └─────────────┘                 └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

## 메트릭 카테고리

### 1. DLQ (Dead Letter Queue) 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `dlq_items_total` | Counter | `domain`, `failure_type` | 생성된 전체 DLQ 항목 수 |
| `dlq_pending_count` | Gauge | `domain` | 현재 대기 중인 DLQ 항목 수 |
| `dlq_items_by_status` | Gauge | `status` | 상태별 DLQ 항목 수 |
| `dlq_created_total` | Counter | `domain` | DLQ 생성 속도 계산용 카운터 |

**사용 예시:**

```python
from shopping.services.self_healing.metrics import (
    record_dlq_item_created,
    update_dlq_pending_gauges,
)

# DLQ 항목 생성 시
record_dlq_item_created(domain="payment", failure_type="PG_TIMEOUT")

# 주기적 게이지 업데이트 (Celery task에서 호출)
pending = update_dlq_pending_gauges()
# Returns: {"payment": 5, "point": 2, ...}
```

### 2. Retry (재시도) 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `retry_attempts_total` | Histogram | `domain` | 해결까지의 시도 횟수 분포 |
| `retry_outcomes_total` | Counter | `domain`, `outcome` | 재시도 결과 (success/failure/exhausted) |
| `retry_success_rate` | Gauge | `domain` | 도메인별 재시도 성공률 (%) |

**Histogram 버킷:**
```python
buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
```

**사용 예시:**

```python
from shopping.services.self_healing.metrics import record_retry_attempt

# 재시도 결과 기록
record_retry_attempt(
    domain="payment",
    attempt_count=3,
    outcome="success"  # success, failure, exhausted
)
```

### 3. Recovery (복구) 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `recovery_time_seconds` | Histogram | `domain`, `resolution_type` | 장애 발생부터 해결까지 시간 |
| `sla_breach_total` | Counter | `domain` | SLA 위반 횟수 |
| `human_review_queue_time_seconds` | Histogram | `domain` | 수동 검토 대기 시간 |

**Histogram 버킷:**
```python
# recovery_time_seconds
buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400)
# 1분, 5분, 15분, 30분, 1시간, 2시간, 4시간, 8시간, 24시간

# human_review_queue_time_seconds
buckets=(300, 900, 1800, 3600, 7200, 14400, 28800)
# 5분, 15분, 30분, 1시간, 2시간, 4시간, 8시간
```

**사용 예시:**

```python
from shopping.services.self_healing.metrics import (
    record_recovery_time,
    record_sla_breach,
    track_recovery_time,
)

# 복구 시간 기록
record_recovery_time(
    domain="payment",
    resolution_type="auto_replay",
    created_at=created_at,
    resolved_at=resolved_at,
)

# SLA 위반 기록
record_sla_breach(domain="payment")

# Context Manager를 사용한 복구 시간 측정
with track_recovery_time("payment", "auto_replay"):
    # 복구 작업 수행
    perform_recovery()
```

### 4. Circuit Breaker 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `circuit_breaker_state` | Gauge | `service` | CB 상태 (0=closed, 1=open, 2=half-open) |
| `circuit_breaker_transitions_total` | Counter | `service`, `from_state`, `to_state` | 상태 전환 횟수 |
| `circuit_breaker_open_duration_seconds` | Histogram | `service` | OPEN 상태 유지 시간 |

**상태 값 매핑:**
```python
state_mapping = {
    "closed": 0,
    "open": 1,
    "half_open": 2,
}
```

**사용 예시:**

```python
from shopping.services.self_healing.metrics import (
    record_circuit_breaker_state_change,
    record_circuit_breaker_open_duration,
)

# 상태 변경 기록
record_circuit_breaker_state_change(
    service="toss_payment",
    from_state="closed",
    to_state="open",
)

# OPEN 상태 유지 시간 기록
record_circuit_breaker_open_duration(
    service="toss_payment",
    duration_seconds=300.0,
)
```

### 5. Replay (리플레이) 메트릭

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `replay_attempts_total` | Counter | `domain`, `replay_type` | 리플레이 시도 횟수 |
| `replay_outcomes_total` | Counter | `domain`, `outcome` | 리플레이 결과 (success/failure/rejected) |

**리플레이 타입:**
- `single`: 단일 항목 리플레이
- `batch`: 배치 리플레이
- `conditional`: 조건부 리플레이 (CB 닫힘 시)

**데코레이터 사용:**

```python
from shopping.services.self_healing.metrics import track_replay

@track_replay("batch")
def batch_replay(domain: str, items: list):
    # 리플레이 로직

### 6. Error Budget 메트릭 (NEW)

> 상세 문서: [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md)

Error Budget 관리 및 배포 정책에 관련된 메트릭입니다.

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|-------------|------|--------|------|
| `error_budget_remaining_percent` | Gauge | `slo_name` | Error Budget 잔여량 (%) |
| `error_budget_remaining_minutes` | Gauge | `slo_name` | Error Budget 잔여량 (분) |
| `error_budget_burn_rate_1h` | Gauge | `slo_name` | 1시간 Burn Rate |
| `error_budget_burn_rate_6h` | Gauge | `slo_name` | 6시간 Burn Rate |
| `deployment_freeze_status` | Gauge | `slo_name` | 동결 상태 (0-3) |
| `freeze_decision_total` | Counter | `decision_type` | 동결 결정 횟수 |
| `deployment_active_override` | Gauge | - | 활성 Override 여부 |

**동결 상태 값 매핑:**
```python
freeze_status_mapping = {
    "proceed": 0,        # 정상 배포 가능
    "caution": 1,        # 주의하여 배포
    "warning": 2,        # 신규 기능 자제
    "freeze_recommended": 3,  # 동결 권고
}
```

**사용 예시:**

```python
from selfhealing.services.metrics import (
    record_error_budget_status,
    record_deployment_freeze_status,
    record_freeze_decision,
    record_active_override,
)

# Error Budget 상태 기록
record_error_budget_status(
    slo_name="availability",
    remaining_percent=65.5,
    remaining_minutes=28.3,
    burn_rate_1h=2.1,
    burn_rate_6h=1.8,
)

# 배포 동결 상태 기록
record_deployment_freeze_status(slo_name="availability", status="warning")

# 동결 결정 기록
record_freeze_decision(decision_type="freeze_acknowledged")

# 활성 Override 기록
record_active_override(is_active=True)
    return ReplayResult(success=True)
```

---

## 도메인 정의

```python
# shopping/services/self_healing/metrics.py

DOMAINS: list[str] = [
    "payment",      # 결제 도메인
    "point",        # 포인트 도메인
    "inventory",    # 재고 도메인
    "webhook",      # 웹훅 도메인
    "notification", # 알림 도메인
]
```

새로운 도메인 추가 시 이 리스트를 업데이트해야 합니다.

---

## 주기적 메트릭 수집

Gauge 타입 메트릭은 주기적으로 데이터베이스에서 값을 읽어 업데이트해야 합니다.

### collect_all_metrics()

```python
from shopping.services.self_healing.metrics import collect_all_metrics

# Celery 태스크에서 호출
result = collect_all_metrics()
# Returns:
# {
#     "dlq_pending_by_domain": {"payment": 5, "point": 2},
#     "dlq_by_status": {"pending": 7, "resolved": 100},
#     "circuit_breaker_states": {"toss_payment": "closed"},
#     "retry_success_rates": {"payment": 95.5, "point": 88.2},
#     "collected_at": "2024-01-15T10:30:00.123456",
# }
```

### 개별 게이지 업데이트 함수

| 함수 | 설명 |
|------|------|
| `update_dlq_pending_gauges()` | 도메인별 대기 중인 DLQ 항목 수 업데이트 |
| `update_dlq_status_gauges()` | 상태별 DLQ 항목 분포 업데이트 |
| `update_circuit_breaker_gauges()` | Circuit Breaker 상태 업데이트 |
| `update_retry_success_rates()` | 도메인별 재시도 성공률 업데이트 |

---

## Prometheus 설정

### 기본 설정

파일 위치: [docker/prometheus/prometheus.yml](../../docker/prometheus/prometheus.yml)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  
  external_labels:
    environment: 'production'
    service: 'self-healing'

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  # Django 애플리케이션 메트릭
  - job_name: 'django'
    static_configs:
      - targets: ['web:8000']
    metrics_path: /metrics
    scrape_interval: 15s
    scrape_timeout: 10s

  # Celery Worker 메트릭
  - job_name: 'celery'
    static_configs:
      - targets: ['celery:9808']
    metrics_path: /metrics
```

---

## 알림 규칙 (Alerting Rules)

파일 위치: [docker/prometheus/rules/alerts.yml](../../docker/prometheus/rules/alerts.yml)

### DLQ 알림

| 알림 이름 | 조건 | Severity | 설명 |
|-----------|------|----------|------|
| `DLQHighPendingCount` | `sum(dlq_pending_count) > 50` for 5m | warning | DLQ 대기 항목 증가 |
| `DLQCriticalPendingCount` | `sum(dlq_pending_count) > 100` for 3m | critical | DLQ 위험 수준 |
| `DLQSpikeDetected` | 5분 대비 2배 이상 증가 | warning | DLQ 급증 감지 |

### Circuit Breaker 알림

| 알림 이름 | 조건 | Severity | 설명 |
|-----------|------|----------|------|
| `CircuitBreakerOpen` | `circuit_breaker_state == 1` for 1m | critical | CB 열림 상태 |
| `CircuitBreakerHalfOpen` | `circuit_breaker_state == 2` for 5m | warning | 복구 시도 중 |
| `CircuitBreakerHighFailureRate` | 실패율 > 30% for 3m | warning | 높은 실패율 |

### Retry 알림

| 알림 이름 | 조건 | Severity | 설명 |
|-----------|------|----------|------|
| `RetrySuccessRateLow` | 성공률 < 80% for 5m | warning | 재시도 성공률 저하 |
| `RetryRateHigh` | rate > 10/s for 5m | warning | 재시도 급증 |

### SLO/Error Budget 알림

| 알림 이름 | 조건 | Severity | 설명 |
|-----------|------|----------|------|
| `SLOAvailabilityViolation` | 가용성 < 99.9% | critical | SLO 위반 |
| `ErrorBudgetCritical` | 잔여 < 25% | critical | Error Budget 위험 |
| `ErrorBudgetWarning` | 잔여 < 50% | warning | Error Budget 경고 |
| `ErrorBudgetFastBurn` | 1시간 Burn Rate > 14.4 | critical | 급속 소진 |
| `ErrorBudgetSlowBurn` | 6시간 Burn Rate > 3 | warning | 느린 소진 |

### 시스템 알림

| 알림 이름 | 조건 | Severity | 설명 |
|-----------|------|----------|------|
| `SelfHealingMetricsDown` | `up{job="django"} == 0` for 2m | critical | 메트릭 수집 실패 |
| `RequestThroughputDrop` | 1시간 전 대비 50% 이하 | warning | 처리량 급감 |

---

## 코드 기반 알림 규칙 정의

메트릭 모듈에는 Prometheus 알림 규칙의 코드 기반 정의가 포함되어 있습니다:

```python
# shopping/services/self_healing/metrics.py

ALERTING_RULES = {
    "DLQPendingHigh": {
        "expr": "dlq_pending_count > 10",
        "for": "5m",
        "severity": "warning",
        "team": "ops",
        "summary": "DLQ pending count is high",
        "description": "More than 10 items pending in DLQ for domain {{ $labels.domain }}",
        "runbook_url": "https://docs.internal/runbooks/dlq-pending-high",
    },
    "CircuitBreakerOpen": {
        "expr": "circuit_breaker_state == 1",
        "for": "1m",
        "severity": "critical",
        "team": "ops",
        "summary": "Circuit breaker is open",
        "description": "Circuit breaker for {{ $labels.service }} is in OPEN state",
        "runbook_url": "https://docs.internal/runbooks/circuit-breaker-open",
    },
    # ... 더 많은 규칙
}
```

알림 규칙 YAML 생성:
```bash
python manage.py generate_self_healing_alerts
```

---

## Grafana 대시보드

### 사용 가능한 대시보드

| 대시보드 | 파일 위치 | 설명 |
|----------|-----------|------|
| Self-Healing Overview | [self_healing_overview.json](../../docker/grafana/provisioning/dashboards/self_healing_overview.json) | 전체 시스템 상태 요약 |
| DLQ Monitoring | [dlq_monitoring.json](../../docker/grafana/provisioning/dashboards/dlq_monitoring.json) | DLQ 상세 모니터링 |
| Error Budget | [error_budget.json](../../docker/grafana/provisioning/dashboards/error_budget.json) | SLO/Error Budget 추적 |

### Self-Healing Overview 대시보드

#### 패널 구성

**1. 시스템 상태 요약 Row**

| 패널 | 타입 | 쿼리 | 설명 |
|------|------|------|------|
| Circuit Breaker 상태 | Stat | `selfhealing_circuit_breaker_state{service=~"$service"}` | CB 상태 (색상 코드) |
| DLQ 대기 항목 | Stat | `sum(dlq_pending_count)` | 전체 대기 항목 수 |
| 재시도 성공률 | Gauge | `avg(retry_success_rate)` | 평균 재시도 성공률 |
| SLA 위반 | Stat | `increase(sla_breach_total[24h])` | 24시간 SLA 위반 횟수 |

**2. Circuit Breaker 상태 매핑**

```json
{
  "mappings": [
    {"options": {"0": {"color": "green", "text": "CLOSED"}}},
    {"options": {"1": {"color": "red", "text": "OPEN"}}},
    {"options": {"2": {"color": "yellow", "text": "HALF-OPEN"}}}
  ]
}
```

**3. DLQ 대기 항목 임계값**

```json
{
  "thresholds": {
    "steps": [
      {"color": "green", "value": null},
      {"color": "yellow", "value": 10},
      {"color": "orange", "value": 50},
      {"color": "red", "value": 100}
    ]
  }
}
```

### 대시보드 변수

| 변수 | 타입 | 용도 |
|------|------|------|
| `$datasource` | Datasource | Prometheus 데이터소스 선택 |
| `$service` | Query | 서비스 필터링 |
| `$domain` | Query | 도메인 필터링 |
| `$interval` | Interval | 시간 집계 간격 |

---

## 안전한 메트릭 등록

중복 등록 방지를 위한 헬퍼 함수들:

```python
def _get_or_create_counter(name: str, description: str, labels: list[str]) -> Counter:
    """Get existing counter or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Counter(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]

def _get_or_create_gauge(name: str, description: str, labels: list[str]) -> Gauge:
    """Get existing gauge or create new one."""
    # Similar implementation...

def _get_or_create_histogram(
    name: str, 
    description: str, 
    labels: list[str], 
    buckets: tuple = None
) -> Histogram:
    """Get existing histogram or create new one."""
    # Similar implementation...
```

이는 Django의 auto-reload 또는 테스트 환경에서 발생할 수 있는 중복 등록 오류를 방지합니다.

---

## PromQL 쿼리 예시

### DLQ 관련 쿼리

```promql
# 도메인별 대기 중인 DLQ 항목
sum by (domain) (dlq_pending_count)

# 5분간 DLQ 생성 속도
rate(dlq_created_total[5m])

# 도메인별 실패 유형 분포
sum by (domain, failure_type) (dlq_items_total)
```

### Circuit Breaker 쿼리

```promql
# 현재 OPEN 상태인 서비스
selfhealing_circuit_breaker_state == 1

# 최근 1시간 상태 전환 횟수
sum(increase(circuit_breaker_transitions_total[1h])) by (service)

# OPEN 상태 유지 시간 P95
histogram_quantile(0.95, rate(circuit_breaker_open_duration_seconds_bucket[1h]))
```

### 복구 성능 쿼리

```promql
# 도메인별 복구 시간 P95
histogram_quantile(0.95, 
  sum(rate(recovery_time_seconds_bucket[1h])) by (le, domain)
)

# 재시도 성공률
(
  sum(rate(retry_outcomes_total{outcome="success"}[5m])) by (domain)
  /
  sum(rate(retry_outcomes_total[5m])) by (domain)
) * 100

# SLA 위반 추이
increase(sla_breach_total[1d])
```

### Error Budget 쿼리

```promql
# Error Budget 잔여량 (%)
100 * (
  1 - (
    sum(increase(dlq_items_total[30d])) 
    / 
    (sum(increase(requests_total[30d])) * 0.001)
  )
)

# Burn Rate (1시간 기준)
(
  (sum(rate(dlq_items_total[1h])) / sum(rate(requests_total[1h]))) 
  / 0.001 * 720
)
```

---

## 운영 권장사항

### 1. 메트릭 수집 주기

| 메트릭 유형 | 권장 주기 | 이유 |
|-------------|-----------|------|
| Prometheus scrape | 15초 | 적절한 해상도와 리소스 균형 |
| Gauge 업데이트 | 30초~1분 | DB 부하 최소화 |
| Alert evaluation | 15초 | 빠른 알림 감지 |

### 2. 레이블 카디널리티 관리

```python
# 좋은 예: 제한된 레이블 값
dlq_items_total.labels(domain="payment", failure_type="PG_TIMEOUT")

# 나쁜 예: 무한 카디널리티
dlq_items_total.labels(domain="payment", transaction_id=tx_id)  # ❌
```

### 3. 알림 피로도 방지

- `for` 절을 사용하여 일시적 스파이크 무시
- 적절한 severity 레벨 설정
- Runbook URL 포함으로 빠른 대응 지원

### 4. 대시보드 구성

- 가장 중요한 지표를 상단에 배치
- 색상 코드로 상태를 직관적으로 표시
- 드릴다운을 위한 링크 제공

---

## 관련 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) - DLQ 시스템
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드
