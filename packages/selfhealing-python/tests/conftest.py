"""
Pytest configuration and fixtures for selfhealing tests.
"""

import pytest
from datetime import datetime


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
