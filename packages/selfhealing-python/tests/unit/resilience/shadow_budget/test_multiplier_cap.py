"""
Multiplier Cap 테스트.
"""

import pytest


class TestMultiplierCap:
    """Multiplier Cap 테스트."""

    def test_cap_prevents_weight_explosion(self, shadow_calculator, monkeypatch):
        """
        Cap이 가중치 폭발을 방지함.
        
        Critical(10x) × Payment(24x) × 반복(2x) = 480x → Cap 50x로 제한
        """
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config (payment = 1시간 → 24배)
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        # 실제 코드는 selfhealing.settings.get_config를 사용함
        monkeypatch.setattr(
            "selfhealing.settings.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service (occurrence_count=10 → 2배)
        mock_pattern = LearningPattern(
            pattern_id="test-cap",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        # Critical 10개:
        # severity: 10 * 0.01 = 0.1분 (base)
        # 무제한 multiplier: 1.0(source) * 24(domain) * 2(pattern) = 48
        # Cap 후: min(48, 50) = 48
        # 최종: 0.1 * 48 = 4.8분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        assert result == pytest.approx(4.8, rel=1e-3)

    def test_cap_applied_when_exceeds_limit(self, shadow_calculator, monkeypatch):
        """Cap이 50 초과 시 적용됨."""
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config (payment = 0.5시간 → 48배) - 경계값 테스트
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 0.5})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        # 실제 코드는 selfhealing.settings.get_config를 사용함
        monkeypatch.setattr(
            "selfhealing.settings.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service (occurrence_count=10 → 2배)
        mock_pattern = LearningPattern(
            pattern_id="test-cap-exceed",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        # 무제한 multiplier: 1.0(source) * 48(domain) * 2(pattern) = 96
        # Cap 후: min(96, 50) = 50
        # medium 10개: 10 * 0.001 = 0.01분 (base)
        # 최종: 0.01 * 50 = 0.5분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        assert result == pytest.approx(0.5, rel=1e-3)


class TestCalculateShadowBudgetWithDomainAndPattern:
    """calculate_shadow_budget 통합 테스트."""

    def test_calculate_with_domain_and_failure_type(
        self, shadow_calculator, sample_failsafe_period, monkeypatch
    ):
        """domain과 failure_type을 사용한 Shadow Budget 계산."""
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 2})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        # 실제 코드는 selfhealing.settings.get_config를 사용함
        monkeypatch.setattr(
            "selfhealing.settings.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service
        mock_pattern = LearningPattern(
            pattern_id="test-integrate",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=5,  # 1.5배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        result = shadow_calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
            errors_by_severity={"medium": 100},
            domain="payment",
            failure_type="timeout",
        )
        
        # 100 * 0.001 = 0.1 (base)
        # none_available source = 0.5
        # payment domain = 24/2 = 12
        # pattern = 1.5
        # multiplier = 0.5 * 12 * 1.5 = 9.0
        # 최종: 0.1 * 9.0 = 0.9분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.9, rel=1e-2)
