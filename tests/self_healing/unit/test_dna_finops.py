"""
FinOps DNA 단위 테스트

Phase 2: dna_finops.py 모듈 테스트
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from load_tests.utils.selfhealing.dna_finops import (
    FinOpsController,
    RecoveryBudget,
    CostItem,
    RecoveryItem,
    CostCategory,
    BudgetStatus,
    FinOpsReport,
    GracefulDegradationLevel,
)


class TestCostItem:
    """CostItem 데이터 클래스 테스트"""
    
    def test_cost_item_creation(self):
        """CostItem 생성 테스트"""
        item = CostItem(
            name="Database Query",
            category=CostCategory.COMPUTE,
            estimated_cost=0.005,
            duration_seconds=2.0,
        )
        
        assert item.name == "Database Query"
        assert item.category == CostCategory.COMPUTE
        assert item.estimated_cost == 0.005
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        item = CostItem(
            name="API Call",
            category=CostCategory.NETWORK,
            estimated_cost=0.001,
            duration_seconds=0.5,
        )
        
        result = item.to_dict()
        
        assert result["name"] == "API Call"
        assert result["category"] == "network"
        assert result["estimated_cost"] == 0.001


class TestRecoveryBudget:
    """RecoveryBudget 데이터 클래스 테스트"""
    
    def test_budget_creation(self):
        """예산 생성 테스트"""
        budget = RecoveryBudget(
            total_budget=100.0,
            hourly_limit=10.0,
            daily_limit=50.0,
        )
        
        assert budget.total_budget == 100.0
        assert budget.current_spent == 0.0
    
    def test_remaining_budget(self):
        """남은 예산 계산 테스트"""
        budget = RecoveryBudget(
            total_budget=100.0,
            current_spent=30.0,
        )
        
        assert budget.remaining_budget == 70.0
    
    def test_utilization_percentage(self):
        """예산 사용률 테스트"""
        budget = RecoveryBudget(
            total_budget=100.0,
            current_spent=25.0,
        )
        
        assert budget.utilization_percentage == 25.0
    
    def test_is_exceeded_false(self):
        """예산 초과 안됨 테스트"""
        budget = RecoveryBudget(
            total_budget=100.0,
            current_spent=50.0,
        )
        
        assert budget.is_exceeded is False
    
    def test_is_exceeded_true(self):
        """예산 초과됨 테스트"""
        budget = RecoveryBudget(
            total_budget=100.0,
            current_spent=110.0,
        )
        
        assert budget.is_exceeded is True


class TestRecoveryItem:
    """RecoveryItem 데이터 클래스 테스트"""
    
    def test_recovery_item_creation(self):
        """복구 항목 생성 테스트"""
        item = RecoveryItem(
            name="Database Retry",
            estimated_cost=0.05,
            priority=1,
            business_impact=0.8,
        )
        
        assert item.name == "Database Retry"
        assert item.priority == 1
    
    def test_roi_calculation(self):
        """ROI 계산 테스트"""
        item = RecoveryItem(
            name="API Recovery",
            estimated_cost=0.10,
            priority=2,
            business_impact=0.9,  # 90% impact
        )
        
        # ROI = impact / cost
        assert item.roi == pytest.approx(9.0, rel=0.01)
    
    def test_roi_zero_cost(self):
        """비용 0일 때 ROI 테스트"""
        item = RecoveryItem(
            name="Free Recovery",
            estimated_cost=0.0,
            priority=1,
            business_impact=0.5,
        )
        
        # 0으로 나누면 무한대
        assert item.roi == float("inf")


class TestBudgetStatus:
    """BudgetStatus 열거형 테스트"""
    
    def test_status_values(self):
        """상태 값 테스트"""
        assert BudgetStatus.HEALTHY.value == "healthy"
        assert BudgetStatus.WARNING.value == "warning"
        assert BudgetStatus.CRITICAL.value == "critical"
        assert BudgetStatus.EXCEEDED.value == "exceeded"


class TestGracefulDegradationLevel:
    """GracefulDegradationLevel 열거형 테스트"""
    
    def test_level_values(self):
        """레벨 값 테스트"""
        assert GracefulDegradationLevel.FULL.value == "full"
        assert GracefulDegradationLevel.REDUCED.value == "reduced"
        assert GracefulDegradationLevel.MINIMAL.value == "minimal"
        assert GracefulDegradationLevel.DISABLED.value == "disabled"


class TestFinOpsController:
    """FinOpsController 클래스 테스트"""
    
    @pytest.fixture
    def controller(self):
        """기본 FinOps 컨트롤러"""
        return FinOpsController(
            total_budget=100.0,
            hourly_limit=10.0,
            daily_limit=50.0,
        )
    
    def test_initialization(self, controller):
        """초기화 테스트"""
        assert controller.budget.total_budget == 100.0
        assert controller.budget.hourly_limit == 10.0
    
    def test_get_budget_status_healthy(self, controller):
        """Healthy 상태 테스트"""
        status = controller.get_budget_status()
        
        assert status == BudgetStatus.HEALTHY
    
    def test_get_budget_status_warning(self, controller):
        """Warning 상태 테스트"""
        controller.budget.current_spent = 75.0  # 75%
        
        status = controller.get_budget_status()
        
        assert status == BudgetStatus.WARNING
    
    def test_get_budget_status_critical(self, controller):
        """Critical 상태 테스트"""
        controller.budget.current_spent = 92.0  # 92%
        
        status = controller.get_budget_status()
        
        assert status == BudgetStatus.CRITICAL
    
    def test_get_budget_status_exceeded(self, controller):
        """Exceeded 상태 테스트"""
        controller.budget.current_spent = 110.0  # 110%
        
        status = controller.get_budget_status()
        
        assert status == BudgetStatus.EXCEEDED
    
    def test_can_spend_true(self, controller):
        """지출 가능 테스트"""
        assert controller.can_spend(10.0) is True
    
    def test_can_spend_false_exceeds_budget(self, controller):
        """예산 초과로 지출 불가 테스트"""
        controller.budget.current_spent = 95.0
        
        assert controller.can_spend(10.0) is False
    
    def test_record_cost(self, controller):
        """비용 기록 테스트"""
        cost = CostItem(
            name="Recovery Operation",
            category=CostCategory.COMPUTE,
            estimated_cost=5.0,
            duration_seconds=10.0,
        )
        
        controller.record_cost(cost)
        
        assert controller.budget.current_spent == 5.0
        assert len(controller.cost_history) == 1
    
    def test_prioritize_recovery_items(self, controller):
        """복구 항목 우선순위화 테스트"""
        items = [
            RecoveryItem(
                name="Low ROI Item",
                estimated_cost=1.0,
                priority=3,
                business_impact=0.1,
            ),
            RecoveryItem(
                name="High ROI Item",
                estimated_cost=0.1,
                priority=1,
                business_impact=0.9,
            ),
            RecoveryItem(
                name="Medium ROI Item",
                estimated_cost=0.5,
                priority=2,
                business_impact=0.5,
            ),
        ]
        
        prioritized = controller.prioritize_recovery_items(items)
        
        # ROI 기준 정렬 (높은 순)
        assert prioritized[0].name == "High ROI Item"


class TestFinOpsControllerCallbacks:
    """FinOpsController 콜백 테스트"""
    
    def test_alert_callback_triggered(self):
        """Alert 콜백 트리거 테스트"""
        alert_received = []
        
        def on_alert(status, budget):
            alert_received.append((status, budget))
        
        controller = FinOpsController(
            total_budget=100.0,
            alert_callback=on_alert,
        )
        
        # Warning 임계값 도달
        controller.budget.current_spent = 75.0
        controller._check_and_alert()
        
        assert len(alert_received) == 1
        assert alert_received[0][0] == BudgetStatus.WARNING


class TestFinOpsReport:
    """FinOpsReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = FinOpsReport(
            timestamp=datetime.now().isoformat(),
            total_budget=100.0,
            current_spent=45.0,
            remaining_budget=55.0,
            utilization_percentage=45.0,
            status=BudgetStatus.HEALTHY,
            cost_breakdown={},
            recommendations=[],
        )
        
        assert report.total_budget == 100.0
        assert report.status == BudgetStatus.HEALTHY
    
    def test_to_markdown(self):
        """마크다운 변환 테스트"""
        report = FinOpsReport(
            timestamp="2025-12-29T00:00:00",
            total_budget=100.0,
            current_spent=45.0,
            remaining_budget=55.0,
            utilization_percentage=45.0,
            status=BudgetStatus.HEALTHY,
            cost_breakdown={"compute": 30.0, "network": 15.0},
            recommendations=["비용 최적화 권장"],
        )
        
        md = report.to_markdown()
        
        assert "# FinOps Report" in md
        assert "45.0%" in md
        assert "HEALTHY" in md


class TestGracefulDegradation:
    """Graceful Degradation 테스트"""
    
    def test_get_degradation_level_full(self):
        """Full 레벨 테스트 (예산 여유)"""
        controller = FinOpsController(total_budget=100.0)
        controller.budget.current_spent = 50.0  # 50%
        
        level = controller.get_degradation_level()
        
        assert level == GracefulDegradationLevel.FULL
    
    def test_get_degradation_level_reduced(self):
        """Reduced 레벨 테스트 (70-90%)"""
        controller = FinOpsController(total_budget=100.0)
        controller.budget.current_spent = 80.0  # 80%
        
        level = controller.get_degradation_level()
        
        assert level == GracefulDegradationLevel.REDUCED
    
    def test_get_degradation_level_minimal(self):
        """Minimal 레벨 테스트 (90-100%)"""
        controller = FinOpsController(total_budget=100.0)
        controller.budget.current_spent = 95.0  # 95%
        
        level = controller.get_degradation_level()
        
        assert level == GracefulDegradationLevel.MINIMAL
    
    def test_get_degradation_level_disabled(self):
        """Disabled 레벨 테스트 (예산 초과)"""
        controller = FinOpsController(total_budget=100.0)
        controller.budget.current_spent = 105.0  # 105%
        
        level = controller.get_degradation_level()
        
        assert level == GracefulDegradationLevel.DISABLED


class TestCostEstimation:
    """비용 추정 테스트"""
    
    def test_estimate_operation_cost(self):
        """작업 비용 추정 테스트"""
        controller = FinOpsController(total_budget=100.0)
        
        # 복잡도와 지속시간 기반 추정
        cost = controller.estimate_operation_cost(
            operation_type="database_query",
            complexity=3,
            duration_seconds=2.0,
        )
        
        assert cost > 0
        assert isinstance(cost, float)
    
    def test_estimate_recovery_cost(self):
        """복구 비용 추정 테스트"""
        controller = FinOpsController(total_budget=100.0)
        
        items = [
            RecoveryItem(
                name="Item 1",
                estimated_cost=5.0,
                priority=1,
                business_impact=0.5,
            ),
            RecoveryItem(
                name="Item 2",
                estimated_cost=10.0,
                priority=2,
                business_impact=0.7,
            ),
        ]
        
        total = controller.estimate_recovery_cost(items)
        
        assert total == 15.0


class TestHourlyDailyLimits:
    """시간별/일별 제한 테스트"""
    
    def test_hourly_limit_check(self):
        """시간별 제한 체크"""
        controller = FinOpsController(
            total_budget=100.0,
            hourly_limit=5.0,
        )
        
        # 시간 내 비용 기록
        for _ in range(4):
            controller.record_cost(CostItem(
                name="Small Op",
                category=CostCategory.COMPUTE,
                estimated_cost=1.0,
                duration_seconds=1.0,
            ))
        
        # 시간 제한 근처
        assert controller.can_spend(1.5) is False  # 4 + 1.5 = 5.5 > 5.0
    
    def test_reset_hourly_counter(self):
        """시간별 카운터 리셋 테스트"""
        controller = FinOpsController(
            total_budget=100.0,
            hourly_limit=5.0,
        )
        
        controller._hourly_spent = 4.5
        
        # 1시간 후 시뮬레이션
        controller._last_hourly_reset = datetime.now() - timedelta(hours=2)
        controller._check_reset_counters()
        
        assert controller._hourly_spent == 0.0
