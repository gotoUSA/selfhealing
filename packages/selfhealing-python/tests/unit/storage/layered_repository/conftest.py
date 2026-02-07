"""
Layered Repository 테스트 공통 설정.

이 패키지의 모든 테스트에서 사용하는 fixtures.
"""

import pytest
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock


@pytest.fixture
def mock_l2_repo():
    """Mock L2 레포지토리."""
    from selfhealing.adapters.memory.circuit_breaker import (
        InMemoryCircuitBreakerStateRepository,
    )

    mock = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
    mock.get_all.return_value = []
    return mock


@pytest.fixture
def shadow_logger():
    """Shadow Logger fixture."""
    from selfhealing.adapters.memory.circuit_breaker import get_shadow_logger

    logger = get_shadow_logger()
    logger.clear()
    yield logger
    logger.clear()


@pytest.fixture
def drift_reconciler():
    """Drift Reconciler fixture."""
    from selfhealing.adapters.memory.circuit_breaker import get_drift_reconciler

    reconciler = get_drift_reconciler()
    reconciler.clear_history()
    yield reconciler
    reconciler.clear_history()
