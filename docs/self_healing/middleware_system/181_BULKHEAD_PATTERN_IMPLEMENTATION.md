# 181. Bulkhead 패턴 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-05
> **의존성**: [180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)
> **예상 소요**: 3-5일
> **예상 코드량**: ~800줄

---

## 0. 문서 목적

이 문서는 **Bulkhead(격벽) 패턴** 구현 가이드입니다.

**핵심 목표**:
- "리소스 격리를 통해 한 컴포넌트의 장애가 다른 컴포넌트로 전파되지 않도록 방지"
- "서비스/도메인별 독립적인 리소스 풀 제공"
- "기존 `ConnectionType` 분류 체계와 통합"

---

## 1. Bulkhead 패턴이란?

### 1.1 개념

**Bulkhead(격벽)**는 선박 설계에서 유래한 패턴입니다.

```
┌─────────────────────────────────────────────────────────────┐
│                        선박 (Ship)                          │
├───────────┬───────────┬───────────┬───────────┬─────────────┤
│  구역 A   │  구역 B   │  구역 C   │  구역 D   │   구역 E    │
│ (침수 X)  │ (침수 O)  │ (침수 X)  │ (침수 X)  │  (침수 X)   │
│  정상     │  격리됨   │   정상    │   정상    │    정상     │
└───────────┴───────────┴───────────┴───────────┴─────────────┘
          격벽(Bulkhead)이 침수 확산을 방지
```

**소프트웨어에서의 적용**:
- 리소스(스레드, 커넥션, 메모리)를 격리된 풀로 분리
- 한 풀의 고갈이 다른 풀에 영향을 주지 않음
- **연쇄 장애(Cascading Failure) 방지**

### 1.2 Bulkhead 유형

| 유형 | 격리 대상 | 사용 사례 |
|------|----------|----------|
| **Thread Pool Bulkhead** | 스레드 | CPU 바운드 작업 격리 |
| **Semaphore Bulkhead** | 동시 실행 수 | I/O 바운드 작업 제한 |
| **Connection Pool Bulkhead** | DB/Redis 커넥션 | 외부 리소스 격리 |

---

## 2. 현재 상황 분석 (코드 근거)

### 2.1 기존 ConnectionType 분류 - 격리 없음

**파일**: `packages/selfhealing-python/src/selfhealing/core/connection_health.py`
**라인**: 22-27

```python
class ConnectionType(str, Enum):
    """Types of connections to monitor"""

    DATABASE = "database"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
    MESSAGE_QUEUE = "message_queue"
```

**한계점**:
- 분류만 존재, **리소스 격리 없음**
- 모든 타입이 동일한 스레드/커넥션 풀 공유

### 2.2 기존 ThreadPoolExecutor - 단일 풀

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/memory/layered_repository/base.py`
**라인**: 37-48

```python
class LayeredRepositoryBase:
    # ThreadPoolExecutor for async L2 operations with timeout
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        """Get or create shared ThreadPoolExecutor."""
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(
                        max_workers=4, thread_name_prefix="l2_sync"
                    )
        return cls._executor
```

**한계점**:
- **단일 공유 풀** (`max_workers=4`)
- L2 동기화가 느려지면 **모든 작업이 영향받음**

### 2.3 기존 AsyncHealingLogger - 단일 풀

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 138, 173, 323

```python
class AsyncHealingLogger:
    """
    - ThreadPoolExecutor: CRITICAL 이벤트 스레드 풀 (스레드 폭발 방지)
    """
    _critical_executor: ThreadPoolExecutor | None = None

    # ...
    cls._critical_executor = ThreadPoolExecutor(
        max_workers=..., thread_name_prefix="critical_event"
    )
```

**한계점**:
- CRITICAL 전용 풀은 있으나, **도메인별 격리 없음**

### 2.4 기존 RateController - 전역 제어

**파일**: `packages/selfhealing-python/src/selfhealing/scaling/rate_controller.py`
**라인**: 163-170

```python
class RateController:
    def __init__(
        self,
        settings: BackpressureSettings | None = None,
        queue_size_provider: Callable[[], int] | None = None,
    ):
        self._token_bucket = TokenBucket(self._current_rate)
```

**한계점**:
- **전역 단일 Token Bucket**
- 도메인별 Rate Limit 불가

### 2.5 기존 TrafficGate - 격리 없음

**파일**: `packages/selfhealing-python/src/selfhealing/scaling/traffic_gate.py`
**라인**: 63-75

```python
class TrafficGate:
    def __init__(
        self,
        settings: BackpressureSettings | None = None,
        rate_controller: RateController | None = None,
        load_shedding: Any | None = None,
    ):
        self._rate_controller = rate_controller or get_rate_controller()
```

**한계점**:
- 단일 `RateController` 사용
- **도메인별 독립적인 게이트 없음**

### 2.6 누락된 기능 요약

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| 도메인별 스레드 풀 | ❌ | Thread Pool Bulkhead |
| 도메인별 동시 실행 제한 | ❌ | Semaphore Bulkhead |
| 도메인별 Rate Limit | ❌ | 격리된 Token Bucket |
| 리소스 사용량 모니터링 | ❌ | Bulkhead 메트릭 |

---

## 3. 구현 목표

### 3.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Application Layer                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       BulkheadRegistry (싱글톤)                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │  bulkheads: dict[str, Bulkhead]                                         ││
│  │    "database"     → SemaphoreBulkhead(permits=10)                       ││
│  │    "cache"        → SemaphoreBulkhead(permits=20)                       ││
│  │    "external_api" → ThreadPoolBulkhead(max_workers=5)                   ││
│  │    "message_queue"→ SemaphoreBulkhead(permits=15)                       ││
│  │    "custom_domain"→ SemaphoreBulkhead(permits=8)                        ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│  Database Pool    │   │    Cache Pool     │   │ External API Pool │
│  (격리됨)         │   │    (격리됨)       │   │   (격리됨)        │
│  permits: 10      │   │    permits: 20    │   │   workers: 5      │
│  active: 3        │   │    active: 8      │   │   active: 2       │
└───────────────────┘   └───────────────────┘   └───────────────────┘
```

### 3.2 핵심 컴포넌트

| 컴포넌트 | 책임 |
|---------|------|
| `Bulkhead` | 리소스 격리 인터페이스 |
| `SemaphoreBulkhead` | 세마포어 기반 동시 실행 제한 |
| `ThreadPoolBulkhead` | 스레드 풀 기반 격리 |
| `BulkheadRegistry` | 도메인별 Bulkhead 관리 |
| `BulkheadMetrics` | Prometheus 메트릭 노출 |
| `@bulkhead` | 데코레이터 |

---

## 4. 상세 설계

### 4.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── resilience/
│   ├── __init__.py              # (기존)
│   ├── bypass_hooks.py          # (기존)
│   └── bulkhead/                # (신규)
│       ├── __init__.py
│       ├── base.py              # Bulkhead ABC
│       ├── semaphore.py         # SemaphoreBulkhead (동기)
│       ├── async_semaphore.py   # AsyncSemaphoreBulkhead (비동기) ⭐ 신규
│       ├── threadpool.py        # ThreadPoolBulkhead
│       ├── registry.py          # BulkheadRegistry
│       ├── metrics.py           # BulkheadMetrics
│       ├── decorator.py         # @bulkhead 데코레이터 (sync/async 자동 분기)
│       ├── exceptions.py        # BulkheadFullException
│       └── otel.py              # OpenTelemetry Span 통합 (선택적) ⭐ 신규
├── settings/
│   └── bulkhead.py              # BulkheadSettings (Pydantic)
```

### 4.2 인터페이스 설계

#### 4.2.1 Bulkhead ABC

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/base.py
"""
Bulkhead Base - 리소스 격리 인터페이스.

도메인 프리 설계: 특정 비즈니스 도메인에 종속되지 않음.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator, TypeVar

T = TypeVar("T")


class BulkheadType(str, Enum):
    """Bulkhead 유형."""

    SEMAPHORE = "semaphore"
    THREAD_POOL = "thread_pool"


@dataclass
class BulkheadState:
    """Bulkhead 현재 상태."""

    name: str
    bulkhead_type: BulkheadType
    max_concurrent: int
    active_count: int
    waiting_count: int
    rejected_count: int
    last_rejection_time: datetime | None = None

    @property
    def available_permits(self) -> int:
        """사용 가능한 허가 수."""
        return max(0, self.max_concurrent - self.active_count)

    @property
    def utilization_percent(self) -> float:
        """사용률 (%)."""
        if self.max_concurrent == 0:
            return 0.0
        return (self.active_count / self.max_concurrent) * 100


class Bulkhead(ABC):
    """
    Bulkhead 추상 인터페이스.

    Usage:
        bulkhead = SemaphoreBulkhead("my_domain", max_concurrent=10)

        # 컨텍스트 매니저 사용
        with bulkhead.acquire():
            do_work()

        # 또는 데코레이터 사용
        @bulkhead.wrap
        def do_work():
            pass
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Bulkhead 이름."""
        pass

    @abstractmethod
    def acquire(self, timeout: float | None = None) -> Generator[None, None, None]:
        """
        리소스 획득 (컨텍스트 매니저).

        Args:
            timeout: 대기 타임아웃 (초). None이면 즉시 실패.

        Yields:
            None

        Raises:
            BulkheadFullException: 리소스 획득 실패
        """
        pass

    @abstractmethod
    def try_acquire(self) -> bool:
        """
        리소스 획득 시도 (논블로킹).

        Returns:
            True면 획득 성공, False면 실패
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """리소스 반환."""
        pass

    @abstractmethod
    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        pass

    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]:
        """
        함수를 Bulkhead로 감싸는 데코레이터.

        Args:
            fn: 감쌀 함수

        Returns:
            Bulkhead가 적용된 함수
        """
        from functools import wraps

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            with self.acquire():
                return fn(*args, **kwargs)

        return wrapper
```

#### 4.2.2 SemaphoreBulkhead

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/semaphore.py
"""
Semaphore Bulkhead - 세마포어 기반 동시 실행 제한.

I/O 바운드 작업에 적합.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullException

logger = logging.getLogger(__name__)


class SemaphoreBulkhead(Bulkhead):
    """
    세마포어 기반 Bulkhead.

    동시 실행 수를 제한하여 리소스 고갈 방지.

    Usage:
        bulkhead = SemaphoreBulkhead("database", max_concurrent=10)

        with bulkhead.acquire(timeout=1.0):
            db_operation()
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
        fair: bool = True,
    ):
        """
        Args:
            name: Bulkhead 이름 (도메인 식별자)
            max_concurrent: 최대 동시 실행 수
            fair: True면 FIFO 순서 보장 (성능 약간 저하)
        """
        self._name = name
        self._max_concurrent = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()

        # 통계
        self._active_count = 0
        self._waiting_count = 0
        self._rejected_count = 0
        self._last_rejection_time: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @contextmanager
    def acquire(
        self, timeout: float | None = None
    ) -> Generator[None, None, None]:
        """
        리소스 획득.

        Args:
            timeout: 대기 타임아웃. None이면 즉시 실패 (논블로킹).

        Raises:
            BulkheadFullException: 리소스 획득 실패
        """
        acquired = False
        try:
            with self._lock:
                self._waiting_count += 1

            # timeout=None이면 blocking=False로 즉시 시도
            if timeout is None:
                acquired = self._semaphore.acquire(blocking=False)
            else:
                acquired = self._semaphore.acquire(blocking=True, timeout=timeout)

            with self._lock:
                self._waiting_count -= 1
                if acquired:
                    self._active_count += 1
                else:
                    self._rejected_count += 1
                    self._last_rejection_time = datetime.now(timezone.utc)

            if not acquired:
                raise BulkheadFullException(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_concurrent,
                    active_count=self._active_count,
                )

            yield

        finally:
            if acquired:
                self._semaphore.release()
                with self._lock:
                    self._active_count -= 1

    def try_acquire(self) -> bool:
        """논블로킹 획득 시도."""
        acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._active_count += 1
        return acquired

    def release(self) -> None:
        """리소스 반환."""
        self._semaphore.release()
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        with self._lock:
            return BulkheadState(
                name=self._name,
                bulkhead_type=BulkheadType.SEMAPHORE,
                max_concurrent=self._max_concurrent,
                active_count=self._active_count,
                waiting_count=self._waiting_count,
                rejected_count=self._rejected_count,
                last_rejection_time=self._last_rejection_time,
            )
```

#### 4.2.3 ThreadPoolBulkhead

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/threadpool.py
"""
Thread Pool Bulkhead - 스레드 풀 기반 격리.

CPU 바운드 작업에 적합.

ContextVar 전파:
- core/test_mode_context.py: _is_synthetic_request
- services/http_client.py: _is_chaos_request
- settings/layered_provider.py: _request_overrides
위 ContextVar들이 스레드 풀로 전파되도록 contextvars.copy_context() 사용.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Generator, TypeVar

from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullException,
    BulkheadTimeoutException,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ThreadPoolBulkhead(Bulkhead):
    """
    스레드 풀 기반 Bulkhead.

    독립적인 스레드 풀에서 작업을 실행하여 완전한 격리 제공.
    ContextVar 자동 전파로 요청 ID, 트레이싱 컨텍스트 유지.

    Usage:
        bulkhead = ThreadPoolBulkhead("external_api", max_workers=5)

        # Future 반환
        future = bulkhead.submit(api_call, arg1, arg2)
        result = future.result(timeout=10.0)

        # 또는 동기 실행
        result = bulkhead.execute(api_call, arg1, timeout=10.0)
    """

    def __init__(
        self,
        name: str,
        max_workers: int = 5,
        queue_size: int = 10,
        thread_name_prefix: str | None = None,
    ):
        """
        Args:
            name: Bulkhead 이름
            max_workers: 최대 워커 스레드 수
            queue_size: 대기 큐 크기 (초과 시 거부)
            thread_name_prefix: 스레드 이름 접두사
        """
        self._name = name
        self._max_workers = max_workers
        self._queue_size = queue_size
        self._thread_name_prefix = thread_name_prefix or f"bulkhead_{name}"

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=self._thread_name_prefix,
        )
        self._lock = threading.Lock()

        # 통계
        self._active_count = 0
        self._waiting_count = 0
        self._rejected_count = 0
        self._last_rejection_time: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @contextmanager
    def acquire(
        self, timeout: float | None = None
    ) -> Generator[None, None, None]:
        """
        Thread Pool Bulkhead는 submit/execute 사용 권장.
        이 메서드는 호환성을 위해 제공.
        """
        with self._lock:
            if self._active_count >= self._max_workers + self._queue_size:
                self._rejected_count += 1
                self._last_rejection_time = datetime.now(timezone.utc)
                raise BulkheadFullException(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_workers,
                    active_count=self._active_count,
                )
            self._active_count += 1

        try:
            yield
        finally:
            with self._lock:
                self._active_count -= 1

    def try_acquire(self) -> bool:
        """논블로킹 획득 시도."""
        with self._lock:
            if self._active_count >= self._max_workers + self._queue_size:
                return False
            self._active_count += 1
            return True

    def release(self) -> None:
        """리소스 반환."""
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def submit(
        self, fn: Callable[..., T], *args: Any, **kwargs: Any
    ) -> Future[T]:
        """
        작업 비동기 제출 (ContextVar 전파 포함).

        Args:
            fn: 실행할 함수
            *args: 위치 인자
            **kwargs: 키워드 인자

        Returns:
            Future 객체

        Raises:
            BulkheadFullException: 큐가 가득 찬 경우

        Note:
            contextvars.copy_context()를 사용하여 다음 ContextVar들이 전파됨:
            - _is_synthetic_request (테스트 메트릭 분리)
            - _is_chaos_request (Chaos 실험 추적)
            - _request_overrides (요청별 설정 오버라이드)
        """
        with self._lock:
            if self._waiting_count >= self._queue_size:
                self._rejected_count += 1
                self._last_rejection_time = datetime.now(timezone.utc)
                raise BulkheadFullException(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_workers,
                    active_count=self._active_count,
                )
            self._waiting_count += 1

        # ⭐ 핵심: 현재 컨텍스트 복사 (ContextVar 전파)
        ctx = contextvars.copy_context()

        def wrapped() -> T:
            with self._lock:
                self._waiting_count -= 1
                self._active_count += 1
            try:
                # ⭐ 복사된 컨텍스트 내에서 실행
                return ctx.run(fn, *args, **kwargs)
            finally:
                with self._lock:
                    self._active_count -= 1

        return self._executor.submit(wrapped)

    def execute(
        self,
        fn: Callable[..., T],
        *args: Any,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> T:
        """
        작업 동기 실행.

        Args:
            fn: 실행할 함수
            *args: 위치 인자
            timeout: 실행 타임아웃 (초)
            **kwargs: 키워드 인자

        Returns:
            함수 실행 결과

        Raises:
            BulkheadFullException: 큐가 가득 찬 경우
            BulkheadTimeoutException: 타임아웃 발생
        """
        future = self.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise BulkheadTimeoutException(
                bulkhead_name=self._name,
                timeout=timeout,
            )

    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        with self._lock:
            return BulkheadState(
                name=self._name,
                bulkhead_type=BulkheadType.THREAD_POOL,
                max_concurrent=self._max_workers,
                active_count=self._active_count,
                waiting_count=self._waiting_count,
                rejected_count=self._rejected_count,
                last_rejection_time=self._last_rejection_time,
            )

    def shutdown(self, wait: bool = True) -> None:
        """스레드 풀 종료."""
        self._executor.shutdown(wait=wait)
```

#### 4.2.4 BulkheadRegistry

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/registry.py
"""
Bulkhead Registry - 도메인별 Bulkhead 관리.

도메인 프리 설계: ConnectionType 외에도 임의의 도메인 키 지원.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.base import Bulkhead, BulkheadState
from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead

if TYPE_CHECKING:
    from selfhealing.settings.bulkhead import BulkheadSettings

logger = logging.getLogger(__name__)


class BulkheadRegistry:
    """
    도메인별 Bulkhead 레지스트리.

    기존 ConnectionType과 통합되며, 커스텀 도메인도 지원.

    Usage:
        registry = get_bulkhead_registry()

        # ConnectionType으로 조회
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        # 커스텀 도메인으로 조회
        custom_bulkhead = registry.get("my_custom_domain")

        # 사용
        with db_bulkhead.acquire():
            db_operation()
    """

    def __init__(self, settings: BulkheadSettings | None = None):
        """
        Args:
            settings: Bulkhead 설정. None이면 기본값 사용.
        """
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        self._settings = settings or get_bulkhead_settings()
        self._bulkheads: dict[str, Bulkhead] = {}
        self._lock = threading.Lock()

        # ConnectionType 기반 기본 Bulkhead 등록
        self._register_default_bulkheads()

    def _register_default_bulkheads(self) -> None:
        """ConnectionType 기반 기본 Bulkhead 등록."""
        defaults = {
            ConnectionType.DATABASE.value: SemaphoreBulkhead(
                name=ConnectionType.DATABASE.value,
                max_concurrent=self._settings.database_max_concurrent,
            ),
            ConnectionType.CACHE.value: SemaphoreBulkhead(
                name=ConnectionType.CACHE.value,
                max_concurrent=self._settings.cache_max_concurrent,
            ),
            ConnectionType.EXTERNAL_API.value: ThreadPoolBulkhead(
                name=ConnectionType.EXTERNAL_API.value,
                max_workers=self._settings.external_api_max_workers,
                queue_size=self._settings.external_api_queue_size,
            ),
            ConnectionType.MESSAGE_QUEUE.value: SemaphoreBulkhead(
                name=ConnectionType.MESSAGE_QUEUE.value,
                max_concurrent=self._settings.message_queue_max_concurrent,
            ),
        }

        for name, bulkhead in defaults.items():
            self._bulkheads[name] = bulkhead
            logger.debug(f"[BulkheadRegistry] Registered default: {name}")

    def get(self, name: str | ConnectionType) -> Bulkhead:
        """
        Bulkhead 조회.

        Args:
            name: 도메인 이름 또는 ConnectionType

        Returns:
            Bulkhead 인스턴스

        Raises:
            KeyError: 등록되지 않은 도메인
        """
        key = name.value if isinstance(name, ConnectionType) else name

        with self._lock:
            if key not in self._bulkheads:
                raise KeyError(f"Bulkhead not found: {key}")
            return self._bulkheads[key]

    def get_or_create(
        self,
        name: str,
        max_concurrent: int = 10,
        bulkhead_type: str = "semaphore",
    ) -> Bulkhead:
        """
        Bulkhead 조회 또는 생성.

        Args:
            name: 도메인 이름
            max_concurrent: 최대 동시 실행 수
            bulkhead_type: "semaphore" 또는 "thread_pool"

        Returns:
            Bulkhead 인스턴스
        """
        with self._lock:
            if name not in self._bulkheads:
                if bulkhead_type == "thread_pool":
                    self._bulkheads[name] = ThreadPoolBulkhead(
                        name=name,
                        max_workers=max_concurrent,
                    )
                else:
                    self._bulkheads[name] = SemaphoreBulkhead(
                        name=name,
                        max_concurrent=max_concurrent,
                    )
                logger.info(f"[BulkheadRegistry] Created: {name} ({bulkhead_type})")

            return self._bulkheads[name]

    def register(self, bulkhead: Bulkhead) -> None:
        """
        커스텀 Bulkhead 등록.

        Args:
            bulkhead: 등록할 Bulkhead
        """
        with self._lock:
            self._bulkheads[bulkhead.name] = bulkhead
            logger.info(f"[BulkheadRegistry] Registered: {bulkhead.name}")

    def unregister(self, name: str) -> bool:
        """
        Bulkhead 등록 해제.

        Args:
            name: 도메인 이름

        Returns:
            True면 해제 성공
        """
        with self._lock:
            if name in self._bulkheads:
                del self._bulkheads[name]
                return True
            return False

    def get_all_states(self) -> dict[str, BulkheadState]:
        """모든 Bulkhead 상태 반환."""
        with self._lock:
            return {name: bh.get_state() for name, bh in self._bulkheads.items()}

    def list_names(self) -> list[str]:
        """등록된 모든 도메인 이름 반환."""
        with self._lock:
            return list(self._bulkheads.keys())


# =============================================================================
# Singleton
# =============================================================================

_registry: BulkheadRegistry | None = None
_registry_lock = threading.Lock()


def get_bulkhead_registry() -> BulkheadRegistry:
    """BulkheadRegistry 싱글톤 반환."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = BulkheadRegistry()
    return _registry


def reset_bulkhead_registry() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _registry
    with _registry_lock:
        _registry = None
```

#### 4.2.5 BulkheadSettings

```python
# packages/selfhealing-python/src/selfhealing/settings/bulkhead.py
"""
Bulkhead Settings - Pydantic v2.

도메인별 리소스 격리 설정.

Environment Variables:
    SELFHEALING_BULKHEAD_ENABLED=true
    SELFHEALING_BULKHEAD_DATABASE_MAX_CONCURRENT=10
    SELFHEALING_BULKHEAD_CACHE_MAX_CONCURRENT=20
    SELFHEALING_BULKHEAD_EXTERNAL_API_MAX_WORKERS=5
    SELFHEALING_BULKHEAD_MESSAGE_QUEUE_MAX_CONCURRENT=15
"""

import logging
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class BulkheadSettings(BaseSettings):
    """Bulkhead 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BULKHEAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Global
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Bulkhead 활성화 여부",
    )

    # ==========================================================================
    # ConnectionType별 설정
    # ==========================================================================
    database_max_concurrent: int = Field(
        default=10,
        ge=1,
        le=100,
        description="DATABASE 타입 최대 동시 실행 수",
    )

    cache_max_concurrent: int = Field(
        default=20,
        ge=1,
        le=200,
        description="CACHE 타입 최대 동시 실행 수",
    )

    external_api_max_workers: int = Field(
        default=5,
        ge=1,
        le=50,
        description="EXTERNAL_API 타입 스레드 풀 워커 수",
    )

    external_api_queue_size: int = Field(
        default=10,
        ge=0,
        le=100,
        description="EXTERNAL_API 타입 대기 큐 크기",
    )

    message_queue_max_concurrent: int = Field(
        default=15,
        ge=1,
        le=100,
        description="MESSAGE_QUEUE 타입 최대 동시 실행 수",
    )

    # ==========================================================================
    # 기본값 (커스텀 도메인용)
    # ==========================================================================
    default_max_concurrent: int = Field(
        default=10,
        ge=1,
        le=100,
        description="커스텀 도메인 기본 최대 동시 실행 수",
    )

    # ==========================================================================
    # Timeout
    # ==========================================================================
    default_acquire_timeout: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="기본 리소스 획득 타임아웃 (초)",
    )


# =============================================================================
# Singleton
# =============================================================================

_settings: BulkheadSettings | None = None
_settings_lock = threading.Lock()


def get_bulkhead_settings() -> BulkheadSettings:
    """BulkheadSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = BulkheadSettings()
    return _settings


def reset_bulkhead_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
```

#### 4.2.6 Exceptions

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/exceptions.py
"""
Bulkhead Exceptions.
"""


class BulkheadException(Exception):
    """Bulkhead 기본 예외."""

    pass


class BulkheadFullException(BulkheadException):
    """Bulkhead가 가득 차서 요청 거부."""

    def __init__(
        self,
        bulkhead_name: str,
        max_concurrent: int,
        active_count: int,
    ):
        self.bulkhead_name = bulkhead_name
        self.max_concurrent = max_concurrent
        self.active_count = active_count
        super().__init__(
            f"Bulkhead '{bulkhead_name}' is full: "
            f"{active_count}/{max_concurrent} active"
        )


class BulkheadTimeoutException(BulkheadException):
    """Bulkhead 작업 타임아웃."""

    def __init__(self, bulkhead_name: str, timeout: float):
        self.bulkhead_name = bulkhead_name
        self.timeout = timeout
        super().__init__(
            f"Bulkhead '{bulkhead_name}' timed out after {timeout}s"
        )
```

#### 4.2.7 Decorator

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/decorator.py
"""
Bulkhead Decorator - 동기/비동기 자동 분기.

기존 프로젝트 패턴 준수:
- metrics/decorators.py#L67: asyncio.iscoroutinefunction() 패턴
- utils/jitter.py#L77: sync_wrapper/async_wrapper 분기 패턴
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

T = TypeVar("T")


def bulkhead(
    name: str | ConnectionType,
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Bulkhead 데코레이터 (동기/비동기 자동 분기).

    프로젝트 패턴 준수:
    - asyncio.iscoroutinefunction()으로 자동 분기
    - sync_wrapper / async_wrapper 패턴

    Usage:
        @bulkhead(ConnectionType.DATABASE)
        def db_operation():
            pass

        @bulkhead(ConnectionType.DATABASE)
        async def async_db_operation():
            pass

        @bulkhead("custom_domain", timeout=5.0)
        def custom_operation():
            pass

        @bulkhead("api", fallback=lambda: {"error": "service unavailable"})
        def api_call():
            pass
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullException,
            )

            registry = get_bulkhead_registry()
            key = name.value if isinstance(name, ConnectionType) else name
            bh = registry.get(key)

            try:
                with bh.acquire(timeout=timeout):
                    return fn(*args, **kwargs)
            except BulkheadFullException:
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise

        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullException,
            )

            registry = get_bulkhead_registry()
            key = name.value if isinstance(name, ConnectionType) else name

            # AsyncSemaphoreBulkhead 사용
            bh = registry.get_async(key)

            try:
                async with bh.acquire(timeout=timeout):
                    return await fn(*args, **kwargs)
            except BulkheadFullException:
                if fallback is not None:
                    # fallback도 async일 수 있음
                    if asyncio.iscoroutinefunction(fallback):
                        return await fallback(*args, **kwargs)
                    return fallback(*args, **kwargs)
                raise

        # 핵심: asyncio.iscoroutinefunction()으로 자동 분기
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper  # type: ignore
        return sync_wrapper

    return decorator
```

---

## 5. 기존 코드 통합 가이드

### 5.1 LayeredRepositoryBase 통합

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/memory/layered_repository/base.py`

**변경 전**:
```python
cls._executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="l2_sync"
)
```

**변경 후**:
```python
from selfhealing.resilience.bulkhead import get_bulkhead_registry
from selfhealing.core.connection_health import ConnectionType

# Bulkhead 사용
registry = get_bulkhead_registry()
cache_bulkhead = registry.get(ConnectionType.CACHE)

with cache_bulkhead.acquire(timeout=5.0):
    result = self._l2.get(service_name)
```

### 5.2 ConnectionHealthMonitor 통합

**파일**: `packages/selfhealing-python/src/selfhealing/core/connection_health.py`

Bulkhead 상태를 PartitionState에 포함:

```python
@dataclass
class PartitionState:
    db_available: bool = True
    cache_available: bool = True
    external_apis: dict[str, bool] = field(default_factory=dict)
    detected_at: datetime | None = None

    # Bulkhead 상태 추가
    bulkhead_states: dict[str, BulkheadState] = field(default_factory=dict)
```

---

## 6. Prometheus 메트릭

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/metrics.py

# 메트릭 정의
selfhealing_bulkhead_active_count = Gauge(
    "selfhealing_bulkhead_active_count",
    "현재 활성 요청 수",
    ["bulkhead_name", "bulkhead_type"],
)

selfhealing_bulkhead_max_concurrent = Gauge(
    "selfhealing_bulkhead_max_concurrent",
    "최대 동시 실행 수",
    ["bulkhead_name"],
)

selfhealing_bulkhead_rejected_total = Counter(
    "selfhealing_bulkhead_rejected_total",
    "거부된 요청 총 수",
    ["bulkhead_name"],
)

selfhealing_bulkhead_utilization_percent = Gauge(
    "selfhealing_bulkhead_utilization_percent",
    "Bulkhead 사용률 (%)",
    ["bulkhead_name"],
)
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 | 검증 항목 |
|--------|----------|
| `test_semaphore_bulkhead_basic` | 기본 획득/반환 |
| `test_semaphore_bulkhead_full` | 가득 찬 경우 예외 |
| `test_semaphore_bulkhead_timeout` | 타임아웃 대기 |
| `test_threadpool_bulkhead_submit` | 비동기 제출 |
| `test_threadpool_bulkhead_execute` | 동기 실행 |
| `test_registry_get_default` | ConnectionType 조회 |
| `test_registry_custom_domain` | 커스텀 도메인 등록 |
| `test_decorator_basic` | 데코레이터 동작 |
| `test_decorator_fallback` | Fallback 동작 |

### 7.2 동시성 테스트

```python
def test_concurrent_access():
    """동시 접근 시 격리 검증."""
    bulkhead = SemaphoreBulkhead("test", max_concurrent=5)
    results = []

    def worker():
        try:
            with bulkhead.acquire(timeout=1.0):
                time.sleep(0.5)
                results.append("success")
        except BulkheadFullException:
            results.append("rejected")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5개만 성공, 5개는 거부
    assert results.count("success") == 5
    assert results.count("rejected") == 5
```

---

## 8. 설계 결정 사항

> 리뷰 피드백에 대한 선택 근거

### 8.1 비동기 지원 방식 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. 단일 클래스에 sync/async 혼합 | 복잡도 증가 | ❌ |
| B. **별도 AsyncSemaphoreBulkhead 클래스** | 명확한 분리, 프로젝트 패턴 일치 | ✅ |

**선택 이유**: 프로젝트의 기존 패턴(`metrics/decorators.py`, `utils/jitter.py`)이 `sync_wrapper`/`async_wrapper`를 명확히 분리하고 `asyncio.iscoroutinefunction()`으로 분기하는 방식을 사용

### 8.2 런타임 설정 변경 방식 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. RuntimeConfigManager 직접 폴링 | 강한 결합 | ❌ |
| B. **EventBus CONFIG_UPDATED 구독** | 느슨한 결합, 기존 인프라 활용 | ✅ |

**선택 이유**: `services/event_bus.py#L78`에 `CONFIG_UPDATED` 이벤트가 이미 정의되어 있고, 다른 컴포넌트들도 EventBus를 통해 설정 변경을 감지하는 패턴 사용

### 8.3 격벽 우선순위 방식 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. Priority-aware Registry (복잡한 가중치 계산) | 과도한 복잡도 | ❌ |
| B. **설정 기반 차등 할당** | 단순하고 예측 가능 | ✅ |

**선택 이유**: 기존 `FeaturePriority` + `CascadeLoadShedding`으로 이미 우선순위 기반 트래픽 제어가 가능하며, 격벽 크기는 설정에서 도메인별로 차등 지정하는 것이 더 명확함

### 8.4 OTel 통합 방식 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. OTel 필수 의존성 | 미사용 환경에서 오류 | ❌ |
| B. **선택적 통합 (graceful degradation)** | OTel 비활성화 시에도 정상 동작 | ✅ |

**선택 이유**: `observability/__init__.py`에서 `_is_otel_available()` 함수로 선택적 로딩 패턴을 이미 사용 중이며, `services/circuit_breaker/tracing.py#L617-673`의 `create_otel_span()` 패턴 참조

### 8.5 멀티 DB 키 네이밍 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. `db_default`, `db_replica` | 프로젝트 네이밍 규칙과 불일치 | ❌ |
| B. **`database:default`, `database:replica`** | ConnectionType과 일관성, 파싱 용이 | ✅ |

**선택 이유**: `ConnectionType.DATABASE.value`가 `"database"`이므로 `database:{alias}` 형태가 일관성 있으며, `:`로 구분하면 파싱이 용이함

---

## 9. 참조

### 9.1 코드 근거

| 기존 코드 | 위치 | 관련성 |
|----------|------|--------|
| `ConnectionType` | `core/connection_health.py#L22-27` | 도메인 분류 기준 |
| `LayeredRepositoryBase._executor` | `adapters/memory/layered_repository/base.py#L37-48` | 통합 대상 |
| `AsyncHealingLogger._critical_executor` | `utils/async_logger.py#L173` | 통합 대상 |
| `RateController` | `scaling/rate_controller.py` | 연계 대상 |
| `TrafficGate` | `scaling/traffic_gate.py` | 연계 대상 |

### 9.2 관련 문서

- [180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)
- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)

---

## 10. 보완 구현 가이드

> 리뷰 피드백을 반영한 추가 구현 사항

### 10.1 비동기(Asyncio) 지원 - AsyncSemaphoreBulkhead

**근거**: 프로젝트 전반에서 `asyncio.iscoroutinefunction()` 패턴 사용

- `metrics/decorators.py#L67`: async_wrapper/sync_wrapper 분기
- `utils/jitter.py#L77`: 동일 패턴
- `services/error_budget_gate/gate.py#L809`: 동일 패턴

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/async_semaphore.py
"""
Async Semaphore Bulkhead - asyncio.Semaphore 기반.

I/O 바운드 비동기 작업에 적합.

프로젝트 패턴 준수:
- metrics/decorators.py: async_wrapper 패턴
- utils/jitter.py: asyncio.sleep 사용
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullException

logger = logging.getLogger(__name__)


class AsyncSemaphoreBulkhead:
    """
    비동기 세마포어 기반 Bulkhead.

    asyncio 환경에서 동시 실행 수를 제한하여 리소스 고갈 방지.
    threading.Semaphore와 달리 이벤트 루프를 블로킹하지 않음.

    Usage:
        bulkhead = AsyncSemaphoreBulkhead("database", max_concurrent=10)

        async with bulkhead.acquire(timeout=1.0):
            await db_operation()
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
    ):
        """
        Args:
            name: Bulkhead 이름 (도메인 식별자)
            max_concurrent: 최대 동시 실행 수
        """
        self._name = name
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()

        # 통계
        self._active_count = 0
        self._waiting_count = 0
        self._rejected_count = 0
        self._last_rejection_time: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @asynccontextmanager
    async def acquire(
        self, timeout: float | None = None
    ) -> AsyncGenerator[None, None]:
        """
        비동기 리소스 획득.

        Args:
            timeout: 대기 타임아웃. None이면 즉시 실패 (논블로킹).

        Raises:
            BulkheadFullException: 리소스 획득 실패
        """
        acquired = False
        try:
            async with self._lock:
                self._waiting_count += 1

            # timeout=None이면 즉시 시도 (논블로킹)
            if timeout is None:
                # locked()가 True면 즉시 실패
                if self._semaphore.locked():
                    acquired = False
                else:
                    # 즉시 획득 시도
                    try:
                        await asyncio.wait_for(
                            self._semaphore.acquire(),
                            timeout=0.001,  # 거의 즉시
                        )
                        acquired = True
                    except asyncio.TimeoutError:
                        acquired = False
            else:
                try:
                    await asyncio.wait_for(
                        self._semaphore.acquire(),
                        timeout=timeout,
                    )
                    acquired = True
                except asyncio.TimeoutError:
                    acquired = False

            async with self._lock:
                self._waiting_count -= 1
                if acquired:
                    self._active_count += 1
                else:
                    self._rejected_count += 1
                    self._last_rejection_time = datetime.now(timezone.utc)

            if not acquired:
                raise BulkheadFullException(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_concurrent,
                    active_count=self._active_count,
                )

            yield

        finally:
            if acquired:
                self._semaphore.release()
                async with self._lock:
                    self._active_count -= 1

    async def try_acquire(self) -> bool:
        """논블로킹 획득 시도."""
        if self._semaphore.locked():
            return False
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.001)
            async with self._lock:
                self._active_count += 1
            return True
        except asyncio.TimeoutError:
            return False

    async def release(self) -> None:
        """리소스 반환."""
        self._semaphore.release()
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def get_state(self) -> BulkheadState:
        """현재 상태 반환 (동기)."""
        return BulkheadState(
            name=self._name,
            bulkhead_type=BulkheadType.SEMAPHORE,
            max_concurrent=self._max_concurrent,
            active_count=self._active_count,
            waiting_count=self._waiting_count,
            rejected_count=self._rejected_count,
            last_rejection_time=self._last_rejection_time,
        )
```

---

### 10.2 ContextVar 전파 - ThreadPoolBulkhead 보완

**근거**: 프로젝트에서 ContextVar 광범위 사용

- `core/test_mode_context.py#L41`: `_is_synthetic_request` ContextVar
- `services/http_client.py#L24`: `_is_chaos_request` ContextVar
- `settings/layered_provider.py#L26`: `_request_overrides` ContextVar

**문제**: ThreadPool에서 ContextVar 미전파 시 테스트 메트릭이 운영 메트릭 오염

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/threadpool.py
# submit 메서드 수정

import contextvars

def submit(
    self, fn: Callable[..., T], *args: Any, **kwargs: Any
) -> Future[T]:
    """
    작업 비동기 제출 (ContextVar 전파 포함).

    Args:
        fn: 실행할 함수
        *args: 위치 인자
        **kwargs: 키워드 인자

    Returns:
        Future 객체

    Raises:
        BulkheadFullException: 큐가 가득 찬 경우
    """
    with self._lock:
        if self._waiting_count >= self._queue_size:
            self._rejected_count += 1
            self._last_rejection_time = datetime.now(timezone.utc)
            raise BulkheadFullException(
                bulkhead_name=self._name,
                max_concurrent=self._max_workers,
                active_count=self._active_count,
            )
        self._waiting_count += 1

    # ⭐ 핵심: 현재 컨텍스트 복사
    ctx = contextvars.copy_context()

    def wrapped() -> T:
        with self._lock:
            self._waiting_count -= 1
            self._active_count += 1
        try:
            # ⭐ 복사된 컨텍스트 내에서 실행
            # _is_synthetic_request, _is_chaos_request 등 전파
            return ctx.run(fn, *args, **kwargs)
        finally:
            with self._lock:
                self._active_count -= 1

    return self._executor.submit(wrapped)
```

---

### 10.3 런타임 설정 변경 - EventBus 구독

**근거**: 기존 EventBus 인프라 활용

- `services/event_bus.py#L78`: `CONFIG_UPDATED = "config_updated"` 이벤트 존재
- `services/event_bus.py#L220-240`: `subscribe()` 메서드로 이벤트 구독

**선택 이유**: RuntimeConfigManager 직접 구독 대신 EventBus 사용
- EventBus는 이미 싱글톤으로 구현됨
- 느슨한 결합 유지
- 다른 컴포넌트와 일관된 패턴

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/registry.py
# BulkheadRegistry 클래스 확장

from selfhealing.services.event_bus import (
    EventType,
    SelfHealingEvent,
    get_event_bus,
)

class BulkheadRegistry:
    """
    도메인별 Bulkhead 레지스트리 (런타임 설정 변경 지원).

    CONFIG_UPDATED 이벤트 구독으로 동적 설정 반영.
    """

    def __init__(self, settings: BulkheadSettings | None = None):
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        self._settings = settings or get_bulkhead_settings()
        self._bulkheads: dict[str, Bulkhead] = {}
        self._async_bulkheads: dict[str, AsyncSemaphoreBulkhead] = {}  # ⭐ 비동기용
        self._lock = threading.Lock()

        # ConnectionType 기반 기본 Bulkhead 등록
        self._register_default_bulkheads()

        # ⭐ CONFIG_UPDATED 이벤트 구독
        self._subscribe_config_updates()

    def _subscribe_config_updates(self) -> None:
        """CONFIG_UPDATED 이벤트 구독."""
        try:
            bus = get_event_bus()
            bus.subscribe(
                EventType.CONFIG_UPDATED,
                self._on_config_updated,
            )
            logger.info("[BulkheadRegistry] Subscribed to CONFIG_UPDATED events")
        except Exception as e:
            logger.warning(f"[BulkheadRegistry] Failed to subscribe: {e}")

    def _on_config_updated(self, event: SelfHealingEvent) -> None:
        """
        설정 변경 이벤트 핸들러.

        bulkhead 관련 설정 변경 시 격벽 재생성.
        """
        config_type = event.data.get("config_type", "")

        # bulkhead 설정 변경만 처리
        if "bulkhead" not in config_type:
            return

        logger.info(
            f"[BulkheadRegistry] Config updated: {config_type}, "
            f"reloading bulkheads..."
        )

        # 설정 리로드
        from selfhealing.settings.bulkhead import (
            get_bulkhead_settings,
            reset_bulkhead_settings,
        )

        reset_bulkhead_settings()
        self._settings = get_bulkhead_settings()

        # 기본 Bulkhead 재생성
        # 주의: 진행 중인 요청에 영향 최소화를 위해 점진적 교체
        self._reload_default_bulkheads()

    def _reload_default_bulkheads(self) -> None:
        """기본 Bulkhead 재생성 (점진적 교체)."""
        with self._lock:
            # 새 Bulkhead 생성
            new_bulkheads = {
                ConnectionType.DATABASE.value: SemaphoreBulkhead(
                    name=ConnectionType.DATABASE.value,
                    max_concurrent=self._settings.database_max_concurrent,
                ),
                ConnectionType.CACHE.value: SemaphoreBulkhead(
                    name=ConnectionType.CACHE.value,
                    max_concurrent=self._settings.cache_max_concurrent,
                ),
                # ... 나머지 ConnectionType
            }

            # 교체
            for name, bulkhead in new_bulkheads.items():
                old = self._bulkheads.get(name)
                self._bulkheads[name] = bulkhead
                logger.info(
                    f"[BulkheadRegistry] Reloaded {name}: "
                    f"max_concurrent={bulkhead._max_concurrent}"
                )

    def get_async(self, name: str | ConnectionType) -> AsyncSemaphoreBulkhead:
        """
        비동기 Bulkhead 조회.

        Args:
            name: 도메인 이름 또는 ConnectionType

        Returns:
            AsyncSemaphoreBulkhead 인스턴스
        """
        key = name.value if isinstance(name, ConnectionType) else name

        with self._lock:
            if key not in self._async_bulkheads:
                # 동기 버전의 설정을 기반으로 생성
                sync_bh = self._bulkheads.get(key)
                max_concurrent = (
                    sync_bh._max_concurrent
                    if sync_bh
                    else self._settings.default_max_concurrent
                )
                self._async_bulkheads[key] = AsyncSemaphoreBulkhead(
                    name=key,
                    max_concurrent=max_concurrent,
                )
            return self._async_bulkheads[key]
```

---

### 10.4 Circuit Breaker 연동 정책

**정책**: BulkheadFullException은 CB 실패 카운트에서 **제외**

**근거**: 리소스 부족(Bulkhead)과 서비스 장애(CB)는 다른 문제

- `settings/circuit_breaker.py#L73`: `excluded_exceptions` 필드 존재
- `BulkheadFullException`은 "서비스가 실패한 것"이 아니라 "리소스가 부족한 것"

```python
# settings/circuit_breaker.py에 BulkheadFullException 추가 권장
excluded_exceptions: list[str] = Field(
    default_factory=lambda: [
        "selfhealing.resilience.bulkhead.exceptions.BulkheadFullException",
    ],
    description="Exception types to exclude from failure count",
)
```

**메트릭 분리**:

```
┌─────────────────────────────────────────────────────────────────┐
│                        메트릭 분리 정책                          │
├────────────────────────┬────────────────────────────────────────┤
│  Bulkhead 거부         │  selfhealing_bulkhead_rejected_total   │
│  (리소스 부족)         │  → CB 실패 카운트 제외                 │
├────────────────────────┼────────────────────────────────────────┤
│  서비스 응답 오류      │  selfhealing_cb_failure_total          │
│  (실제 장애)           │  → CB 실패 카운트 포함                 │
└────────────────────────┴────────────────────────────────────────┘
```

---

### 10.5 TrafficGate 통합 파이프라인

**처리 순서**: Bulkhead → LoadShedding → RateController

**근거**: 도메인별 격리를 먼저 체크하여 특정 도메인의 폭주가 전체 Rate Limit 소진 방지

- `scaling/traffic_gate.py#L98-115`: 기존 LoadShedding → RateController 순서

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      TrafficGate 통합 파이프라인                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  요청 ─────► [1. Bulkhead] ─────► [2. LoadShedding] ─────► [3. RateController]
│              (도메인 격리)        (우선순위 필터)         (전역 Rate Limit)  │
│                   │                     │                       │           │
│                   ▼                     ▼                       ▼           │
│              selfhealing_          기존 메트릭              기존 메트릭     │
│              bulkhead_                                                      │
│              rejected_total                                                 │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  이점:                                                                      │
│  - database 폭주 시 → database 격벽만 거부 → cache, external_api 정상       │
│  - 전역 Rate Limit 소진 방지                                                │
│  - 도메인별 병목 지점 명확히 파악 가능                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**TrafficGate 확장**:

```python
# packages/selfhealing-python/src/selfhealing/scaling/traffic_gate.py
# should_allow 메서드 확장

def should_allow(
    self,
    priority: int = 0,
    bulkhead_name: str | None = None,  # ⭐ 신규 파라미터
    metadata: dict[str, Any] | None = None,
) -> TrafficDecision:
    """
    트래픽 허용 여부 결정.

    처리 순서:
    1. Bulkhead (도메인별 격리) - 신규
    2. CascadeLoadShedding (우선순위 필터링)
    3. RateController (전역 Rate Limit)
    """
    current_level = self._rate_controller.get_state().level

    # ⭐ 0단계: Bulkhead 확인 (도메인별 격리)
    if bulkhead_name is not None:
        try:
            from selfhealing.resilience.bulkhead import get_bulkhead_registry
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullException,
            )

            registry = get_bulkhead_registry()
            bulkhead = registry.get(bulkhead_name)

            if not bulkhead.try_acquire():
                return TrafficDecision(
                    allowed=False,
                    reason=f"Bulkhead '{bulkhead_name}' is full",
                    level=current_level,
                    gate="Bulkhead",
                    metadata=metadata,
                )
            # 획득 성공 시 release는 호출자 책임
        except KeyError:
            pass  # 등록되지 않은 bulkhead는 무시
        except Exception as e:
            logger.warning(f"[TrafficGate] Bulkhead error: {e}")

    # 1단계: CascadeLoadShedding 확인 (기존)
    if self._load_shedding is not None:
        # ... 기존 코드 ...

    # 2단계: RateController 확인 (기존)
    # ... 기존 코드 ...
```

---

### 10.6 OpenTelemetry 통합 (선택적)

**근거**: 프로젝트에 OTel 인프라 존재

- `observability/__init__.py#L141`: `get_tracer()` 함수 존재
- `services/circuit_breaker/tracing.py#L617-673`: `create_otel_span()` 패턴

**선택적 구현**: OTel 비활성화 시에도 정상 동작

```python
# packages/selfhealing-python/src/selfhealing/resilience/bulkhead/otel.py
"""
Bulkhead OpenTelemetry Integration (선택적).

OTel이 비활성화된 경우에도 정상 동작.

패턴 참조: services/circuit_breaker/tracing.py#L617-673
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)


def _is_otel_enabled() -> bool:
    """OTEL 활성화 여부 확인."""
    try:
        from selfhealing.observability import is_otel_enabled

        return is_otel_enabled()
    except ImportError:
        return False


@contextmanager
def bulkhead_span(
    bulkhead_name: str,
    max_concurrent: int,
    timeout: float | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Bulkhead 작업에 대한 OTel Span 생성 (선택적).

    OTEL이 비활성화된 경우 빈 컨텍스트만 반환.

    Args:
        bulkhead_name: Bulkhead 이름
        max_concurrent: 최대 동시 실행 수
        timeout: 타임아웃 설정

    Yields:
        span_data: Span에 추가할 데이터 딕셔너리

    Usage:
        with bulkhead_span("database", 10) as span_data:
            # 작업 수행
            span_data["custom_attr"] = "value"
    """
    span_data: dict[str, Any] = {}
    start_time = time.time()
    span = None

    if _is_otel_enabled():
        try:
            from selfhealing.observability import get_tracer

            tracer = get_tracer()
            if tracer is not None:
                span = tracer.start_span(
                    name=f"bulkhead.acquire.{bulkhead_name}",
                    attributes={
                        "bulkhead.name": bulkhead_name,
                        "bulkhead.max_concurrent": max_concurrent,
                        "bulkhead.timeout": timeout or 0,
                    },
                )
        except Exception as e:
            logger.debug(f"[BulkheadOTel] Failed to create span: {e}")

    try:
        yield span_data
    finally:
        wait_time_ms = (time.time() - start_time) * 1000

        if span is not None:
            try:
                span.set_attribute("bulkhead.wait_time_ms", wait_time_ms)
                for key, value in span_data.items():
                    span.set_attribute(f"bulkhead.{key}", value)
                span.end()
            except Exception as e:
                logger.debug(f"[BulkheadOTel] Failed to end span: {e}")
```

**SemaphoreBulkhead에 통합**:

```python
# semaphore.py의 acquire 메서드에 OTel 통합

@contextmanager
def acquire(
    self, timeout: float | None = None
) -> Generator[None, None, None]:
    from selfhealing.resilience.bulkhead.otel import bulkhead_span

    with bulkhead_span(self._name, self._max_concurrent, timeout) as span_data:
        acquired = False
        try:
            # ... 기존 획득 로직 ...
            span_data["acquired"] = acquired
            span_data["active_count"] = self._active_count

            if not acquired:
                span_data["rejected"] = True
                raise BulkheadFullException(...)

            yield
        finally:
            # ... 기존 정리 로직 ...
```

---

### 10.7 상태 대시보드 엔드포인트

**근거**: 기존 `/health/gate` 패턴 존재

- `services/error_budget_gate/gate.py#L655`: 헬스체크 엔드포인트 패턴
- `settings/observability.py#L118`: 헬스체크 경로 목록

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/bulkhead.py
"""
Bulkhead Status API Endpoint.

엔드포인트: /api/self-healing/bulkhead/status/

패턴 참조: services/error_budget_gate/gate.py#L655
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from django.http import JsonResponse
from django.views import View

logger = logging.getLogger(__name__)


class BulkheadStatusView(View):
    """
    Bulkhead 상태 조회 API.

    GET /api/self-healing/bulkhead/status/
    GET /api/self-healing/bulkhead/status/?name=database
    """

    def get(self, request):
        from selfhealing.resilience.bulkhead import get_bulkhead_registry

        registry = get_bulkhead_registry()
        name_filter = request.GET.get("name")

        states = registry.get_all_states()

        if name_filter:
            states = {k: v for k, v in states.items() if k == name_filter}

        response_data = {
            "bulkheads": {
                name: {
                    "type": state.bulkhead_type.value,
                    "max_concurrent": state.max_concurrent,
                    "active_count": state.active_count,
                    "waiting_count": state.waiting_count,
                    "rejected_count": state.rejected_count,
                    "available_permits": state.available_permits,
                    "utilization_percent": round(state.utilization_percent, 2),
                    "last_rejection_time": (
                        state.last_rejection_time.isoformat()
                        if state.last_rejection_time
                        else None
                    ),
                }
                for name, state in states.items()
            },
            "summary": {
                "total_bulkheads": len(states),
                "total_active": sum(s.active_count for s in states.values()),
                "total_rejected": sum(s.rejected_count for s in states.values()),
                "high_utilization": [
                    name
                    for name, s in states.items()
                    if s.utilization_percent > 80
                ],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return JsonResponse(response_data)


# urls.py에 추가
# path("api/self-healing/bulkhead/status/", BulkheadStatusView.as_view()),
```

---

### 10.8 멀티 DB 지원 - database:{alias} 키

**근거**: Django에서 여러 DB alias 사용 일반적

- `services/health_check.py#L183-196`: `check_all_databases()`에서 `connections` 순회

```python
# BulkheadRegistry에 DB alias별 조회 메서드 추가

def get_for_database(self, alias: str = "default") -> Bulkhead:
    """
    DB alias별 Bulkhead 반환.

    Args:
        alias: Django DB alias (default, replica, analytics 등)

    Returns:
        해당 alias의 Bulkhead

    Usage:
        registry = get_bulkhead_registry()

        # default DB
        with registry.get_for_database("default").acquire():
            Model.objects.using("default").all()

        # replica DB
        with registry.get_for_database("replica").acquire():
            Model.objects.using("replica").all()
    """
    key = f"database:{alias}"
    return self.get_or_create(
        name=key,
        max_concurrent=self._settings.database_max_concurrent,
        bulkhead_type="semaphore",
    )


def get_for_cache(self, name: str = "default") -> Bulkhead:
    """
    캐시 인스턴스별 Bulkhead 반환.

    Args:
        name: 캐시 이름 (default, session 등)

    Returns:
        해당 캐시의 Bulkhead
    """
    key = f"cache:{name}"
    return self.get_or_create(
        name=key,
        max_concurrent=self._settings.cache_max_concurrent,
        bulkhead_type="semaphore",
    )
```

**BulkheadSettings 확장**:

```python
# settings/bulkhead.py

class BulkheadSettings(BaseSettings):
    # ... 기존 설정 ...

    # ==========================================================================
    # 멀티 인스턴스 지원
    # ==========================================================================
    database_aliases: dict[str, int] = Field(
        default_factory=lambda: {
            "default": 10,
            "replica": 15,  # replica는 읽기 전용이므로 더 많이 허용
        },
        description="DB alias별 max_concurrent 설정",
    )

    cache_instances: dict[str, int] = Field(
        default_factory=lambda: {
            "default": 20,
            "session": 10,
        },
        description="캐시 인스턴스별 max_concurrent 설정",
    )
```

---

## 11. 구현 체크리스트 (확장)

### 11.1 기본 구현

- [x] `resilience/bulkhead/base.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/semaphore.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/threadpool.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/registry.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/decorator.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/exceptions.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/metrics.py` 작성 ✅ 2026-02-05
- [x] `settings/bulkhead.py` 작성 ✅ 2026-02-05
- [x] `resilience/bulkhead/__init__.py` 작성 ✅ 2026-02-05

### 11.2 보완 구현 (10장)

- [x] `resilience/bulkhead/async_semaphore.py` 작성 (10.1) ✅ 2026-02-05
- [x] `threadpool.py`에 `contextvars.copy_context()` 추가 (10.2) ✅ 2026-02-05
- [x] `registry.py`에 EventBus 구독 추가 (10.3) ✅ 2026-02-05
- [x] `settings/circuit_breaker.py`에 BulkheadFullException 제외 추가 (10.4) ✅ 2026-02-05
- [x] `scaling/traffic_gate.py` 확장 (10.5) ✅ 2026-02-05
- [x] `resilience/bulkhead/otel.py` 작성 (10.6) ✅ 2026-02-05
- [x] `api/django/views/bulkhead.py` 작성 (10.7) ✅ 2026-02-05
- [x] `registry.py`에 `get_for_database()` 추가 (10.8) ✅ 2026-02-05
- [x] `decorator.py`에 async 분기 추가 (4.2.7) ✅ 2026-02-05

### 11.3 테스트

- [x] 단위 테스트 작성 ✅ 2026-02-05 (108개 테스트)
- [x] 동시성 테스트 작성 ✅ 2026-02-05
- [x] 비동기 테스트 작성 (`pytest.mark.asyncio`) ✅ 2026-02-05
- [x] ContextVar 전파 테스트 ✅ 2026-02-05

### 11.4 통합

- [x] LayeredRepositoryBase 통합 ✅ 2026-02-05
- [x] ConnectionHealthMonitor 통합 ✅ 2026-02-05
- [x] TrafficGate 통합 ✅ 2026-02-05
- [x] 문서 업데이트 ✅ 2026-02-05
