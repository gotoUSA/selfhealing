# 159. Grafana 스택 통합 계획 (Loki/Tempo/Mimir)

> **문서 목적**: 기존 Grafana + Prometheus 구성을 Loki/Tempo/Mimir 스택으로 확장하고 통합하는 계획을 제시합니다.

---

## 1. 현재 Grafana 구성 분석

### 1.1 Datasource 현황

현재 `docker/grafana/provisioning/datasources/datasource.yml`에 정의된 데이터소스:

| Datasource | Type | URL | 상태 |
|------------|------|-----|------|
| Prometheus | prometheus | http://prometheus:9090 | 기본값, 유일한 소스 |

### 1.2 Dashboard 현황

현재 `docker/grafana/provisioning/dashboards/`에 정의된 대시보드:

| 대시보드 | 용도 | 데이터소스 |
|---------|------|-----------|
| self_healing_overview.json | Self-Healing 시스템 개요 | Prometheus |
| dlq_monitoring.json | DLQ 모니터링 | Prometheus |
| cascade_event_audit.json | Cascade Event 감사 추적 | Prometheus |
| error_budget.json | Error Budget 추적 | Prometheus |
| error_budget_gate.json | Error Budget Gate | Prometheus |

### 1.3 메트릭 쿼리 패턴

현재 대시보드에서 사용되는 PromQL 패턴 (prometheus.py 메트릭 기반):

| 메트릭 카테고리 | 쿼리 예시 |
|----------------|----------|
| DLQ | `selfhealing_dlq_pending_count{domain="payment"}` |
| Circuit Breaker | `selfhealing_circuit_breaker_state{service_name="toss_payments"}` |
| Retry | `rate(selfhealing_retry_outcomes_total[5m])` |
| Recovery | `histogram_quantile(0.95, selfhealing_recovery_time_seconds)` |
| RED | `rate(selfhealing_http_requests_total[1m])` |

---

## 2. 목표 Grafana 스택 구성

### 2.1 새로운 Datasource 구성

| Datasource | Type | URL | 용도 |
|------------|------|-----|------|
| Prometheus (기존) | prometheus | http://prometheus:9090 | 기존 메트릭 (전환 기간) |
| Mimir | prometheus | http://mimir:9009/prometheus | 장기 메트릭 저장 |
| Tempo | tempo | http://tempo:3200 | 분산 추적 |
| Loki | loki | http://loki:3100 | 로그 저장 |

### 2.2 Datasource 역할

```
┌───────────────────────────────────────────────────────────────────┐
│                           Grafana                                  │
│                                                                    │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐      │
│  │    Metrics     │  │    Traces      │  │     Logs       │      │
│  │    Panels      │  │    Panels      │  │    Panels      │      │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘      │
│          │                   │                   │                │
│          ▼                   ▼                   ▼                │
│  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐       │
│  │ Mimir/Prom    │   │    Tempo      │   │     Loki      │       │
│  │ Datasource    │   │  Datasource   │   │  Datasource   │       │
│  └───────────────┘   └───────────────┘   └───────────────┘       │
└───────────────────────────────────────────────────────────────────┘
                │                   │                   │
                ▼                   ▼                   ▼
         ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
         │    Mimir    │    │    Tempo    │    │    Loki     │
         │  (Metrics)  │    │  (Traces)   │    │   (Logs)    │
         └─────────────┘    └─────────────┘    └─────────────┘
```

---

## 3. 상관관계 (Correlations) 설정

### 3.1 Grafana Correlations 기능

Grafana의 Correlations 기능을 통해 세 가지 텔레메트리 데이터를 연결합니다.

| 출발점 | 도착점 | 연결 방법 |
|--------|--------|----------|
| Metrics → Traces | trace_id로 연결 | Exemplar 또는 링크 |
| Traces → Logs | trace_id로 필터링 | 자동 쿼리 |
| Logs → Traces | trace_id 클릭 | 링크 |

### 3.2 trace_id 기반 연결

현재 시스템에서 trace_id가 전파되는 경로:

| 구성요소 | trace_id 출처 | 코드 위치 |
|---------|--------------|----------|
| Django 요청 | `get_trace_id()` | `selfhealing/audit/trace.py` |
| Celery Task | `generate_celery_trace_id()` | `selfhealing/audit/trace.py` |
| Audit 로그 | `AuditLogger.log()` | `selfhealing/audit/logger.py` |
| CB 상태 변화 | `TriggeringRequestInfo.trace_id` | `selfhealing/services/circuit_breaker/tracing.py` |
| Cascade Event | `ExternalTraceContext.trace_id` | `selfhealing/audit/cascade_event.py` |

### 3.3 Exemplar 지원

Prometheus/Mimir Exemplar를 통해 메트릭에서 직접 Trace로 이동:

| 메트릭 | Exemplar 연결 | 활용 |
|--------|--------------|------|
| `http_request_duration_seconds` | trace_id | 느린 요청 추적 |
| `circuit_breaker_failures_total` | trace_id | 실패 원인 추적 |
| `retry_outcomes_total` | trace_id | 재시도 흐름 추적 |

---

## 4. 대시보드 확장

### 4.1 기존 대시보드 유지

기존 Prometheus 기반 대시보드는 수정 없이 유지:
- Mimir는 PromQL 호환
- Datasource URL만 변경하면 동작

### 4.2 새로운 대시보드 추가

| 대시보드 | 데이터소스 | 용도 |
|---------|-----------|------|
| Request Tracing | Tempo | 요청 전체 흐름 시각화 |
| Log Explorer | Loki | 로그 검색 및 분석 |
| Unified View | Tempo + Loki + Mimir | 상관관계 통합 뷰 |
| CB Trace Analysis | Tempo | CB 상태 변화 추적 |

### 4.3 Request Tracing 대시보드 설계

**패널 구성:**

| 패널 | 유형 | 데이터소스 | 쿼리 |
|------|------|-----------|------|
| Trace List | Table | Tempo | 최근 Trace 목록 |
| Service Graph | Node Graph | Tempo | 서비스 간 호출 관계 |
| Trace Duration | Time Series | Tempo | 응답 시간 추이 |
| Error Traces | Table | Tempo | 에러 발생 Trace |

### 4.4 Log Explorer 대시보드 설계

**패널 구성:**

| 패널 | 유형 | 데이터소스 | 쿼리 |
|------|------|-----------|------|
| Log Volume | Time Series | Loki | 로그 발생량 |
| Log Stream | Logs | Loki | 실시간 로그 |
| Error Logs | Table | Loki | level="ERROR" 필터 |
| Audit Logs | Table | Loki | source="audit" 필터 |

### 4.5 Unified View 대시보드 설계

**패널 구성:**

| 패널 | 유형 | 데이터소스 | 연동 |
|------|------|-----------|------|
| Request Rate | Time Series | Mimir | - |
| Error Rate | Time Series | Mimir | → Tempo 링크 |
| Latency P95 | Time Series | Mimir | → Tempo Exemplar |
| Trace Viewer | Trace | Tempo | → Loki 필터 |
| Related Logs | Logs | Loki | trace_id 자동 필터 |

---

## 5. 구현 순서 체크리스트

### 5.1 Phase 1: Loki 통합

- [ ] **1.1** Loki 서비스 설정
  - `docker/loki/loki.yml` 생성
  - docker-compose.yml에 loki 서비스 추가
- [ ] **1.2** Grafana Loki Datasource 추가
  - `docker/grafana/provisioning/datasources/datasource.yml` 수정
  - Loki datasource 정의 추가
- [ ] **1.3** Loki 연결 검증
  - Grafana Explore에서 Loki 쿼리 테스트
- [ ] **1.4** Log Explorer 대시보드 생성
  - `docker/grafana/provisioning/dashboards/log_explorer.json`
- [ ] **1.5** 기존 로그 필터 검증
  - level 필터
  - source 필터
  - trace_id 필터

### 5.2 Phase 2: Tempo 통합

- [ ] **2.1** Tempo 서비스 설정
  - `docker/tempo/tempo.yml` 생성
  - docker-compose.yml에 tempo 서비스 추가
- [ ] **2.2** Grafana Tempo Datasource 추가
  - `docker/grafana/provisioning/datasources/datasource.yml` 수정
- [ ] **2.3** Tempo 연결 검증
  - Grafana Explore에서 Trace 검색 테스트
- [ ] **2.4** Request Tracing 대시보드 생성
  - `docker/grafana/provisioning/dashboards/request_tracing.json`
- [ ] **2.5** Service Graph 패널 설정

### 5.3 Phase 3: Mimir 통합 (선택)

- [ ] **3.1** Mimir 서비스 설정
  - `docker/mimir/mimir.yml` 생성
  - docker-compose.yml에 mimir 서비스 추가
- [ ] **3.2** Grafana Mimir Datasource 추가
- [ ] **3.3** 기존 대시보드 Datasource 전환
  - Prometheus → Mimir
- [ ] **3.4** PromQL 호환성 검증
- [ ] **3.5** Alerting 규칙 마이그레이션

### 5.4 Phase 4: 상관관계 설정

- [ ] **4.1** Traces → Logs 연동 설정
  - Tempo에서 Loki로 링크
  - trace_id 기반 자동 필터
- [ ] **4.2** Logs → Traces 연동 설정
  - Loki 로그에서 trace_id 클릭 시 Tempo로 이동
- [ ] **4.3** Metrics → Traces Exemplar 설정
  - Mimir/Prometheus Exemplar 활성화
  - Tempo 연결 설정
- [ ] **4.4** Unified View 대시보드 생성
  - `docker/grafana/provisioning/dashboards/unified_view.json`
- [ ] **4.5** 상관관계 E2E 테스트
  - Metric → Trace → Log 전체 경로 검증

### 5.5 Phase 5: 대시보드 마이그레이션

- [ ] **5.1** 기존 대시보드 백업
  - self_healing_overview.json
  - dlq_monitoring.json
  - cascade_event_audit.json
  - error_budget.json
  - error_budget_gate.json
- [ ] **5.2** 대시보드에 Trace 링크 추가
  - CB 상태 변화 패널에 trace_id 링크
  - DLQ 항목에 trace_id 링크
- [ ] **5.3** 대시보드에 Log 패널 추가
  - 관련 로그 표시 패널
- [ ] **5.4** 대시보드 검증
  - 모든 패널 정상 동작
  - 상관관계 링크 동작

---

## 6. Datasource 프로비저닝

### 6.1 현재 datasource.yml 상태 (코드 확인)

**파일 위치**: `docker/grafana/provisioning/datasources/datasource.yml`

**현재 내용:**
```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
    jsonData:
      timeInterval: "15s"
      httpMethod: POST
```

**문제점**: Prometheus만 정의되어 있어 Tempo, Loki 연동 불가

### 6.2 필수 수정: datasource.yml 확장

**추가해야 할 Datasource:**

| Datasource | Type | URL | 필수 여부 |
|------------|------|-----|----------|
| Tempo | tempo | http://tempo:3200 | 필수 (Traces) |
| Loki | loki | http://loki:3100 | 필수 (Logs) |
| Mimir | prometheus | http://mimir:9009/prometheus | 선택 (장기 Metrics) |

### 6.3 Derived Fields 설정 (Loki → Tempo)

Loki 로그에서 trace_id 추출하여 Tempo로 연결:

| 설정 | 값 | 설명 |
|------|---|------|
| Derived field name | trace_id | 필드명 |
| Regex | `trace_id=([a-f0-9]+)` | 추출 패턴 |
| Internal link | Tempo datasource | 연결 대상 |

**코드 근거**: `selfhealing/audit/trace.py`의 `generate_trace_id()` 형식:
- 형식: `req-{cluster_prefix}-{uuid8}` 또는 `req-{uuid8}`
- 예: `req-seop-a1b2c3d4`

**Loki Derived Field regex 패턴:**
```
req-[a-z]*-?([a-f0-9]{8})
```

### 6.4 TraceID 연결 설정 (Tempo → Loki)

Tempo에서 Loki 로그 검색 연결:

| 설정 | 값 | 설명 |
|------|---|------|
| Trace to logs | Loki datasource | 연결 대상 |
| Filter by trace ID | 활성화 | trace_id 자동 필터 |

### 6.5 기존 대시보드 호환성 (수정 불필요)

**코드 확인 결과**: 기존 대시보드들은 변수 기반 Datasource 사용

**확인된 패턴** (`self_healing_overview.json` 등):
```json
"datasource": { "type": "prometheus", "uid": "${datasource}" }
```

**장점:**
- Mimir 전환 시 PromQL 쿼리 수정 불필요
- Datasource 선택 변수로 동적 전환 가능
- 기존 대시보드 5개 모두 동일 패턴 사용

**기존 대시보드 목록** (`docker/grafana/provisioning/dashboards/`):
1. self_healing_overview.json
2. dlq_monitoring.json
3. cascade_event_audit.json
4. error_budget.json
5. error_budget_gate.json

---

## 7. Grafana 그 다음은?

### 7.1 Grafana의 역할 (OTEL 도입 후)

| 이전 | 이후 | 변화 |
|------|------|------|
| Prometheus 시각화 | 통합 관측성 플랫폼 | 역할 확장 |
| 메트릭만 | Metrics + Traces + Logs | 데이터 유형 확장 |
| 단일 Datasource | 4개 Datasource | 소스 다양화 |
| PromQL만 | PromQL + TraceQL + LogQL | 쿼리 언어 확장 |

### 7.2 Grafana 유지 이유

| 이유 | 설명 |
|------|------|
| 기존 대시보드 | 이미 구축된 대시보드 재활용 |
| PromQL 호환 | Mimir가 PromQL 완전 호환 |
| 통합 UI | Metrics/Traces/Logs 단일 UI |
| 상관관계 | 세 데이터 유형 간 자동 연결 |
| 팀 익숙도 | 이미 사용 중인 도구 |

### 7.3 AI 연동 관점에서의 Grafana

Grafana는 AI 연동 시에도 핵심 역할:

| 역할 | 설명 |
|------|------|
| 시각화 레이어 | AI 분석 결과 시각화 |
| 알림 허브 | AI 이상 탐지 알림 |
| 데이터 접근 | AI 모델의 데이터 쿼리 게이트웨이 |
| 대시보드 API | AI 기반 동적 대시보드 생성 |

---

## 8. 검증 기준

### 8.1 기능 검증

| 항목 | 검증 방법 | 기대 결과 |
|------|----------|----------|
| Loki 연결 | Explore에서 쿼리 | 로그 표시 |
| Tempo 연결 | Explore에서 검색 | Trace 표시 |
| Mimir 연결 | 기존 대시보드 | 메트릭 표시 (기존과 동일) |
| Traces → Logs | Trace에서 로그 버튼 클릭 | 관련 로그 표시 |
| Logs → Traces | 로그의 trace_id 클릭 | Trace 상세 표시 |
| Metrics → Traces | Exemplar 클릭 | Trace 상세 표시 |

### 8.2 성능 검증

| 항목 | 검증 방법 | 기대 결과 |
|------|----------|----------|
| 대시보드 로딩 | 페이지 로드 시간 | < 3초 |
| Trace 검색 | 검색 응답 시간 | < 5초 |
| 로그 검색 | 검색 응답 시간 | < 5초 |
| 상관관계 전환 | 링크 클릭 응답 | < 2초 |

---

## 9. 관련 문서

- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md): 전체 아키텍처 개요
- [157_OTEL_SDK_INTEGRATION.md](157_OTEL_SDK_INTEGRATION.md): SDK 도입 계획
- [158_OTEL_COLLECTOR_CONFIGURATION.md](158_OTEL_COLLECTOR_CONFIGURATION.md): Collector 구성
- [160_OTEL_MIGRATION_CHECKLIST.md](160_OTEL_MIGRATION_CHECKLIST.md): 마이그레이션 체크리스트
