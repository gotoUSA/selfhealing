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

FallbackPolicy는 두 가지 사용 모드를 지원한다:

1. **단독 사용**: `execute(func)` — func 실행 후 실패 시 Fallback 체인 시도
2. **Composer 체인 내 사용**: `_apply_fallback(original_error)` — 이전 Policy에서 이미 실패한 상황에서 Fallback 체인만 시도

이 분리는 231번 문서(PolicyComposer)의 `fallback_wrapper`에서 `fb.execute(lambda: inner())`를 호출할 때
이미 실패한 `inner()`를 **다시 실행하는 중복 호출 문제**를 해결한다.

**중복 실행 문제의 코드 근거** — 231번 문서 §3.2 `_execute_policy_chain()`:

```python
# 231번 문서의 기존 fallback_wrapper (문제 있음)
def fallback_wrapper(inner=outer_fn, fb=current_policy):
    try:
        value = inner()              # ← 내부 체인 실행 (1회차)
    except Exception as e:
        result = PolicyResult(outcome=PolicyOutcome.FAILURE, error=e)

    if fb._predicate(result):
        return fb.execute(lambda: inner()).value  # ← inner()를 또 실행 (2회차) — 중복!
```

**해결**: Composer는 실패 감지 후 `_apply_fallback(error)`를 호출하여 func 재실행 없이
fallback_chain → fallback_fn → default_value 순서만 시도한다.

```python
class FallbackPolicy(ResiliencePolicy[T]):
    """
    Fallback Policy — 실패 시 대체 응답 제공.

    현재 3곳에 분산된 Fallback 로직을 통합한다:
    - core/fallback_strategy.py의 FallbackStrategy 3개 구현체
    - services/circuit_breaker/service.py의 should_allow_with_fallback()
    - resilience/bulkhead/decorator.py의 @bulkhead(fallback=...)

    두 가지 실행 경로:
    - execute(func): 단독 사용 — func 실행 후 실패 시 Fallback
    - _apply_fallback(error): Composer 전용 — func 재실행 없이 Fallback만 시도
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

    @property
    def name(self) -> str:
        return "fallback"

    @staticmethod
    def _default_predicate(result: PolicyResult[T]) -> bool:
        """기본 조건: outcome이 SUCCESS가 아니면 Fallback 활성화."""
        return result.outcome != PolicyOutcome.SUCCESS

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        단독 사용 — func 실행 후 실패 시 Fallback 체인 순차 시도.

        ResiliencePolicy Protocol 구현. BulkheadPolicy, CircuitBreakerPolicy와
        동일한 시그니처(execute(func, *args, context=, **kwargs))를 따른다.

        실행 순서:
        1. func() 실행
        2. 성공 → PolicyResult(SUCCESS) 즉시 반환
        3. 실패 → _apply_fallback(error) 위임
        """
        # Step 1: Primary 실행
        try:
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["fallback"],
                metadata={"fallback_used": False},
            )
        except Exception as primary_error:
            # Step 2: Fallback 위임 (func 재실행 없음)
            return self._apply_fallback(
                original_error=primary_error,
                context=context,
            )

    def _apply_fallback(
        self,
        original_error: Exception,
        context: PolicyContext | None = None,
    ) -> PolicyResult[T]:
        """
        Composer 전용 — func 재실행 없이 Fallback 체인만 시도.

        PolicyComposer._execute_policy_chain()의 fallback_wrapper에서 호출된다.
        이전 Policy 체인에서 이미 실패한 상황이므로 func를 다시 실행하지 않는다.

        이 메서드는 FallbackPolicy 고유의 요구사항이다:
        - CircuitBreakerPolicy.execute(): 단일 경로 (CB 체크 → 실행 → 결과)
        - RetryPolicy.execute(): 단일 경로 (루프 내 재시도)
        - BulkheadPolicy.execute(): 단일 경로 (슬롯 획득 → 실행)
        위 3개 Policy는 execute()만으로 단독/Composer 양쪽 모두 동작하지만,
        FallbackPolicy만 "단독 시 func 실행 + Composer 시 func 미실행"이 필요하다.

        실행 순서:
        1. fallback_chain 순차 시도 (설정된 경우)
        2. fallback_fn 시도 (단일 fallback)
        3. default_value 반환 (설정된 경우)
        4. 모든 실패 → PolicyResult(FAILURE)

        Args:
            original_error: 이전 Policy 체인에서 발생한 원본 예외
            context: PolicyContext (Guard/Hook/Sink 전파용)

        Returns:
            PolicyResult[T]: Fallback 결과. 예외를 던지지 않는다.
        """
        # Step 1: Fallback 체인 순차 시도
        for i, fallback in enumerate(self._fallback_chain):
            try:
                result = fallback()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    executed_policies=["fallback"],
                    metadata={
                        "fallback_used": True,
                        "fallback_index": i,
                        "original_error": str(original_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Fallback chain[{i}] failed: {e}")
                continue

        # Step 2: 단일 fallback_fn 시도
        if self._fallback_fn is not None:
            try:
                result = self._fallback_fn()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    executed_policies=["fallback"],
                    metadata={
                        "fallback_used": True,
                        "fallback_source": "fallback_fn",
                        "original_error": str(original_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Fallback function failed: {e}")

        # Step 3: Default 값 반환
        if self._default_value is not None:
            return PolicyResult(
                value=self._default_value,
                outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                executed_policies=["fallback"],
                metadata={
                    "fallback_used": True,
                    "fallback_source": "default_value",
                    "original_error": str(original_error),
                },
            )

        # Step 4: 모든 실패
        return PolicyResult(
            value=None,
            outcome=PolicyOutcome.FAILURE,
            error=original_error,
            executed_policies=["fallback"],
            metadata={"fallback_used": True, "all_fallbacks_exhausted": True},
        )
```

**네이밍 근거 — `_apply_fallback` 선정 이유**:

| 후보 이름 | 기존 시스템 사용 건수 | 판정 |
|-----------|---------------------|------|
| `_apply_fallback` | 0건 | ✅ 안전 — 충돌 없음 |
| `handle_failure` | 11건 (`FailureSink.handle_failure()`, `DLQSink.handle_failure()`, `PaymentRecoveryService.handle_failure()` 등) | ❌ 충돌 — `FailureSink` Protocol과 의미론 상이 |
| `_handle_failure` | 2건 (`PartitionAwareFallback._handle_failure()`, `rate_limit.py._handle_failure()`) | ❌ 충돌 — 기존 fallback_strategy.py 내부 메서드와 혼동 |

`handle_failure`는 `FailureSink` Protocol(`interfaces/resilience_policy.py` L333)에서
**"모든 Policy 소진 후 최종 실패 → DLQ 저장"** 용도로 사용된다.
`_apply_fallback`은 **"실패 시 대체 응답 제공"**으로 역할이 다르다.

**Composer 연동 설계 원칙** (231번 문서 구현 시 적용):

```python
# 231번 문서 _execute_policy_chain()에서의 수정된 fallback_wrapper
def fallback_wrapper(inner=outer_fn, fb=current_policy):
    try:
        value = inner()  # 내부 체인 실행 (1회)
        return value
    except Exception as e:
        # 실패 감지 → _apply_fallback() 직접 호출 (func 재실행 없음)
        result = PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)
        if fb._predicate(result):
            fb_result = fb._apply_fallback(original_error=e)
            if fb_result.success:
                return fb_result.value
            raise e  # Fallback도 실패 → 원본 예외 전파
        raise e  # predicate 미충족 → 원본 예외 전파
```

### 3.2 Composition 연동 — `_apply_fallback` 기반 중복 실행 방지

FallbackPolicy는 단독 사용(`execute()`)과 Composition 사용이 모두 가능해야 한다.

**단독 사용**: `execute(func)` 호출 → func 실행 → 실패 시 `_apply_fallback(error)` 위임
**Composer 체인**: Composer가 실패를 감지하면 `_apply_fallback(error)` 직접 호출 → func 재실행 없음

Composer는 `_apply_fallback()`을 호출하므로 `execute()`를 경유하지 않는다.
이로써 이전 Policy 체인에서 이미 실패한 func를 다시 실행하는 중복 호출 문제가 해결된다.

```python
# PolicyComposer._execute_policy_chain() 내부 fallback_wrapper (수정된 설계)
def fallback_wrapper(inner=outer_fn, fb=current_policy):
    try:
        value = inner()  # 내부 체인 실행 (1회만)
        return value
    except Exception as e:
        # predicate 확인 → _apply_fallback 직접 호출
        result = PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)
        if fb._predicate(result):
            fb_result = fb._apply_fallback(original_error=e)
            if fb_result.success:
                return fb_result.value
        raise e  # Fallback 미적용 또는 실패 → 원본 예외 전파
```

**실행 흐름 비교**:

| 사용 모드 | 호출 경로 | func 실행 횟수 |
|-----------|---------|---------------|
| 단독 사용 | `execute(func)` → func() → 실패 시 `_apply_fallback()` | 1회 |
| Composer 체인 | Composer → inner() 실행 → 실패 감지 → `_apply_fallback()` 직접 호출 | 1회 (내부 체인) |
| ~~기존 설계 (삭제)~~ | ~~Composer → inner() 실행 → `execute(lambda: inner())` → inner() 재실행~~ | ~~2회 (중복)~~ |

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
`fallback_chain` + `predicate` 조합으로 표현할 수 있다.

#### Stale State 문제와 Provider 패턴

`PartitionState`는 `core/connection_health.py` L55에 정의된 **mutable** dataclass이다.
(`@dataclass`이며 `frozen=True`가 아님). 기존 `PartitionAwareFallback`도
`update_partition_state()` 메서드(`fallback_strategy.py` L219)로 수동 갱신을 지원하지만,
호출자가 갱신을 잊으면 stale 상태로 판단하는 문제가 있다.

fallback chain을 **생성 시점**에 구성하면 그 시점의 PartitionState가 고정된다:

```python
# ❌ 문제: chain 구성 시점에 분기 결정이 고정됨 (Stale)
def partition_aware_chain(partition_state: PartitionState) -> list[Callable]:
    chain = []
    if not partition_state.cache_available:  # ← 이 시점의 값으로 고정
        chain.append(lambda: get_from_db())
    return chain

fallback = FallbackPolicy(
    fallback_chain=partition_aware_chain(partition_state),  # 생성 시점에 고정
)
```

해결책: `Callable[[], PartitionState]` 형태의 **Provider**를 주입하고,
각 lambda가 **실행 시점**에 최신 상태를 조회하도록 한다.

```python
# AS-IS: PartitionAwareFallback (인스턴스 직접 주입, 수동 갱신 필요)
strategy = PartitionAwareFallback(
    partition_state=partition_state,           # 참조 저장 + update_partition_state() 수동 호출
    cache_fallback=lambda: get_from_cache(),
    db_fallback=lambda: get_from_db(),
)
result = strategy.execute(primary_fn=lambda: call_api())

# TO-BE: FallbackPolicy + state_provider (실행 시점 최신 상태 조회)
def partition_aware_chain(
    state_provider: Callable[[], PartitionState],
) -> list[Callable]:
    """
    PartitionState Provider 기반 동적 fallback chain 생성.

    각 fallback lambda가 실행되는 시점에 state_provider()를 호출하여
    최신 PartitionState를 조회한다. 이로써 생성 시점의 상태 고정(Stale) 문제를 방지한다.

    Args:
        state_provider: 실행 시점마다 최신 PartitionState를 반환하는 공급자 함수.
                        예: lambda: connection_health_monitor.get_state()
    """
    def db_fallback():
        ps = state_provider()  # 실행 시점에 최신 상태 조회
        if ps.db_available:
            return get_from_db()
        raise RuntimeError("DB unavailable at fallback execution time")

    def cache_fallback():
        ps = state_provider()  # 실행 시점에 최신 상태 조회
        if ps.cache_available:
            return get_from_cache()
        raise RuntimeError("Cache unavailable at fallback execution time")

    # 캐시 우선 → DB 순서. 각 lambda 실행 시 가용성을 실시간 체크.
    return [cache_fallback, db_fallback]

fallback = FallbackPolicy(
    fallback_chain=partition_aware_chain(
        state_provider=lambda: connection_health_monitor.get_state(),
    ),
    default_value={"status": "degraded"},
)
```

**Provider 패턴 선택 근거**:

| 방식 | 장점 | 단점 |
|------|------|------|
| 인스턴스 직접 주입 (현재 `PartitionAwareFallback`) | 단순함 | stale 위험, `update_partition_state()` 수동 호출 필요 |
| `Callable[[], PartitionState]` Provider | 실행 시점 최신 상태 보장 | lambda 내부 조회 패턴 필요 |
| `fallback_chain` 자체를 `Callable[[], list[Callable]]`로 | 완전 동적 | Callable 중첩으로 API 복잡 |

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

### 3.6 AsyncFallbackPolicy 설계

225번 문서에서 `ResiliencePolicy`(동기)와 `AsyncResiliencePolicy`(비동기) Protocol을
별도로 분리했다(`interfaces/resilience_policy.py` L195, L217).
FallbackPolicy도 동일 원칙에 따라 비동기 버전을 별도 클래스로 구현한다.

**기존 동기/비동기 분리 선례 (3건)**:

| 동기 | 비동기 | 파일 | 관계 |
|------|--------|------|------|
| `BulkheadPolicy` | `AsyncBulkheadPolicy` | `resilience/bulkhead/policy.py` L49, L174 | 별도 클래스, 상속 없음 |
| `SemaphoreBulkhead` | `AsyncSemaphoreBulkhead` | `bulkhead/base.py`, `async_semaphore.py` | 별도 클래스, 상속 없음 |
| `HedgingStrategy` | `AsyncHedgingStrategy` | `core/hedging/strategy.py`, `async_strategy.py` L49 | 별도 클래스, 상속 없음 |

```python
class AsyncFallbackPolicy:
    """
    비동기 Fallback Policy — AsyncResiliencePolicy Protocol 구현.

    동기 FallbackPolicy와 동일한 Fallback 체인 로직을 비동기로 제공한다.
    BulkheadPolicy/AsyncBulkheadPolicy 분리 선례와 동일한 패턴.

    제약 사항 — 소비자 책임(Consumer Responsibility):
    fallback_chain, fallback_fn에 전달하는 함수는 반드시 async def여야 한다.
    동기 Fallback 함수를 혼용하려면 소비자가 asyncio.to_thread()로 래핑하여 주입한다.
    이 원칙은 AsyncHedgingStrategy의 candidates 타입
    (list[Callable[[], Awaitable[T]]], async_strategy.py L77)과 동일하다.
    """

    def __init__(
        self,
        fallback_fn: Callable[[], Awaitable[T]] | None = None,
        default_value: T | None = None,
        fallback_chain: list[Callable[[], Awaitable[T]]] | None = None,
        predicate: Callable[[PolicyResult[T]], bool] | None = None,
    ):
        self._fallback_fn = fallback_fn
        self._default_value = default_value
        self._fallback_chain = fallback_chain or []
        self._predicate = predicate or self._default_predicate

    @property
    def name(self) -> str:
        return "fallback"

    @staticmethod
    def _default_predicate(result: PolicyResult[T]) -> bool:
        return result.outcome != PolicyOutcome.SUCCESS

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        단독 사용 — 비동기 func 실행 후 실패 시 Fallback.

        AsyncBulkheadPolicy.execute() (policy.py L214)와 동일 패턴:
        await func(*args, **kwargs) 호출 후 결과를 PolicyResult로 반환.
        """
        try:
            result = await func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["fallback"],
                metadata={"fallback_used": False},
            )
        except Exception as primary_error:
            return await self._apply_fallback(
                original_error=primary_error,
                context=context,
            )

    async def _apply_fallback(
        self,
        original_error: Exception,
        context: PolicyContext | None = None,
    ) -> PolicyResult[T]:
        """
        AsyncPolicyComposer 전용 — 비동기 Fallback 체인만 시도.

        동기 FallbackPolicy._apply_fallback()의 비동기 대응.
        """
        for i, fallback in enumerate(self._fallback_chain):
            try:
                result = await fallback()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    executed_policies=["fallback"],
                    metadata={
                        "fallback_used": True,
                        "fallback_index": i,
                        "original_error": str(original_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Async fallback chain[{i}] failed: {e}")
                continue

        if self._fallback_fn is not None:
            try:
                result = await self._fallback_fn()
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    executed_policies=["fallback"],
                    metadata={
                        "fallback_used": True,
                        "fallback_source": "fallback_fn",
                        "original_error": str(original_error),
                    },
                )
            except Exception as e:
                logger.warning(f"Async fallback function failed: {e}")

        if self._default_value is not None:
            return PolicyResult(
                value=self._default_value,
                outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                executed_policies=["fallback"],
                metadata={
                    "fallback_used": True,
                    "fallback_source": "default_value",
                    "original_error": str(original_error),
                },
            )

        return PolicyResult(
            value=None,
            outcome=PolicyOutcome.FAILURE,
            error=original_error,
            executed_policies=["fallback"],
            metadata={"fallback_used": True, "all_fallbacks_exhausted": True},
        )
```

**소비자 책임 예시 — 동기 Fallback 함수 래핑**:

```python
import asyncio

# 동기 함수를 비동기 AsyncFallbackPolicy에서 사용하려면
# 소비자가 asyncio.to_thread()로 직접 래핑해야 한다.
async_fallback = AsyncFallbackPolicy(
    fallback_chain=[
        lambda: asyncio.to_thread(sync_cache_get, "product:123"),  # 동기 → 비동기 래핑
        lambda: async_db_query("product:123"),                      # 네이티브 비동기
    ],
    default_value={"status": "unavailable"},
)

# Composer에서도 비동기 전용:
result = await compose_async(
    AsyncBulkheadPolicy(async_bulkhead=bh),
    async_fallback,
).execute(lambda: async_call_api())
```

**Composer 분리**: 231번 문서 §3.4에서 `compose()` → `PolicyComposer`(동기),
`compose_async()` → `AsyncPolicyComposer`(비동기)로 분리되어 있다.
`AsyncFallbackPolicy`는 `compose_async()`에서만 사용 가능하며,
동기 `compose()`에 혼용하면 Mypy 타입 에러 또는 런타임 TypeError가 발생한다.

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

### 4.2 CircuitBreakerFallbackResult → PolicyResult (CB 측 과도기 변환)

> **중요**: 이 매핑 테이블은 **FallbackPolicy 내부에 구현되지 않는다.**
> 이 변환 로직은 과도기 동안 기존 `should_allow_with_fallback()` 메서드가
> 내부적으로 PolicyResult를 반환할 때 사용하는 **CB 측의 변환 로직**이다.
>
> FallbackPolicy는 `CircuitBreakerFallbackResult` 타입을 전혀 알지 못하며,
> 순수하게 `PolicyResult`만 바라본다 (관심사 분리).
>
> **코드 근거**: `CircuitBreakerPolicy.execute()`(`services/circuit_breaker/policy.py` L131-L203)는
> `CircuitBreakerFallbackResult`를 사용하지 않고 `PolicyResult`를 직접 생성한다:
> - CB OPEN → `PolicyResult(outcome=REJECTED, error=CircuitBreakerOpenError(...))`
> - CB 허용 + 성공 → `PolicyResult(outcome=SUCCESS, value=result)`
>
> `should_allow_with_fallback()`는 이미 deprecated 처리 완료(`service.py` L322-L326)이며,
> 이 테이블은 deprecated 메서드의 반환값을 PolicyResult로 변환해야 하는
> **기존 소비자의 마이그레이션 참조용**으로만 유지된다.

| CB Fallback 필드 | PolicyResult 매핑 | 변환 책임 |
|-----------------|------------------|----------|
| `allowed=True` | 매핑 불필요 — CB Policy가 처리 | CircuitBreakerPolicy |
| `fallback_used=True, fallback_type="cache"` | `SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="cache"` | 기존 소비자 마이그레이션 시 |
| `fallback_used=True, fallback_type="dlq"` | `SUCCESS_WITH_FALLBACK`, `metadata["fallback_source"]="dlq"` | 기존 소비자 마이그레이션 시 |
| `allowed=False, fallback_used=False` | `REJECTED` — compose(CB, Fallback) 패턴으로 FallbackPolicy가 이어받음 | CircuitBreakerPolicy → FallbackPolicy |

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

### 5.1 Phase 1 — FallbackPolicy 생성 (네이티브 구현, 기존 코드 수정 없음)

```
resilience/policies/
├── __init__.py
├── base.py           # ResiliencePolicy (225번 문서)
├── retry.py          # RetryPolicy (226번 문서)
├── circuit_breaker.py # CircuitBreakerPolicy (227번 문서)
├── bulkhead.py       # BulkheadPolicy (228번 문서)
└── fallback.py       # FallbackPolicy ← 신규 생성
```

FallbackPolicy는 §3.1에서 정의한 네이티브 `fallback_chain` + `predicate` 기반으로 구현한다.
기존 `FallbackStrategy` 구현체 래핑이 아닌, **순수 PolicyResult 기반 신규 구현**이다.

**RetryPolicy 선례** — 기존 구현체를 재사용하지 않고 새로 작성:

`RetryPolicy`(`services/retry_handler/policy.py` L49)는 기존 `RetryHandler`를 재사용하지 않았다.
docstring에서 명확히 선언한다:

```python
# services/retry_handler/policy.py L49-L56
class RetryPolicy:
    """
    순수 재시도 Policy.
    Kill Switch, ErrorBudgetGate, Audit, DLQ 등 외부 관심사는
    PolicyComposer의 Guard/Hook/Sink가 처리한다.
    """
```

FallbackPolicy도 동일 원칙을 따른다:

```python
# resilience/policies/fallback.py
class FallbackPolicy(ResiliencePolicy[T]):
    """순수 Fallback Policy — 네이티브 fallback_chain + predicate 기반."""

    def __init__(
        self,
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
        fallback_chain: list[Callable[[], T]] | None = None,
        predicate: Callable[[PolicyResult[T]], bool] | None = None,
    ):
        self._fallback_fn = fallback_fn
        self._default_value = default_value
        self._fallback_chain = fallback_chain or []
        self._predicate = predicate or self._default_predicate
```

#### `strategy` 파라미터 — 과도기 Shim (완벽한 하위 호환 미보장)

기존 `FallbackStrategy` 구현체(`SimpleFallback`, `PartitionAwareFallback`, `CacheFirstFallback`)를
`strategy` 파라미터로 주입하여 과도기적으로 사용할 수 있다.
단, **완벽한 하위 호환을 보장하지 않는 임시 과도기(Shim) 수단**이다.

**기존 구현체의 구조적 문제 3건**:

| 구현체 | 문제 | 코드 근거 |
|--------|------|----------|
| `SimpleFallback` | `execute(primary_fn)`이 `primary_fn()`을 직접 실행 → Composer 체인에서 func 중복 실행 | `fallback_strategy.py` L69-L72 |
| `PartitionAwareFallback` | 동일 중복 실행 문제 + `_handle_failure()`가 `FallbackResult` 반환 (PolicyResult 아님) | `fallback_strategy.py` L143-L146, L149 |
| `CacheFirstFallback` | `primary_fn` 파라미터를 **무시** — ABC 계약 위반 | `fallback_strategy.py` L252-L255 |

`strategy` Shim 사용 시 FallbackPolicy는 예외를 던지는 더미 lambda를 `primary_fn`에 주입하여
fallback 경로로 유도해야 하나, 이는 우회적이고 `CacheFirstFallback`에서는 동작하지 않는다.

```python
# ⚠️ 과도기 Shim — 완벽한 호환 미보장
class FallbackPolicy(ResiliencePolicy[T]):
    def __init__(
        self,
        strategy: FallbackStrategy | None = None,  # Shim 전용
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
        fallback_chain: list[Callable[[], T]] | None = None,
        predicate: Callable[[PolicyResult[T]], bool] | None = None,
    ):
        self._strategy = strategy  # None이면 네이티브 경로 사용
        # ... (나머지 동일)
```

**권장 마이그레이션 경로**: `strategy` Shim보다 네이티브 `fallback_chain` + `predicate`를
사용하여 로직을 새로 작성하는 것을 공식 권장한다. §6.1의 Before/After 비교 참조.

### 5.2 Phase 2 — CB/Bulkhead 내장 Fallback deprecated

1. `should_allow_with_fallback()` → `DeprecationWarning` 추가
2. `@bulkhead(fallback=...)` → `DeprecationWarning` 추가
3. 사용처를 `compose(CircuitBreakerPolicy, FallbackPolicy)` 패턴으로 전환 안내

### 5.3 Phase 3 — HedgingStrategy 상속 해체

`HedgingStrategy(FallbackStrategy)` 상속을 제거하고 독립 Policy로 전환.
상세 내용은 **230번 HedgingPolicy 전환 설계**에서 다룬다.

### 5.4 Phase 4 — 기존 FallbackStrategy 구현체 유지 (삭제하지 않음)

기존 3개 구현체(`SimpleFallback`, `PartitionAwareFallback`, `CacheFirstFallback`)는
**삭제하지 않는다**. Hedging(`core/hedging/strategy.py` L50의 `HedgingStrategy(FallbackStrategy)` 상속)과
Async Hedging(`core/hedging/async_strategy.py`의 `FallbackResult`, `FallbackMode` import),
테스트(`tests/unit/chaos/test_partial_partition.py`),
패키지 export(`core/__init__.py` L65-71) 등 기존 사용처가 존재한다.

`strategy` 파라미터를 통한 FallbackPolicy 래핑은 **과도기 Shim**으로만 지원된다:

```python
# ⚠️ 과도기 Shim — 완벽한 하위 호환 미보장
# §5.1에서 설명한 구조적 문제(primary_fn 중복 실행, ABC 계약 위반)가 존재한다.
from selfhealing.core.fallback_strategy import PartitionAwareFallback

partition_fallback = FallbackPolicy(
    strategy=PartitionAwareFallback(
        partition_state=partition_state,
        cache_fallback=cache_fn,
        db_fallback=db_fn,
    ),
)
```

**권장 마이그레이션**: `strategy` Shim 대신 네이티브 `fallback_chain` + `predicate` 사용:

```python
# ✅ 권장 — 네이티브 FallbackPolicy (§3.3의 Provider 패턴 적용)
partition_fallback = FallbackPolicy(
    fallback_chain=partition_aware_chain(
        state_provider=lambda: connection_health_monitor.get_state(),
    ),
    default_value={"status": "degraded"},
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
