"""
Error Budget Reconciliation 테스트 공통 fixtures.
"""

import pytest
from datetime import datetime, timezone


@pytest.fixture
def period_tracker():
    """Fresh FailSafePeriodTracker for each test."""
    from selfhealing.services.error_budget.reconciliation import FailSafePeriodTracker
    return FailSafePeriodTracker(max_periods=10)


@pytest.fixture
def shadow_calculator():
    """Fresh ShadowBudgetCalculator for each test."""
    from selfhealing.services.error_budget.reconciliation import ShadowBudgetCalculator
    return ShadowBudgetCalculator()


@pytest.fixture
def reconciliation_service(period_tracker, shadow_calculator):
    """Fresh ErrorBudgetReconciliationService for each test."""
    from selfhealing.services.error_budget.reconciliation import (
        ErrorBudgetReconciliationService,
        ReconciliationConfig,
    )
    # 테스트에서는 짧은 기간 자동 제외 비활성화
    config = ReconciliationConfig(
        auto_exclude_short_periods=False,
    )
    return ErrorBudgetReconciliationService(
        config=config,
        period_tracker=period_tracker,
        shadow_calculator=shadow_calculator,
    )


@pytest.fixture
def mock_current_budget():
    """Mock for current budget status."""
    return {
        "remaining_percent": 75.0,
        "consumed_minutes": 10.8,
        "total_minutes": 43.2,
    }
