"""
ShadowBudgetCalculator 테스트.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock


class TestShadowBudgetCalculator:
    """ShadowBudgetCalculator 테스트."""

    def test_calculate_shadow_budget_no_data_source(self, shadow_calculator):
        """데이터 소스 없을 때 계산."""
        from selfhealing.services.error_budget.reconciliation import (
            FailSafePeriod,
            ReconciliationStatus,
        )

        now = datetime.now(timezone.utc)
        period = FailSafePeriod(
            period_id="test-period",
            started_at=now - timedelta(hours=1),
            ended_at=now,
            is_active=False,
        )

        result = shadow_calculator.calculate_shadow_budget(
            failsafe_period=period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
        )

        assert result is not None
        assert result.status == ReconciliationStatus.CALCULATED
        assert result.log_source == "none_available"

    def test_calculate_with_prometheus_source(self):
        """Prometheus 소스로 계산."""
        from selfhealing.services.error_budget.reconciliation import (
            FailSafePeriod,
            ShadowBudgetCalculator,
        )

        mock_prometheus = Mock(return_value=500)
        calculator = ShadowBudgetCalculator(get_prometheus_errors=mock_prometheus)

        now = datetime.now(timezone.utc)
        period = FailSafePeriod(
            period_id="test-period",
            started_at=now - timedelta(hours=1),
            ended_at=now,
            is_active=False,
        )

        result = calculator.calculate_shadow_budget(
            failsafe_period=period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
        )

        assert result.estimated_errors == 500
        assert result.log_source == "prometheus"
        mock_prometheus.assert_called_once()

    def test_calculate_with_dlq_source(self):
        """DLQ 소스로 계산."""
        from selfhealing.services.error_budget.reconciliation import (
            FailSafePeriod,
            ShadowBudgetCalculator,
        )

        mock_dlq = Mock(return_value=200)
        calculator = ShadowBudgetCalculator(get_dlq_entries=mock_dlq)

        now = datetime.now(timezone.utc)
        period = FailSafePeriod(
            period_id="test-period",
            started_at=now - timedelta(hours=1),
            ended_at=now,
            is_active=False,
        )

        result = calculator.calculate_shadow_budget(
            failsafe_period=period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
        )

        assert result.estimated_errors == 200
        assert result.log_source == "dlq"

    def test_source_priority_prometheus_first(self):
        """데이터 소스 우선순위: Prometheus > DLQ > Logs."""
        from selfhealing.services.error_budget.reconciliation import (
            FailSafePeriod,
            ShadowBudgetCalculator,
        )

        mock_prometheus = Mock(return_value=100)
        mock_dlq = Mock(return_value=50)
        mock_logs = Mock(return_value=[{}, {}, {}])

        calculator = ShadowBudgetCalculator(
            get_prometheus_errors=mock_prometheus,
            get_dlq_entries=mock_dlq,
            get_error_logs=mock_logs,
        )

        now = datetime.now(timezone.utc)
        period = FailSafePeriod(
            period_id="test-period",
            started_at=now - timedelta(hours=1),
            ended_at=now,
            is_active=False,
        )

        result = calculator.calculate_shadow_budget(
            failsafe_period=period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
        )

        assert result.log_source == "prometheus"
        mock_prometheus.assert_called_once()
        mock_dlq.assert_not_called()
        mock_logs.assert_not_called()
