# OpenTelemetry 환경변수 문서

> **문서 목적**: OpenTelemetry SDK 및 관련 서비스의 환경변수 설정 참조 문서

---

## 1. SDK 환경변수 (애플리케이션)

### 1.1 기본 설정

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OTEL_ENABLED` | `false` | OpenTelemetry SDK 활성화 여부 |
| `OTEL_SERVICE_NAME` | `selfhealing` | 텔레메트리에서 사용할 서비스 이름 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP Collector 엔드포인트 URL |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | `5000` | OTLP exporter 타임아웃 (밀리초) |

### 1.2 샘플링 설정

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | 샘플링 전략 |
| `OTEL_TRACES_SAMPLER_ARG` | `0.01` | 샘플링 비율 (0.0~1.0, 0.01 = 1%) |
| `OTEL_ADAPTIVE_SAMPLING_ENABLED` | `true` | 긴급도 기반 적응형 샘플링 활성화 |
| `OTEL_SLA_CRITICAL_MS` | `500` | SLA 임계값 (밀리초), 초과 시 100% 샘플링 |

**사용 가능한 OTEL_TRACES_SAMPLER 값:**
- `always_on`: 모든 trace 수집
- `always_off`: trace 수집 안 함
- `traceidratio`: trace ID 기반 비율 샘플링
- `parentbased_always_on`: 부모 기반, 기본 수집
- `parentbased_always_off`: 부모 기반, 기본 미수집
- `parentbased_traceidratio`: 부모 기반, 비율 샘플링 (권장)

### 1.3 리소스 속성

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OTEL_RESOURCE_ATTRIBUTES` | `""` | 쉼표 구분 key=value 쌍 |

**예시:**
```bash
OTEL_RESOURCE_ATTRIBUTES="deployment.environment=production,deployment.region=ap-northeast-2,cluster.name=seop"
```

### 1.4 계측 설정

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OTEL_DJANGO_INSTRUMENT_ENABLED` | `true` | Django 자동 계측 활성화 |
| `OTEL_REQUESTS_INSTRUMENT_ENABLED` | `true` | requests 라이브러리 자동 계측 |
| `OTEL_CELERY_INSTRUMENT_ENABLED` | `true` | Celery 자동 계측 활성화 |

### 1.5 제외 설정

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OTEL_EXCLUDED_URLS` | `/health,/health/,/health/ready,/health/live,/health/l3,/metrics` | 추적에서 제외할 URL 경로 (쉼표 구분) |

---

## 2. Docker Compose 환경변수

### 2.1 Django 서비스

```yaml
services:
  web:
    environment:
      # OTEL 기본 설정
      - OTEL_ENABLED=true
      - OTEL_SERVICE_NAME=django-web
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

      # 샘플링
      - OTEL_TRACES_SAMPLER=parentbased_traceidratio
      - OTEL_TRACES_SAMPLER_ARG=0.01

      # 리소스 속성
      - OTEL_RESOURCE_ATTRIBUTES=deployment.environment=development,deployment.region=local
```

### 2.2 Celery Worker 서비스

```yaml
services:
  celery_worker:
    environment:
      - OTEL_ENABLED=true
      - OTEL_SERVICE_NAME=celery-worker
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_TRACES_SAMPLER_ARG=0.01
      - OTEL_CELERY_INSTRUMENT_ENABLED=true
```

### 2.3 OTEL Collector 서비스

```yaml
services:
  otel-collector:
    environment:
      - GOGC=80                    # Go GC 설정
      - GOMAXPROCS=4               # CPU 코어 수
```

---

## 3. 환경별 권장 설정

### 3.1 개발 환경 (Development)

```bash
OTEL_ENABLED=true
OTEL_SERVICE_NAME=selfhealing-dev
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_TRACES_SAMPLER=always_on
OTEL_TRACES_SAMPLER_ARG=1.0
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=development
```

### 3.2 스테이징 환경 (Staging)

```bash
OTEL_ENABLED=true
OTEL_SERVICE_NAME=selfhealing-staging
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1
OTEL_ADAPTIVE_SAMPLING_ENABLED=true
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=staging
```

### 3.3 운영 환경 (Production)

```bash
OTEL_ENABLED=true
OTEL_SERVICE_NAME=selfhealing
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.01
OTEL_ADAPTIVE_SAMPLING_ENABLED=true
OTEL_SLA_CRITICAL_MS=500
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,deployment.region=ap-northeast-2
```

---

## 4. 롤백 설정

### 4.1 OTEL 완전 비활성화

```bash
OTEL_ENABLED=false
```

이 설정으로:
- SDK 초기화가 건너뛰어짐
- 기존 트레이싱 시스템으로 폴백
- 성능 오버헤드 제로

### 4.2 샘플링만 비활성화

```bash
OTEL_ENABLED=true
OTEL_TRACES_SAMPLER=always_off
```

이 설정으로:
- SDK는 초기화되지만 trace 수집 안 함
- 메트릭/로그는 계속 수집

---

## 5. 테스트 환경변수

### 5.1 통합 테스트용

```bash
# docker-compose.test.yml에서 사용
OTEL_COLLECTOR_ENDPOINT=http://otel-collector:4318
TEMPO_ENDPOINT=http://tempo:3200
MIMIR_ENDPOINT=http://mimir:9009
LOKI_ENDPOINT=http://loki:3100
COLLECTOR_HEALTH_ENDPOINT=http://otel-collector:13133
COLLECTOR_METRICS_ENDPOINT=http://otel-collector:8888
GRAFANA_ENDPOINT=http://grafana:3000
WEB_ENDPOINT=http://web:8000
```

### 5.2 단위 테스트용

```bash
OTEL_ENABLED=false  # 단위 테스트에서는 비활성화
```

---

## 6. 표준 OTEL 환경변수 참조

OpenTelemetry 표준 환경변수 (SDK에서 자동 인식):

| 환경변수 | 설명 |
|----------|------|
| `OTEL_SDK_DISABLED` | SDK 완전 비활성화 |
| `OTEL_LOG_LEVEL` | 로깅 레벨 (debug, info, warn, error) |
| `OTEL_PROPAGATORS` | 컨텍스트 전파자 (tracecontext, baggage, b3) |
| `OTEL_TRACES_EXPORTER` | Trace exporter 유형 (otlp, console, none) |
| `OTEL_METRICS_EXPORTER` | Metrics exporter 유형 |
| `OTEL_LOGS_EXPORTER` | Logs exporter 유형 |

---

## 7. 멀티 리전 설정

### 7.1 서울 리전 (Primary)

```bash
OTEL_SERVICE_NAME=selfhealing
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.seoul:4317
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,deployment.region=ap-northeast-2,cluster.name=seop
```

### 7.2 도쿄 리전 (Secondary)

```bash
OTEL_SERVICE_NAME=selfhealing
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.tokyo:4317
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,deployment.region=ap-northeast-1,cluster.name=tokp
```

---

## 8. 검증 명령어

### 8.1 환경변수 확인

```bash
# Python 애플리케이션에서
python -c "from selfhealing.settings.observability import get_otel_settings; s = get_otel_settings(); print(s.model_dump())"

# 환경변수 직접 확인
env | grep OTEL
```

### 8.2 설정 적용 확인

```bash
# Django 서버 시작 시 로그 확인
docker-compose logs web | grep -i opentelemetry

# 예상 출력:
# OpenTelemetry initialized: service=selfhealing endpoint=http://otel-collector:4317 sampling_ratio=1.00%
```

---

## 관련 문서

- [운영 가이드](./161_OTEL_OPERATIONS_GUIDE.md)
- [트러블슈팅 가이드](./162_OTEL_TROUBLESHOOTING_GUIDE.md)
- [SDK 통합 계획](./157_OTEL_SDK_INTEGRATION.md)
