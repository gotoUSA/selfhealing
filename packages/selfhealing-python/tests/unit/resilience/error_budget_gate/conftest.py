"""
Error Budget Gate 테스트 공통 fixtures.

분리된 테스트 파일들이 사용하는 공통 설정 및 mock 객체.
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def mock_error_budget_percent():
    """에러 예산 백분율 mock fixture."""
    def _mock(gate, value):
        return patch.object(gate, '_get_error_budget_percent', return_value=value)
    return _mock


@pytest.fixture
def default_gate_config():
    """기본 Gate 설정 fixture."""
    from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

    return ErrorBudgetGateConfig(
        enabled=True,
        critical_threshold_percent=10.0,
        warning_threshold_percent=20.0,
        fail_open=True,
        cache_ttl_seconds=30,
    )


@pytest.fixture
def gate_with_rate_limit():
    """Rate Limit가 활성화된 Gate fixture."""
    from selfhealing.services.error_budget_gate import (
        ErrorBudgetGate,
        ErrorBudgetGateConfig,
    )

    config = ErrorBudgetGateConfig(
        enabled=True,
        fail_open=True,
        fail_open_rate_limit_enabled=True,
        fail_open_rate_limit_per_minute=5,
        fail_open_rate_limit_window_seconds=60,
    )
    return ErrorBudgetGate(config=config)


@pytest.fixture
def gate_with_circuit_breaker():
    """Circuit Breaker가 활성화된 Gate fixture."""
    from selfhealing.services.error_budget_gate import (
        ErrorBudgetGate,
        ErrorBudgetGateConfig,
    )

    config = ErrorBudgetGateConfig(
        enabled=True,
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_recovery_timeout=30,
    )
    return ErrorBudgetGate(config=config)
