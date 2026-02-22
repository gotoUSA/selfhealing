"""
통합 시나리오 테스트.
"""

from unittest.mock import Mock


class TestReconciliationScenarios:
    """통합 시나리오 테스트."""

    def test_full_reconciliation_flow(self, mock_current_budget):
        """전체 Reconciliation 플로우 테스트."""
        from selfhealing.services.error_budget.reconciliation import (
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
            ShadowBudgetCalculator,
        )

        # 1. 서비스 설정
        mock_apply = Mock()
        mock_prometheus = Mock(return_value=500)

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                auto_calculate=True,
                max_adjustment_percent_per_cycle=10.0,
                auto_exclude_short_periods=False,  # 테스트에서 비활성화
            ),
            shadow_calculator=ShadowBudgetCalculator(
                get_prometheus_errors=mock_prometheus,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)
        service._apply_adjustment = mock_apply

        # 2. Fail-Safe 발동
        period = service.start_failsafe_period(
            reason="Redis cluster failover",
            component="error_budget_gate",
        )
        assert period.is_active is True

        # 3. 일정 시간 후 복구 (여기서는 즉시)
        shadow = service.end_failsafe_period()
        assert shadow is not None
        assert shadow.log_source == "prometheus"

        # 4. 운영자 리뷰 후 승인
        pending = service.get_pending_shadow_budgets()
        assert len(pending) == 1

        approved = service.approve_shadow_budget(
            calculation_id=shadow.calculation_id,
            approved_by="sre_oncall",
            justification="프로메테우스 로그 확인 완료, 에러 500건 확인",
        )

        assert approved.status == ReconciliationStatus.APPLIED
        assert mock_apply.called

    def test_chaos_engineering_exclusion_flow(self, mock_current_budget):
        """Chaos Engineering 기간 제외 플로우."""
        from selfhealing.services.error_budget.reconciliation import (
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                auto_exclude_short_periods=False,  # 테스트에서 비활성화
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # 1. Chaos 실험 중 Fail-Safe 발동
        period = service.start_failsafe_period(reason="Network partition injection")
        shadow = service.end_failsafe_period()

        # 2. 운영자가 Chaos 실험임을 확인하고 거부
        rejected = service.reject_shadow_budget(
            calculation_id=shadow.calculation_id,
            rejected_by="chaos_engineer",
            reason="계획된 Chaos Engineering 실험 (네트워크 파티션 테스트)",
        )

        assert rejected.status == ReconciliationStatus.REJECTED

        # 3. Excluded Period가 생성됨
        excluded = service.get_excluded_periods()
        assert len(excluded) == 1
        assert "Chaos Engineering" in excluded[0].reason

    def test_multiple_short_failsafe_auto_excluded(self, mock_current_budget):
        """여러 짧은 Fail-Safe 기간 자동 제외."""
        from selfhealing.services.error_budget.reconciliation import (
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                auto_exclude_short_periods=True,
                short_period_threshold_seconds=30.0,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # 여러 번 짧은 Fail-Safe 발생 (즉시 복구)
        for i in range(5):
            period = service.start_failsafe_period(reason=f"Quick recovery {i}")
            service.end_failsafe_period()

        # 모두 자동 제외
        excluded = service.get_excluded_periods()
        pending = service.get_pending_shadow_budgets()

        assert len(excluded) == 5
        assert len(pending) == 0
