"""
Pytest configuration and fixtures for selfhealing tests.
"""

import pytest
from datetime import datetime


# =============================================================================
# Core Type Fixtures
# =============================================================================


@pytest.fixture
def sample_failed_operation_data():
    """Sample failed operation data for tests."""
    from selfhealing.core.types import FailedOperationData

    return FailedOperationData(
        id=1,
        domain="payment",
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
        service_name="payment-gateway",
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
def mock_payment_adapter():
    """Provides a mock payment adapter for testing."""
    from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter

    adapter = MockPaymentAdapter()
    yield adapter
    adapter.reset()


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
        payment="mock",
        cache="memory",
        queue="sync",
    )
    ProviderRegistry.clear_instances()

    yield ProviderRegistry

    # Restore original state
    ProviderRegistry._instances = original_instances
