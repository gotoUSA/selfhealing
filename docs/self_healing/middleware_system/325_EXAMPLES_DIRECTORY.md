# 325. Examples Directory — 참고용 설정 예시

> **Status**: Planning
> **Severity**: P3 (LOW) — repo 분리 시 함께 생성
> **Target**: `examples/` (selfhealing repo 신규 디렉토리)
> **References**:
> - 319 — Repo Separation Overview (Step 6)
> - 320 — Auto-Configuration (consumer 설정 단순화)

---

## 1. 목적

selfhealing 라이브러리를 사용하는 새 consumer 앱이 빠르게 시작할 수 있도록
Django, Celery, Gunicorn, K8s, Prometheus 설정 예시를 제공한다.

현재 shopping repo의 실제 설정에서 추출하되, 비즈니스 로직을 제거하고 일반화한다.

---

## 2. 디렉토리 구조

```
examples/
├── README.md                     # Quick Start 가이드
├── django-setup/
│   ├── settings.py               # 최소 Django 설정
│   ├── celery.py                 # Celery Beat 연동
│   ├── gunicorn.conf.py          # Gunicorn fork hook
│   └── urls.py                   # URL 설정
├── k8s/
│   ├── deployment.yaml           # Django API 배포
│   ├── celery-worker.yaml        # Celery Worker
│   ├── celery-critical-worker.yaml  # Critical 큐 Worker
│   ├── hpa.yaml                  # HPA
│   ├── pdb.yaml                  # Pod Disruption Budget
│   └── servicemonitor.yaml       # Prometheus ServiceMonitor
├── monitoring/
│   ├── prometheus-alerts.yml     # 알림 규칙
│   ├── prometheus-adapter.yaml   # Custom metrics adapter
│   ├── grafana-dashboard.json    # Grafana 대시보드
│   └── otel-collector.yml        # OTEL Collector
└── docker/
    ├── Dockerfile                # 컨테이너 빌드
    └── docker-compose.yml        # 로컬 개발 환경
```

---

## 3. 핵심 예시 파일

### 3.1 examples/django-setup/settings.py

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
    # selfhealing — 이 1줄로 미들웨어/시그널/OTEL 전부 자동 설정
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
```

### 3.2 examples/django-setup/celery.py

```python
from celery import Celery

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# selfhealing Beat 스케줄 merge (1줄)
from selfhealing.celery_app import (
    SELFHEALING_BEAT_SCHEDULE,
    SELFHEALING_QUEUES,
    SELFHEALING_TASK_ROUTES,
)

app.conf.beat_schedule = {
    # Your app tasks here
}
app.conf.beat_schedule.update(SELFHEALING_BEAT_SCHEDULE)

app.conf.task_queues = {
    "default": {"exchange": "default", "exchange_type": "direct", "routing_key": "default"},
    **SELFHEALING_QUEUES,
}

app.conf.task_routes = {
    **SELFHEALING_TASK_ROUTES,
}
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

## 4. 추출 방법

현재 shopping repo 설정에서 추출:

| 예시 파일 | 원본 파일 | 추출 방법 |
|----------|----------|----------|
| django-setup/settings.py | `myproject/settings/base.py` | selfhealing 관련 설정만 추출, shopping 제거 |
| django-setup/celery.py | `myproject/celery.py` | shopping Beat 태스크 제거, selfhealing merge만 남김 |
| django-setup/gunicorn.conf.py | `gunicorn.conf.py` | selfhealing.server helper 사용으로 단순화 |
| k8s/*.yaml | `k8s/selfhealing-*.yaml` | namespace/label 일반화 |
| monitoring/prometheus-alerts.yml | `docker/prometheus/rules/alerts.yml` | 그대로 복사 (selfhealing 메트릭 기반) |
| monitoring/grafana-dashboard.json | `docker/grafana/` | 대시보드 JSON export |
| monitoring/otel-collector.yml | `docker/otel-collector/` | service.name 일반화 |
| docker/Dockerfile | `Dockerfile` | packages/ 제거, pip install 방식으로 단순화 |
| docker/docker-compose.yml | `docker-compose.yml` | shopping 제거, selfhealing infra만 유지 |
