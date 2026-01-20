"""
Test Factories for Selfhealing Tests.

이 패키지는 테스트용 Mock 객체와 데이터를 생성하는 Factory 패턴을 제공합니다.

주요 구성요소:
- TestDataFactory: 테스트 데이터 생성 (FailedOperationData, CircuitBreakerStateData 등)
- MockRedisClient: 통합된 Redis Mock 클라이언트
- InMemoryCircuitBreakerRepository: CB 상태 저장용 인메모리 Repository
- InMemoryDLQRepository: DLQ 엔트리 저장용 인메모리 Repository

사용 예시:
    from tests.factories import TestDataFactory, MockRedisClient
    from tests.factories.repositories import InMemoryCircuitBreakerRepository

    # 테스트 데이터 생성
    cb_state = TestDataFactory.circuit_breaker_state(service_name="payment-api")
    failed_op = TestDataFactory.failed_operation(domain="order", status="pending")

    # Mock Redis 사용
    redis = MockRedisClient()
    redis.set("key", "value")

    # Repository 사용
    repo = InMemoryCircuitBreakerRepository()
    state = repo.get_or_create("test_service")
"""

from tests.factories.data_factory import (
    TestDataFactory,
    MockCircuitBreakerStateData,
    DefaultValues,
)
from tests.factories.redis import MockRedisClient, MockPipeline, MockDistributedLock
from tests.factories.repositories import (
    InMemoryCircuitBreakerRepository,
    InMemoryRateLimitTracker,
    InMemoryDLQRepository,
    MockDLQEntry,
)

__all__ = [
    # Data Factory
    "TestDataFactory",
    "MockCircuitBreakerStateData",
    "DefaultValues",
    # Redis
    "MockRedisClient",
    "MockPipeline",
    "MockDistributedLock",
    # Repositories
    "InMemoryCircuitBreakerRepository",
    "InMemoryRateLimitTracker",
    "InMemoryDLQRepository",
    "MockDLQEntry",
]
