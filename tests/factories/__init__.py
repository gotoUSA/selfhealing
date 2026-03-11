"""
Test Factories for Global Integration Tests.

전역 tests 폴더는 **통합 테스트 전용**입니다.
실제 Docker 서비스(Redis, PostgreSQL, Celery)를 연결하여 테스트합니다.

⚠️  selfhealing Unit Test는 별도 repo에서 진행: https://github.com/gotoUSA/selfhealing-python

주요 구성요소:
- Constants: 테스트 상수 (Domains, Services, FailureTypes, Status, CircuitState)
- Builders: 복잡한 테스트 객체 생성을 위한 Builder 클래스들
- Data Factory: selfhealing 및 Django 테스트 데이터 생성
- Integration: 실제 Docker 서비스 연결 (Redis, PostgreSQL, Celery)

사용 예시:
    # Integration Test (실제 Docker 연결)
    from tests.factories.integration import RealRedisClientFactory

    real_redis = RealRedisClientFactory.create()
    real_redis.set("key", "value")  # 실제 Redis에 저장

    # Builder 패턴
    from tests.factories import CircuitBreakerStateBuilder

    cb_state = (CircuitBreakerStateBuilder()
        .payment_service()
        .opened()
        .with_failure_count(5)
        .build())
"""

# Constants
from tests.factories.constants import (
    Domains,
    Services,
    FailureTypes,
    Status,
    CircuitState,
    OrderStatus,
    PaymentStatus,
    TestConstants,
    CeleryTestConfig,
    RedisTestConfig,
    DatabaseTestConfig,
    # Phase 3: 추가 상수
    CanaryCluster,
    CanaryPercentage,
    ChaosIntensity,
    RBACRole,
)

# Builders
from tests.factories.builders import (
    CircuitBreakerStateBuilder,
    FailedOperationBuilder,
    CanaryRolloutBuilder,
    MockServiceBuilder,
    # Phase 3: 추가 Builder
    MockRequestBuilder,
    CanaryStageBuilder,
    ChaosExperimentBuilder,
    WatchdogConfigBuilder,
)

# Data Factories
from tests.factories.data_factory import (
    TestDataFactory,
    MockCircuitBreakerStateData,
    MockFailedOperationData,
    MockCanaryRolloutData,
)

# Integration (실제 Docker 연결)
from tests.factories.integration import (
    RealRedisClientFactory,
    RealDatabaseFactory,
    CeleryTaskRunner,
    IntegrationTestContext,
)

__all__ = [
    # Constants
    "Domains",
    "Services",
    "FailureTypes",
    "Status",
    "CircuitState",
    "OrderStatus",
    "PaymentStatus",
    "TestConstants",
    "CeleryTestConfig",
    "RedisTestConfig",
    "DatabaseTestConfig",
    # Phase 3: 추가 상수
    "CanaryCluster",
    "CanaryPercentage",
    "ChaosIntensity",
    "RBACRole",
    # Builders
    "CircuitBreakerStateBuilder",
    "FailedOperationBuilder",
    "CanaryRolloutBuilder",
    "MockServiceBuilder",
    # Phase 3: 추가 Builder
    "MockRequestBuilder",
    "CanaryStageBuilder",
    "ChaosExperimentBuilder",
    "WatchdogConfigBuilder",
    # Data Factory
    "TestDataFactory",
    "MockCircuitBreakerStateData",
    "MockFailedOperationData",
    "MockCanaryRolloutData",
    # Integration
    "RealRedisClientFactory",
    "RealDatabaseFactory",
    "CeleryTaskRunner",
    "IntegrationTestContext",
]
