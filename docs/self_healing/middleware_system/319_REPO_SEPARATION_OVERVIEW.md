# 319. Repository Separation — selfhealing 라이브러리 완전 분리

> **Status**: Planning
> **Severity**: P1 (HIGH) — 아키텍처 기반 변경
> **Target**: selfhealing-python 독립 repo + shopping consumer repo
> **References**:
> - 223 — Host App Decoupling (모델/Admin/management 명령 패키지 이관, 완료)
> - 316 — Gunicorn Preload Optimization (fork hook 구조)
> - 317 — Orphan Service Wiring (Celery Beat 태스크 등록)

---

## 1. 목표

selfhealing을 독립 Python 라이브러리로 분리하여 `pip install selfhealing[django,celery]`만으로
모든 기능이 동작하도록 한다. shopping(테스트베드)은 consumer로서 이 라이브러리를 dependency로 설치한다.

### 1.1 현재 구조 (Monorepo)

```
myproject/                          ← 단일 repo
├── packages/selfhealing-python/    ← 라이브러리 (Hatchling 빌드)
├── shopping/                       ← 테스트베드
├── myproject/                      ← Django 설정 (selfhealing 설정 50+ 줄 포함)
├── tests/                          ← selfhealing 통합 테스트 181파일
├── docs/                           ← selfhealing 문서 전체
├── k8s/                            ← K8s 매니페스트 (selfhealing 16파일)
├── docker/                         ← Prometheus/Grafana/OTEL 설정
└── scripts/                        ← selfhealing 분석 스크립트
```

### 1.2 목표 구조 (2-Repo)

```
[Repo 1] selfhealing-python/          ← 독립 라이브러리
├── src/selfhealing/                   ← 소스코드 (변경 최소)
├── tests/
│   ├── unit/                          ← 기존 1,018 유닛 테스트
│   ├── integration/                   ← tests/self_healing/ 이동 (shopping import 제거)
│   ├── chaos/                         ← 카오스 테스트
│   └── testapp/                       ← 최소 Django 테스트용 앱
├── docs/                              ← 전체 문서 이동
├── examples/                          ← 참고용 설정 예시 (Django, K8s, Prometheus)
├── scripts/                           ← 분석 스크립트
├── pyproject.toml                     ← 이미 존재 (Hatchling)
└── .github/workflows/ci.yml           ← 라이브러리 CI (lint + unit test)

[Repo 2] shopping/                     ← 테스트베드 (consumer)
├── shopping/                          ← 앱 비즈니스 로직
├── myproject/
│   ├── settings/                      ← INSTALLED_APPS 1줄 + 도메인 설정
│   ├── celery.py                      ← Beat schedule 1줄 merge
│   └── urls.py                        ← URL include 1줄
├── tests/hybrid/                      ← shopping+selfhealing 결합 테스트만
├── k8s/                               ← 실제 배포 매니페스트
├── docker/                            ← 실제 모니터링 설정
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml                     ← dependencies: selfhealing[django,celery]
└── .github/workflows/ci.yml
```

---

## 2. 커플링 분석 (234파일, 6개 카테고리)

### 2.1 분리 전 해결 필수 (코드 변경 필요)

| # | 위치 | 현재 상태 | 목표 | 문서 |
|---|------|----------|------|------|
| C1 | `myproject/settings/base.py` MIDDLEWARE 10+줄 | consumer가 미들웨어 경로 직접 나열 | auto-config: `INSTALLED_APPS` 1줄로 자동 삽입 | 320 |
| C2 | `myproject/celery.py` Beat 15+태스크 | consumer가 태스크명/스케줄 하드코딩 | selfhealing이 기본 Beat schedule dict 제공 | 321 |
| C3 | `myproject/celery.py` 시그널 훅 | consumer가 `setup_selfhealing_signals()` 직접 호출 | `AppConfig.ready()`에서 자동 등록 | 320 |
| C4 | `gunicorn.conf.py` fork hook 8개 | consumer가 selfhealing import 8개 직접 작성 | selfhealing이 hook helper 함수 1개 제공 | 322 |
| C5 | `myproject/settings/base.py` EXCEPTION_HANDLER | consumer가 selfhealing 예외 핸들러 경로 하드코딩 | auto-config에서 자동 설정 | 320 |
| C6 | `myproject/wsgi.py` get_config() | consumer가 직접 호출 | AppConfig.ready()에서 자동 처리 | 320 |

### 2.2 그대로 유지 (import 경로 불변)

| 카테고리 | 파일 수 | 이유 |
|----------|---------|------|
| shopping 비즈니스 코드 (4파일) | 4 | `selfhealing.*` import 경로 동일 |
| K8s 매니페스트 (16파일) | 16 | 배포 설정은 consumer repo에 유지 |
| Prometheus/Grafana/OTEL (4파일) | 4 | 모니터링 설정은 consumer repo에 유지 |
| 환경변수 `.env` | 1 | `SELFHEALING_*` 접두사 유지 |
| Dockerfile/docker-compose | 2 | `pip install -e packages/...` → `pip install selfhealing` 변경만 |

### 2.3 이동 대상

| 대상 | 원본 위치 | 이동 위치 | 비고 |
|------|----------|----------|------|
| docs 전체 | `docs/` | selfhealing repo `docs/` | shopping 문서 없음 (삭제됨) |
| 통합 테스트 | `tests/self_healing/` (123파일) | selfhealing repo `tests/integration/` | shopping import 37파일 → testapp으로 전환 |
| 통합 테스트 | `tests/integration/selfhealing/` | selfhealing repo `tests/integration/` | |
| API 테스트 | `tests/api/` | selfhealing repo `tests/integration/api/` | |
| 부하 테스트 | `tests/load/` | selfhealing repo `tests/load/` | |
| 혼합 테스트 | `tests/hybrid/` (12파일) | shopping repo `tests/hybrid/` | shopping+selfhealing 모두 필요 |
| 분석 스크립트 | `scripts/analyze_*.py`, `scripts/verify_wiring.py` | selfhealing repo `scripts/` | |
| K8s 매니페스트 | `k8s/` | shopping repo `k8s/` (유지) + selfhealing `examples/k8s/` (참고용 복사) | |
| Prometheus | `docker/prometheus/` | shopping repo (유지) + selfhealing `examples/monitoring/` | |
| Grafana | `docker/grafana/` | shopping repo (유지) + selfhealing `examples/monitoring/` | |
| OTEL | `docker/otel-collector/` | shopping repo (유지) + selfhealing `examples/monitoring/` | |

---

## 3. 실행 계획 (8 Step)

### Phase A: Decoupling (현재 monorepo에서 수행, 테스트 통과 확인 후 분리)

| Step | 내용 | 문서 | 예상 변경 |
|------|------|------|----------|
| **Step 1** | Django Auto-Configuration | 320 | `AppConfig.ready()` 확장, 미들웨어 자동 삽입, 시그널 자동 등록 |
| **Step 2** | Celery Beat Schedule 내부화 | 321 | selfhealing 내부에 `BEAT_SCHEDULE` dict 정의, consumer 1줄 merge |
| **Step 3** | Gunicorn Hook Helper | 322 | selfhealing이 `post_fork_reset()` 1개 함수 제공 |
| **Step 4** | Public API 정의 | 323 | `selfhealing/__init__.py`에 안정적 re-export |

### Phase B: 테스트 이관

| Step | 내용 | 문서 | 예상 변경 |
|------|------|------|----------|
| **Step 5** | Test App 생성 + 테스트 이관 | 324 | `tests/testapp/` 최소 Django 앱, shopping import 37파일 전환 |

### Phase C: 물리적 분리

| Step | 내용 | 문서 | 예상 변경 |
|------|------|------|----------|
| **Step 6** | Examples 디렉토리 생성 | 325 | Django, K8s, Prometheus, Docker 예시 |
| **Step 7** | Repo 분리 실행 | 326 | git filter-branch / 새 repo 생성, docs/scripts 이동 |
| **Step 8** | CI/CD 분리 + 배포 전략 | 327 | 라이브러리 CI, consumer CI, Git dependency 설정 |

### 의존성 그래프

```
Step 1 (Auto-Config) ──┐
Step 2 (Beat)          ├──→ Step 5 (Test App) ──→ Step 7 (Repo 분리) ──→ Step 8 (CI)
Step 3 (Gunicorn)      │                                ↑
Step 4 (Public API) ───┘                    Step 6 (Examples) ─────────┘
```

---

## 4. Consumer 설정 비교 (Before / After)

### 4.1 Before: 현재 consumer가 해야 하는 것

```python
# settings/base.py — 미들웨어 10+줄 하드코딩
MIDDLEWARE = [
    "selfhealing.audit.trace.trace_id_middleware",
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.tiering.TieringMiddleware",
    "selfhealing.api.django.middleware.IPBanMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
    # ... + 6개 더
]

# REST_FRAMEWORK EXCEPTION_HANDLER 직접 지정
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "selfhealing.api.django.exceptions.handler.selfhealing_exception_handler",
}

# OTEL 수동 초기화
from selfhealing.observability import initialize_opentelemetry
initialize_opentelemetry()
```

```python
# celery.py — Beat 태스크 15+개, 큐 4개, 라우팅 4개 하드코딩
app.conf.beat_schedule = {
    "check-circuit-breaker-recovery": {
        "task": "selfhealing.celery_tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,
    },
    # ... 14개 더
}

# 시그널 수동 설정
from selfhealing.adapters.celery import setup_selfhealing_signals
setup_selfhealing_signals(app=app, ...)
```

```python
# gunicorn.conf.py — fork hook 8개 import
from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
from selfhealing.adapters.kafka.producer import reset_kafka_producer
from selfhealing.observability import reset_opentelemetry
from selfhealing.adapters.ipc import reset_cb_state_snapshot
# ... 4개 더
```

### 4.2 After: 분리 후 consumer가 해야 하는 것

```python
# settings/base.py — 1줄
INSTALLED_APPS = [
    "selfhealing.adapters.django",    # 미들웨어, OTEL, 시그널 전부 자동
]

# 도메인 설정 (비즈니스 로직이므로 consumer가 정의하는 것이 올바름)
SELFHEALING_CORE_DOMAINS = ["payment", "order", "inventory"]
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [r"^/api/orders/", r"^/api/payments/"]
SELF_HEALING_DOMAIN_MAPPING = {"/payments/": "payment", "/orders/": "order"}

# 특정 미들웨어 비활성화 (선택사항, 기본값 전부 True)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

```python
# celery.py — 1줄
from selfhealing.celery_app import SELFHEALING_BEAT_SCHEDULE
app.conf.beat_schedule.update(SELFHEALING_BEAT_SCHEDULE)
# 시그널은 AppConfig.ready()에서 자동 등록됨
```

```python
# gunicorn.conf.py — 1줄
def post_fork(server, worker):
    from selfhealing.server import post_fork_reset
    post_fork_reset(worker)
```

```python
# urls.py — 1줄 (현재와 동일)
path("api/self-healing/", include("selfhealing.api.django.urls")),
```

```toml
# pyproject.toml
[project]
dependencies = [
    "selfhealing[django,celery,prometheus] @ git+https://github.com/USER/selfhealing-python.git",
]
```

---

## 5. examples/ 디렉토리 구조

selfhealing 라이브러리 repo에 참고용 설정 예시를 제공한다.
실제 shopping repo의 설정에서 추출하되, 비즈니스 로직 제거 후 일반화.

```
examples/
├── django-setup/
│   ├── README.md                 # Quick Start 가이드
│   ├── settings.py               # 최소 Django 설정 예시
│   ├── celery.py                 # Celery Beat 연동 예시
│   ├── gunicorn.conf.py          # Gunicorn fork hook 예시
│   └── urls.py                   # URL 설정 예시
├── k8s/
│   ├── deployment.yaml           # Django API 배포
│   ├── celery-worker.yaml        # Celery Worker 배포
│   ├── celery-critical-worker.yaml  # Critical 큐 Worker
│   ├── hpa.yaml                  # HPA 설정
│   ├── pdb.yaml                  # Pod Disruption Budget
│   └── servicemonitor.yaml       # Prometheus ServiceMonitor
├── monitoring/
│   ├── prometheus-alerts.yml     # 알림 규칙 예시
│   ├── prometheus-adapter.yaml   # Custom metrics adapter
│   ├── grafana-dashboard.json    # Grafana 대시보드
│   └── otel-collector.yml        # OTEL Collector 설정
└── docker/
    ├── Dockerfile                # 컨테이너 빌드 예시
    └── docker-compose.yml        # 로컬 개발 환경 예시
```

---

## 6. 테스트 이관 전략

### 6.1 현재 테스트 분포

| 위치 | 파일 수 | shopping import | 어디로 |
|------|---------|-----------------|--------|
| `packages/selfhealing-python/tests/unit/` | 1,018 | 0 | selfhealing `tests/unit/` (그대로) |
| `tests/self_healing/` | 123 | 37 | selfhealing `tests/integration/` (shopping→testapp 전환) |
| `tests/integration/selfhealing/` | 3 | 1 | selfhealing `tests/integration/` |
| `tests/api/` | 6 | 0 | selfhealing `tests/integration/api/` |
| `tests/hybrid/` | 12 | 12 | shopping `tests/hybrid/` (유지) |
| `tests/load/` | ? | 확인 필요 | selfhealing `tests/load/` |

### 6.2 testapp 전략

shopping을 import하는 37개 테스트의 import 유형:

1. **Django ORM 모델 참조** (FailedOperation 등) → selfhealing 내부 모델 사용 (223에서 이미 이관됨)
2. **shopping factory** (OrderFactory 등) → testapp에 최소 팩토리 정의
3. **shopping settings** (DB, Redis URL 등) → testapp settings로 대체
4. **shopping task** (payment_tasks 등) → testapp에 더미 태스크 정의

```
tests/testapp/
├── __init__.py
├── settings.py       # 최소 Django 설정 (SQLite + fake Redis)
├── models.py         # 테스트용 최소 모델
├── factories.py      # 테스트용 팩토리
├── tasks.py          # 테스트용 더미 Celery 태스크
└── urls.py           # 테스트용 URL
```

---

## 7. 배포 전략

### 7.1 단계별 전환

| 단계 | 방법 | 장점 | 단점 |
|------|------|------|------|
| **초기** | Git dependency | 설정 간단, 즉시 사용 | 빌드 시 clone, 느림 |
| **안정화 후** | GitHub Releases + wheel | 빠른 설치, 버전 관리 | 수동 릴리스 |
| **최종** | Private PyPI (TestPyPI 또는 self-hosted) | 표준 pip workflow | 인프라 필요 |

### 7.2 Git Dependency 설정

```toml
# shopping/pyproject.toml
[project]
dependencies = [
    "selfhealing[django,celery,prometheus] @ git+https://github.com/USER/selfhealing-python.git@v0.1.0",
]
```

### 7.3 Dockerfile 변경

```dockerfile
# Before
COPY packages/ /code/packages/
RUN pip install --no-cache-dir -e /code/packages/selfhealing-python
ENV PYTHONPATH=/code:/code/packages/selfhealing-python/src

# After
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/code
# selfhealing은 pyproject.toml dependencies에서 자동 설치
```

---

## 8. 하위 문서 목록

| # | 문서 | 내용 | 선행 |
|---|------|------|------|
| 320 | `320_AUTO_CONFIGURATION.md` | AppConfig 미들웨어 자동 삽입, 시그널 자동 등록, OTEL 자동 초기화, EXCEPTION_HANDLER 자동 설정 | - |
| 321 | `321_CELERY_BEAT_INTERNALIZATION.md` | selfhealing 내부 Beat schedule dict, 큐/라우팅 정의, consumer merge API | - |
| 322 | `322_GUNICORN_HOOK_HELPER.md` | `post_fork_reset()`, `post_worker_init_helper()`, `worker_exit_helper()` 통합 함수 | - |
| 323 | `323_PUBLIC_API_DEFINITION.md` | `selfhealing/__init__.py` re-export, 안정적 public API 명세 | - |
| 324 | `324_TEST_APP_AND_MIGRATION.md` | testapp 생성, shopping import 37파일 전환, 테스트 이동 | 320, 321 |
| 325 | `325_EXAMPLES_DIRECTORY.md` | Django/K8s/Prometheus/Docker 예시 파일 생성 | 320, 321, 322 |
| 326 | `326_REPO_SPLIT_EXECUTION.md` | Git 히스토리 분리, docs/scripts 이동, 파일 정리 | 324, 325 |
| 327 | `327_CI_CD_AND_DEPLOYMENT.md` | 라이브러리 CI, consumer CI, Git dependency, 버전 관리 | 326 |
