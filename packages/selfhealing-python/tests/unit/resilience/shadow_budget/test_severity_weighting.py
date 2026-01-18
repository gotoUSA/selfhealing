"""
Severity 기반 가중치 테스트.

CRITICAL, HIGH, MEDIUM, LOW 가중치 테스트.
"""

import pytest


class TestSeverityWeightConstants:
    """Severity 상수 정의 테스트."""

    def test_critical_severity_weight(self):
        """CRITICAL은 0.01분 (10배 가중치)."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SEVERITY_WEIGHT,
        )
        assert SEVERITY_WEIGHT["critical"] == 0.01

    def test_high_severity_weight(self):
        """HIGH는 0.005분 (5배 가중치)."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SEVERITY_WEIGHT,
        )
        assert SEVERITY_WEIGHT["high"] == 0.005

    def test_medium_severity_weight(self):
        """MEDIUM은 0.001분 (기본값)."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SEVERITY_WEIGHT,
        )
        assert SEVERITY_WEIGHT["medium"] == 0.001

    def test_low_severity_weight(self):
        """LOW는 0.0005분 (절반 가중치)."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            SEVERITY_WEIGHT,
        )
        assert SEVERITY_WEIGHT["low"] == 0.0005


class TestSeverityWeighting:
    """Severity 기반 가중치 테스트."""

    def test_critical_severity_10x_weight(self, shadow_calculator):
        """CRITICAL은 기본값 대비 10배 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
        )
        # 10 * 0.01 * 1.0 = 0.1분
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_high_severity_5x_weight(self, shadow_calculator):
        """HIGH는 기본값 대비 5배 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"high": 10},
            log_source="prometheus",
        )
        # 10 * 0.005 * 1.0 = 0.05분
        assert result == pytest.approx(0.05, rel=1e-6)

    def test_medium_severity_base_weight(self, shadow_calculator):
        """MEDIUM은 기본 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
        )
        # 10 * 0.001 * 1.0 = 0.01분
        assert result == pytest.approx(0.01, rel=1e-6)

    def test_low_severity_half_weight(self, shadow_calculator):
        """LOW는 기본값 대비 절반 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"low": 10},
            log_source="prometheus",
        )
        # 10 * 0.0005 * 1.0 = 0.005분
        assert result == pytest.approx(0.005, rel=1e-6)

    def test_unknown_severity_defaults_to_base(self, shadow_calculator):
        """알 수 없는 severity는 기본 가중치 사용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"unknown": 10},
            log_source="prometheus",
        )
        # 10 * 0.001 * 1.0 = 0.01분
        assert result == pytest.approx(0.01, rel=1e-6)

    def test_case_insensitive_severity(self, shadow_calculator):
        """Severity는 대소문자 구분 없음."""
        result_lower = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
        )
        result_upper = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"CRITICAL": 10},
            log_source="prometheus",
        )
        assert result_lower == result_upper


class TestMixedSeverityWeighting:
    """혼합 Severity 가중치 테스트."""

    def test_mixed_severity_calculation(self, shadow_calculator):
        """여러 severity 혼합 계산."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 5,   # 5 * 0.01 = 0.05
                "high": 10,      # 10 * 0.005 = 0.05
                "medium": 20,    # 20 * 0.001 = 0.02
                "low": 50,       # 50 * 0.0005 = 0.025
            },
            log_source="prometheus",
        )
        # 총합: 0.05 + 0.05 + 0.02 + 0.025 = 0.145분
        assert result == pytest.approx(0.145, rel=1e-6)

    def test_mixed_severity_with_source_reliability(self, shadow_calculator):
        """혼합 severity + source reliability 조합."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 10,  # 10 * 0.01 = 0.1
                "medium": 100,   # 100 * 0.001 = 0.1
            },
            log_source="dlq",  # 0.9 reliability
        )
        # (0.1 + 0.1) * 0.9 = 0.18분
        assert result == pytest.approx(0.18, rel=1e-6)

    def test_empty_errors_returns_zero(self, shadow_calculator):
        """에러가 없으면 0 반환."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={},
            log_source="prometheus",
        )
        assert result == 0.0
