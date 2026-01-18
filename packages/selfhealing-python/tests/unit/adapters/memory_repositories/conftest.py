"""
Memory Repositories 테스트 공통 설정.
"""

import pytest


@pytest.fixture
def failed_op_repo():
    """InMemoryFailedOperationRepository fixture."""
    from selfhealing.adapters.memory import InMemoryFailedOperationRepository
    return InMemoryFailedOperationRepository()


@pytest.fixture
def circuit_breaker_repo():
    """InMemoryCircuitBreakerStateRepository fixture."""
    from selfhealing.adapters.memory import InMemoryCircuitBreakerStateRepository
    return InMemoryCircuitBreakerStateRepository()


@pytest.fixture
def security_repo():
    """InMemorySecurityIncidentRepository fixture."""
    from selfhealing.adapters.memory import InMemorySecurityIncidentRepository
    return InMemorySecurityIncidentRepository()
