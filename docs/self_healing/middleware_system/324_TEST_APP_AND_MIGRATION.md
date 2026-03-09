# 324. Test App and Test Migration — 통합 테스트 이관

> **Status**: Planning
> **Severity**: P2 (MEDIUM) — repo 분리 선행 조건
> **Target**: `packages/selfhealing-python/tests/testapp/` (신규)
> **References**:
> - 319 — Repo Separation Overview (Step 5)
> - 223 — Host App Decoupling (모델 이관 완료)

---

## 1. 현황 및 문제

### 1.1 전역 `tests/` 디렉토리 현황

| 위치 | 파일 수 | shopping import | 분리 후 위치 |
|------|---------|-----------------|--------------|
| `tests/self_healing/` | 123 | 37 | selfhealing `tests/integration/` |
| `tests/integration/selfhealing/` | 3 | 1 | selfhealing `tests/integration/` |
| `tests/api/` | 6 | 0 | selfhealing `tests/integration/api/` |
| `tests/hybrid/` | 12 | 12 | shopping `tests/hybrid/` (유지) |
| `tests/load/` | ? | 확인 필요 | selfhealing `tests/load/` |

### 1.2 문제

37개 테스트가 `shopping`을 import한다.
이 테스트들을 selfhealing repo로 이동하려면 shopping 의존성을 제거해야 한다.

---

## 2. testapp 설계

### 2.1 목적

selfhealing의 Django 통합 테스트에서 `shopping` 대신 사용할 최소 Django 앱.
모델, 팩토리, 태스크를 제공하되, 비즈니스 로직은 없다.

### 2.2 구조

```
tests/testapp/
├── __init__.py
├── settings.py           # 최소 Django 설정
├── models.py             # 테스트용 최소 모델
├── factories.py          # 테스트용 팩토리 (factory_boy)
├── tasks.py              # 테스트용 더미 Celery 태스크
├── urls.py               # 테스트용 URL
└── migrations/
    └── 0001_initial.py   # 자동 생성
```

### 2.3 settings.py

```python
# tests/testapp/settings.py
SECRET_KEY = "test-secret-key-for-selfhealing"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "selfhealing.adapters.django",
    "tests.testapp",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "testapp.TestUser"

ROOT_URLCONF = "tests.testapp.urls"

MIDDLEWARE = [
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# selfhealing 최소 설정
SELFHEALING_CORE_DOMAINS = ["payment", "order", "test"]
SELFHEALING_AUTO_MIDDLEWARE = False  # 테스트에서는 수동 제어
```

### 2.4 models.py

```python
# tests/testapp/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser


class TestUser(AbstractUser):
    """테스트용 User 모델."""

    class Meta:
        app_label = "testapp"


class TestOrder(models.Model):
    """테스트용 주문 모델 (shopping.Order 대체)."""
    user = models.ForeignKey(TestUser, on_delete=models.CASCADE)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "testapp"
```

### 2.5 tasks.py

```python
# tests/testapp/tasks.py
"""테스트용 더미 Celery 태스크 (shopping.tasks 대체)."""
from celery import shared_task


@shared_task
def dummy_payment_task(order_id: int):
    """테스트용 결제 태스크."""
    return {"order_id": order_id, "status": "processed"}


@shared_task
def dummy_order_task(order_id: int):
    """테스트용 주문 태스크."""
    return {"order_id": order_id, "status": "completed"}
```

---

## 3. shopping import 전환 전략

37개 테스트의 shopping import를 분류하면:

### 3.1 모델 참조 (대부분)

```python
# Before
from shopping.models import Order, User

# After
from tests.testapp.models import TestOrder, TestUser
```

### 3.2 팩토리 참조

```python
# Before
from shopping.tests.factories import OrderFactory, UserFactory

# After
from tests.testapp.factories import TestOrderFactory, TestUserFactory
```

### 3.3 Django settings 참조

```python
# Before
from django.conf import settings  # shopping settings 가정

# After
# conftest.py에서 @pytest.mark.django_db(settings="tests.testapp.settings")
```

### 3.4 shopping 비즈니스 로직 참조

shopping 비즈니스 로직을 직접 테스트하는 테스트 → **shopping repo에 남김** (hybrid/).

### 3.5 Import 패턴 기반 분류 기준

37개 파일을 분류할 때, 테스트가 import하는 모듈로 판단한다:

```
├─ selfhealing.* 만 import → selfhealing repo (tests/integration/)
│   라이브러리 순수 로직 검증
│   예: CB 상태 전이, DLQ 저장/재생, Retry 정책 단독 검증
│
├─ shopping.* + selfhealing.* 양쪽 import → shopping repo (tests/hybrid/)
│   소비자 계약 테스트 (Consumer Contract Test)
│   예: Toss API 실패 → CB OPEN → 결제 차단 연동 시나리오
│
└─ shopping.* 만 import → shopping repo (tests/unit/ 또는 tests/integration/)
    순수 비즈니스 로직 검증
```

§5 step 2(37파일 분석)에서 이 트리를 적용한다.

---

## 4. 파일 이동 계획

### 4.1 selfhealing repo로 이동

```
현재 위치                              → selfhealing repo 위치
tests/self_healing/adapters/           → tests/integration/adapters/
tests/self_healing/admin/              → tests/integration/admin/
tests/self_healing/api/                → tests/integration/api/
tests/self_healing/audit/              → tests/integration/audit/
tests/self_healing/chaos/              → tests/integration/chaos/
tests/self_healing/circuit_breaker/    → tests/integration/circuit_breaker/
tests/self_healing/config/             → tests/integration/config/
tests/self_healing/dashboard/          → tests/integration/dashboard/
tests/self_healing/django/             → tests/integration/django/
tests/self_healing/drift/              → tests/integration/drift/
tests/self_healing/e2e/                → tests/integration/e2e/
tests/self_healing/integration/        → tests/integration/integration/
tests/self_healing/metrics/            → tests/integration/metrics/
tests/self_healing/notification/       → tests/integration/notification/
tests/self_healing/replay/             → tests/integration/replay/
tests/self_healing/resilience/         → tests/integration/resilience/
tests/self_healing/security/           → tests/integration/security/
tests/self_healing/services/           → tests/integration/services/
tests/self_healing/tasks/              → tests/integration/tasks/
tests/self_healing/unit_adapters/      → tests/integration/unit_adapters/
tests/self_healing/utils/              → tests/integration/utils/
tests/api/                             → tests/integration/api/ (merge)
tests/integration/selfhealing/         → tests/integration/ (merge)
```

### 4.2 shopping repo에 남김

```
tests/hybrid/                          → shopping tests/hybrid/ (유지)
tests/conftest.py                      → shopping tests/conftest.py (유지)
tests/factories/                       → shopping tests/factories/ (유지)
```

---

## 5. 실행 순서

1. testapp 생성 (`tests/testapp/`)
2. shopping import 37파일 분석 → 각 파일별 전환 방법 결정
3. 모델/팩토리/settings 참조를 testapp으로 전환
4. shopping 비즈니스 로직 의존 테스트 → hybrid/로 이동
5. 전환 완료 후 전체 테스트 실행 확인
6. 파일 이동 (현재 위치 → selfhealing repo 구조)

---

## 6. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | testapp 독립 실행 | `pytest tests/ --ds=tests.testapp.settings` 성공 |
| 2 | shopping import 0개 | selfhealing tests/에서 `from shopping` grep 결과 0 |
| 3 | 기존 테스트 전체 통과 | 전환 후 기존 테스트 결과 동일 |

---

## 7. 설계 리뷰 결정사항

### 7.1 CI 파이프라인 — 테스트 계층 Job 분리

**결정**: ✅ 채택

`pyproject.toml`에 이미 정의된 4-tier 마커 체계(`tier1`~`tier4_load`)를 CI 워크플로우에 반영한다.

| CI 단계 | 티어 | DB 엔진 | 인프라 | 대상 |
|---------|------|---------|-------|------|
| PR 검증 | tier1 | SQLite / InMemory | 없음 | testapp 기반 어댑터 바인딩 + 순수 단위 테스트 |
| Merge 전 | tier2 | Docker PostgreSQL 15 | Redis 7, Celery | `requires_db`, `requires_redis` 마커 테스트 |
| Nightly | tier3_chaos | Docker 전체 | 전체 인프라 | 카오스 엔지니어링, 부하 테스트 |

testapp이 완성되면 PR 단계에서 Docker 인프라를 제거하여 피드백을 2분 이내로 단축한다. 현재 `django-ci.yml`이 PR에서도 PostgreSQL + Redis를 띄우는 구조는 testapp 전환 후 개선 대상이다.

### 7.2 Redis / Kafka Teardown — State Leak 방지

**결정**: ✅ 채택 (기존 구현 견고, Saga 키 prefix 1건 수정)

기존 구현의 3중 Redis 정리 전략은 충분하다:

| 패턴 | 위치 | 설명 |
|------|------|------|
| Session flushdb | `tests/conftest.py:160` | 세션 종료 시 전체 DB flush |
| Function pre/post flush | `tests/conftest.py:265-267` | 매 테스트 전후 flush |
| Pattern-based delete | `tests/conftest.py:189-191` | `test:selfhealing:*` 키만 삭제 |

Kafka도 고유 topic prefix(`test.{uuid}.*`)와 `consumer_auto_offset_reset="earliest"`로 격리된다.

**수정 필요 1건**: `test_saga_orchestrator_integration.py`의 `KEY_PREFIX = "selfhealing:state:"`가 `test:` prefix를 사용하지 않아 프로덕션 키와 충돌 가능. 이관 시 `test:selfhealing:state:`로 변경한다.

### 7.3 미들웨어 검증용 Dummy Views

**결정**: ✅ 채택

§2.2 구조에 `views.py`가 누락되어 있으므로 추가한다. 다음 더미 뷰로 미들웨어 커버리지를 확보한다:

| 뷰 | 반환 | 검증 대상 |
|----|------|----------|
| `SuccessView` | 200 OK | 정상 경로 미들웨어 체인 |
| `ErrorView` | 500 raise | CB 장애 감지, HealthBridge |
| `SlowView` | 지연 후 200 | 타임아웃, Pool 소진 |
| `RateLimitTestView` | 429 | HybridRateLimitMiddleware L1/L2 전환 |
| `TieredEndpointView` | 200 (헤더 분기) | TieringMiddleware API 티어 분류 |

### 7.4 Celery 더미 태스크 확장 — 장애 시나리오

**결정**: ✅ 채택

§2.5의 `tasks.py`에 항상 성공하는 태스크만 존재. CB/DLQ 극한 시나리오 검증을 위해 3종 추가:

| 태스크 | 동작 | 검증 대상 |
|--------|------|----------|
| `always_failing_task` | 즉시 `raise RuntimeError` | CB failure_threshold 도달, DLQ 저장 |
| `deterministic_failing_task` | `failure_rate` 파라미터로 실패율 제어 | CB half-open 판정 |
| `slow_task` | `time.sleep(delay)` + `soft_time_limit` | 워커 타임아웃, 데몬 스레드 상호작용 |

`random` 사용은 테스트 재현성을 해치므로, `failure_rate=1.0`(항상 실패) / `0.0`(항상 성공)으로 결정적 사용을 원칙으로 한다.

### 7.5 Testing Utilities 공개 여부

**결정**: ❌ 불채택

selfhealing은 Consumer가 블랙박스로 사용하는 판매용 라이브러리다. 싱글톤 리셋, ContextVar 격리 같은 내부 테스트 유틸리티를 Consumer에게 노출할 이유가 없다. Consumer가 이런 것을 알아야 한다면 라이브러리 설계 결함이다. 테스트 유틸리티는 라이브러리 내부 전용으로 유지한다. 이관 시 `tests/_fixtures/`로 모듈 분리하는 것은 내부 정리 차원에서 유효하다.

### 7.6 SQLite 사용 범위 명확화

**결정**: ✅ 현행 유지

§2.3의 SQLite in-memory DB는 testapp의 어댑터 바인딩 검증용으로 적합하다. `select_for_update` 같은 DB 종속 기능은 `@pytest.mark.requires_db` + Docker PostgreSQL 15로 테스트하며, 이 문서의 testapp 범위에 포함하지 않는다.
