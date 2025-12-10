# Self-Healing Package Structure

> **Last Updated**: 2025-12-10  
> **Version**: 0.1.0  
> **Status**: Active Development

---

## 📁 Directory Layout

```
packages/selfhealing-python/
├── pyproject.toml                 # 패키지 설정 (dependencies, build)
├── README.md                      # Quick Start & 개요
├── CHANGELOG.md                   # 버전 히스토리
├── LICENSE                        # MIT License
│
├── src/selfhealing/               # 메인 소스 코드
│   ├── __init__.py                # Public API exports
│   │
│   ├── core/                      # 🔵 Pure Python 핵심 로직
│   │   ├── __init__.py
│   │   ├── types.py               # FailureType, OperationStatus, CircuitState
│   │   ├── config.py              # SLAThresholds, 설정 클래스
│   │   ├── backoff.py             # BackoffCalculator (지수 백오프)
│   │   └── forensic.py            # 장애 분석 유틸리티
│   │
│   ├── interfaces/                # 🟡 추상 인터페이스 (ABC)
│   │   ├── __init__.py
│   │   ├── repositories.py        # FailedOperationRepository, CircuitBreakerStateRepository
│   │   ├── payment_provider.py    # PaymentProviderInterface
│   │   ├── cache_provider.py      # CacheProviderInterface, DistributedLock
│   │   ├── task_queue.py          # TaskQueueInterface
│   │   └── web_framework.py       # WebFrameworkInterface
│   │
│   ├── adapters/                  # 🟢 구체적 구현체들
│   │   ├── __init__.py
│   │   │
│   │   ├── django/                # Django ORM 어댑터
│   │   │   ├── __init__.py
│   │   │   ├── models.py          # FailedOperation, CircuitBreakerState 모델
│   │   │   ├── repositories.py    # DjangoFailedOperationRepository 등
│   │   │   ├── admin.py           # Django Admin 등록
│   │   │   └── apps.py            # Django App Config
│   │   │
│   │   ├── cache/                 # 캐시 어댑터
│   │   │   ├── __init__.py
│   │   │   ├── redis_adapter.py   # RedisCacheAdapter
│   │   │   ├── memory_adapter.py  # InMemoryCacheAdapter (테스트용)
│   │   │   └── memcached_adapter.py # MemcachedCacheAdapter
│   │   │
│   │   ├── payments/              # 결제 어댑터
│   │   │   ├── __init__.py
│   │   │   ├── mock_adapter.py    # MockPaymentAdapter (테스트용)
│   │   │   └── stripe_adapter.py  # StripePaymentAdapter
│   │   │
│   │   ├── queues/                # 태스크 큐 어댑터
│   │   │   ├── __init__.py
│   │   │   ├── celery_adapter.py  # CeleryTaskAdapter
│   │   │   ├── rq_adapter.py      # RQTaskAdapter
│   │   │   └── sync_adapter.py    # SyncTaskAdapter (테스트용)
│   │   │
│   │   ├── frameworks/            # 웹 프레임워크 어댑터
│   │   │   ├── __init__.py
│   │   │   └── fastapi_adapter.py # FastAPIAdapter
│   │   │
│   │   └── celery/                # Celery 태스크 정의
│   │       ├── __init__.py
│   │       └── tasks.py           # Self-healing Celery tasks
│   │
│   ├── api/                       # 🟣 REST API 엔드포인트
│   │   ├── __init__.py
│   │   └── django/                # Django REST Framework
│   │       ├── __init__.py
│   │       ├── views.py           # API ViewSets
│   │       ├── serializers.py     # DRF Serializers
│   │       └── urls.py            # URL routing
│   │
│   ├── metrics/                   # 📊 메트릭 수집
│   │   ├── __init__.py
│   │   └── prometheus.py          # Prometheus 메트릭
│   │
│   └── factory.py                 # 🏭 ProviderRegistry & Factory
│
├── tests/                         # 테스트
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures
│   ├── unit/                      # 단위 테스트
│   └── integration/               # 통합 테스트
│
└── docs/                          # 📚 문서
    ├── 0_OVERVIEW/                # 시스템 개요
    ├── 1_REQUIREMENTS/            # 요구사항
    ├── 2_STRATEGY/                # 전략 문서
    ├── 3_EXECUTION/               # 실행 계획
    ├── 4_REPORTING/               # 리포팅
    ├── 5_CONTROL_API/             # Control API 문서
    ├── _reserved_future/          # 미래 계획
    │
    ├── STRUCTURE.md               # 📍 현재 문서
    ├── MIGRATION.md               # 마이그레이션 가이드
    ├── PLUGGABLE_ARCHITECTURE.md  # 플러그인 아키텍처 설계
    ├── CELERY_RETRY_GUIDE.md      # Celery 재시도 가이드
    └── ...
```

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Application Layer                                │
│                   (Django Shopping Mall, FastAPI, etc.)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        selfhealing.factory                               │
│                      ProviderRegistry.get_*()                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   interfaces/   │      │   interfaces/   │      │   interfaces/   │
│  repositories   │      │ cache_provider  │      │   task_queue    │
└────────┬────────┘      └────────┬────────┘      └────────┬────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│ adapters/django │      │ adapters/cache  │      │ adapters/queues │
│  repositories   │      │ redis/memory    │      │ celery/sync/rq  │
└─────────────────┘      └─────────────────┘      └─────────────────┘
```

---

## 📦 Module Descriptions

### `core/` - Pure Python Core

프레임워크 의존성 없는 순수 Python 코드

| 파일 | 설명 |
|------|------|
| `types.py` | `FailureType`, `OperationStatus`, `CircuitState` Enum 정의 |
| `config.py` | `SLAThresholds` 설정 클래스 |
| `backoff.py` | 지수 백오프 계산 (`BackoffCalculator`) |
| `forensic.py` | 장애 원인 분석 유틸리티 |

### `interfaces/` - Abstract Base Classes

외부 의존성을 추상화하는 인터페이스

| 파일 | 주요 클래스 |
|------|-------------|
| `repositories.py` | `FailedOperationRepository`, `CircuitBreakerStateRepository` |
| `cache_provider.py` | `CacheProviderInterface`, `DistributedLock` |
| `payment_provider.py` | `PaymentProviderInterface` |
| `task_queue.py` | `TaskQueueInterface` |
| `web_framework.py` | `WebFrameworkInterface` |

### `adapters/` - Concrete Implementations

인터페이스의 구체적 구현체

| 폴더 | 구현체 | 상태 |
|------|--------|------|
| `django/` | Django ORM 기반 Repository | ✅ 완료 |
| `cache/redis` | Redis 캐시 어댑터 | ✅ 완료 |
| `cache/memory` | 인메모리 캐시 (테스트용) | ✅ 완료 |
| `cache/memcached` | Memcached 어댑터 | ✅ 완료 |
| `queues/celery` | Celery 태스크 큐 | ✅ 완료 |
| `queues/sync` | 동기 실행 (테스트용) | ✅ 완료 |
| `queues/rq` | Redis Queue 어댑터 | ✅ 완료 |
| `payments/mock` | Mock 결제 (테스트용) | ✅ 완료 |
| `payments/stripe` | Stripe 결제 | ✅ 완료 |
| `frameworks/fastapi` | FastAPI 어댑터 | ✅ 완료 |

### `api/` - REST API Endpoints

Self-Healing Control API

| 엔드포인트 | 설명 |
|-----------|------|
| `POST /api/self-healing/control/` | 제어 액션 실행 |
| `GET /api/self-healing/status/` | 서비스 상태 조회 |
| `POST /api/self-healing/dlq/replay/` | DLQ 일괄 재처리 |
| `GET /api/self-healing/metrics/` | 메트릭 조회 |

### `factory.py` - Provider Registry

```python
from selfhealing.factory import ProviderRegistry

# 프로바이더 가져오기
cache = ProviderRegistry.get_cache("redis")
queue = ProviderRegistry.get_queue("celery")

# 기본값 설정
ProviderRegistry.set_defaults(
    cache="memory",   # 테스트 환경
    queue="sync",     # 동기 실행
)
```

---

## 🔗 Related Documents

| 문서 | 설명 |
|------|------|
| [MIGRATION.md](./MIGRATION.md) | 기존 코드 마이그레이션 가이드 |
| [PLUGGABLE_ARCHITECTURE.md](./PLUGGABLE_ARCHITECTURE.md) | 플러그인 아키텍처 설계 |
| [CELERY_RETRY_GUIDE.md](./CELERY_RETRY_GUIDE.md) | Celery 재시도 전략 |
| [L3_SELF_HEALING_SYSTEM.md](./L3_SELF_HEALING_SYSTEM.md) | 시스템 상세 설명 |
| [0_OVERVIEW/](./0_OVERVIEW/) | 개요 문서 |
| [5_CONTROL_API/](./5_CONTROL_API/) | Control API 상세 |

---

## 📊 Import Examples

```python
# Core types
from selfhealing import FailureType, OperationStatus, CircuitState

# Factory
from selfhealing.factory import ProviderRegistry

# Interfaces (for type hints)
from selfhealing.interfaces import (
    FailedOperationRepository,
    CacheProviderInterface,
    TaskQueueInterface,
)

# Django Adapters
from selfhealing.adapters.django import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
)

# Cache Adapters
from selfhealing.adapters.cache import (
    RedisCacheAdapter,
    InMemoryCacheAdapter,
)

# Queue Adapters
from selfhealing.adapters.queues import (
    CeleryTaskAdapter,
    SyncTaskAdapter,
)
```

---

*Last Updated: 2025-12-10*
