"""
Test Factories for Selfhealing Tests.

이 패키지는 테스트용 Mock 객체와 데이터를 생성하는 Factory 패턴을 제공합니다.

주요 구성요소:
- TestDataFactory: 테스트 데이터 생성 (FailedOperationData, CircuitBreakerStateData 등)
- MockRedisClient: 통합된 Redis Mock 클라이언트
- InMemoryCircuitBreakerRepository: CB 상태 저장용 인메모리 Repository
- InMemoryDLQRepository: DLQ 엔트리 저장용 인메모리 Repository
- Constants: 테스트 상수 (Domains, Services, FailureTypes, Status, CircuitState)

사용 예시:
    from tests.factories import TestDataFactory, MockRedisClient, Domains, Status
    from tests.factories.repositories import InMemoryCircuitBreakerRepository

    # 테스트 데이터 생성 (상수 사용)
    cb_state = TestDataFactory.circuit_breaker_state(service_name="payment-api")
    failed_op = TestDataFactory.failed_operation(domain=Domains.ORDER, status=Status.PENDING)

    # Mock Redis 사용
    redis = MockRedisClient()
    redis.set("key", "value")

    # Repository 사용
    repo = InMemoryCircuitBreakerRepository()
    state = repo.get_or_create("test_service")
"""

# Constants (constants.py)
from tests.factories.constants import (
    DefaultValues,
    Domains,
    Services,
    FailureTypes,
    Status,
    CircuitState,
)
from tests.factories.data_factory import (
    TestDataFactory,
    MockCircuitBreakerStateData,
)
from tests.factories.redis import MockRedisClient, MockPipeline, MockDistributedLock
from tests.factories.repositories import (
    InMemoryCircuitBreakerRepository,
    InMemoryRateLimitTracker,
    InMemoryDLQRepository,
    MockDLQEntry,
)
from tests.factories.time_helpers import (
    freeze_time,
    mock_sleep,
    MockSleep,
    get_fixed_datetime,
    make_datetime_range,
)

__all__ = [
    # Constants
    "DefaultValues",
    "Domains",
    "Services",
    "FailureTypes",
    "Status",
    "CircuitState",
    # Data Factory
    "TestDataFactory",
    "MockCircuitBreakerStateData",
    # Redis
    "MockRedisClient",
    "MockPipeline",
    "MockDistributedLock",
    # Repositories
    "InMemoryCircuitBreakerRepository",
    "InMemoryRateLimitTracker",
    "InMemoryDLQRepository",
    "MockDLQEntry",
    # Time Helpers
    "freeze_time",
    "mock_sleep",
    "MockSleep",
    "get_fixed_datetime",
    "make_datetime_range",
]
