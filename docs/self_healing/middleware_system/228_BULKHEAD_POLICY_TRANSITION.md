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
├── exceptions.py     # BulkheadFullError
├── metrics.py        # Prometheus 메트릭
├── otel.py           # OpenTelemetry 통합
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

## 3. 전환 설계

### 3.1 BulkheadPolicy 클래스

```python
class BulkheadPolicy(ResiliencePolicy[T]):
    """
    Bulkhead Policy — 리소스 격리.

    내부적으로 기존 Bulkhead ABC 구현체를 재사용한다.
    base.py의 wrap() 메서드를 PolicyResult 형태로 감싼다.

    base.py가 완전 독립(크로스 의존 0건)이므로 래핑이 가장 직접적이다.
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
        timeout: float | None = None,
        bulkhead_type: BulkheadType = BulkheadType.SEMAPHORE,
    ):
        self._name = name
        self._timeout = timeout

        # 기존 구현체 재사용
        if bulkhead_type == BulkheadType.SEMAPHORE:
            from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
            self._bulkhead = SemaphoreBulkhead(
                name=name, max_concurrent=max_concurrent,
            )
        elif bulkhead_type == BulkheadType.THREAD_POOL:
            from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead
            self._bulkhead = ThreadPoolBulkhead(
                name=name, max_concurrent=max_concurrent,
            )

    @property
    def name(self) -> str:
        return "bulkhead"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        격벽 리소스 내에서 함수 실행.

        1. 리소스 획득 시도
        2. 성공 → func 실행 → 리소스 해제
        3. 실패 (BulkheadFullError) → REJECTED 반환
        """
        try:
            with self._bulkhead.acquire(timeout=self._timeout):
                result = func(*args, **kwargs)
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    executed_policies=["bulkhead"],
                    metadata={
                        "bulkhead_name": self._name,
                        "state": self._get_state_dict(),
                    },
                )
        except BulkheadFullError as e:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._name,
                    "state": self._get_state_dict(),
                },
            )

    def _get_state_dict(self) -> dict:
        state = self._bulkhead.get_state()
        return {
            "active_count": state.active_count,
            "max_concurrent": state.max_concurrent,
            "available_permits": state.available_permits,
            "utilization_percent": state.utilization_percent,
        }
```

### 3.2 @bulkhead 데코레이터의 fallback 분리

현재:
```python
# decorator.py — fallback이 bulkhead 내부에 하드코딩
@bulkhead("api", fallback=lambda: {"error": "service unavailable"})
def api_call():
    return requests.get(url)
```

전환 후:
```python
# Policy Composition — Fallback이 별도 Policy
policy = compose(
    bulkhead("api", max_concurrent=10),
    fallback(lambda: {"error": "service unavailable"}),
)
result = policy.execute(api_call)
```

기존 `@bulkhead` 데코레이터는 하위 호환을 위해 유지하되,
내부적으로 BulkheadPolicy + FallbackPolicy 조합으로 전환:

```python
# 전환 후 decorator.py 내부
def bulkhead(name, timeout=None, fallback=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            policies = [BulkheadPolicy(name, timeout=timeout)]
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

## 4. 비동기 지원

### 4.1 AsyncBulkheadPolicy

`resilience/bulkhead/async_semaphore.py`에 이미 `AsyncSemaphoreBulkhead`가 존재한다.

```python
class AsyncBulkheadPolicy(ResiliencePolicy[T]):
    """비동기 Bulkhead Policy."""

    async def execute_async(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        try:
            async with self._async_bulkhead.acquire(timeout=self._timeout):
                result = await func(*args, **kwargs)
                return PolicyResult(value=result, outcome=PolicyOutcome.SUCCESS, ...)
        except BulkheadFullError as e:
            return PolicyResult(outcome=PolicyOutcome.REJECTED, error=e, ...)
```

## 5. HedgingPolicy에서의 BulkheadPolicy 사용

225번 문서에서 설계한 대로, HedgingPolicy는 BulkheadPolicy를 내부 하위 Policy로 주입받는다:

```python
# 현재 하드코딩 (hedging/config.py L66-L75)
config = HedgingConfig(
    bulkhead_name="api",                    # 격벽 이름 문자열
    acquire_bulkhead_per_candidate=False,    # 전체/후보별 분기
)

# Policy Composition 전환
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    overall_policy=BulkheadPolicy("api", max_concurrent=10),   # 전체 격벽
)
# 또는
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    per_candidate_policy=BulkheadPolicy("api", max_concurrent=5),  # 후보별 격벽
)
```

## 6. 영향 범위

### 6.1 현재 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `resilience/bulkhead/decorator.py` | `@bulkhead` 데코레이터 — Registry에서 조회 |
| `core/hedging/strategy.py` L97 | `get_bulkhead_registry()` lazy import |
| `core/hedging/async_strategy.py` L97 | `get_bulkhead_registry()` lazy import |
| `scaling/traffic_gate.py` L126 | `bulkhead_name` 파라미터로 연동 |

### 6.2 변경 불필요

| 컴포넌트 | 이유 |
|----------|------|
| `base.py` | 추상 인터페이스 — 그대로 재사용 |
| `semaphore.py`, `threadpool.py`, `async_semaphore.py` | 구현체 — 그대로 재사용 |
| `registry.py` | 인프라 레이어 — Policy 외부에서 유지 |
| `metrics.py`, `otel.py` | 관측성 — PolicyHook으로 연결 |

## 7. 체크리스트

- [ ] `BulkheadPolicy` 클래스 생성 (SemaphoreBulkhead 래핑)
- [ ] `AsyncBulkheadPolicy` 클래스 생성 (AsyncSemaphoreBulkhead 래핑)
- [ ] `@bulkhead` 데코레이터의 fallback 파라미터를 FallbackPolicy 조합으로 전환
- [ ] HedgingPolicy의 `per_candidate_policy`/`overall_policy` 인터페이스 설계
- [ ] 기존 테스트 통과 확인
