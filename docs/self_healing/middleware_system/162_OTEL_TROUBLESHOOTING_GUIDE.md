# OpenTelemetry 트러블슈팅 가이드

> **문서 목적**: OpenTelemetry 시스템의 일반적인 문제 해결 방법 및 디버깅 가이드

---

## 1. 진단 체크리스트

문제 발생 시 다음 순서로 확인:

1. ✅ 서비스 헬스체크 상태
2. ✅ 네트워크 연결 상태
3. ✅ 설정 파일 검증
4. ✅ 로그 확인
5. ✅ 메트릭 확인

---

## 2. 일반적인 문제 및 해결책

### 2.1 Trace가 Tempo에 저장되지 않음

#### 증상
- Grafana에서 trace를 찾을 수 없음
- Tempo API 조회 시 404 반환

#### 진단 단계

```bash
# 1. Collector 헬스체크
curl http://otel-collector:13133/

# 2. Tempo 헬스체크
curl http://tempo:3200/ready

# 3. Collector 메트릭 확인
curl http://otel-collector:8888/metrics | grep otelcol_exporter

# 4. Collector 로그 확인
docker-compose logs otel-collector | tail -50

# 5. Tempo 로그 확인
docker-compose logs tempo | tail -50
```

#### 가능한 원인 및 해결책

| 원인 | 확인 방법 | 해결책 |
|------|----------|--------|
| Collector → Tempo 연결 실패 | 로그에 connection refused | Tempo 서비스 상태 확인, 네트워크 확인 |
| 배치 미플러시 | 짧은 시간 내 조회 | 10초 이상 대기 후 재조회 |
| 샘플링으로 제외 | OTEL_TRACES_SAMPLER_ARG 확인 | 샘플링 비율 조정 또는 1.0으로 설정 |
| 메모리 제한 도달 | otelcol_processor_dropped_spans > 0 | memory_limiter 설정 조정 |

### 2.2 로그가 Loki에 저장되지 않음

#### 증상
- Grafana Explore에서 로그 조회 불가
- LogQL 쿼리 결과 없음

#### 진단 단계

```bash
# 1. Loki 헬스체크
curl http://loki:3100/ready

# 2. Loki 레이블 확인
curl http://loki:3100/loki/api/v1/labels

# 3. Collector → Loki 전송 확인
curl http://otel-collector:8888/metrics | grep loki

# 4. Loki 로그 확인
docker-compose logs loki | tail -50
```

#### 가능한 원인 및 해결책

| 원인 | 확인 방법 | 해결책 |
|------|----------|--------|
| Loki 서비스 다운 | ready 엔드포인트 실패 | Loki 컨테이너 재시작 |
| 인덱스 기간 불일치 | 쿼리 시간 범위 확인 | 올바른 시간 범위로 쿼리 |
| 라벨 형식 오류 | 레이블 API 확인 | OTEL Collector loki exporter 설정 확인 |

### 2.3 메트릭이 Mimir에 저장되지 않음

#### 증상
- PromQL 쿼리 결과 없음
- Grafana 대시보드 패널 빈 상태

#### 진단 단계

```bash
# 1. Mimir 헬스체크
curl http://mimir:9009/ready

# 2. Mimir에서 메트릭 목록 확인
curl "http://mimir:9009/prometheus/api/v1/label/__name__/values"

# 3. Collector → Mimir 전송 확인
curl http://otel-collector:8888/metrics | grep prometheusremotewrite

# 4. Mimir 로그 확인
docker-compose logs mimir | tail -50
```

#### 가능한 원인 및 해결책

| 원인 | 확인 방법 | 해결책 |
|------|----------|--------|
| Remote Write 실패 | Collector 로그에 write 에러 | Mimir 엔드포인트 URL 확인 |
| 메트릭 이름 충돌 | Mimir 로그에 conflict | 메트릭 이름 규칙 확인 |
| 인제스터 용량 초과 | Mimir 메트릭 확인 | Mimir 리소스 증가 |

### 2.4 Grafana에서 Datasource 연결 실패

#### 증상
- Datasource 테스트 실패
- 대시보드 로드 시 에러

#### 진단 단계

```bash
# 1. Grafana에서 각 서비스로 직접 연결 테스트
docker-compose exec grafana curl http://tempo:3200/ready
docker-compose exec grafana curl http://loki:3100/ready
docker-compose exec grafana curl http://mimir:9009/ready

# 2. Grafana 로그 확인
docker-compose logs grafana | grep -i error
```

#### 가능한 원인 및 해결책

| 원인 | 확인 방법 | 해결책 |
|------|----------|--------|
| DNS 해석 실패 | 서비스명으로 접근 불가 | Docker 네트워크 확인 |
| URL 오타 | datasource.yml 검토 | URL 수정 |
| 인증 설정 불일치 | Grafana 로그 확인 | 인증 설정 동기화 |

---

## 3. 성능 문제

### 3.1 높은 메모리 사용량

#### 증상
- Collector OOM Kill
- 느린 응답 시간

#### 진단

```bash
# 메모리 사용량 확인
curl http://otel-collector:8888/metrics | grep memory

# 큐 크기 확인
curl http://otel-collector:8888/metrics | grep queue
```

#### 해결책

```yaml
# otel-collector-config.yml
processors:
  memory_limiter:
    limit_mib: 512      # 증가
    spike_limit_mib: 128
    check_interval: 1s
```

### 3.2 데이터 손실

#### 증상
- 전송된 span 수와 저장된 span 수 불일치
- 메트릭에 gaps 존재

#### 진단

```bash
# 드롭된 데이터 확인
curl http://otel-collector:8888/metrics | grep dropped

# 재시도 횟수 확인
curl http://otel-collector:8888/metrics | grep retry
```

#### 해결책

```yaml
# 재시도 설정 강화
exporters:
  otlp/tempo:
    retry_on_failure:
      enabled: true
      initial_interval: 1s
      max_interval: 30s
      max_elapsed_time: 120s
    sending_queue:
      enabled: true
      num_consumers: 10
      queue_size: 10000  # 증가
```

---

## 4. 상관관계 문제

### 4.1 Trace → Log 링크 동작 안 함

#### 증상
- Trace 상세에서 "View Logs" 클릭 시 결과 없음

#### 확인 사항

1. 로그에 `trace_id` 필드 존재 여부
2. Loki derived fields 설정
3. trace_id 형식 일치 (32자리 hex)

#### 해결책

Loki datasource 설정 확인:
```yaml
jsonData:
  derivedFields:
    - datasourceUid: tempo
      matcherRegex: '"trace_id":"([a-f0-9]{32})"'
      name: TraceID
      url: "$${__value.raw}"
```

### 4.2 Log → Trace 링크 동작 안 함

#### 증상
- 로그에서 trace ID 클릭 시 Tempo로 이동 안 함

#### 확인 사항

1. Tempo tracesToLogsV2 설정
2. 로그의 traceId 필드 존재

#### 해결책

Tempo datasource 설정 확인:
```yaml
jsonData:
  tracesToLogsV2:
    datasourceUid: loki
    filterByTraceID: true
    customQuery: true
    query: '{service_name="$${__span.tags.service.name}"} | json | trace_id="$${__span.traceId}"'
```

---

## 5. 설정 검증

### 5.1 Collector 설정 검증

```bash
# 로컬에서 설정 파일 검증
docker run --rm \
  -v $(pwd)/docker/otel-collector:/etc/otel \
  otel/opentelemetry-collector-contrib:latest \
  --config /etc/otel/otel-collector-config.yml \
  --dry-run
```

### 5.2 YAML 문법 검증

```bash
# Python으로 YAML 검증
python -c "import yaml; yaml.safe_load(open('docker/otel-collector/otel-collector-config.yml'))"
```

---

## 6. 디버깅 모드

### 6.1 Collector 디버그 로깅

```yaml
# otel-collector-config.yml
service:
  telemetry:
    logs:
      level: debug  # info → debug
```

### 6.2 디버그 Exporter 활성화

```yaml
exporters:
  debug:
    verbosity: detailed  # basic → detailed

service:
  pipelines:
    traces:
      exporters: [otlp/tempo, debug]  # debug 추가
```

---

## 7. 복구 절차

### 7.1 Collector 복구

```bash
# 1. 서비스 중지
docker-compose stop otel-collector

# 2. 설정 확인/수정
cat docker/otel-collector/otel-collector-config.yml

# 3. 서비스 시작
docker-compose up -d otel-collector

# 4. 헬스체크
curl http://otel-collector:13133/
```

### 7.2 전체 스택 복구

```bash
# 1. 전체 중지
docker-compose down

# 2. 볼륨 유지하며 재시작
docker-compose up -d

# 3. 모든 서비스 헬스체크
for svc in otel-collector tempo mimir loki grafana; do
  echo "Checking $svc..."
  docker-compose exec $svc wget -q -O- http://localhost/ready || echo "Failed"
done
```

---

## 8. 로그 패턴 및 의미

### 8.1 Collector 로그 패턴

| 로그 패턴 | 의미 | 조치 |
|----------|------|------|
| `connection refused` | 백엔드 연결 실패 | 백엔드 서비스 확인 |
| `context deadline exceeded` | 타임아웃 | 네트워크/부하 확인 |
| `dropping data` | 메모리 제한 도달 | 리소스 증가 |
| `retry failed` | 재시도 실패 | 백엔드 상태 확인 |

### 8.2 Tempo 로그 패턴

| 로그 패턴 | 의미 | 조치 |
|----------|------|------|
| `failed to write block` | 디스크 쓰기 실패 | 디스크 공간 확인 |
| `max live traces exceeded` | 동시 trace 초과 | 설정 조정 |

---

## 9. 에스컬레이션 기준

| 상황 | 심각도 | 에스컬레이션 |
|------|--------|-------------|
| 단일 서비스 다운 | Warning | L1 운영팀 |
| 다중 서비스 다운 | Critical | L2 SRE팀 |
| 데이터 손실 발생 | Critical | L2 SRE팀 |
| 전체 스택 장애 | Emergency | L3 플랫폼팀 |

---

## 관련 문서

- [운영 가이드](./161_OTEL_OPERATIONS_GUIDE.md)
- [환경변수 문서](./163_OTEL_ENVIRONMENT_VARIABLES.md)
- [아키텍처 개요](./156_OTEL_OBSERVABILITY_OVERVIEW.md)
