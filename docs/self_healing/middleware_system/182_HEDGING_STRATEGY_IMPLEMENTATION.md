# 182. Hedging 전략 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-05
> **의존성**: [181_BULKHEAD_PATTERN_IMPLEMENTATION.md](181_BULKHEAD_PATTERN_IMPLEMENTATION.md)
> **예상 소요**: 2-4일
> **예상 코드량**: ~600줄

---

## 0. 문서 목적

이 문서는 **Hedging(헷징) 전략** 구현 가이드입니다.

**핵심 목표**:
- "동일 요청을 여러 인스턴스에 동시 전송하여 가장 빠른 응답 사용"
- "Tail Latency(p99) 감소"
- "기존 `FallbackStrategy`와 통합"

---

## 1. Hedging 전략이란?

### 1.1 개념

**Hedging(헷징)**은 금융에서 유래한 용어로, 리스크를 분산시키는 전략입니다.

소프트웨어에서는 **동일한 요청을 여러 경로/인스턴스에 동시 전송**하고, **가장 먼저 응답하는 결과를 사용**합니다.

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ 동일 요청 동시 전송
       ├─────────────────────────────────────┐
       │                                     │
       ▼                                     ▼
┌─────────────────┐                 ┌─────────────────┐
│  Instance A     │                 │  Instance B     │
│  (2000ms 소요)  │                 │  (200ms 소요)   │
└────────┬────────┘                 └────────┬────────┘
         │                                   │
         │ 늦은 응답 (무시)                  │ 빠른 응답 (채택)
         │                                   │
         └───────────────────┬───────────────┘
                             │
                             ▼
                    ┌─────────────┐
                    │   Result    │
                    │  (200ms)    │
                    └─────────────┘
```

### 1.2 Hedging vs Retry vs Fallback

| 패턴 | 동작 | 시점 | 리소스 사용 |
|------|------|------|------------|
| **Retry** | 실패 후 재시도 | 순차적 | 1x |
| **Fallback** | 실패 시 대체 경로 | 순차적 | 1x |
| **Hedging** | 동시 요청, 빠른 응답 채택 | 병렬 | 2x ~ Nx |

### 1.3 적합한 사용 사례

| 적합 | 부적합 |
|------|--------|
| 읽기 전용 조회 | 쓰기 작업 (데이터 중복 위험) |
| 멱등(Idempotent) 작업 | 비멱등 작업 |
| Tail Latency가 높은 서비스 | 리소스 비용 민감 환경 |
| 여러 리전/인스턴스 존재 | 단일 인스턴스 환경 |

---

## 2. 현재 상황 분석 (코드 근거)

### 2.1 기존 FallbackMode - 순차 시도만 지원

**파일**: `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`
**라인**: 26-34

```python
class FallbackMode(str, Enum):
    """Fallback behavior modes"""

    FAIL_FAST = "fail_fast"      # 즉시 실패
    USE_CACHE = "use_cache"      # 캐시된 값 사용
    USE_DEFAULT = "use_default"  # 기본값 사용
    DEGRADE_GRACEFULLY = "degrade"  # 기능 축소
    RETRY_ALTERNATIVE = "retry_alt"  # 대체 경로 시도
```

**한계점**:
- `RETRY_ALTERNATIVE`는 **순차적** 대체 경로 시도
- **병렬 실행** 없음

### 2.2 기존 SimpleFallback - 순차 실행

**파일**: `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`
**라인**: 71-99

```python
class SimpleFallback(FallbackStrategy):
    """Simple fallback that tries fallback_fn then default_value."""

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        try:
            result = primary_fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            logger.warning(f"Primary function failed: {e}")

            # Try explicit fallback
            if fallback_fn:
                try:
                    result = fallback_fn()
                    return FallbackResult(
                        value=result,
                        used_fallback=True,
                        fallback_mode=FallbackMode.RETRY_ALTERNATIVE,
                        original_error=str(e),
                    )
                except Exception as fallback_e:
                    logger.warning(f"Fallback function also failed: {fallback_e}")
```

**한계점**:
- `primary_fn` 실패 → `fallback_fn` 시도 (순차)
- **동시 실행 불가**

### 2.3 기존 PartitionAwareFallback - 상태 기반 순차

**파일**: `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`
**라인**: 115-210

```python
class PartitionAwareFallback(FallbackStrategy):
    """
    Fallback strategy aware of partition state.
    Automatically selects fallback based on which connections are available.
    """

    def _handle_failure(
        self,
        error: Exception,
        fallback_fn: Callable[[], T] | None,
        default_value: T | None,
    ) -> FallbackResult[T]:
        # 1. 명시적 fallback 함수가 있으면 시도
        if fallback_fn:
            try:
                result = fallback_fn()
                # ...
```

**한계점**:
- 파티션 상태에 따른 **순차적** Fallback 선택
- **병렬 시도 없음**

### 2.4 ThreadPoolExecutor 존재 - 병렬 실행 가능

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/memory/layered_repository/base.py`
**라인**: 37-48

```python
from concurrent.futures import ThreadPoolExecutor

class LayeredRepositoryBase:
    _executor: ThreadPoolExecutor | None = None

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="l2_sync"
            )
        return cls._executor
```

**활용 가능**:
- `ThreadPoolExecutor`로 **병렬 실행** 인프라 존재
- `concurrent.futures`의 `as_completed` 활용 가능

### 2.5 누락된 기능 요약

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| 병렬 요청 실행 | ❌ | HedgedExecution |
| 첫 응답 채택 | ❌ | FirstResponseSelector |
| 지연 Hedging | ❌ | Delayed Hedge Trigger |
| Hedging 메트릭 | ❌ | Prometheus 메트릭 |

---

## 3. 구현 목표

### 3.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              HedgingStrategy                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │  Mode: IMMEDIATE | DELAYED | ADAPTIVE                                   ││
│  │  Candidates: [fn1, fn2, fn3, ...]                                       ││
│  │  Timeout: 5.0s                                                          ││
│  │  Delay: 0.1s (DELAYED 모드)                                             ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                      HedgingExecutor                                    ││
│  │                                                                         ││
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                     ││
│  │   │   Future 1  │  │   Future 2  │  │   Future 3  │                     ││
│  │   │  (fn1 실행) │  │  (fn2 실행) │  │  (fn3 실행) │                     ││
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                     ││
│  │          │                │                │                            ││
│  │          └────────────────┼────────────────┘                            ││
│  │                           │                                             ││
│  │                           ▼                                             ││
│  │                  ┌─────────────────┐                                    ││
│  │                  │  FirstComplete  │                                    ││
│  │                  │    Selector     │                                    ││
│  │                  └────────┬────────┘                                    ││
│  │                           │                                             ││
│  │                           ▼                                             ││
│  │                  ┌─────────────────┐                                    ││
│  │                  │ HedgingResult   │                                    ││
│  │                  │ (value, source) │                                    ││
│  │                  └─────────────────┘                                    ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 핵심 컴포넌트

| 컴포넌트 | 책임 |
|---------|------|
| `HedgingMode` | 헷징 모드 (IMMEDIATE/DELAYED/ADAPTIVE) |
| `HedgingConfig` | 헷징 설정 (타임아웃, 딜레이, 최대 후보 수) |
| `HedgingResult` | 헷징 결과 (값, 소스, 지연시간) |
| `HedgingExecutor` | 병렬 실행 및 첫 응답 선택 |
| `HedgingStrategy` | FallbackStrategy 확장 |
| `@hedged` | 데코레이터 |

---

## 4. 상세 설계

### 4.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── core/
│   ├── fallback_strategy.py     # (기존) FallbackMode에 HEDGE 추가
│   └── hedging/                  # (신규)
│       ├── __init__.py
│       ├── config.py             # HedgingConfig, HedgingMode
│       ├── executor.py           # HedgingExecutor
│       ├── strategy.py           # HedgingStrategy
│       ├── result.py             # HedgingResult
│       ├── decorator.py          # @hedged
│       ├── metrics.py            # HedgingMetrics
│       └── exceptions.py         # HedgingException
├── settings/
│   └── hedging.py                # HedgingSettings (Pydantic)
```

### 4.2 인터페이스 설계

#### 4.2.1 HedgingMode & HedgingConfig

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/config.py
"""
Hedging Configuration.

도메인 프리 설계: 특정 비즈니스 로직에 종속되지 않음.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class HedgingMode(str, Enum):
    """헷징 모드."""

    IMMEDIATE = "immediate"
    """모든 후보를 즉시 동시 실행."""

    DELAYED = "delayed"
    """Primary 응답 지연 시 Secondary 실행."""

    ADAPTIVE = "adaptive"
    """과거 지연시간 기반 동적 결정."""


@dataclass
class HedgingConfig:
    """헷징 설정."""

    mode: HedgingMode = HedgingMode.DELAYED
    """헷징 모드."""

    timeout: float = 5.0
    """전체 타임아웃 (초)."""

    delay: float = 0.1
    """DELAYED 모드에서 Secondary 실행 전 대기 시간 (초)."""

    max_candidates: int = 3
    """최대 동시 실행 후보 수."""

    cancel_on_success: bool = True
    """첫 성공 시 나머지 작업 취소."""

    require_idempotent: bool = True
    """멱등 작업만 허용 (경고 표시용)."""


@dataclass
class HedgingCandidate:
    """헷징 후보."""

    name: str
    """후보 이름 (로깅/메트릭용)."""

    fn: Callable[[], T]
    """실행할 함수."""

    priority: int = 0
    """우선순위 (낮을수록 높은 우선순위)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
```

#### 4.2.2 HedgingResult

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/result.py
"""
Hedging Result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class HedgingResult(Generic[T]):
    """헷징 실행 결과."""

    value: T | None
    """결과 값."""

    success: bool
    """성공 여부."""

    source: str
    """응답을 제공한 후보 이름."""

    latency_ms: float
    """응답 지연시간 (밀리초)."""

    hedged: bool
    """헷징이 발생했는지 여부 (Primary 외 후보 사용)."""

    candidates_tried: int
    """시도된 후보 수."""

    candidates_succeeded: int
    """성공한 후보 수."""

    candidates_failed: int
    """실패한 후보 수."""

    error: str | None = None
    """실패 시 에러 메시지."""

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """결과 생성 시간."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    @property
    def hedging_benefit_ms(self) -> float | None:
        """
        헷징으로 인한 지연시간 개선 (밀리초).

        Primary보다 빠른 Secondary가 사용된 경우에만 의미 있음.
        """
        primary_latency = self.metadata.get("primary_latency_ms")
        if primary_latency and self.hedged:
            return primary_latency - self.latency_ms
        return None
```

#### 4.2.3 HedgingExecutor

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/executor.py
"""
Hedging Executor - 병렬 실행 및 첫 응답 선택.

도메인 프리 설계.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
    CancelledError,
)
from typing import TypeVar

from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.exceptions import (
    HedgingAllFailedException,
    HedgingTimeoutException,
)
from selfhealing.core.hedging.result import HedgingResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


class HedgingExecutor:
    """
    헷징 실행기.

    여러 후보를 병렬로 실행하고 가장 빠른 성공 응답을 반환.

    Usage:
        executor = HedgingExecutor(config)

        candidates = [
            HedgingCandidate("primary", lambda: api_call_region_a()),
            HedgingCandidate("secondary", lambda: api_call_region_b()),
        ]

        result = executor.execute(candidates)
        print(f"Result from {result.source}: {result.value}")
    """

    # 공유 스레드 풀
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def _get_executor(cls, max_workers: int = 10) -> ThreadPoolExecutor:
        """공유 스레드 풀 반환."""
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix="hedging",
                    )
        return cls._executor

    def __init__(self, config: HedgingConfig | None = None):
        """
        Args:
            config: 헷징 설정. None이면 기본값 사용.
        """
        self._config = config or HedgingConfig()

    def execute(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        헷징 실행.

        Args:
            candidates: 실행할 후보 목록

        Returns:
            HedgingResult: 첫 성공 응답

        Raises:
            HedgingAllFailedException: 모든 후보 실패
            HedgingTimeoutException: 타임아웃
        """
        if not candidates:
            raise ValueError("At least one candidate required")

        # 후보 수 제한
        candidates = candidates[: self._config.max_candidates]

        # 모드에 따른 실행
        if self._config.mode == HedgingMode.IMMEDIATE:
            return self._execute_immediate(candidates)
        elif self._config.mode == HedgingMode.DELAYED:
            return self._execute_delayed(candidates)
        else:  # ADAPTIVE
            return self._execute_adaptive(candidates)

    def _execute_immediate(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """IMMEDIATE 모드: 모든 후보 즉시 실행."""
        start_time = time.perf_counter()
        executor = self._get_executor()

        # 모든 후보 동시 제출
        future_to_candidate: dict[Future, HedgingCandidate] = {}
        for candidate in candidates:
            future = executor.submit(candidate.fn)
            future_to_candidate[future] = candidate

        # 첫 성공 응답 대기
        result = self._wait_for_first_success(
            future_to_candidate,
            start_time,
            hedged=(len(candidates) > 1),
        )

        return result

    def _execute_delayed(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """DELAYED 모드: Primary 먼저, 지연 시 Secondary 추가."""
        if len(candidates) < 2:
            return self._execute_immediate(candidates)

        start_time = time.perf_counter()
        executor = self._get_executor()

        # Primary 먼저 실행
        primary = candidates[0]
        primary_future = executor.submit(primary.fn)

        future_to_candidate: dict[Future, HedgingCandidate] = {
            primary_future: primary
        }

        try:
            # Primary 응답 대기 (delay 시간만큼)
            result = primary_future.result(timeout=self._config.delay)
            latency_ms = (time.perf_counter() - start_time) * 1000

            return HedgingResult(
                value=result,
                success=True,
                source=primary.name,
                latency_ms=latency_ms,
                hedged=False,
                candidates_tried=1,
                candidates_succeeded=1,
                candidates_failed=0,
            )

        except Exception:
            # Primary가 delay 내에 응답하지 않음 → Secondary 추가
            for candidate in candidates[1:]:
                future = executor.submit(candidate.fn)
                future_to_candidate[future] = candidate

            return self._wait_for_first_success(
                future_to_candidate,
                start_time,
                hedged=True,
            )

    def _execute_adaptive(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """ADAPTIVE 모드: 과거 지연시간 기반 동적 결정."""
        # TODO: 과거 지연시간 기반 동적 delay 계산
        # 현재는 DELAYED와 동일하게 동작
        return self._execute_delayed(candidates)

    def _wait_for_first_success(
        self,
        future_to_candidate: dict[Future, HedgingCandidate],
        start_time: float,
        hedged: bool,
    ) -> HedgingResult[T]:
        """첫 성공 응답 대기."""
        remaining_timeout = self._config.timeout - (
            time.perf_counter() - start_time
        )

        succeeded = 0
        failed = 0
        errors: list[str] = []
        primary_latency_ms: float | None = None

        for future in as_completed(
            future_to_candidate.keys(),
            timeout=max(0.001, remaining_timeout),
        ):
            candidate = future_to_candidate[future]
            latency_ms = (time.perf_counter() - start_time) * 1000

            # Primary 지연시간 기록
            if candidate == list(future_to_candidate.values())[0]:
                primary_latency_ms = latency_ms

            try:
                result = future.result(timeout=0)
                succeeded += 1

                # 첫 성공 → 나머지 취소
                if self._config.cancel_on_success:
                    for f in future_to_candidate:
                        if f != future and not f.done():
                            f.cancel()

                return HedgingResult(
                    value=result,
                    success=True,
                    source=candidate.name,
                    latency_ms=latency_ms,
                    hedged=hedged and candidate != list(future_to_candidate.values())[0],
                    candidates_tried=len(future_to_candidate),
                    candidates_succeeded=succeeded,
                    candidates_failed=failed,
                    metadata={"primary_latency_ms": primary_latency_ms},
                )

            except CancelledError:
                pass  # 취소된 작업 무시

            except Exception as e:
                failed += 1
                errors.append(f"{candidate.name}: {e}")
                logger.warning(f"[Hedging] {candidate.name} failed: {e}")

        # 모든 후보 실패
        raise HedgingAllFailedException(
            candidates_tried=len(future_to_candidate),
            errors=errors,
        )
```

#### 4.2.4 HedgingStrategy (FallbackStrategy 확장)

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/strategy.py
"""
Hedging Strategy - FallbackStrategy 확장.

기존 FallbackStrategy와 호환되면서 Hedging 기능 추가.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    FallbackStrategy,
)
from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
)
from selfhealing.core.hedging.executor import HedgingExecutor
from selfhealing.core.hedging.exceptions import HedgingException

logger = logging.getLogger(__name__)

T = TypeVar("T")


class HedgingStrategy(FallbackStrategy):
    """
    헷징 전략.

    여러 후보를 병렬로 실행하고 가장 빠른 응답 사용.

    Usage:
        strategy = HedgingStrategy(
            candidates=[
                lambda: fetch_from_region_a(),
                lambda: fetch_from_region_b(),
            ],
            config=HedgingConfig(mode=HedgingMode.DELAYED),
        )

        result = strategy.execute(primary_fn=lambda: fetch_from_region_a())
    """

    def __init__(
        self,
        candidates: list[Callable[[], T]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
    ):
        """
        Args:
            candidates: 후보 함수 목록 (첫 번째가 Primary)
            candidate_names: 후보 이름 목록 (선택)
            config: 헷징 설정
            default_value: 모든 후보 실패 시 기본값
        """
        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._executor = HedgingExecutor(self._config)

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        """
        헷징 실행.

        Args:
            primary_fn: Primary 함수 (candidates의 첫 번째와 동일하거나 별도 지정)
            fallback_fn: Fallback 함수 (candidates에 추가됨)
            default_value: 기본값 (self._default_value 오버라이드)

        Returns:
            FallbackResult
        """
        # 후보 목록 구성
        candidates: list[HedgingCandidate] = []

        # primary_fn을 첫 번째 후보로
        candidates.append(HedgingCandidate(
            name=self._get_name(0, "primary"),
            fn=primary_fn,
            priority=0,
        ))

        # 기존 candidates 추가 (primary_fn과 다른 경우)
        for i, fn in enumerate(self._candidates):
            if fn != primary_fn:
                candidates.append(HedgingCandidate(
                    name=self._get_name(i + 1, f"candidate_{i + 1}"),
                    fn=fn,
                    priority=i + 1,
                ))

        # fallback_fn 추가
        if fallback_fn:
            candidates.append(HedgingCandidate(
                name="fallback",
                fn=fallback_fn,
                priority=len(candidates),
            ))

        # 후보가 1개뿐이면 일반 실행
        if len(candidates) == 1:
            return self._execute_single(candidates[0].fn, default_value)

        # 헷징 실행
        try:
            result = self._executor.execute(candidates)

            return FallbackResult(
                value=result.value,
                used_fallback=result.hedged,
                fallback_mode=FallbackMode.RETRY_ALTERNATIVE if result.hedged else None,
                original_error=None,
            )

        except HedgingException as e:
            logger.warning(f"[HedgingStrategy] All candidates failed: {e}")

            # 기본값 반환
            final_default = default_value or self._default_value
            if final_default is not None:
                return FallbackResult(
                    value=final_default,
                    used_fallback=True,
                    fallback_mode=FallbackMode.USE_DEFAULT,
                    original_error=str(e),
                )

            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(e),
            )

    def _execute_single(
        self, fn: Callable[[], T], default_value: T | None
    ) -> FallbackResult[T]:
        """단일 함수 실행 (헷징 없음)."""
        try:
            result = fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            if default_value is not None:
                return FallbackResult(
                    value=default_value,
                    used_fallback=True,
                    fallback_mode=FallbackMode.USE_DEFAULT,
                    original_error=str(e),
                )
            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(e),
            )

    def _get_name(self, index: int, default: str) -> str:
        """후보 이름 반환."""
        if index < len(self._candidate_names):
            return self._candidate_names[index]
        return default
```

#### 4.2.5 Exceptions

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/exceptions.py
"""
Hedging Exceptions.
"""


class HedgingException(Exception):
    """헷징 기본 예외."""

    pass


class HedgingAllFailedException(HedgingException):
    """모든 후보 실패."""

    def __init__(self, candidates_tried: int, errors: list[str]):
        self.candidates_tried = candidates_tried
        self.errors = errors
        super().__init__(
            f"All {candidates_tried} candidates failed: {errors}"
        )


class HedgingTimeoutException(HedgingException):
    """헷징 타임아웃."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"Hedging timed out after {timeout}s")
```

#### 4.2.6 Decorator

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/decorator.py
"""
Hedging Decorator.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from selfhealing.core.hedging.config import HedgingConfig, HedgingMode
from selfhealing.core.hedging.strategy import HedgingStrategy

T = TypeVar("T")


def hedged(
    *candidates: Callable[..., T],
    mode: HedgingMode = HedgingMode.DELAYED,
    timeout: float = 5.0,
    delay: float = 0.1,
    default: T | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    헷징 데코레이터.

    데코레이션된 함수를 Primary로, candidates를 Secondary로 사용.

    Usage:
        @hedged(fetch_from_region_b, fetch_from_region_c)
        def fetch_from_region_a():
            return api_call_a()

        # 또는 설정 지정
        @hedged(fallback_fn, mode=HedgingMode.IMMEDIATE, timeout=10.0)
        def primary_fn():
            return api_call()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        config = HedgingConfig(
            mode=mode,
            timeout=timeout,
            delay=delay,
        )

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Primary 함수 래핑 (인자 전달)
            def primary() -> T:
                return fn(*args, **kwargs)

            # Candidates 래핑 (인자 없이 호출)
            wrapped_candidates = [
                lambda c=c: c(*args, **kwargs) for c in candidates
            ]

            strategy = HedgingStrategy(
                candidates=wrapped_candidates,
                config=config,
                default_value=default,
            )

            result = strategy.execute(primary_fn=primary)

            if result.value is None and result.fallback_mode == "fail_fast":
                raise RuntimeError(
                    f"Hedging failed: {result.original_error}"
                )

            return result.value

        return wrapper

    return decorator
```

#### 4.2.7 HedgingSettings

```python
# packages/selfhealing-python/src/selfhealing/settings/hedging.py
"""
Hedging Settings - Pydantic v2.

Environment Variables:
    SELFHEALING_HEDGING_ENABLED=true
    SELFHEALING_HEDGING_DEFAULT_MODE=delayed
    SELFHEALING_HEDGING_DEFAULT_TIMEOUT=5.0
    SELFHEALING_HEDGING_DEFAULT_DELAY=0.1
    SELFHEALING_HEDGING_MAX_CANDIDATES=3
"""

import logging
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class HedgingSettings(BaseSettings):
    """헷징 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_HEDGING_",
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
        description="헷징 활성화 여부",
    )

    # ==========================================================================
    # 기본 설정
    # ==========================================================================
    default_mode: str = Field(
        default="delayed",
        description="기본 헷징 모드 (immediate, delayed, adaptive)",
    )

    default_timeout: float = Field(
        default=5.0,
        ge=0.1,
        le=60.0,
        description="기본 타임아웃 (초)",
    )

    default_delay: float = Field(
        default=0.1,
        ge=0.0,
        le=10.0,
        description="DELAYED 모드 기본 대기 시간 (초)",
    )

    max_candidates: int = Field(
        default=3,
        ge=1,
        le=10,
        description="최대 동시 실행 후보 수",
    )

    # ==========================================================================
    # 스레드 풀
    # ==========================================================================
    executor_max_workers: int = Field(
        default=10,
        ge=1,
        le=50,
        description="헷징 스레드 풀 최대 워커 수",
    )


# =============================================================================
# Singleton
# =============================================================================

_settings: HedgingSettings | None = None
_settings_lock = threading.Lock()


def get_hedging_settings() -> HedgingSettings:
    """HedgingSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = HedgingSettings()
    return _settings


def reset_hedging_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
```

---

## 5. 기존 코드 통합 가이드

### 5.1 FallbackMode 확장

**파일**: `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`

**변경**:
```python
class FallbackMode(str, Enum):
    """Fallback behavior modes"""

    FAIL_FAST = "fail_fast"
    USE_CACHE = "use_cache"
    USE_DEFAULT = "use_default"
    DEGRADE_GRACEFULLY = "degrade"
    RETRY_ALTERNATIVE = "retry_alt"
    HEDGE = "hedge"  # 신규: 헷징으로 인한 대체 응답
```

### 5.2 기존 FallbackStrategy와 호환

`HedgingStrategy`는 `FallbackStrategy`를 상속하므로 기존 코드와 호환:

```python
from selfhealing.core.fallback_strategy import FallbackStrategy
from selfhealing.core.hedging import HedgingStrategy

# 기존 인터페이스 유지
strategy: FallbackStrategy = HedgingStrategy(
    candidates=[fn1, fn2, fn3],
)

result = strategy.execute(primary_fn=fn1)
```

---

## 6. Prometheus 메트릭

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/metrics.py

# 메트릭 정의
selfhealing_hedging_total = Counter(
    "selfhealing_hedging_total",
    "총 헷징 실행 수",
    ["mode"],
)

selfhealing_hedging_success_total = Counter(
    "selfhealing_hedging_success_total",
    "헷징 성공 수",
    ["source"],  # primary, secondary, fallback
)

selfhealing_hedging_failed_total = Counter(
    "selfhealing_hedging_failed_total",
    "헷징 실패 수 (모든 후보 실패)",
    [],
)

selfhealing_hedging_latency_seconds = Histogram(
    "selfhealing_hedging_latency_seconds",
    "헷징 응답 지연시간",
    ["source"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

selfhealing_hedging_benefit_seconds = Histogram(
    "selfhealing_hedging_benefit_seconds",
    "헷징으로 인한 지연시간 개선",
    [],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 | 검증 항목 |
|--------|----------|
| `test_hedging_immediate_mode` | 모든 후보 즉시 실행 |
| `test_hedging_delayed_mode` | Primary 먼저, 지연 시 Secondary |
| `test_hedging_first_success` | 첫 성공 응답 반환 |
| `test_hedging_all_failed` | 모든 후보 실패 시 예외 |
| `test_hedging_cancel_on_success` | 성공 시 나머지 취소 |
| `test_hedging_timeout` | 타임아웃 처리 |
| `test_hedging_decorator` | 데코레이터 동작 |
| `test_hedging_with_fallback` | FallbackStrategy 호환 |

### 7.2 지연시간 테스트

```python
def test_hedging_reduces_latency():
    """헷징으로 지연시간 감소 검증."""
    import time

    def slow_fn():
        time.sleep(1.0)
        return "slow"

    def fast_fn():
        time.sleep(0.1)
        return "fast"

    strategy = HedgingStrategy(
        candidates=[slow_fn, fast_fn],
        config=HedgingConfig(mode=HedgingMode.IMMEDIATE),
    )

    start = time.perf_counter()
    result = strategy.execute(primary_fn=slow_fn)
    elapsed = time.perf_counter() - start

    # 빠른 응답 사용, 1초가 아닌 0.1초 내외
    assert result.value == "fast"
    assert elapsed < 0.5
```

---

## 8. 구현 체크리스트

- [ ] `core/hedging/config.py` 작성
- [ ] `core/hedging/result.py` 작성
- [ ] `core/hedging/executor.py` 작성
- [ ] `core/hedging/strategy.py` 작성
- [ ] `core/hedging/decorator.py` 작성
- [ ] `core/hedging/exceptions.py` 작성
- [ ] `core/hedging/metrics.py` 작성
- [ ] `core/hedging/__init__.py` 작성
- [ ] `settings/hedging.py` 작성
- [ ] `core/fallback_strategy.py`에 HEDGE 모드 추가
- [ ] 단위 테스트 작성
- [ ] 지연시간 테스트 작성
- [ ] 문서 업데이트

---

## 9. 주의사항

### 9.1 멱등성(Idempotency) 확인

⚠️ **헷징은 동일 요청을 여러 번 실행합니다.**

```python
# ❌ 위험: 비멱등 작업
@hedged(create_order_region_b)
def create_order_region_a(order_data):
    return api.create_order(order_data)  # 주문이 중복 생성될 수 있음

# ✅ 안전: 멱등 작업 (읽기)
@hedged(get_product_region_b)
def get_product_region_a(product_id):
    return api.get_product(product_id)  # 조회는 여러 번 해도 무해
```

### 9.2 리소스 비용

헷징은 리소스를 2~N배 사용합니다:

| 후보 수 | 리소스 사용 |
|--------|------------|
| 2 | 2x |
| 3 | 3x |

**DELAYED 모드**를 사용하면 Primary가 빠를 때 리소스 절약:

```python
config = HedgingConfig(
    mode=HedgingMode.DELAYED,
    delay=0.1,  # 100ms 내 Primary 응답 시 Secondary 실행 안 함
)
```

---

## 10. 참조

### 10.1 코드 근거

| 기존 코드 | 위치 | 관련성 |
|----------|------|--------|
| `FallbackMode` | `core/fallback_strategy.py#L26-34` | 확장 대상 |
| `SimpleFallback` | `core/fallback_strategy.py#L71-99` | 패턴 참조 |
| `ThreadPoolExecutor` | `adapters/memory/layered_repository/base.py#L37-48` | 인프라 활용 |

### 10.2 관련 문서

- [181_BULKHEAD_PATTERN_IMPLEMENTATION.md](181_BULKHEAD_PATTERN_IMPLEMENTATION.md)
- [180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)
