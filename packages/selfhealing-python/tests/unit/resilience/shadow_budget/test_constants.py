"""
가중치 상수 및 Source Reliability 테스트.

핵심 설계 원칙 (Cap, Source Reliability) 테스트.
"""

import pytest


class TestWeightedBudgetConstants:
    """가중치 상수 정의 테스트."""

    def test_max_weight_multiplier_defined(self):
        """MAX_WEIGHT_MULTIPLIER가 50.0으로 정의되어 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            MAX_WEIGHT_MULTIPLIER,
        )
        assert MAX_WEIGHT_MULTIPLIER == 50.0

    def test_source_reliability_prometheus_highest(self):
        """Prometheus가 가장 높은 신뢰도(1.0)를 가져야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SOURCE_RELIABILITY,
        )
        assert SOURCE_RELIABILITY["prometheus"] == 1.0

    def test_source_reliability_dlq(self):
        """DLQ 신뢰도는 0.9."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SOURCE_RELIABILITY,
        )
        assert SOURCE_RELIABILITY["dlq"] == 0.9

    def test_source_reliability_application_logs(self):
        """Application logs 신뢰도는 0.8."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SOURCE_RELIABILITY,
        )
        assert SOURCE_RELIABILITY["application_logs"] == 0.8

    def test_source_reliability_none_available_lowest(self):
        """데이터 없을 때 가장 낮은 신뢰도(0.5)."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SOURCE_RELIABILITY,
        )
        assert SOURCE_RELIABILITY["none_available"] == 0.5

    def test_base_weight_minutes(self):
        """기본 가중치는 0.001분."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            BASE_WEIGHT_MINUTES,
        )
        assert BASE_WEIGHT_MINUTES == 0.001


class TestSourceReliabilityWeight:
    """Source Reliability 가중치 적용 테스트."""

    def test_prometheus_source_full_weight(self, shadow_calculator):
        """Prometheus 소스는 100% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="prometheus",
        )
        # 100 * 0.001 * 1.0 = 0.1
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_dlq_source_90_percent_weight(self, shadow_calculator):
        """DLQ 소스는 90% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="dlq",
        )
        # 100 * 0.001 * 0.9 = 0.09
        assert result == pytest.approx(0.09, rel=1e-6)

    def test_application_logs_source_80_percent_weight(self, shadow_calculator):
        """Application logs 소스는 80% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="application_logs",
        )
        # 100 * 0.001 * 0.8 = 0.08
        assert result == pytest.approx(0.08, rel=1e-6)

    def test_none_available_source_50_percent_weight(self, shadow_calculator):
        """데이터 없을 때 50% 가중치 적용 (보수적)."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="none_available",
        )
        # 100 * 0.001 * 0.5 = 0.05
        assert result == pytest.approx(0.05, rel=1e-6)

    def test_unknown_source_defaults_to_full_weight(self, shadow_calculator):
        """알 수 없는 소스는 100% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="unknown_source",
        )
        # 100 * 0.001 * 1.0 = 0.1
        assert result == pytest.approx(0.1, rel=1e-6)
