"""
Global pytest configuration and fixtures.

⚠️  전역 tests 폴더는 **통합 테스트 전용**입니다.
    - Unit Test: packages/selfhealing-python/tests/unit/
    - Integration Test: 이 폴더 (실제 Docker 서비스 연결)

Auto-skip logic for infrastructure-dependent tests.
Shared fixtures from shopping/tests/conftest.py via pytest testpaths.

Factory/Builder Pattern:
    이 conftest는 tests/factories 패키지의 Factory/Builder 패턴을 활용합니다.
    - tests.factories.constants: 테스트 상수 (Domains, Services, Status, CircuitState)
    - tests.factories.builders: Builder 패턴 (CircuitBreakerStateBuilder, FailedOperationBuilder)
    - tests.factories.integration: 실제 Docker 연결 (RealRedisClientFactory, RealDatabaseFactory)

Note:
    pyproject.toml에서 testpaths = ["shopping/tests", "tests"]로 설정되어 있으므로
    shopping/tests/conftest.py의 fixture들이 자동으로 사용 가능합니다.
    pytest_plugins 사용 시 중복 등록 에러가 발생합니다.
"""

import os
import pytest

# =============================================================================
# pytest_plugins 제거됨 - pyproject.toml의 testpaths 설정으로 fixture 공유
# shopping/tests/conftest.py가 자동 로드되므로 pytest_plugins로 재등록하면 충돌 발생
# =============================================================================


# =============================================================================
# Constants import (for fixtures)
# =============================================================================
from tests.factories.constants import (
    RedisTestConfig,
    DatabaseTestConfig,
)


def pytest_configure(config):
    """pytest 시작 시 DB 연결 상태 확인 및 환경 설정."""
    # 마커가 이미 pyproject.toml에 정의되어 있으므로 여기서는 skip
    pass


def pytest_collection_modifyitems(config, items):
    """
    테스트 수집 후 requires_db, requires_redis 마커가 있는 테스트 자동 skip.

    환경변수로 인프라가 available하다고 표시하지 않으면 skip.
    실제 인프라 연결을 확인하여 자동으로 available 여부를 판단합니다.
    """
    # 환경변수 우선, 없으면 실제 연결 확인
    db_available = os.environ.get("TEST_DB_AVAILABLE", "").lower() == "true"
    redis_available = os.environ.get("TEST_REDIS_AVAILABLE", "").lower() == "true"

    # 환경변수가 설정되지 않은 경우, 실제 연결 확인
    if not db_available and not os.environ.get("TEST_DB_AVAILABLE"):
        db_available = _check_db_connection()

    if not redis_available and not os.environ.get("TEST_REDIS_AVAILABLE"):
        redis_available = _check_redis_connection()

    skip_db = pytest.mark.skip(reason="Database not available (set TEST_DB_AVAILABLE=true)")
    skip_redis = pytest.mark.skip(reason="Redis not available (set TEST_REDIS_AVAILABLE=true)")

    for item in items:
        if not db_available and "requires_db" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_db)
        if not redis_available and "requires_redis" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_redis)


def _check_db_connection() -> bool:
    """PostgreSQL 연결 확인."""
    try:
        import psycopg2

        config = DatabaseTestConfig()
        conn = psycopg2.connect(
            host=config.DEFAULT_HOST,
            port=config.DEFAULT_PORT,
            database=config.DEFAULT_DB,
            user=config.DEFAULT_USER,
            password=config.DEFAULT_PASSWORD,
        )
        conn.close()
        return True
    except Exception:
        return False


def _check_redis_connection() -> bool:
    """
    Redis 연결 확인.

    테스트용 포트(16379)를 먼저 확인하고, 없으면 기본 포트(6379)도 확인합니다.
    docker-compose.test.yml을 사용할 때는 16379 포트가 사용됩니다.
    """
    try:
        import redis

        config = RedisTestConfig()

        # 테스트용 포트(16379) 먼저 확인
        try:
            client = redis.Redis(
                host=config.DEFAULT_HOST,
                port=config.TEST_PORT,
                db=config.TEST_DB,
            )
            client.ping()
            client.close()
            return True
        except Exception:
            pass

        # 기본 포트(6379) 확인 (로컬 Redis)
        client = redis.Redis(
            host=config.DEFAULT_HOST,
            port=config.DEFAULT_PORT,
            db=config.DEFAULT_DB,
        )
        client.ping()
        client.close()
        return True
    except Exception:
        return False


# =============================================================================
# Redis Fixtures for Integration Tests
# =============================================================================


@pytest.fixture(scope="session")
def redis_client():
    """
    Real Redis client for integration tests.

    Requires Docker Compose: docker-compose -f docker-compose.test.yml up -d
    Uses RedisTestConfig.TEST_PORT (16379) which maps to container's 6379.
    """
    import redis

    config = RedisTestConfig()
    redis_url = os.environ.get("REDIS_URL", config.test_redis_url)
    client = redis.from_url(redis_url, decode_responses=True)

    # Verify connection
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available. Run: docker-compose -f docker-compose.test.yml up -d")

    yield client

    # Cleanup: flush test database
    client.flushdb()


@pytest.fixture
def redis_circuit_breaker_repository(redis_client):
    """
    Real Redis-based Circuit Breaker Repository.

    Uses ResilientStorageBackend with actual Redis connection.
    Port configuration from RedisTestConfig.TEST_PORT.
    """
    from selfhealing.adapters.resilient.backend import (
        ResilientStorageBackend,
        ResilientStorageConfig,
    )
    from selfhealing.adapters.redis.circuit_breaker import RedisCircuitBreakerStateRepository

    # Create backend with test namespace using RedisTestConfig
    config_redis = RedisTestConfig()
    redis_url = os.environ.get("REDIS_URL", config_redis.test_redis_url)
    config = ResilientStorageConfig(
        redis_url=redis_url,
        key_prefix="test:selfhealing:",
        allow_memory_only=True,  # Allow fallback for test isolation
    )
    backend = ResilientStorageBackend(config=config)

    yield RedisCircuitBreakerStateRepository(backend=backend)

    # Cleanup: remove test keys
    for key in redis_client.keys("test:selfhealing:*"):
        redis_client.delete(key)


@pytest.fixture
def redis_dlq_repository(redis_client):
    """
    Real Redis-based DLQ Repository.

    Uses ResilientStorageBackend with actual Redis connection.
    Port configuration from RedisTestConfig.TEST_PORT.
    """
    from selfhealing.adapters.resilient.backend import (
        ResilientStorageBackend,
        ResilientStorageConfig,
    )
    from selfhealing.adapters.redis.dlq import RedisDLQRepository

    config_redis = RedisTestConfig()
    redis_url = os.environ.get("REDIS_URL", config_redis.test_redis_url)
    config = ResilientStorageConfig(
        redis_url=redis_url,
        key_prefix="test:selfhealing:dlq:",
        allow_memory_only=True,
    )
    backend = ResilientStorageBackend(config=config)

    yield RedisDLQRepository(backend=backend)

    # Cleanup: remove test keys
    for key in redis_client.keys("test:selfhealing:dlq:*"):
        redis_client.delete(key)


# =============================================================================
# Integration Test용 실제 Docker 연결 Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def docker_redis_client():
    """
    Docker Compose의 실제 Redis 클라이언트.

    docker-compose.yml 기본 포트 6379 사용.
    통합 테스트에서 실제 Redis 동작을 검증할 때 사용.
    """
    import redis

    config = RedisTestConfig()
    client = redis.Redis(
        host=config.DEFAULT_HOST,
        port=config.DEFAULT_PORT,  # 6379
        db=config.TEST_DB,
        decode_responses=True,
    )

    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Docker Redis not available. Run: docker-compose up -d")

    yield client

    # Cleanup
    client.flushdb()


@pytest.fixture
def clean_redis(docker_redis_client):
    """
    테스트 전/후 Redis 정리.

    각 테스트가 깨끗한 상태에서 시작하도록 보장.
    """
    docker_redis_client.flushdb()
    yield docker_redis_client
    docker_redis_client.flushdb()


@pytest.fixture(scope="session")
def docker_db_connection():
    """
    Docker Compose의 실제 PostgreSQL 연결.

    통합 테스트에서 실제 DB 트랜잭션을 검증할 때 사용.
    """
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")

    config = DatabaseTestConfig()

    try:
        conn = psycopg2.connect(
            host=config.DEFAULT_HOST,
            port=config.DEFAULT_PORT,
            database=config.DEFAULT_DB,
            user=config.DEFAULT_USER,
            password=config.DEFAULT_PASSWORD,
        )
        yield conn
        conn.close()
    except psycopg2.OperationalError:
        pytest.skip("Docker PostgreSQL not available. Run: docker-compose up -d")


# =============================================================================
# Celery Integration Test Fixtures
# =============================================================================


@pytest.fixture
def celery_eager_mode(settings):
    """
    Celery Eager 모드 활성화.

    태스크가 동기적으로 실행되어 결과를 즉시 확인 가능.
    """
    original_always_eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    original_eager_propagates = getattr(settings, "CELERY_TASK_EAGER_PROPAGATES", False)

    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    yield

    settings.CELERY_TASK_ALWAYS_EAGER = original_always_eager
    settings.CELERY_TASK_EAGER_PROPAGATES = original_eager_propagates


@pytest.fixture
def celery_async_mode(settings):
    """
    Celery Async 모드 (실제 워커 필요).

    실제 비동기 동작 테스트 시 사용.
    Docker의 celery_worker 컨테이너가 실행 중이어야 함.
    """
    original_always_eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

    settings.CELERY_TASK_ALWAYS_EAGER = False

    yield

    settings.CELERY_TASK_ALWAYS_EAGER = original_always_eager


# =============================================================================
# Builder Pattern Fixtures (tests.factories 활용)
# =============================================================================


@pytest.fixture
def circuit_breaker_builder():
    """
    Circuit Breaker State Builder.

    체이닝 방식으로 CB 상태 객체 생성.

    사용 예:
        def test_cb(circuit_breaker_builder):
            state = (circuit_breaker_builder
                .payment_service()
                .opened()
                .with_failure_count(5)
                .build())
    """
    from tests.factories.builders import CircuitBreakerStateBuilder

    return CircuitBreakerStateBuilder()


@pytest.fixture
def failed_operation_builder():
    """
    Failed Operation (DLQ) Builder.

    체이닝 방식으로 DLQ 엔트리 생성.

    사용 예:
        def test_dlq(failed_operation_builder):
            entry = (failed_operation_builder
                .payment_domain()
                .pg_timeout()
                .pending()
                .build())
    """
    from tests.factories.builders import FailedOperationBuilder

    return FailedOperationBuilder()


@pytest.fixture
def canary_rollout_builder():
    """
    Canary Rollout Builder.

    체이닝 방식으로 Canary 롤아웃 객체 생성.
    """
    from tests.factories.builders import CanaryRolloutBuilder

    return CanaryRolloutBuilder()


# =============================================================================
# Data Factory Fixture
# =============================================================================


@pytest.fixture
def test_data():
    """
    Test Data Factory.

    다양한 테스트 데이터 생성을 위한 Factory.

    사용 예:
        def test_something(test_data):
            cb_state = test_data.circuit_breaker_state(
                service_name="payment-api",
                state="open"
            )

            failed_op = test_data.failed_operation(
                domain="payment",
                failure_type="PG_TIMEOUT"
            )

            toss_response = test_data.toss_payment_response(
                status="DONE",
                amount=10000
            )
    """
    from tests.factories.data_factory import TestDataFactory

    return TestDataFactory


# =============================================================================
# Integration Test Context Fixture
# =============================================================================


@pytest.fixture
def integration_context(docker_redis_client, docker_db_connection):
    """
    통합 테스트 컨텍스트.

    Redis + DB를 함께 사용하는 통합 테스트에서 사용.

    사용 예:
        def test_full_flow(integration_context):
            ctx = integration_context
            ctx.redis.set("key", "value")
            # DB 연산...
    """
    from tests.factories.integration import IntegrationTestContext

    ctx = IntegrationTestContext(
        redis_client=docker_redis_client,
        db_connection=docker_db_connection,
    )

    yield ctx

    ctx.cleanup()
