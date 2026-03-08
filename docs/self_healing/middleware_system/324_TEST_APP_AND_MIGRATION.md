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
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.testapp.settings")

SECRET_KEY = "test-secret-key-for-selfhealing"

INSTALLED_APPS = [
    "django.contrib.admin",
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
    pass


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
