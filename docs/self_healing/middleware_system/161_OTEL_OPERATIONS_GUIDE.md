# OpenTelemetry 운영 가이드

> **문서 목적**: OpenTelemetry 기반 통합 관측성 시스템의 일상 운영 및 관리 가이드

---

## 1. 시스템 구성 개요

### 1.1 아키텍처

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   Django    │     │  OTEL Collector │     │    Tempo    │
│   Celery    │────▶│   (Gateway)     │────▶│  (Traces)   │
│   Services  │     │                 │     └─────────────┘
└─────────────┘     │                 │     ┌─────────────┐
                    │                 │────▶│    Mimir    │
                    │                 │     │  (Metrics)  │
                    │                 │     └─────────────┘
                    │                 │     ┌─────────────┐
                    └─────────────────┴────▶│    Loki     │
                                            │   (Logs)    │
                                            └─────────────┘
                                                    │
                                            ┌───────▼───────┐
                                            │    Grafana    │
                                            │ (Dashboards)  │
                                            └───────────────┘
```

### 1.2 서비스 포트 정보

| 서비스 | 포트 | 프로토콜 | 용도 |
|--------|------|----------|------|
| OTEL Collector | 4317 | gRPC | OTLP Trace/Metrics/Logs 수신 |
| OTEL Collector | 4318 | HTTP | OTLP Trace/Metrics/Logs 수신 |
| OTEL Collector | 13133 | HTTP | 헬스체크 |
| OTEL Collector | 8888 | HTTP | 자체 메트릭 |
| Tempo | 3200 | HTTP | Trace 조회 API |
| Tempo | 4317 | gRPC | OTLP Trace 수신 |
| Mimir | 9009 | HTTP | Prometheus API |
| Loki | 3100 | HTTP | 로그 조회/Push API |
| Grafana | 3000 | HTTP | 대시보드 UI |

---

## 2. 일상 운영

### 2.1 헬스체크 확인

```bash
# OTEL Collector 헬스체크
curl http://otel-collector:13133/

# Tempo 헬스체크
curl http://tempo:3200/ready

# Mimir 헬스체크
curl http://mimir:9009/ready

# Loki 헬스체크
curl http://loki:3100/ready

# Grafana 헬스체크
curl http://grafana:3000/api/health
```

### 2.2 메트릭 모니터링

Collector 자체 메트릭 확인:
```bash
curl http://otel-collector:8888/metrics
```

주요 모니터링 메트릭:
- `otelcol_receiver_accepted_spans`: 수신된 span 수
- `otelcol_exporter_sent_spans`: 전송된 span 수
- `otelcol_processor_batch_batch_send_size`: 배치 크기
- `otelcol_process_memory_rss`: 메모리 사용량

### 2.3 로그 확인

```bash
# Docker 로그 확인
docker-compose logs otel-collector
docker-compose logs tempo
docker-compose logs mimir
docker-compose logs loki
```

---

## 3. 주요 운영 시나리오

### 3.1 Trace 조회

Tempo API를 통한 trace 조회:
```bash
# trace_id로 조회
curl http://tempo:3200/api/traces/{trace_id}

# 서비스별 검색
curl "http://tempo:3200/api/search?tags=service.name%3Dselfhealing"
```

### 3.2 로그 조회

Loki LogQL 쿼리:
```bash
# 서비스별 로그 조회
curl -G http://loki:3100/loki/api/v1/query \
  --data-urlencode 'query={service_name="selfhealing"}'

# 에러 로그만 조회
curl -G http://loki:3100/loki/api/v1/query \
  --data-urlencode 'query={service_name="selfhealing"} |= "ERROR"'

# trace_id로 로그 조회
curl -G http://loki:3100/loki/api/v1/query \
  --data-urlencode 'query={service_name="selfhealing"} | json | trace_id="abc123"'
```

### 3.3 메트릭 조회

Mimir PromQL 쿼리:
```bash
# 즉시 쿼리
curl "http://mimir:9009/prometheus/api/v1/query?query=up"

# 범위 쿼리
curl "http://mimir:9009/prometheus/api/v1/query_range?query=up&start=2024-01-01T00:00:00Z&end=2024-01-01T01:00:00Z&step=15s"
```

---

## 4. 스케일링 가이드

### 4.1 Collector 스케일링

Collector 복제 시 고려사항:
- 스테이트리스 구조로 수평 확장 가능
- 로드밸런서 뒤에 배치
- `memory_limiter` 설정 조정

```yaml
# docker-compose.yml 예시
otel-collector:
  deploy:
    replicas: 3
  environment:
    - GOMAXPROCS=4
```

### 4.2 스토리지 스케일링

| 컴포넌트 | 스케일링 방법 |
|----------|--------------|
| Tempo | S3/GCS 백엔드 사용 |
| Mimir | 블록 스토리지 확장 |
| Loki | 청크 스토리지 분리 |

---

## 5. 백업 및 복구

### 5.1 설정 백업

```bash
# Collector 설정 백업
cp docker/otel-collector/otel-collector-config.yml backup/

# Grafana 대시보드 백업
docker-compose exec grafana tar -czf /tmp/dashboards.tar.gz /var/lib/grafana/dashboards
docker cp grafana:/tmp/dashboards.tar.gz backup/
```

### 5.2 데이터 볼륨 백업

```bash
# Tempo 데이터
docker-compose stop tempo
tar -czf tempo-data.tar.gz ./docker/tempo/data
docker-compose start tempo

# Mimir 데이터
docker-compose stop mimir
tar -czf mimir-data.tar.gz ./docker/mimir/data
docker-compose start mimir
```

---

## 6. 유지보수 절차

### 6.1 Collector 재시작

```bash
# 무중단 재시작 (새 설정 적용)
docker-compose restart otel-collector

# 전체 재시작
docker-compose down otel-collector
docker-compose up -d otel-collector
```

### 6.2 설정 변경 적용

1. 설정 파일 수정
2. 설정 검증
3. 재시작
4. 헬스체크 확인

```bash
# 설정 검증 (로컬 테스트)
docker run --rm -v $(pwd)/docker/otel-collector:/etc/otel \
  otel/opentelemetry-collector-contrib:latest \
  --config /etc/otel/otel-collector-config.yml --dry-run

# 적용
docker-compose restart otel-collector

# 확인
curl http://otel-collector:13133/
```

---

## 7. 모니터링 알림 설정

### 7.1 권장 알림 규칙

| 알림 | 조건 | 심각도 |
|------|------|--------|
| Collector 다운 | up{job="otel-collector"} == 0 | Critical |
| 메모리 초과 | otelcol_process_memory_rss > 200MB | Warning |
| 전송 실패 증가 | rate(otelcol_exporter_send_failed_spans[5m]) > 0 | Warning |
| 큐 적체 | otelcol_exporter_queue_size > 1000 | Warning |

### 7.2 Grafana 알림 설정

Grafana UI에서:
1. Alerting → Alert rules
2. Create rule
3. PromQL 조건 설정
4. 알림 채널 연결

---

## 8. 용량 계획

### 8.1 예상 리소스 사용량

| 컴포넌트 | CPU (코어) | 메모리 | 디스크/일 |
|----------|-----------|--------|----------|
| Collector | 0.5-2 | 256-512MB | - |
| Tempo | 0.5-1 | 512MB-1GB | ~10GB |
| Mimir | 0.5-1 | 512MB-1GB | ~5GB |
| Loki | 0.5-1 | 512MB-1GB | ~20GB |
| Grafana | 0.2-0.5 | 256-512MB | ~100MB |

### 8.2 데이터 보존 정책

| 데이터 유형 | 기본 보존 기간 | 권장 설정 |
|------------|---------------|----------|
| Traces | 7일 | 14-30일 |
| Metrics | 15일 | 30-90일 |
| Logs | 7일 | 14-30일 |

---

## 9. 연락처 및 에스컬레이션

| 레벨 | 담당 | 연락 방법 |
|------|------|----------|
| L1 | 운영팀 | Slack #ops-alerts |
| L2 | SRE팀 | PagerDuty |
| L3 | 플랫폼팀 | Jira 티켓 |

---

## 관련 문서

- [트러블슈팅 가이드](./161_OTEL_TROUBLESHOOTING_GUIDE.md)
- [환경변수 문서](./162_OTEL_ENVIRONMENT_VARIABLES.md)
- [아키텍처 개요](./156_OTEL_OBSERVABILITY_OVERVIEW.md)
