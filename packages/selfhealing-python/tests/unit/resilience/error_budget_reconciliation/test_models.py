"""
FailSafePeriod 및 ShadowBudget 데이터 모델 테스트.
"""

from datetime import datetime, timedelta, timezone


class TestFailSafePeriodModel:
    """FailSafePeriod 데이터 모델 테스트."""

    def test_create_failsafe_period(self):
        """FailSafePeriod 생성 테스트."""
        from selfhealing.services.error_budget.reconciliation import FailSafePeriod

        period = FailSafePeriod(
            period_id="test-period-1",
            started_at=datetime.now(timezone.utc),
            trigger_reason="Redis connection timeout",
            trigger_component="error_budget_gate",
        )

        assert period.period_id == "test-period-1"
        assert period.is_active is True
        assert period.ended_at is None
        assert period.fail_open_count == 0

    def test_duration_calculation(self):
        """기간 계산 테스트."""
        from selfhealing.services.error_budget.reconciliation import FailSafePeriod

        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        end = datetime.now(timezone.utc)

        period = FailSafePeriod(
            period_id="test-period-2",
            started_at=start,
            ended_at=end,
            is_active=False,
        )

        assert 4.9 <= period.duration_minutes <= 5.1

    def test_to_dict(self):
        """to_dict 변환 테스트."""
        from selfhealing.services.error_budget.reconciliation import FailSafePeriod

        period = FailSafePeriod(
            period_id="test-period-3",
            started_at=datetime.now(timezone.utc),
            trigger_reason="DB timeout",
        )

        result = period.to_dict()

        assert "period_id" in result
        assert "started_at" in result
        assert "trigger_reason" in result
        assert result["is_active"] is True


class TestShadowBudgetModel:
    """ShadowBudget 데이터 모델 테스트."""

    def test_create_shadow_budget(self):
        """ShadowBudget 생성 테스트."""
        from selfhealing.services.error_budget.reconciliation import (
            ReconciliationStatus,
            ShadowBudget,
        )

        now = datetime.now(timezone.utc)
        shadow = ShadowBudget(
            calculation_id="calc-1",
            calculated_at=now,
            failsafe_period_id="period-1",
            failsafe_period_start=now - timedelta(hours=1),
            failsafe_period_end=now,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            shadow_remaining_percent=70.0,
            shadow_consumed_minutes=12.96,
            adjustment_percent=5.0,
            adjustment_minutes=2.16,
            estimated_errors=1000,
            log_source="prometheus",
            status=ReconciliationStatus.CALCULATED,
        )

        assert shadow.adjustment_percent == 5.0
        assert shadow.status == ReconciliationStatus.CALCULATED

    def test_to_dict(self):
        """to_dict 변환 테스트."""
        from selfhealing.services.error_budget.reconciliation import ShadowBudget

        now = datetime.now(timezone.utc)
        shadow = ShadowBudget(
            calculation_id="calc-2",
            calculated_at=now,
            failsafe_period_id="period-1",
            failsafe_period_start=now - timedelta(hours=1),
            failsafe_period_end=now,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            shadow_remaining_percent=70.0,
            shadow_consumed_minutes=12.96,
            adjustment_percent=5.0,
            adjustment_minutes=2.16,
        )

        result = shadow.to_dict()

        assert "calculation_id" in result
        assert "primary_budget" in result
        assert "shadow_budget" in result
        assert "adjustment" in result
