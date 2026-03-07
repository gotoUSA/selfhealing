# 311. Interface Contract Integrity — 인터페이스 계약 정합성 확보

> **Status**: Refactor
> **Severity**: P1 (HIGH)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/interfaces/repositories.py` — 메서드 중복
> - `packages/selfhealing-python/src/selfhealing/interfaces/__init__.py` — export 누락
> - `packages/selfhealing-python/src/selfhealing/interfaces/web_framework.py` — 구현체 없음
> - `packages/selfhealing-python/src/selfhealing/adapters/cache/` — 어댑터 간 기능 불균형
> **References**:
> - 309 — 아키텍처 패턴 통일
> - 310 — 기능 중복 제거

---

## 1. 현황 및 문제

15개 인터페이스와 23개 어댑터 사이에 **계약 위반, 중복 정의, 구현 누락, 기능 불균형**이 존재한다.

---

## 2. 불일치 상세 분석

### 2.1 CircuitBreakerStateRepository 메서드 중복 (P1)

`repositories.py`에서 동일한 시그니처와 docstring을 가진 메서드가 2개 정의되어 있다.

```python
# interfaces/repositories.py:647-654

@abstractmethod
def get_all(self) -> list[CircuitBreakerStateData]:
    """Get all circuit breaker states"""
    ...

@abstractmethod
def get_all_states(self) -> list[CircuitBreakerStateData]:
    """Get all circuit breaker states"""
    ...
```

**문제**:
- 모든 구현체(Redis, InMemory)가 두 메서드를 모두 구현해야 함
- 호출자 입장에서 어떤 메서드를 사용해야 하는지 혼란
- 실제로 한쪽은 다른쪽을 호출하는 wrapper인 경우가 대부분

**사용 현황**:

| 메서드 | 호출 위치 |
|--------|-----------|
| `get_all()` | `adapters/ipc/cb_state_snapshot.py`, `adapters/memory/layered_repository/` |
| `get_all_states()` | `adapters/django/statistics.py`, `adapters/celery/tasks/circuit_breaker.py`, `services/` |

---

### 2.2 구현체 없는 인터페이스 (P1)

| 인터페이스 | 구현체 수 | 상세 |
|-----------|----------|------|
| `WebFrameworkInterface` | **0** | 정의만 있고 어댑터 없음. Django 직접 사용 중 |
| `SecurityIncidentRepository` | **1** (InMemory만) | Redis/Django 구현 없음 |
| `ConfigProviderInterface` | **2** (DictConfig, EnvConfig) | 기본 구현만 존재, 인터페이스 파일 내부에 정의 |

**WebFrameworkInterface 분석**:

```python
# interfaces/web_framework.py:420-
class WebFrameworkInterface(ABC):
    @abstractmethod
    def create_router(self, prefix: str, tags: list[str]) -> Any: ...
    @abstractmethod
    def add_route(self, router: Any, path: str, ...) -> None: ...
    @abstractmethod
    def to_request_context(self, raw_request: Any) -> RequestContext: ...
    @abstractmethod
    def from_response_context(self, ctx: ResponseContext) -> Any: ...
```

현재 Django가 직접 사용되고 있어 이 인터페이스의 실질적 사용처가 없다.

---

### 2.3 interfaces/__init__.py Export 누락 (P2)

`interfaces/__init__.py`에서 일부 인터페이스가 import/export 되지 않는다.

| 인터페이스 | `__init__.py`에서 import | `__all__`에 포함 |
|-----------|------------------------|-----------------|
| `EventJournalRepository` | X | X |
| `NotificationAdapter` | X | X |
| `NotificationSeverity` | X | X |
| `NotificationChannel` | X | X |
| `Notification` | X | X |

**영향**:
- `from selfhealing.interfaces import EventJournalRepository` 불가
- 사용자는 `from selfhealing.interfaces.event_journal import EventJournalRepository`로 직접 접근 필요
- 인터페이스 목록의 "공식 카탈로그" 역할이 불완전

---

### 2.4 어댑터 간 기능 불균형 (P2)

동일 인터페이스의 구현체들이 서로 다른 부가 기능을 포함한다.

#### 2.4.1 Cache Adapter Drift Metrics 불균형

| 어댑터 | Drift Metrics | 코드 위치 |
|--------|--------------|-----------|
| `InMemoryCacheAdapter` | O (`record_cache_get`, `record_cache_set`, `record_cache_ttl_expired`) | `adapters/cache/memory_adapter.py:36-48` |
| `RedisCacheAdapter` | X | `adapters/cache/redis_adapter.py` |
| `MemcachedCacheAdapter` | X | `adapters/cache/memcached_adapter.py` |

**문제**: InMemory에서만 drift metrics가 수집되어, 운영(Redis) 환경에서는 drift detection이 불가능.

#### 2.4.2 Lock Owner ID 방식 불일치

| 어댑터 | Owner ID 형식 | 코드 |
|--------|-------------|------|
| `RedisDistributedLock` | `f"{threading.get_ident()}:{id(self)}:{time.time()}"` | `adapters/cache/redis_adapter.py:33-` |
| `InMemoryLock` | `f"{threading.get_ident()}:{id(self)}"` | `adapters/cache/memory_adapter.py:67-` |
| `MemcachedDistributedLock` | `str(uuid.uuid4())` | `adapters/cache/memcached_adapter.py:26-` |

**문제**: owner ID 형식이 다르면 lock 전환 시 (InMemory → Redis 마이그레이션) 호환성 문제 발생 가능.

---

## 3. 개선 계획

### 3.1 Phase 1: 메서드 중복 제거 (P1)

**목표**: `get_all()`과 `get_all_states()` 중 하나를 표준으로 결정

#### 3.1.1 결정: `get_all_states()`를 표준으로 채택

**근거**:
- `get_all()`은 너무 일반적인 이름 (무엇의 all인지 불명확)
- `get_all_states()`가 "Circuit Breaker **상태** 목록"이라는 의미를 명확히 전달
- `get_all_states()`의 호출자가 더 많음 (statistics, celery tasks, services)

#### 3.1.2 Pagination 불요 판단

`get_all_states()`는 `list[]` 반환을 유지한다. Pagination은 도입하지 않는다.

**근거**:
- CB 인스턴스 수는 서버 수가 아닌 `보호 대상 서비스 수 × Cell 수`로 결정됨
- 일반 기업 10~200개, 대기업 + Cell Topology 시 최대 2,000개 수준
- 50,000개(극단적 시나리오)에서도 ~25MB로 Python 프로세스 OOM 위험 없음
- 20개 이상 호출자(panic_threshold, health_check, statistics 등)가 **전체 상태 기반 집계**를 수행하므로 Pagination 시 로직 재설계 필요
- `PaginatedResult` 인프라가 `interfaces/statistics.py:78`에 이미 존재하므로, 추후 API 엔드포인트에서 필요 시 Service 계층에서 감싸면 됨

#### 3.1.3 구현

```python
# interfaces/repositories.py — CircuitBreakerStateRepository

# 제거:
# @abstractmethod
# def get_all(self) -> list[CircuitBreakerStateData]:
#     """Get all circuit breaker states"""
#     ...

# 유지:
@abstractmethod
def get_all_states(self) -> list[CircuitBreakerStateData]:
    """Get all circuit breaker states"""
    ...

# 호환성:
def get_all(self) -> list[CircuitBreakerStateData]:
    """Deprecated: Use get_all_states() instead."""
    return self.get_all_states()
```

#### 3.1.4 수정 대상

| 파일 | 변경 |
|------|------|
| `interfaces/repositories.py` | `get_all()`를 non-abstract deprecated wrapper로 변경 |
| `adapters/redis/circuit_breaker.py` | `get_all()` 구현 제거 (base class wrapper 사용) |
| `adapters/memory/circuit_breaker.py` | `get_all()` 구현 제거 |
| `adapters/ipc/cb_state_snapshot.py` | `get_all()` 호출을 `get_all_states()` 호출로 변경 |
| `adapters/memory/layered_repository/` | `get_all()` 호출을 `get_all_states()` 호출로 변경 |

---

### 3.2 Phase 2: 구현체 없는 인터페이스 처리 (P1)

#### 3.2.1 WebFrameworkInterface

**결정**: 현 위치 유지 + docstring 추가 (이동/격리 불요)

**근거**:
- 현재 Django 전용으로 동작하며 프레임워크 마이그레이션 계획 없음
- 인터페이스는 향후 FastAPI/Flask 마이그레이션을 위한 설계 문서 역할
- 구현체 없이 유지하되, docstring에 상태 명시
- `deprecated/` 또는 `docs/design_drafts/`로 이동 **불가** — 동일 파일의 10개 심볼(`HttpMethod`, `ContentType`, `RequestContext`, `ResponseContext` 등)이 `interfaces/__init__.py`의 `__all__`에 export 중이므로 이동 시 import path 파괴

```python
# interfaces/web_framework.py — docstring 추가
class WebFrameworkInterface(ABC):
    """
    Abstract interface for web framework integration.

    NOTE: No production implementation exists yet. Django is used directly.
    This interface is preserved as a design contract for future framework
    migration (Django -> FastAPI, Flask, etc.).

    When implementing:
    - See adapters/django/ for reference patterns
    - Register via ProviderRegistry.register_web_framework()
    """
```

#### 3.2.2 SecurityIncidentRepository

**결정**: Django 구현체 추가 필요

**근거**:
- InMemory만 존재하면 운영 환경에서 재시작 시 보안 인시던트 데이터 유실
- 보안 데이터는 반드시 영속적 저장소 필요
- Django ORM 기반 구현이 자연스러움 (기존 FailedOperationRepository, CircuitBreakerStateRepository가 Django 구현 보유)

```python
# adapters/django/security_incident.py (신규)
class DjangoSecurityIncidentRepository(SecurityIncidentRepository):
    """Django ORM implementation for SecurityIncidentRepository."""

    def create(self, incident_type, severity, ...) -> SecurityIncidentData:
        instance = SecurityIncident.objects.create(
            incident_type=incident_type,
            severity=severity,
            ...
        )
        return self._to_data(instance)

    def get_by_id(self, id: int) -> SecurityIncidentData | None:
        try:
            return self._to_data(SecurityIncident.objects.get(id=id))
        except SecurityIncident.DoesNotExist:
            return None

    # ... 나머지 메서드 구현
```

**전제 조건 확인 완료**: Django SecurityIncident 모델은 이미 존재한다.
- Abstract 모델: `adapters/django/models/_abstract_security_incident.py`
- Concrete 모델: `adapters/django/models/__init__.py:173-209` (`db_table="security_incidents"`)
- Migration: `adapters/django/migrations/0002_add_dlq_and_security_models.py:385-503`
- DTO: `interfaces/repositories.py:228-277` (`SecurityIncidentData`)

따라서 이번 311 범위에서는 **DjangoSecurityIncidentRepository 구현 + ProviderRegistry 등록**만 수행한다.

---

### 3.3 Phase 3: __init__.py Export 보완 (P2)

**목표**: 모든 공식 인터페이스가 `interfaces/__init__.py`에서 접근 가능

#### 3.3.1 추가할 항목

```python
# interfaces/__init__.py — 추가

# =============================================================================
# Event Journal Interface
# =============================================================================
from selfhealing.interfaces.event_journal import (
    EventJournalEntry,
    EventJournalFilter,
    EventJournalRepository,
)

# =============================================================================
# Notification Interface
# =============================================================================
from selfhealing.interfaces.notification import (
    Notification,
    NotificationAdapter,
    NotificationChannel,
    NotificationSeverity,
)

# __all__에 추가:
__all__ = [
    # ... 기존 항목 ...

    # Event Journal
    "EventJournalRepository",
    "EventJournalEntry",
    "EventJournalFilter",

    # Notification
    "Notification",
    "NotificationAdapter",
    "NotificationChannel",
    "NotificationSeverity",
]
```

---

### 3.4 Phase 4: 어댑터 기능 균형 맞추기 (P2)

#### 3.4.1 Drift Metrics 균일 적용 — Decorator 패턴

**방법**: `CacheProviderInterface`에 `record_metric()` 메서드를 추가하는 대신,
Decorator 패턴(`MetricsAwareCacheAdapter`)을 도입하여 메트릭 수집 책임을 분리한다.

**근거**:
- 캐시 오퍼레이션(비즈니스 로직)과 관측성(Observability)을 단일 클래스에 혼합하면 SRP 위반
- Decorator로 분리하면 기존 Redis/Memcached/InMemory 어댑터 코드 **무변경**
- Netflix Hystrix/Resilience4j의 DecoratorPattern과 동일한 엔터프라이즈 표준

**구현**:

```python
# adapters/cache/metrics_decorator.py (신규)
class MetricsAwareCacheAdapter(CacheProviderInterface):
    """Decorator that adds drift metrics to any CacheProviderInterface."""

    def __init__(self, delegate: CacheProviderInterface):
        self._delegate = delegate

    def get(self, key: str) -> Any | None:
        result = self._delegate.get(key)
        record_cache_get(backend=type(self._delegate).__name__, hit=result is not None)
        return result

    def set(self, key: str, value: Any, ttl=None) -> bool:
        result = self._delegate.set(key, value, ttl)
        record_cache_set(backend=type(self._delegate).__name__)
        return result

    # ... 나머지 메서드는 delegate에 위임
```

**전환 방식**: Big Bang (점진적 전환 불가)
- `InMemoryCacheAdapter`에 하드코딩된 기존 메트릭 코드(`memory_adapter.py:36-48` 및 산재된 `record_*` 호출) **전량 삭제**
- ProviderRegistry/팩토리에서 모든 캐시 어댑터를 `MetricsAwareCacheAdapter`로 wrap하여 반환
- 점진적 전환 시 메트릭 수집 경로가 2개(내부 하드코딩 + Decorator) 공존하여 중복 카운팅 위험이 있으므로 한 번에 전환

#### 3.4.2 Lock Owner ID 표준화

**표준 형식**: `{hostname}:{pid}:{thread_id}:{unique_suffix}`

PID를 포함하는 이유: Python 웹 애플리케이션(Gunicorn pre-fork, uWSGI 등)은 단일 호스트 내에서
멀티 프로세스로 동작한다. `threading.get_ident()`는 프로세스 내에서만 고유하므로,
서로 다른 Worker 프로세스에서 동일한 thread_id가 반환될 수 있다.
UUID suffix가 충돌을 방지하지만, PID가 있으면 디버깅/감사 시 어떤 Worker가 락을 보유하는지 즉시 식별 가능하다.

```python
# interfaces/cache_provider.py — DistributedLock 추가

import os
import socket
import threading
import uuid

def generate_lock_owner_id() -> str:
    """표준 lock owner ID 생성. 모든 DistributedLock 구현에서 사용."""
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:8]}"
```

| 어댑터 | 변경 |
|--------|------|
| `RedisDistributedLock` | `generate_lock_owner_id()` 사용 |
| `InMemoryLock` | `generate_lock_owner_id()` 사용 |
| `MemcachedDistributedLock` | `generate_lock_owner_id()` 사용 |

---

## 4. 자동 검증: Architecture Test

### 4.1 인터페이스-구현체 정합성 테스트

```python
# tests/unit/test_interface_contract.py

import inspect
from abc import ABC

def test_all_abstract_methods_implemented():
    """모든 ABC 구현체가 abstractmethod를 빠짐없이 구현했는지 검증."""
    from selfhealing import interfaces, adapters

    # ABC 인터페이스 목록 수집
    abc_interfaces = [
        obj for name, obj in inspect.getmembers(interfaces, inspect.isclass)
        if issubclass(obj, ABC) and obj is not ABC
    ]

    for iface in abc_interfaces:
        abstract_methods = {
            name for name, _ in inspect.getmembers(iface)
            if getattr(getattr(iface, name, None), '__isabstractmethod__', False)
        }
        # 각 인터페이스의 구현체 찾기
        implementations = _find_implementations(iface, adapters)
        for impl in implementations:
            impl_methods = set(dir(impl))
            missing = abstract_methods - impl_methods
            assert not missing, (
                f"{impl.__name__} is missing methods from {iface.__name__}: {missing}"
            )


def test_interfaces_exported_in_init():
    """모든 interfaces/*.py의 ABC/Protocol이 __init__.py에서 export되는지 검증."""
    import selfhealing.interfaces as iface_pkg
    from pathlib import Path

    iface_dir = Path(iface_pkg.__file__).parent
    exported = set(iface_pkg.__all__)

    for py_file in iface_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module = importlib.import_module(f"selfhealing.interfaces.{py_file.stem}")
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, ABC) or hasattr(obj, '__protocol_attrs__')):
                if obj.__module__ == module.__name__:
                    assert name in exported, (
                        f"{name} from {py_file.name} not in interfaces/__all__"
                    )
```

### 4.2 Abstract Property 검증 테스트

`__isabstractmethod__` 기반 검사는 `@property` + `@abstractmethod` 조합도 감지한다.
Python의 `property` descriptor는 `fget`의 `__isabstractmethod__`를 `property` 객체 자체로 전파하므로,
Section 4.1의 테스트 로직이 이미 abstract property를 커버한다.

현재 `interfaces/` 디렉토리에서 유일한 abstract property는 `web_framework.py:449`의
`WebFrameworkInterface.framework_name`이며, 구현체가 0개이므로 실질적 검증 대상은 없다.

단, 이 보장을 명시적으로 문서화하기 위해 확인 테스트 1개를 추가한다:

```python
def test_abstract_property_detected():
    """Verify that @property @abstractmethod is caught by our detection logic."""
    from selfhealing.interfaces.web_framework import WebFrameworkInterface
    assert getattr(
        WebFrameworkInterface.framework_name, '__isabstractmethod__', False
    ) is True
```

---

## 5. 구현 순서

| Phase | 작업 | 파일 수 | 우선순위 |
|-------|------|---------|----------|
| 1 | `get_all` → `get_all_states` 통일 | 5-7 | P1 |
| 2 | WebFrameworkInterface docstring + SecurityIncidentRepo Django 구현 | 2 | P1 |
| 3 | `__init__.py` export 보완 | 1 | P2 |
| 4 | MetricsAwareCacheAdapter Decorator + InMemory 메트릭 코드 삭제 + Lock owner ID 표준화 (PID 포함) | 5-6 | P2 |
| 5 | Architecture Test 추가 (abstract property 검증 포함) | 1 신규 | P2 |

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| `get_all()` 제거 시 기존 호출자 break | 컴파일 에러 | deprecated wrapper 제공, 1 major 버전 유예 |
| SecurityIncident Django 모델 미존재 | 구현 불가 | **해소됨**: Abstract/Concrete 모델 + Migration 모두 확인 완료 (`adapters/django/models/`, `migrations/0002`) |
| Lock owner ID 변경 시 기존 lock 호환성 | 진행 중 lock 해제 불가 | **무중단 배포에 안전함**: 각 `DistributedLock` 인스턴스는 생성 시점의 `self._owner_id`를 보존하고, `release()`는 해당 값과의 동일성만 검증. 형식 변경이 기존 lock에 영향을 주지 않음 (Backward-compatible Unlock 로직 불필요) |

---

## 7. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
| 2026-03-07 | 1.1.0 | 7개 리뷰 결과 반영: Pagination 불요 판단(3.1.2), SecurityIncident 모델 존재 확인(3.2.2), WebFrameworkInterface 이동 불가 근거(3.2.1), Decorator 패턴 채택 + Big Bang 전환(3.4.1), Lock Owner ID에 PID 추가(3.4.2), Lock 마이그레이션 안전성 확인(6. 위험 및 완화), Abstract Property 검증 커버리지 확인(4.2) |
