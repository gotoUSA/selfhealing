# 41. Wrapper 리팩토링 PART 4: 설계 보완 및 구현

> **작성일**: 2026-01-17  
> **상태**: ✅ 구현 완료 (1~5)  
> **관련 문서**: [PART1](41_WRAPPER_REFACTORING_PART1.md), [PART2](41_WRAPPER_REFACTORING_PART2.md), [PART3](41_WRAPPER_REFACTORING_PART3.md)

---

## 1. 개요

PART 1~3 리팩토링 완료 후 아키텍트 리뷰에서 도출된 추가 보완 사항들의 상세 구현 계획입니다.

### 1.1 보완 대상

| # | 항목 | 우선순위 | 상태 |
|---|---|---|---|
| 1 | SafeGauge LRU 캐시 (메모리 관리) | **높음** | ✅ 구현 완료 |
| 2 | Decorator Universal Async Support | **높음** | ✅ 구현 완료 |
| 3 | Task Layer exc_info 일관성 | 중간 | ✅ 구현 완료 |
| 4 | ProviderRegistry override_provider | 중간 | ✅ 구현 완료 |
| 5 | automation_gate Audit 포렌식 필드 | 중간 | ✅ 구현 완료 |
| 6 | SyncInfo 관리 방안 검증 | 낮음 | ✅ 설계 완료 |
| 7 | CleanupService 상태 비저장 검증 | 낮음 | ✅ 설계 완료 |

---

## 2. SafeGauge: LRU 캐시 기반 메모리 관리

### 2.1 문제점 분석

**현재 코드** (`metrics/safe_gauge/core.py` Lines 279-285):
```python
class SafeGauge:
    def __init__(self, gauge: Optional["Gauge"]):
        self._gauge = gauge
        self._children: Dict[tuple, SafeGaugeChild] = {}  # ⚠️ 무한 증가 가능
        self._lock = threading.Lock()
```

**위험 시나리오**:
- 레이블에 사용자 ID, 주문 번호 등 가변성 높은 값 사용 시
- `_children` 딕셔너리가 무한정 증가 → OOM(Out of Memory)

### 2.2 해결 방안 비교

#### 방안 1: WeakRef 기반 캐시

```python
import weakref

class SafeGauge:
    def __init__(self, gauge):
        self._children: Dict[tuple, weakref.ref] = {}
```

| 장점 | 단점 |
|---|---|
| 메모리 자동 정리 (GC 의존) | **Shadow 값 손실** (참조 끊기면 즉시 삭제) |
| 명시적 cleanup 불필요 | Prometheus Gauge child는 여전히 메모리에 남음 |
| 간단한 구현 | 예측 불가능한 정리 시점 |

**⚠️ 치명적 문제**: SafeGauge의 핵심 가치인 `_shadow_value` 보존 불가. 
참조가 끊기면 shadow 값이 사라져 음수 방지 로직이 무력화됨.

#### 방안 2: LRU 캐시 (OrderedDict 기반) ✅ 권장

```python
from collections import OrderedDict

class SafeGauge:
    def __init__(self, gauge, max_label_combinations: int = 1000):
        self._children: OrderedDict[tuple, SafeGaugeChild] = OrderedDict()
        self._max_label_combinations = max_label_combinations
```

| 장점 | 단점 |
|---|---|
| 메모리 상한 보장 | Eviction 시 shadow 값 손실 (경고 로그 필요) |
| 예측 가능한 동작 | 상한 도달 시 가장 오래된 메트릭 정확도 저하 |
| 자주 사용되는 메트릭 보존 | 추가 구현 필요 |
| Thread-safe 구현 용이 | OrderedDict 오버헤드 (미미함) |

### 2.3 구현 코드

```python
# metrics/safe_gauge/core.py - 개선된 SafeGauge

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from .sync import SyncInfo, SyncStatus
from .noop import NoOpGaugeChild
from .clamping import clamp_non_negative

if TYPE_CHECKING:
    from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# Eviction 메트릭용 (선택적)
_eviction_count = 0


class SafeGauge:
    """
    Safe wrapper for Prometheus Gauge with LRU-based memory management.
    
    Features:
    - Prevents negative gauge values (clamping at 0)
    - LRU cache to prevent unbounded memory growth
    - Configurable max_label_combinations
    - Eviction callback for monitoring
    
    Args:
        gauge: Prometheus Gauge to wrap
        max_label_combinations: Maximum number of label combinations to cache (default: 1000)
        on_eviction: Optional callback when a child is evicted
        
    Example:
        >>> raw = Gauge("orders_pending", "Pending orders", ["user_id"])
        >>> safe = SafeGauge(raw, max_label_combinations=500)
        >>> 
        >>> # 500개 초과 시 가장 오래된 레이블 조합 자동 제거
        >>> safe.labels(user_id="user_001").inc()
    """

    def __init__(
        self, 
        gauge: Optional["Gauge"],
        max_label_combinations: int = 1000,
        on_eviction: Optional[Callable[[tuple, "SafeGaugeChild"], None]] = None,
    ):
        """
        Initialize SafeGauge with LRU cache.

        Args:
            gauge: Prometheus Gauge to wrap. If None, operations are no-ops.
            max_label_combinations: Maximum cached label combinations (default: 1000)
            on_eviction: Callback when a child is evicted (for monitoring)
        """
        self._gauge = gauge
        self._children: OrderedDict[tuple, SafeGaugeChild] = OrderedDict()
        self._max_label_combinations = max_label_combinations
        self._on_eviction = on_eviction
        self._lock = threading.Lock()
        self._eviction_count = 0

    def labels(self, **kwargs) -> "SafeGaugeChild":
        """
        Get a SafeGaugeChild for the given labels.
        
        Uses LRU cache: recently accessed children are kept, 
        oldest are evicted when max_label_combinations is exceeded.

        Args:
            **kwargs: Label key-value pairs

        Returns:
            SafeGaugeChild instance for thread-safe operations
        """
        if self._gauge is None:
            return NoOpGaugeChild()

        key = tuple(sorted(kwargs.items()))

        with self._lock:
            if key in self._children:
                # Move to end (most recently used)
                self._children.move_to_end(key)
                return self._children[key]
            
            # Evict oldest if at capacity
            if len(self._children) >= self._max_label_combinations:
                self._evict_oldest()
            
            # Create new child
            gauge_child = self._gauge.labels(**kwargs)
            child = SafeGaugeChild(gauge_child, kwargs)
            self._children[key] = child
            return child

    def _evict_oldest(self) -> None:
        """Evict the least recently used child."""
        if not self._children:
            return
        
        oldest_key, oldest_child = self._children.popitem(last=False)
        self._eviction_count += 1
        
        # Log warning for operational awareness
        logger.warning(
            f"[SafeGauge] LRU eviction #{self._eviction_count}: "
            f"labels={dict(oldest_key)}, shadow_value={oldest_child.get_shadow_value()}"
        )
        
        # Record eviction metric
        try:
            from selfhealing.metrics.counters import SAFEGAUGE_EVICTIONS
            SAFEGAUGE_EVICTIONS.labels(gauge_name=self._gauge._name if self._gauge else "unknown").inc()
        except Exception:
            pass
        
        # Callback for custom handling
        if self._on_eviction:
            try:
                self._on_eviction(oldest_key, oldest_child)
            except Exception as e:
                logger.error(f"[SafeGauge] Eviction callback failed: {e}")

    def get_child(self, **kwargs) -> Optional["SafeGaugeChild"]:
        """
        Get existing SafeGaugeChild without creating new one.
        Does NOT update LRU order.

        Args:
            **kwargs: Label key-value pairs

        Returns:
            SafeGaugeChild if exists, None otherwise
        """
        key = tuple(sorted(kwargs.items()))
        with self._lock:
            return self._children.get(key)

    @property
    def is_available(self) -> bool:
        """Check if underlying gauge is available."""
        return self._gauge is not None
    
    @property
    def current_size(self) -> int:
        """Current number of cached label combinations."""
        with self._lock:
            return len(self._children)
    
    @property
    def eviction_count(self) -> int:
        """Total number of evictions since creation."""
        return self._eviction_count
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring.
        
        Returns:
            Dict with cache stats
        """
        with self._lock:
            return {
                "current_size": len(self._children),
                "max_size": self._max_label_combinations,
                "eviction_count": self._eviction_count,
                "utilization_percent": (len(self._children) / self._max_label_combinations) * 100,
            }


__all__ = [
    "SafeGauge",
    "SafeGaugeChild",
]
```

### 2.4 네이밍 결정

| 원래 제안 | 채택 | 이유 |
|---|---|---|
| `max_children` | `max_label_combinations` | 더 명확 - "레이블 조합"이 실제 의미 |
| `on_eviction` | `on_eviction` | 적절 - 콜백 목적 명확 |

### 2.5 환경별 권장 설정

| 환경 | `max_label_combinations` | 이유 |
|---|---|---|
| 개발/테스트 | 100 | 빠른 eviction 테스트 |
| 단일 서버 | 1,000 | 기본값 |
| K8s 10 Pods | 500 | 메모리 공유 고려 |
| K8s 100+ Pods | 200 | 메모리 제한 엄격 |

### 2.6 SyncInfo 관리 설계

#### 현재 SyncInfo 구조

**코드 위치**: `metrics/safe_gauge/sync.py`

```python
@dataclass
class SyncInfo:
    """Prometheus ↔ Shadow 동기화 정보"""
    last_sync_time: Optional[float] = None
    sync_count: int = 0
    drift_detected_count: int = 0
    last_drift_amount: float = 0.0
    status: SyncStatus = SyncStatus.UNSYNCED
```

#### 설계 결정: 임베디드 방식 유지

| 방안 | 장점 | 단점 | 결정 |
|---|---|---|---|
| SyncInfo 내장 (현재) | 간단, 성능 최적 | 별도 관리 어려움 | ✅ 채택 |
| 별도 SyncInfoRegistry | 중앙 관리 | 복잡도 증가, 동기화 필요 | ❌ 기각 |

**이유**: SafeGaugeChild와 SyncInfo는 1:1 관계로, 분리 시 오버헤드만 증가.
LRU eviction 시 SyncInfo도 함께 정리되어 메모리 관리 일관성 유지.

### 2.7 SAFEGAUGE_EVICTIONS 메트릭 정의

```python
# metrics/counters.py에 추가

from prometheus_client import Counter

SAFEGAUGE_EVICTIONS = Counter(
    "selfhealing_safegauge_evictions_total",
    "Total number of SafeGauge LRU evictions",
    ["gauge_name"]
)
```

---

## 3. Decorator Universal Async Support

### 3.1 문제점 분석

**현재 비동기 지원 현황**:

| Decorator | 비동기 지원 | 코드 위치 |
|---|---|---|
| `with_jitter` | ✅ 지원 | `utils/jitter.py` |
| `track_replay` | ❌ 미지원 | `metrics/decorators.py` |
| `track_dlq_creation` | ❌ 미지원 | `metrics/decorators.py` |
| `track_dlq_resolution` | ❌ 미지원 | `metrics/decorators.py` |
| `automation_gate` | ❌ 미지원 | `services/error_budget_gate/gate.py` |

**`with_jitter` 구현 (참조용)**:
```python
# utils/jitter.py - 이미 Universal 패턴 적용됨
if asyncio.iscoroutinefunction(func):
    return async_wrapper
return sync_wrapper
```

### 3.2 Universal Decorator 헬퍼

```python
# utils/decorator_utils.py (신규 파일)

"""
Universal Decorator Utilities.

Provides helpers for creating decorators that support both sync and async functions.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar, ParamSpec

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def make_universal_decorator(
    sync_before: Callable[..., Any] = None,
    sync_after: Callable[..., Any] = None,
    async_before: Callable[..., Any] = None,
    async_after: Callable[..., Any] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Create a universal decorator that works with both sync and async functions.
    
    Args:
        sync_before: Called before sync function execution
        sync_after: Called after sync function execution
        async_before: Called before async function execution (can be async)
        async_after: Called after async function execution (can be async)
    
    Returns:
        Universal decorator
    
    Example:
        >>> def log_start(*args, **kwargs):
        ...     logger.info("Starting...")
        >>> 
        >>> def log_end(result, *args, **kwargs):
        ...     logger.info(f"Finished with: {result}")
        >>> 
        >>> @make_universal_decorator(sync_before=log_start, sync_after=log_end)
        ... def my_func():
        ...     return "done"
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                if async_before:
                    if asyncio.iscoroutinefunction(async_before):
                        await async_before(*args, **kwargs)
                    else:
                        async_before(*args, **kwargs)
                
                result = await func(*args, **kwargs)
                
                if async_after:
                    if asyncio.iscoroutinefunction(async_after):
                        await async_after(result, *args, **kwargs)
                    else:
                        async_after(result, *args, **kwargs)
                
                return result
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                if sync_before:
                    sync_before(*args, **kwargs)
                
                result = func(*args, **kwargs)
                
                if sync_after:
                    sync_after(result, *args, **kwargs)
                
                return result
            return sync_wrapper
    return decorator


def universal_wrapper(
    func: Callable[P, R],
    sync_impl: Callable[..., R],
    async_impl: Callable[..., R],
) -> Callable[P, R]:
    """
    Wrap a function with either sync or async implementation based on function type.
    
    Args:
        func: Original function to wrap
        sync_impl: Synchronous wrapper implementation
        async_impl: Asynchronous wrapper implementation
    
    Returns:
        Wrapped function
    """
    if asyncio.iscoroutinefunction(func):
        return async_impl
    return sync_impl


__all__ = [
    "make_universal_decorator",
    "universal_wrapper",
]
```

### 3.3 track_replay Universal 구현

```python
# metrics/decorators.py - 개선된 track_replay

import asyncio
import time
from functools import wraps
from typing import Callable, TypeVar, ParamSpec

from selfhealing.metrics.event_handlers import ReplayEventHandler

P = ParamSpec("P")
R = TypeVar("R")


def track_replay(
    domain: str = "",
    replay_type: str = "auto",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Replay 함수에 메트릭 추적을 추가하는 Universal 데코레이터.
    
    동기/비동기 함수 모두 지원합니다.

    Args:
        domain: 도메인 이름 (빈 문자열이면 kwargs에서 추출)
        replay_type: Replay 유형 (auto, manual, batch)

    Example:
        >>> @track_replay(domain="payment")
        ... async def replay_payment(dlq_item):
        ...     await process_payment(dlq_item.payload)
        ...     return True
        
        >>> @track_replay(replay_type="batch")
        ... def batch_replay(items, domain="payment"):
        ...     pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _domain = domain or kwargs.get("domain", "unknown")
            _replay_type = kwargs.get("replay_type", replay_type)
            
            ReplayEventHandler.on_replay_started(_domain, _replay_type)
            start_time = time.monotonic()
            success = False
            
            try:
                result = func(*args, **kwargs)
                success = result if isinstance(result, bool) else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(_domain, success, duration)

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _domain = domain or kwargs.get("domain", "unknown")
            _replay_type = kwargs.get("replay_type", replay_type)
            
            ReplayEventHandler.on_replay_started(_domain, _replay_type)
            start_time = time.monotonic()
            success = False
            
            try:
                result = await func(*args, **kwargs)
                success = result if isinstance(result, bool) else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(_domain, success, duration)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
```

### 3.4 automation_gate Universal 구현

```python
# services/error_budget_gate/gate.py - 개선된 automation_gate

import asyncio
import functools
from typing import Callable, TypeVar, ParamSpec

P = ParamSpec("P")
R = TypeVar("R")


def automation_gate(action: str = "") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    자동화 게이트 데코레이터 (Universal - sync/async 모두 지원).
    
    에러 예산이 부족하면 함수 실행을 차단합니다.
    
    Usage:
        >>> @automation_gate(action="dlq_auto_replay")
        ... def auto_replay_dlq():
        ...     pass
        
        >>> @automation_gate(action="async_cleanup")
        ... async def async_cleanup():
        ...     await do_cleanup()
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        
        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        
        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Note: require_automation_allowed is sync, 
            # run in executor if needed for true async
            require_automation_allowed(action=action or func.__name__)
            return await func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
```

### 3.5 track_dlq_creation/resolution Universal 구현

```python
# metrics/decorators.py - 개선된 DLQ 데코레이터

def track_dlq_creation(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 생성 함수에 메트릭 추적을 추가하는 Universal 데코레이터.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = func(*args, **kwargs)
            failure_type = kwargs.get("failure_type", "unknown")
            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = await func(*args, **kwargs)
            failure_type = kwargs.get("failure_type", "unknown")
            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def track_dlq_resolution(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 해결 함수에 메트릭 추적을 추가하는 Universal 데코레이터.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.monotonic()
            result = func(*args, **kwargs)
            duration = time.monotonic() - start_time
            resolution_type = kwargs.get("resolution_type", "auto_replay")
            DLQMetricEventHandler.on_item_resolved(domain, resolution_type, duration)
            return result

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.monotonic()
            result = await func(*args, **kwargs)
            duration = time.monotonic() - start_time
            resolution_type = kwargs.get("resolution_type", "auto_replay")
            DLQMetricEventHandler.on_item_resolved(domain, resolution_type, duration)
            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
```

---

## 3.6 CleanupService 상태 비저장 설계 검증

### 3.6.1 현재 설계 분석

**CleanupService 구조** (`services/cleanup_service.py`):

```python
class CleanupService:
    """
    Cleanup 작업을 위한 상태 비저장(Stateless) 서비스.
    
    모든 의존성은 메서드 내부에서 lazy import됨.
    """
    
    # __init__ 없음 - 상태 비저장
    
    def archive_old_dlq_entries(self, older_than_days: int = 30) -> CleanupResult:
        from selfhealing.services.dlq_service import get_dlq_service
        # lazy import + 호출
```

### 3.6.2 설계 근거

| 설계 결정 | 근거 |
|---|---|
| **상태 비저장** | Celery 워커 재사용 안전성, 테스트 격리 용이 |
| **Lazy Import** | 순환 참조 방지, 초기화 시간 최소화 |
| **의존성 주입 없음** | 서비스 팩토리 패턴(`get_*_service`)으로 충분 |

### 3.6.3 의존성 주입 vs 현재 방식

| 방식 | 장점 | 단점 | 채택 |
|---|---|---|---|
| **현재 (Lazy Import)** | 간단, 순환참조 방지 | 테스트 시 mock 어려움 | ✅ |
| **생성자 DI** | 테스트 용이 | 순환참조 위험, 복잡도 증가 | ❌ |
| **하이브리드** | 유연성 | 코드 중복 | 향후 검토 |

**결론**: 현재 Lazy Import 방식 유지. `ProviderRegistry.override_provider`로 테스트 격리 보완.

---

## 4. Task Layer exc_info 일관성

### 4.1 문제점 분석

**현재 상태**:

| 레이어 | `exc_info` 사용 | 스택트레이스 |
|---|---|---|
| CleanupService | ✅ `exc_info=True` | ✅ 로그에 기록 |
| cleanup_tasks | ❌ 없음 | ❌ 메시지만 |

**코드 비교**:
```python
# cleanup_service.py Line 90 ✅
logger.error(f"[CleanupService] Archive failed: {e}", exc_info=True)

# cleanup_tasks.py Lines 48-50 ❌
except Exception as e:
    logger.error(f"[CleanupTask] archive_old_dlq_entries failed: {e}")  # exc_info 없음
    raise
```

### 4.2 구현 코드

```python
# tasks/cleanup_tasks.py - 일관성 개선

def archive_old_dlq_entries(older_than_days: int = 30) -> Dict[str, Any]:
    """30일 이상 된 해결된 DLQ 항목을 아카이브."""
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.archive_old_dlq_entries(older_than_days=older_than_days)
        return result.to_dict()

    except Exception as e:
        logger.error(
            f"[CleanupTask] archive_old_dlq_entries failed: {e}",
            exc_info=True  # ✅ 추가
        )
        raise


def cleanup_expired_config(older_than_hours: int = 24) -> Dict[str, Any]:
    """만료된 Pending Config 항목 정리."""
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.cleanup_expired_config(older_than_hours=older_than_hours)
        return result.to_dict()

    except Exception as e:
        logger.error(
            f"[CleanupTask] cleanup_expired_config failed: {e}",
            exc_info=True  # ✅ 추가
        )
        raise


def expire_approval_requests(older_than_hours: int = 72) -> Dict[str, Any]:
    """72시간 이상 대기 중인 승인 요청 만료 처리."""
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.expire_approval_requests(older_than_hours=older_than_hours)
        return result.to_dict()

    except Exception as e:
        logger.error(
            f"[CleanupTask] expire_approval_requests failed: {e}",
            exc_info=True  # ✅ 추가
        )
        raise


def purge_archived_dlq_entries(
    older_than_days: int = 90,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """90일 이상 된 아카이브 항목을 영구 삭제."""
    from selfhealing.services.cleanup_service import get_cleanup_service

    try:
        service = get_cleanup_service()
        result = service.purge_archived_dlq_entries(
            older_than_days=older_than_days,
            dry_run=dry_run,
        )
        return result.to_dict()

    except Exception as e:
        logger.error(
            f"[CleanupTask] purge_archived_dlq_entries failed: {e}",
            exc_info=True  # ✅ 추가
        )
        raise
```

### 4.3 적용 대상 파일

| 파일 | 수정 필요 함수 수 |
|---|---|
| `tasks/cleanup_tasks.py` | 4개 |
| `tasks/daily_report.py` | 1개 |
| `tasks/chaos_scheduler.py` | 검토 필요 |
| `tasks/governance.py` | 검토 필요 |

---

## 5. ProviderRegistry override_provider

### 5.1 문제점 분석

**현재 테스트 방식** (`conftest.py` Lines 273-288):
```python
# ⚠️ private 속성 직접 접근
original_instances = ProviderRegistry._instances.copy()
ProviderRegistry.clear_instances()
yield ProviderRegistry
ProviderRegistry._instances = original_instances  # ⚠️ 내부 상태 직접 복원
```

**문제점**:
- `_instances` 같은 private 속성 직접 접근
- 전역 상태 오염 가능성
- 멀티스레드 테스트 시 경쟁 조건

### 5.2 구현 코드

```python
# services/factory/registry.py - override_provider 추가

from contextlib import contextmanager
from typing import Generator

class ProviderRegistry:
    # 기존 코드 유지...
    
    # =========================================================================
    # Test Isolation Utilities
    # =========================================================================
    
    @classmethod
    @contextmanager
    def override_provider(
        cls, 
        provider_type: str, 
        mock_instance: Any
    ) -> Generator[None, None, None]:
        """
        테스트용 Provider 임시 교체 (Context Manager).
        
        전역 상태를 안전하게 교체하고 자동으로 복원합니다.
        
        Args:
            provider_type: "cache" 또는 "queue"
            mock_instance: Mock 인스턴스
        
        Usage:
            >>> with ProviderRegistry.override_provider("cache", mock_cache):
            ...     # 이 블록 내에서만 mock_cache 사용
            ...     do_something()
            >>> # 자동 복원
        
        Thread Safety:
            이 메서드는 thread-local이 아니므로, 
            멀티스레드 테스트에서는 각 테스트가 독립 프로세스에서 실행되어야 합니다.
        """
        if provider_type not in ("cache", "queue"):
            raise ValueError(f"Unknown provider_type: {provider_type}")
        
        instances = cls._cache_instances if provider_type == "cache" else cls._queue_instances
        default_name = cls._default_cache if provider_type == "cache" else cls._default_queue
        
        # 기존 인스턴스 백업
        old_instance = instances.get(default_name)
        
        # Mock 인스턴스 설정
        instances[default_name] = mock_instance
        logger.debug(f"[ProviderRegistry] Override {provider_type}: {type(mock_instance).__name__}")
        
        try:
            yield
        finally:
            # 복원
            if old_instance is not None:
                instances[default_name] = old_instance
            else:
                instances.pop(default_name, None)
            logger.debug(f"[ProviderRegistry] Restored {provider_type}")
    
    @classmethod
    @contextmanager
    def isolated_test_context(cls) -> Generator["ProviderRegistry", None, None]:
        """
        완전히 격리된 테스트 컨텍스트 제공.
        
        모든 인스턴스와 기본값을 임시로 교체하고 자동 복원합니다.
        
        Usage:
            >>> with ProviderRegistry.isolated_test_context() as registry:
            ...     registry.set_defaults(cache="memory", queue="sync")
            ...     # 격리된 환경에서 테스트
            >>> # 자동 복원
        """
        # 전체 상태 백업
        old_cache_instances = cls._cache_instances.copy()
        old_queue_instances = cls._queue_instances.copy()
        old_default_cache = cls._default_cache
        old_default_queue = cls._default_queue
        
        # 초기화
        cls._cache_instances = {}
        cls._queue_instances = {}
        
        logger.debug("[ProviderRegistry] Entering isolated test context")
        
        try:
            yield cls
        finally:
            # 복원
            cls._cache_instances = old_cache_instances
            cls._queue_instances = old_queue_instances
            cls._default_cache = old_default_cache
            cls._default_queue = old_default_queue
            logger.debug("[ProviderRegistry] Exited isolated test context")
```

### 5.3 테스트 코드 개선

```python
# tests/conftest.py - 개선된 fixture

@pytest.fixture
def isolated_provider_registry():
    """Provides an isolated ProviderRegistry context for tests."""
    with ProviderRegistry.isolated_test_context() as registry:
        registry.configure_for_testing()
        yield registry


@pytest.fixture
def mock_cache_provider():
    """Provides a mock cache provider for tests."""
    from unittest.mock import MagicMock
    
    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    mock_cache.set.return_value = True
    mock_cache.health_check.return_value = True
    
    with ProviderRegistry.override_provider("cache", mock_cache):
        yield mock_cache
```

---

## 6. automation_gate Audit 포렌식 필드

### 6.1 현재 코드 분석

**현재 `log_error_budget_blocked_audit`** (`chaos_audit.py` Lines 386-447):
```python
def log_error_budget_blocked_audit(
    action: str,
    gate_status: str,
    error_budget_percent: Optional[float] = None,
    threshold_percent: Optional[float] = None,
    reason: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
    details = {
        "action": action,
        "gate_status": gate_status,
        "error_budget_percent": error_budget_percent,
        "threshold_percent": threshold_percent,
        "reason": reason,
        "manual_mode_enforced": True,
        # ⚠️ trace_id, actor_roles 없음 (details에)
    }
    
    wal_seq = _write_to_wal(
        event_type="ERROR_BUDGET_BLOCKED",
        source="ErrorBudgetGate",
        details=details,
        ...
        # trace_id, actor_roles는 _write_to_wal 내부에서 자동 추출됨
    )
```

**`_write_to_wal` 자동 추출** (`audit/base.py` Lines 171-187):
```python
actor_id, actor_type, final_roles = _get_actor_info(actor_roles)
final_trace_id = _get_trace_id_from_context(trace_id)

wal_entry = {
    ...
    "trace_id": final_trace_id,      # ✅ WAL 최상위에 기록
    "actor_roles": final_roles,       # ✅ WAL 최상위에 기록
    ...
}
```

### 6.2 현재 상태 평가

| 필드 | WAL 최상위 | details 내부 | 상태 |
|---|---|---|---|
| `trace_id` | ✅ 자동 | ❌ 없음 | ⚠️ 개선 가능 |
| `actor_roles` | ✅ 자동 | ❌ 없음 | ⚠️ 개선 가능 |

**현재도 동작하지만**, `details` 내에도 포함하면 쿼리/분석 시 더 편리합니다.

### 6.3 개선된 구현

```python
# services/audit/chaos_audit.py - 개선된 log_error_budget_blocked_audit

def log_error_budget_blocked_audit(
    action: str,
    gate_status: str,
    error_budget_percent: Optional[float] = None,
    threshold_percent: Optional[float] = None,
    reason: Optional[str] = None,
    request: Any = None,
    # 새 파라미터들 (명시적 전달 또는 자동 추출)
    blocked_request_trace_id: Optional[str] = None,
    actor_roles: Optional[list[str]] = None,
) -> Optional[int]:
    """
    Error Budget Gate 차단을 Audit 로그에 기록.
    
    에러 예산 부족으로 인한 자동화 차단을 기록합니다.
    WAL 기반 누락 0 보장.
    
    Args:
        action: 차단된 액션 이름
        gate_status: 게이트 상태 (BLOCKED, CRITICAL, etc.)
        error_budget_percent: 현재 에러 예산 잔여율
        threshold_percent: 차단 임계치
        reason: 차단 사유
        request: Django request 객체 (선택)
        blocked_request_trace_id: 차단된 요청의 Trace ID (None이면 자동 추출)
        actor_roles: 실행 주체의 역할 목록 (None이면 자동 추출)
    
    Returns:
        WAL 시퀀스 번호 (성공 시), None (실패 시)
    """
    # trace_id 자동 추출
    if blocked_request_trace_id is None:
        try:
            from selfhealing.audit.trace import get_trace_id
            blocked_request_trace_id = get_trace_id()
        except Exception:
            blocked_request_trace_id = None
    
    # actor_roles 자동 추출
    if actor_roles is None:
        try:
            from selfhealing.context.actor_context import ActorContext
            if ActorContext.is_set():
                actor_roles = ActorContext.get_current().roles
            else:
                actor_roles = []
        except Exception:
            actor_roles = []
    
    details = {
        "action": action,
        "gate_status": gate_status,
        "error_budget_percent": error_budget_percent,
        "threshold_percent": threshold_percent,
        "reason": reason,
        "manual_mode_enforced": True,
        # ✅ 포렌식 필드 추가
        "blocked_request_trace_id": blocked_request_trace_id,
        "actor_roles_at_block": actor_roles,
        "blocked_at": time.time(),
    }
    details = {k: v for k, v in details.items() if v is not None}
    
    wal_seq = _write_to_wal(
        event_type="ERROR_BUDGET_BLOCKED",
        source="ErrorBudgetGate",
        details=details,
        success=False,
        error_message=reason,
        target_id=action,
        # 명시적 전달 (중복이지만 일관성)
        actor_roles=actor_roles,
        trace_id=blocked_request_trace_id,
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.ERROR_BUDGET_BLOCKED,
                source="ErrorBudgetGate",
                details=details,
                success=False,
                error_message=reason,
                target_id=action,
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    budget_str = f"{error_budget_percent:.1f}%" if error_budget_percent is not None else "N/A"
    logger.warning(
        f"[ErrorBudgetAudit] BLOCKED | action={action} | "
        f"budget={budget_str} | status={gate_status} | "
        f"trace_id={blocked_request_trace_id}"  # ✅ 로그에도 추가
    )
    return wal_seq
```

### 6.4 네이밍 결정

| 제안된 이름 | 채택 | 이유 |
|---|---|---|
| `triggering_trace_id` | ❌ | "triggering"이 모호함 |
| `blocked_request_trace_id` | ✅ | 차단된 요청임을 명확히 |
| `actor_roles` | `actor_roles_at_block` | 차단 시점의 역할임을 명확히 |

### 6.5 추가 권장 필드

| 필드 | 목적 |
|---|---|
| `blocked_at` | 차단 시점 timestamp |
| `budget_consumption_rate` | 예산 소진 속도 (향후) |
| `recent_actions` | 최근 N개 액션 목록 (향후) |

---

## 7. 구현 로드맵

```
Phase 4.1: SafeGauge LRU 캐시 ✅
├── OrderedDict 기반 LRU 구현
├── max_label_combinations 파라미터 추가
├── Eviction 메트릭 추가
└── 단위 테스트 추가

Phase 4.2: Universal Async Decorators ✅
├── utils/decorator_utils.py 생성
├── track_replay Universal 구현
├── automation_gate Universal 구현
├── track_dlq_* Universal 구현
└── 단위 테스트 추가

Phase 4.3: exc_info 일관성 ✅
├── cleanup_tasks.py 수정
├── daily_report.py 수정
└── 기타 Task 파일 검토

Phase 4.4: ProviderRegistry 테스트 격리 ✅
├── override_provider Context Manager 추가
├── isolated_test_context 추가
└── conftest.py 개선

Phase 4.5: Audit 포렌식 필드 ✅
├── log_error_budget_blocked_audit 개선
├── blocked_request_trace_id 추가
├── actor_roles_at_block 추가
└── 통합 테스트 추가
```

---

## 8. 검증 체크리스트

### 8.1 SafeGauge LRU

- [x] `max_label_combinations` 초과 시 eviction 발생
- [x] LRU 순서 유지 확인 (최근 접근 항목 보존)
- [x] Eviction 메트릭 기록 확인
- [x] Thread-safety 테스트

### 8.2 Universal Decorators

- [x] 동기 함수에 적용 시 정상 동작
- [x] 비동기 함수에 적용 시 정상 동작
- [x] 메트릭 기록 정상
- [x] 예외 전파 정상

### 8.3 exc_info 일관성

- [x] 모든 Task에서 `exc_info=True` 적용
- [x] 스택트레이스 로그 확인
- [x] Celery 결과 백엔드 정상

### 8.4 ProviderRegistry 테스트 격리

- [x] `override_provider` Context Manager 동작
- [x] `isolated_test_context` 동작
- [x] 복원 확인

### 8.5 Audit 포렌식

- [x] `blocked_request_trace_id` WAL 기록 확인
- [x] `actor_roles_at_block` WAL 기록 확인
- [x] 자동 추출 동작 확인

---

## 9. 위험 분석 및 완화 전략

### 9.1 SafeGauge LRU 캐시 위험

| 위험 | 심각도 | 완화 전략 |
|---|---|---|
| Eviction 시 shadow 값 손실 | 중간 | 경고 로그 기록, 모니터링 대시보드 |
| 높은 cardinality 레이블 사용 | 높음 | 코드 리뷰 가이드, Lint 규칙 추가 |
| 환경별 설정 누락 | 낮음 | 환경 변수 기반 기본값, 문서화 |

### 9.2 Universal Async 위험

| 위험 | 심각도 | 완화 전략 |
|---|---|---|
| 비동기 컨텍스트 전환 오버헤드 | 낮음 | 벤치마크 테스트 수행 |
| 기존 sync-only 코드 호환성 | 낮음 | 하위 호환성 유지 (sync 기본) |
| 예외 전파 문제 | 중간 | 통합 테스트 강화 |

### 9.3 테스트 격리 위험

| 위험 | 심각도 | 완화 전략 |
|---|---|---|
| 복원 실패 시 전역 상태 오염 | 높음 | try/finally 필수, 테스트 후 검증 |
| 멀티스레드 테스트 경쟁 조건 | 중간 | 프로세스 격리 또는 락 사용 |

---

## 10. 마이그레이션 가이드

### 10.1 SafeGauge LRU 마이그레이션

**변경 전**:
```python
from selfhealing.metrics.safe_gauge.core import SafeGauge

gauge = SafeGauge(raw_gauge)
```

**변경 후**:
```python
from selfhealing.metrics.safe_gauge.core import SafeGauge

# 기본값 사용 (max_label_combinations=1000)
gauge = SafeGauge(raw_gauge)

# 환경별 커스터마이징
gauge = SafeGauge(
    raw_gauge, 
    max_label_combinations=500,  # K8s 환경
    on_eviction=lambda k, v: logger.info(f"Evicted: {k}")
)
```

**호환성**: ✅ 하위 호환 (기존 코드 변경 불필요)

### 10.2 Universal Decorator 마이그레이션

**변경 불필요**: 기존 sync 함수는 그대로 동작, async 함수 지원 추가됨.

```python
# 기존 (변경 없음)
@track_replay(domain="payment")
def sync_replay():
    pass

# 신규 지원
@track_replay(domain="payment")
async def async_replay():
    await do_something()
```

### 10.3 ProviderRegistry 테스트 마이그레이션

**변경 전**:
```python
@pytest.fixture
def provider_registry():
    original = ProviderRegistry._instances.copy()  # ⚠️ private 접근
    ProviderRegistry.clear_instances()
    yield ProviderRegistry
    ProviderRegistry._instances = original
```

**변경 후**:
```python
@pytest.fixture
def provider_registry():
    with ProviderRegistry.isolated_test_context() as registry:
        registry.configure_for_testing()
        yield registry
```

---

## 11. 설계 결정 기록 (ADR)

### ADR-001: LRU vs WeakRef 선택

- **결정**: LRU 캐시 (OrderedDict) 채택
- **이유**: SafeGauge의 핵심 가치인 `_shadow_value` 보존 필수
- **대안 기각**: WeakRef는 GC 시점에 shadow 값 손실로 음수 방지 로직 무력화

### ADR-002: Universal Decorator 패턴 채택

- **결정**: `asyncio.iscoroutinefunction()` 기반 분기
- **이유**: `with_jitter`에서 이미 검증된 패턴
- **참조**: Python 3.8+ 표준 패턴

### ADR-003: 포렌식 필드 네이밍

- **결정**: `blocked_request_trace_id`, `actor_roles_at_block` 채택
- **이유**: "차단된 요청"과 "차단 시점" 의미 명확화
- **기각**: `triggering_trace_id` (모호함)

---

## 12. 참조

- [PART 1: SafeGauge 모듈화](41_WRAPPER_REFACTORING_PART1.md)
- [PART 2: Task Wrapper 일관성](41_WRAPPER_REFACTORING_PART2.md)
- [PART 3: Decorator 통합](41_WRAPPER_REFACTORING_PART3.md)
- [메인 리팩토링 계획](41_WRAPPER_REFACTORING_PLAN.md)
