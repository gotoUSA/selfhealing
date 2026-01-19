"""
Global pytest configuration and fixtures.

Auto-skip logic for infrastructure-dependent tests.
Shared fixtures from shopping/tests/conftest.py via pytest_plugins.
"""
import pytest

# =============================================================================
# pytest_plugins: shopping/tests/conftest.py의 fixture들을 이 conftest에서 사용 가능하게 함
# 이렇게 하면 tests/hybrid/ 등에서 user_factory, product 등의 fixture 사용 가능
# =============================================================================
pytest_plugins = ["shopping.tests.conftest"]


def pytest_configure(config):
    """pytest 시작 시 DB 연결 상태 확인 및 환경 설정."""
    # 마커가 이미 pyproject.toml에 정의되어 있으므로 여기서는 skip


def pytest_collection_modifyitems(config, items):
    """
    테스트 수집 후 requires_db, requires_redis 마커가 있는 테스트 자동 skip.
    
    환경변수로 인프라가 available하다고 표시하지 않으면 skip.
    """
    import os
    
    db_available = os.environ.get("TEST_DB_AVAILABLE", "").lower() == "true"
    redis_available = os.environ.get("TEST_REDIS_AVAILABLE", "").lower() == "true"
    
    skip_db = pytest.mark.skip(reason="Database not available (set TEST_DB_AVAILABLE=true)")
    skip_redis = pytest.mark.skip(reason="Redis not available (set TEST_REDIS_AVAILABLE=true)")
    
    for item in items:
        if not db_available and "requires_db" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_db)
        if not redis_available and "requires_redis" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_redis)


# =============================================================================
# Redis Fixtures for Integration Tests
# =============================================================================

@pytest.fixture(scope="session")
def redis_client():
    """
    Real Redis client for integration tests.
    
    Requires Docker Compose: docker-compose -f docker-compose.test.yml up -d
    Port: 16379 (mapped from container's 6379)
    """
    import os
    import redis
    
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:16379/0")
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
    """
    import os
    from selfhealing.adapters.resilient.backend import (
        ResilientStorageBackend,
        ResilientStorageConfig,
    )
    from selfhealing.adapters.redis.circuit_breaker import RedisCircuitBreakerStateRepository
    
    # Create backend with test namespace
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:16379/0")
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
    """
    import os
    from selfhealing.adapters.resilient.backend import (
        ResilientStorageBackend,
        ResilientStorageConfig,
    )
    from selfhealing.adapters.redis.dlq import RedisDLQRepository
    
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:16379/0")
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
