"""
ErrorBudgetReconciliationService 핵심 기능 테스트.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock


class TestReconciliationService:
    """ErrorBudgetReconciliationService 테스트."""

    def test_start_failsafe_period(self, reconciliation_service):
        """Fail-Safe 시작 이벤트 처리."""
        period = reconciliation_service.start_failsafe_period(
            reason="Redis timeout",
            component="error_budget_gate",
        )
        
        assert period is not None
        assert period.is_active is True

    def test_end_failsafe_period_auto_calculate(self, reconciliation_service, mock_current_budget):
        """Fail-Safe 종료 시 자동 계산."""
        from selfhealing.services.error_budget.reconciliation import ReconciliationStatus
        
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Test")
        shadow = reconciliation_service.end_failsafe_period()
        
        assert shadow is not None
        assert shadow.status == ReconciliationStatus.CALCULATED

    def test_end_failsafe_period_short_period_excluded(self, period_tracker):
        """짧은 기간 자동 제외."""
        from selfhealing.services.error_budget.reconciliation import (
            ErrorBudgetReconciliationService,
            ReconciliationConfig,
        )
        
        # 이 테스트는 자동 제외를 활성화해야 함
        service = ErrorBudgetReconciliationService(
            config=ReconciliationConfig(
                auto_exclude_short_periods=True,
                short_period_threshold_seconds=120.0,  # 2분
            ),
            period_tracker=period_tracker,
        )
        
        period = service.start_failsafe_period(reason="Quick recovery")
        # 즉시 종료 (1초 미만)
        result = service.end_failsafe_period()
        
        # 짧은 기간은 자동 제외되어 Shadow Budget 없음
        assert result is None
        
        excluded = service.get_excluded_periods()
        assert len(excluded) == 1
        assert "Auto-excluded" in excluded[0].reason

    def test_get_pending_shadow_budgets(self, reconciliation_service, mock_current_budget):
        """승인 대기 Shadow Budget 조회."""
        from selfhealing.services.error_budget.reconciliation import ReconciliationStatus
        
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        # 2개 기간 생성
        period1 = reconciliation_service.start_failsafe_period(reason="First")
        reconciliation_service.end_failsafe_period()
        period2 = reconciliation_service.start_failsafe_period(reason="Second")
        reconciliation_service.end_failsafe_period()
        
        pending = reconciliation_service.get_pending_shadow_budgets()
        
        assert len(pending) == 2
        assert all(sb.status == ReconciliationStatus.CALCULATED for sb in pending)

    def test_approve_shadow_budget(self, reconciliation_service, mock_current_budget):
        """Shadow Budget 승인."""
        from selfhealing.services.error_budget.reconciliation import ReconciliationStatus
        
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Test")
        shadow = reconciliation_service.end_failsafe_period()
        
        approved = reconciliation_service.approve_shadow_budget(
            calculation_id=shadow.calculation_id,
            approved_by="ops_lead",
            justification="로그 확인 완료",
        )
        
        assert approved.status == ReconciliationStatus.APPLIED
        assert approved.reviewed_by == "ops_lead"
        assert approved.review_justification == "로그 확인 완료"

    def test_approve_with_capped_mode(self, reconciliation_service, mock_current_budget):
        """Capped 모드로 승인 시 최대 조정량 제한."""
        mock_apply = Mock()
        reconciliation_service._apply_adjustment = mock_apply
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        reconciliation_service._config.max_adjustment_percent_per_cycle = 5.0
        
        # Shadow Budget 생성 (조정량 > 5%)
        period = reconciliation_service.start_failsafe_period(reason="Test")
        shadow = reconciliation_service.end_failsafe_period()
        
        # 강제로 큰 조정량 설정
        shadow.adjustment_percent = 15.0
        
        reconciliation_service.approve_shadow_budget(
            calculation_id=shadow.calculation_id,
            approved_by="admin",
            justification="Approve with cap",
        )
        
        # 최대 5%만 적용되어야 함
        mock_apply.assert_called_once_with(5.0)

    def test_reject_shadow_budget(self, reconciliation_service, mock_current_budget):
        """Shadow Budget 거부 (Excluded Period 생성)."""
        from selfhealing.services.error_budget.reconciliation import ReconciliationStatus
        
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Chaos experiment")
        shadow = reconciliation_service.end_failsafe_period()
        
        rejected = reconciliation_service.reject_shadow_budget(
            calculation_id=shadow.calculation_id,
            rejected_by="sre_lead",
            reason="Chaos Engineering 실험 기간",
        )
        
        assert rejected.status == ReconciliationStatus.REJECTED
        
        excluded = reconciliation_service.get_excluded_periods()
        assert len(excluded) == 1
        assert excluded[0].reason == "Chaos Engineering 실험 기간"

    def test_approve_invalid_status(self, reconciliation_service, mock_current_budget):
        """이미 처리된 Shadow Budget 승인 시도."""
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Test")
        shadow = reconciliation_service.end_failsafe_period()
        
        # 첫 번째 승인
        reconciliation_service.approve_shadow_budget(
            calculation_id=shadow.calculation_id,
            approved_by="admin",
            justification="First",
        )
        
        # 두 번째 승인 시도
        result = reconciliation_service.approve_shadow_budget(
            calculation_id=shadow.calculation_id,
            approved_by="admin",
            justification="Second",
        )
        
        assert result is None

    def test_exclude_period_manually(self, reconciliation_service):
        """수동 기간 제외."""
        now = datetime.now(timezone.utc)
        
        exclusion = reconciliation_service.exclude_period(
            start=now - timedelta(hours=2),
            end=now - timedelta(hours=1),
            reason="Planned maintenance",
            excluded_by="ops_lead",
            notes="월간 정기 점검",
        )
        
        assert exclusion is not None
        assert exclusion.reason == "Planned maintenance"
        assert "월간 정기 점검" in exclusion.notes

    def test_remove_exclusion(self, reconciliation_service):
        """제외 기간 삭제."""
        now = datetime.now(timezone.utc)
        
        exclusion = reconciliation_service.exclude_period(
            start=now - timedelta(hours=1),
            end=now,
            reason="Test",
            excluded_by="admin",
        )
        
        result = reconciliation_service.remove_exclusion(exclusion.exclusion_id)
        assert result is True
        
        excluded = reconciliation_service.get_excluded_periods()
        assert len(excluded) == 0

    def test_remove_nonexistent_exclusion(self, reconciliation_service):
        """존재하지 않는 제외 기간 삭제."""
        result = reconciliation_service.remove_exclusion("nonexistent-id")
        assert result is False

    def test_get_status(self, reconciliation_service, mock_current_budget):
        """전체 상태 조회."""
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Test")
        reconciliation_service.end_failsafe_period()
        
        status = reconciliation_service.get_status()
        
        assert "enabled" in status
        assert "period_tracker" in status
        assert "shadow_budgets" in status
        assert "config" in status

    def test_update_config(self, reconciliation_service):
        """설정 업데이트."""
        config = reconciliation_service.update_config(
            auto_apply=True,
            max_adjustment_percent_per_cycle=15.0,
        )
        
        assert config.auto_apply is True
        assert config.max_adjustment_percent_per_cycle == 15.0
