# 309. Architecture Pattern Consistency — 어댑터 등록/DI/인터페이스/임포트 패턴 통일

> **Status**: Refactor
> **Severity**: P0 (CRITICAL)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/factory.py` — ProviderRegistry
> - `packages/selfhealing-python/src/selfhealing/interfaces/notification.py` — 자체 레지스트리
> - `packages/selfhealing-python/src/selfhealing/services/` — DI 패턴 혼용
> - `packages/selfhealing-python/src/selfhealing/interfaces/` — ABC vs Protocol 혼용
> - `packages/selfhealing-python/src/selfhealing/adapters/__init__.py` — 임포트 전략
> - `packages/selfhealing-python/src/selfhealing/audit/__init__.py` — 임포트 전략
> **References**:
> - 309 (이 문서)
> - 310 — 기능 중복 제거
> - 311 — 인터페이스 계약 정합성

---

## 1. 현황 및 문제

시스템이 60+ 서비스, 23 어댑터, 15 인터페이스로 성장하면서 **아키텍처 패턴이 4개 축에서 불일치**가 발생했다.
각 불일치는 독립적으로는 작지만, 전체적으로 새 코드 작성 시 "어떤 패턴을 따라야 하는가?"에 대한 혼란을 유발한다.

---

## 2. 불일치 상세 분석

### 2.1 어댑터 등록 방식 이원화 (P0)

현재 어댑터 등록에 **2가지 별도 메커니즘**이 존재한다.

| 방식 | 사용처 | 코드 위치 |
|------|--------|-----------|
| `ProviderRegistry` (classmethod 기반) | Cache, Queue, Repository, Audit, Alert, TrafficRouting, Statistics | `factory.py:44-` |
| 모듈 레벨 dict + 함수 | NotificationAdapter | `interfaces/notification.py:217-241` |

**notification.py의 자체 레지스트리 코드**:

```python
# interfaces/notification.py:217-241
_notification_adapters: dict[NotificationChannel, NotificationAdapter] = {}
_default_adapter: NotificationAdapter = LoggingNotificationAdapter()

def register_notification_adapter(adapter: NotificationAdapter) -> None:
    _notification_adapters[adapter.channel] = adapter

def get_notification_adapter(channel: NotificationChannel | None = None) -> NotificationAdapter:
    if channel and channel in _notification_adapters:
        return _notification_adapters[channel]
    return _default_adapter
```

**ProviderRegistry 방식**:

```python
# factory.py:44-
class ProviderRegistry:
    _caches: ClassVar[dict[str, type | Callable]] = {}
    _cache_instances: ClassVar[dict[str, CacheProviderInterface]] = {}

    @classmethod
    def register_cache(cls, name: str, adapter_class: type | Callable) -> None:
        cls._caches[name] = adapter_class

    @classmethod
    def get_cache(cls, name: str | None = None) -> CacheProviderInterface:
        ...
```

**문제**:
- 동일한 시스템에서 어댑터 등록/조회 방식이 2가지
- NotificationAdapter는 ProviderRegistry의 lifecycle 관리(reset, clear_instances)에서 누락
- 테스트 시 ProviderRegistry.reset()으로 초기화해도 Notification 어댑터는 그대로 남음

---

### 2.2 의존성 주입(DI) 패턴 3가지 혼용 (P1)

서비스마다 어댑터/리포지토리를 획득하는 방식이 다르다.

#### Pattern A: Lazy Property + ProviderRegistry (권장 패턴)

```python
# services/dlq/base.py:44-51
@property
def repository(self) -> FailedOperationRepository:
    if self._repository is None:
        from selfhealing.factory import ProviderRegistry
        self._repository = ProviderRegistry.get_failed_operation_repo()
    return self._repository
```

**사용**: DLQServiceBase, CircuitBreakerService

#### Pattern B: Lazy Property + ProviderRegistry + Fallback

```python
# services/replay_service/service.py:87-100
@property
def repository(self) -> FailedOperationRepository:
    if self._repository is None:
        try:
            from selfhealing.factory import ProviderRegistry
            self._repository = ProviderRegistry.get_failed_operation_repo()
        except (ImportError, ValueError):
            from selfhealing.adapters.memory import InMemoryFailedOperationRepository
            self._repository = InMemoryFailedOperationRepository()
    return self._repository
```

**사용**: ReplayService

#### Pattern C: ProviderRegistry 완전 우회

```python
# services/chaos/scheduler/service.py:58-75
class ChaosSchedulerService:
    def __init__(self):
        self._schedules: dict[str, ScheduledExperiment] = {}
        # ProviderRegistry 사용하지 않음, 자체 in-memory 저장소
```

**사용**: ChaosSchedulerService, RunbookService

**문제**:
- Pattern A는 ProviderRegistry 미초기화 시 크래시
- Pattern B는 graceful degradation 제공하지만 일부 서비스만 적용
- Pattern C는 ProviderRegistry 밖에 존재하여 통합 테스트/모니터링에서 누락

---

### 2.3 인터페이스 정의 방식 불일치 (P2)

| 방식 | 사용 인터페이스 | 특성 |
|------|----------------|------|
| `ABC` + `@abstractmethod` | FailedOperationRepository, CircuitBreakerStateRepository, SecurityIncidentRepository, CacheProviderInterface, TaskQueueInterface, AuditLogAdapter, AlertAdapter, StatisticsRepositoryInterface, EventJournalRepository, WebFrameworkInterface, TrafficRoutingAdapter, RateLimitStorageInterface, ConfigProviderInterface | 구현체가 ABC를 상속해야 함 |
| `Protocol` (runtime_checkable) | NotificationAdapter | duck typing, 상속 불요 |
| `Protocol` (non-runtime) | ResiliencePolicy, AsyncResiliencePolicy, PolicyGuard, PolicyHook, FailureSink, AnomalyDetectionStrategy, ForecastStrategy, ClassificationStrategy, BatchCapable, StrategyLifecycle | duck typing, isinstance 불가 |

**문제**:
- 새 어댑터 개발 시 ABC를 상속해야 하는지, Protocol만 구현하면 되는지 일관성 없음
- runtime_checkable Protocol은 구조적 타입 체킹만 제공하여, ABC의 `@abstractmethod` 강제와 동작이 다름
- 같은 "어댑터" 카테고리인 AuditLogAdapter(ABC)와 NotificationAdapter(Protocol)가 서로 다른 계약 방식

---

### 2.4 임포트 전략 3가지 혼용 (P3)

| 전략 | 사용처 | 코드 예시 |
|------|--------|-----------|
| `TYPE_CHECKING` guard | `factory.py:24-39` | `if TYPE_CHECKING: from selfhealing.interfaces.audit_adapter import AuditLogAdapter` |
| `try/except ImportError` | `adapters/__init__.py:48-90` | `try: from ... import X except ImportError: X = None` |
| `__getattr__` lazy loading | `audit/__init__.py:65-261` | `_LAZY_IMPORTS = {...}; def __getattr__(name): ...` |

**문제**:
- 같은 문제(순환 참조 방지/선택적 의존성)를 3가지 다른 방법으로 해결
- 새 모듈 추가 시 어떤 전략을 따라야 하는지 불명확
- `try/except` 패턴에서 None이 할당된 경우 `__all__`에는 여전히 이름이 있어 런타임 AttributeError 가능

---

## 3. 개선 계획

### 3.1 Phase 1: 어댑터 등록 통일 (P0)

**목표**: NotificationAdapter를 ProviderRegistry로 통합

#### 3.1.1 ProviderRegistry에 Notification 관련 메서드 추가

```python
# factory.py — ProviderRegistry 클래스에 추가
class ProviderRegistry:
    _notifications: ClassVar[dict[str, type | Callable]] = {}
    _notification_instances: ClassVar[dict[str, NotificationAdapter]] = {}
    _default_notification: ClassVar[str] = "logging"

    @classmethod
    def register_notification(cls, name: str, adapter_class: type | Callable) -> None:
        cls._notifications[name] = adapter_class
        logger.debug("registry.notification_registered", name=name)

    @classmethod
    def get_notification(cls, name: str | None = None) -> NotificationAdapter:
        name = name or cls._default_notification
        if name not in cls._notification_instances:
            if name not in cls._notifications:
                raise ValueError(f"Unknown notification adapter: {name}")
            factory = cls._notifications[name]
            cls._notification_instances[name] = (
                factory() if callable(factory) and not isinstance(factory, type) else factory()
            )
        return cls._notification_instances[name]
```

#### 3.1.2 notification.py 마이그레이션

```python
# interfaces/notification.py — 모듈 레벨 레지스트리 함수를 ProviderRegistry 위임으로 변경
def register_notification_adapter(adapter: NotificationAdapter) -> None:
    """Backward-compatible wrapper. Delegates to ProviderRegistry."""
    from selfhealing.factory import ProviderRegistry
    channel_name = adapter.channel.value if hasattr(adapter, 'channel') else "default"
    ProviderRegistry.register_notification(channel_name, lambda: adapter)

def get_notification_adapter(channel: NotificationChannel | None = None) -> NotificationAdapter:
    """Backward-compatible wrapper. Delegates to ProviderRegistry."""
    from selfhealing.factory import ProviderRegistry
    name = channel.value if channel else None
    try:
        return ProviderRegistry.get_notification(name)
    except ValueError:
        return _default_adapter
```

#### 3.1.3 auto-registration 추가

```python
# factory.py — _auto_register_notification_adapters()
def _auto_register_notification_adapters():
    from selfhealing.interfaces.notification import (
        LoggingNotificationAdapter,
        StdoutNotificationAdapter,
    )
    ProviderRegistry.register_notification("logging", LoggingNotificationAdapter)
    ProviderRegistry.register_notification("stdout", StdoutNotificationAdapter)
```

#### 3.1.4 ProviderRegistry 스레드 안전성 — Double-Checked Locking

ProviderRegistry의 모든 getter(`get_cache`, `get_notification` 등)는 check-then-act 패턴이다.
gunicorn 워커, FastAPI 같은 멀티 스레드/비동기 환경에서 여러 스레드가 동시에 getter를 호출하면
어댑터 인스턴스가 중복 생성되는 Race Condition이 발생할 수 있다.

**현재 문제 코드** (`factory.py:327-338`):

```python
if singleton:
    key = f"cache:{name}"
    if key in cls._instances:       # Thread A 통과
        return cls._instances[key]
# ...
instance = cls._cache_providers[name]()  # Thread A, B 모두 도달
if singleton:
    cls._instances[key] = instance       # 인스턴스 2개 생성
```

**기존 선례**: `config.py:248-256`의 `EventLoggingConfig`와 `coordination/factory.py:18`이
이미 `threading.Lock`으로 싱글톤 생성을 보호하고 있다.

**구현: 클래스 레벨 Lock + Double-Checked Locking**

```python
import threading

class ProviderRegistry:
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get_cache(cls, name: str | None = None, singleton: bool = True) -> CacheProviderInterface:
        name = name or cls._default_cache
        if singleton:
            key = f"cache:{name}"
            if key in cls._instances:          # Fast path (락 없이)
                return cls._instances[key]
            with cls._lock:                    # Slow path
                if key in cls._instances:      # Double-check (락 내부 재확인 필수)
                    return cls._instances[key]
                if name not in cls._cache_providers:
                    raise ValueError(...)
                instance = cls._cache_providers[name]()
                cls._instances[key] = instance
                return instance
        # non-singleton path
        ...
```

**적용 범위**:
- 락은 **singleton 인스턴스 생성 경로(slow path)에만** 적용
- `register_*` 메서드: 앱 기동 시 단일 스레드에서 호출 → 락 불요
- `_auto_register_adapters()`: 모듈 import 시점(GIL 보호) → 락 불요
- 모든 getter(`get_cache`, `get_queue`, `get_notification` 등)에 동일 패턴 적용

**주의**: Python dict의 읽기/쓰기는 CPython GIL 하에서 사실상 원자적이지만,
이는 구현 세부사항이며 언어 보장이 아니다. Python 3.13+ free-threaded 모드(`--disable-gil`)
대비를 위해 DCL의 inner check를 반드시 포함해야 한다.

**영향 범위**:
- `factory.py` — ProviderRegistry 확장
- `interfaces/notification.py` — 기존 함수를 wrapper로 변경 (backward-compatible)
- `factory.py` — clear_instances(), reset() 메서드에 notification 추가

---

### 3.2 Phase 2: DI 패턴 표준화 (P1)

**목표**: 모든 서비스가 동일한 DI 패턴 사용

#### 3.2.1 표준 DI 패턴 결정

**Pattern B (Lazy + Fallback + Fail-Fast + Metrics)를 표준으로 채택**:

운영 환경에서 In-Memory Fallback은 Silent Data Loss를 유발할 수 있다.
워커 재시작 시 메모리에 쌓인 재시도 데이터가 조용히 증발하며,
K8s의 liveness/readiness probe 기반 자동 복구 메커니즘을 무력화시킨다.
따라서 환경별 Fallback 정책을 적용한다.

**Fallback 정책 (3단계)**:

| 정책 | 환경 | 동작 |
|------|------|------|
| `ALLOW` | dev/test | InMemory fallback 허용 |
| `WARN_AND_ALLOW` | staging | Fallback + 메트릭 + 경고 |
| `FAIL_FAST` | production | 즉시 크래시 → K8s 파드 재시작 |

**설정**: `SELFHEALING_FALLBACK_POLICY` 환경 변수로 제어.
기존 `settings/namespace.py`의 `SELFHEALING_NAMESPACE_ENV`와 연동.

```python
# settings에 추가
class FallbackPolicy(str, Enum):
    ALLOW = "allow"
    WARN_AND_ALLOW = "warn"
    FAIL_FAST = "fail_fast"
```

**표준 DI 패턴 (최종)**:

```python
# 표준 패턴 — 모든 서비스에서 사용
@property
def repository(self) -> FailedOperationRepository:
    if self._repository is None:
        try:
            from selfhealing.factory import ProviderRegistry
            self._repository = ProviderRegistry.get_failed_operation_repo()
        except (ImportError, ValueError) as exc:
            policy = get_config().fallback_policy  # SELFHEALING_FALLBACK_POLICY
            if policy == FallbackPolicy.FAIL_FAST:
                raise RuntimeError(
                    f"ProviderRegistry unavailable in production: {exc}"
                ) from exc
            from selfhealing.adapters.memory import InMemoryFailedOperationRepository
            self._repository = InMemoryFailedOperationRepository()
            logger.warning(
                "service.fallback_adapter",
                adapter="InMemoryFailedOperationRepository",
                service=self.__class__.__name__,
            )
            # Prometheus 메트릭 emit
            try:
                from selfhealing.metrics.prometheus import get_metrics
                metrics = get_metrics()
                if hasattr(metrics, 'di_fallback_total'):
                    metrics.di_fallback_total.labels(
                        service=self.__class__.__name__,
                        adapter="InMemoryFailedOperationRepository",
                    ).inc()
            except Exception:
                pass  # 메트릭 실패가 서비스를 중단시키면 안 됨
    return self._repository
```

**Prometheus 메트릭 정의** (`metrics/prometheus.py`에 추가):

```python
self.di_fallback_total = Counter(
    f"{prefix}_di_fallback_total",
    "DI fallback to in-memory adapter",
    ["service", "adapter"],
)
```

프로젝트 내 기존 fallback 메트릭 선례:
- `metrics/prometheus.py:226` — `mesh_preemptive_fallback_total`
- `audit/cascade_metrics.py:326` — `selfhealing_cascade_fallback_writes_total`
- `metrics/audit_buffer_metrics.py:100` — `audit_buffer_fallback_size`

Grafana 알림: `rate(selfhealing_di_fallback_total[5m]) > 0`으로 즉시 구성 가능.

**근거**:
- ProviderRegistry 미초기화 시에도 서비스가 동작 (graceful degradation, dev/staging)
- 운영 환경에서는 Fail-Fast로 K8s 자동 복구 활용 (Google SRE: "Fail loudly and early")
- InMemory fallback은 테스트/개발 환경에서 유용
- fallback 시 로깅 + Prometheus 메트릭으로 정량적 모니터링

#### 3.2.2 수정 대상

| 서비스 | 현재 패턴 | 변경 |
|--------|-----------|------|
| `DLQServiceBase` | A (fallback 없음) | B로 변경 |
| `CircuitBreakerService` | A (fallback 없음) | B로 변경 |
| `ChaosSchedulerService` | C (ProviderRegistry 미사용) | B로 변경 + Repository 인터페이스 도입 검토 |
| `RunbookService` | C (ProviderRegistry 미사용) | B로 변경 + Repository 인터페이스 도입 검토 |

#### 3.2.3 ChaosScheduler / RunbookService 처리 방침

이 서비스들은 현재 자체 in-memory 저장소를 사용한다.
ProviderRegistry 통합 방식은 2가지:

**Option A**: 전용 Repository 인터페이스 신규 정의
- `ChaosExperimentRepository(ABC)`, `RunbookRepository(ABC)` 추가
- ProviderRegistry에 등록
- 높은 비용, 해당 서비스가 persistence를 필요로 할 때만 유의미

**Option B**: 현 상태 유지 + 문서화
- in-memory 저장소가 의도적 설계 결정인 경우
- `__init__` docstring에 "ProviderRegistry 미사용 사유" 명시
- ADR로 결정 근거 기록

**권장**: Option B. 이들은 실험/임시 데이터를 다루며 persistence 요구사항이 낮다.
단, `@property` + try/except 패턴은 적용하여 향후 확장 가능성 확보.

---

### 3.3 Phase 3: 인터페이스 스타일 ADR 작성 (P2)

**목표**: ABC와 Protocol 사용 기준을 명확히 정의

#### 3.3.1 ADR: ABC vs Protocol 선택 기준

| 기준 | ABC | Protocol |
|------|-----|----------|
| 구현체가 **반드시 상속**해야 하는 경우 | O | X |
| 메서드 누락 시 **인스턴스화 차단** 필요 | O | X |
| **외부 사용자**가 자체 구현체를 제공하는 경우 | X | O |
| **duck typing** 허용이 바람직한 경우 | X | O |
| 기본 구현(default method)이 필요한 경우 | O | X |

**이 프로젝트에서의 결정**:

```
Repository (데이터 접근) → ABC
  근거: 복잡한 계약, 기본 구현(update_metadata), 구현 누락 시 런타임 오류 심각

Adapter (외부 통합) → ABC
  근거: 기존 모든 어댑터가 ABC, 일관성 유지
  NotificationAdapter를 ABC로 마이그레이션

Strategy (알고리즘 교체) → Protocol
  근거: ML Strategy 등은 외부 라이브러리에서 제공 가능, duck typing 적합

Policy (정책 조합) → Protocol
  근거: 다양한 조합이 가능해야 함, 상속보다 구성(composition) 우선
```

#### 3.3.2 NotificationAdapter ABC 마이그레이션

```python
# interfaces/notification.py — Protocol → ABC 전환
from abc import ABC, abstractmethod

class NotificationAdapter(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> bool: ...

    @abstractmethod
    def send_batch(self, notifications: list[Notification]) -> int: ...

    @property
    @abstractmethod
    def channel(self) -> NotificationChannel: ...
```

**내장 구현체**: `StdoutNotificationAdapter`, `LoggingNotificationAdapter`는
ABC 상속으로 즉시 변경한다.

#### 3.3.3 Duck-typed 어댑터 하위 호환 — Virtual Subclass 자동 등록

Protocol → ABC 전환 시 기존에 duck typing에 의존하던 외부 사용자의
커스텀 어댑터가 `isinstance()` 검사를 통과하지 못하는 Breaking Change가 발생한다.
현재 프로젝트 내에서 `isinstance(x, NotificationAdapter)` 호출은 없지만,
ProviderRegistry 통합(Phase 1) 시 내부적으로 타입 검증이 추가될 수 있다.

**`register_notification_adapter`에서 자동 Virtual Subclass 등록**:

`@overload`를 사용하여 MyPy 타입 안전성을 유지하면서, duck-typed 객체도 수용한다.
별도의 LegacyProtocol을 정의하지 않는다 — 프로젝트 내 13개 ABC 인터페이스 중
어떤 것도 Legacy Protocol을 병행하지 않으므로 일관성을 위해 `@overload` 패턴을 채택한다.

```python
from typing import overload

@overload
def register_notification_adapter(adapter: NotificationAdapter) -> None: ...
@overload
def register_notification_adapter(adapter: object) -> None: ...

def register_notification_adapter(adapter: object) -> None:
    """Register adapter. Auto-registers as virtual subclass if duck-typed."""
    if not isinstance(adapter, NotificationAdapter):
        required = ('send', 'send_batch', 'channel')
        missing = [m for m in required if not hasattr(adapter, m)]
        if missing:
            raise TypeError(
                f"Adapter {type(adapter).__name__} missing: {missing}. "
                f"Inherit from NotificationAdapter."
            )
        NotificationAdapter.register(type(adapter))
        logger.warning(
            "notification.duck_typed_adapter_registered",
            adapter_type=type(adapter).__name__,
        )
    _notification_adapters[adapter.channel] = adapter
```

**설계 결정 근거**:
- `Any` 타입 힌트 사용 금지 — 정적 분석 도구가 인자 타입을 검사하지 못함
- `LegacyNotificationProtocol` 별도 정의 금지 — ABC와 동일한 메서드 시그니처를
  두 곳에서 유지해야 하므로 309가 해결하려는 "패턴 불일치"를 고착화함
- `@overload` 패턴 채택 — 첫 번째 overload로 MyPy가 `NotificationAdapter` 타입 추론,
  두 번째 overload로 duck-typed 객체 수용, 런타임에 검증 후 `ABC.register()` 호출

**영향 범위**:
- `StdoutNotificationAdapter` — `(NotificationAdapter)` 상속 추가
- `LoggingNotificationAdapter` — `(NotificationAdapter)` 상속 추가
- 외부 duck-typed 구현체 — 코드 변경 없이 `register_notification_adapter()`로 등록 가능

---

### 3.4 Phase 4: 임포트 전략 표준화 (P3)

**목표**: 하나의 전략으로 통일

#### 3.4.1 표준 전략: `__getattr__` + `TYPE_CHECKING` + `__all__` 3중 패턴

`__getattr__` 단독 사용 시 IDE 자동 완성과 MyPy 정적 타입 체킹이 동작하지 않는다.
pandas, FastAPI 등 대형 오픈소스에서 검증된 3중 패턴을 채택하여
**런타임 최적화 + DX(개발자 경험) + 모듈 공개 범위**를 동시에 확보한다.

```python
# 표준 패턴 — 대형 __init__.py에서 사용
from __future__ import annotations
import importlib
from typing import TYPE_CHECKING

# (1) IDE autocomplete + MyPy 지원 (런타임에는 실행되지 않음)
if TYPE_CHECKING:
    from selfhealing.adapters.redis import (
        RedisCircuitBreakerStateRepository,
        RedisDLQRepository,
    )
    from selfhealing.adapters.memory import (
        InMemoryFailedOperationRepository,
        InMemoryCircuitBreakerStateRepository,
    )
    # ...

# (2) 런타임 Lazy Loading
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "RedisCircuitBreakerStateRepository": (
        "selfhealing.adapters.redis", "RedisCircuitBreakerStateRepository"
    ),
    "RedisDLQRepository": (
        "selfhealing.adapters.redis", "RedisDLQRepository"
    ),
    "InMemoryFailedOperationRepository": (
        "selfhealing.adapters.memory", "InMemoryFailedOperationRepository"
    ),
    "InMemoryCircuitBreakerStateRepository": (
        "selfhealing.adapters.memory", "InMemoryCircuitBreakerStateRepository"
    ),
    # ...
}

def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value  # 캐싱
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# (3) __all__과 _LAZY_IMPORTS 자동 동기화
__all__ = list(_LAZY_IMPORTS.keys())
```

**3중 패턴의 각 역할**:

| 요소 | 역할 | 런타임 비용 |
|------|------|------------|
| `TYPE_CHECKING` 블록 | IDE 자동 완성, MyPy 타입 추론 | 0 (실행되지 않음) |
| `_LAZY_IMPORTS` + `__getattr__` | 실제 모듈 로딩 (최초 접근 시) | 최초 1회만 |
| `__all__ = list(_LAZY_IMPORTS.keys())` | 모듈 공개 범위 동기화 | 0 |

**핵심**: `__all__ = list(_LAZY_IMPORTS.keys())`로 두 목록을 자동 동기화하여,
기존 `try/except` 패턴의 "`__all__`에 이름은 있지만 런타임 AttributeError" 문제를 완전 해결.

**근거**:
- `TYPE_CHECKING`은 런타임에 타입 정보 접근 불가 → 동적 디스패치에 부적합 (단독 사용 불가)
- `try/except`는 None 할당으로 `__all__` 불일치 유발
- `__getattr__`는 Python 3.7+ 공식 지원, import 실패 시 명확한 AttributeError
- 3중 패턴으로 병행 시 각 약점이 상호 보완됨

#### 3.4.2 수정 대상

| 파일 | 현재 전략 | 변경 |
|------|-----------|------|
| `adapters/__init__.py` | try/except ImportError | `__getattr__` lazy loading |
| `factory.py` | TYPE_CHECKING (유지) | 변경 불요 — 타입 힌트 전용이므로 적합 |
| `audit/__init__.py` | `__getattr__` (이미 적용) | 변경 불요 |

**`factory.py`의 TYPE_CHECKING은 유지**: 이 파일은 런타임에 lazy import를 `_auto_register_*` 함수 내부에서 수행하며, TYPE_CHECKING은 순수하게 타입 힌트 목적이므로 `__getattr__`와 충돌하지 않는다.

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 우선순위 |
|-------|------|---------|----------|
| 1 | Notification → ProviderRegistry 통합 | 2 | P0 |
| 2 | DI 패턴 표준화 (Pattern B 적용) | 4-6 | P1 |
| 3 | ADR 작성 + NotificationAdapter ABC 전환 | 3-4 | P2 |
| 4 | adapters/__init__.py 임포트 전략 변경 | 1 | P3 |

---

## 5. 테스트 계획

### 5.1 Architecture Tests (신규)

```python
# tests/unit/test_architecture_patterns.py

class TestProviderRegistryCompleteness:
    """모든 어댑터가 ProviderRegistry를 통해 접근 가능한지 검증."""

    def test_notification_adapter_registered(self):
        """NotificationAdapter가 ProviderRegistry에 등록되어야 한다."""
        from selfhealing.factory import ProviderRegistry
        adapter = ProviderRegistry.get_notification()
        assert adapter is not None

    def test_all_adapters_reset_on_registry_reset(self):
        """ProviderRegistry.reset()이 모든 어댑터 인스턴스를 초기화한다."""
        from selfhealing.factory import ProviderRegistry
        ProviderRegistry.reset()
        # notification도 초기화되었는지 확인
        assert "notification" not in ProviderRegistry._notification_instances or \
               len(ProviderRegistry._notification_instances) == 0


class TestDIPatternConsistency:
    """모든 서비스가 표준 DI 패턴을 따르는지 검증."""

    def test_services_use_lazy_property(self):
        """주요 서비스들이 @property 기반 lazy loading을 사용한다."""
        import inspect
        from selfhealing.services.dlq.base import DLQServiceBase
        assert isinstance(
            inspect.getattr_static(DLQServiceBase, 'repository'),
            property
        )
```

### 5.2 기존 테스트 영향

- Notification 관련 테스트: `register_notification_adapter()` 호출이 ProviderRegistry를 경유하도록 변경
- ProviderRegistry.reset() 테스트: notification 인스턴스도 초기화되는지 확인 추가

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| NotificationAdapter ABC 전환이 breaking change | 외부 사용자 구현체가 상속 필요 | `@overload` + `ABC.register()` 자동 등록으로 duck-typed 어댑터 즉시 호환 (§3.3.3) |
| DI fallback이 운영 환경에서 InMemory 사용 | Silent Data Loss, K8s 자동 복구 무력화 | 환경별 3단계 Fallback Policy: prod → Fail-Fast 크래시 (§3.2.1) |
| Fallback 발생을 로그로만 감지 | 즉각적 알림 불가 | `di_fallback_total` Prometheus 카운터 + Grafana 알림 (§3.2.1) |
| ProviderRegistry Race Condition | 멀티 스레드 환경에서 인스턴스 중복 생성 | Double-Checked Locking 패턴 도입 (§3.1.4) |
| `__getattr__` 단독 사용 시 IDE 지원 불가 | 개발자 경험 저하, MyPy 오류 | `TYPE_CHECKING` + `__all__` 3중 패턴 병행 (§3.4.1) |
| ChaosScheduler에 Repository 도입 시 복잡도 증가 | 불필요한 추상화 | Option B (현 상태 유지 + 문서화) 채택 |

---

## 7. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
| 2026-03-07 | 1.1.0 | Q1~Q5 구현 세부사항 추가: DCL 스레드 안전성(§3.1.4), Fail-Fast 정책+Prometheus 메트릭(§3.2.1), Virtual Subclass @overload 브릿지(§3.3.3), TYPE_CHECKING 3중 패턴(§3.4.1), 위험 테이블 갱신 |
