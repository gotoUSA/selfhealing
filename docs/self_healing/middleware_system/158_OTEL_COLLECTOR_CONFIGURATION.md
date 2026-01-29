# 158. OpenTelemetry Collector 구성 계획

> **문서 목적**: OTEL Collector를 배포하고 텔레메트리 파이프라인을 구성하는 상세 계획을 제시합니다.

---

## 1. OTEL Collector 역할

### 1.1 현재 데이터 흐름

```
┌─────────────┐         ┌─────────────┐
│   Django    │────────▶│  Prometheus │
│   /metrics  │  scrape │             │
└─────────────┘         └──────┬──────┘
                               │
                               ▼
                        ┌─────────────┐
                        │   Grafana   │
                        └─────────────┘
```

**현재 구성 (docker/prometheus/prometheus.yml 기반):**

| 수집 대상 | 엔드포인트 | scrape_interval |
|----------|-----------|-----------------|
| prometheus | localhost:9090 | 15s |
| django | web:8000/metrics | 15s |
| celery | celery:9808/metrics | 15s (선택) |
| redis | redis:9121/metrics | 15s (선택) |
| postgres | postgres-exporter:9187/metrics | 15s (선택) |
| locust | locust:8089/stats/prometheus | 15s (테스트용) |

### 1.2 목표 데이터 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│                     Self-Healing System                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  Django  │  │  Celery  │  │  Redis   │  │ Postgres │        │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
│       │             │             │             │               │
│       └─────────────┴──────┬──────┴─────────────┘               │
│                            │ OTLP                               │
└────────────────────────────┼────────────────────────────────────┘
                             ▼
                   ┌──────────────────┐
                   │  OTEL Collector  │
                   │                  │
                   │  Receivers:      │
                   │  - otlp (4317)   │
                   │  - prometheus    │
                   │                  │
                   │  Exporters:      │
                   │  - prometheusrw  │
                   │  - otlp (tempo)  │
                   │  - loki          │
                   └────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │    Mimir    │   │    Tempo    │   │    Loki     │
   │  (Metrics)  │   │  (Traces)   │   │   (Logs)    │
   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                     ┌─────────────┐
                     │   Grafana   │
                     └─────────────┘
```

---

## 2. Collector 구성 요소

### 2.1 Receivers (데이터 수신)

| Receiver | 용도 | 포트 |
|----------|------|------|
| `otlp` | OTEL SDK에서 Traces/Metrics/Logs 수신 | 4317 (gRPC), 4318 (HTTP) |
| `prometheus` | 기존 Prometheus 메트릭 scrape 호환 | - |

**기존 Prometheus scrape 호환성:**

현재 `docker/prometheus/prometheus.yml`에 정의된 scrape_configs를 Collector에서도 유지할 수 있습니다:
- django: web:8000/metrics
- celery: celery:9808/metrics (선택)
- redis: redis:9121/metrics (선택)

### 2.2 Processors (데이터 처리)

| Processor | 용도 | 설정 |
|-----------|------|------|
| `batch` | 배치 처리로 효율성 향상 | send_batch_size: 1000, timeout: 10s |
| `memory_limiter` | 메모리 사용량 제한 | limit_mib: 512, check_interval: 1s |
| `resource` | 리소스 속성 추가 | service.name, deployment.environment |
| `attributes` | 속성 변환/필터링 | 민감 정보 마스킹 |

**리소스 속성 (현재 코드 기반):**

| 속성 | 값 | 근거 |
|------|---|------|
| `service.name` | selfhealing | `prometheus.py`: prefix="selfhealing" |
| `deployment.environment` | development/production | `docker-compose.yml`: ENVIRONMENT 환경변수 |
| `service.version` | - | 배포 시 설정 |

### 2.3 Exporters (데이터 전송)

| Exporter | 대상 | 용도 |
|----------|------|------|
| `prometheusremotewrite` | Mimir | 메트릭 장기 저장 |
| `otlp` | Tempo | 분산 추적 저장 |
| `loki` | Loki | 로그 저장 |

---

## 3. 파이프라인 설계

### 3.1 Traces 파이프라인

```
receivers: [otlp]
    │
    ▼
processors: [memory_limiter, batch, resource]
    │
    ▼
exporters: [otlp/tempo]
```

**Trace 데이터 흐름:**
1. Django/Celery에서 OTEL SDK로 Span 생성
2. OTLP로 Collector에 전송 (4317 포트)
3. 리소스 속성 추가 (service.name 등)
4. Tempo로 전송

### 3.2 Metrics 파이프라인

```
receivers: [otlp, prometheus]
    │
    ▼
processors: [memory_limiter, batch, resource]
    │
    ▼
exporters: [prometheusremotewrite]
```

**Metrics 데이터 흐름:**
1. OTEL SDK 메트릭 → OTLP receiver
2. 기존 Prometheus 메트릭 → prometheus receiver (scrape)
3. 리소스 속성 추가
4. Mimir로 remote write

**현재 메트릭과의 호환성:**

`selfhealing/metrics/prometheus.py`에 정의된 메트릭들:
- Counter: dlq_items_total, retry_outcomes_total, circuit_breaker_failures_total 등
- Gauge: dlq_pending_count, circuit_breaker_state, retry_success_rate 등
- Histogram: retry_attempts_distribution, recovery_time_seconds 등

이 메트릭들은:
- 기존 방식: Prometheus가 /metrics 엔드포인트 scrape
- 새 방식: Collector가 scrape 후 Mimir로 remote write

### 3.3 Logs 파이프라인

```
receivers: [otlp]
    │
    ▼
processors: [memory_limiter, batch, resource, attributes]
    │
    ▼
exporters: [loki]
```

**Logs 데이터 흐름:**
1. Django 로깅 → OTEL Log Exporter
2. OTLP로 Collector에 전송
3. 속성 처리 (trace_id 추출 등)
4. Loki로 전송

**현재 로깅과의 연동:**

`selfhealing/settings/logging_config.py` 설정:
- `structured_json=True`: 이미 JSON 형식
- `include_timestamps=True`: 타임스탬프 포함
- `include_request_id=True`: trace_id 포함

---

## 4. Docker Compose 통합

### 4.1 새로운 서비스 정의

현재 `docker-compose.yml`에 다음 서비스 추가 필요:

| 서비스 | 이미지 | 용도 |
|--------|--------|------|
| otel-collector | otel/opentelemetry-collector-contrib | 텔레메트리 수집/분배 |
| tempo | grafana/tempo | 분산 추적 저장 |
| loki | grafana/loki | 로그 저장 |
| mimir | grafana/mimir (선택) | 메트릭 장기 저장 |

### 4.2 네트워크 구성

| 포트 | 서비스 | 용도 |
|------|--------|------|
| 4317 | otel-collector | OTLP gRPC 수신 |
| 4318 | otel-collector | OTLP HTTP 수신 |
| 3200 | tempo | Tempo API |
| 3100 | loki | Loki API |
| 9009 | mimir | Mimir API |

### 4.3 볼륨 구성

| 볼륨 | 마운트 포인트 | 용도 |
|------|-------------|------|
| otel_config | /etc/otelcol | Collector 설정 |
| tempo_data | /var/tempo | Trace 데이터 |
| loki_data | /var/loki | 로그 데이터 |
| mimir_data | /var/mimir | 메트릭 데이터 |

---

## 5. 구현 순서 체크리스트

### 5.1 Phase 1: Collector 기본 배포

- [ ] **1.1** Collector 설정 디렉토리 생성
  - `docker/otel-collector/` 디렉토리
- [ ] **1.2** Collector 기본 설정 파일 작성
  - `docker/otel-collector/otel-collector-config.yml`
- [ ] **1.3** docker-compose.yml에 otel-collector 서비스 추가
- [ ] **1.4** Collector 헬스체크 설정
- [ ] **1.5** Collector 시작 검증

### 5.2 Phase 2: Traces 파이프라인 구성

- [ ] **2.1** otlp receiver 설정
  - gRPC: 4317 포트
  - HTTP: 4318 포트
- [ ] **2.2** Tempo 서비스 추가
  - `docker/tempo/tempo.yml` 설정
  - docker-compose.yml에 tempo 서비스
- [ ] **2.3** otlp exporter (Tempo) 설정
- [ ] **2.4** traces 파이프라인 설정
- [ ] **2.5** Trace 수신 검증

### 5.3 Phase 3: Metrics 파이프라인 구성

- [ ] **3.1** prometheus receiver 설정
  - 기존 scrape_configs 마이그레이션
- [ ] **3.2** Mimir 서비스 추가 (선택)
  - 또는 기존 Prometheus 유지
- [ ] **3.3** prometheusremotewrite exporter 설정
- [ ] **3.4** metrics 파이프라인 설정
- [ ] **3.5** 기존 메트릭 수집 검증

### 5.4 Phase 4: Logs 파이프라인 구성

- [ ] **4.1** Loki 서비스 추가
  - `docker/loki/loki.yml` 설정
  - docker-compose.yml에 loki 서비스
- [ ] **4.2** loki exporter 설정
- [ ] **4.3** logs 파이프라인 설정
- [ ] **4.4** 속성 프로세서 설정
  - trace_id 추출
  - 민감 정보 마스킹
- [ ] **4.5** 로그 수신 검증

### 5.5 Phase 5: 통합 검증

- [ ] **5.1** 전체 파이프라인 E2E 테스트
- [ ] **5.2** Grafana 데이터소스 추가
  - Tempo datasource
  - Loki datasource
  - Mimir datasource (또는 기존 Prometheus 유지)
- [ ] **5.3** 상관관계 검증
  - Trace → Logs 연동
  - Metrics → Traces 연동
- [ ] **5.4** 성능 테스트
  - 부하 시 데이터 손실 없음
  - 메모리 사용량 제한 동작

---

## 6. 기존 인프라 호환성

### 6.1 Prometheus 공존 전략

| 옵션 | 설명 | 권장 |
|------|------|------|
| 옵션 1 | Prometheus 유지 + Collector 병렬 | 초기 전환 시 |
| 옵션 2 | Collector가 Prometheus 역할 대체 | 안정화 후 |
| 옵션 3 | Mimir로 완전 대체 | 장기 목표 |

**초기 권장 (옵션 1):**
- 기존 Prometheus → Grafana 경로 유지
- Collector → Tempo/Loki 경로 추가
- 점진적으로 Mimir로 전환

### 6.2 Grafana 대시보드 호환성

현재 대시보드 (`docker/grafana/provisioning/dashboards/`):
- self_healing_overview.json
- dlq_monitoring.json
- cascade_event_audit.json
- error_budget.json
- error_budget_gate.json

**호환성 유지 방법:**
1. Prometheus datasource 유지
2. Mimir 전환 시 datasource URL만 변경
3. PromQL 쿼리 동일하게 동작

### 6.3 기존 Alerting 호환성

현재 `docker/prometheus/rules/`에 alert 규칙 정의:

**호환성 유지:**
- Prometheus/Mimir 모두 동일한 alerting rules 지원
- Collector 파이프라인과 무관하게 동작

---

## 7. 환경별 구성

### 7.1 개발 환경

| 구성요소 | 설정 |
|---------|------|
| Collector | 로컬 단일 인스턴스 |
| Tempo | 로컬 파일 저장 |
| Loki | 로컬 파일 저장 |
| 보존 기간 | 1일 |

### 7.2 프로덕션 환경

| 구성요소 | 설정 |
|---------|------|
| Collector | 복제본 2개 이상 |
| Tempo | 객체 스토리지 (S3/GCS) |
| Loki | 객체 스토리지 (S3/GCS) |
| Mimir | 객체 스토리지 (S3/GCS) |
| 보존 기간 | 30일+ |

---

## 8. 모니터링 및 알림

### 8.1 Collector 자체 모니터링

| 메트릭 | 설명 | 알림 조건 |
|--------|------|----------|
| otelcol_receiver_accepted_spans | 수신된 Span 수 | 급격한 감소 시 |
| otelcol_exporter_send_failed_spans | 전송 실패 Span 수 | > 0 지속 시 |
| otelcol_process_memory_rss | 메모리 사용량 | 제한의 80% 초과 시 |

### 8.2 파이프라인 헬스 체크

| 대상 | 체크 방법 | 주기 |
|------|----------|------|
| Collector | /health 엔드포인트 | 30s |
| Tempo | /ready 엔드포인트 | 30s |
| Loki | /ready 엔드포인트 | 30s |

---

## 9. 관련 문서

- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md): 전체 아키텍처 개요
- [157_OTEL_SDK_INTEGRATION.md](157_OTEL_SDK_INTEGRATION.md): SDK 도입 계획
- [159_GRAFANA_STACK_INTEGRATION.md](159_GRAFANA_STACK_INTEGRATION.md): Grafana 스택 통합
