# 182. Hedging 전략 구현 가이드

> **버전**: 1.1.0
> **작성일**: 2026-02-05
> **최종 수정**: 2026-02-05
> **의존성**: [181_BULKHEAD_PATTERN_IMPLEMENTATION.md](181_BULKHEAD_PATTERN_IMPLEMENTATION.md), [180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)
> **예상 소요**: 3-5일
> **예상 코드량**: ~1,000줄

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
│       ├── executor.py           # HedgingExecutor (동기)
│       ├── async_executor.py     # AsyncHedgingExecutor (비동기)
│       ├── strategy.py           # HedgingStrategy
│       ├── result.py             # HedgingResult
│       ├── decorator.py          # @hedged (동기/비동기 자동 분기)
│       ├── metrics.py            # HedgingMetrics
│       ├── otel.py               # OpenTelemetry 통합
│       ├── latency_tracker.py    # ADAPTIVE 모드용 지연시간 추적
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

    # =========================================================================
    # Bulkhead 연동 (181 문서 통합)
    # 코드 근거: resilience/bulkhead/registry.py - BulkheadRegistry.get()
    # =========================================================================
    bulkhead_name: str | None = None
    """헷징 요청이 사용할 격벽 이름. None이면 격벽 미사용."""

    acquire_bulkhead_per_candidate: bool = False
    """
    True: 각 후보마다 격벽 획득 (도메인별 격리 강화).
    False: 전체 헷징에 대해 1회만 격벽 획득 (기본값, 리소스 효율).
    """

    # =========================================================================
    # Backpressure 연동 (180 문서 통합)
    # 코드 근거: scaling/config.py - BackpressureLevel
    # =========================================================================
    disable_on_load_level: str = "high"
    """
    이 BackpressureLevel 이상이면 헷징 비활성화.
    값: "none", "low", "medium", "high", "critical"
    기본값 "high": HIGH/CRITICAL 레벨에서 헷징 비활성화.
    """

    delay_multiplier_on_medium: float = 2.0
    """MEDIUM 레벨에서 delay 배율. delay * 2.0 = 200ms."""

    delay_multiplier_on_high: float = 5.0
    """HIGH 레벨에서 delay 배율 (비활성화 전 적용). delay * 5.0 = 500ms."""

    # =========================================================================
    # 확정적 에러 처리
    # 코드 근거: services/retry_handler.py#L82-85 - non_retryable_exceptions
    # =========================================================================
    non_retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (
            PermissionError,
            KeyError,
            ValueError,
            # 커스텀 HTTP 에러는 exceptions.py에서 정의
        )
    )
    """이 예외 발생 시 다른 후보 대기 없이 즉시 실패 처리."""

    non_retryable_http_codes: frozenset[int] = field(
        default_factory=lambda: frozenset({400, 401, 403, 404, 405, 410, 422})
    )
    """재시도 불가 HTTP 상태 코드. 403, 404 등."""

    # =========================================================================
    # 결과 정합성 검증 (비동기 백그라운드)
    # 코드 근거: drift_reconciliation.py - DriftReconciler 패턴
    # =========================================================================
    result_validator: Callable[[T, T], bool] | None = None
    """
    두 결과 비교 함수. None이면 검증 비활성화.

    중요: 비동기 검증이므로 첫 응답 속도에 영향 없음.
    불일치 시 로깅만 하고 채택 결과는 변경하지 않음.
    """

    validation_sample_rate: float = 0.1
    """
    검증 샘플링 비율 (0.0~1.0).
    기본값 0.1 = 10% 요청만 검증.
    운영 오버헤드 최소화.
    """

    validation_policy: str = "any_mismatch"
    """
    검증 정책 (3개 이상 후보 시).
    - "any_mismatch": 어떤 불일치든 로깅 (기본값, 단순)
    - "all_same": 모든 결과가 동일해야 정상
    - "majority_wins": 과반수가 동의하면 정상 (Quorum)
    """

    skip_validation_on_high_load: bool = True
    """
    HIGH/CRITICAL 부하 시 검증 생략.
    코드 근거: scaling/config.py - BackpressureLevel
    """

    escalate_on_structure_mismatch: bool = True
    """
    구조적 불일치(type, structure) 시 Meta-Watchdog 에스컨레이션.
    코드 근거: meta/escalation.py - EscalationManager
    """

    persist_critical_mismatches: bool = False
    """
    구조적 불일치는 DiskPersistentBuffer에 저장 (Graceful Shutdown 대응).
    코드 근거: audit/persistence/disk_buffer.py - DiskPersistentBuffer
    """


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

#### 4.2.6 HedgingResult

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

#### 4.2.3 HedgingExecutor (동기 + ContextVar 전파)

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/executor.py
"""
Hedging Executor - 병렬 실행 및 첫 응답 선택.

도메인 프리 설계.

코드 근거:
- resilience/bulkhead/threadpool.py#L169-180: ContextVar 전파 패턴
- services/retry_handler.py#L82-85: non_retryable_exceptions 패턴
"""

from __future__ import annotations

import contextvars  # ContextVar 전파 추가
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
    NonRetryableHedgingError,  # 확정적 에러
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

    def _submit_with_context(
        self, executor: ThreadPoolExecutor, fn: callable
    ) -> Future:
        """
        ContextVar를 유지하면서 스레드 풀에 작업 제출.

        코드 근거: resilience/bulkhead/threadpool.py#L169-180
        - ctx = contextvars.copy_context()
        - ctx.run(fn, *args, **kwargs)
        """
        ctx = contextvars.copy_context()

        def wrapper():
            return ctx.run(fn)

        return executor.submit(wrapper)

    def _is_non_retryable(self, exc: Exception) -> bool:
        """
        재시도 불가 예외인지 확인.

        코드 근거: services/retry_handler.py#L82-85
        - non_retryable_exceptions = (KeyError, ValueError, ...)
        """
        # 타입 기반 체크
        if isinstance(exc, self._config.non_retryable_exceptions):
            return True

        # HTTP 상태 코드 기반 체크 (HTTPError 등)
        status_code = getattr(exc, "status_code", None)
        if status_code and status_code in self._config.non_retryable_http_codes:
            return True

        return False

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

        # 모든 후보 동시 제출 (ContextVar 전파)
        future_to_candidate: dict[Future, HedgingCandidate] = {}
        for candidate in candidates:
            future = self._submit_with_context(executor, candidate.fn)
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

        # Primary 먼저 실행 (ContextVar 전파)
        primary = candidates[0]
        primary_future = self._submit_with_context(executor, primary.fn)

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
            # Primary가 delay 내에 응답하지 않음 → Secondary 추가 (ContextVar 전파)
            for candidate in candidates[1:]:
                future = self._submit_with_context(executor, candidate.fn)
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

                # 확정적 에러 시 즉시 중단
                if self._is_non_retryable(e):
                    logger.warning(
                        f"[Hedging] Non-retryable error: {type(e).__name__}"
                    )
                    # 나머지 모두 취소
                    for f in future_to_candidate:
                        if not f.done():
                            f.cancel()
                    raise NonRetryableHedgingError(
                        f"Non-retryable: {candidate.name}: {e}"
                    ) from e

        # 모든 후보 실패
        raise HedgingAllFailedException(
            candidates_tried=len(future_to_candidate),
            errors=errors,
        )
```

#### 4.2.4 AsyncHedgingExecutor (비동기)

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/async_executor.py
"""
Async Hedging Executor - 비동기 병렬 실행.

코드 근거:
- resilience/bulkhead/async_semaphore.py: AsyncSemaphoreBulkhead 패턴
  - asyncio.Semaphore, asyncio.Lock, asyncio.wait_for 사용
- resilience/bulkhead/decorator.py#L60: asyncio.iscoroutinefunction() 분기

선택 이유:
- ThreadPoolExecutor 대신 asyncio.wait(return_when=FIRST_COMPLETED) 사용
- 네이티브 코루틴 지원으로 GIL 영향 없는 진정한 병렬 처리
- FastAPI/aiohttp 등 async 환경과 자연스러운 통합
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.exceptions import (
    HedgingAllFailedException,
    HedgingTimeoutException,
    NonRetryableHedgingError,
)
from selfhealing.core.hedging.result import HedgingResult
from selfhealing.core.hedging.latency_tracker import HedgingLatencyTracker

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncHedgingExecutor:
    """
    비동기 헷징 실행기.

    코드 근거:
    - resilience/bulkhead/async_semaphore.py#L47-80
    - asyncio.wait(return_when=asyncio.FIRST_COMPLETED) 패턴

    Usage:
        executor = AsyncHedgingExecutor(config)
        result = await executor.execute(candidates)
    """

    def __init__(
        self,
        config: HedgingConfig | None = None,
        latency_tracker: HedgingLatencyTracker | None = None,
    ):
        self._config = config or HedgingConfig()
        self._latency_tracker = latency_tracker

    def _is_non_retryable(self, exc: Exception) -> bool:
        """재시도 불가 예외인지 확인."""
        if isinstance(exc, self._config.non_retryable_exceptions):
            return True
        status_code = getattr(exc, "status_code", None)
        if status_code and status_code in self._config.non_retryable_http_codes:
            return True
        return False

    async def execute(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """
        비동기 헷징 실행.

        코드 근거: resilience/bulkhead/async_semaphore.py
        - asyncio.wait_for() 타임아웃 패턴
        """
        if not candidates:
            raise ValueError("At least one candidate required")

        candidates = candidates[: self._config.max_candidates]

        if self._config.mode == HedgingMode.IMMEDIATE:
            return await self._execute_immediate(candidates)
        elif self._config.mode == HedgingMode.DELAYED:
            return await self._execute_delayed(candidates)
        else:  # ADAPTIVE
            return await self._execute_adaptive(candidates)

    async def _execute_immediate(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """IMMEDIATE 모드: 모든 후보 즉시 실행."""
        start_time = time.perf_counter()

        # 모든 후보를 Task로 생성
        tasks: dict[asyncio.Task, HedgingCandidate] = {}
        for candidate in candidates:
            # async 함수인 경우 직접 호출, 그렇지 않으면 to_thread 사용
            if asyncio.iscoroutinefunction(candidate.fn):
                coro = candidate.fn()
            else:
                coro = asyncio.to_thread(candidate.fn)
            task = asyncio.create_task(coro)
            tasks[task] = candidate

        return await self._wait_for_first_success(tasks, start_time)

    async def _execute_delayed(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """DELAYED 모드: Primary 먼저, 지연 시 Secondary 추가."""
        if len(candidates) < 2:
            return await self._execute_immediate(candidates)

        start_time = time.perf_counter()
        primary = candidates[0]

        # Primary Task 생성
        if asyncio.iscoroutinefunction(primary.fn):
            primary_coro = primary.fn()
        else:
            primary_coro = asyncio.to_thread(primary.fn)

        primary_task = asyncio.create_task(primary_coro)

        try:
            # Primary가 delay 내에 응답하면 바로 반환
            result = await asyncio.wait_for(
                asyncio.shield(primary_task),
                timeout=self._config.delay,
            )
            latency_ms = (time.perf_counter() - start_time) * 1000

            # ADAPTIVE 모드용 지연시간 기록
            if self._latency_tracker:
                self._latency_tracker.record(latency_ms)

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

        except asyncio.TimeoutError:
            # delay 초과 → Secondary 추가
            tasks: dict[asyncio.Task, HedgingCandidate] = {
                primary_task: primary
            }

            for candidate in candidates[1:]:
                if asyncio.iscoroutinefunction(candidate.fn):
                    coro = candidate.fn()
                else:
                    coro = asyncio.to_thread(candidate.fn)
                task = asyncio.create_task(coro)
                tasks[task] = candidate

            return await self._wait_for_first_success(tasks, start_time, hedged=True)

    async def _execute_adaptive(
        self, candidates: list[HedgingCandidate]
    ) -> HedgingResult[T]:
        """
        ADAPTIVE 모드: P50 기반 동적 delay 계산.

        코드 근거: services/throttle/adaptive.py#L249
        - RTTGradientCalculator의 deque(maxlen=100) 패턴

        구현 선택:
        - P50 기반 delay 계산 (기존 시스템 RTTGradientCalculator와 유사)
        - deque(maxlen=100)으로 슬라이딩 윈도우 유지
        """
        if self._latency_tracker:
            adaptive_delay = self._latency_tracker.get_p50_delay()
            if adaptive_delay:
                # 기존 delay를 P50 기반으로 대체
                original_delay = self._config.delay
                self._config.delay = adaptive_delay
                logger.debug(
                    f"[Hedging] ADAPTIVE delay: {original_delay}s -> {adaptive_delay}s (P50)"
                )

        return await self._execute_delayed(candidates)

    async def _wait_for_first_success(
        self,
        tasks: dict[asyncio.Task, HedgingCandidate],
        start_time: float,
        hedged: bool = False,
    ) -> HedgingResult[T]:
        """
        첫 성공 응답 대기.

        코드 근거: asyncio.wait(return_when=FIRST_COMPLETED)
        """
        succeeded = 0
        failed = 0
        errors: list[str] = []
        pending = set(tasks.keys())
        primary_latency_ms: float | None = None

        remaining_timeout = self._config.timeout - (
            time.perf_counter() - start_time
        )

        while pending and remaining_timeout > 0:
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                candidate = tasks[task]
                latency_ms = (time.perf_counter() - start_time) * 1000

                if candidate == list(tasks.values())[0]:
                    primary_latency_ms = latency_ms

                try:
                    result = task.result()
                    succeeded += 1

                    # ADAPTIVE 모드용 지연시간 기록
                    if self._latency_tracker:
                        self._latency_tracker.record(latency_ms)

                    # 첫 성공 → 나머지 취소
                    if self._config.cancel_on_success:
                        for p in pending:
                            p.cancel()

                    return HedgingResult(
                        value=result,
                        success=True,
                        source=candidate.name,
                        latency_ms=latency_ms,
                        hedged=hedged and candidate != list(tasks.values())[0],
                        candidates_tried=len(tasks),
                        candidates_succeeded=succeeded,
                        candidates_failed=failed,
                        metadata={"primary_latency_ms": primary_latency_ms},
                    )

                except asyncio.CancelledError:
                    pass

                except Exception as e:
                    failed += 1
                    errors.append(f"{candidate.name}: {e}")
                    logger.warning(f"[Hedging] {candidate.name} failed: {e}")

                    # 확정적 에러 시 즉시 중단
                    if self._is_non_retryable(e):
                        for p in pending:
                            p.cancel()
                        raise NonRetryableHedgingError(
                            f"Non-retryable: {candidate.name}: {e}"
                        ) from e

            remaining_timeout = self._config.timeout - (
                time.perf_counter() - start_time
            )

        # 모든 후보 실패 또는 타임아웃
        for p in pending:
            p.cancel()

        raise HedgingAllFailedException(
            candidates_tried=len(tasks),
            errors=errors,
        )
```

#### 4.2.5 HedgingLatencyTracker (ADAPTIVE 모드용)

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/latency_tracker.py
"""
Hedging Latency Tracker - ADAPTIVE 모드용 지연시간 추적.

코드 근거:
- services/throttle/adaptive.py#L249: RTTGradientCalculator
  - self._window = deque(maxlen=self._settings.window_size)
  - 기본 window_size=100

선택 이유:
- RTTGradientCalculator와 동일한 deque(maxlen=100) 패턴 사용
- P50 기반으로 delay 계산 (P95/P99는 너무 보수적)
- 이름: LatencyWindowCalculator 대신 HedgingLatencyTracker (역할 명확)
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque

logger = logging.getLogger(__name__)


class HedgingLatencyTracker:
    """
    헷징 지연시간 추적기.

    ADAPTIVE 모드에서 과거 지연시간을 기반으로 동적 delay를 계산.

    코드 근거:
    - services/throttle/adaptive.py#L249: deque(maxlen=100)
    - P50 기반 계산으로 중간값 활용

    Usage:
        tracker = HedgingLatencyTracker(window_size=100)

        # 지연시간 기록
        tracker.record(150.0)  # 150ms

        # ADAPTIVE delay 계산
        delay = tracker.get_p50_delay()
        if delay:
            config.delay = delay
    """

    def __init__(self, window_size: int = 100, base_delay: float = 0.1):
        """
        Args:
            window_size: 슬라이딩 윈도우 크기 (기본 100, RTTGradientCalculator 참조)
            base_delay: 기본 delay (초), 데이터 부족 시 사용
        """
        self._window: Deque[float] = deque(maxlen=window_size)
        self._base_delay = base_delay
        self._lock = threading.Lock()
        self._min_samples = 10  # 최소 샘플 수

    def record(self, latency_ms: float) -> None:
        """
        지연시간 기록.

        Args:
            latency_ms: 응답 지연시간 (밀리초)
        """
        with self._lock:
            self._window.append(latency_ms)

    def get_p50_delay(self) -> float | None:
        """
        P50 기반 delay 반환 (초).

        Returns:
            P50 기반 delay (초) 또는 None (데이터 부족 시)

        선택 이유:
        - P50(중앙값)은 이상치에 덜 민감
        - P95/P99는 너무 보수적이라 헷징 이점 감소
        - RTTGradientCalculator도 중앙 경향값 활용
        """
        with self._lock:
            if len(self._window) < self._min_samples:
                return None

            sorted_latencies = sorted(self._window)
            p50_index = len(sorted_latencies) // 2
            p50_ms = sorted_latencies[p50_index]

            # 밀리초 → 초 변환, 최소 10ms
            return max(0.01, p50_ms / 1000.0)

    def get_p95_delay(self) -> float | None:
        """P95 기반 delay 반환 (초)."""
        with self._lock:
            if len(self._window) < self._min_samples:
                return None

            sorted_latencies = sorted(self._window)
            p95_index = int(len(sorted_latencies) * 0.95)
            p95_ms = sorted_latencies[min(p95_index, len(sorted_latencies) - 1)]

            return max(0.01, p95_ms / 1000.0)

    def get_stats(self) -> dict:
        """통계 반환."""
        with self._lock:
            if not self._window:
                return {"count": 0}

            sorted_latencies = sorted(self._window)
            return {
                "count": len(self._window),
                "min_ms": sorted_latencies[0],
                "max_ms": sorted_latencies[-1],
                "p50_ms": sorted_latencies[len(sorted_latencies) // 2],
                "p95_ms": sorted_latencies[int(len(sorted_latencies) * 0.95)],
            }

    def clear(self) -> None:
        """윈도우 초기화."""
        with self._lock:
            self._window.clear()
```

#### 4.2.6 HedgingResult

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/strategy.py
"""
Hedging Strategy - FallbackStrategy 확장.

기존 FallbackStrategy와 호환되면서 Hedging 기능 추가.

코드 근거:
- resilience/bulkhead/registry.py#L100-136: BulkheadRegistry, EventBus 구독
- scaling/config.py: BackpressureLevel
- scaling/traffic_gate.py: Bulkhead 연동 패턴
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
from selfhealing.core.hedging.exceptions import (
    HedgingException,
    HedgingDisabledError,
)
from selfhealing.core.hedging.otel import hedging_span

# Bulkhead 연동 (181 문서)
from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

# Backpressure 연동 (180 문서)
from selfhealing.scaling.config import BackpressureLevel

# 동적 설정 변경 (EventBus)
from selfhealing.core.events import EventBus, EventType

logger = logging.getLogger(__name__)

T = TypeVar("T")

# BackpressureLevel 순서 (낮음 → 높음)
_LOAD_LEVEL_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class HedgingStrategy(FallbackStrategy):
    """
    헷징 전략 - Bulkhead/Backpressure 연동 및 동적 설정 변경 지원.

    코드 근거:
    - resilience/bulkhead/registry.py#L100-136: _subscribe_config_updates 패턴
    - scaling/traffic_gate.py: Bulkhead 연동 패턴

    Usage:
        strategy = HedgingStrategy(
            candidates=[
                lambda: fetch_from_region_a(),
                lambda: fetch_from_region_b(),
            ],
            config=HedgingConfig(
                mode=HedgingMode.DELAYED,
                bulkhead_name="api_bulkhead",
                disable_on_load_level="high",
            ),
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

        # Bulkhead 레지스트리 (181 문서)
        self._bulkhead_registry = None
        if self._config.bulkhead_name:
            self._bulkhead_registry = get_bulkhead_registry()

        # 현재 Backpressure 레벨 (180 문서)
        self._current_load_level: str = "none"

        # EventBus 구독 (동적 설정 변경)
        self._subscribe_config_updates()

    def _subscribe_config_updates(self) -> None:
        """
        EventBus CONFIG_UPDATED 구독.

        코드 근거: resilience/bulkhead/registry.py#L100-136
        - _subscribe_config_updates() 메서드
        - EventBus.subscribe(EventType.CONFIG_UPDATED, callback)
        """
        try:
            event_bus = EventBus.get_instance()
            event_bus.subscribe(
                EventType.CONFIG_UPDATED,
                self._on_config_updated,
            )
            logger.debug("[HedgingStrategy] Subscribed to CONFIG_UPDATED")
        except Exception as e:
            logger.warning(f"[HedgingStrategy] Failed to subscribe: {e}")

    def _on_config_updated(self, event_data: dict) -> None:
        """
        설정 변경 이벤트 처리.

        코드 근거: resilience/bulkhead/registry.py#L120-136
        - _on_config_updated() 메서드

        지원 항목:
        - hedging.mode: 헷징 모드 변경
        - hedging.delay: delay 변경
        - hedging.enabled: 활성화/비활성화
        - backpressure.level: Backpressure 레벨 변경
        """
        config_key = event_data.get("key", "")
        config_value = event_data.get("value")

        if config_key == "hedging.mode" and config_value:
            from selfhealing.core.hedging.config import HedgingMode
            try:
                self._config.mode = HedgingMode(config_value)
                logger.info(f"[HedgingStrategy] Mode changed to: {config_value}")
            except ValueError:
                logger.warning(f"[HedgingStrategy] Invalid mode: {config_value}")

        elif config_key == "hedging.delay" and config_value is not None:
            self._config.delay = float(config_value)
            logger.info(f"[HedgingStrategy] Delay changed to: {config_value}")

        elif config_key == "backpressure.level" and config_value:
            self._current_load_level = config_value.lower()
            logger.info(
                f"[HedgingStrategy] Load level updated: {self._current_load_level}"
            )

    def _get_effective_delay(self) -> float:
        """
        현재 부하 레벨에 따른 실제 delay 반환.

        코드 근거: scaling/config.py - BackpressureLevel
        - NONE, LOW, MEDIUM, HIGH, CRITICAL

        로직:
        - NONE/LOW: 기본 delay
        - MEDIUM: delay * delay_multiplier_on_medium
        - HIGH: delay * delay_multiplier_on_high
        """
        if self._current_load_level == "medium":
            return self._config.delay * self._config.delay_multiplier_on_medium
        elif self._current_load_level == "high":
            return self._config.delay * self._config.delay_multiplier_on_high
        return self._config.delay

    def _should_disable_hedging(self) -> bool:
        """
        현재 부하 레벨이 disable_on_load_level 이상인지 확인.

        코드 근거: scaling/config.py - BackpressureLevel 비교
        """
        current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
        disable_order = _LOAD_LEVEL_ORDER.get(
            self._config.disable_on_load_level, 3
        )
        return current_order >= disable_order

    def _acquire_bulkhead(self) -> bool:
        """
        Bulkhead 슬롯 획득.

        코드 근거: resilience/bulkhead/registry.py - BulkheadRegistry.get()
        """
        if not self._bulkhead_registry or not self._config.bulkhead_name:
            return True

        bulkhead = self._bulkhead_registry.get(self._config.bulkhead_name)
        if bulkhead:
            return bulkhead.try_acquire()
        return True

    def _release_bulkhead(self) -> None:
        """Bulkhead 슬롯 해제."""
        if not self._bulkhead_registry or not self._config.bulkhead_name:
            return

        bulkhead = self._bulkhead_registry.get(self._config.bulkhead_name)
        if bulkhead:
            bulkhead.release()

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        """
        헷징 실행 - Bulkhead/Backpressure 연동 포함.

        Args:
            primary_fn: Primary 함수
            fallback_fn: Fallback 함수 (선택)
            default_value: 기본값 (선택)

        Returns:
            FallbackResult
        """
        # Backpressure 체크: 높은 부하 시 헷징 비활성화
        if self._should_disable_hedging():
            logger.warning(
                f"[HedgingStrategy] Hedging disabled due to load: "
                f"{self._current_load_level}"
            )
            # Primary만 실행 (헷징 없이)
            return self._execute_single(primary_fn, default_value)

        # Bulkhead 획득 (전체 헷징에 대해)
        if not self._config.acquire_bulkhead_per_candidate:
            if not self._acquire_bulkhead():
                logger.warning("[HedgingStrategy] Bulkhead full, fallback to single")
                return self._execute_single(primary_fn, default_value)

        try:
            # 실제 delay를 부하 레벨에 따라 조정
            effective_delay = self._get_effective_delay()
            original_delay = self._config.delay
            self._config.delay = effective_delay

            # 후보 목록 구성
            candidates = self._build_candidates(primary_fn, fallback_fn)

            # 후보가 1개뿐이면 일반 실행
            if len(candidates) == 1:
                return self._execute_single(candidates[0].fn, default_value)

            # OTel span으로 감싸서 헷징 실행
            with hedging_span(
                mode=self._config.mode.value,
                candidates_count=len(candidates),
            ) as span:
                result = self._executor.execute(candidates)

                # OTel 속성 추가
                span.set_attribute("hedging.winner", result.source)
                span.set_attribute("hedging.latency_ms", result.latency_ms)
                if result.hedging_benefit_ms:
                    span.set_attribute(
                        "hedging.benefit_ms", result.hedging_benefit_ms
                    )

                return FallbackResult(
                    value=result.value,
                    used_fallback=result.hedged,
                    fallback_mode=FallbackMode.HEDGE if result.hedged else None,
                    original_error=None,
                )

        except HedgingException as e:
            logger.warning(f"[HedgingStrategy] All candidates failed: {e}")

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

        finally:
            # Bulkhead 해제
            if not self._config.acquire_bulkhead_per_candidate:
                self._release_bulkhead()

            # delay 복원
            self._config.delay = original_delay

    def _build_candidates(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None,
    ) -> list[HedgingCandidate]:
        """후보 목록 구성."""
        candidates: list[HedgingCandidate] = []

        candidates.append(HedgingCandidate(
            name=self._get_name(0, "primary"),
            fn=primary_fn,
            priority=0,
        ))

        for i, fn in enumerate(self._candidates):
            if fn != primary_fn:
                candidates.append(HedgingCandidate(
                    name=self._get_name(i + 1, f"candidate_{i + 1}"),
                    fn=fn,
                    priority=i + 1,
                ))

        if fallback_fn:
            candidates.append(HedgingCandidate(
                name="fallback",
                fn=fallback_fn,
                priority=len(candidates),
            ))

        return candidates[: self._config.max_candidates]

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

#### 4.2.7 OTel 트레이싱

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/otel.py
"""
Hedging OpenTelemetry 통합.

코드 근거:
- resilience/bulkhead/otel.py: bulkhead_span() 패턴
- OTel span attributes 표준

선택 이유:
- 기존 bulkhead_span() 패턴과 동일한 인터페이스
- 컨텍스트 매니저로 자동 span 생성/종료
- hedging.* 네임스페이스로 속성 정리

REMARKS_AUDIT 대신 선택한 이유:
- 코드베이스에 REMARKS_AUDIT 패턴 없음
- logger.warning() + OTel span이 기존 패턴
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Status, StatusCode

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

logger = logging.getLogger(__name__)


class _NoOpSpan:
    """OTel 미설치 시 사용하는 더미 Span."""

    def set_attribute(self, key: str, value) -> None:
        pass

    def set_status(self, status) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def hedging_span(
    mode: str,
    candidates_count: int,
    operation_name: str = "hedging.execute",
) -> Iterator[Span | _NoOpSpan]:
    """
    헷징 실행을 위한 OTel span 컨텍스트 매니저.

    코드 근거: resilience/bulkhead/otel.py - bulkhead_span()

    Usage:
        with hedging_span(mode="delayed", candidates_count=3) as span:
            result = executor.execute(candidates)
            span.set_attribute("hedging.winner", result.source)
            span.set_attribute("hedging.benefit_ms", result.hedging_benefit_ms)

    Attributes:
        - hedging.mode: 헷징 모드 (immediate, delayed, adaptive)
        - hedging.candidates_count: 후보 수
        - hedging.winner: 승리한 후보 이름
        - hedging.latency_ms: 응답 지연시간
        - hedging.benefit_ms: 헷징으로 인한 이득 (밀리초)
        - hedging.hedged: 헷징 발생 여부
    """
    if not _HAS_OTEL:
        yield _NoOpSpan()
        return

    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(operation_name) as span:
        # 초기 속성 설정
        span.set_attribute("hedging.mode", mode)
        span.set_attribute("hedging.candidates_count", candidates_count)

        try:
            yield span
            span.set_status(Status(StatusCode.OK))

        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.warning(f"[Hedging] Span error: {e}")
            raise


def record_hedging_result(
    span: Span | _NoOpSpan,
    winner: str,
    latency_ms: float,
    hedged: bool,
    benefit_ms: float | None = None,
) -> None:
    """
    헷징 결과를 span에 기록.

    Args:
        span: OTel span
        winner: 승리한 후보 이름
        latency_ms: 응답 지연시간
        hedged: 헷징 발생 여부
        benefit_ms: 헷징 이득 (밀리초)
    """
    span.set_attribute("hedging.winner", winner)
    span.set_attribute("hedging.latency_ms", latency_ms)
    span.set_attribute("hedging.hedged", hedged)

    if benefit_ms is not None:
        span.set_attribute("hedging.benefit_ms", benefit_ms)
```

#### 4.2.8 Exceptions

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/exceptions.py
"""
Hedging Exceptions.

코드 근거:
- services/retry_handler.py#L82-85: non_retryable_exceptions 패턴
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


class NonRetryableHedgingError(HedgingException):
    """
    재시도 불가 에러 - 즉시 실패 처리.

    코드 근거: services/retry_handler.py#L82-85
    - non_retryable_exceptions = (KeyError, ValueError, ...)
    - 403, 404 등 확정적 HTTP 에러 포함

    사용 시나리오:
    - PermissionError (403): 권한 없음 → 재시도 무의미
    - KeyError/ValueError: 잘못된 요청 → 재시도 무의미
    - HTTP 4xx 에러: 클라이언트 오류 → 재시도 무의미
    """

    def __init__(self, message: str, original_error: Exception | None = None):
        self.original_error = original_error
        super().__init__(message)


class HedgingDisabledError(HedgingException):
    """
    헷징 비활성화 에러.

    Backpressure 레벨이 높아 헷징이 비활성화된 경우.
    """

    def __init__(self, load_level: str):
        self.load_level = load_level
        super().__init__(
            f"Hedging disabled due to high load: {load_level}"
        )
```

#### 4.2.9 Decorator (동기/비동기 자동 분기)

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/decorator.py
"""
Hedging Decorator - 동기/비동기 자동 분기.

코드 근거:
- resilience/bulkhead/decorator.py#L60: asyncio.iscoroutinefunction() 패턴
- services/jitter/decorator.py#L45: async/sync 자동 분기

선택 이유:
- 기존 시스템의 decorator.py들이 동일한 패턴 사용
- 사용자가 sync/async 구분 없이 동일한 데코레이터 사용 가능
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

from selfhealing.core.hedging.config import HedgingConfig, HedgingMode
from selfhealing.core.hedging.strategy import HedgingStrategy
from selfhealing.core.hedging.async_strategy import AsyncHedgingStrategy

T = TypeVar("T")


def hedged(
    *candidates: Callable[..., T],
    mode: HedgingMode = HedgingMode.DELAYED,
    timeout: float = 5.0,
    delay: float = 0.1,
    default: T | None = None,
    bulkhead_name: str | None = None,
    disable_on_load_level: str = "high",
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    헷징 데코레이터 - 동기/비동기 자동 분기.

    코드 근거: resilience/bulkhead/decorator.py#L60
    - asyncio.iscoroutinefunction(fn)으로 분기

    Usage:
        # 동기 함수
        @hedged(fetch_from_region_b, fetch_from_region_c)
        def fetch_from_region_a():
            return api_call_a()

        # 비동기 함수 - 자동 감지
        @hedged(async_fetch_b, async_fetch_c)
        async def async_fetch_a():
            return await async_api_call_a()

        # Bulkhead 연동
        @hedged(fallback_fn, bulkhead_name="api_bulkhead")
        def api_call():
            return api.fetch()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        config = HedgingConfig(
            mode=mode,
            timeout=timeout,
            delay=delay,
            bulkhead_name=bulkhead_name,
            disable_on_load_level=disable_on_load_level,
        )

        # 비동기 함수인 경우
        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                """비동기 래퍼."""
                async def primary() -> T:
                    return await fn(*args, **kwargs)

                wrapped_candidates = [
                    lambda c=c: c(*args, **kwargs) for c in candidates
                ]

                strategy = AsyncHedgingStrategy(
                    candidates=wrapped_candidates,
                    config=config,
                    default_value=default,
                )

                result = await strategy.execute(primary_fn=primary)

                if result.value is None and result.fallback_mode == "fail_fast":
                    raise RuntimeError(
                        f"Hedging failed: {result.original_error}"
                    )

                return result.value

            return async_wrapper

        # 동기 함수인 경우
        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            """동기 래퍼."""
            def primary() -> T:
                return fn(*args, **kwargs)

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

        return sync_wrapper

    return decorator
```

#### 4.2.8 ResultMismatchRecord

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/result_validator.py
"""
Hedging Result Validator - 비동기 결과 정합성 검증.

핵심 원칙:
1. 첫 응답 즉시 반환 (속도 유지)
2. 다른 응답 도착 후 백그라운드 비교
3. 불일치 시 로깅/메트릭만 (채택 결과 변경 안 함)

코드 근거:
- drift_reconciliation.py: DriftReconciliationRecord, 콜백 패턴
- multiregion/replicator.py: source_region 필드
- meta/escalation.py: EscalationManager 패턴
- audit/persistence/disk_buffer.py: DiskPersistentBuffer 패턴
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ResultMismatchRecord:
    """
    결과 불일치 기록.

    코드 근거: drift_reconciliation.py - DriftReconciliationRecord 패턴
    """

    operation_id: str
    """요청 식별자."""

    winner_source: str
    """채택된 결과의 출처 (예: "primary", "secondary")."""

    winner_value_hash: str
    """채택된 결과의 해시 (전체 값 저장 대신)."""

    other_source: str
    """다른 결과의 출처."""

    other_value_hash: str
    """다른 결과의 해시."""

    mismatch_type: Literal["value", "type", "structure"]
    """불일치 유형: value(값 다름), type(타입 다름), structure(구조 다름)."""

    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """감지 시각."""

    latency_diff_ms: float = 0.0
    """두 응답 간 지연시간 차이."""

    # =========================================================================
    # 리전 정보 (코드 근거: multiregion/replicator.py - source_region)
    # =========================================================================
    winner_region: str = ""
    """승리 결과의 리전 (예: ap-northeast-2)."""

    other_region: str = ""
    """다른 결과의 리전 (예: us-east-1)."""

    estimated_replication_lag_ms: float | None = None
    """추정 복제 지연 (latency_diff_ms 기반)."""
```

#### 4.2.9 HedgingResultValidator

```python
# packages/selfhealing-python/src/selfhealing/core/hedging/result_validator.py (계속)
"""
비동기 결과 정합성 검증기.

흐름:
  t=0ms     t=100ms      t=200ms       t=500ms
    │          │            │             │
    │  Primary │            │  Secondary  │
    │  응답    │            │  응답       │
    ▼          ▼            ▼             ▼
  [요청]   [즉시 반환]   [백그라운드]   [비교 완료]
            (속도 유지)    비교 시작      로깅/메트릭

결과: 사용자는 100ms에 응답 받음 (속도 유지)
      불일치는 500ms에 감지 (모니터링)
"""

# Backpressure 연동 (코드 근거: scaling/config.py)
try:
    from selfhealing.scaling.config import BackpressureLevel
    _HAS_BACKPRESSURE = True
except ImportError:
    _HAS_BACKPRESSURE = False

# Meta-Watchdog 에스컨레이션 (코드 근거: meta/escalation.py)
try:
    from selfhealing.meta.escalation import (
        EscalationManager,
        EscalationLevel,
    )
    _HAS_ESCALATION = True
except ImportError:
    _HAS_ESCALATION = False

# DiskPersistentBuffer (코드 근거: audit/persistence/disk_buffer.py)
try:
    from selfhealing.audit.persistence.disk_buffer import get_disk_buffer
    _HAS_DISK_BUFFER = True
except ImportError:
    _HAS_DISK_BUFFER = False


class HedgingResultValidator:
    """
    헷징 결과 비동기 검증기.

    핵심 원칙:
    1. 첫 응답 즉시 반환 (속도 유지)
    2. 다른 응답 도착 후 백그라운드 비교
    3. 불일치 시 로깅/메트릭만 (채택 결과 변경 안 함)

    Usage:
        validator = HedgingResultValidator(
            comparator=lambda a, b: a == b,
            on_mismatch=lambda record: metrics.inc("hedging_mismatch"),
        )

        # 비동기 검증 (즉시 반환, 백그라운드 비교)
        validator.validate_async(
            operation_id="op-123",
            winner_source="primary",
            winner_value=result1,
            winner_latency_ms=100,
            other_results={"secondary": (result2, 200)},
        )
    """

    def __init__(
        self,
        comparator: Callable[[T, T], bool] | None = None,
        on_mismatch: Callable[[ResultMismatchRecord], None] | None = None,
        enabled: bool = True,
        sample_rate: float = 0.1,
        policy: str = "any_mismatch",
        skip_on_high_load: bool = True,
        escalate_on_structure: bool = True,
        persist_critical: bool = False,
        current_load_level_getter: Callable[[], str] | None = None,
    ):
        """
        Args:
            comparator: 두 결과 비교 함수. None이면 == 사용.
            on_mismatch: 불일치 발견 시 콜백.
            enabled: 검증 활성화 여부.
            sample_rate: 샘플링 비율 (0.0~1.0).
            policy: 검증 정책 (any_mismatch, all_same, majority_wins).
            skip_on_high_load: HIGH/CRITICAL 부하 시 검증 생략.
            escalate_on_structure: 구조적 불일치 시 에스컨레이션.
            persist_critical: 구조적 불일치 DiskBuffer 저장.
            current_load_level_getter: 현재 부하 레벨 반환 함수.
        """
        self._comparator = comparator or (lambda a, b: a == b)
        self._on_mismatch = on_mismatch
        self._enabled = enabled
        self._sample_rate = sample_rate
        self._policy = policy
        self._skip_on_high_load = skip_on_high_load
        self._escalate_on_structure = escalate_on_structure
        self._persist_critical = persist_critical
        self._get_load_level = current_load_level_getter

        # 백그라운드 실행용 스레드 풀
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="hedging_validator",
        )

        # 불일치 기록
        self._mismatch_history: list[ResultMismatchRecord] = []
        self._lock = threading.Lock()
        self._max_history = 100

        # DiskBuffer (선택적)
        self._disk_buffer = None
        if persist_critical and _HAS_DISK_BUFFER:
            try:
                self._disk_buffer = get_disk_buffer()
            except Exception as e:
                logger.warning(f"[HedgingValidator] DiskBuffer unavailable: {e}")

        # EscalationManager (선택적)
        self._escalation_mgr = None
        if escalate_on_structure and _HAS_ESCALATION:
            try:
                self._escalation_mgr = EscalationManager()
            except Exception as e:
                logger.warning(f"[HedgingValidator] EscalationManager unavailable: {e}")

    def _should_skip_due_to_load(self) -> bool:
        """
        부하 레벨에 따라 검증 생략 여부 결정.

        코드 근거: scaling/config.py - BackpressureLevel
        - HIGH, CRITICAL 레벨에서는 검증 생략
        """
        if not self._skip_on_high_load:
            return False

        if not self._get_load_level:
            return False

        try:
            level = self._get_load_level().lower()
            return level in ("high", "critical")
        except Exception:
            return False

    def should_validate(self) -> bool:
        """샘플링에 따라 검증 여부 결정."""
        if not self._enabled:
            return False

        # 부하 체크 (코드 근거: scaling/config.py)
        if self._should_skip_due_to_load():
            logger.debug("[HedgingValidator] Skipped due to high load")
            return False

        if self._sample_rate >= 1.0:
            return True

        return random.random() < self._sample_rate

    def validate_async(
        self,
        operation_id: str,
        winner_source: str,
        winner_value: T,
        winner_latency_ms: float,
        other_results: dict[str, tuple[T, float]],
        winner_region: str = "",
        other_regions: dict[str, str] | None = None,
    ) -> None:
        """
        비동기로 결과 비교.

        이 메서드는 즉시 반환되고, 비교는 백그라운드에서 실행됩니다.

        Args:
            operation_id: 요청 식별자
            winner_source: 승리 후보 이름
            winner_value: 승리 값
            winner_latency_ms: 승리 지연시간
            other_results: {source: (value, latency_ms)}
            winner_region: 승리 리전 (선택)
            other_regions: {source: region} 매핑 (선택)
        """
        if not self.should_validate():
            return

        if not other_results:
            return

        # 백그라운드에서 비교
        self._executor.submit(
            self._do_validate,
            operation_id,
            winner_source,
            winner_value,
            winner_latency_ms,
            other_results,
            winner_region,
            other_regions or {},
        )

    def _do_validate(
        self,
        operation_id: str,
        winner_source: str,
        winner_value: T,
        winner_latency_ms: float,
        other_results: dict[str, tuple[T, float]],
        winner_region: str,
        other_regions: dict[str, str],
    ) -> None:
        """실제 비교 수행 (백그라운드)."""
        for other_source, (other_value, other_latency_ms) in other_results.items():
            try:
                is_equal = self._comparator(winner_value, other_value)

                if not is_equal:
                    other_region = other_regions.get(other_source, "")
                    mismatch_type = self._detect_mismatch_type(
                        winner_value, other_value
                    )

                    record = ResultMismatchRecord(
                        operation_id=operation_id,
                        winner_source=winner_source,
                        winner_value_hash=self._hash_value(winner_value),
                        other_source=other_source,
                        other_value_hash=self._hash_value(other_value),
                        mismatch_type=mismatch_type,
                        latency_diff_ms=other_latency_ms - winner_latency_ms,
                        winner_region=winner_region,
                        other_region=other_region,
                        estimated_replication_lag_ms=(
                            abs(other_latency_ms - winner_latency_ms)
                            if winner_region and other_region else None
                        ),
                    )

                    self._record_mismatch(record)

            except Exception as e:
                logger.warning(f"[HedgingValidator] Comparison failed: {e}")

    def _hash_value(self, value: Any) -> str:
        """값의 해시 생성 (로깅용)."""
        try:
            serialized = json.dumps(value, sort_keys=True, default=str)
            return hashlib.md5(serialized.encode()).hexdigest()[:8]
        except Exception:
            return str(hash(str(value)))[:8]

    def _detect_mismatch_type(
        self, a: Any, b: Any
    ) -> Literal["value", "type", "structure"]:
        """불일치 유형 감지."""
        if type(a) != type(b):
            return "type"
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a.keys()) != set(b.keys()):
                return "structure"
        return "value"

    def _record_mismatch(self, record: ResultMismatchRecord) -> None:
        """불일치 기록 저장 및 콜백 실행."""
        # 1. 메모리 기록
        with self._lock:
            self._mismatch_history.append(record)
            if len(self._mismatch_history) > self._max_history:
                self._mismatch_history = self._mismatch_history[-self._max_history:]

        # 2. 경고 로깅
        logger.warning(
            f"[HedgingValidator] Result mismatch detected: "
            f"op={record.operation_id}, "
            f"winner={record.winner_source}({record.winner_region or 'local'}), "
            f"other={record.other_source}({record.other_region or 'local'}), "
            f"type={record.mismatch_type}, "
            f"lag_ms={record.estimated_replication_lag_ms}"
        )

        # 3. 콜백 실행
        if self._on_mismatch:
            try:
                self._on_mismatch(record)
            except Exception as e:
                logger.warning(f"[HedgingValidator] Callback error: {e}")

        # 4. 구조적 불일치 시 에스컨레이션 (코드 근거: meta/escalation.py)
        if record.mismatch_type in ("type", "structure"):
            self._handle_critical_mismatch(record)

    def _handle_critical_mismatch(self, record: ResultMismatchRecord) -> None:
        """
        구조적 불일치 처리.

        코드 근거:
        - meta/escalation.py: EscalationManager.escalate()
        - audit/persistence/disk_buffer.py: DiskPersistentBuffer.put()
        """
        # Meta-Watchdog 에스컨레이션
        if self._escalation_mgr and self._escalate_on_structure:
            try:
                self._escalation_mgr.escalate(
                    level=EscalationLevel.WARNING,
                    component="hedging",
                    message=(
                        f"Result {record.mismatch_type} mismatch: "
                        f"{record.winner_source} vs {record.other_source}"
                    ),
                    details={
                        "operation_id": record.operation_id,
                        "mismatch_type": record.mismatch_type,
                        "winner_region": record.winner_region,
                        "other_region": record.other_region,
                    },
                )
                logger.info(
                    f"[HedgingValidator] Escalated to Meta-Watchdog: "
                    f"{record.operation_id}"
                )
            except Exception as e:
                logger.warning(f"[HedgingValidator] Escalation failed: {e}")

        # DiskPersistentBuffer 저장 (Graceful Shutdown 대응)
        if self._disk_buffer and self._persist_critical:
            try:
                self._disk_buffer.put({
                    "type": "hedging_mismatch",
                    "operation_id": record.operation_id,
                    "mismatch_type": record.mismatch_type,
                    "winner_source": record.winner_source,
                    "other_source": record.other_source,
                    "winner_region": record.winner_region,
                    "other_region": record.other_region,
                    "detected_at": record.detected_at.isoformat(),
                })
                logger.debug(
                    f"[HedgingValidator] Persisted to DiskBuffer: "
                    f"{record.operation_id}"
                )
            except Exception as e:
                logger.warning(f"[HedgingValidator] DiskBuffer write failed: {e}")

    def get_mismatch_stats(self) -> dict:
        """불일치 통계 반환."""
        with self._lock:
            if not self._mismatch_history:
                return {"total": 0}

            by_type: dict[str, int] = {}
            by_region_pair: dict[str, int] = {}

            for record in self._mismatch_history:
                # 유형별 집계
                by_type[record.mismatch_type] = by_type.get(
                    record.mismatch_type, 0
                ) + 1

                # 리전 쌍별 집계
                if record.winner_region and record.other_region:
                    pair = f"{record.winner_region}<->{record.other_region}"
                    by_region_pair[pair] = by_region_pair.get(pair, 0) + 1

            return {
                "total": len(self._mismatch_history),
                "by_type": by_type,
                "by_region_pair": by_region_pair,
                "recent_5": [
                    {
                        "op": r.operation_id,
                        "winner": f"{r.winner_source}({r.winner_region})",
                        "other": f"{r.other_source}({r.other_region})",
                        "type": r.mismatch_type,
                    }
                    for r in self._mismatch_history[-5:]
                ],
            }

    def shutdown(self) -> None:
        """검증기 종료. 대기 중인 작업 완료 후 종료."""
        self._executor.shutdown(wait=True)
```

#### 4.2.10 HedgingSettings

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

### 8.1 핵심 모듈

- [ ] `core/hedging/config.py` 작성
- [ ] `core/hedging/result.py` 작성
- [ ] `core/hedging/executor.py` 작성 (ContextVar 전파 포함)
- [ ] `core/hedging/async_executor.py` 작성
- [ ] `core/hedging/strategy.py` 작성 (Bulkhead/Backpressure 연동)
- [ ] `core/hedging/async_strategy.py` 작성
- [ ] `core/hedging/decorator.py` 작성 (동기/비동기 자동 분기)
- [ ] `core/hedging/exceptions.py` 작성 (NonRetryableHedgingError 포함)
- [ ] `core/hedging/metrics.py` 작성
- [ ] `core/hedging/otel.py` 작성 (hedging_span 포함)
- [ ] `core/hedging/latency_tracker.py` 작성 (ADAPTIVE 모드용)
- [ ] `core/hedging/result_validator.py` 작성 (비동기 결과 정합성 검증)
- [ ] `core/hedging/__init__.py` 작성
- [ ] `settings/hedging.py` 작성

### 8.2 통합

- [ ] `core/fallback_strategy.py`에 HEDGE 모드 추가
- [ ] BulkheadRegistry 연동 (181 문서)
- [ ] BackpressureLevel 연동 (180 문서)
- [ ] EventBus CONFIG_UPDATED 구독 (동적 모드 변경)

### 8.3 테스트

- [ ] 단위 테스트 작성
- [ ] 지연시간 테스트 작성
- [ ] ContextVar 전파 테스트
- [ ] Bulkhead 연동 테스트
- [ ] Backpressure 연동 테스트
- [ ] OTel span 테스트
- [ ] 비동기 헷징 테스트
- [ ] 확정적 에러 처리 테스트
- [ ] 결과 정합성 검증 테스트
- [ ] Backpressure 연동 검증 생략 테스트
- [ ] Meta-Watchdog 에스컨레이션 테스트
- [ ] DiskPersistentBuffer 영속화 테스트

### 8.4 문서

- [ ] API 문서 업데이트
- [ ] 사용 예제 추가

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
| `contextvars.copy_context()` | `resilience/bulkhead/threadpool.py#L169-180` | ContextVar 전파 |
| `asyncio.iscoroutinefunction()` | `resilience/bulkhead/decorator.py#L60` | 동기/비동기 분기 |
| `AsyncSemaphoreBulkhead` | `resilience/bulkhead/async_semaphore.py` | 비동기 패턴 |
| `BulkheadRegistry` | `resilience/bulkhead/registry.py#L100-136` | 격벽 관리, EventBus 구독 |
| `BackpressureLevel` | `scaling/config.py` | 부하 레벨 정의 |
| `RTTGradientCalculator` | `services/throttle/adaptive.py#L249` | deque(maxlen=100) 슬라이딩 윈도우 |
| `non_retryable_exceptions` | `services/retry_handler.py#L82-85` | 확정적 에러 패턴 |
| `bulkhead_span()` | `resilience/bulkhead/otel.py` | OTel span 패턴 |
| `DriftReconciliationRecord` | `adapters/memory/drift_reconciliation.py` | 불일치 기록 패턴 |
| `source_region` | `multiregion/replicator.py` | 리전 정보 필드 |
| `EscalationManager` | `meta/escalation.py` | 에스컨레이션 패턴 |
| `DiskPersistentBuffer` | `audit/persistence/disk_buffer.py` | 영속적 저장 패턴 |
| `QuorumWitness` | `multiregion/quorum.py` | Quorum/다수결 패턴 |

### 10.2 관련 문서

- [181_BULKHEAD_PATTERN_IMPLEMENTATION.md](181_BULKHEAD_PATTERN_IMPLEMENTATION.md)
- [180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)

---

## 11. 선택 이유 요약

### 11.1 구현 선택 사항

| 항목 | 선택 | 대안 | 선택 이유 |
|------|------|------|----------|
| **비동기 실행** | `asyncio.wait(FIRST_COMPLETED)` | `ThreadPoolExecutor` | 네이티브 코루틴 지원, GIL 영향 없음, FastAPI 통합 |
| **ContextVar 전파** | `contextvars.copy_context().run()` | 수동 전파 | 기존 `threadpool.py` 패턴과 동일 |
| **ADAPTIVE delay** | P50 기반 | P95/P99 기반 | P95/P99는 너무 보수적, 헷징 이점 감소 |
| **슬라이딩 윈도우** | `deque(maxlen=100)` | 커스텀 구현 | `RTTGradientCalculator`와 동일 패턴 |
| **부하 임계값 타입** | `disable_on_load_level: str` | `backpressure_threshold` | 180 문서 `BackpressureLevel` enum과 일치 |
| **지연시간 추적기 이름** | `HedgingLatencyTracker` | `LatencyWindowCalculator` | 역할이 명확함 (Hedging 전용) |
| **감사 로깅** | `logger.warning()` + OTel | `REMARKS_AUDIT` | 코드베이스에 `REMARKS_AUDIT` 없음 |
| **동기/비동기 분기** | `asyncio.iscoroutinefunction()` | 별도 데코레이터 | 기존 `decorator.py` 패턴과 동일 |
| **결과 정합성 검증** | 비동기 백그라운드 | 동기 검증 | 속도 유지 + 모니터링 목적 |
| **불일치 기록** | `ResultMismatchRecord` | 커스텀 구조 | `DriftReconciliationRecord` 패턴 복사 |
| **부하 시 검증** | HIGH/CRITICAL에서 생략 | 항상 실행 | 스레드 풀 고갈 방지 |
| **에스컨레이션** | 구조적 불일치만 | 모든 불일치 | 경보 피로 방지 |
| **영속화** | 구조적 불일치만 | 모든 불일치 | 오버헤드 최소화 |

### 11.2 코드 근거 기반 결정

모든 구현 선택은 기존 코드베이스의 패턴을 따릅니다:

1. **ContextVar 전파**: `resilience/bulkhead/threadpool.py#L169-180`에서 동일 패턴 사용 중
2. **EventBus 구독**: `resilience/bulkhead/registry.py#L100-136`의 `_subscribe_config_updates()` 패턴 복사
3. **OTel span**: `resilience/bulkhead/otel.py`의 `bulkhead_span()` 패턴 복사
4. **non_retryable 처리**: `services/retry_handler.py#L82-85`의 예외 튜플 패턴 복사
