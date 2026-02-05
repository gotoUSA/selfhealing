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
│       ├── semaphore.py         # SemaphoreBulkhead
│       ├── threadpool.py        # ThreadPoolBulkhead
│       ├── registry.py          # BulkheadRegistry
│       ├── metrics.py           # BulkheadMetrics
│       ├── decorator.py         # @bulkhead 데코레이터
│       └── exceptions.py        # BulkheadFullException
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
"""

from __future__ import annotations

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
        작업 비동기 제출.

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
            # 간단한 큐 크기 체크 (실제로는 ThreadPoolExecutor 내부 큐 사용)
            if self._waiting_count >= self._queue_size:
                self._rejected_count += 1
                self._last_rejection_time = datetime.now(timezone.utc)
                raise BulkheadFullException(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_workers,
                    active_count=self._active_count,
                )
            self._waiting_count += 1

        def wrapped() -> T:
            with self._lock:
                self._waiting_count -= 1
                self._active_count += 1
            try:
                return fn(*args, **kwargs)
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
Bulkhead Decorator.
"""

from __future__ import annotations

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
    Bulkhead 데코레이터.

    Usage:
        @bulkhead(ConnectionType.DATABASE)
        def db_operation():
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
        def wrapper(*args: Any, **kwargs: Any) -> T:
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

        return wrapper

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

## 8. 구현 체크리스트

- [ ] `resilience/bulkhead/base.py` 작성
- [ ] `resilience/bulkhead/semaphore.py` 작성
- [ ] `resilience/bulkhead/threadpool.py` 작성
- [ ] `resilience/bulkhead/registry.py` 작성
- [ ] `resilience/bulkhead/decorator.py` 작성
- [ ] `resilience/bulkhead/exceptions.py` 작성
- [ ] `resilience/bulkhead/metrics.py` 작성
- [ ] `settings/bulkhead.py` 작성
- [ ] `resilience/bulkhead/__init__.py` 작성
- [ ] 단위 테스트 작성
- [ ] 동시성 테스트 작성
- [ ] LayeredRepositoryBase 통합
- [ ] ConnectionHealthMonitor 통합
- [ ] 문서 업데이트

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
