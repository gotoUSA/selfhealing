"""
Bulkhead Pattern - 리소스 격리를 통한 연쇄 장애 방지.

격벽(Bulkhead) 패턴은 선박 설계에서 유래한 패턴으로,
한 컴포넌트의 장애가 다른 컴포넌트로 전파되지 않도록 리소스를 격리합니다.

주요 컴포넌트:
- SemaphoreBulkhead: 세마포어 기반 동시 실행 제한 (I/O 바운드)
- AsyncSemaphoreBulkhead: 비동기 세마포어 기반 격벽
- ThreadPoolBulkhead: 스레드 풀 기반 격리 (CPU 바운드)
- BulkheadRegistry: 도메인별 격벽 관리
- @bulkhead: 동기/비동기 자동 분기 데코레이터

Usage:
    from selfhealing.resilience.bulkhead import (
        SemaphoreBulkhead,
        bulkhead,
        get_bulkhead_registry,
    )
    from selfhealing.core.connection_health import ConnectionType

    # 직접 사용
    bulkhead = SemaphoreBulkhead("my_domain", max_concurrent=10)
    with bulkhead.acquire(timeout=5.0):
        do_work()

    # 레지스트리 사용
    registry = get_bulkhead_registry()
    db_bulkhead = registry.get(ConnectionType.DATABASE)
    with db_bulkhead.acquire():
        db_operation()

    # 데코레이터 사용
    @bulkhead(ConnectionType.DATABASE)
    def db_operation():
        pass

    @bulkhead(ConnectionType.DATABASE)
    async def async_db_operation():
        pass
"""

from selfhealing.resilience.bulkhead.async_semaphore import AsyncSemaphoreBulkhead
from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.decorator import (
    bulkhead,
    bulkhead_for_cache,
    bulkhead_for_database,
)
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadError,
    BulkheadFullError,
    BulkheadTimeoutError,
    # Deprecated aliases
    BulkheadException,
    BulkheadFullException,
    BulkheadTimeoutException,
)
from selfhealing.resilience.bulkhead.metrics import (
    BulkheadMetricsUpdater,
    get_metrics_updater,
    increment_rejected_count,
    start_metrics_updater,
    stop_metrics_updater,
    update_bulkhead_metrics,
)
from selfhealing.resilience.bulkhead.otel import (
    bulkhead_operation_span,
    bulkhead_span,
)
from selfhealing.resilience.bulkhead.policy import (
    AsyncBulkheadPolicy,
    BulkheadPolicy,
    async_bulkhead_policy,
    bulkhead_policy,
)
from selfhealing.resilience.bulkhead.registry import (
    BulkheadRegistry,
    get_bulkhead_registry,
    reset_bulkhead_registry,
)
from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead

__all__ = [
    # Base
    "Bulkhead",
    "BulkheadState",
    "BulkheadType",
    # Implementations
    "SemaphoreBulkhead",
    "AsyncSemaphoreBulkhead",
    "ThreadPoolBulkhead",
    # Registry
    "BulkheadRegistry",
    "get_bulkhead_registry",
    "reset_bulkhead_registry",
    # Exceptions
    "BulkheadError",
    "BulkheadFullError",
    "BulkheadTimeoutError",
    # Deprecated aliases
    "BulkheadException",
    "BulkheadFullException",
    "BulkheadTimeoutException",
    # Decorator
    "bulkhead",
    "bulkhead_for_database",
    "bulkhead_for_cache",
    # Metrics
    "BulkheadMetricsUpdater",
    "get_metrics_updater",
    "start_metrics_updater",
    "stop_metrics_updater",
    "update_bulkhead_metrics",
    "increment_rejected_count",
    # OTel
    "bulkhead_span",
    "bulkhead_operation_span",
    # Policy
    "BulkheadPolicy",
    "AsyncBulkheadPolicy",
    "bulkhead_policy",
    "async_bulkhead_policy",
]
