# 230. HedgingPolicy 전환 설계

## 1. 개요

HedgingPolicy는 전환 대상 중 **가장 복잡한 아키텍처 변경**을 요구하는 패턴이다.
현재 `HedgingStrategy`는 `FallbackStrategy`를 **상속**하며,
Bulkhead/Backpressure/EventBus를 **내부에 하드코딩**한다.

전환의 핵심은 3가지이다:
1. **FallbackStrategy 상속 해체** — 독립 Policy로 분리
2. **Bulkhead 하드코딩 제거** — `per_candidate_policy`/`overall_policy` 주입으로 대체
3. **EventBus 구독 분리** — Hook 인터페이스로 외부화

## 2. 현재 구현 분석

### 2.1 파일 구조

```
core/hedging/
├── strategy.py          # HedgingStrategy (389줄) — FallbackStrategy 상속 + Bulkhead/EventBus 내장
├── async_strategy.py    # AsyncHedgingStrategy (371줄) — 비동기 버전 (FallbackStrategy 미상속)
├── config.py            # HedgingConfig (161줄) — Bulkhead/Backpressure 설정 하드코딩
├── decorator.py         # @hedged, @hedged_sync, @hedged_async (302줄)
├── executor.py          # HedgingExecutor (279줄) — 동기, ThreadPool 기반, 크로스 의존 0건
├── async_executor.py    # AsyncHedgingExecutor (297줄) — 비동기, asyncio 기반, 크로스 의존 0건
├── result.py            # HedgingResult[T]
├── exceptions.py        # HedgingError 계층
├── latency_tracker.py   # ADAPTIVE 모드용 P50 추적
├── result_validator.py  # 결과 일관성 검증
├── otel.py              # OpenTelemetry 연동
├── metrics.py           # Prometheus 메트릭
└── __init__.py          # 34개 심볼 re-export
```

### 2.2 HedgingStrategy 크로스-패턴 의존성 (3건)

| 의존 대상 | 위치 | 방식 | 용도 |
|-----------|------|------|------|
| `FallbackStrategy` (상속) | `strategy.py` L13-16, L50 | 정적 import + 클래스 상속 | `execute()` 시그니처 공유, `FallbackResult` 반환 |
| `BulkheadRegistry` | `strategy.py` L97 | lazy import (`get_bulkhead_registry`) | 헷징 요청에 대한 리소스 격리 |
| `EventBus` | `strategy.py` L108 | lazy import (`get_event_bus`) | CONFIG_UPDATED 구독으로 동적 설정 변경 |

### 2.3 AsyncHedgingStrategy 크로스-패턴 의존성 (3건)

| 의존 대상 | 위치 | 방식 | 용도 |
|-----------|------|------|------|
| `FallbackResult`, `FallbackMode` | `async_strategy.py` L13-16 | 정적 import | 결과 타입으로 사용 (상속은 안 함) |
| `BulkheadRegistry` | `async_strategy.py` L102 | lazy import | 비동기 Bulkhead 획득 |
| `EventBus` | `async_strategy.py` L126 | lazy import | CONFIG_UPDATED 구독 |

**중요**: `AsyncHedgingStrategy`는 `FallbackStrategy`를 **상속하지 않는다**.
`FallbackResult`와 `FallbackMode`를 **결과 타입으로만** import한다.

### 2.4 Executor 의존성 (0건)

`HedgingExecutor` (executor.py, 279줄)와 `AsyncHedgingExecutor` (async_executor.py, 297줄)는
**크로스-패턴 의존성이 0건**이다. hedging 패키지 내부 모듈만 참조한다:

- `config` → `HedgingCandidate`, `HedgingConfig`, `HedgingMode`
- `exceptions` → `HedgingAllFailedError`, `HedgingTimeoutError`, `NonRetryableHedgingError`
- `latency_tracker` → `HedgingLatencyTracker`
- `result` → `HedgingResult`

Executor는 **변경 없이 재사용** 가능하다.

### 2.5 FallbackStrategy 상속의 문제점

`strategy.py` L50:

```python
class HedgingStrategy(FallbackStrategy):
```

상속으로 인한 제약:

1. **시그니처 고정**: `execute(primary_fn, fallback_fn, default_value) → FallbackResult`
   - Policy Composition의 `execute(func, *args, **kwargs) → PolicyResult`와 비호환

2. **결과 타입 고정**: `FallbackResult`를 반환해야 함
   - `PolicyResult`로의 통합 불가

3. **의미적 혼란**: Hedging은 "병렬 경쟁"이지 "실패 시 대체"가 아님
   - `used_fallback=True`가 "다른 후보가 이김"을 의미하게 되어 semantics가 왜곡
   - `FallbackMode.HEDGE`를 추가하여 무리하게 맞춘 흔적 (L33)

4. **조합 불가**: HedgingStrategy를 FallbackPolicy와 독립적으로 조합할 수 없음
   - 둘 다 Fallback 계열이므로 Composer에서 중복으로 인식

### 2.6 Bulkhead 하드코딩 분석

`strategy.py`의 Bulkhead 연동 (L92-L230):

```python
# 생성자 (L92-L100)
self._bulkhead_registry = None
if self._config.bulkhead_name:
    try:
        from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry
        self._bulkhead_registry = get_bulkhead_registry()
    except ImportError:
        logger.debug("[HedgingStrategy] Bulkhead registry not available")

# execute() 내부 (L228-L230)
if not self._config.acquire_bulkhead_per_candidate:
    if not self._acquire_bulkhead():
        return self._execute_single(primary_fn, default_value)
```

`config.py`의 Bulkhead 설정 (L66-L75):

```python
bulkhead_name: str | None = None
"""헷징 요청이 사용할 격벽 이름. None이면 격벽 미사용."""

acquire_bulkhead_per_candidate: bool = False
"""
True: 각 후보마다 격벽 획득 (도메인별 격리 강화).
False: 전체 헷징에 대해 1회만 격벽 획득 (기본값, 리소스 효율).
"""
```

**사용자가 이전 논의에서 지적한 핵심**:
Policy Composition에서는 Bulkhead 제어를 HedgingConfig 내부가 아니라
`per_candidate_policy`/`overall_policy` 주입으로 표현해야 한다.

### 2.7 EventBus 구독 분석

`strategy.py` L105-L121 `_subscribe_config_updates()`:

```python
def _subscribe_config_updates(self) -> None:
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus
        bus = get_event_bus()
        bus.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
    except ImportError:
        logger.debug("[HedgingStrategy] EventBus not available")
```

이벤트 핸들러 `_on_config_updated()` (L130-L163):
- `hedging.mode` → `HedgingMode` 변경
- `hedging.delay` → delay 값 변경
- `backpressure.level` → 부하 레벨 변경

**판단**: EventBus 구독은 동적 설정 변경의 **전달 채널**이지 Hedging 핵심 로직이 아니다.
PolicyHook으로 분리하여 Hedging이 EventBus 없이도 동작하게 해야 한다.

### 2.8 Backpressure 연동 분석

`strategy.py`의 Backpressure 로직 (L168-L189):

```python
def _should_disable_hedging(self) -> bool:
    current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
    disable_order = _LOAD_LEVEL_ORDER.get(self._config.disable_on_load_level, 3)
    return current_order >= disable_order

def _get_effective_delay(self) -> float:
    if self._current_load_level == "medium":
        return self._config.delay * self._config.delay_multiplier_on_medium
    elif self._current_load_level == "high":
        return self._config.delay * self._config.delay_multiplier_on_high
    return self._config.delay
```

**판단**: Backpressure는 HedgingPolicy 내부에 유지할 수 있다.
부하 레벨에 따른 delay 조정과 비활성화는 Hedging **고유** 로직이다.
(외부 패턴이 아니라 Hedging Config의 일부)

다만, `_current_load_level`의 **갱신 방법**은 EventBus Hook으로 분리한다.

### 2.9 Decorator 분석

`decorator.py`의 `@hedged` (L33-L96):

```python
@hedged(fetch_from_region_b, fetch_from_region_c)
def fetch_from_region_a():
    return api_call_a()
```

내부에서 `HedgingStrategy` 또는 `AsyncHedgingStrategy`를 직접 생성한다.
Policy 전환 후에는 `HedgingPolicy`를 생성하도록 변경해야 한다.

## 3. 전환 설계

### 3.1 HedgingPolicy 클래스

```python
class HedgingPolicy(ResiliencePolicy[T]):
    """
    Hedging Policy — 병렬 경쟁 실행.

    FallbackStrategy 상속을 해체하고 독립 Policy로 전환한다.
    내부적으로 기존 HedgingExecutor/AsyncHedgingExecutor를 재사용한다.

    Bulkhead 제어는 per_candidate_policy/overall_policy로 외부 주입.
    EventBus 구독은 PolicyHook으로 분리.
    """

    def __init__(
        self,
        candidates: list[Callable[[], T]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
        # === Policy Composition 신규 파라미터 ===
        per_candidate_policy: ResiliencePolicy[T] | None = None,
        overall_policy: ResiliencePolicy[T] | None = None,
    ):
        """
        Args:
            candidates: 후보 함수 목록 (첫 번째가 Primary)
            candidate_names: 후보 이름 목록 (선택)
            config: 헷징 설정
            default_value: 모든 후보 실패 시 기본값
            per_candidate_policy: 각 후보에 적용할 Policy (Bulkhead, Timeout 등)
            overall_policy: 전체 헷징에 적용할 Policy (Bulkhead, Timeout 등)
        """
        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._per_candidate_policy = per_candidate_policy
        self._overall_policy = overall_policy

        # Executor 재사용 (크로스 의존 0건 — 변경 불필요)
        self._executor = HedgingExecutor(self._config)

        # Backpressure 상태 (Hedging 고유 로직 — 내부 유지)
        self._current_load_level: str = "none"

    def execute(self, func: Callable[..., T], *args, **kwargs) -> PolicyResult[T]:
        """
        헷징 실행 — Policy Composition 인터페이스.

        실행 순서:
        1. Backpressure 체크 → 비활성화 시 single 실행
        2. overall_policy가 있으면 전체를 래핑
        3. 후보 목록 구성 (func이 Primary)
        4. per_candidate_policy가 있으면 각 후보를 래핑
        5. Executor로 병렬 실행
        6. PolicyResult로 변환
        """
        # Step 1: Backpressure 체크
        if self._should_disable_hedging():
            return self._execute_single(func, *args, **kwargs)

        # Step 2: overall_policy 적용
        if self._overall_policy is not None:
            # 전체 헷징을 하나의 함수로 감싸서 overall_policy로 래핑
            def hedging_as_single():
                return self._execute_hedging(func, *args, **kwargs)

            result = self._overall_policy.execute(hedging_as_single)
            return result

        # Step 3: 직접 헷징 실행
        return self._execute_hedging(func, *args, **kwargs)

    def _execute_hedging(self, func, *args, **kwargs) -> PolicyResult[T]:
        """실제 헷징 실행 로직."""
        # 후보 목록 구성
        candidates = self._build_candidates(func, *args, **kwargs)

        if len(candidates) == 1:
            return self._execute_single(func, *args, **kwargs)

        # per_candidate_policy 적용
        if self._per_candidate_policy is not None:
            candidates = self._wrap_candidates_with_policy(candidates)

        try:
            result = self._executor.execute(candidates)

            return PolicyResult(
                value=result.value,
                outcome=PolicyOutcome.SUCCESS,
                metadata={
                    "hedged": result.hedged,
                    "winner": result.source,
                    "latency_ms": result.latency_ms,
                    "hedging_benefit_ms": result.hedging_benefit_ms,
                },
            )
        except HedgingError as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    metadata={"hedging_all_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
            )

    def _wrap_candidates_with_policy(
        self, candidates: list[HedgingCandidate]
    ) -> list[HedgingCandidate]:
        """각 후보를 per_candidate_policy로 래핑."""
        wrapped = []
        for c in candidates:
            original_fn = c.fn
            policy = self._per_candidate_policy

            def policy_wrapped(fn=original_fn, p=policy):
                result = p.execute(fn)
                if result.outcome == PolicyOutcome.SUCCESS:
                    return result.value
                raise RuntimeError(f"Candidate policy rejected: {result.outcome}")

            wrapped.append(HedgingCandidate(
                name=c.name,
                fn=policy_wrapped,
                priority=c.priority,
                metadata=c.metadata,
            ))
        return wrapped
```

### 3.2 FallbackStrategy 상속 해체

#### AS-IS: 상속 관계

```
FallbackStrategy (ABC)
├── SimpleFallback
├── PartitionAwareFallback
├── CacheFirstFallback
└── HedgingStrategy ← 의미적으로 부적절한 상속
```

#### TO-BE: 독립 Policy 구조

```
ResiliencePolicy (Protocol)
├── RetryPolicy
├── CircuitBreakerPolicy
├── BulkheadPolicy
├── FallbackPolicy        ← FallbackStrategy 구현체 래핑
├── HedgingPolicy         ← 독립! FallbackStrategy 미상속
└── TimeoutPolicy
```

#### 코드 변경

`strategy.py`에서:

```python
# AS-IS (L13-L16, L50)
from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    FallbackStrategy,
)

class HedgingStrategy(FallbackStrategy):
    def execute(self, primary_fn, fallback_fn=None, default_value=None) -> FallbackResult:
        ...

# TO-BE
from selfhealing.resilience.policies.base import ResiliencePolicy, PolicyResult, PolicyOutcome

class HedgingPolicy(ResiliencePolicy[T]):
    def execute(self, func: Callable[..., T], *args, **kwargs) -> PolicyResult[T]:
        ...
```

#### 하위 호환 — FallbackResult 변환 어댑터

기존 코드가 `HedgingStrategy.execute() → FallbackResult`를 기대하므로,
과도기에 어댑터를 제공한다:

```python
class HedgingStrategyCompat(HedgingStrategy):
    """
    기존 HedgingStrategy와 호환되는 래퍼.

    .. deprecated:: 2.0
        Use HedgingPolicy instead.
    """

    def __init__(self, policy: HedgingPolicy):
        self._policy = policy

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        """기존 FallbackStrategy.execute() 시그니처 호환."""
        warnings.warn(
            "HedgingStrategy is deprecated. Use HedgingPolicy instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        result = self._policy.execute(primary_fn)
        return self._to_fallback_result(result)

    @staticmethod
    def _to_fallback_result(result: PolicyResult[T]) -> FallbackResult[T]:
        """PolicyResult → FallbackResult 변환."""
        if result.outcome == PolicyOutcome.SUCCESS:
            hedged = result.metadata.get("hedged", False)
            return FallbackResult(
                value=result.value,
                used_fallback=hedged,
                fallback_mode=FallbackMode.HEDGE if hedged else None,
            )
        elif result.outcome == PolicyOutcome.SUCCESS_WITH_FALLBACK:
            return FallbackResult(
                value=result.value,
                used_fallback=True,
                fallback_mode=FallbackMode.USE_DEFAULT,
                original_error=str(result.error) if result.error else None,
            )
        else:
            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(result.error) if result.error else None,
            )
```

### 3.3 Bulkhead 외부 주입 설계

#### AS-IS: HedgingConfig에 Bulkhead 하드코딩

```python
# config.py L66-L75
bulkhead_name: str | None = None
acquire_bulkhead_per_candidate: bool = False

# strategy.py L92-L100 — 생성자에서 BulkheadRegistry lazy import
self._bulkhead_registry = None
if self._config.bulkhead_name:
    from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry
    self._bulkhead_registry = get_bulkhead_registry()

# strategy.py L228-L230 — execute()에서 직접 acquire/release
if not self._acquire_bulkhead():
    return self._execute_single(primary_fn, default_value)
```

#### TO-BE: per_candidate_policy / overall_policy 주입

```python
# 엔터프라이즈 사용자가 Bulkhead 제어를 Policy 조합으로 표현

# 패턴 1: 전체 헷징에 Bulkhead 적용
hedging = HedgingPolicy(
    candidates=[fetch_region_a, fetch_region_b, fetch_region_c],
    config=HedgingConfig(mode=HedgingMode.DELAYED),
    overall_policy=BulkheadPolicy(
        bulkhead=SemaphoreBulkhead("api_bulkhead", max_concurrent=10),
        timeout=1.0,
    ),
)

# 패턴 2: 각 후보에 Bulkhead 적용
hedging = HedgingPolicy(
    candidates=[fetch_region_a, fetch_region_b, fetch_region_c],
    config=HedgingConfig(mode=HedgingMode.DELAYED),
    per_candidate_policy=BulkheadPolicy(
        bulkhead=SemaphoreBulkhead("candidate_bulkhead", max_concurrent=5),
    ),
)

# 패턴 3: 후보마다 다른 Policy (compose 사용)
hedging = HedgingPolicy(
    candidates=[fetch_region_a, fetch_region_b, fetch_region_c],
    config=HedgingConfig(mode=HedgingMode.DELAYED),
    per_candidate_policy=compose(
        TimeoutPolicy(timeout_ms=500),
        BulkheadPolicy(bulkhead=SemaphoreBulkhead("per_cand", max_concurrent=3)),
    ),
)
```

**이 설계가 HedgingConfig의 `bulkhead_name`/`acquire_bulkhead_per_candidate`를 대체한다.**

| HedgingConfig 설정 | Policy Composition 대체 |
|-------------------|----------------------|
| `bulkhead_name` + `acquire_bulkhead_per_candidate=False` | `overall_policy=BulkheadPolicy(...)` |
| `bulkhead_name` + `acquire_bulkhead_per_candidate=True` | `per_candidate_policy=BulkheadPolicy(...)` |
| `bulkhead_name=None` | 두 파라미터 모두 생략 |

### 3.4 EventBus 구독 → PolicyHook 분리

#### AS-IS: EventBus 직접 구독

```python
# strategy.py L105-L121
def _subscribe_config_updates(self) -> None:
    from selfhealing.services.event_bus import EventType, get_event_bus
    bus = get_event_bus()
    bus.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
```

#### TO-BE: PolicyHook 인터페이스

```python
class ConfigUpdateHook(PolicyHook):
    """EventBus CONFIG_UPDATED 이벤트를 Policy에 전달하는 Hook."""

    def __init__(self):
        self._policies: list[HedgingPolicy] = []

    def register(self, policy: HedgingPolicy) -> None:
        self._policies.append(policy)

    def start(self) -> None:
        """EventBus 구독 시작."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus
            bus = get_event_bus()
            bus.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except ImportError:
            pass

    def _on_config_updated(self, event) -> None:
        for policy in self._policies:
            policy.update_config(event)


# HedgingPolicy에 update_config 메서드 추가
class HedgingPolicy(ResiliencePolicy[T]):
    def update_config(self, event: dict) -> None:
        """외부에서 설정을 갱신하는 메서드."""
        config_key = event.get("key", "")
        config_value = event.get("value")

        if config_key == "hedging.mode" and config_value:
            self._config.mode = HedgingMode(config_value)
        elif config_key == "hedging.delay" and config_value is not None:
            self._config.delay = float(config_value)
        elif config_key == "backpressure.level" and config_value:
            self._current_load_level = config_value.lower()
```

**효과**: HedgingPolicy가 EventBus import 없이 동작. Hook이 연결을 중개.

### 3.5 Async 지원

`AsyncHedgingStrategy`는 이미 `FallbackStrategy`를 **상속하지 않으므로** 변경이 상대적으로 용이하다.
`FallbackResult`/`FallbackMode` import를 `PolicyResult`/`PolicyOutcome`으로 교체하면 된다.

```python
class AsyncHedgingPolicy(ResiliencePolicy[T]):
    """비동기 Hedging Policy."""

    def __init__(
        self,
        candidates: list[Callable[[], Awaitable[T]]] | None = None,
        config: HedgingConfig | None = None,
        per_candidate_policy: ResiliencePolicy[T] | None = None,
        overall_policy: ResiliencePolicy[T] | None = None,
    ):
        self._executor = AsyncHedgingExecutor(self._config)
        ...

    async def execute_async(
        self, func: Callable[..., Awaitable[T]], *args, **kwargs
    ) -> PolicyResult[T]:
        """비동기 헷징 실행."""
        ...
```

`execute_async()` 내부에서 기존 `AsyncHedgingExecutor`를 **그대로 재사용**한다.

### 3.6 Decorator 전환

`decorator.py`의 3개 데코레이터를 `HedgingPolicy` 기반으로 전환:

```python
# AS-IS: @hedged 내부에서 HedgingStrategy 직접 생성 (decorator.py L84-L96)
strategy = HedgingStrategy(
    candidates=wrapped_candidates,
    config=config,
    default_value=default,
)
result = strategy.execute(primary_fn=primary)

# TO-BE: HedgingPolicy 사용
policy = HedgingPolicy(
    candidates=wrapped_candidates,
    config=config,
    default_value=default,
    per_candidate_policy=BulkheadPolicy(...) if bulkhead_name else None,
)
result = policy.execute(primary)
```

## 4. 컴포넌트별 변경 요약

### 4.1 변경 대상

| 파일 | 변경 내용 | 영향도 |
|------|----------|-------|
| `strategy.py` | `FallbackStrategy` 상속 해체, Bulkhead/EventBus 제거 | 🔴 High |
| `async_strategy.py` | `FallbackResult`/`FallbackMode` → `PolicyResult`/`PolicyOutcome` | 🟡 Medium |
| `config.py` | `bulkhead_name`, `acquire_bulkhead_per_candidate` deprecated | 🟡 Medium |
| `decorator.py` | `HedgingStrategy` → `HedgingPolicy` 생성 변경 | 🟡 Medium |
| `__init__.py` | `HedgingPolicy`, `AsyncHedgingPolicy` export 추가 | 🟢 Low |

### 4.2 변경 불필요 (그대로 재사용)

| 파일 | 이유 |
|------|------|
| `executor.py` (279줄) | 크로스 의존 0건. hedging 내부만 참조 |
| `async_executor.py` (297줄) | 크로스 의존 0건. hedging 내부만 참조 |
| `latency_tracker.py` | ADAPTIVE 모드 전용 P50 추적기 — 독립 |
| `result.py` | `HedgingResult` — Executor 결과 타입 |
| `exceptions.py` | 전용 예외 계층 — 독립 |
| `result_validator.py` | 결과 일관성 검증 — 독립 |
| `otel.py` | OpenTelemetry 연동 — 독립 |
| `metrics.py` | Prometheus 메트릭 — 독립 |

**12개 파일 중 5개만 변경, 7개는 재사용.**

## 5. Before/After 비교

### 5.1 기본 사용

```python
# === BEFORE (현재) ===
from selfhealing.core.hedging import HedgingStrategy, HedgingConfig, HedgingMode

strategy = HedgingStrategy(
    candidates=[fetch_region_b, fetch_region_c],
    config=HedgingConfig(
        mode=HedgingMode.DELAYED,
        delay=0.1,
        bulkhead_name="api_bulkhead",          # ← Bulkhead 하드코딩
        disable_on_load_level="high",
    ),
)
result = strategy.execute(primary_fn=fetch_region_a)
# result: FallbackResult(value=..., used_fallback=True, fallback_mode=HEDGE)

# === AFTER (Policy Composition) ===
from selfhealing.resilience.policies import HedgingPolicy, BulkheadPolicy, compose

result = compose(
    HedgingPolicy(
        candidates=[fetch_region_b, fetch_region_c],
        config=HedgingConfig(mode=HedgingMode.DELAYED, delay=0.1),
        overall_policy=BulkheadPolicy(bulkhead=get_bulkhead("api_bulkhead")),
    ),
).execute(fetch_region_a)
# result: PolicyResult(value=..., outcome=SUCCESS, metadata={"hedged": True, "winner": "candidate_1"})
```

### 5.2 Retry + Hedging + Fallback 조합

```python
# === BEFORE (현재) ===
# 불가능! RetryHandler와 HedgingStrategy를 조합하는 표준 방법이 없음.
# 소비자가 직접 중첩 호출을 작성해야 함:
handler = RetryHandler(config=RetryConfig(max_retries=2))
strategy = HedgingStrategy(candidates=[...], config=HedgingConfig(...))

result = handler.execute(lambda: strategy.execute(primary_fn=fetch_api).value)
# ↑ FallbackResult.value를 꺼내서 RetryHandler에 전달하는 수동 연결

# === AFTER (Policy Composition) ===
result = compose(
    RetryPolicy(max_retries=2),
    HedgingPolicy(
        candidates=[fetch_region_b, fetch_region_c],
        config=HedgingConfig(mode=HedgingMode.DELAYED),
    ),
    FallbackPolicy(default_value={"status": "degraded"}),
).execute(fetch_api)
# Retry 2회 실패마다 Hedging으로 병렬 시도 → 최종 실패 시 Fallback
```

### 5.3 per_candidate_policy — Bulkhead + Timeout 주입

```python
# === BEFORE (현재) ===
# config.py의 bulkhead_name으로만 제어 가능.
# 후보별 Timeout은 HedgingConfig.timeout으로 전체 일괄 적용.
# 후보별 세밀한 제어 불가.

strategy = HedgingStrategy(
    candidates=[fast_api, slow_api],
    config=HedgingConfig(
        timeout=5.0,                    # 전체 동일
        bulkhead_name="api_bulkhead",   # 전체 동일
    ),
)

# === AFTER (Policy Composition) ===
# 후보별 Policy 주입으로 세밀한 제어 가능
hedging = HedgingPolicy(
    candidates=[fast_api, slow_api],
    config=HedgingConfig(mode=HedgingMode.DELAYED),
    per_candidate_policy=compose(
        TimeoutPolicy(timeout_ms=500),          # 후보별 500ms
        BulkheadPolicy(bulkhead=get_bulkhead("per_cand")),  # 후보별 격벽
    ),
    overall_policy=BulkheadPolicy(
        bulkhead=get_bulkhead("overall"),        # 전체 격벽 (별도)
    ),
)
```

### 5.4 비동기 @hedged 데코레이터

```python
# === BEFORE (현재) ===
@hedged(async_fetch_b, async_fetch_c, bulkhead_name="api_bulkhead")
async def async_fetch_a():
    return await async_api_call_a()

# === AFTER (Policy Composition) ===
@hedged(async_fetch_b, async_fetch_c)  # bulkhead_name 제거
async def async_fetch_a():
    return await async_api_call_a()

# Bulkhead는 외부에서 조합:
result = compose(
    BulkheadPolicy(bulkhead=get_bulkhead("api_bulkhead")),
    hedging_policy_from_decorator,  # @hedged가 내부적으로 생성한 Policy
).execute(async_fetch_a)
```

## 6. 마이그레이션 전략

### 6.1 Phase 1 — HedgingPolicy 생성 (기존 코드 병행)

```
resilience/policies/
├── ...
└── hedging.py       # HedgingPolicy, AsyncHedgingPolicy ← 신규 생성
```

기존 `core/hedging/strategy.py`의 `HedgingStrategy`는 수정하지 않는다.
`HedgingPolicy`가 내부적으로 `HedgingExecutor`를 직접 사용하여
`strategy.py`에 의존하지 않는 독립 경로를 구축한다.

### 6.2 Phase 2 — HedgingStrategy deprecated

```python
# strategy.py — 과도기
class HedgingStrategy(FallbackStrategy):
    """
    .. deprecated:: 2.0
        Use HedgingPolicy instead.
        HedgingPolicy supports per_candidate_policy and overall_policy
        for Bulkhead/Timeout composition.
    """
    def __init__(self, ...):
        warnings.warn(
            "HedgingStrategy is deprecated. Use HedgingPolicy instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        ...
```

### 6.3 Phase 3 — HedgingConfig 정리

`bulkhead_name`, `acquire_bulkhead_per_candidate`를 deprecated 처리:

```python
# config.py — 과도기
@dataclass
class HedgingConfig:
    bulkhead_name: str | None = field(
        default=None,
        metadata={"deprecated": "Use per_candidate_policy or overall_policy instead"},
    )
    acquire_bulkhead_per_candidate: bool = field(
        default=False,
        metadata={"deprecated": "Use per_candidate_policy instead"},
    )
```

### 6.4 Phase 4 — FallbackStrategy 상속 완전 제거

HedgingStrategy에서 `FallbackStrategy` 상속을 제거하고,
`HedgingStrategyCompat` 어댑터만 존재하게 한다.
이 시점에서 `FallbackResult` import도 제거된다.

## 7. 참조

- **225번 문서**: `ResiliencePolicy`, `PolicyResult`, `PolicyOutcome` 인터페이스 정의
- **228번 문서**: `BulkheadPolicy` — `per_candidate_policy`/`overall_policy`로 주입되는 Policy
- **229번 문서**: `FallbackPolicy` — Hedging과 독립적으로 조합 가능
- **231번 문서**: `PolicyComposer`의 HedgingPolicy 실행 순서 처리 (예정)
