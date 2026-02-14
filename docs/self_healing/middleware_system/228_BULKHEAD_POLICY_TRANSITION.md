# 228. BulkheadPolicy 전환 설계

## 1. 개요

Bulkhead는 현재 시스템에서 **가장 독립적인 패턴**이다.
`resilience/bulkhead/base.py`는 크로스-패턴 의존성이 **0건**이며,
표준 라이브러리만 사용한다. Policy 래핑이 가장 용이하다.

## 2. 현재 구현 분석

### 2.1 파일 구조

```
resilience/bulkhead/
├── base.py           # Bulkhead ABC — 완전 독립 (표준 라이브러리만 사용)
├── semaphore.py      # SemaphoreBulkhead 구현
├── async_semaphore.py # AsyncSemaphoreBulkhead 구현
├── threadpool.py     # ThreadPoolBulkhead 구현
├── decorator.py      # @bulkhead 데코레이터
├── registry.py       # BulkheadRegistry (ConnectionType, EventBus 의존)
├── exceptions.py     # BulkheadFullError, BulkheadTimeoutError
├── metrics.py        # Prometheus 메트릭
├── otel.py           # OpenTelemetry 통합
├── policy.py         # NEW: BulkheadPolicy (Phase 2 구현 대상)
└── __init__.py       # 패키지 퍼사드
```

### 2.2 base.py — Bulkhead ABC (완전 독립)

`resilience/bulkhead/base.py`에 정의된 추상 인터페이스:

```python
class Bulkhead(ABC):
    """격벽(Bulkhead) 추상 인터페이스."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    @contextmanager
    def acquire(self, timeout: float | None = None) -> Generator[None, None, None]: ...

    @abstractmethod
    def try_acquire(self) -> bool: ...

    @abstractmethod
    def release(self) -> None: ...

    @abstractmethod
    def get_state(self) -> BulkheadState: ...

    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]:
        """함수를 격벽으로 감싸는 데코레이터."""
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with self.acquire():
                return fn(*args, **kwargs)
        return wrapper
```

**이미 `wrap()` 메서드가 존재**하여, `execute()` 형태로 Policy 변환이 직접적이다.

### 2.3 decorator.py — @bulkhead 데코레이터

`resilience/bulkhead/decorator.py`의 `bulkhead()` 함수:

```python
def bulkhead(
    name: str | ConnectionType,
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,  # ← Fallback 내장
) -> Callable[[Callable[..., T]], Callable[..., T]]:
```

**문제점**: `fallback` 파라미터가 데코레이터 내부에 하드코딩.
Policy Composition에서는 FallbackPolicy가 별도로 담당해야 한다.

### 2.4 registry.py 의존성

| 의존 대상 | 위치 | 제거 가능 |
|-----------|------|----------|
| `ConnectionType` | L26 정적 import | ⚠️ 부분적 — 도메인 키로 광범위 사용 |
| `EventBus` | L96 lazy import | ✅ optional 구독 |

Registry는 Policy 외부의 인프라 레이어이므로 변환 대상이 아니다.

### 2.5 exceptions.py — 예외 클래스

```python
class BulkheadFullError(BulkheadError):
    """격벽이 가득 차서 요청이 거부됨."""
    bulkhead_name: str
    max_concurrent: int
    active_count: int

class BulkheadTimeoutError(BulkheadError):
    """ThreadPoolBulkhead에서 작업 실행 타임아웃."""
    bulkhead_name: str
    timeout: float
```

`BulkheadFullError`는 `PolicyOutcome.REJECTED`로, `BulkheadTimeoutError`는 `PolicyOutcome.TIMEOUT`으로 매핑한다.

## 3. 설계 결정 사항 (확정)

6가지 설계 질문에 대한 검토 결과와 확정 사항을 기록한다.

### 3.1 동기/비동기 Policy 클래스 — 분리 확정

**결정**: `BulkheadPolicy`와 `AsyncBulkheadPolicy`를 별도 클래스로 구현한다.

**코드 근거**:
- `AsyncSemaphoreBulkhead`는 `Bulkhead(ABC)`를 상속하지 않는 완전 독립 클래스 (`async_semaphore.py` L33)
- 225번 구현체(`interfaces/resilience_policy.py` L188-L244)에서 `ResiliencePolicy` / `AsyncResiliencePolicy` Protocol 분리 완료
- 기존 `@bulkhead` 데코레이터도 `asyncio.iscoroutinefunction()` 분기로 동기/비동기 경로 완전 분리 (`decorator.py` L86-L144)
- `is_async=True` 플래그 방식은 사용하지 않는 쪽 메서드에 `NotImplementedError`가 생겨 Protocol 위반

### 3.2 BulkheadRegistry 관계 — DI + 팩토리 확정

**결정**: `BulkheadPolicy` 생성자는 `Bulkhead` 인스턴스를 직접 주입받고, `bulkhead_policy()` 팩토리 함수가 Registry 연동을 담당한다.

**코드 근거**:
- `CircuitBreakerPolicy`가 동일 패턴 사용: `cb_service: CircuitBreakerService | None = None` → `None`이면 내부 기본 생성 (`services/circuit_breaker/policy.py` L69-L75)
- `BulkheadRegistry`는 싱글톤으로 전역 인스턴스 관리 → `get_or_create()` 반환 (`registry.py` L176-L206)
- 기존 `@bulkhead` 데코레이터가 `registry.get(key)` 패턴 사용 (`decorator.py` L97-L100)
- DI 방식은 테스트에서 Mock `Bulkhead` 주입 가능, Registry 미의존

### 3.3 acquire 타임아웃 — Fast Fail 유지 확정

**결정**: `timeout=None` 전달 시 즉시 실패(Fast Fail). 별도 기본값 불필요.

**코드 근거**:
- `SemaphoreBulkhead.acquire(timeout=None)` → `blocking=False` 즉시 시도 (`semaphore.py` L88)
- `AsyncSemaphoreBulkhead.acquire(timeout=None)` → `locked()` 체크 후 `timeout=0.001` 즉시 시도 (`async_semaphore.py` L82-L90)
- `base.py` docstring 명시: `"timeout: 대기 타임아웃 (초). None이면 즉시 실패 (논블로킹)."` (L104)
- `ThreadPoolBulkhead.acquire(timeout)` → `timeout` 파라미터 미사용(`noqa: ARG002`), 항상 즉시 용량 체크 (`threadpool.py` L106)

### 3.4 BulkheadFullError 처리 — 원본 예외 그대로 확정

**결정**: `BulkheadFullError`를 `result.error`에 그대로 담고, `metadata`에 상세 정보 포함.

**코드 근거**:
- `CircuitBreakerPolicy`도 동일 패턴: `error=CircuitBreakerOpenError(self._service_name)` → `REJECTED` (`services/circuit_breaker/policy.py` L171-L177)
- `BulkheadFullError`에 `bulkhead_name`, `max_concurrent`, `active_count` 필드 포함 (`exceptions.py` L29-L35)
- 소비자 구분: `isinstance(result.error, BulkheadFullError)` vs `CircuitBreakerOpenError` → 예외 타입으로 즉시 식별 가능
- `PolicyResult`의 3중 구분 체계: `outcome` + `error` + `metadata`로 충분

### 3.5 데코레이터 수정 — Phase 5 연기 확정

**결정**: 이번 Phase 2에서는 `BulkheadPolicy` / `AsyncBulkheadPolicy` 클래스만 구현. `@bulkhead` 데코레이터 수정은 Phase 5(231번 PolicyComposer 이후)로 연기.

**코드 근거**:
- 데코레이터 전환 코드가 `PolicyComposer.compose()`에 의존 → PolicyComposer 미구현 시 불가
- 224번 마스터 플랜: Phase 2 = "독립 패턴 Policy 래핑", Phase 4 = "PolicyComposer(231번)", Phase 5 = "데코레이터 마이그레이션"
- 현재 `@bulkhead` 데코레이터는 프로덕션에서 `BulkheadFullError` catch + fallback 호출로 정상 동작 중 (`decorator.py` L131-L137)

### 3.6 ThreadPoolBulkhead — 지원 확정 (isinstance 분기)

**결정**: `BulkheadPolicy.execute()` 내부에서 `isinstance` 체크로 `ThreadPoolBulkhead` 분기 처리. `BulkheadTimeoutError` → `PolicyOutcome.TIMEOUT` 매핑.

**코드 근거**:
- `ThreadPoolBulkhead`는 `Bulkhead(ABC)` 상속 + `acquire()` 컨텍스트 매니저 구현 (`threadpool.py` L106-L125)
- `ThreadPoolBulkhead.execute()` = `submit() → Future.result(timeout=30.0)` 동기 대기 (`threadpool.py` L175-L195)
- `ThreadPoolBulkhead.acquire()`의 `timeout` 파라미터는 미사용(`noqa: ARG002`), 항상 즉시 용량 체크 (`threadpool.py` L106)
- `BulkheadTimeoutError`는 `ThreadPoolBulkhead.execute()`에서만 발생 (`exceptions.py` L48-L58)
- DI 방식(`Bulkhead` 인스턴스 주입)이면 `SemaphoreBulkhead`/`ThreadPoolBulkhead`의 생성자 차이(`max_concurrent` vs `max_workers`+`queue_size`)가 소비자에게 노출되지 않음

## 4. 전환 설계

### 4.1 BulkheadPolicy 클래스

**구현 파일**: `resilience/bulkhead/policy.py`

파일 배치 근거: `RetryPolicy` → `services/retry_handler/policy.py`, `CircuitBreakerPolicy` → `services/circuit_breaker/policy.py` 선례를 따라 각 패턴 패키지 내부에 `policy.py` 배치.

```python
class BulkheadPolicy(ResiliencePolicy[T]):
    """
    Bulkhead Policy — 리소스 격리.

    내부적으로 기존 Bulkhead ABC 구현체를 재사용한다.

    예외 처리 컨트랙트 (CircuitBreakerPolicy와 동일):
    - BulkheadFullError → PolicyResult(outcome=REJECTED)로 흡수
    - BulkheadTimeoutError → PolicyResult(outcome=TIMEOUT)로 흡수 (ThreadPool 전용)
    - 함수 실행 중 비즈니스 예외 → raise로 재전파 (상위 Policy에서 처리)

    코드 근거: CircuitBreakerPolicy (services/circuit_breaker/policy.py L199)
    함수 실행 예외를 raise로 재전파하여 상위 Retry 등에서 catch하도록 함.

    상속 근거: 6개 Policy 전체 일관성.
    CircuitBreakerPolicy, FallbackPolicy, HedgingPolicy, ThrottlePolicy가
    명시적으로 ResiliencePolicy[T]를 상속하므로 동일 패턴을 적용한다.
    """

    def __init__(
        self,
        bulkhead: Bulkhead,
        timeout: float | None = None,
    ):
        """
        Args:
            bulkhead: Bulkhead ABC 구현체 (SemaphoreBulkhead, ThreadPoolBulkhead)
                      DI로 주입. Registry 조회는 bulkhead_policy() 팩토리 사용.
            timeout: 리소스 획득 타임아웃.
                     None이면 즉시 실패 (Fast Fail).
                     ThreadPoolBulkhead의 경우 execute() 실행 타임아웃으로 사용.
        """
        self._bulkhead = bulkhead
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "bulkhead"

    @property
    def bulkhead_name(self) -> str:
        """내부 Bulkhead 인스턴스의 도메인 식별자."""
        return self._bulkhead.name

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        격벽 리소스 내에서 함수 실행.

        ThreadPoolBulkhead 분기:
        - isinstance 체크 → ThreadPoolBulkhead.execute() 호출
        - 독립 스레드 풀 격리 + ContextVar 전파 활용
        - BulkheadTimeoutError → PolicyOutcome.TIMEOUT 매핑

        SemaphoreBulkhead 경로:
        - with acquire(timeout) 컨텍스트 매니저
        - BulkheadFullError → PolicyOutcome.REJECTED 매핑

        함수 실행 중 비즈니스 예외는 raise로 재전파한다.
        (CircuitBreakerPolicy와 동일한 예외 처리 컨트랙트)
        """
        from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead

        try:
            if isinstance(self._bulkhead, ThreadPoolBulkhead):
                # ThreadPool: 독립 스레드 풀에서 실행 + ContextVar 전파
                result = self._bulkhead.execute(
                    func, *args, timeout=self._timeout or 30.0, **kwargs,
                )
            else:
                # Semaphore: 현재 스레드에서 실행
                with self._bulkhead.acquire(timeout=self._timeout):
                    result = func(*args, **kwargs)

            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        except BulkheadFullError as e:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        except BulkheadTimeoutError as e:
            return PolicyResult(
                outcome=PolicyOutcome.TIMEOUT,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "timeout": self._timeout,
                    "state": self._get_state_dict(),
                },
            )
        # 함수 실행 중 비즈니스 예외는 catch하지 않고 상위로 전파 (raise)

    def _get_state_dict(self) -> dict:
        state = self._bulkhead.get_state()
        return {
            "active_count": state.active_count,
            "max_concurrent": state.max_concurrent,
            "available_permits": state.available_permits,
            "utilization_percent": state.utilization_percent,
        }
```

### 4.2 팩토리 함수 — `bulkhead_policy()`

```python
def bulkhead_policy(
    name: str,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    bulkhead_type: str = "semaphore",
) -> BulkheadPolicy:
    """
    BulkheadPolicy 팩토리 — BulkheadRegistry 싱글톤 통합.

    Registry의 get_or_create()를 호출하여 동일 name에 대해
    전역 단일 Bulkhead 인스턴스를 보장한다.

    Registry 의존성은 이 함수에서만 발생한다.
    BulkheadPolicy 클래스 자체는 Registry를 모른다 (테스트 용이).

    코드 근거:
    - registry.get_or_create(name, max_concurrent, bulkhead_type)
      → 기존 인스턴스가 있으면 반환 (registry.py L176-L206)
    - @bulkhead 데코레이터가 동일 패턴 사용 (decorator.py L97-L100)

    Args:
        name: 도메인 이름 (Registry 키)
        max_concurrent: 최대 동시 실행 수 (None이면 Registry 기본값)
        timeout: 리소스 획득 타임아웃 (None이면 즉시 실패)
        bulkhead_type: "semaphore" 또는 "thread_pool"

    Returns:
        BulkheadPolicy 인스턴스 (Registry 싱글톤 Bulkhead 사용)
    """
    from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

    registry = get_bulkhead_registry()
    bulkhead = registry.get_or_create(
        name=name,
        max_concurrent=max_concurrent,
        bulkhead_type=bulkhead_type,
    )
    return BulkheadPolicy(bulkhead=bulkhead, timeout=timeout)
```

### 4.3 기존 CircuitBreakerPolicy와의 패턴 비교

생성자 DI 패턴의 일관성을 검증한다:

| 항목 | CircuitBreakerPolicy | BulkheadPolicy |
|------|---------------------|----------------|
| 코드 위치 | `services/circuit_breaker/policy.py` | `resilience/bulkhead/policy.py` |
| 내부 구현체 | `cb_service: CircuitBreakerService` | `bulkhead: Bulkhead` |
| DI 방식 | 생성자 `cb_service` 매개변수 (L69) | 생성자 `bulkhead` 매개변수 |
| 기본 생성 | `None`이면 `_create_default_service()` (L88-L101) | 팩토리 함수 `bulkhead_policy()`가 전담 |
| `name` 프로퍼티 | `"circuit_breaker"` | `"bulkhead"` |
| REJECTED 반환 | `CircuitBreakerOpenError` → `REJECTED` (L171-L177) | `BulkheadFullError` → `REJECTED` |
| 함수 예외 처리 | `raise` 재전파 (L199) | `raise` 재전파 (동일) |
| Hook 통합 | `self._hooks` 리스트 + `_invoke_hooks()` (L130-L134) | Phase 4(PolicyComposer) 시점에 추가 |

### 4.4 @bulkhead 데코레이터의 fallback 분리 (Phase 5 연기)

> **⚠️ 이 작업은 Phase 5(231번 PolicyComposer 이후)로 연기한다.**
> Phase 2 범위에서는 기존 `@bulkhead` 데코레이터를 수정하지 않는다.

현재:
```python
# decorator.py — fallback이 bulkhead 내부에 하드코딩
@bulkhead("api", fallback=lambda: {"error": "service unavailable"})
def api_call():
    return requests.get(url)
```

Phase 5 전환 후 (참고):
```python
# Policy Composition — Fallback이 별도 Policy
policy = compose(
    bulkhead("api", max_concurrent=10),
    fallback(lambda: {"error": "service unavailable"}),
)
result = policy.execute(api_call)
```

Phase 5에서 기존 `@bulkhead` 데코레이터는 하위 호환을 위해 유지하되,
내부적으로 BulkheadPolicy + FallbackPolicy 조합으로 전환:

```python
# Phase 5 전환 후 decorator.py 내부
def bulkhead(name, timeout=None, fallback=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            policies = [bulkhead_policy(name, timeout=timeout)]
            if fallback:
                policies.append(FallbackPolicy(fallback_fn=fallback))
            composed = PolicyComposer.compose(*policies)
            result = composed.execute(fn, *args, **kwargs)
            if result.success:
                return result.value
            raise result.error
        return wrapper
    return decorator
```

## 5. 비동기 지원

### 5.1 AsyncBulkheadPolicy

`resilience/bulkhead/async_semaphore.py`에 이미 `AsyncSemaphoreBulkhead`가 존재한다.
`AsyncResiliencePolicy` Protocol을 따르며, 동기 `BulkheadPolicy`와 완전히 분리된 클래스이다.

**구현 파일**: `resilience/bulkhead/policy.py` (BulkheadPolicy와 같은 파일)

```python
class AsyncBulkheadPolicy:
    """
    비동기 Bulkhead Policy — AsyncResiliencePolicy Protocol 구현.

    AsyncSemaphoreBulkhead를 래핑하여 PolicyResult 형태로 반환한다.
    동기 BulkheadPolicy와 동일한 예외 처리 컨트랙트를 따른다.

    코드 근거:
    - AsyncSemaphoreBulkhead는 Bulkhead(ABC) 미상속, 별도 클래스 (async_semaphore.py L33)
    - AsyncResiliencePolicy Protocol의 execute()는 async def (resilience_policy.py L236)
    """

    def __init__(
        self,
        async_bulkhead: AsyncSemaphoreBulkhead,
        timeout: float | None = None,
    ):
        """
        Args:
            async_bulkhead: AsyncSemaphoreBulkhead 인스턴스 (DI)
            timeout: 리소스 획득 타임아웃 (None이면 즉시 실패)
        """
        self._async_bulkhead = async_bulkhead
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "bulkhead"

    @property
    def bulkhead_name(self) -> str:
        """내부 AsyncSemaphoreBulkhead의 도메인 식별자."""
        return self._async_bulkhead.name

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 격벽 리소스 내에서 함수 실행.

        BulkheadFullError → PolicyResult(outcome=REJECTED)로 흡수.
        함수 실행 중 비즈니스 예외는 raise로 재전파.
        """
        try:
            async with self._async_bulkhead.acquire(timeout=self._timeout):
                result = await func(*args, **kwargs)
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    executed_policies=["bulkhead"],
                    metadata={
                        "bulkhead_name": self._async_bulkhead.name,
                        "state": self._get_state_dict(),
                    },
                )
        except BulkheadFullError as e:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._async_bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        # 함수 실행 중 비즈니스 예외는 catch하지 않고 상위로 전파

    def _get_state_dict(self) -> dict:
        state = self._async_bulkhead.get_state()
        return {
            "active_count": state.active_count,
            "max_concurrent": state.max_concurrent,
            "available_permits": state.available_permits,
            "utilization_percent": state.utilization_percent,
        }
```

### 5.2 비동기 팩토리 함수

```python
def async_bulkhead_policy(
    name: str,
    max_concurrent: int | None = None,
    timeout: float | None = None,
) -> AsyncBulkheadPolicy:
    """
    AsyncBulkheadPolicy 팩토리 — BulkheadRegistry 싱글톤 통합.

    Registry의 get_async()를 호출하여 동일 name에 대해
    전역 단일 AsyncSemaphoreBulkhead 인스턴스를 보장한다.

    코드 근거: registry.get_async(key) → 동기 버전 설정 기반으로
    비동기 인스턴스 생성/반환 (registry.py L227-L242)
    """
    from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

    registry = get_bulkhead_registry()

    # 동기 Bulkhead가 없으면 먼저 생성 (비동기는 동기 설정 기반)
    if max_concurrent is not None:
        registry.get_or_create(name=name, max_concurrent=max_concurrent)

    async_bh = registry.get_async(name)
    return AsyncBulkheadPolicy(async_bulkhead=async_bh, timeout=timeout)
```

## 6. HedgingPolicy에서의 BulkheadPolicy 사용

225번 문서에서 설계한 대로, HedgingPolicy는 BulkheadPolicy를 내부 하위 Policy로 주입받는다:

```python
# 현재 하드코딩 (hedging/config.py L66-L75)
config = HedgingConfig(
    bulkhead_name="api",                    # 격벽 이름 문자열
    acquire_bulkhead_per_candidate=False,    # 전체/후보별 분기
)

# Policy Composition 전환 — 팩토리 함수로 Registry 싱글톤 보장
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    overall_policy=bulkhead_policy("api", max_concurrent=10),   # 전체 격벽
)
# 또는
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    per_candidate_policy=bulkhead_policy("api", max_concurrent=5),  # 후보별 격벽
)
```

## 7. 영향 범위

### 7.1 현재 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `resilience/bulkhead/decorator.py` | `@bulkhead` 데코레이터 — Registry에서 조회 |
| `core/hedging/strategy.py` L97 | `get_bulkhead_registry()` lazy import |
| `core/hedging/async_strategy.py` L97 | `get_bulkhead_registry()` lazy import |
| `scaling/traffic_gate.py` L126 | `bulkhead_name` 파라미터로 연동 |

### 7.2 변경 불필요

| 컴포넌트 | 이유 |
|----------|------|
| `base.py` | 추상 인터페이스 — 그대로 재사용 |
| `semaphore.py`, `threadpool.py`, `async_semaphore.py` | 구현체 — 그대로 재사용 |
| `registry.py` | 인프라 레이어 — Policy 외부에서 유지. 팩토리 함수에서만 참조 |
| `metrics.py`, `otel.py` | 관측성 — Phase 4에서 PolicyHook으로 연결 |
| `decorator.py` | Phase 5에서 수정 예정. 이번 단계에서는 변경 없음 |

### 7.3 네이밍 충돌 검증

| 이름 | Python 코드 내 존재 여부 | 판정 |
|------|------------------------|------|
| `BulkheadPolicy` | `.py` 파일 0건 (문서만 존재) | ✅ 충돌 없음 |
| `AsyncBulkheadPolicy` | `.py` 파일 0건 | ✅ 충돌 없음 |
| `bulkhead_policy` (팩토리) | `.py` 파일 0건 | ✅ 충돌 없음 |
| `async_bulkhead_policy` (팩토리) | `.py` 파일 0건 | ✅ 충돌 없음 |

기존 네이밍 패턴 일관성:
- `RetryPolicy` (`services/retry_handler/policy.py` L49) → `name` = `"retry"`
- `CircuitBreakerPolicy` (`services/circuit_breaker/policy.py` L40) → `name` = `"circuit_breaker"`
- `BulkheadPolicy` → `name` = `"bulkhead"` ✅ 일관

## 8. 체크리스트

### Phase 2 범위 (이번 단계)

- [x] `BulkheadPolicy` 클래스 생성 (`resilience/bulkhead/policy.py`)
  - Bulkhead DI 생성자
  - ThreadPoolBulkhead isinstance 분기
  - BulkheadFullError → REJECTED, BulkheadTimeoutError → TIMEOUT
  - 함수 실행 예외는 raise 재전파
- [x] `AsyncBulkheadPolicy` 클래스 생성 (같은 파일)
  - AsyncSemaphoreBulkhead DI 생성자
  - BulkheadFullError → REJECTED
  - 함수 실행 예외는 raise 재전파
- [x] `bulkhead_policy()` 팩토리 함수 (Registry 연동)
- [x] `async_bulkhead_policy()` 팩토리 함수 (Registry 연동)
- [x] `__init__.py` export 추가
- [x] 단위 테스트 작성 (`test_bulkhead_policy.py` — 89건 통과)
  - 계약 검증: name, outcome, executed_policies, Protocol 호환, export
  - 동작 검증: Semaphore 성공/REJECTED, ThreadPool 성공/TIMEOUT, 예외 재전파
  - 동작 검증: _get_state_dict, PolicyContext 전달, 팩토리 함수 (동기/비동기)
- [x] 기존 테스트 통과 확인
- [x] `ResiliencePolicy` Protocol 호환성 검증 완료 (`isinstance` = True)

### Phase 5 범위 (231번 이후)

- [ ] `@bulkhead` 데코레이터의 fallback 파라미터를 FallbackPolicy 조합으로 전환
- [ ] `@bulkhead_for_database`, `@bulkhead_for_cache` 데코레이터 전환

### 별도 문서 범위

- [ ] HedgingPolicy의 `per_candidate_policy`/`overall_policy` 인터페이스 설계 (230번 문서)
