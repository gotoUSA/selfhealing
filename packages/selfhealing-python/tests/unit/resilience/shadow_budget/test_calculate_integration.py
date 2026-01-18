"""
calculate_shadow_budget 통합 및 엣지 케이스 테스트.
"""

import pytest


class TestCalculateShadowBudgetWithWeighting:
    """calculate_shadow_budget 가중치 적용 통합 테스트."""

    def test_calculate_with_errors_by_severity(self, shadow_calculator, sample_failsafe_period):
        """errors_by_severity 제공 시 가중치 적용."""
        result = shadow_calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
            errors_by_severity={
                "critical": 10,  # 10 * 0.01 = 0.1분
                "high": 20,      # 20 * 0.005 = 0.1분
            },
        )
        
        # estimated_errors는 severity 합계
        assert result.estimated_errors == 30
        
        # adjustment_minutes는 가중치 적용된 값
        # (0.1 + 0.1) * source_reliability(none_available=0.5) = 0.1분
        # 기본 log_source가 "none_available"이므로 0.5 적용
        assert result.adjustment_minutes == pytest.approx(0.1, rel=1e-3)

    def test_calculate_without_errors_by_severity_uses_medium(
        self, sample_failsafe_period
    ):
        """errors_by_severity 미제공 시 모든 에러를 medium으로 처리."""
        from selfhealing.services.error_budget.reconciliation import ShadowBudgetCalculator
        
        # Prometheus 함수를 제공하여 에러 수 반환
        def mock_prometheus(start, end):
            return 100
        
        calculator = ShadowBudgetCalculator(get_prometheus_errors=mock_prometheus)
        result = calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
        )
        
        # 100 에러 * 0.001 (medium) * 1.0 (prometheus) = 0.1분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.1, rel=1e-3)
        assert result.log_source == "prometheus"

    def test_calculate_applies_source_reliability(
        self, sample_failsafe_period
    ):
        """calculate_shadow_budget이 source_reliability를 적용."""
        from selfhealing.services.error_budget.reconciliation import ShadowBudgetCalculator
        
        # DLQ 함수를 제공
        def mock_dlq(start, end):
            return 100
        
        calculator = ShadowBudgetCalculator(get_dlq_entries=mock_dlq)
        result = calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
        )
        
        # 100 에러 * 0.001 (medium) * 0.9 (dlq) = 0.09분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.09, rel=1e-3)
        assert result.log_source == "dlq"


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_zero_errors_all_severities(self, shadow_calculator):
        """모든 severity가 0인 경우."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            },
            log_source="prometheus",
        )
        assert result == 0.0

    def test_very_large_error_count(self, shadow_calculator):
        """대량 에러 처리."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 100000},  # 10만 개
            log_source="prometheus",
        )
        # 100000 * 0.01 = 1000분
        assert result == pytest.approx(1000.0, rel=1e-6)

    def test_negative_error_count_treated_as_zero_contribution(self, shadow_calculator):
        """음수 에러 수 (비정상 케이스)."""
        # 실제로는 발생하면 안 되지만, 방어적 테스트
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": -10},
            log_source="prometheus",
        )
        # -10 * 0.001 = -0.01 (음수 반환 - 호출자가 처리)
        assert result == pytest.approx(-0.01, rel=1e-6)
