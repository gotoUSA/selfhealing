# 325. Examples Directory — 참고용 설정 예시

> **Status**: Planning
> **Severity**: P3 (LOW) — repo 분리 시 함께 생성
> **Target**: `examples/` (selfhealing repo 신규 디렉토리)
> **References**:
> - 319 — Repo Separation Overview (Step 6)
> - 320 — Auto-Configuration (consumer 설정 단순화)
> - 321 — Celery Beat Internalization (configure_selfhealing_celery 래퍼)

---

## 1. 목적

selfhealing 라이브러리를 사용하는 새 consumer 앱이 빠르게 시작할 수 있도록
Django, Celery, Gunicorn, K8s, Prometheus 설정 예시를 제공한다.

현재 shopping repo의 실제 설정에서 추출하되, 비즈니스 로직을 제거하고 일반화한다.

---

## 2. 디렉토리 구조

```
examples/
├── README.md                        # Quick Start 가이드
├── django-setup/
│   ├── settings.py                  # 최소 Django 설정 (명시적 래퍼 패턴)
│   ├── celery.py                    # Celery Beat 연동 (configure_selfhealing_celery)
│   ├── gunicorn.conf.py             # Gunicorn fork hook
│   └── urls.py                      # URL 설정
├── k8s/
│   ├── base/                        # Kustomize Base 매니페스트
│   │   ├── kustomization.yaml
│   │   ├── deployment.yaml          # Django API 배포
│   │   ├── celery-worker.yaml       # Celery Worker
│   │   ├── celery-critical-worker.yaml  # Critical 큐 Worker
│   │   ├── hpa.yaml                 # HPA (Prometheus Adapter 연동)
│   │   ├── keda-scaledobject.yaml   # KEDA ScaledObject (Celery 큐)
│   │   ├── pdb.yaml                 # Pod Disruption Budget
│   │   ├── servicemonitor.yaml      # Prometheus ServiceMonitor
│   │   └── networkpolicy.yaml       # Pod 간 통신 제한
│   └── overlays/
│       ├── dev/
│       │   ├── kustomization.yaml   # replicas: 1, 리소스 축소
│       │   └── patches/
│       ├── staging/
│       │   ├── kustomization.yaml
│       │   └── patches/
│       └── production/
│           ├── kustomization.yaml   # replicas: 3+, PDB, anti-affinity 강화
│           └── patches/
│               ├── resource-limits.yaml
│               └── security-context.yaml
├── monitoring/
│   ├── prometheus-alerts.yml        # 알림 규칙
│   ├── prometheus-adapter.yaml      # Custom metrics adapter
│   ├── grafana-dashboard.json       # Grafana 대시보드 (UID 파라미터화)
│   └── otel-collector.yml           # OTEL Collector (Tail Sampling 포함)
├── docker/
│   ├── Dockerfile                   # 컨테이너 빌드
│   ├── docker-compose.yml           # Phase 1 최소 인프라 샌드박스
│   └── config/
│       ├── tempo.yml                # Tempo 설정 (분산 추적 저장소)
│       ├── mimir.yml                # Mimir 설정 (메트릭 장기 저장소)
│       ├── loki.yml                 # Loki 설정 (로그 저장소)
│       ├── grafana-datasources.yml  # Grafana 데이터소스 프로비저닝
│       └── grafana-dashboards.yml   # Grafana 대시보드 프로비저닝
└── scripts/
    └── sanitize-dashboards.sh       # Grafana Dashboard UID Sanitizer
```

---

## 3. 핵심 예시 파일

### 3.1 examples/django-setup/settings.py

> **아키텍처 결정**: 320 문서에서 합의한 **명시적 래퍼 패턴** 적용.
> `configure_selfhealing(namespace=globals())`를 settings.py 맨 마지막에 호출한다.
> INSTALLED_APPS의 `selfhealing.adapters.django`는 AppConfig.ready() 내부 초기화 전용이며,
> MIDDLEWARE/REST_FRAMEWORK 자동 설정을 수행하지 않는다.

```python
# 최소 selfhealing 설정 예시
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # selfhealing — AppConfig.ready() 내부 초기화 전용
    # (hash chain 동기화, orphan 서비스, session 시그널 등)
    # MIDDLEWARE/REST_FRAMEWORK 자동 설정은 하지 않음
    "selfhealing.adapters.django",
    # Your app
    "myapp",
]

# 비즈니스 도메인 설정 (필수)
SELFHEALING_CORE_DOMAINS = ["payment", "order", "inventory"]

# DLQ 적재 대상 경로 (선택)
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [
    r"^/api/orders/",
    r"^/api/payments/",
]

# 도메인 추론 매핑 (선택)
SELF_HEALING_DOMAIN_MAPPING = {
    "/payments/": "payment",
    "/orders/": "order",
}

# Celery 시그널 도메인 매핑 (선택)
SELFHEALING_TASK_DOMAIN_MAPPING = {
    "myapp.tasks.process_payment": "payment",
    "myapp.tasks.process_order": "order",
}

# 특정 기능 비활성화 (선택, 기본값 전부 True)
# SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
# SELFHEALING_AUDIT_MIDDLEWARE_ENABLED = False

# ─── 반드시 settings.py 맨 마지막에 호출 ───
# 320 문서 합의: 명시적 래퍼로 MIDDLEWARE, REST_FRAMEWORK 설정
from selfhealing.adapters.django import configure_selfhealing

configure_selfhealing(namespace=globals())
# Gunicorn 환경에서는 OTEL을 post_worker_init에서 초기화:
# configure_selfhealing(namespace=globals(), disable_auto_otel=True)
```

### 3.2 examples/django-setup/celery.py

> **아키텍처 결정**: 321 문서에서 합의한 **configure_selfhealing_celery(app)** 헬퍼 사용.
> dict.update() 병합 대신 1줄 래퍼 호출로 Beat 스케줄, Queue, Route, Task 등록을 처리한다.
> 모듈별 `include_*` 플래그로 선택적 비활성화가 가능하다.

```python
from celery import Celery

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Consumer Beat 스케줄 (selfhealing과 별도)
app.conf.beat_schedule = {
    # Your app tasks here
}

# ─── selfhealing Celery 통합 (321 문서 합의 패턴) ───
# 1. Beat 스케줄/Queue/Route/Task 일괄 등록
from selfhealing.adapters.celery.beat_schedule import configure_selfhealing_celery

configure_selfhealing_celery(
    app,
    # 모듈별 선택적 비활성화 (기본값 전부 True)
    # include_intelligence=False,
    # include_saga=False,
    queue_prefix="myproject",  # 멀티서비스 큐 네임스페이스 격리
    queue_type="quorum",       # RabbitMQ Quorum Queue (메시지 유실 방지)
    enable_dlx=True,           # Dead Letter Exchange
)

# 2. Celery 시그널 훅 (CB, DLQ, Metrics, Forensics 자동 연동)
from selfhealing.adapters.celery import setup_selfhealing_signals

setup_selfhealing_signals(
    app=app,
    enabled=True,
    cb_enabled=True,
    dlq_enabled=True,
    metrics_enabled=True,
    forensics_enabled=True,
    task_domain_mapping={
        "myapp.tasks.process_payment": "payment",
        "myapp.tasks.process_order": "order",
    },
)
```

### 3.3 examples/django-setup/gunicorn.conf.py

```python
import os

workers = int(os.environ.get("GUNICORN_WORKERS", 4))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

_env = os.environ.get("DEPLOYMENT_ENV", "development")
preload_app = _env != "development"
reload = _env == "development"


def post_fork(server, worker):
    from django.db import connections
    for conn in connections.all():
        conn.close()
    from selfhealing.server import post_fork_reset
    post_fork_reset(worker)


def post_worker_init(worker):
    from selfhealing.server import post_worker_init_start
    post_worker_init_start(worker)


def worker_exit(server, worker):
    from selfhealing.server import worker_exit_cleanup
    worker_exit_cleanup(worker)
```

### 3.4 examples/docker/Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /code
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
# selfhealing은 pyproject.toml dependencies에서 자동 설치

COPY . .
ENV PYTHONPATH=/code

CMD ["gunicorn", "myproject.wsgi:application", "-c", "gunicorn.conf.py"]
```

---

## 4. Phase 1 / Phase 2 샌드박스 전략

examples/ 디렉토리는 2단계로 구성한다.

### Phase 1 (325 범위): 참조용 + 최소 인프라 샌드박스

`docker-compose up` 한 번으로 selfhealing 연동에 필요한 인프라만 기동한다.
Consumer는 자기 앱을 이 인프라에 연결하여 즉시 개발을 시작할 수 있다.

```
examples/docker/docker-compose.yml 서비스:
  - redis (7-alpine)          — Cache/Broker
  - db (PostgreSQL 15)        — DB
  - otel-collector             — 텔레메트리 수집
  - tempo                      — Trace 저장
  - mimir                      — Metric 저장
  - loki                       — Log 저장
  - grafana                    — 대시보드 (프로비저닝 완료)
```

Consumer 앱(Django/Celery)은 포함하지 않는다.
Consumer가 자기 프로젝트에서 `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`로
연결하면 Grafana에서 즉시 메트릭/트레이스/로그를 확인할 수 있다.

### Phase 2 (후속 문서): End-to-End 실행형 데모

별도 문서에서 Dummy Consumer 앱과 시나리오 스크립트를 추가한다.

```
examples/sandbox/
├── docker-compose.yml          # Phase 1 인프라 + Dummy Consumer
├── dummy_consumer/             # 의도적 에러 발생 앱
├── scenarios/
│   ├── circuit_breaker_demo.sh # CB Open → Half-Open → Close 시각화
│   ├── dlq_replay_demo.sh      # DLQ 적재 → Replay 실행
│   ├── error_budget_demo.sh    # 30일 SLO 99.9% 위반 시뮬레이션
│   └── chaos_injection_demo.sh # Chaos Mode 활성화 데모
├── load_test/
│   └── locustfile.py           # Locust 부하 테스트 (CB 트리거용)
└── README.md                   # "5분 안에 CB 열리는 것 확인하기"
```

Phase 2 시나리오는 기존 Prometheus 알림 규칙(`docker/prometheus/rules/alerts.yml`)과
연동하여 다음 알림이 실제로 트리거되는 것을 시각적으로 확인할 수 있다:

| 시나리오 | 트리거하는 알림 | Chaos 환경변수 |
|----------|---------------|---------------|
| CB Demo | `CircuitBreakerOpen`, `CircuitBreakerHalfOpen` | `CHAOS_PAYMENT_CONFIRM_DELAY_MS=3000` |
| DLQ Demo | `DLQHighPendingCount`, `DLQSpikeDetected` | `PAYMENT_CB_FAILURE_THRESHOLD=3` |
| Error Budget | `ErrorBudgetCritical`, `ErrorBudgetFastBurn` | 지속적 에러 주입 |
| Chaos | `SelfHealingMetricsDown`, `RequestThroughputDrop` | `PHASE2_CHAOS_MODE=true` |

---

## 5. K8s 매니페스트: Kustomize Base/Overlay 구조

### 5.1 Kustomize 선택 근거

| 기준 | Helm | Kustomize | 결정 |
|------|------|-----------|------|
| 목적 부합 | 배포 패키지 | 참조용 예시 | **Kustomize** |
| 학습 곡선 | 템플릿 문법 | kubectl 내장 | Kustomize가 낮음 |
| Consumer 활용 | Chart 의존 | base 복사 후 overlay 작성 | Kustomize가 유연 |
| CRD 지원 | values.yaml | Strategic Merge Patch | 동등 |

Consumer는 대부분 사내 표준 Helm Chart를 이미 보유하고 있으므로,
selfhealing examples는 "이 컴포넌트들이 필요하다"는 명세를 Kustomize base로 제공한다.

### 5.2 Base 매니페스트 구성

```yaml
# examples/k8s/base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

commonLabels:
  app.kubernetes.io/part-of: selfhealing
  app.kubernetes.io/managed-by: kustomize

resources:
  - deployment.yaml
  - celery-worker.yaml
  - celery-critical-worker.yaml
  - hpa.yaml                  # Django API only
  - keda-scaledobject.yaml    # Celery workers only
  - pdb.yaml
  - servicemonitor.yaml
  - networkpolicy.yaml
```

> **주의 — HPA/KEDA 분리 원칙**:
> Django API는 `hpa.yaml`(Prometheus Adapter 커스텀 메트릭)로 스케일링하고,
> Celery Workers(default + critical)는 `keda-scaledobject.yaml`(Redis 큐 길이 + CPU 폴백)로 스케일링한다.
> 동일 Deployment에 HPA와 KEDA ScaledObject를 동시에 적용하면
> KEDA가 내부적으로 생성하는 HPA와 충돌하여 스케일링 진동이 발생한다.

### 5.3 Overlay 예시 (Production)

```yaml
# examples/k8s/overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: production

resources:
  - ../../base

patches:
  - path: patches/resource-limits.yaml
  - path: patches/security-context.yaml

images:
  - name: myproject
    newTag: v1.2.3  # :latest 금지 — 시맨틱 버전 필수
```

### 5.4 추출 전 보안 하드닝 체크리스트

현재 `k8s/` 디렉토리의 Raw YAML을 examples/로 추출하기 전에
다음 항목을 반드시 수정한다.

| 항목 | 현재 상태 | 수정 사항 | 심각도 |
|------|----------|----------|--------|
| 이미지 태그 | `image: myproject:latest` | `image: myproject:v0.0.0` (placeholder) + overlay에서 지정 | CRITICAL |
| securityContext | 전체 미설정 | `runAsNonRoot: true`, `capabilities.drop: ["ALL"]` 추가 | CRITICAL |
| NetworkPolicy | 미정의 | Pod 간 통신 제한 정책 추가 | HIGH |
| Namespace | `production`/`selfhealing` 혼재 | base에서 제거, overlay에서 지정 | HIGH |
| KEDA minReplicaCount | critical worker `minReplicaCount: 1` | `minReplicaCount: 2` (HA 보장) | HIGH |
| KEDA Fallback Scaler | Redis 트리거만 존재 | CPU 기반 Fallback 트리거 추가 | HIGH |
| OTEL Sidecar 리소스 | `cpu: 50m, memory: 64Mi` | `cpu: 100m, memory: 128Mi` (최소) | HIGH |
| PDB 중복 | deployment 파일 + 별도 PDB 파일 공존 | 단일 소스 (base/pdb.yaml)로 통합 | MEDIUM |
| Secret 참조 | 이름 하드코딩 (`postgres-secrets`) | Kustomize secretGenerator 또는 placeholder | MEDIUM |

### 5.5 Production Overlay: securityContext 패치 (Defense-in-Depth)

> base에 이미 securityContext가 적용되어 있으나, production overlay에도
> 동일한 값을 명시적으로 패치한다. base가 변경되더라도 production 환경의
> 보안 컨텍스트가 유지되도록 하는 안전망 역할이다.

```yaml
# examples/k8s/overlays/production/patches/security-context.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: django-api
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
        - name: django
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
```

---

## 6. Grafana Dashboard 이식성 (UID 파라미터화)

### 6.1 문제

Grafana에서 직접 Export한 JSON에는 Data Source UID가 하드코딩된다.
Consumer가 Import하면 자신의 DS UID와 불일치하여 패널이 깨진다.

현재 대시보드 분석 결과:

| 문제 유형 | 예시 | 영향 |
|----------|------|------|
| UID 하드코딩 | `"uid": "loki"` (dlq_monitoring.json) | Loki 패널 깨짐 |
| 이름 기반 참조 | `"datasource": "Prometheus"` (adaptive_throttle.json) | DS 이름 변경 시 깨짐 |
| 타임존 하드코딩 | `"timezone": "Asia/Seoul"` (dlq_monitoring.json) | 글로벌 팀 부적합 |
| Exemplar 링크 UID | `"datasourceUid": "tempo"` | 트레이스 연동 불가 |

### 6.2 Sanitizer 스크립트

```bash
#!/usr/bin/env bash
# examples/scripts/sanitize-dashboards.sh
# Grafana Dashboard UID를 템플릿 변수로 치환하여 이식성 확보
set -euo pipefail

DASHBOARD_DIR="${1:-examples/monitoring}"

for f in "$DASHBOARD_DIR"/*.json; do
  [ -f "$f" ] || continue

  # 1) UID 기반 참조 → 템플릿 변수
  sed -i \
    -e 's/"uid":\s*"prometheus"/"uid": "${DS_PROMETHEUS}"/g' \
    -e 's/"uid":\s*"mimir"/"uid": "${DS_MIMIR}"/g' \
    -e 's/"uid":\s*"tempo"/"uid": "${DS_TEMPO}"/g' \
    -e 's/"uid":\s*"loki"/"uid": "${DS_LOKI}"/g' \
    "$f"

  # 2) datasourceUid (Exemplar/Cross-DS 링크) → 템플릿 변수
  sed -i \
    -e 's/"datasourceUid":\s*"prometheus"/"datasourceUid": "${DS_PROMETHEUS}"/g' \
    -e 's/"datasourceUid":\s*"tempo"/"datasourceUid": "${DS_TEMPO}"/g' \
    -e 's/"datasourceUid":\s*"loki"/"datasourceUid": "${DS_LOKI}"/g' \
    -e 's/"datasourceUid":\s*"mimir"/"datasourceUid": "${DS_MIMIR}"/g' \
    "$f"

  # 3) 이름 기반 참조 → UID 기반으로 변환
  sed -i \
    -e 's/"datasource":\s*"Prometheus"/"datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}/g' \
    "$f"

  # 4) 타임존 → UTC (글로벌 호환)
  sed -i \
    -e 's/"timezone":\s*"Asia\/Seoul"/"timezone": "utc"/g' \
    "$f"

  echo "Sanitized: $f"
done
```

### 6.3 대시보드 Datasource UID 전략

대시보드 JSON은 **두 가지 용도**로 사용된다:

| 용도 | Datasource UID 형식 | 동작 |
|------|---------------------|------|
| **파일 기반 프로비저닝** (docker-compose) | 직접 UID (`"uid": "mimir"`) | Grafana가 프로비저닝된 DS UID로 즉시 연결 |
| **수동 Import** (Grafana UI) | `__inputs` 변수 (`"uid": "${DS_MIMIR}"`) | Import 시 DS 매핑 프롬프트 표시 |

docker-compose 샌드박스에서는 **파일 기반 프로비저닝**이 주 사용 경로이므로,
대시보드 JSON에는 `grafana-datasources.yml`에 정의된 실제 UID (`mimir`, `tempo`, `loki`)를 사용한다.

Consumer가 자체 Grafana에 수동 Import할 때는 `sanitize-dashboards.sh`를 실행하여
하드코딩된 UID를 `__inputs` 변수로 치환한 후 Import한다.

```bash
# 수동 Import 전 UID → __inputs 변수 치환
bash examples/scripts/sanitize-dashboards.sh examples/monitoring
# 이후 Grafana UI에서 Import → DS 매핑 프롬프트에서 자신의 DS 선택
```

### 6.4 장기 과제: Grafonnet 마이그레이션

빅테크(Google SRE, GitLab)에서는 대시보드를 JSON이 아닌 Jsonnet/Grafonnet 코드로 관리한다.
Data Source가 변수로 자연스럽게 분리되고, Export/Import 이식성 문제가 원천 차단된다.
현재 10개 대시보드가 JSON으로 존재하므로 325 범위에서는 Sanitizer를 사용하되,
Grafonnet 마이그레이션은 장기 과제로 분류한다.

---

## 7. OTEL Collector 필수 파이프라인

### 7.1 현재 구성 수준

기존 `docker/otel-collector/` 구성은 이미 엔터프라이즈급이다:

| 기능 | 설정 | 수준 |
|------|------|------|
| 3-tier 아키텍처 | Agent (48MiB) → Gateway (400MiB) → Backend | Google SRE급 |
| Sensitive Data Redaction | JWT, Bearer, RFC1918 IP, 서버 경로 패턴 차단 | GDPR/SOC2 대응 |
| Disk Buffering | WAL + file_storage (Tempo/Loki), compaction 활성 | 무중단 배포 대응 |
| Bearer Token Auth | Gateway 인증 (`OTEL_GATEWAY_API_KEY`) | Zero-trust 대응 |
| Retry with Backoff | 1s → 10s, max 30s | 기본 복원력 |
| Memory Limiter | 256MiB (main), 48MiB (agent), 400MiB (gateway) | 리소스 보호 |

### 7.2 추가 필수: Tail Sampling 정책

CB 상태 전환, DLQ 에러 시에는 모든 Span을 유지해야 하고,
정상 요청은 1% 샘플링이면 충분하다. Gateway에 Tail Sampling을 적용한다.

```yaml
# examples/monitoring/otel-collector.yml — Gateway pipeline에 추가
processors:
  tail_sampling:
    decision_wait: 10s
    num_traces: 100000
    policies:
      # 에러 Span은 100% 수집
      - name: errors-always
        type: status_code
        status_code: { status_codes: [ERROR] }
      # selfhealing 핵심 컴포넌트 Span 100% 수집
      - name: selfhealing-critical
        type: string_attribute
        string_attribute:
          key: selfhealing.component
          values: [circuit_breaker, dlq, saga, retry]
      # 고지연 요청 (500ms+) 100% 수집
      - name: high-latency
        type: latency
        latency: { threshold_ms: 500 }
      # 나머지는 1% 확률 샘플링
      - name: probabilistic-default
        type: probabilistic
        probabilistic: { sampling_percentage: 1 }
```

### 7.3 Head Sampling과 Tail Sampling의 상호작용

> **주의**: 이 설정을 올바르게 이해하지 않으면 에러 가시성을 잃는다.

| 구성 | Head (App) | Tail (Gateway) | 에러 Span 수집률 | 비용 |
|------|-----------|---------------|-----------------|------|
| **권장 A** | OFF (100% 전송) | ON (정책 기반) | 100% | 높음 (네트워크) |
| **권장 B** | 10% | ON (에러 100%) | 10% (도착분 전부 유지) | 중간 |
| **비권장** | 1% | ON (에러 100%) | 1% (에러도 99% 드롭) | 낮지만 무의미 |

예시에서는 **권장 A**를 기본으로 사용한다:

```bash
# Consumer .env
OTEL_TRACES_SAMPLER=always_on              # Head Sampling OFF
# Tail Sampling은 Gateway otel-collector.yml에서 처리
```

비용이 문제라면 **권장 B** (Head 10%)를 사용하되,
에러 Span의 90%가 App 레벨에서 이미 드롭된다는 점을 반드시 인지해야 한다.

### 7.4 추가 권장 설정

| 항목 | 현재 | 권장 | 이유 |
|------|------|------|------|
| Retry max_elapsed_time | 30s | 5m~10m | 네트워크 장애 시 30초는 짧음. file_storage 큐 보유 시 길어도 무방 |
| Rate Limiter | 미적용 | 추가 | 버그 있는 Agent가 Backend를 DDoS 가능 |
| OTEL Sidecar memory | 64Mi | 128Mi+ | 프로덕션 트래픽에서 OOM 위험 |
| Gateway TLS | `insecure: true` (기본) | `insecure: false` | Agent-Gateway 간 mTLS 권장 |
| Gateway HA | 단일 인스턴스 | 3+ replicas + PDB | SPOF 제거 |

### 7.5 예시 파이프라인 전체 흐름

```
Consumer App (Django/Celery)
  ├── OTEL_TRACES_SAMPLER=always_on (100% 전송)
  └── OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-agent:4317

OTEL Agent (Sidecar, 128MiB)
  ├── Receivers: otlp (gRPC/HTTP)
  ├── Processors: memory_limiter, batch(500, 5s)
  ├── Exporters: otlp → Gateway (TLS, API Key auth)
  └── Buffering: file_storage (agent crash 시 유실 방지)

OTEL Gateway (Cluster, 512MiB × 3 replicas)
  ├── Receivers: otlp (Bearer Token auth)
  ├── Processors:
  │   ├── memory_limiter (400MiB)
  │   ├── tail_sampling (에러/CB/DLQ 100%, 나머지 1%)
  │   ├── batch (1000, 10s)
  │   ├── resource (service.name, region, cluster_id)
  │   ├── attributes (sensitive data masking)
  │   └── redaction (JWT, IP, API key 패턴 차단)
  ├── Exporters:
  │   ├── otlp/tempo (Traces, file_storage queue)
  │   ├── prometheusremotewrite/mimir (Metrics, WAL buffer)
  │   └── otlphttp/loki (Logs, file_storage queue)
  └── Retry: 1s → 10s, max_elapsed_time 5m
```

---

## 8. 추출 방법

현재 shopping repo 설정에서 추출:

| 예시 파일 | 원본 파일 | 추출 방법 |
|----------|----------|----------|
| django-setup/settings.py | `myproject/settings/base.py` | selfhealing 관련 설정만 추출, `configure_selfhealing()` 래퍼 패턴 적용 |
| django-setup/celery.py | `myproject/celery.py` | `configure_selfhealing_celery(app)` + `setup_selfhealing_signals()` 패턴 적용 |
| django-setup/gunicorn.conf.py | `gunicorn.conf.py` | selfhealing.server helper 사용으로 단순화 |
| k8s/base/*.yaml | `k8s/selfhealing-*.yaml` | namespace 제거, securityContext 추가, `:latest` → placeholder, 보안 하드닝 체크리스트(5.4절) 적용 |
| k8s/overlays/ | 신규 작성 | dev/staging/production overlay 생성, Kustomize 구조 |
| monitoring/prometheus-alerts.yml | `docker/prometheus/rules/alerts.yml` | 그대로 복사 (selfhealing 메트릭 기반) |
| monitoring/grafana-dashboard.json | `docker/grafana/` | JSON export → 프로비저닝용 직접 UID 사용 (6.3절), 수동 Import 시 Sanitizer 실행 |
| monitoring/otel-collector.yml | `docker/otel-collector/` | service.name 일반화, Tail Sampling 정책 추가(7.2절), retry 시간 상향(7.4절) |
| docker/Dockerfile | `Dockerfile` | packages/ 제거, `COPY . .` 후 `pip install .` (non-editable) |
| docker/docker-compose.yml | `docker-compose.yml` | shopping 제거, selfhealing infra만 유지 (Phase 1 범위, 4절) |
| docker/config/tempo.yml | `docker/tempo/tempo.yml` | 한국어 → 영어 주석, 설정 동일 유지 |
| docker/config/mimir.yml | `docker/mimir/mimir.yml` | 한국어 → 영어 주석, 설정 동일 유지 |
| docker/config/loki.yml | `docker/loki/loki.yml` | 한국어 → 영어 주석, 설정 동일 유지 |
| docker/config/grafana-datasources.yml | `docker/grafana/provisioning/datasources/datasource.yml` | 멀티 리전 템플릿 제거, Loki 파생 필드 간소화, Mimir을 기본 DS로 설정 |
| docker/config/grafana-dashboards.yml | `docker/grafana/provisioning/dashboards/dashboard.yml` | 대시보드 경로를 /var/lib/grafana/dashboards 로 변경 |
| scripts/sanitize-dashboards.sh | 신규 작성 | UID/이름/타임존/Exemplar 링크 Sanitizer (6.2절) |
