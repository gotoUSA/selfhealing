# Self-Healing Package Migration Guide

이 문서는 기존 `shopping.services.self_healing` 모듈에서 독립적인 `selfhealing` 패키지로 마이그레이션하는 방법을 설명합니다.

## 📋 개요

`selfhealing` 패키지는 **프레임워크 독립적인** 자가 치유 시스템입니다.
Django, FastAPI, Flask 등 어떤 프레임워크에서든 `pip install selfhealing` 한 번으로 바로 사용할 수 있습니다.

## 🔄 Import 변경 사항

### Repository Adapters

```python
# ❌ 이전 (deprecated)
from shopping.services.self_healing.adapters.django_repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)

# ✅ 새로운 방식
from selfhealing.adapters.django import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)
```

### Core Services

```python
# ❌ 이전 (deprecated)
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerService,
)
from shopping.services.self_healing.dlq_service import DLQService

# ✅ 새로운 방식
from selfhealing.services.circuit_breaker import CircuitBreakerService
from selfhealing.services.dlq_service import DLQService
```

### Chaos Context

```python
# ❌ 이전 (deprecated)
from shopping.services.self_healing.chaos_context import (
    ChaosExperimentContext,
    is_chaos_experiment,
    attach_chaos_context,
)

# ✅ 새로운 방식
from selfhealing.services.chaos_context import (
    ChaosExperimentContext,
    is_chaos_experiment,
    attach_chaos_context,
)
```

### Factory

```python
# ❌ 이전 (deprecated)
from shopping.services.self_healing.factory import create_dlq_service

# ✅ 새로운 방식
from selfhealing.factory import create_dlq_service, ProviderRegistry
```

## 🚀 Django 통합 설정

### 1. INSTALLED_APPS에 추가

```python
# settings.py
INSTALLED_APPS = [
    # ...
    'selfhealing.adapters.django',  # Self-healing Django adapter
    # ...
]
```

### 2. AppConfig에서 설정 (선택적)

기본적으로 `selfhealing` 패키지는 Django 어댑터를 자동으로 감지합니다.
수동으로 설정하려면:

```python
# your_app/apps.py
from django.apps import AppConfig


class YourAppConfig(AppConfig):
    name = 'your_app'

    def ready(self):
        from selfhealing.factory import ProviderRegistry

        # Django를 기본 어댑터로 설정
        ProviderRegistry._default_repo = "django"
```

### 3. 마이그레이션 실행

```bash
python manage.py migrate selfhealing_django
```

## 🔧 FastAPI 통합 설정

```python
# main.py
from fastapi import FastAPI
from selfhealing.factory import create_dlq_service, ProviderRegistry
from selfhealing.adapters.sqlalchemy import SQLAlchemyFailedOperationRepository

app = FastAPI()

# SQLAlchemy 어댑터 등록
ProviderRegistry.register_failed_operation_repo(
    "sqlalchemy",
    SQLAlchemyFailedOperationRepository
)
ProviderRegistry._default_repo = "sqlalchemy"

# DLQ 서비스 생성
dlq_service = create_dlq_service()
```

## ⚠️ Deprecation 경고

마이그레이션 전환 기간 동안, 이전 경로로 import하면 `DeprecationWarning`이 발생합니다:

```
DeprecationWarning: Importing from 'shopping.services.self_healing.adapters'
is deprecated. Please migrate to 'selfhealing.adapters.django'.
```

경고를 억제하려면:

```python
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="shopping.services.self_healing")
```

## 📅 마이그레이션 일정

| 단계 | 날짜 | 설명 |
|------|------|------|
| Phase 1 | 현재 | Deprecation 경고 활성화, 이전 경로 계속 작동 |
| Phase 2 | +3개월 | 경고 레벨을 `PendingDeprecationWarning` → `DeprecationWarning` |
| Phase 3 | +6개월 | 이전 경로 완전 제거 |

## 🔍 마이그레이션 체크리스트

- [ ] 모든 `shopping.services.self_healing.adapters` import를 `selfhealing.adapters.django`로 변경
- [ ] 모든 서비스 import를 `selfhealing.services`로 변경
- [ ] `INSTALLED_APPS`에 `selfhealing.adapters.django` 추가
- [ ] 마이그레이션 실행
- [ ] 테스트 실행하여 deprecation 경고 확인
- [ ] CI/CD에서 deprecation 경고 없음 확인

## 📞 지원

문제가 있으시면 GitHub Issues를 통해 문의하세요.
