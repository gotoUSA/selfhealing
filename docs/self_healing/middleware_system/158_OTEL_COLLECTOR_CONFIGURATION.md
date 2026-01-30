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

### 2.0 셀프힐링 전용 설정 (범용화 불필요)

**결론**: 이 OTEL Collector는 셀프힐링 프로젝트 **전용**으로 범용화가 불필요합니다.

**하드코딩된 설정 (의도적)**:

| 설정 | 값 | 근거 |
|------|---|------|
| `service.name` | selfhealing | 단일 서비스 운영 |
| `deployment.environment` | development | Docker Compose 환경 |
| 백엔드 엔드포인트 | tempo:4317, mimir:9009, loki:3100 | Docker 내부 네트워크 |
| scrape 대상 | web:8000, celery_worker:9808 | 셀프힐링 스택 고정 |

**범용화가 필요한 경우**:
```yaml
# 환경변수로 주입
environment:
  - SERVICE_NAME=${SERVICE_NAME:-selfhealing}
  - TEMPO_ENDPOINT=${TEMPO_ENDPOINT:-tempo:4317}
```

**범용화하지 않는 이유**:
1. 다른 서비스 추가 계획 없음
2. 환경변수화는 복잡성만 증가
3. Docker Compose 환경 고정

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
| `redaction` | 민감 정보 2중 마스킹 | 패턴 기반 필터링 |

### 2.2.1 리소스 제한 (Resource Limits)

**근거**: docker-compose.yml에 현재 CPU 제한 미정의

Collector가 가용 자원을 무제한 사용하여 애플리케이션 성능에 영향을 주는 상황(Resource Starvation) 방지:

| 설정 | 개발 환경 | 프로덕션 환경 | 비고 |
|------|---------|------------|------|
| `cpus` | 0.25 | 0.5 ~ 1.0 | Docker Compose deploy |
| `mem_limit` | 256mb | 512mb | memory_limiter와 일치 |
| `mem_reservation` | 128mb | 256mb | 최소 보장 메모리 |

**Docker Compose 설정 예시:**
```yaml
otel-collector:
  deploy:
    resources:
      limits:
        cpus: "0.5"
        memory: 512M
      reservations:
        cpus: "0.25"
        memory: 256M
```

**처리량 기준**: 100 req/min Rate Limit (L2 Redis) 기준, 1,000 RPS 처리 가능 목표

### 2.2.2 민감 정보 2중 마스킹 (Redaction Processor) ✅ 구현 완료

**근거**: 115번 문서의 `_mask_error_message` 및 `sensitive_key_patterns` 설정

개발자가 실수로 로그에 토큰을 찍더라도 Collector가 중앙 저장소(Loki/Tempo)로 보내기 전에 필터링하는 '최후의 보루':

| 마스킹 대상 | 패턴 | 대체 값 |
|-------------|--------|----------|
| 내부 IP | `10.*`, `192.168.*`, `172.16-31.*` | `****` |
| 서버 경로 | `/home/*`, `/var/*`, `C:\*` | `****` |
| Bearer 토큰 | `Bearer ...` | `****` |
| JWT 토큰 | `eyJ...` | `****` |
| API Key | `sk-*`, `pk-*` | `****` |

**버전 선택 근거 (v0.144.0)**:

| 버전 | redaction 지원 | loki exporter | 비고 |
|------|---------------|---------------|------|
| v0.96.0 | traces만 | loki 전용 exporter | logs/metrics 미보호 |
| v0.115.0 | traces, logs, metrics | loki 전용 exporter | 완전한 보호 |
| **v0.144.0** | 모든 기능 | otlphttp (Loki OTLP 지원) | **권장 - 최신 안정** |

**v0.144.0 주요 변경사항**:
1. `loki` exporter 제거 → `otlphttp`로 Loki OTLP 엔드포인트 사용
2. `telemetry.metrics.address` → `telemetry.metrics.readers` 구조 변경
3. `otlp` alias deprecated → `otlp_grpc` 권장 (경고만, 동작함)

**업계 관행**: OpenTelemetry는 LTS 없이 빠르게 발전. 6개월 이전 버전은 보안 패치 미지원. 최신 안정 버전 사용 권장.

**구현 파일**: `docker/otel-collector/otel-collector-config.yml`

```yaml
processors:
  redaction:
    allow_all_keys: true
    blocked_values:
      # 내부 IP 주소 (RFC 1918)
      - "10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"
      - "192\\.168\\.\\d{1,3}\\.\\d{1,3}"
      - "172\\.(1[6-9]|2[0-9]|3[0-1])\\.\\d{1,3}\\.\\d{1,3}"
      # 서버 경로
      - "/home/[^\\s]+"
      - "/var/[^\\s]+"
      - "C:\\\\[^\\s]+"
      # Bearer/JWT 토큰
      - "Bearer\\s+[A-Za-z0-9\\-_.]+"
      - "eyJ[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+"
    summary: debug

# v0.115.0에서 모든 파이프라인에 redaction 적용
service:
  pipelines:
    traces:
      processors: [..., redaction]
    metrics:
      processors: [..., redaction]
    logs:
      processors: [..., redaction]
```

**통합 테스트**: `tests/integration/otel/test_redaction_processor.py`

**실행 방법**:
```bash
docker-compose -f docker-compose.test.yml run --rm test-otel-redaction
```

### 2.2.3 Fail-Safe 관측성 (Resilient Observability) ✅ 구현 완료

**근거**: 155번 문서의 `_last_known_safe_limit` FAIL-SAFE 패턴

관측성 시스템(Collector/Tempo 등) 장애가 실제 서비스 성능에 영향을 주지 않도록:

| 설정 | 값 | 목적 |
|------|---|------|
| `export_timeout` | 5s | 매우 짧은 타임아웃 |
| `retry_on_failure.max_elapsed_time` | 30s | 최대 재시도 시간 |
| `sending_queue.storage` | file_storage | 로컬 디스크 스풀링 |

**file_storage 익스텐션:**

**구현 파일**: `docker/otel-collector/otel-collector-config.yml`

```yaml
extensions:
  file_storage:
    directory: /var/lib/otelcol/buffer
    timeout: 1s
    compaction:
      directory: /var/lib/otelcol/buffer/compaction
      on_start: true
      on_rebound: true
      rebound_needed_threshold_mib: 50
      rebound_trigger_threshold_mib: 10

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    timeout: 5s
    retry_on_failure:
      enabled: true
      initial_interval: 1s
      max_interval: 10s
      max_elapsed_time: 30s
    sending_queue:
      enabled: true
      num_consumers: 10
      queue_size: 5000
      storage: file_storage  # Collector 장애 시 로컬 스풀링

  # v0.144.0+: loki exporter 제거됨, otlphttp로 Loki OTLP 엔드포인트 사용
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
    sending_queue:
      enabled: true
      num_consumers: 10
      queue_size: 5000
      storage: file_storage  # Loki 장애 시 로컬 스풀링
```

**통합 테스트**: `tests/integration/otel/test_file_storage_extension.py`

**실행 방법**:
```bash
docker-compose -f docker-compose.test.yml run --rm test-otel-file-storage
```

**동작**: Tempo/Mimir/Loki 장애 시 데이터를 로컬 디스크에 임시 저장 후 복구 시 재전송

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
| `otlp/tempo` | Tempo | 분산 추적 저장 |
| `otlphttp/loki` | Loki (OTLP) | 로그 저장 (v0.144.0+) |

#### 2.3.1 prometheusremotewrite 버퍼링 전략 ✅ 구현 완료

**참고**: `prometheusremotewrite`는 `sending_queue` 미지원. `remote_write_queue`만 지원하며, `file_storage` 연동 불가.

| 설정 | 지원 여부 | 비고 |
|------|----------|------|
| `sending_queue.storage` | ❌ 미지원 | Tempo/Loki에서만 사용 |
| `remote_write_queue` | ✅ 지원 | 메모리 기반 큐 |
| **`wal` (Write-Ahead Log)** | ✅ 지원 | **디스크 기반 버퍼링** |

**해결책: WAL(Write-Ahead Log) 사용**

```yaml
prometheusremotewrite:
  endpoint: http://mimir:9009/api/v1/push
  remote_write_queue:
    enabled: true
    num_consumers: 10
    queue_size: 5000
  # WAL - 디스크 기반 버퍼링 (Mimir 장애 시 데이터 보존)
  wal:
    directory: /var/lib/otelcol/wal
    buffer_size: 300
    truncate_frequency: 60s
```

**통합 테스트**: `tests/integration/otel/test_redaction_processor.py::TestPrometheusRemoteWriteQueue`

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
processors: [memory_limiter, batch, resource, attributes/logs, redaction]
    │
    ▼
exporters: [otlphttp/loki]  # v0.144.0+: Loki OTLP 엔드포인트 사용
```

**Logs 데이터 흐름:**
1. Django 로깅 → OTEL Log Exporter
2. OTLP로 Collector에 전송
3. 속성 처리 (trace_id 추출 등) 및 민감정보 마스킹
4. Loki OTLP 엔드포인트(`/otlp`)로 전송

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

- [x] **1.1** Collector 설정 디렉토리 생성
  - `docker/otel-collector/` 디렉토리
- [x] **1.2** Collector 기본 설정 파일 작성
  - `docker/otel-collector/otel-collector-config.yml`
- [x] **1.3** docker-compose.yml에 otel-collector 서비스 추가
- [x] **1.4** Collector 헬스체크 설정
- [x] **1.5** Collector 시작 검증

#### 1.4 구현 노트: 커스텀 Alpine 기반 이미지

**배경**: 공식 `otel/opentelemetry-collector-contrib` 이미지는 scratch 기반으로 셸/wget/curl이 없음.
Docker Compose 환경에서 헬스체크는 컨테이너 내부에서 명령을 실행해야 하므로 커스텀 이미지가 필요.

**구현** (`docker/otel-collector/Dockerfile`):
```dockerfile
ARG OTEL_VERSION=0.96.0
FROM otel/opentelemetry-collector-contrib:${OTEL_VERSION} AS collector
FROM alpine:3.19
RUN apk add --no-cache ca-certificates wget
COPY --from=collector /otelcol-contrib /otelcol-contrib
RUN adduser -D -u 10001 otel
USER otel
ENTRYPOINT ["/otelcol-contrib"]
```

**wget 선택 이유**:
- Alpine/busybox에 기본 포함 (curl은 별도 설치 필요)
- Grafana 스택(Tempo, Mimir, Loki)이 동일하게 wget 사용
- 헬스체크 명령: `wget --spider -q http://localhost:13133/`

**참고**: Kubernetes 환경에서는 kubelet이 외부에서 HTTP 프로브를 수행하므로 원본 scratch 이미지 사용 가능.

### 5.2 Phase 2: Traces 파이프라인 구성

- [x] **2.1** otlp receiver 설정
  - gRPC: 4317 포트
  - HTTP: 4318 포트
- [x] **2.2** Tempo 서비스 추가
  - `docker/tempo/tempo.yml` 설정
  - docker-compose.yml에 tempo 서비스
- [x] **2.3** otlp exporter (Tempo) 설정
- [x] **2.4** traces 파이프라인 설정
- [x] **2.5** Trace 수신 검증
  - 통합 테스트: `tests/integration/otel/test_otel_collector_integration.py`

### 5.3 Phase 3: Metrics 파이프라인 구성

- [x] **3.1** prometheus receiver 설정
  - 기존 scrape_configs 마이그레이션
- [x] **3.2** Mimir 서비스 추가 (선택)
  - 또는 기존 Prometheus 유지
- [x] **3.3** prometheusremotewrite exporter 설정
- [x] **3.4** metrics 파이프라인 설정
- [x] **3.5** 기존 메트릭 수집 검증
  - 통합 테스트: `tests/integration/otel/test_metrics_pipeline_integration.py`

### 5.4 Phase 4: Logs 파이프라인 구성

- [x] **4.1** Loki 서비스 추가
  - `docker/loki/loki.yml` 설정
  - docker-compose.yml에 loki 서비스
- [x] **4.2** loki exporter 설정
- [x] **4.3** logs 파이프라인 설정
- [x] **4.4** 속성 프로세서 설정
  - trace_id 추출
  - 민감 정보 마스킹
- [x] **4.5** 로그 수신 검증
  - 통합 테스트: `tests/integration/otel/test_logs_pipeline_integration.py`

### 5.5 Phase 5: 통합 검증

- [x] **5.1** 전체 파이프라인 E2E 테스트
  - 통합 테스트: `tests/integration/otel/test_e2e_pipeline_integration.py`
  - 실행: `docker-compose -f docker-compose.test.yml run --rm test-otel-e2e`
- [x] **5.2** Grafana 데이터소스 추가
  - Tempo datasource: `docker/grafana/provisioning/datasources/datasource.yml`
  - Loki datasource: trace_id 기반 Trace 연동 설정
  - Mimir datasource: PromQL 쿼리 지원
- [x] **5.3** 상관관계 검증
  - 통합 테스트: `tests/integration/otel/test_telemetry_correlation_integration.py`
  - Trace → Logs 연동 (trace_id 기반)
  - Metrics → Traces 연동 (service.name 기반)
  - 실행: `docker-compose -f docker-compose.test.yml run --rm test-otel-correlation`
- [x] **5.4** 성능 테스트
  - 통합 테스트: `tests/integration/otel/test_collector_performance_integration.py`
  - 부하 시 데이터 손실 없음 (100개 Trace/Metric/Log 연속 전송)
  - 메모리 사용량 제한 동작 검증
  - 동시 다발적 요청 처리 안정성
  - 실행: `docker-compose -f docker-compose.test.yml run --rm test-otel-performance`

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

### 7.1.1 Tiered Storage 전략

**근거**: `cascade_retention.py`의 Hot/Warm/Cold 계층별 보관 정책

OTEL 데이터에도 동일한 Tiered Storage 전략 적용:

| 계층 | 보존 기간 | 저장소 | 용도 | 코드 근거 |
|------|---------|--------|------|----------|
| **Hot** | 7일 | Mimir/Tempo (SSD) | 실시간 조회 | `hot_retention_days=7` |
| **Warm** | 30일 | Loki (HDD) | 복잡한 쿼리 | `warm_retention_days=90` |
| **Cold** | 1년 | S3/GCS Archive | 법적 요구사항 | `cold_retention_days=365` |

**비용 최적화 효과:**
- Hot (7일): 빠른 SSD 저장, 높은 비용
- Warm (30일): 일반 HDD, 중간 비용
- Cold (1년): 객체 스토리지, 낮은 비용

**데이터 유형별 보존:**

| 데이터 | Hot | Warm | Cold |
|--------|-----|------|------|
| Traces (Tempo) | 7일 | - | - |
| Logs (Loki) | 7일 | 30일 | 1년 |
| Metrics (Mimir) | 7일 | 30일 | 1년 |

### 7.2 프로덕션 환경

| 구성요소 | 설정 |
|---------|------|
| Collector | Agent + Gateway 2단계 |
| Tempo | 객체 스토리지 (S3/GCS) |
| Loki | 객체 스토리지 (S3/GCS) |
| Mimir | 객체 스토리지 (S3/GCS) |
| 보존 기간 | 30일+ |

### 7.2.1 Agent-Gateway 2단계 아키텍처

각 파드가 직접 중앙 Mimir/Tempo로 쏘지 않고 로컬 사이드카로 쏠야 애플리케이션 Latency 영향 최소화:

```
┌───────────────────────────────────────────┐
│              Application Pod                │
│  ┌───────────┐  ┌──────────────────┐  │
│  │  Django   │  │ OTEL Collector │  │
│  │  (App)    │─▶│ (Agent/Sidecar)│  │
│  └───────────┘  └────────┬─────────┘  │
└───────────────────────┬───────────────────┘
                       │ (localhost)
                       ▼
┌───────────────────────────────────────────┐
│           OTEL Collector (Gateway)           │
│           - 복제본 2개 이상                   │
│           - 중앙 배치                        │
└───────────────────┬───────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│  Mimir  │ │  Tempo  │ │  Loki   │
└─────────┘ └─────────┘ └─────────┘
```

| 단계 | 역할 | 리소스 | 위치 |
|------|------|---------|------|
| Agent (Sidecar) | 로컬 수집, 배치 | CPU 0.1, Mem 64MB | 각 Pod 내부 |
| Gateway (Cluster) | 중앙 집계, 라우팅 | CPU 0.5, Mem 512MB | 중앙 배치 |

**장점:**
- 애플리케이션 → Sidecar: localhost 통신으로 Latency 최소
- Sidecar → Gateway: 배치 처리로 네트워크 효율화
- Gateway 장애 시 Agent의 file_storage로 로컬 버퍼링

### 7.2.2 gRPC 인증 및 보안

관측성 데이터가 외부로 유출되거나 오염되는 것을 방지:

| 설정 | 값 | 목적 |
|------|---|------|
| `headers.Authorization` | Bearer {API_KEY} | API Key 인증 |
| `tls.insecure` | false (prod) | TLS 활성화 |
| `tls.ca_file` | /etc/ssl/certs/ca.crt | CA 인증서 |

**Gateway 설정 예시:**
```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        auth:
          authenticator: headers_auth

extensions:
  headers_auth:
    headers:
      - header: Authorization
        action: validate
        regex: "Bearer [a-zA-Z0-9]+"
```

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
