"""
Pytest configuration and fixtures for selfhealing tests.
"""

import os
import pytest
from datetime import datetime


# =============================================================================
# Singleton Reset Fixtures (테스트 격리용)
# =============================================================================

@pytest.fixture
def reset_watchdog_singleton():
    """
    AuditWatchdog 싱글톤을 테스트 전후로 리셋하는 fixture.
    
    Usage:
        def test_something(reset_watchdog_singleton):
            # 테스트 코드
    """
    import selfhealing.audit.audit_watchdog as aw_module
    
    # Setup: 기존 싱글톤 정리
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
        except Exception:
            pass
        aw_module._watchdog_instance = None
    
    yield
    
    # Teardown: 테스트 후 정리
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
        except Exception:
            pass
        aw_module._watchdog_instance = None


# =============================================================================
# DB 연결 필요 테스트 자동 Skip 설정
# =============================================================================

def pytest_collection_modifyitems(config, items):
    """
    DB 연결이 필요한 테스트들을 자동으로 skip 처리합니다.
    packages/selfhealing-python/tests 내에서 django_db 마커가 있는 테스트는
    DB가 없는 환경에서는 skip됩니다.
    """
    # DB 연결 가능 여부 확인
    db_available = os.environ.get("SELFHEALING_TEST_DB_AVAILABLE", "false").lower() == "true"
    
    if db_available:
        return  # DB가 있으면 skip하지 않음
    
    skip_db = pytest.mark.skip(reason="Database not available (set SELFHEALING_TEST_DB_AVAILABLE=true to run)")
    
    for item in items:
        if "django_db" in [marker.name for marker in item.iter_markers()]:
            item.add_marker(skip_db)


# =============================================================================
# Core Type Fixtures
# =============================================================================


@pytest.fixture
def sample_failed_operation_data():
    """Sample failed operation data for tests."""
    from selfhealing.core.types import FailedOperationData

    return FailedOperationData(
        id=1,
        domain="order",
        failure_type="network",
        status="pending",
        created_at=datetime.now(),
        context={"order_id": 123, "amount": 10000},
        error_message="Connection timeout",
        retry_count=0,
        max_retries=3,
    )


@pytest.fixture
def sample_circuit_breaker_data():
    """Sample circuit breaker state data for tests."""
    from selfhealing.core.types import CircuitBreakerStateData

    return CircuitBreakerStateData(
        service_name="external-gateway",
        state="closed",
        failure_count=0,
        success_count=10,
    )


@pytest.fixture
def sample_config():
    """Sample configuration for tests."""
    from selfhealing.core.config import SelfHealingConfig

    return SelfHealingConfig()


# =============================================================================
# Chaos Engineering Fixtures (imported from chaos/conftest.py)
# =============================================================================


@pytest.fixture
def failure_injector():
    """Provides a configurable failure injector for chaos tests."""
    from tests.chaos.conftest import FailureInjector

    return FailureInjector(failure_rate=0.3)


@pytest.fixture
def burst_failure_injector():
    """Provides a burst failure pattern injector."""
    from tests.chaos.conftest import BurstFailureInjector

    return BurstFailureInjector(burst_size=10, burst_interval=50)


@pytest.fixture
def latency_injector():
    """Provides a latency injector for slow degradation tests."""
    from tests.chaos.conftest import LatencyInjector

    return LatencyInjector(
        min_latency_ms=100,
        max_latency_ms=30000,
        degradation_rate=100,
    )


@pytest.fixture
def resource_simulator():
    """Provides a resource exhaustion simulator."""
    from tests.chaos.conftest import ResourceExhaustionSimulator

    return ResourceExhaustionSimulator(max_connections=100)


# =============================================================================
# Pluggable Architecture Fixtures
# =============================================================================


@pytest.fixture
def memory_cache_adapter():
    """Provides an in-memory cache adapter for testing."""
    from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

    adapter = InMemoryCacheAdapter(key_prefix="test:")
    yield adapter
    adapter.flush_all()


@pytest.fixture
def sync_queue_adapter():
    """Provides a synchronous task queue adapter for testing."""
    from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

    return SyncTaskAdapter()


@pytest.fixture(autouse=False)
def test_provider_registry():
    """Setup and teardown provider registry for tests."""
    from selfhealing.factory import ProviderRegistry

    # Store original state
    original_instances = ProviderRegistry._instances.copy()

    # Set test defaults
    ProviderRegistry.set_defaults(
        cache="memory",
        queue="sync",
    )
    ProviderRegistry.clear_instances()

    yield ProviderRegistry

    # Restore original state
    ProviderRegistry._instances = original_instances
