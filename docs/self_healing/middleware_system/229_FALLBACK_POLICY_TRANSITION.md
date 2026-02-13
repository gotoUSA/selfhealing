# 229. FallbackPolicy 전환 설계

## 1. 개요

FallbackPolicy는 현재 시스템에서 **가장 분산된 패턴**이다.
Fallback 로직이 3곳에 각각 독립적으로 구현되어 있으며, 서로 다른 결과 타입을 사용한다.
Policy Composition 전환의 핵심은 이 3곳의 Fallback을 **하나의 FallbackPolicy로 통합**하는 것이다.

## 2. 현재 구현 분석

### 2.1 Fallback이 존재하는 3곳

| 위치 | 클래스/메서드 | 결과 타입 | 용도 |
|------|-------------|----------|------|
| `core/fallback_strategy.py` | `FallbackStrategy` ABC + 3개 구현체 | `FallbackResult` | 범용 Fallback (Primary → Fallback → Default) |
| `services/circuit_breaker/service.py` L293 | `should_allow_with_fallback()` | `CircuitBreakerFallbackResult` | CB OPEN 시 cache/dlq/default fallback |
| `resilience/bulkhead/decorator.py` | `@bulkhead(fallback=...)` | 없음 (직접 호출) | Bulkhead Full 시 fallback 함수 호출 |

**문제**: 동일한 개념(실패 시 대체 응답)이 3곳에 중복 구현되어 있고, 결과 타입이 서로 다르다.

### 2.2 core/fallback_strategy.py — 범용 Fallback (290줄)

#### 파일 구조

```
core/fallback_strategy.py
├── FallbackMode (Enum)           # L26-L33: 6가지 모드
├── FallbackResult (dataclass)    # L36-L48: 범용 결과 타입
├── FallbackStrategy (ABC)        # L52-L63: 추상 인터페이스
├── SimpleFallback                # L66-L109: Primary → Fallback → Default
├── PartitionAwareFallback        # L112-L219: 파티션 상태 기반 자동 선택
└── CacheFirstFallback            # L222-L290: 캐시 우선 → DB 폴백
```

#### FallbackMode — 6가지 모드 (L26-L33)

```python
class FallbackMode(str, Enum):
    FAIL_FAST = "fail_fast"          # 즉시 실패
    USE_CACHE = "use_cache"          # 캐시된 값 사용
    USE_DEFAULT = "use_default"      # 기본값 사용
    DEGRADE_GRACEFULLY = "degrade"   # 기능 축소
    RETRY_ALTERNATIVE = "retry_alt"  # 대체 경로 시도
    HEDGE = "hedge"                  # 병렬 헷징으로 인한 대체 응답
```

#### FallbackResult 결과 타입 (L36-L48)

```python
@dataclass
class FallbackResult(Generic[T]):
    value: T | None
    used_fallback: bool
    fallback_mode: FallbackMode | None = None
    original_error: str | None = None

    @property
    def success(self) -> bool:
        return self.value is not None or (
            self.used_fallback and self.fallback_mode != FallbackMode.FAIL_FAST
        )
```

#### FallbackStrategy ABC (L52-L63)

```python
class FallbackStrategy(ABC):
    @abstractmethod
    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        pass
```

**현재 시그니처 문제**: `execute()`가 `primary_fn`을 받는다.
Policy Composition에서는 FallbackPolicy가 **이전 Policy 실패 후 호출**되므로,
"이전 Policy가 실행한 primary_fn"의 결과(PolicyResult)를 입력으로 받아야 한다.

### 2.3 크로스-패턴 의존성

#### FallbackStrategy ABC 자체: 1건

| 의존 대상 | 위치 | 방식 | 제거 가능 |
|-----------|------|------|----------|
| `PartitionState` (connection_health) | L19 정적 import | `PartitionAwareFallback` 생성자 인자 | ⚠️ 부분적 — PartitionAwareFallback 전용 |

`PartitionState`는 `core/connection_health.py` L55에 정의된 dataclass:

```python
@dataclass
class PartitionState:
    db_available: bool = True
    cache_available: bool = True
    external_apis: dict[str, bool] = field(default_factory=dict)
```

**판단**: `PartitionState`는 Fallback의 "판단 조건"이지 크로스-패턴 의존이 아니다.
`PartitionAwareFallback`는 PartitionState를 **생성자 주입**으로 받으므로 이미 DI 구조이다.

#### Hedging → FallbackStrategy 상속: 1건

`core/hedging/strategy.py` L50:

```python
class HedgingStrategy(FallbackStrategy):
```

HedgingStrategy가 FallbackStrategy를 **상속**하여 `execute()` 시그니처를 재사용한다.
230번 HedgingPolicy 문서에서 이 상속 관계 해체를 상세히 다룬다.

#### CB → 자체 Fallback: 분리 대상

`services/circuit_breaker/service.py` L293-L360의 `should_allow_with_fallback()`는
CB 내부에 Fallback을 하드코딩한다. 227번 문서에서 이미 분리 대상으로 지정했다.

### 2.4 3개 구현체 상세 분석

#### SimpleFallback (L66-L109)

```python
class SimpleFallback(FallbackStrategy):
    def execute(self, primary_fn, fallback_fn=None, default_value=None):
        try:
            result = primary_fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            # 1. fallback_fn 시도
            # 2. default_value 반환
            # 3. 모든 실패 → FallbackMode.FAIL_FAST
```

**실행 순서**: Primary → Fallback 함수 → Default 값 → Fail Fast
**외부 의존성**: 0건. 완전 독립.

#### PartitionAwareFallback (L112-L219)

```python
class PartitionAwareFallback(FallbackStrategy):
    def __init__(
        self,
        partition_state: PartitionState,    # DI로 주입
        cache_fallback: Callable | None,
        db_fallback: Callable | None,
    ): ...

    def _handle_failure(self, error, fallback_fn, default_value):
        # 1. 명시적 fallback_fn 시도
        # 2. 캐시 불가 + DB 가용 → DB fallback (L165)
        # 3. DB 불가 + 캐시 가용 → 캐시 fallback (L181)
        # 4. default_value 반환
        # 5. 모든 실패 → FAIL_FAST
```

**실행 순서**: Primary → Fallback 함수 → (PartitionState 기반) DB/Cache → Default → Fail Fast
**외부 의존성**: `PartitionState` (생성자 주입 — 이미 DI)

#### CacheFirstFallback (L222-L290)

```python
class CacheFirstFallback(FallbackStrategy):
    def __init__(
        self,
        cache_fn: Callable,
        db_fn: Callable,
        update_cache_fn: Callable | None,
    ): ...

    def execute(self, primary_fn=None, fallback_fn=None, default_value=None):
        # primary_fn 무시! cache_fn이 primary 역할
        # 1. cache_fn() → hit 시 반환
        # 2. db_fn() → 성공 시 update_cache_fn() 호출 후 반환
        # 3. default_value
        # 4. FAIL_FAST
```

**주의**: `primary_fn` 파라미터를 **무시**한다. `cache_fn`이 실질적 primary.
이는 FallbackStrategy ABC의 계약을 위반하는 변칙적 사용이다.
**외부 의존성**: 0건. 완전 독립. (cache_fn, db_fn은 생성자 주입)

### 2.5 CB의 내장 Fallback (분리 대상)

`services/circuit_breaker/service.py` L293-L360 `should_allow_with_fallback()`:

```python
# CB OPEN 시 분기
if strategy == "cache" and cache_key:
    cached_data = self._get_cached_data(cache_key)     # Django cache 직접 접근 (L374)
    return CircuitBreakerFallbackResult.from_cache(data=cached_data)

if strategy == "dlq" and request_data:
    success = self._enqueue_to_dlq(service_name, request_data)  # dlq_service import (L395)
    return CircuitBreakerFallbackResult.to_dlq(...)

if strategy == "default_response" and default_response is not None:
    return CircuitBreakerFallbackResult.default_response(data=default_response)

return CircuitBreakerFallbackResult.block(...)
```

**문제점 3가지**:

1. **CB 내부에 cache/dlq/default 3가지 Fallback이 하드코딩**: Fallback 전략 추가 시 CB 수정 필요
2. **독자 결과 타입** `CircuitBreakerFallbackResult`: `FallbackResult`와 비호환
3. **Django cache 직접 import** (L374): `from django.core.cache import cache` → 프레임워크 의존

### 2.6 Bulkhead의 내장 Fallback (분리 대상)

`resilience/bulkhead/decorator.py`의 `@bulkhead(fallback=...)` 파라미터:
228번 문서에서 이미 분리 대상으로 지정. FallbackPolicy 외부화로 해결.

### 2.7 사용처 현황

| 사용처 | 사용 클래스 | 파일 |
|--------|-----------|------|
| Hedging | `HedgingStrategy(FallbackStrategy)` 상속 | `core/hedging/strategy.py` |
| Async Hedging | `FallbackResult`, `FallbackMode` import | `core/hedging/async_strategy.py` |
| 테스트 | `PartitionAwareFallback`, `SimpleFallback`, `CacheFirstFallback` | `tests/unit/chaos/test_partial_partition.py` |
| 패키지 export | `core/__init__.py` L65-71 | `SimpleFallback`, `PartitionAwareFallback`, `CacheFirstFallback`, `FallbackStrategy` |

## 3. 전환 설계

### 3.1 FallbackPolicy 클래스

```python
class FallbackPolicy(ResiliencePolicy[T]):
    """
    Fallback Policy — 실패 시 대체 응답 제공.

    현재 3곳에 분산된 Fallback 로직을 통합한다:
    - core/fallback_strategy.py의 FallbackStrategy 3개 구현체
    - services/circuit_breaker/service.py의 should_allow_with_fallback()
    - resilience/bulkhead/decorator.py의 @bulkhead(fallback=...)

    Policy Composition에서는 이전 Policy 실패 시 FallbackPolicy가 호출된다.
    """

    def __init__(
        self,
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
        fallback_chain: list[Callable[[], T]] | None = None,
        predicate: Callable[[PolicyResult[T]], bool] | None = None,
    ):
        """
        Args:
            fallback_fn: 단일 fallback 함수 (SimpleFallback 호환)
            default_value: 기본값 (모든 fallback 실패 시)
            fallback_chain: 순차 시도할 fallback 함수 리스트
            predicate: Fallback 활성화 조건 (기본: 실패 시 항상)
        """
        self._fallback_fn = fallback_fn
        self._default_value = default_value
        self._fallback_chain = fallback_chain or []
        self._predicate = predicate or self._default_predicate

    @staticmethod
    def _default_predicate(result: PolicyResult[T]) -> bool:
        """기본 조건: outcome이 SUCCESS가 아니면 Fallback 활성화."""
        return result.outcome != PolicyOutcome.SUCCESS

    def execute(self, func: Callable[..., T], *args, **kwargs) -> PolicyResult[T]:
        """
        func 실행 후 실패 시 Fallback 체인 순차 시도.

        실행 순서:
        1. func() 실행
        2. 성공 → PolicyResult(SUCCESS) 즉시 반환
        3. 실패 → predicate 확인
        4. fallback_chain 순차 시도 (설정된 경우)
        5. fallback_fn 시도 (단일 fallback)
        6. default_value 반환 (설정된 경우)
        7. 모든 실패 → PolicyResult(FAILURE)
        """
        # Step 1: Primary 실행
        try:
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                metadata={"fallback_used": False},
            )
        except Exception as primary_error:
            pass

        # Step 2: Fallback 체인 순차 시도
        for i, fallback in enumerate(self._fallback_chain):
            try:
                result = fallback()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    metadata={
                        "fallback_used": True,
                        "fallback_index": i,
                        "original_error": str(primary_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Fallback chain[{i}] failed: {e}")
                continue

        # Step 3: 단일 fallback_fn 시도
        if self._fallback_fn is not None:
            try:
                result = self._fallback_fn()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    metadata={
                        "fallback_used": True,
                        "fallback_source": "fallback_fn",
                        "original_error": str(primary_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Fallback function failed: {e}")

        # Step 4: Default 값 반환
        if self._default_value is not None:
            return PolicyResult(
                value=self._default_value,
                outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                metadata={
                    "fallback_used": True,
                    "fallback_source": "default_value",
                    "original_error": str(primary_error),
                },
            )

        # Step 5: 모든 실패
        return PolicyResult(
            value=None,
            outcome=PolicyOutcome.FAILURE,
            error=primary_error,
            metadata={"fallback_used": True, "all_fallbacks_exhausted": True},
        )
```

### 3.2 Composition 연동 — 이전 Policy 결과 기반 Fallback

FallbackPolicy는 단독 사용(`execute()`)과 Composition 사용이 모두 가능해야 한다.
Composition 모드에서는 **이전 Policy의 `PolicyResult`를 검사**하여 Fallback 여부를 결정한다:

```python
# PolicyComposer 내부에서의 호출 패턴
class PolicyComposer:
    def execute(self, func, *args, **kwargs):
        result = PolicyResult(...)
        for policy in self._policies:
            if isinstance(policy, FallbackPolicy):
                # FallbackPolicy는 이전 결과가 실패일 때만 활성화
                if policy._predicate(result):
                    result = policy.execute(func, *args, **kwargs)
            else:
                result = policy.execute(func, *args, **kwargs)
        return result
```

이 설계에서 Composer가 FallbackPolicy를 **조건부 실행**하므로,
`predicate` 커스터마이징으로 다양한 활성화 조건을 지원한다:

```python
# 예: CB REJECTED일 때만 Fallback 활성화
fallback = FallbackPolicy(
    fallback_fn=lambda: get_cached_response(),
    predicate=lambda r: r.outcome == PolicyOutcome.REJECTED,
)
```

### 3.3 PartitionAwareFallback → Policy 변환

PartitionAwareFallback의 PartitionState 기반 자동 선택 로직은
`fallback_chain` + `predicate` 조합으로 표현할 수 있다:

```python
# AS-IS: PartitionAwareFallback (하드코딩된 분기)
strategy = PartitionAwareFallback(
    partition_state=partition_state,
    cache_fallback=lambda: get_from_cache(),
    db_fallback=lambda: get_from_db(),
)
result = strategy.execute(primary_fn=lambda: call_api())

# TO-BE: FallbackPolicy + PartitionState predicate
def partition_aware_chain(partition_state: PartitionState) -> list[Callable]:
    """PartitionState 기반 동적 fallback chain 생성."""
    chain = []
    if not partition_state.cache_available and partition_state.db_available:
        chain.append(lambda: get_from_db())
    elif not partition_state.db_available and partition_state.cache_available:
        chain.append(lambda: get_from_cache())
    else:
        # 둘 다 가용 → 캐시 우선
        chain.extend([lambda: get_from_cache(), lambda: get_from_db()])
    return chain

fallback = FallbackPolicy(
    fallback_chain=partition_aware_chain(partition_state),
    default_value={"status": "degraded"},
)
```

### 3.4 CacheFirstFallback → Policy 변환

CacheFirstFallback의 "primary_fn 무시" 패턴은 FallbackPolicy에서 정리된다:

```python
# AS-IS: CacheFirstFallback (primary_fn 무시 — ABC 계약 위반)
strategy = CacheFirstFallback(
    cache_fn=lambda: redis.get("product:123"),
    db_fn=lambda: Product.objects.get(id=123),
    update_cache_fn=lambda v: redis.set("product:123", v),
)
result = strategy.execute(primary_fn=None)  # primary_fn 무시됨

# TO-BE: FallbackPolicy (정상적 execute() 계약 준수)
fallback = FallbackPolicy(
    fallback_chain=[
        lambda: db_get_and_cache("product:123"),
    ],
    default_value=None,
)

# cache_fn이 primary가 되는 구조:
result = compose(
    fallback,
).execute(lambda: redis_get("product:123"))
```

또는 cache_fn/db_fn의 계층 구조를 Composition으로 표현:

```python
# Cache → DB → Default를 Policy Chain으로 표현
cache_policy = TimeoutPolicy(timeout_ms=50)   # 캐시는 50ms 이내
db_fallback = FallbackPolicy(
    fallback_chain=[lambda: db_query("product:123")],
    default_value={"status": "not_found"},
)

result = compose(
    cache_policy,
    db_fallback,
).execute(lambda: redis_get("product:123"))
```

### 3.5 CB 내장 Fallback 분리

현재 `should_allow_with_fallback()` 내부의 cache/dlq/default 분기를 제거하고,
외부 FallbackPolicy로 분리한다:

```python
# AS-IS: CB 내부 하드코딩 (service.py L293-L360)
result = cb_service.should_allow_with_fallback(
    service_name="payment_api",
    cache_key="payment:cache:123",
    default_response={"status": "pending"},
    request_data={"order_id": 123},
)

# TO-BE: CB Policy + Fallback Policy 조합
result = compose(
    CircuitBreakerPolicy(service_name="payment_api"),
    FallbackPolicy(
        fallback_chain=[
            lambda: cache.get("payment:cache:123"),
        ],
        default_value={"status": "pending"},
        predicate=lambda r: r.outcome == PolicyOutcome.REJECTED,
    ),
).execute(lambda: call_payment_api(order_id=123))
```

`should_allow_with_fallback()` 메서드는 **deprecated** 처리 후 제거:

```python
# service.py — 과도기 처리
def should_allow_with_fallback(self, ...) -> CircuitBreakerFallbackResult:
    """
    .. deprecated:: 2.0
        Use CircuitBreakerPolicy + FallbackPolicy composition instead.
    """
    warnings.warn(
        "should_allow_with_fallback() is deprecated. "
        "Use compose(CircuitBreakerPolicy(...), FallbackPolicy(...)) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # 기존 로직 유지 (하위 호환)
    ...
```

## 4. 결과 타입 매핑

### 4.1 FallbackResult → PolicyResult 변환

| FallbackResult 필드 | PolicyResult 매핑 |
|--------------------|------------------|
| `value` | `PolicyResult.value` |
| `used_fallback=False` | `PolicyResult.outcome = SUCCESS` |
| `used_fallback=True` + `FAIL_FAST` | `PolicyResult.outcome = FAILURE` |
| `used_fallback=True` + `USE_CACHE` | `PolicyResult.outcome = SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="cache"` |
| `used_fallback=True` + `USE_DEFAULT` | `PolicyResult.outcome = SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="default"` |
| `used_fallback=True` + `DEGRADE_GRACEFULLY` | `PolicyResult.outcome = SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="degraded"` |
| `used_fallback=True` + `RETRY_ALTERNATIVE` | `PolicyResult.outcome = SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="alternative"` |
| `used_fallback=True` + `HEDGE` | `PolicyResult.outcome = SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="hedge"` |
| `original_error` | `PolicyResult.error` (Exception 변환) + `metadata["original_error"]` |

### 4.2 CircuitBreakerFallbackResult → PolicyResult

| CB Fallback 필드 | PolicyResult 매핑 |
|-----------------|------------------|
| `allowed=True` | 매핑 불필요 — CB Policy가 처리 |
| `fallback_used=True, fallback_type="cache"` | `SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="cache"` |
| `fallback_used=True, fallback_type="dlq"` | `SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="dlq"` |
| `allowed=False, fallback_used=False` | `REJECTED` — FallbackPolicy가 이어받음 |

### 4.3 FallbackMode → PolicyOutcome 매핑

```python
_FALLBACK_MODE_TO_OUTCOME = {
    FallbackMode.FAIL_FAST: PolicyOutcome.FAILURE,
    FallbackMode.USE_CACHE: PolicyOutcome.SUCCESS_WITH_FALLBACK,
    FallbackMode.USE_DEFAULT: PolicyOutcome.SUCCESS_WITH_FALLBACK,
    FallbackMode.DEGRADE_GRACEFULLY: PolicyOutcome.SUCCESS_WITH_FALLBACK,
    FallbackMode.RETRY_ALTERNATIVE: PolicyOutcome.SUCCESS_WITH_FALLBACK,
    FallbackMode.HEDGE: PolicyOutcome.SUCCESS_WITH_FALLBACK,
}
```

`FallbackMode` 정보는 `PolicyResult.metadata["fallback_mode"]`에 보존한다.
기존 코드가 `FallbackMode`를 참조하는 경우에 대한 하위 호환 지원.

## 5. 마이그레이션 전략

### 5.1 Phase 1 — FallbackPolicy 생성 (기존 코드 수정 없음)

```
resilience/policies/
├── __init__.py
├── base.py           # ResiliencePolicy (225번 문서)
├── retry.py          # RetryPolicy (226번 문서)
├── circuit_breaker.py # CircuitBreakerPolicy (227번 문서)
├── bulkhead.py       # BulkheadPolicy (228번 문서)
└── fallback.py       # FallbackPolicy ← 신규 생성
```

`fallback.py` 내부에서 기존 `FallbackStrategy` 구현체를 **재사용**한다:

```python
# resilience/policies/fallback.py
from selfhealing.core.fallback_strategy import (
    FallbackStrategy,
    SimpleFallback,
    FallbackResult,
    FallbackMode,
)

class FallbackPolicy(ResiliencePolicy[T]):
    """기존 FallbackStrategy 구현체를 Policy로 래핑."""

    def __init__(
        self,
        strategy: FallbackStrategy | None = None,
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
        fallback_chain: list[Callable[[], T]] | None = None,
        predicate: Callable[[PolicyResult[T]], bool] | None = None,
    ):
        # strategy가 주어지면 기존 구현체 재사용
        # 아니면 SimpleFallback 기반 신규 생성
        if strategy is not None:
            self._strategy = strategy
        else:
            self._strategy = None

        self._fallback_fn = fallback_fn
        self._default_value = default_value
        self._fallback_chain = fallback_chain or []
        self._predicate = predicate or self._default_predicate
```

### 5.2 Phase 2 — CB/Bulkhead 내장 Fallback deprecated

1. `should_allow_with_fallback()` → `DeprecationWarning` 추가
2. `@bulkhead(fallback=...)` → `DeprecationWarning` 추가
3. 사용처를 `compose(CircuitBreakerPolicy, FallbackPolicy)` 패턴으로 전환 안내

### 5.3 Phase 3 — HedgingStrategy 상속 해체

`HedgingStrategy(FallbackStrategy)` 상속을 제거하고 독립 Policy로 전환.
상세 내용은 **230번 HedgingPolicy 전환 설계**에서 다룬다.

### 5.4 Phase 4 — 기존 FallbackStrategy 구현체 유지

기존 3개 구현체(`SimpleFallback`, `PartitionAwareFallback`, `CacheFirstFallback`)는
**삭제하지 않는다**. FallbackPolicy의 `strategy` 파라미터로 주입 가능하므로
기존 사용자 코드와의 하위 호환성을 유지한다.

```python
# 기존 구현체 래핑 예시 — 하위 호환
from selfhealing.core.fallback_strategy import PartitionAwareFallback

partition_fallback = FallbackPolicy(
    strategy=PartitionAwareFallback(
        partition_state=partition_state,
        cache_fallback=cache_fn,
        db_fallback=db_fn,
    ),
)
```

## 6. Before/After 비교

### 6.1 SimpleFallback 사용

```python
# === BEFORE (현재) ===
from selfhealing.core.fallback_strategy import SimpleFallback

strategy = SimpleFallback()
result = strategy.execute(
    primary_fn=lambda: call_external_api(),
    fallback_fn=lambda: get_cached_response(),
    default_value={"status": "unavailable"},
)
if result.success:
    return result.value

# === AFTER (Policy Composition) ===
from selfhealing.resilience.policies import FallbackPolicy, compose

result = compose(
    FallbackPolicy(
        fallback_fn=lambda: get_cached_response(),
        default_value={"status": "unavailable"},
    ),
).execute(lambda: call_external_api())

assert result.outcome in (PolicyOutcome.SUCCESS, PolicyOutcome.SUCCESS_WITH_FALLBACK)
return result.value
```

### 6.2 CB + Fallback 조합

```python
# === BEFORE (현재) ===
from selfhealing.services import get_circuit_breaker_service

cb = get_circuit_breaker_service()
result = cb.should_allow_with_fallback(
    service_name="payment_api",
    cache_key="payment:123",
    default_response={"status": "pending"},
)
if result.allowed:
    response = call_payment_api()
elif result.fallback_used:
    response = result.fallback_data
else:
    raise ServiceUnavailableError(result.message)

# === AFTER (Policy Composition) ===
from selfhealing.resilience.policies import (
    CircuitBreakerPolicy, FallbackPolicy, compose,
)

result = compose(
    CircuitBreakerPolicy(service_name="payment_api"),
    FallbackPolicy(
        fallback_chain=[lambda: cache.get("payment:123")],
        default_value={"status": "pending"},
        predicate=lambda r: r.outcome == PolicyOutcome.REJECTED,
    ),
).execute(lambda: call_payment_api())
```

### 6.3 Retry + Fallback 조합

```python
# === BEFORE (현재) ===
# RetryHandler에 fallback 개념이 없음 — DLQ로 이동만 가능
handler = RetryHandler(config=RetryConfig(max_retries=3))
result = handler.execute(call_api)
if not result.success:
    # 소비자가 직접 fallback 처리
    return get_cached_response()

# === AFTER (Policy Composition) ===
result = compose(
    RetryPolicy(max_retries=3),
    FallbackPolicy(
        fallback_fn=lambda: get_cached_response(),
    ),
).execute(lambda: call_api())
# Retry 3회 실패 → FallbackPolicy 자동 활성화 → 캐시 응답
```

## 7. 엔터프라이즈 유연성

### 7.1 커스텀 Fallback 체인

```python
# 엔터프라이즈 고객: 다단계 Fallback 체인
fallback = FallbackPolicy(
    fallback_chain=[
        lambda: redis_cache.get("product:123"),           # L1: Redis
        lambda: local_cache.get("product:123"),            # L2: Local LRU
        lambda: cdn_stale.get("product:123"),              # L3: CDN Stale
        lambda: db_replica.query("SELECT ... LIMIT 1"),    # L4: Read Replica
    ],
    default_value={"status": "temporarily_unavailable"},
)
```

### 7.2 조건부 Fallback (predicate 커스터마이징)

```python
# Timeout 시에만 Fallback, 4xx 에러에는 Fallback 하지 않음
fallback = FallbackPolicy(
    fallback_fn=lambda: get_cached_response(),
    predicate=lambda r: r.outcome == PolicyOutcome.TIMEOUT,
)

# Retry 소진 + CB OPEN 모두 Fallback
fallback = FallbackPolicy(
    fallback_fn=lambda: get_cached_response(),
    predicate=lambda r: r.outcome in (PolicyOutcome.FAILURE, PolicyOutcome.REJECTED),
)
```

### 7.3 Fallback 비활성화

```python
# Fallback 없이 Retry + CB만 사용 (FallbackPolicy 생략)
result = compose(
    RetryPolicy(max_retries=3),
    CircuitBreakerPolicy(service_name="api"),
).execute(lambda: call_api())
# Retry 소진 시 즉시 FAILURE — Fallback 없음
```

## 8. 참조

- **225번 문서**: `PolicyResult`, `PolicyOutcome`, `ResiliencePolicy` 인터페이스 정의
- **227번 문서**: `should_allow_with_fallback()` 분리 설계
- **228번 문서**: `@bulkhead(fallback=...)` 분리 설계
- **230번 문서**: HedgingStrategy → FallbackStrategy 상속 해체 (예정)
- **231번 문서**: PolicyComposer의 FallbackPolicy 조건부 실행 로직 (예정)
