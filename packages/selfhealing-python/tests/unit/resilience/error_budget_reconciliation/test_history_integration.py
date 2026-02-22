"""
Reconciliation과 ConfigHistory 연동 테스트.

Shadow Budget 승인 시 ConfigHistory에 자동 저장.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


class TestReconciliationHistoryIntegration:
    """
    Reconciliation과 ConfigHistory 연동 테스트.
    """

    def test_approve_saves_to_history(self, mock_current_budget):
        """Shadow Budget 승인 시 ConfigHistory에 저장."""
        from selfhealing.services.error_budget.reconciliation import (
            ApplyMode,
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                apply_mode=ApplyMode.IMMEDIATE,
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Fail-Safe 발생 및 종료
        period = service.start_failsafe_period(reason="Test failure")
        shadow = service.end_failsafe_period()

        # ConfigHistoryService mock
        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_history_service = Mock()
            mock_get_service.return_value = mock_history_service

            # 승인
            approved = service.approve_shadow_budget(
                calculation_id=shadow.calculation_id,
                approved_by="ops_admin",
                justification="로그 확인 완료",
            )

            # 검증
            assert approved.status == ReconciliationStatus.APPLIED
            mock_history_service.save_version.assert_called_once()

            call_args = mock_history_service.save_version.call_args
            assert call_args.kwargs["config_type"] == "error_budget"
            assert call_args.kwargs["changed_by"] == "ops_admin"
            assert "Shadow Budget Reconciliation" in call_args.kwargs["reason"]

            values = call_args.kwargs["values"]
            assert values["reconciliation_id"] == shadow.calculation_id
            assert values["failsafe_period_id"] == shadow.failsafe_period_id
            assert "adjustment_percent" in values
            assert "apply_mode" in values

    def test_approve_with_capped_mode_saves_correct_adjustment(self, mock_current_budget):
        """Capped 모드에서 조정된 adjustment가 history에 저장됨."""
        from selfhealing.services.error_budget.reconciliation import (
            ApplyMode,
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
            ShadowBudget,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                apply_mode=ApplyMode.CAPPED,
                max_adjustment_percent_per_cycle=5.0,
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Shadow Budget 직접 생성 (Capped 테스트를 위해)
        mock_shadow = ShadowBudget(
            calculation_id="test-calc",
            calculated_at=datetime.now(timezone.utc),
            failsafe_period_id="test-period",
            failsafe_period_start=datetime.now(timezone.utc) - timedelta(hours=1),
            failsafe_period_end=datetime.now(timezone.utc),
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            shadow_remaining_percent=55.0,  # 20% 차이
            shadow_consumed_minutes=19.44,
            adjustment_percent=20.0,  # 큰 adjustment
            adjustment_minutes=8.64,
            estimated_errors=100,
            log_source="prometheus",
            status=ReconciliationStatus.CALCULATED,
        )

        # Shadow Budget 직접 등록
        service._shadow_budgets[mock_shadow.calculation_id] = mock_shadow

        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_history_service = Mock()
            mock_get_service.return_value = mock_history_service

            # 승인
            approved = service.approve_shadow_budget(
                calculation_id=mock_shadow.calculation_id,
                approved_by="ops_lead",
                justification="Capped adjustment test",
            )

            # 검증 - Capped된 값이 저장됨
            call_args = mock_history_service.save_version.call_args
            values = call_args.kwargs["values"]
            assert values["adjustment_percent"] == 5.0  # max_adjustment_percent_per_cycle
            assert values["apply_mode"] == "capped"

    def test_history_save_failure_does_not_break_approval(self, mock_current_budget):
        """History 저장 실패해도 승인은 성공."""
        from selfhealing.services.error_budget.reconciliation import (
            ApplyMode,
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                apply_mode=ApplyMode.IMMEDIATE,
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Fail-Safe 발생 및 종료
        period = service.start_failsafe_period(reason="Test failure")
        shadow = service.end_failsafe_period()

        # ConfigHistoryService mock - 예외 발생
        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_history_service = Mock()
            mock_history_service.save_version.side_effect = Exception("Redis connection failed")
            mock_get_service.return_value = mock_history_service

            # 승인 - 예외가 발생해도 성공해야 함
            approved = service.approve_shadow_budget(
                calculation_id=shadow.calculation_id,
                approved_by="ops_admin",
                justification="Should succeed despite history failure",
            )

            # 검증 - 승인은 성공
            assert approved is not None
            assert approved.status == ReconciliationStatus.APPLIED

    def test_approve_saves_primary_before_and_after(self, mock_current_budget):
        """승인 시 Primary Budget의 변경 전/후 값이 저장됨."""
        from selfhealing.services.error_budget.reconciliation import (
            ApplyMode,
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                apply_mode=ApplyMode.IMMEDIATE,
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Fail-Safe 발생 및 종료
        period = service.start_failsafe_period(reason="Test failure")
        shadow = service.end_failsafe_period()

        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_history_service = Mock()
            mock_get_service.return_value = mock_history_service

            # 승인
            service.approve_shadow_budget(
                calculation_id=shadow.calculation_id,
                approved_by="ops_admin",
                justification="Test",
            )

            # 검증
            call_args = mock_history_service.save_version.call_args
            values = call_args.kwargs["values"]

            assert "primary_remaining_before" in values
            assert "primary_remaining_after" in values
            # before - adjustment = after
            expected_after = values["primary_remaining_before"] - values["adjustment_percent"]
            assert abs(values["primary_remaining_after"] - expected_after) < 0.01

    def test_history_service_import_failure_graceful_degradation(self, mock_current_budget):
        """ConfigHistoryService import 실패 시 Graceful Degradation."""
        from selfhealing.services.error_budget.reconciliation import (
            ApplyMode,
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                apply_mode=ApplyMode.IMMEDIATE,
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Fail-Safe 발생 및 종료
        period = service.start_failsafe_period(reason="Test failure")
        shadow = service.end_failsafe_period()

        # Import 실패 시뮬레이션
        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_get_service.side_effect = ImportError("Module not found")

            # 승인 - import 실패해도 성공해야 함
            approved = service.approve_shadow_budget(
                calculation_id=shadow.calculation_id,
                approved_by="ops_admin",
                justification="Should succeed despite import failure",
            )

            # 검증 - 승인은 성공
            assert approved is not None
            assert approved.status == ReconciliationStatus.APPLIED

    def test_reject_does_not_save_to_history(self, mock_current_budget):
        """거부(Reject) 시에는 ConfigHistory에 저장하지 않음."""
        from selfhealing.services.error_budget.reconciliation import (
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
            ReconciliationStatus,
        )

        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                auto_exclude_short_periods=False,
            ),
        )
        service._get_current_budget = Mock(return_value=mock_current_budget)

        # Fail-Safe 발생 및 종료
        period = service.start_failsafe_period(reason="Test failure")
        shadow = service.end_failsafe_period()

        with patch('selfhealing.services.config_history.get_config_history_service') as mock_get_service:
            mock_history_service = Mock()
            mock_get_service.return_value = mock_history_service

            # 거부
            rejected = service.reject_shadow_budget(
                calculation_id=shadow.calculation_id,
                rejected_by="ops_admin",
                reason="Chaos experiment - not real errors",
            )

            # 검증 - History에 저장하지 않음 (적용되지 않았으므로)
            mock_history_service.save_version.assert_not_called()
            assert rejected.status == ReconciliationStatus.REJECTED
