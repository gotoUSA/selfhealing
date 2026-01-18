"""
팩토리 함수 테스트.
"""

import pytest
from unittest.mock import Mock


class TestFactoryFunctions:
    """팩토리 함수 테스트."""

    def test_get_period_tracker_singleton(self):
        """FailSafePeriodTracker 싱글톤 테스트."""
        from selfhealing.services.error_budget.reconciliation import get_period_tracker
        import selfhealing.services.error_budget.reconciliation as module
        
        # Reset singleton for test
        module._period_tracker = None
        
        tracker1 = get_period_tracker()
        tracker2 = get_period_tracker()
        
        assert tracker1 is tracker2

    def test_get_reconciliation_service_singleton(self):
        """ErrorBudgetReconciliationService 싱글톤 테스트."""
        from selfhealing.services.error_budget.reconciliation import get_reconciliation_service
        import selfhealing.services.error_budget.reconciliation as module
        
        # Reset singleton for test
        module._reconciliation_service = None
        module._period_tracker = None
        
        service1 = get_reconciliation_service()
        service2 = get_reconciliation_service()
        
        assert service1 is service2

    def test_configure_reconciliation_service(self):
        """configure_reconciliation_service 테스트."""
        from selfhealing.services.error_budget.reconciliation import (
            configure_reconciliation_service,
            ReconciliationConfig,
        )
        import selfhealing.services.error_budget.reconciliation as module
        
        # Reset singleton for test
        module._reconciliation_service = None
        module._period_tracker = None
        
        mock_budget = Mock(return_value={"remaining_percent": 80.0})
        mock_apply = Mock()
        
        service = configure_reconciliation_service(
            config=ReconciliationConfig(auto_apply=True),
            get_current_budget=mock_budget,
            apply_adjustment=mock_apply,
        )
        
        assert service._config.auto_apply is True
        assert service._get_current_budget is mock_budget
