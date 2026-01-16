# 41. Wrapper 리팩토링 PART 3: Decorator 통합 및 기타 Wrapper

> **작성일**: 2026-01-16  
> **상태**: ✅ COMPLETED (2026-01-16)  
> **대상 파일들**:  
> - `core/tls_handler.py` (#3)
> - `services/auto_tuning/chaos_aware_metrics.py` (#4)
> - `services/retry_handler.py` (#5)
> - `services/rate_limit_coordinator.py` (#6)
> - `services/error_budget_gate/gate.py` (#7)
> - `metrics/decorators.py` (#8)
> - `metrics/jitter.py` (#9)
> - `services/metrics/updaters.py` (#10)
> - `services/auto_tuning/service.py` (#11)

---

## 1. Decorator 현황 분석

### 1.1 Decorator 목록 및 위치

| # | Decorator | 파일 | 줄 수 | 목적 |
|---|---|---|---|---|
| 5 | `with_retry` | `services/retry_handler.py` | ~50 | 함수 재시도 |
| 6 | `rate_limit_aware` | `services/rate_limit_coordinator.py` | ~40 | Rate Limit 대응 |
| 7 | `automation_gate` | `services/error_budget_gate/gate.py` | ~20 | 에러 버짓 체크 |
| 8 | `track_dlq_creation` | `metrics/decorators.py` | ~30 | DLQ 생성 추적 |
| 8 | `track_dlq_resolution` | `metrics/decorators.py` | ~30 | DLQ 해결 추적 |
| 8 | `track_replay` | `metrics/decorators.py` | ~30 | Replay 추적 |
| 8 | `track_execution_time` | `metrics/decorators.py` | ~25 | 실행 시간 추적 |
| 8 | `track_counter` | `metrics/decorators.py` | ~25 | 카운터 추적 |
| 9 | `with_jitter` | `metrics/jitter.py` | ~30 | Thundering Herd 방지 |
| 10 | `track_replay` | `services/metrics/updaters.py` | ~30 | Replay 시도 추적 |

### 1.2 코드 분석

#### #5: with_retry (`services/retry_handler.py` Lines 580-626)

```python
def with_retry(
    domain: str = "default",
    max_attempts: int | None = None,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to add retry logic to a function.
    
    Example:
        @with_retry(domain="payment", max_attempts=3)
        def call_external_api():
            return requests.post(...)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            config = RetryConfig.from_settings(domain)
            if max_attempts is not None:
                config.max_attempts = max_attempts
            if retryable_exceptions is not None:
                config.retryable_exceptions = retryable_exceptions

            handler = RetryHandler(config=config, domain=domain)
            result = handler.execute(func, *args, **kwargs)

            if result.success:
                return result.value
            else:
                raise MaxRetriesExceededError(...)
        return wrapper
    return decorator
```

**분석**: 
- ✅ 적절한 크기 (~50줄)
- ✅ 단일 책임 (재시도 로직만)
- ⚠️ 위치가 retry_handler.py에 있어 발견하기 어려움

#### #6: rate_limit_aware (`services/rate_limit_coordinator.py` Lines 280-320)

```python
def rate_limit_aware(
    self,
    key: str,
    is_429: Optional[Callable[[Any], bool]] = None,
    get_retry_after: Optional[Callable[[Any], Optional[float]]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to make a function rate-limit aware.
    
    Example:
        @coordinator.rate_limit_aware("payment_api")
        def call_payment_api():
            return requests.post(...)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            self.wait_if_needed(key)
            result = func(*args, **kwargs)
            
            _is_429 = is_429 or _default_is_429
            _get_retry_after = get_retry_after or _default_get_retry_after

            if _is_429(result):
                retry_after = _get_retry_after(result)
                self.on_rate_limited(key, retry_after)
            else:
                self.on_success(key)
            return result
        return wrapper
    return decorator
```

**분석**:
- ✅ 적절한 크기 (~40줄)
- ⚠️ 인스턴스 메서드로 정의됨 (coordinator.rate_limit_aware)
- ⚠️ 독립 사용 불가 (RateLimitCoordinator 필요)

#### #7: automation_gate (`services/error_budget_gate/gate.py` Lines 730-760)

```python
def automation_gate(action: str = ""):
    """
    자동화 게이트 데코레이터.
    
    에러 예산이 부족하면 함수 실행을 차단합니다.
    
    Usage:
        @automation_gate(action="dlq_auto_replay")
        def auto_replay_dlq():
            # 에러 예산이 충분할 때만 실행됨
            ...
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    
    return decorator
```

**분석**:
- ✅ 간결함 (~20줄)
- ⚠️ `@functools.wraps` 미사용 (수동으로 `__name__`, `__doc__` 복사)

#### #8: metrics/decorators.py (232줄)

```python
# 5개의 메트릭 추적 데코레이터

@track_dlq_creation(domain="payment")
def create_payment_dlq(): ...

@track_dlq_resolution(domain="payment")  
def resolve_payment_dlq(): ...

@track_replay(domain="payment")
def replay_payment(): ...

@track_execution_time("processing_seconds")
def process_data(): ...

@track_counter("api_calls_total")
def call_api(): ...
```

**분석**:
- ✅ 관련 데코레이터들이 한 파일에 모여 있음
- ✅ 적절한 크기 (232줄)
- ✅ 일관된 패턴 사용

#### #9: with_jitter (`metrics/jitter.py` Lines 25-70)

```python
def with_jitter(
    max_delay_seconds: float = 60.0,
    min_delay_seconds: float = 0.0,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    동기화 함수에 무작위 지연을 추가하는 데코레이터.
    Thundering Herd 방지.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            jitter = random.uniform(min_delay_seconds, max_delay_seconds)
            logger.debug(f"[Jitter] Sleeping for {jitter:.2f}s")
            time.sleep(jitter)
            return func(*args, **kwargs)

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            jitter = random.uniform(min_delay_seconds, max_delay_seconds)
            await asyncio.sleep(jitter)
            return await func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
```

**분석**:
- ✅ Sync/Async 모두 지원
- ✅ 적절한 크기 (~45줄)
- ⚠️ `metrics/` 폴더에 있지만 메트릭과 무관 (위치 문제)

#### #10: track_replay (`services/metrics/updaters.py` Lines 237-260)

```python
def track_replay(replay_type: str = "single"):
    """
    Decorator to track replay attempts.
    
    Usage:
        @track_replay("batch")
        def batch_replay(...)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            domain = kwargs.get("domain", "unknown")
            try:
                result = func(*args, **kwargs)
                success = getattr(result, "success", True) if result else False
                record_replay_attempt(domain, replay_type, success)
                return result
            except Exception:
                record_replay_attempt(domain, replay_type, success=False)
                raise
        return wrapper
    return decorator
```

**분석**:
- ⚠️ `metrics/decorators.py`의 `track_replay`와 **중복**!
- ⚠️ 같은 이름, 다른 위치 → 혼란 유발

---

## 2. 기타 Wrapper 분석

### 2.1 #3: TLSResilientClient (`core/tls_handler.py`)

```python
# Lines 253-262: Abstract Base Class
class TLSResilientClient(ABC):
    """Abstract base for TLS-resilient HTTP client wrapper"""

    @abstractmethod
    def request(self, method: str, url: str, **kwargs) -> Any:
        """Make HTTP request with TLS error handling"""
        pass

    @abstractmethod
    def on_tls_error(self, error_info: TLSErrorInfo) -> None:
        """Callback when TLS error occurs"""
        pass


# Lines 265-340: 구현체 (유일한 구현체)
class SimpleTLSResilientClient(TLSResilientClient):
    """
    Simple implementation of TLS-resilient client.
    Wraps any HTTP client and adds TLS error handling.
    """
    def __init__(
        self,
        http_client: Any,
        error_callback: Optional[Callable[[TLSErrorInfo], None]] = None,
        max_retries: int = 3,
    ):
        self._client = http_client
        self._error_callback = error_callback
        self._max_retries = max_retries
        self._classifier = TLSErrorClassifier()

    def request(self, method: str, url: str, **kwargs) -> Any:
        """Make request with TLS error handling and retry."""
        for attempt in range(self._max_retries):
            try:
                return self._client.request(method, url, **kwargs)
            except ssl.SSLError as e:
                error_info = self._classifier.classify(e, url)
                self.on_tls_error(error_info)
                if not error_info.is_retryable:
                    raise
        # ...
```

**분석**:
- ⚠️ ABC + 단일 구현체 (YAGNI 위반 가능성)
- ✅ 하지만 확장성을 위한 설계로 볼 수 있음
- **결론**: 현재 유지 (리팩토링 불필요)

### 2.2 #4: ChaosAwareMetricsAdapter (`services/auto_tuning/chaos_aware_metrics.py`)

```python
class ChaosAwareMetricsAdapter:
    """
    Chaos 실험 인식 메트릭 어댑터.
    Delegate 패턴을 사용하여 기존 메트릭 어댑터를 래핑합니다.
    """
    
    def __init__(
        self,
        delegate: MetricsAdapterProtocol,
        skip_during_chaos: bool = True,
    ):
        self._delegate = delegate
        self._skip_during_chaos = skip_during_chaos
    
    def collect_metrics(self, service: str, window_seconds: int) -> Dict[str, float]:
        if self._skip_during_chaos and self._is_chaos_experiment_running():
            return {}  # 빈 메트릭 → 조정 없음
        return self._delegate.collect_metrics(service, window_seconds)
```

**분석**:
- ✅ Delegate 패턴 잘 적용
- ✅ 적절한 크기 (140줄)
- ✅ 단일 책임
- **결론**: 현재 유지 (리팩토링 불필요)

### 2.3 #11: MetricsProviderWrapper (`services/auto_tuning/service.py` Lines 970-985)

```python
def _create_metrics_provider(self, metrics_adapter):
    """MetricsProvider 래퍼 생성"""
    class MetricsProviderWrapper:
        def __init__(self, adapter):
            self.adapter = adapter
        
        def get_error_rate(self) -> float:
            metrics = self.adapter.fetch_current_metrics()
            return metrics.get("error_rate", 0.0)
        
        def get_latency_p99(self) -> float:
            metrics = self.adapter.fetch_current_metrics()
            return metrics.get("p99_latency_ms", 0.0)
        
        def get_throughput(self) -> float:
            metrics = self.adapter.fetch_current_metrics()
            return metrics.get("throughput_rps", 0.0)
    
    return MetricsProviderWrapper(metrics_adapter)
```

**분석**:
- ⚠️ 내부 클래스로 정의됨 (테스트 어려움)
- ⚠️ 간단한 어댑터 패턴이지만 외부 클래스로 분리 권장
- **결론**: 외부 클래스로 분리 권장

---

## 3. 리팩토링 계획

### 3.1 Decorator 통합 모듈 구조 (권장)

현재 데코레이터가 6개 파일에 분산되어 있음. 통합 모듈 생성 권장:

```
core/
└── decorators/
    ├── __init__.py       # Public API (re-export)
    ├── resilience.py     # with_retry, rate_limit_aware, with_jitter
    ├── metrics.py        # track_dlq_*, track_replay, track_execution_time
    └── governance.py     # automation_gate
```

**그러나** 현재 구조도 나쁘지 않음:
- `metrics/decorators.py` - 메트릭 관련
- `metrics/jitter.py` - Thundering Herd (메트릭과 무관하지만...)
- 각 서비스 내 데코레이터 - 해당 서비스와 밀접

### 3.2 우선순위별 작업

#### 높음: track_replay 중복 제거

**문제**:
```python
# metrics/decorators.py
@track_replay(domain="payment")  # DLQ/Replay 메트릭 추적

# services/metrics/updaters.py  
@track_replay(replay_type="batch")  # Replay 시도 추적 (다른 시그니처!)
```

**해결**:
```python
# metrics/decorators.py - 통합
def track_replay(
    domain: str = "",
    replay_type: str = "auto",
) -> Callable:
    """
    Replay 함수에 메트릭 추적을 추가하는 데코레이터.
    
    Args:
        domain: 도메인 이름
        replay_type: Replay 유형 (auto, manual, batch)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _domain = domain or kwargs.get("domain", "unknown")
            ReplayEventHandler.on_replay_started(_domain, replay_type)
            
            start_time = time.monotonic()
            success = False
            try:
                result = func(*args, **kwargs)
                success = getattr(result, "success", True) if result else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(_domain, success, duration)
                record_replay_attempt(_domain, replay_type, success)
        return wrapper
    return decorator
```

#### 중간: automation_gate 개선

```python
# 현재 (functools.wraps 미사용)
def automation_gate(action: str = ""):
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

# 개선
from functools import wraps

def automation_gate(action: str = ""):
    """에러 예산 체크 데코레이터."""
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

#### 낮음: with_jitter 위치 이동

`metrics/jitter.py` → `core/jitter.py` 또는 `utils/jitter.py`

메트릭과 무관한 유틸리티이므로 `metrics/` 폴더에서 이동 권장.

#### 낮음: MetricsProviderWrapper 외부 분리

```python
# services/auto_tuning/metrics_provider.py (NEW)

"""Metrics Provider Wrapper for AutoTuning."""

from typing import Any, Dict


class MetricsProviderWrapper:
    """
    메트릭 어댑터를 MetricsProvider 인터페이스로 래핑.
    
    AutoTuning 서비스에서 사용하는 간단한 어댑터.
    """
    
    def __init__(self, adapter: Any):
        self._adapter = adapter
    
    def get_error_rate(self) -> float:
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("error_rate", 0.0)
    
    def get_latency_p99(self) -> float:
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("p99_latency_ms", 0.0)
    
    def get_throughput(self) -> float:
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("throughput_rps", 0.0)
    
    @property
    def raw_adapter(self) -> Any:
        """원본 어댑터 접근."""
        return self._adapter
```

---

## 4. 리팩토링 우선순위 요약

| 우선순위 | 작업 | 영향도 | 예상 시간 | 상태 |
|---|---|---|---|---|
| **높음** | track_replay 중복 제거 | 혼란 해소 | 2시간 | ✅ 완료 |
| 중간 | automation_gate `@wraps` 적용 | 코드 품질 | 30분 | ✅ 완료 |
| 낮음 | with_jitter 위치 이동 | 구조 개선 | 1시간 | ✅ 완료 |
| 낮음 | MetricsProviderWrapper 분리 | 테스트 용이성 | 1시간 | ✅ 완료 |
| 보류 | Decorator 통합 모듈 | 대규모 변경 | 4시간+ | ⏸️ 향후 검토 |
| 유지 | TLSResilientClient | 확장성 | - | ➡️ 유지 |
| 유지 | ChaosAwareMetricsAdapter | 잘 설계됨 | - | ➡️ 유지 |

---

## 5. 상세 작업 계획

### 5.1 track_replay 중복 제거

**Step 1**: 두 데코레이터 시그니처 통합

```python
# metrics/decorators.py
def track_replay(
    domain: str = "",
    replay_type: str = "auto",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    # 통합 구현
```

**Step 2**: `services/metrics/updaters.py`에서 제거

```python
# DEPRECATED 주석 추가 후 삭제
# def track_replay(...):  # Moved to metrics/decorators.py
```

**Step 3**: 사용처 업데이트

```python
# Before
from selfhealing.services.metrics.updaters import track_replay

# After
from selfhealing.metrics.decorators import track_replay
```

### 5.2 automation_gate 개선

```python
# services/error_budget_gate/gate.py

from functools import wraps
from typing import Callable, TypeVar, ParamSpec

P = ParamSpec("P")
R = TypeVar("R")


def automation_gate(action: str = "") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    자동화 게이트 데코레이터.
    
    에러 예산이 부족하면 함수 실행을 차단합니다.
    
    Args:
        action: 액션 이름 (기본: 함수 이름)
    
    Raises:
        AutomationBlockedError: 에러 예산 부족 시
    
    Usage:
        @automation_gate(action="dlq_auto_replay")
        def auto_replay_dlq():
            ...
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

---

## 6. 검증 체크리스트

### 6.1 track_replay 통합

- [x] 두 데코레이터 시그니처 통합 ✅
- [x] updaters.py에서 중복 제거 (deprecated 처리) ✅
- [x] 모든 사용처 import 경로 업데이트 ✅
- [x] 테스트 통과 ✅

### 6.2 automation_gate 개선

- [x] `@wraps` 적용 ✅
- [x] functools import 추가 ✅
- [x] 기존 동작 유지 확인 ✅

### 6.3 with_jitter 이동

- [x] `utils/jitter.py`로 이동 ✅
- [x] 기존 import 경로 하위 호환성 유지 ✅
- [x] DeprecationWarning 추가 ✅
- [x] utils/__init__.py에 export 추가 ✅
- [x] metrics/__init__.py 내부 import 경로 수정 ✅
- [x] metrics/reconciler.py import 경로 수정 ✅

### 6.4 MetricsProviderWrapper 분리

- [x] 별도 파일 `services/auto_tuning/metrics_provider.py` 생성 ✅
- [x] service.py에서 import 변경 ✅
- [x] Protocol 추가 (MetricsAdapterProtocol) ✅

---

## 7. 변경하지 않는 항목

| Wrapper | 이유 |
|---|---|
| `TLSResilientClient` | ABC 패턴이지만 확장성을 위한 설계. 현재 단일 구현체지만 향후 확장 가능 |
| `ChaosAwareMetricsAdapter` | Delegate 패턴 잘 적용됨. 140줄로 적절한 크기 |
| `with_retry` | retry_handler.py에 적절히 위치. 50줄로 적절한 크기 |
| `rate_limit_aware` | coordinator의 인스턴스 메서드로 적절함 |
| `metrics/decorators.py` | 메트릭 관련 데코레이터 잘 모여 있음 |

---

## 8. 예상 효과

| 지표 | Before | After | 상태 |
|---|---|---|---|
| track_replay 정의 수 | 2개 (중복) | 1개 (통합) | ✅ |
| automation_gate 타입 안전성 | 낮음 | 높음 | ✅ |
| with_jitter 위치 논리성 | 낮음 (metrics/) | 높음 (utils/) | ✅ |
| MetricsProviderWrapper 테스트 용이성 | 낮음 | 높음 | ✅ |

---

## 9. 구현 완료 요약

### 9.1 track_replay 통합 (2026-01-16)

**변경된 파일**:
- `metrics/decorators.py`: `track_replay` 시그니처 확장 (domain + replay_type)
- `services/metrics/updaters.py`: deprecated 처리, re-export from decorators

**새 시그니처**:
```python
def track_replay(
    domain: str = "",
    replay_type: str = "auto",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
```

### 9.2 automation_gate 개선 (2026-01-16)

**변경된 파일**:
- `services/error_budget_gate/gate.py`: @functools.wraps 적용

**Before**:
```python
wrapper.__name__ = func.__name__
wrapper.__doc__ = func.__doc__
```

**After**:
```python
@functools.wraps(func)
def wrapper(*args, **kwargs):
```

### 9.3 with_jitter 위치 이동 (2026-01-16)

**새 파일**:
- `utils/jitter.py`: 전체 구현 이동

**변경된 파일**:
- `metrics/jitter.py`: deprecated, re-export from utils
- `metrics/__init__.py`: import from utils
- `metrics/reconciler.py`: import from utils
- `utils/__init__.py`: export 추가

### 9.4 MetricsProviderWrapper 분리 (2026-01-16)

**새 파일**:
- `services/auto_tuning/metrics_provider.py`: 외부 클래스 + Protocol

**변경된 파일**:
- `services/auto_tuning/service.py`: import 추가, 내부 클래스 제거
