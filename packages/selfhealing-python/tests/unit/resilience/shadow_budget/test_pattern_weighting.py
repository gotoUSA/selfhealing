"""
Learning 패턴 기반 가중치 테스트.
"""

import pytest


class TestPatternWeightConstants:
    """Pattern 상수 정의 테스트."""

    def test_pattern_occurrence_weight_defined(self):
        """PATTERN_OCCURRENCE_WEIGHT가 정의되어 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            PATTERN_OCCURRENCE_WEIGHT,
        )
        assert PATTERN_OCCURRENCE_WEIGHT["high"] == 2.0
        assert PATTERN_OCCURRENCE_WEIGHT["medium"] == 1.5
        assert PATTERN_OCCURRENCE_WEIGHT["low"] == 1.2
        assert PATTERN_OCCURRENCE_WEIGHT["none"] == 1.0


class TestPatternWeighting:
    """Learning 패턴 기반 가중치 테스트."""

    def test_no_pattern_returns_base_weight(self, shadow_calculator):
        """패턴이 없으면 기본 가중치(1.0) 반환."""
        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        # Learning 서비스에 패턴이 없거나 예외 발생 시 1.0
        assert weight == 1.0

    def test_pattern_weight_with_mocked_service(self, shadow_calculator, monkeypatch):
        """Learning 서비스에서 패턴 조회 시 가중치 반환."""
        from selfhealing.services.learning.models import LearningPattern, PatternType

        # Mock 패턴 생성
        mock_pattern = LearningPattern(
            pattern_id="test-1",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout pattern",
            confidence=0.9,
            occurrence_count=10,  # 10회 이상 → 2배
        )

        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        assert weight == pytest.approx(2.0, rel=1e-6)

    def test_pattern_weight_medium_occurrence(self, shadow_calculator, monkeypatch):
        """5회 이상 발생 패턴은 1.5배 가중치."""
        from selfhealing.services.learning.models import LearningPattern, PatternType

        mock_pattern = LearningPattern(
            pattern_id="test-2",
            pattern_type=PatternType.FAILURE,
            name="order:validation_error",
            description="Order validation error",
            confidence=0.8,
            occurrence_count=7,  # 5 <= x < 10 → 1.5배
        )

        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        weight = shadow_calculator._get_pattern_weight("order", "validation_error")
        assert weight == pytest.approx(1.5, rel=1e-6)

    def test_pattern_weight_low_occurrence(self, shadow_calculator, monkeypatch):
        """3회 이상 발생 패턴은 1.2배 가중치."""
        from selfhealing.services.learning.models import LearningPattern, PatternType

        mock_pattern = LearningPattern(
            pattern_id="test-3",
            pattern_type=PatternType.FAILURE,
            name="notification:delivery_failed",
            description="Notification delivery failed",
            confidence=0.7,
            occurrence_count=4,  # 3 <= x < 5 → 1.2배
        )

        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        weight = shadow_calculator._get_pattern_weight("notification", "delivery_failed")
        assert weight == pytest.approx(1.2, rel=1e-6)

    def test_pattern_weight_below_threshold(self, shadow_calculator, monkeypatch):
        """3회 미만 발생 패턴은 기본 가중치(1.0)."""
        from selfhealing.services.learning.models import LearningPattern, PatternType

        mock_pattern = LearningPattern(
            pattern_id="test-4",
            pattern_type=PatternType.FAILURE,
            name="point:insufficient_balance",
            description="Point insufficient balance",
            confidence=0.6,
            occurrence_count=2,  # x < 3 → 1.0배
        )

        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        weight = shadow_calculator._get_pattern_weight("point", "insufficient_balance")
        assert weight == pytest.approx(1.0, rel=1e-6)

    def test_pattern_weight_service_failure_returns_default(self, shadow_calculator, monkeypatch):
        """Learning 서비스 장애 시 기본값 1.0 반환."""
        class MockLearningService:
            def __init__(self):
                raise RuntimeError("Service unavailable")

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        assert weight == 1.0


class TestPatternWeightIntegration:
    """Pattern 가중치 통합 테스트."""

    def test_calculate_weighted_errors_with_pattern(self, shadow_calculator, monkeypatch):
        """_calculate_weighted_errors에 pattern 적용."""
        from selfhealing.services.learning.models import LearningPattern, PatternType

        mock_pattern = LearningPattern(
            pattern_id="test-5",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,  # 2배 가중치
        )

        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]

        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )

        # medium 10개 * 0.001 * prometheus(1.0) * pattern(2.0) = 0.02분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        # domain 가중치는 config가 없으면 1.0이므로 pattern만 적용
        assert result == pytest.approx(0.02, rel=1e-3)
