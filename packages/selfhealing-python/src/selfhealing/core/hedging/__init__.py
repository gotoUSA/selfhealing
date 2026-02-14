"""
Hedging Strategy Module - 병렬 요청으로 Tail Latency 감소.

동일 요청을 여러 인스턴스에 동시 전송하여 가장 빠른 응답을 사용하는
헷징(Hedging) 전략을 제공합니다.

주요 컴포넌트:
    - HedgingMode: 헷징 모드 (IMMEDIATE, DELAYED, ADAPTIVE)
    - HedgingConfig: 헷징 설정
    - HedgingCandidate: 헷징 후보
    - HedgingResult: 헷징 결과
    - HedgingExecutor: 동기 헷징 실행기
    - AsyncHedgingExecutor: 비동기 헷징 실행기
    - HedgingStrategy: 동기 헷징 전략 (FallbackStrategy 확장)
    - AsyncHedgingStrategy: 비동기 헷징 전략
    - @hedged: 헷징 데코레이터

Usage:
    # 방법 1: 데코레이터 사용
    from selfhealing.core.hedging import hedged, HedgingMode

    @hedged(fetch_from_region_b, mode=HedgingMode.DELAYED, delay=0.1)
    def fetch_from_region_a():
        return api_call_a()

    # 방법 2: Strategy 직접 사용
    from selfhealing.core.hedging import HedgingStrategy, HedgingConfig, HedgingMode

    strategy = HedgingStrategy(
        candidates=[fetch_region_a, fetch_region_b],
        config=HedgingConfig(mode=HedgingMode.DELAYED, delay=0.1),
    )
    result = strategy.execute(primary_fn=fetch_region_a)

    # 방법 3: Executor 직접 사용
    from selfhealing.core.hedging import HedgingExecutor, HedgingCandidate

    executor = HedgingExecutor()
    candidates = [
        HedgingCandidate("region_a", fetch_region_a),
        HedgingCandidate("region_b", fetch_region_b),
    ]
    result = executor.execute(candidates)
"""

from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.result import HedgingResult
from selfhealing.core.hedging.exceptions import (
    HedgingAllFailedError,
    HedgingDisabledError,
    HedgingError,
    HedgingTimeoutError,
    NonRetryableHedgingError,
    # Deprecated aliases
    HedgingException,
    HedgingAllFailedException,
    HedgingTimeoutException,
)
from selfhealing.core.hedging.executor import HedgingExecutor
from selfhealing.core.hedging.async_executor import AsyncHedgingExecutor
from selfhealing.core.hedging.strategy import HedgingStrategy, HedgingStrategyCompat
from selfhealing.core.hedging.async_strategy import AsyncHedgingStrategy
from selfhealing.core.hedging.decorator import (
    hedged,
    hedged_async,
    hedged_sync,
)
from selfhealing.core.hedging.latency_tracker import HedgingLatencyTracker
from selfhealing.core.hedging.result_validator import (
    HedgingResultValidator,
    ResultMismatchRecord,
)
from selfhealing.core.hedging.otel import hedging_span, record_hedging_result
from selfhealing.core.hedging.metrics import (
    record_hedging_execution,
    record_hedging_success,
    record_hedging_failure,
    record_hedging_hedged,
    record_hedging_benefit,
    record_hedging_disabled,
)
from selfhealing.resilience.policies.hedging import (
    AsyncHedgingPolicy,
    HedgingConfigUpdateHook,
    HedgingPolicy,
)

__all__ = [
    # Config
    "HedgingMode",
    "HedgingConfig",
    "HedgingCandidate",
    # Result
    "HedgingResult",
    # Exceptions
    "HedgingError",
    "HedgingAllFailedError",
    "HedgingTimeoutError",
    "NonRetryableHedgingError",
    "HedgingDisabledError",
    # Deprecated aliases
    "HedgingException",
    "HedgingAllFailedException",
    "HedgingTimeoutException",
    # Executors
    "HedgingExecutor",
    "AsyncHedgingExecutor",
    # Strategies (deprecated — HedgingPolicy/AsyncHedgingPolicy 사용 권장)
    "HedgingStrategy",
    "HedgingStrategyCompat",
    "AsyncHedgingStrategy",
    # Decorators
    "hedged",
    "hedged_sync",
    "hedged_async",
    # Latency Tracking
    "HedgingLatencyTracker",
    # Validation
    "HedgingResultValidator",
    "ResultMismatchRecord",
    # OTel
    "hedging_span",
    "record_hedging_result",
    # Metrics
    "record_hedging_execution",
    "record_hedging_success",
    "record_hedging_failure",
    "record_hedging_hedged",
    "record_hedging_benefit",
    "record_hedging_disabled",
    # Policies (ResiliencePolicy Protocol 구현)
    "HedgingPolicy",
    "AsyncHedgingPolicy",
    "HedgingConfigUpdateHook",
]
