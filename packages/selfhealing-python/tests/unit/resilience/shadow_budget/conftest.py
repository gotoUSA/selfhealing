"""
Shadow Budget 테스트 공통 fixtures.

분리된 테스트 파일들이 사용하는 공통 설정 및 mock 객체.
"""

import pytest
from datetime import datetime, timedelta, timezone


@pytest.fixture
def shadow_calculator():
    """Fresh ShadowBudgetCalculator for each test."""
    from selfhealing.services.error_budget.reconciliation import ShadowBudgetCalculator
    return ShadowBudgetCalculator()


@pytest.fixture
def sample_failsafe_period():
    """Sample FailSafePeriod for testing."""
    from selfhealing.services.error_budget.reconciliation import FailSafePeriod
    
    now_time = datetime.now(timezone.utc)
    return FailSafePeriod(
        period_id="test-period-1",
        started_at=now_time - timedelta(hours=1),
        ended_at=now_time,
        trigger_reason="Test trigger",
        trigger_component="test_component",
        is_active=False,
    )


@pytest.fixture
def mock_sla_config(monkeypatch):
    """SLA Config mock fixture factory."""
    from dataclasses import dataclass, field
    
    def _create_mock(domains_sla: dict):
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: domains_sla)
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.settings.get_config",
            lambda: MockConfig(),
        )
    
    return _create_mock


@pytest.fixture
def mock_learning_service(monkeypatch):
    """Learning Service mock fixture factory."""
    def _create_mock(patterns: list):
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return patterns
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
    
    return _create_mock
