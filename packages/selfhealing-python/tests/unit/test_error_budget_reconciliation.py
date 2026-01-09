"""
Error Budget Reconciliation 단위 테스트.

테스트 대상:
- FailSafePeriodTracker: Fail-Safe 기간 추적
- ShadowBudgetCalculator: Shadow Budget 계산
- ErrorBudgetReconciliationService: 통합 Reconciliation 서비스

핵심 원칙: "시스템은 계산하고, 반영은 사람이 결정한다."

Reference: docs/self_healing/12_ERROR_BUDGET.md (Section 13)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from selfhealing.services.error_budget.reconciliation import (
    # Enums
    ReconciliationStatus,
    ApplyMode,
    # Data Models
    FailSafePeriod,
    ShadowBudget,
    ExcludedPeriod,
    # Classes
    FailSafePeriodTracker,
    ShadowBudgetCalculator,
    ReconciliationConfig,
    ErrorBudgetReconciliationService,
    # Factory
    get_period_tracker,
    get_reconciliation_service,
    configure_reconciliation_service,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def period_tracker():
    """Fresh FailSafePeriodTracker for each test."""
    return FailSafePeriodTracker(max_periods=10)


@pytest.fixture
def shadow_calculator():
    """Fresh ShadowBudgetCalculator for each test."""
    return ShadowBudgetCalculator()


@pytest.fixture
def reconciliation_service(period_tracker, shadow_calculator):
    """Fresh ErrorBudgetReconciliationService for each test."""
    # 테스트에서는 짧은 기간 자동 제외 비활성화
    config = ReconciliationConfig(
        auto_exclude_short_periods=False,
    )
    return ErrorBudgetReconciliationService(
        config=config,
        period_tracker=period_tracker,
        shadow_calculator=shadow_calculator,
    )


@pytest.fixture
def mock_current_budget():
    """Mock for current budget status."""
    return {
        "remaining_percent": 75.0,
        "consumed_minutes": 10.8,
        "total_minutes": 43.2,
    }


# =============================================================================
# FailSafePeriod Model Tests
# =============================================================================


class TestFailSafePeriodModel:
    """FailSafePeriod 데이터 모델 테스트."""

    def test_create_failsafe_period(self):
        """FailSafePeriod 생성 테스트."""
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


# =============================================================================
# ShadowBudget Model Tests
# =============================================================================


class TestShadowBudgetModel:
    """ShadowBudget 데이터 모델 테스트."""

    def test_create_shadow_budget(self):
        """ShadowBudget 생성 테스트."""
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


# =============================================================================
# FailSafePeriodTracker Tests
# =============================================================================


class TestFailSafePeriodTracker:
    """FailSafePeriodTracker 테스트."""

    def test_start_period(self, period_tracker):
        """Fail-Safe 기간 시작 테스트."""
        period = period_tracker.start_period(
            reason="Redis timeout",
            component="error_budget_gate",
        )
        
        assert period is not None
        assert period.is_active is True
        assert period.trigger_reason == "Redis timeout"
        
    def test_end_period(self, period_tracker):
        """Fail-Safe 기간 종료 테스트."""
        period_tracker.start_period(reason="Test")
        ended = period_tracker.end_period()
        
        assert ended is not None
        assert ended.is_active is False
        assert ended.ended_at is not None

    def test_end_period_when_no_active(self, period_tracker):
        """활성 기간 없을 때 종료 시도."""
        result = period_tracker.end_period()
        assert result is None

    def test_auto_end_previous_on_new_start(self, period_tracker):
        """새 기간 시작 시 기존 기간 자동 종료."""
        period1 = period_tracker.start_period(reason="First")
        period2 = period_tracker.start_period(reason="Second")
        
        assert period1.is_active is False
        assert period2.is_active is True

    def test_record_fail_open(self, period_tracker):
        """Fail-Open 카운트 기록."""
        period_tracker.start_period(reason="Test")
        period_tracker.record_fail_open()
        period_tracker.record_fail_open()
        period_tracker.record_fail_open()
        
        active = period_tracker.get_active_period()
        assert active.fail_open_count == 3

    def test_record_rate_limit_exceeded(self, period_tracker):
        """Rate Limit 초과 카운트 기록."""
        period_tracker.start_period(reason="Test")
        period_tracker.record_rate_limit_exceeded()
        period_tracker.record_rate_limit_exceeded()
        
        active = period_tracker.get_active_period()
        assert active.rate_limit_exceeded_count == 2

    def test_get_unreconciled_periods(self, period_tracker):
        """Reconciliation 대상 기간 조회."""
        # 3개 기간 생성 후 종료
        period_tracker.start_period(reason="First")
        period_tracker.end_period()
        period_tracker.start_period(reason="Second")
        period_tracker.end_period()
        period_tracker.start_period(reason="Third")  # 활성 상태
        
        unreconciled = period_tracker.get_unreconciled_periods()
        
        assert len(unreconciled) == 2  # 종료된 2개만

    def test_max_periods_limit(self, period_tracker):
        """최대 기간 수 제한 테스트."""
        # 15개 기간 생성 (max_periods=10)
        for i in range(15):
            period_tracker.start_period(reason=f"Period-{i}")
            period_tracker.end_period()
        
        all_periods = period_tracker.get_all_periods(limit=100)
        assert len(all_periods) == 10

    def test_get_status(self, period_tracker):
        """상태 조회 테스트."""
        period_tracker.start_period(reason="Test")
        status = period_tracker.get_status()
        
        assert "active_period" in status
        assert "total_periods" in status
        assert status["active_period"] is not None


# =============================================================================
# ShadowBudgetCalculator Tests
# =============================================================================


class TestShadowBudgetCalculator:
    """ShadowBudgetCalculator 테스트."""

    def test_calculate_shadow_budget_no_data_source(self, shadow_calculator):
        """데이터 소스 없을 때 계산."""
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


# =============================================================================
# ReconciliationConfig Tests
# =============================================================================


class TestReconciliationConfig:
    """ReconciliationConfig 테스트."""

    def test_default_config(self):
        """기본 설정 테스트."""
        config = ReconciliationConfig()
        
        assert config.enabled is True
        assert config.auto_calculate is True
        assert config.auto_apply is False  # 자동 적용 비활성화
        assert config.apply_mode == ApplyMode.CAPPED
        assert config.max_adjustment_percent_per_cycle == 10.0

    def test_to_dict(self):
        """to_dict 변환 테스트."""
        config = ReconciliationConfig(
            auto_apply=True,
            max_adjustment_percent_per_cycle=5.0,
        )
        
        result = config.to_dict()
        
        assert result["auto_apply"] is True
        assert result["max_adjustment_percent_per_cycle"] == 5.0

    def test_from_dict(self):
        """from_dict 복원 테스트."""
        data = {
            "enabled": True,
            "auto_calculate": False,
            "apply_mode": "immediate",
            "max_adjustment_percent_per_cycle": 15.0,
        }
        
        config = ReconciliationConfig.from_dict(data)
        
        assert config.auto_calculate is False
        assert config.apply_mode == ApplyMode.IMMEDIATE
        assert config.max_adjustment_percent_per_cycle == 15.0


# =============================================================================
# ErrorBudgetReconciliationService Tests
# =============================================================================


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
        reconciliation_service._get_current_budget = Mock(return_value=mock_current_budget)
        
        period = reconciliation_service.start_failsafe_period(reason="Test")
        shadow = reconciliation_service.end_failsafe_period()
        
        assert shadow is not None
        assert shadow.status == ReconciliationStatus.CALCULATED

    def test_end_failsafe_period_short_period_excluded(self, period_tracker):
        """짧은 기간 자동 제외."""
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


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunctions:
    """팩토리 함수 테스트."""

    def test_get_period_tracker_singleton(self):
        """FailSafePeriodTracker 싱글톤 테스트."""
        # Reset singleton for test
        import selfhealing.services.error_budget.reconciliation as module
        module._period_tracker = None
        
        tracker1 = get_period_tracker()
        tracker2 = get_period_tracker()
        
        assert tracker1 is tracker2

    def test_get_reconciliation_service_singleton(self):
        """ErrorBudgetReconciliationService 싱글톤 테스트."""
        # Reset singleton for test
        import selfhealing.services.error_budget.reconciliation as module
        module._reconciliation_service = None
        module._period_tracker = None
        
        service1 = get_reconciliation_service()
        service2 = get_reconciliation_service()
        
        assert service1 is service2

    def test_configure_reconciliation_service(self):
        """configure_reconciliation_service 테스트."""
        # Reset singleton for test
        import selfhealing.services.error_budget.reconciliation as module
        module._reconciliation_service = None
        module._period_tracker = None
        
        mock_budget = Mock(return_value={"remaining_percent": 80.0})
        mock_apply = Mock()
        
        service = configure_reconciliation_service(
            config=ReconciliationConfig(auto_apply=True),
            get_current_budget=mock_budget,
            apply_adjustment=mock_apply,
        )
        
        assert service._config.auto_apply is True
        assert service._get_current_budget is mock_budget


# =============================================================================
# Integration Scenario Tests
# =============================================================================


class TestReconciliationScenarios:
    """통합 시나리오 테스트."""

    def test_full_reconciliation_flow(self, mock_current_budget):
        """전체 Reconciliation 플로우 테스트."""
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


# =============================================================================
# Reconciliation History Integration Tests
# =============================================================================


class TestReconciliationHistoryIntegration:
    """
    Reconciliation과 ConfigHistory 연동 테스트.
    
    Phase 2: Shadow Budget 승인 시 ConfigHistory에 자동 저장.
    """

    def test_approve_saves_to_history(self, mock_current_budget):
        """Shadow Budget 승인 시 ConfigHistory에 저장."""
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
