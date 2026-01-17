"""
Self-healing tests conftest.

Provides isolation from shopping app database dependencies.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True, scope="function")
def mock_external_services():
    """
    Mock external services to prevent blocking on Redis/DB connections.
    
    This fixture runs for every test in self_healing test suite.
    """
    mock_backend = MagicMock()
    mock_backend.get.return_value = None
    mock_backend.set.return_value = True
    mock_backend.delete.return_value = True
    
    mock_config_manager = MagicMock()
    mock_config_manager.get_chaos_config.return_value = {}
    mock_config_manager.update_chaos_config.return_value = None
    # Add governance config to prevent Mock comparison errors
    mock_config_manager.get_governance_config.return_value = {
        "threshold_operator": 0.15,
        "threshold_admin": 0.30,
        "threshold_dual_approval": 0.50,
    }
    mock_config_manager.get_approval_requests.return_value = []
    # Add rate limit config
    mock_config_manager.get_rate_limit_config.return_value = {
        "control_api_rate_limit": 100,
        "control_api_window_seconds": 60,
        "emergency_rate_limit": 10,
        "emergency_window_seconds": 60,
    }
    # Add error budget config
    mock_config_manager.get_error_budget_config.return_value = {
        "budget_percent": 0.1,
        "window_hours": 24,
        "burn_rate_thresholds": {"fast": 10.0, "slow": 2.0},
    }
    
    with patch("selfhealing.core.state_backend.get_state_backend", return_value=mock_backend):
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager", return_value=mock_config_manager):
            yield


@pytest.fixture(autouse=True, scope="function")
def reset_singletons():
    """Reset singleton instances before each test."""
    # Reset ChaosSchedulerService singleton
    try:
        from selfhealing.services.chaos import scheduler
        scheduler._chaos_scheduler = None
    except ImportError:
        pass
    
    # Reset ComplianceService singleton
    try:
        from selfhealing.services.compliance.service import ComplianceService
        ComplianceService._instance = None
    except ImportError:
        pass
    
    yield
    
    # Cleanup after test
    try:
        from selfhealing.services.chaos import scheduler
        scheduler._chaos_scheduler = None
    except ImportError:
        pass
    
    try:
        from selfhealing.services.compliance.service import ComplianceService
        ComplianceService._instance = None
    except ImportError:
        pass
