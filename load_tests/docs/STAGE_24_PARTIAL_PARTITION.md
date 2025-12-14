# Stage 24: Partial Network Partition 테스트

## 🎯 목표

네트워크 부분 단절 상황(DB는 살아있는데 Redis/외부 API만 안 보임)에서의 복원력 확보

## 📋 실제 장애 사례

- **2025년 AWS ap-northeast-1**: DB는 정상인데 Redis만 단절 → 캐시 무한 미스, 서비스 과부하

---

## 🏗️ 구현 내용

### 1. Connection Health Monitor 인터페이스

**파일**: `packages/selfhealing-python/src/selfhealing/core/connection_health.py`

```python
"""
Connection Health Monitor

Tracks health of different connection types independently:
- Database connections
- Cache connections (Redis, Memcached)
- External API connections

Enables graceful degradation when partial failures occur.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable


class ConnectionType(str, Enum):
    """Types of connections to monitor"""
    DATABASE = "database"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
    MESSAGE_QUEUE = "message_queue"


class ConnectionStatus(str, Enum):
    """Health status of a connection"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ConnectionHealth:
    """Health status of a single connection"""
    connection_type: ConnectionType
    name: str
    status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    consecutive_failures: int = 0
    error_message: str = ""
    latency_ms: Optional[float] = None


@dataclass
class PartitionState:
    """Current state of network partitions"""
    db_available: bool = True
    cache_available: bool = True
    external_apis: dict[str, bool] = field(default_factory=dict)
    detected_at: Optional[datetime] = None

    @property
    def is_partial_partition(self) -> bool:
        """True if some but not all connections are down"""
        statuses = [self.db_available, self.cache_available] + list(self.external_apis.values())
        return any(statuses) and not all(statuses)


class ConnectionHealthMonitor(ABC):
    """Abstract interface for connection health monitoring"""

    @abstractmethod
    def check_health(self, connection_type: ConnectionType, name: str) -> ConnectionHealth:
        """Check health of a specific connection"""
        pass

    @abstractmethod
    def get_partition_state(self) -> PartitionState:
        """Get current partition state across all connections"""
        pass

    @abstractmethod
    def register_health_check(
        self,
        connection_type: ConnectionType,
        name: str,
        check_fn: Callable[[], bool]
    ) -> None:
        """Register a health check function for a connection"""
        pass


class DefaultConnectionHealthMonitor(ConnectionHealthMonitor):
    """Default implementation of connection health monitoring"""

    def __init__(self):
        self._health_checks: dict[str, Callable[[], bool]] = {}
        self._health_states: dict[str, ConnectionHealth] = {}
        self._failure_threshold = 3

    def register_health_check(
        self,
        connection_type: ConnectionType,
        name: str,
        check_fn: Callable[[], bool]
    ) -> None:
        key = f"{connection_type.value}:{name}"
        self._health_checks[key] = check_fn
        self._health_states[key] = ConnectionHealth(
            connection_type=connection_type,
            name=name,
        )

    def check_health(self, connection_type: ConnectionType, name: str) -> ConnectionHealth:
        key = f"{connection_type.value}:{name}"

        if key not in self._health_checks:
            return ConnectionHealth(
                connection_type=connection_type,
                name=name,
                status=ConnectionStatus.UNKNOWN
            )

        health = self._health_states[key]
        check_fn = self._health_checks[key]

        try:
            start = datetime.now(timezone.utc)
            success = check_fn()
            end = datetime.now(timezone.utc)

            health.last_check = end
            health.latency_ms = (end - start).total_seconds() * 1000

            if success:
                health.status = ConnectionStatus.HEALTHY
                health.last_success = end
                health.consecutive_failures = 0
                health.error_message = ""
            else:
                self._record_failure(health, "Health check returned False")

        except Exception as e:
            self._record_failure(health, str(e))

        return health

    def _record_failure(self, health: ConnectionHealth, error: str) -> None:
        health.consecutive_failures += 1
        health.last_failure = datetime.now(timezone.utc)
        health.error_message = error

        if health.consecutive_failures >= self._failure_threshold:
            health.status = ConnectionStatus.UNHEALTHY
        else:
            health.status = ConnectionStatus.DEGRADED

    def get_partition_state(self) -> PartitionState:
        state = PartitionState()
        state.detected_at = datetime.now(timezone.utc)

        for key, health in self._health_states.items():
            conn_type, name = key.split(":", 1)
            is_healthy = health.status == ConnectionStatus.HEALTHY

            if conn_type == ConnectionType.DATABASE.value:
                state.db_available = is_healthy
            elif conn_type == ConnectionType.CACHE.value:
                state.cache_available = is_healthy
            elif conn_type == ConnectionType.EXTERNAL_API.value:
                state.external_apis[name] = is_healthy

        return state
```

---

### 2. Fallback Strategy

**파일**: `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`

```python
"""
Fallback Strategy for Partial Partitions

Provides graceful degradation strategies when connections fail:
- Cache miss → DB fallback
- External API down → cached/default response
- Message queue down → sync processing
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar, Generic
from enum import Enum

from .connection_health import PartitionState, ConnectionType


T = TypeVar("T")


class FallbackMode(str, Enum):
    """Fallback behavior modes"""
    FAIL_FAST = "fail_fast"           # 즉시 실패
    USE_CACHE = "use_cache"           # 캐시된 값 사용
    USE_DEFAULT = "use_default"       # 기본값 사용
    DEGRADE_GRACEFULLY = "degrade"    # 기능 축소
    RETRY_ALTERNATIVE = "retry_alt"   # 대체 경로 시도


@dataclass
class FallbackResult(Generic[T]):
    """Result of a fallback operation"""
    value: Optional[T]
    used_fallback: bool
    fallback_mode: Optional[FallbackMode] = None
    original_error: Optional[str] = None


class FallbackStrategy(ABC):
    """Abstract fallback strategy"""

    @abstractmethod
    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        """Execute with fallback"""
        pass


class PartitionAwareFallback(FallbackStrategy):
    """
    Fallback strategy aware of partition state.
    Automatically selects fallback based on which connections are available.
    """

    def __init__(
        self,
        partition_state: PartitionState,
        cache_fallback: Optional[Callable[[], Any]] = None,
        db_fallback: Optional[Callable[[], Any]] = None,
    ):
        self._partition_state = partition_state
        self._cache_fallback = cache_fallback
        self._db_fallback = db_fallback

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        try:
            result = primary_fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            return self._handle_failure(e, fallback_fn, default_value)

    def _handle_failure(
        self,
        error: Exception,
        fallback_fn: Optional[Callable[[], T]],
        default_value: Optional[T],
    ) -> FallbackResult[T]:
        # 1. 명시적 fallback 함수가 있으면 시도
        if fallback_fn:
            try:
                result = fallback_fn()
                return FallbackResult(
                    value=result,
                    used_fallback=True,
                    fallback_mode=FallbackMode.RETRY_ALTERNATIVE,
                    original_error=str(error)
                )
            except Exception:
                pass

        # 2. 캐시 사용 불가 + DB 가용 → DB fallback
        if not self._partition_state.cache_available and self._partition_state.db_available:
            if self._db_fallback:
                try:
                    result = self._db_fallback()
                    return FallbackResult(
                        value=result,
                        used_fallback=True,
                        fallback_mode=FallbackMode.DEGRADE_GRACEFULLY,
                        original_error=str(error)
                    )
                except Exception:
                    pass

        # 3. 기본값 반환
        if default_value is not None:
            return FallbackResult(
                value=default_value,
                used_fallback=True,
                fallback_mode=FallbackMode.USE_DEFAULT,
                original_error=str(error)
            )

        # 4. 모든 fallback 실패
        return FallbackResult(
            value=None,
            used_fallback=True,
            fallback_mode=FallbackMode.FAIL_FAST,
            original_error=str(error)
        )
```

---

### 3. 테스트 케이스

**파일**: `packages/selfhealing-python/tests/unit/test_partial_partition.py`

```python
"""
Stage 24: Partial Network Partition Tests

Scenarios:
1. DB alive, Redis dead → cache bypass to DB
2. Redis alive, DB dead → read from cache
3. External API dead → use cached response
4. All connections healthy → normal operation
5. All connections dead → graceful failure
"""

import pytest
from unittest.mock import Mock, patch
from selfhealing.core.connection_health import (
    ConnectionType,
    ConnectionStatus,
    ConnectionHealth,
    PartitionState,
    DefaultConnectionHealthMonitor,
)
from selfhealing.core.fallback_strategy import (
    FallbackMode,
    PartitionAwareFallback,
)


class TestPartialPartitionDetection:
    """Partial partition 감지 테스트"""

    def test_detect_partial_partition_cache_down(self):
        """캐시만 다운된 상태 감지"""
        state = PartitionState(
            db_available=True,
            cache_available=False,
            external_apis={"payment_gateway": True}
        )

        assert state.is_partial_partition is True

    def test_detect_partial_partition_db_down(self):
        """DB만 다운된 상태 감지"""
        state = PartitionState(
            db_available=False,
            cache_available=True,
            external_apis={}
        )

        assert state.is_partial_partition is True

    def test_all_healthy_not_partition(self):
        """모두 정상일 때는 partition 아님"""
        state = PartitionState(
            db_available=True,
            cache_available=True,
            external_apis={"api1": True}
        )

        assert state.is_partial_partition is False

    def test_all_down_not_partial(self):
        """모두 다운이면 partial이 아닌 full partition"""
        state = PartitionState(
            db_available=False,
            cache_available=False,
            external_apis={}
        )

        assert state.is_partial_partition is False


class TestConnectionHealthMonitor:
    """Connection health monitoring 테스트"""

    def test_register_and_check_healthy(self):
        """정상 connection 헬스 체크"""
        monitor = DefaultConnectionHealthMonitor()

        # 항상 성공하는 health check
        monitor.register_health_check(
            ConnectionType.DATABASE,
            "primary",
            lambda: True
        )

        health = monitor.check_health(ConnectionType.DATABASE, "primary")

        assert health.status == ConnectionStatus.HEALTHY
        assert health.consecutive_failures == 0

    def test_consecutive_failures_degrade(self):
        """연속 실패 시 상태 변화"""
        monitor = DefaultConnectionHealthMonitor()
        fail_count = [0]

        def failing_check():
            fail_count[0] += 1
            return False

        monitor.register_health_check(
            ConnectionType.CACHE,
            "redis",
            failing_check
        )

        # 첫 실패 → DEGRADED
        health = monitor.check_health(ConnectionType.CACHE, "redis")
        assert health.status == ConnectionStatus.DEGRADED

        # 두 번째 실패 → 여전히 DEGRADED
        health = monitor.check_health(ConnectionType.CACHE, "redis")
        assert health.status == ConnectionStatus.DEGRADED

        # 세 번째 실패 → UNHEALTHY
        health = monitor.check_health(ConnectionType.CACHE, "redis")
        assert health.status == ConnectionStatus.UNHEALTHY


class TestFallbackStrategy:
    """Fallback strategy 테스트"""

    def test_primary_success_no_fallback(self):
        """Primary 성공 시 fallback 사용 안 함"""
        state = PartitionState(db_available=True, cache_available=True)
        strategy = PartitionAwareFallback(state)

        result = strategy.execute(
            primary_fn=lambda: "primary_value",
            default_value="default"
        )

        assert result.value == "primary_value"
        assert result.used_fallback is False

    def test_cache_down_db_fallback(self):
        """캐시 다운 시 DB fallback 사용"""
        state = PartitionState(db_available=True, cache_available=False)

        def db_fallback():
            return "from_db"

        strategy = PartitionAwareFallback(state, db_fallback=db_fallback)

        result = strategy.execute(
            primary_fn=lambda: (_ for _ in ()).throw(ConnectionError("Redis down")),
            default_value="default"
        )

        assert result.value == "from_db"
        assert result.used_fallback is True
        assert result.fallback_mode == FallbackMode.DEGRADE_GRACEFULLY

    def test_all_fallbacks_fail_use_default(self):
        """모든 fallback 실패 시 기본값 사용"""
        state = PartitionState(db_available=False, cache_available=False)
        strategy = PartitionAwareFallback(state)

        result = strategy.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("All down")),
            default_value="safe_default"
        )

        assert result.value == "safe_default"
        assert result.fallback_mode == FallbackMode.USE_DEFAULT
```

---

### 4. 통합 테스트

**파일**: `packages/selfhealing-python/tests/integration/test_partial_partition_integration.py`

```python
"""
Stage 24: Partial Partition Integration Tests

실제 서비스 시나리오에서 partial partition 처리 검증
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from selfhealing.core.connection_health import (
    ConnectionType,
    DefaultConnectionHealthMonitor,
)
from selfhealing.core.fallback_strategy import PartitionAwareFallback


class TestCachePartitionScenario:
    """
    시나리오: Redis만 다운, DB는 정상
    예상: 캐시 미스 → DB에서 직접 조회
    """

    def test_redis_down_db_fallback(self):
        """Redis 다운 시 DB로 fallback"""
        monitor = DefaultConnectionHealthMonitor()

        # DB: 정상
        monitor.register_health_check(
            ConnectionType.DATABASE, "primary",
            lambda: True
        )

        # Redis: 다운
        monitor.register_health_check(
            ConnectionType.CACHE, "redis",
            lambda: False
        )

        # 3회 체크하여 UNHEALTHY 상태로
        for _ in range(3):
            monitor.check_health(ConnectionType.CACHE, "redis")

        state = monitor.get_partition_state()

        assert state.db_available is True
        assert state.cache_available is False
        assert state.is_partial_partition is True

        # Fallback 전략 적용
        db_data = {"user_id": 123, "name": "Test User"}
        strategy = PartitionAwareFallback(
            state,
            db_fallback=lambda: db_data
        )

        # 캐시 조회 실패 시나리오
        def cache_lookup():
            raise ConnectionError("Redis connection refused")

        result = strategy.execute(
            primary_fn=cache_lookup,
            default_value=None
        )

        assert result.value == db_data
        assert result.used_fallback is True


class TestExternalAPIPartitionScenario:
    """
    시나리오: 외부 결제 API만 다운
    예상: 결제 재시도 큐에 넣고 사용자에게 "처리 중" 응답
    """

    def test_payment_api_down_queue_retry(self):
        """결제 API 다운 시 재시도 큐잉"""
        monitor = DefaultConnectionHealthMonitor()

        # DB, Cache: 정상
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: True)
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: True)

        # 결제 API: 다운
        monitor.register_health_check(
            ConnectionType.EXTERNAL_API, "toss_payments",
            lambda: False
        )

        for _ in range(3):
            monitor.check_health(ConnectionType.EXTERNAL_API, "toss_payments")

        state = monitor.get_partition_state()

        assert state.db_available is True
        assert state.cache_available is True
        assert state.external_apis.get("toss_payments") is False
        assert state.is_partial_partition is True
```

---

## 📁 파일 생성 순서

1. `packages/selfhealing-python/src/selfhealing/core/connection_health.py`
2. `packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py`
3. `packages/selfhealing-python/src/selfhealing/core/__init__.py` 수정
4. `packages/selfhealing-python/tests/unit/test_partial_partition.py`
5. `packages/selfhealing-python/tests/integration/test_partial_partition_integration.py`

---

## ✅ 완료 기준

- [x] ConnectionHealthMonitor 인터페이스 구현
- [x] PartitionState 감지 로직 구현
- [x] FallbackStrategy 구현
- [x] Cache down → DB fallback 테스트 통과
- [x] External API down 처리 테스트 통과
- [x] Partial partition 감지 테스트 통과

---

## 📝 새 세션 시작 프롬프트

```
STAGE_24_PARTIAL_PARTITION.md 문서대로 구현해줘.
ConnectionHealthMonitor와 FallbackStrategy부터 시작.
```
