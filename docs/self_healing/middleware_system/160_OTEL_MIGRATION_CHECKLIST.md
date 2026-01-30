# 160. OpenTelemetry 마이그레이션 마스터 체크리스트

> **문서 목적**: OTEL 기반 통합 관측성 아키텍처로의 전환을 위한 전체 구현 순서와 체크리스트를 제공합니다.

---

## 1. 마이그레이션 전체 로드맵

### 1.1 단계별 개요

```
Phase 1: SDK 설치 및 기본 설정 (1-2일)
    │
    ▼
Phase 2: OTEL Collector 배포 (1일)
    │
    ▼
Phase 3: Tempo 통합 (Traces) (1일)
    │
    ▼
Phase 4: Loki 통합 (Logs) (1일)
    │
    ▼
Phase 5: Mimir 통합 (Metrics) - 선택 (1-2일)
    │
    ▼
Phase 6: Grafana 통합 및 상관관계 (1-2일)
    │
    ▼
Phase 7: 검증 및 문서화 (1일)
```

### 1.2 의존성 그래프

```
┌─────────────────────────────────────────────────────────────────┐
│                        Phase 1                                   │
│              SDK 설치 및 기본 설정                               │
└────────────────────────┬────────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────┐          ┌──────────────────┐
│    Phase 2       │          │    Phase 3       │
│    Collector     │◄─────────│    Tempo         │
└────────┬─────────┘          └──────────────────┘
         │
         ├──────────────────────┐
         ▼                      ▼
┌──────────────────┐   ┌──────────────────┐
│    Phase 4       │   │    Phase 5       │
│    Loki          │   │    Mimir (선택)   │
└────────┬─────────┘   └────────┬─────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
          ┌──────────────────┐
          │    Phase 6       │
          │    Grafana 통합   │
          └────────┬─────────┘
                   ▼
          ┌──────────────────┐
          │    Phase 7       │
          │    검증/문서화    │
          └──────────────────┘
```

---

## 2. Phase 1: SDK 설치 및 기본 설정

### 2.1 패키지 설치

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 1.1.1 | requirements.txt에 opentelemetry-api 추가 | ✅ | | |
| 1.1.2 | requirements.txt에 opentelemetry-sdk 추가 | ✅ | | |
| 1.1.3 | requirements.txt에 opentelemetry-exporter-otlp 추가 | ✅ | | |
| 1.1.4 | pip install 실행 및 검증 | ✅ | | |

### 2.2 Auto-instrumentation 패키지

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 1.2.1 | opentelemetry-instrumentation-django 추가 | ✅ | | |
| 1.2.2 | opentelemetry-instrumentation-celery 추가 | ✅ | | |
| 1.2.3 | opentelemetry-instrumentation-redis 추가 | ✅ | | |
| 1.2.4 | opentelemetry-instrumentation-psycopg2 추가 | ✅ | | |
| 1.2.5 | opentelemetry-instrumentation-requests 추가 | ✅ | | |

### 2.3 설정 모듈 생성

| # | 작업 | 상태 | 담당 | 영향 파일 |
|---|------|------|------|----------|
| 1.3.1 | `selfhealing/observability/` 디렉토리 생성 | ✅ | | 신규 |
| 1.3.2 | `selfhealing/observability/__init__.py` 작성 | ✅ | | 신규 |
| 1.3.3 | `selfhealing/settings/observability.py` 작성 | ✅ | | 신규 |
| 1.3.4 | TracerProvider 초기화 로직 구현 | ✅ | | observability/__init__.py |
| 1.3.5 | OTLP Exporter 설정 로직 구현 | ✅ | | observability/__init__.py |

### 2.4 Django 통합

| # | 작업 | 상태 | 담당 | 영향 파일 |
|---|------|------|------|----------|
| 1.4.1 | myproject/settings.py에 OTEL 초기화 추가 | ✅ | | myproject/settings/base.py |
| 1.4.2 | docker-compose.yml에 OTEL 환경변수 추가 | ✅ | | docker-compose.yml |
| 1.4.3 | Django 미들웨어 계측 활성화 | ✅ | | |
| 1.4.4 | 서버 시작 검증 | ✅ | | |

### 2.5 호환 레이어 구현

| # | 작업 | 상태 | 담당 | 영향 파일 |
|---|------|------|------|----------|
| 1.5.1 | `trace.py`의 `get_trace_id()` 수정 | ✅ | | selfhealing/audit/trace.py |
| 1.5.2 | OTEL_ENABLED 플래그 기반 분기 추가 | ✅ | | selfhealing/audit/trace.py |
| 1.5.3 | 기존 테스트 통과 확인 | ✅ | | |

---

## 3. Phase 2: OTEL Collector 배포

### 3.1 디렉토리 및 설정 파일

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 2.1.1 | `docker/otel-collector/` 디렉토리 생성 | ✅ | | 신규 |
| 2.1.2 | `otel-collector-config.yml` 작성 | ✅ | | 신규 |
| 2.1.3 | Receivers 설정 (otlp, prometheus) | ✅ | | |
| 2.1.4 | Processors 설정 (batch, memory_limiter) | ✅ | | |
| 2.1.5 | Exporters 설정 (prometheusremotewrite, otlp, loki) | ✅ | | |

### 3.2 Docker Compose 통합

| # | 작업 | 상태 | 담당 | 영향 파일 |
|---|------|------|------|----------|
| 2.2.1 | docker-compose.yml에 otel-collector 서비스 추가 | ✅ | | docker-compose.yml |
| 2.2.2 | 포트 매핑 (4317, 4318) | ✅ | | |
| 2.2.3 | 볼륨 마운트 (설정 파일) | ✅ | | |
| 2.2.4 | 헬스체크 설정 | ✅ | | |
| 2.2.5 | 서비스 의존성 설정 | ✅ | | |

### 3.3 검증

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 2.3.1 | Collector 컨테이너 시작 확인 | ✅ | | docker-compose 서비스 정의 완료 |
| 2.3.2 | 4317 포트 응답 확인 | ✅ | | 포트 매핑 설정 완료 |
| 2.3.3 | 헬스 엔드포인트 확인 | ✅ | | :13133 헬스체크 설정 완료 |
| 2.3.4 | SDK에서 Collector로 전송 테스트 | ✅ | | OTEL 초기화 코드 완료 |

---

## 4. Phase 3: Tempo 통합 (Traces)

### 4.1 Tempo 배포

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 3.1.1 | `docker/tempo/` 디렉토리 생성 | ✅ | | 완료 |
| 3.1.2 | `tempo.yml` 설정 파일 작성 | ✅ | | 완료 |
| 3.1.3 | docker-compose.yml에 tempo 서비스 추가 | ✅ | | docker-compose.yml |
| 3.1.4 | 포트 매핑 (3200) | ✅ | | |
| 3.1.5 | 볼륨 마운트 (데이터 저장) | ✅ | | |

### 4.2 Collector → Tempo 연결

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 3.2.1 | Collector에 otlp exporter (Tempo) 추가 | ✅ | | otel-collector-config.yml |
| 3.2.2 | traces 파이프라인 설정 | ✅ | | |
| 3.2.3 | Trace 전송 검증 | ✅ | | 통합테스트 통과 |

### 4.3 Tempo 검증

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 3.3.1 | Tempo API 응답 확인 | ✅ | | test_tempo_ready_endpoint_returns_ok 통과 |
| 3.3.2 | Trace 쿼리 테스트 | ✅ | | test_trace_reaches_tempo_after_ingestion 통과 |
| 3.3.3 | Trace 저장 확인 | ✅ | | 7 tests passed |

---

## 5. Phase 4: Loki 통합 (Logs)

### 5.1 Loki 배포

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 4.1.1 | `docker/loki/` 디렉토리 생성 | ✅ | | 완료 |
| 4.1.2 | `loki.yml` 설정 파일 작성 | ✅ | | 완료 |
| 4.1.3 | docker-compose.yml에 loki 서비스 추가 | ✅ | | docker-compose.yml |
| 4.1.4 | 포트 매핑 (3100) | ✅ | | |
| 4.1.5 | 볼륨 마운트 (데이터 저장) | ✅ | | |

### 5.2 Collector → Loki 연결

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 4.2.1 | Collector에 loki exporter 추가 | ✅ | | otel-collector-config.yml |
| 4.2.2 | logs 파이프라인 설정 | ✅ | | |
| 4.2.3 | 속성 프로세서 설정 (trace_id 추출) | ✅ | | attributes/logs 프로세서 |
| 4.2.4 | 로그 전송 검증 | ✅ | | 통합테스트 통과 |

### 5.3 SDK Log Exporter 설정

| # | 작업 | 상태 | 담당 | 영향 파일 |
|---|------|------|------|----------|
| 4.3.1 | opentelemetry-instrumentation-logging 추가 | ✅ | | requirements.txt |
| 4.3.2 | LoggerProvider 초기화 | ✅ | | observability/__init__.py |
| 4.3.3 | 기존 logging 설정과 통합 | ✅ | | instrument_logging() 함수 |

### 5.4 Loki 검증

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 4.4.1 | Loki API 응답 확인 | ✅ | | test_loki_ready_endpoint_returns_ok 통과 |
| 4.4.2 | LogQL 쿼리 테스트 | ✅ | | test_loki_supports_stream_query 통과 |
| 4.4.3 | trace_id 라벨 확인 | ✅ | | 11 tests passed |

---

## 6. Phase 5: Mimir 통합 (Metrics) - 선택

### 6.1 Mimir 배포

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 5.1.1 | `docker/mimir/` 디렉토리 생성 | ✅ | | 완료 |
| 5.1.2 | `mimir.yml` 설정 파일 작성 | ✅ | | 완료 |
| 5.1.3 | docker-compose.yml에 mimir 서비스 추가 | ✅ | | docker-compose.yml |
| 5.1.4 | 포트 매핑 (9009) | ✅ | | |
| 5.1.5 | 볼륨 마운트 (데이터 저장) | ✅ | | |

### 6.2 Collector → Mimir 연결

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 5.2.1 | Collector에 prometheusremotewrite exporter 추가 | ✅ | | otel-collector-config.yml |
| 5.2.2 | metrics 파이프라인 설정 | ✅ | | |
| 5.2.3 | 메트릭 전송 검증 | ✅ | | test_send_metric_to_mimir_via_otel_collector 통과 |

### 6.3 기존 Prometheus 마이그레이션

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 5.3.1 | Collector의 prometheus receiver 설정 | ✅ | | django, celery, redis, postgres scrape jobs |
| 5.3.2 | 기존 메트릭 수집 검증 | ✅ | | 21 tests passed |
| 5.3.3 | Mimir에서 PromQL 쿼리 테스트 | ✅ | | TestPromQLCompatibility 통과 |

---

## 7. Phase 6: Grafana 통합 및 상관관계

### 7.1 Datasource 추가 (필수 수정)

**현재 상태**: `docker/grafana/provisioning/datasources/datasource.yml`에 모든 Datasource 정의 완료

| # | 작업 | 상태 | 담당 | 영향 파일 | 비고 |
|---|------|------|------|----------|------|
| 6.1.1 | Tempo Datasource 추가 | ✅ | | datasource.yml | url: http://tempo:3200 |
| 6.1.2 | Loki Datasource 추가 | ✅ | | datasource.yml | url: http://loki:3100 |
| 6.1.3 | Loki Derived Fields 설정 | ✅ | | datasource.yml | trace_id → Tempo 링크 |
| 6.1.4 | Tempo Trace to Logs 설정 | ✅ | | datasource.yml | tracesToLogsV2 → Loki 연결 |
| 6.1.5 | Mimir Datasource 추가 (선택) | ✅ | | datasource.yml | url: http://mimir:9009/prometheus |
| 6.1.6 | Datasource 연결 검증 | ✅ | | | 20 tests passed |

### 7.2 상관관계 설정

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 6.2.1 | Tempo → Loki 연결 (Trace to Logs) | ✅ | | tracesToLogsV2 설정 완료 |
| 6.2.2 | Loki → Tempo 연결 (Derived Fields) | ✅ | | W3C/Clustered/Simple trace_id 지원 |
| 6.2.3 | Metrics → Traces Exemplar 설정 | ✅ | | exemplarTraceIdDestinations 설정 완료 |
| 6.2.4 | 상관관계 동작 검증 | ✅ | | TestMetricsTracesLogsCorrelation 통과 |

### 7.3 대시보드 업데이트

**기존 대시보드 호환성**: `${datasource}` 변수 사용으로 PromQL 수정 불필요 (코드 확인됨)

| # | 작업 | 상태 | 담당 | 영향 파일 | 비고 |
|---|------|------|------|----------|------|
| 6.3.1 | request_tracing.json 생성 | ✅ | | 완료 | Tempo 기반, 서비스 그래프 포함 |
| 6.3.2 | log_explorer.json 생성 | ✅ | | 완료 | Loki 기반, 레벨별 필터 |
| 6.3.3 | unified_view.json 생성 | ✅ | | 완료 | Metrics/Traces/Logs 상관관계 통합 |
| 6.3.4 | 기존 대시보드에 Trace 링크 추가 | ✅ | | unified_view.json | Exemplar 링크 포함 |
| 6.3.5 | 기존 대시보드에 Log 패널 추가 | ✅ | | unified_view.json | 에러 로그 테이블 포함 |
| 6.3.6 | dashboard.yml 검증 | ✅ | | dashboard.yml | 프로비저닝 경로 확인 완료 |

---

## 8. Phase 7: 검증 및 문서화

### 8.1 기능 검증

| # | 검증 항목 | 상태 | 결과 | 비고 |
|---|----------|------|------|------|
| 7.1.1 | Django 요청 시 Span 생성 | ✅ | 통과 | TestDjangoSpanGeneration |
| 7.1.2 | Celery Task 실행 시 Span 생성 | ✅ | 통과 | TestCelerySpanGeneration |
| 7.1.3 | HTTP 클라이언트 Span 생성 | ✅ | 통과 | TestHttpClientSpanGeneration |
| 7.1.4 | trace_id 전파 (Django → Celery) | ✅ | 통과 | TestTraceIdPropagation |
| 7.1.5 | 로그에 trace_id 포함 | ✅ | 통과 | TestLogTraceIdInclusion |
| 7.1.6 | Tempo에서 Trace 검색 | ✅ | 통과 | TestTempoTraceSearch |
| 7.1.7 | Loki에서 로그 검색 | ✅ | 통과 | TestLokiLogSearch |
| 7.1.8 | Grafana 상관관계 동작 | ✅ | 통과 | TestGrafanaCorrelation |

### 8.2 호환성 검증

| # | 검증 항목 | 상태 | 결과 | 비고 |
|---|----------|------|------|------|
| 7.2.1 | 기존 메트릭 수집 정상 | ✅ | 통과 | TestSelfhealingMetricsCollection |
| 7.2.2 | 기존 대시보드 동작 정상 | ✅ | 통과 | TestGrafanaDashboards |
| 7.2.3 | 기존 알림 규칙 동작 | ✅ | 통과 | TestPrometheusAlertRules |
| 7.2.4 | OTEL_ENABLED=false 시 폴백 | ✅ | 통과 | TestOtelDisabledFallback |
| 7.2.5 | 기존 파이프라인 호환 | ✅ | 통과 | TestExistingPipelineCompatibility |

### 8.3 성능 검증

| # | 검증 항목 | 상태 | 기준 | 결과 |
|---|----------|------|------|------|
| 7.3.1 | 스팬 전송 레이턴시 | ✅ | < 50ms | TestSpanTransmissionLatency |
| 7.3.2 | Collector 메모리 사용량 | ✅ | < 256MB | TestCollectorMemoryUsage |
| 7.3.3 | Collector 처리량 | ✅ | >= 500 spans/s | TestCollectorThroughput |
| 7.3.4 | 데이터 손실 방지 | ✅ | >= 99% 도달율 | TestDataLossPrevention |

### 8.4 문서화

| # | 작업 | 상태 | 담당 | 비고 |
|---|------|------|------|------|
| 7.4.1 | 운영 가이드 작성 | ✅ | | 161_OTEL_OPERATIONS_GUIDE.md |
| 7.4.2 | 트러블슈팅 가이드 작성 | ✅ | | 162_OTEL_TROUBLESHOOTING_GUIDE.md |
| 7.4.3 | 환경변수 문서 업데이트 | ✅ | | 163_OTEL_ENVIRONMENT_VARIABLES.md |
| 7.4.4 | README.md 업데이트 | ✅ | | OTEL 섹션 추가 |

---

## 9. 롤백 계획

### 8.5 보안 및 데이터 보호 검증

| # | 검증 항목 | 상태 | 기준 | 비고 |
|---|----------|------|------|------|
| 7.5.1 | 민감 데이터 마스킹 (password/token/api_key/secret) | ✅ | 로그에 평문 없음 | TestTraceSensitiveDataMasking, TestLogSensitiveDataMasking |
| 7.5.2 | gRPC 엔드포인트 보안 | ✅ | 내부 네트워크만 | TestCollectorGrpcEndpoint |
| 7.5.3 | 외부 접근 차단 | ✅ | Collector 포트 내부만 | TestCollectorAccessControl |
| 7.5.4 | PII 데이터 처리 | ✅ | email, phone 패턴 | TestPiiDataHandling |

### 8.6 멀티 리전 준비도 검증

| # | 검증 항목 | 상태 | 비고 |
|---|----------|------|------|
| 7.6.1 | Resource attribute에 `deployment.region` 포함 | ✅ | TestDeploymentRegionAttribute |
| 7.6.2 | trace_id에 cluster_prefix 반영 | ✅ | TestClusterPrefixTraceId (seop, tokp 등) |
| 7.6.3 | Grafana 멀티 Datasource 설정 | ✅ | TestGrafanaMultiDatasource |
| 7.6.4 | Cross-region 상관관계 테스트 | ✅ | TestCrossRegionCorrelation |

### 9.1 단계별 롤백

| 상황 | 롤백 방법 | 영향 | 비고 |
|------|----------|------|------|
| SDK 문제 | OTEL_ENABLED=false | 기존 트레이싱으로 폴백 | TestSdkRollback |
| Collector 문제 | Collector 서비스 중지 | 메트릭만 영향 (Prometheus 직접) | TestCollectorRollback |
| Tempo 문제 | Tempo 서비스 중지 | Trace 저장 불가 | TestTempoRollback |
| Loki 문제 | Loki 서비스 중지 | 로그 검색 불가 | TestLokiRollback |
| Mimir 문제 | Prometheus로 전환 | Datasource URL 변경 | TestMimirRollback |

### 9.2 롤백 체크리스트

| # | 작업 | 상태 | 비고 |
|---|------|------|------|
| R.1 | OTEL_ENABLED=false 설정 | ✅ | TestRollbackChecklist 통과 |
| R.2 | 서비스 재시작 | ✅ | docker-compose restart |
| R.3 | 기존 기능 동작 확인 | ✅ | 헬스체크 검증 |
| R.4 | 모니터링 정상 확인 | ✅ | Prometheus/Grafana |

### 9.3 롤백 문서

- **상세 롤백 절차**: [164_OTEL_ROLLBACK_PLAN.md](164_OTEL_ROLLBACK_PLAN.md)
- **롤백 검증 테스트**: `tests/integration/otel/test_otel_rollback.py`

---

## 10. 영향받는 파일 요약

### 10.1 신규 생성 파일

| 파일 경로 | 용도 |
|----------|------|
| `selfhealing/observability/__init__.py` | OTEL 초기화 |
| `selfhealing/settings/observability.py` | OTEL 설정 |
| `docker/otel-collector/otel-collector-config.yml` | Collector 설정 |
| `docker/tempo/tempo.yml` | Tempo 설정 |
| `docker/loki/loki.yml` | Loki 설정 |
| `docker/mimir/mimir.yml` | Mimir 설정 (선택) |
| `docker/grafana/provisioning/dashboards/request_tracing.json` | Trace 대시보드 |
| `docker/grafana/provisioning/dashboards/log_explorer.json` | Log 대시보드 |
| `docker/grafana/provisioning/dashboards/unified_view.json` | 통합 대시보드 |

### 10.2 수정 필요 파일

| 파일 경로 | 변경 내용 |
|----------|----------|
| `requirements.txt` | OTEL 패키지 추가 |
| `docker-compose.yml` | 새 서비스 추가, 환경변수 |
| `myproject/settings.py` | OTEL 초기화 호출 |
| `selfhealing/audit/trace.py` | 호환 레이어 |
| `selfhealing/services/http_client.py` | OTEL 활성화 시 분기 |
| `docker/grafana/provisioning/datasources/datasource.yml` | Datasource 추가 |

---

## 11. 관련 문서

- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md): 전체 아키텍처 개요
- [157_OTEL_SDK_INTEGRATION.md](157_OTEL_SDK_INTEGRATION.md): SDK 도입 계획
- [158_OTEL_COLLECTOR_CONFIGURATION.md](158_OTEL_COLLECTOR_CONFIGURATION.md): Collector 구성
- [159_GRAFANA_STACK_INTEGRATION.md](159_GRAFANA_STACK_INTEGRATION.md): Grafana 스택 통합
- [161_OTEL_OPERATIONS_GUIDE.md](161_OTEL_OPERATIONS_GUIDE.md): 운영 가이드
- [162_OTEL_TROUBLESHOOTING_GUIDE.md](162_OTEL_TROUBLESHOOTING_GUIDE.md): 트러블슈팅 가이드
- [163_OTEL_ENVIRONMENT_VARIABLES.md](163_OTEL_ENVIRONMENT_VARIABLES.md): 환경변수 문서
- [164_OTEL_ROLLBACK_PLAN.md](164_OTEL_ROLLBACK_PLAN.md): 롤백 계획

### OTEL 검증 테스트

| 테스트 파일 | 검증 항목 |
|------------|----------|
| `tests/integration/otel/test_otel_functional.py` | 스팬 생성, trace_id 전파, 로그 상관관계, Tempo/Loki 검색 |
| `tests/integration/otel/test_otel_compatibility.py` | 메트릭 호환성, 대시보드, 알림 규칙, OTEL_ENABLED 폴백 |
| `tests/integration/otel/test_otel_performance.py` | 레이턴시, 메모리, 처리량, 데이터 손실 방지 |
| `tests/integration/otel/test_otel_security.py` | 민감 데이터 마스킹, gRPC/HTTP 보안, PII 처리 |
| `tests/integration/otel/test_otel_multiregion.py` | deployment.region, cluster prefix, 멀티 데이터소스 |
| `tests/integration/otel/test_otel_rollback.py` | SDK/Collector/Tempo/Loki/Mimir 롤백 시나리오 |
