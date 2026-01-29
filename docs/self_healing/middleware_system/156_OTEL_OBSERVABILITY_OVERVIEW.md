# 156. OpenTelemetry 기반 통합 Observability 아키텍처 개요

> **문서 목적**: 현재 시스템의 관측성 구현 현황을 분석하고, OTEL 기반 통합 아키텍처로의 전환 로드맵을 제시합니다.

---

## 1. 현재 시스템 관측성 현황 분석

### 1.1 메트릭 수집 (Metrics)

현재 시스템은 `prometheus_client` 라이브러리를 직접 사용하여 메트릭을 수집합니다.

| 항목 | 현재 상태 | 코드 위치 |
|------|----------|----------|
| 메트릭 라이브러리 | prometheus_client 직접 사용 | `selfhealing/metrics/prometheus.py` |
| 수집 방식 | Django 앱에서 /metrics 엔드포인트 노출 | `django-prometheus` 미들웨어 |
| 저장소 | Prometheus 단일 인스턴스 | `docker/prometheus/prometheus.yml` |
| 시각화 | Grafana (Prometheus datasource) | `docker/grafana/provisioning/datasources/datasource.yml` |

**현재 정의된 메트릭 카테고리:**
- DLQ 메트릭: items_total, pending_count, by_status
- Retry 메트릭: attempts_distribution, outcomes_total, success_rate, delay_seconds
- Recovery 메트릭: recovery_time_seconds, sla_breach_total, human_review_queue_time
- Circuit Breaker 메트릭: state, failures_total, trips_total, transitions_total, open_duration
- Replay 메트릭: attempts_total, outcomes_total, duration_seconds
- Security 메트릭: incidents_total
- RED 메트릭: http_requests_total, http_request_duration_seconds, http_request_errors_total

### 1.2 분산 추적 (Tracing)

현재 시스템은 OpenTelemetry SDK 없이 **자체 구현한 트레이싱** 시스템을 사용합니다.

| 항목 | 현재 상태 | 코드 위치 |
|------|----------|----------|
| Trace ID 생성 | 자체 generate_trace_id() 함수 | `selfhealing/audit/trace.py` |
| 컨텍스트 저장 | contextvars + thread-local 하이브리드 | `selfhealing/audit/trace.py` |
| 헤더 전파 | 수동 헤더 주입 (OTel 의존 없음) | `selfhealing/services/http_client.py` |
| W3C 호환 | ExternalTraceContext 데이터클래스 | `selfhealing/audit/cascade_event.py` |
| Celery 연동 | CELERY_{task_id} 형식 trace_id | `selfhealing/audit/trace.py` |

**지원하는 트레이싱 헤더:**
- X-Request-ID
- X-Trace-ID
- X-Correlation-ID
- traceparent (W3C Trace Context)
- X-Amzn-Trace-Id (AWS X-Ray)

### 1.3 로깅 (Logging)

| 항목 | 현재 상태 | 코드 위치 |
|------|----------|----------|
| 로그 형식 | 구조화된 JSON | `selfhealing/settings/logging_config.py` (structured_json=True) |
| 컴포넌트별 레벨 | DLQ, CB, Replay, SLA 등 개별 설정 | `selfhealing/settings/logging_config.py` |
| Audit 로깅 | 자체 AuditLogger 구현 | `selfhealing/audit/logger.py` |
| 이벤트 버퍼 | 요청별 버퍼링 후 일괄 기록 | `selfhealing/audit/event_buffer.py` |

### 1.4 Grafana 대시보드

| 대시보드 | 용도 | 위치 |
|---------|------|------|
| self_healing_overview.json | Self-Healing 시스템 개요 | `docker/grafana/provisioning/dashboards/` |
| dlq_monitoring.json | DLQ 모니터링 | `docker/grafana/provisioning/dashboards/` |
| cascade_event_audit.json | Cascade Event 감사 | `docker/grafana/provisioning/dashboards/` |
| error_budget.json | Error Budget 추적 | `docker/grafana/provisioning/dashboards/` |
| error_budget_gate.json | Error Budget Gate | `docker/grafana/provisioning/dashboards/` |
| self_healing_dashboard.json | Self-Healing 상세 | `scripts/grafana/dashboards/` |

---

## 2. 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Self-Healing System                                │
│  ┌──────────────┐   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Django     │   │   Celery     │  │   Circuit    │  │   Audit      │        │
│  │   Web        │   │   Worker     │  │   Breaker    │  │   Logger     │        │
│  └──────┬───────┘   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         │                  │                  │                  │              │
│         └──────────────────┴──────────────────┴──────────────────┘              │
│                                      │                                          │
│                          ┌───────────▼───────────┐                              │
│                          │     OTEL SDK          │                              │
│                          │  (Traces/Metrics/Logs)│                              │
│                          └───────────┬───────────┘                              │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │ OTLP (gRPC/HTTP)
                                       ▼
                          ┌───────────────────────────┐
                          │     OTEL Collector        │
                          │  ┌─────────────────────┐  │
                          │  │ Receivers           │  │
                          │  │ - otlp              │  │
                          │  │ - prometheus        │  │
                          │  └─────────────────────┘  │
                          │  ┌─────────────────────┐  │
                          │  │ Processors          │  │
                          │  │ - batch             │  │
                          │  │ - memory_limiter    │  │
                          │  │ - resource          │  │
                          │  └─────────────────────┘  │
                          │  ┌─────────────────────┐  │
                          │  │ Exporters           │  │
                          │  │ - prometheusremote  │  │
                          │  │ - loki              │  │
                          │  │ - tempo (otlp)      │  │
                          │  └─────────────────────┘  │
                          └───────────┬───────────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               │                      │                      │
               ▼                      ▼                      ▼
        ┌─────────────┐        ┌─────────────┐        ┌─────────────┐
        │    Mimir    │        │    Tempo    │        │    Loki     │
        │  (Metrics)  │        │  (Traces)   │        │   (Logs)    │
        └──────┬──────┘        └──────┬──────┘        └──────┬──────┘
               │                      │                      │
               └──────────────────────┼──────────────────────┘
                                      │
                                      ▼
                          ┌───────────────────────────┐
                          │        Grafana            │
                          │  ┌─────────────────────┐  │
                          │  │ Datasources:        │  │
                          │  │ - Mimir (Metrics)   │  │
                          │  │ - Tempo (Traces)    │  │
                          │  │ - Loki (Logs)       │  │
                          │  └─────────────────────┘  │
                          │  ┌─────────────────────┐  │
                          │  │ Correlations:       │  │
                          │  │ - Trace → Logs      │  │
                          │  │ - Metrics → Traces  │  │
                          │  │ - Logs → Traces     │  │
                          │  └─────────────────────┘  │
                          └───────────────────────────┘
```

---

## 3. 기대 효과

### 3.1 현재 vs 목표 비교

| 영역 | 현재 | 목표 | 개선점 |
|------|------|------|--------|
| **Trace 전파** | 수동 헤더 주입 | OTEL SDK 자동 전파 | 코드 간소화, 누락 방지 |
| **Metrics 저장** | Prometheus 단일 | Mimir (분산/장기 저장) | 고가용성, 무제한 보존 |
| **Logs 수집** | 파일/콘솔 | Loki (중앙 집중) | 검색 가능, Trace 연동 |
| **Trace 저장** | 없음 (trace_id만) | Tempo (전체 Span) | 전체 호출 그래프 시각화 |
| **상관관계** | 수동 trace_id 검색 | Grafana 자동 연동 | 1-click 전환 |

### 3.2 AI 연동 준비도

OTEL 기반 아키텍처 도입 시 AI/ML 파이프라인에 유리한 점:

| 데이터 유형 | 현재 수집 중 | AI 활용 가능성 |
|------------|-------------|---------------|
| Circuit Breaker 상태 전이 | 상태/타임스탬프 | 장애 패턴 예측 |
| DLQ 실패 패턴 | domain/failure_type 라벨 | 실패 원인 분류 |
| Cascade Event 체인 | 인과관계 추적 | 근본 원인 자동 분석 |
| RTT/Latency 메트릭 | SLA 위반 감지 | 성능 이상 예측 |
| Recovery 시간 | 해결 소요 시간 | 복구 시간 예측 |

### 3.3 RPS 및 처리 용량 목표

**근거**: 현재 시스템 Rate Limiting 설정 (L2 Redis 100 req/min, L1 Fallback 10 req/min)

OTEL Collector 및 백엔드 시스템은 다음 목표 처리량을 지원해야 함:

| 항목 | 목표 용량 | 산정 근거 |
|------|----------|----------|
| Span 처리 | 1000 spans/s | 피크 타임 RPS 예상치 |
| 메트릭 수집 | 50 metrics/s | 현재 정의된 메트릭 수 기반 |
| 로그 수집 | 500 lines/s | 서비스별 로그 생성량 |

### 3.4 멀티 리전 고려사항

**근거**: `cascade_event_archive.py`의 `namespace` 필드, `trace.py`의 `cluster_prefix`

현재 코드밤이스가 이미 리전 식별을 지원하므로, OTEL 도입 시 다음을 고려:

| 항목 | 현재 | OTEL 연동 |
|------|------|------------|
| trace_id 패턴 | `req-{cluster_prefix}-*` | Resource attribute로 `deployment.region` 추가 |
| namespace | "seoul, global" 형식 | OTEL Resource `service.namespace` 매핑 |
| 저장소 | 리전별 분리 | Collector per-region, Grafana Global View |

---

## 4. 문서 구성

이 OTEL 통합 프로젝트는 다음 문서들로 구성됩니다:

| 문서 번호 | 제목 | 내용 |
|----------|------|------|
| 156 | 개요 (본 문서) | 현황 분석, 목표 아키텍처, 기대 효과 |
| 157 | OTEL SDK 도입 | SDK 설치, 자동 계측, 수동 Span 생성 |
| 158 | OTEL Collector 구성 | Collector 배포, 파이프라인 설정 |
| 159 | Grafana 스택 통합 | Loki/Tempo/Mimir 연동, 대시보드 마이그레이션 |
| 160 | 마이그레이션 체크리스트 | 단계별 구현 순서, 검증 기준 |

---

## 5. 선행 조건

### 5.1 코드 기반 호환성 확인 완료

| 확인 항목 | 상태 | 근거 |
|----------|------|------|
| W3C Trace Context 파싱 | ✅ 구현됨 | `trace.py`: traceparent 헤더 파싱 로직 |
| ExternalTraceContext 구조 | ✅ 설계됨 | `cascade_event.py`: trace_id, span_id, trace_flags 필드 |
| 구조화된 JSON 로깅 | ✅ 활성화됨 | `logging_config.py`: structured_json=True |
| Prometheus 메트릭 정의 | ✅ 완료 | `prometheus.py`: Counter, Gauge, Histogram 정의 |
| Celery trace_id 표준화 | ✅ 구현됨 | `trace.py`: generate_celery_trace_id() |

### 5.2 인프라 요구사항

| 구성요소 | 용도 | 비고 |
|---------|------|------|
| OTEL Collector | 텔레메트리 수집/분배 | 필수 |
| Loki | 로그 저장/검색 | 필수 |
| Tempo | 분산 추적 저장 | 필수 |
| Mimir | 메트릭 장기 저장 | 선택 (기존 Prometheus 유지 가능) |

---

## 6. 관련 문서

- [07_CONTROL_API.md](../07_CONTROL_API.md): 메트릭 API 엔드포인트
- [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md): Cascade Event 감사 추적
- [28_CELERY_TRACE_ID_STANDARDIZATION.md](28_CELERY_TRACE_ID_STANDARDIZATION.md): Celery trace_id 표준화
- [SYSTEM_ARCHITECTURE.md](../../SYSTEM_ARCHITECTURE.md): 시스템 전체 아키텍처
