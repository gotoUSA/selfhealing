# Migration Guide

> `shopping.services.self_healing` → `selfhealing` 패키지 마이그레이션 가이드

## 📋 개요

이 문서는 기존 `shopping.services.self_healing` 모듈에서 새로운 독립 패키지 `selfhealing`으로 
마이그레이션하는 방법을 설명합니다.

## 🚀 빠른 마이그레이션

### 1단계: 패키지 설치

```bash
# requirements.txt에 추가
pip install -e ./packages/selfhealing-python[all]
```

### 2단계: Import 경로 변경

| 이전 (Deprecated) | 새로운 경로 |
|-------------------|-------------|
| `from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService` | `from selfhealing.services import CircuitBreakerService` |
| `from shopping.services.self_healing.dlq_service import DLQService, store_to_dlq` | `from selfhealing.services import DLQService, store_to_dlq` |
| `from shopping.services.self_healing.replay_service import ReplayService` | `from selfhealing.services import ReplayService` |
| `from shopping.services.self_healing.retry_handler import RetryHandler` | `from selfhealing.services import RetryHandler` |
| `from shopping.services.self_healing.backoff_calculator import BackoffCalculator` | `from selfhealing.core import BackoffCalculator` |
| `from shopping.services.self_healing.config import SLAThresholds` | `from selfhealing.core import SLAThresholds` |
| `from shopping.services.self_healing.metrics import get_dlq_pending_count` | `from selfhealing.metrics import get_dlq_pending_count` |
| `from shopping.models.failed_operation import FailedOperation` | `from selfhealing.adapters.django.models import FailedOperation` |
| `from shopping.models.security_incident import SecurityIncident` | `from selfhealing.adapters.django.models import SecurityIncident` |

### 3단계: Django 설정 업데이트

```python
# settings.py
INSTALLED_APPS = [
    # ...
    'selfhealing.adapters.django',  # 추가
]

# Self-Healing 통합 설정 (선택적)
SELFHEALING = {
    'circuit_breaker': {
        'failure_threshold': 5,
        'recovery_timeout': 60,
    },
    'dlq': {
        'max_retries': 3,
        'retention_hours': 72,
    },
}
```

### 4단계: URL 설정 (선택적)

```python
# urls.py
urlpatterns = [
    # ...
    path('api/self-healing/', include('selfhealing.api.django.urls')),
]
```

### 5단계: Celery Beat 스케줄 업데이트 (해당시)

```python
# celery.py
CELERY_BEAT_SCHEDULE = {
    'collect-self-healing-metrics': {
        'task': 'selfhealing.adapters.celery.tasks.collect_metrics',
        'schedule': 60.0,
    },
    'cleanup-expired-dlq': {
        'task': 'selfhealing.adapters.celery.tasks.cleanup_expired',
        'schedule': 3600.0,
    },
}
```

## 📦 세부 모듈별 마이그레이션

### CircuitBreakerService

```python
# 이전
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerService,
    is_circuit_open,
    record_failure,
    record_success,
)

# 새로운
from selfhealing.services import CircuitBreakerService
from selfhealing.core import CircuitState

# 사용법은 동일
cb = CircuitBreakerService(service_name="payment-gateway")
if cb.is_open:
    raise CircuitOpenError()
```

### DLQ Service

```python
# 이전
from shopping.services.self_healing.dlq_service import store_to_dlq, get_dlq_entries

# 새로운
from selfhealing.services import store_to_dlq, get_dlq_entries

# 사용법은 동일
await store_to_dlq(
    domain="payment",
    failure_type="gateway_timeout",
    context={"order_id": 123},
)
```

### Backoff Calculator

```python
# 이전
from shopping.services.self_healing.backoff_calculator import (
    BackoffCalculator,
    BackoffConfig,
)

# 새로운
from selfhealing.core import BackoffCalculator, BackoffConfig

config = BackoffConfig(
    strategy="exponential",
    base_delay=1.0,
    max_delay=300.0,
)
calculator = BackoffCalculator(config)
delay = calculator.calculate(attempt=3)
```

### Metrics

```python
# 이전
from shopping.services.self_healing.metrics import (
    get_dlq_pending_count,
    record_circuit_state,
)

# 새로운
from selfhealing.metrics import (
    get_dlq_pending_count,
    record_circuit_state,
)
```

## ⚠️ 주의사항

### 1. 데이터베이스 마이그레이션

새 패키지를 설치한 후 마이그레이션을 실행해야 합니다:

```bash
python manage.py migrate selfhealing
```

> **참고**: 기존 `FailedOperation`, `SecurityIncident` 모델의 데이터는 
> 새 앱으로 자동 마이그레이션되지 않습니다. 필요시 데이터 마이그레이션 스크립트를 
> 별도로 실행하세요.

### 2. Factory 함수

Factory 함수를 사용하는 경우:

```python
# 이전
from shopping.services.self_healing.factory import get_dlq_service

# 새로운
from selfhealing import get_dlq_service

# 또는 직접 서비스 생성
from selfhealing.services import DLQService
from selfhealing.adapters.django import DjangoFailedOperationRepository

repo = DjangoFailedOperationRepository()
dlq_service = DLQService(repository=repo)
```

### 3. Deprecation 경고

기존 `shopping.services.self_healing` 경로는 당분간 유지되지만,
deprecation 경고가 표시됩니다:

```python
# 이 import는 경고를 발생시킵니다
from shopping.services.self_healing import CircuitBreakerService
# DeprecationWarning: Importing from shopping.services.self_healing is deprecated. 
# Use 'from selfhealing import CircuitBreakerService' instead.
```

## 🧪 마이그레이션 검증

마이그레이션 후 다음 테스트를 실행하여 검증하세요:

```bash
# 새 패키지 테스트
cd packages/selfhealing-python
pytest tests/ -v

# 기존 쇼핑몰 테스트
cd ../..
pytest shopping/tests/unit/self_healing/ -v
pytest shopping/tests/integration/self_healing/ -v
```

## 📞 지원

마이그레이션 중 문제가 발생하면:
1. GitHub Issues 등록
2. 문서 참조: `docs/` 폴더

---

*Last updated: 2025-12-10*
