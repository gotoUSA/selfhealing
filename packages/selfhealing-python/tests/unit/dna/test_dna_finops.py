"""
FinOps DNA 단위 테스트

dna_finops.py 모듈 테스트
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from load_tests.utils.selfhealing.dna_finops import (
    FinOpsController,
    RecoveryBudget,
    CostItem,
    RecoveryItem,
    CostItemType,
    BudgetExceededAction,
    CostReport,
    BudgetExceededError,
)


class TestCostItemType:
    """CostItemType 열거형 테스트"""
    
    def test_cost_item_type_values(self):
        """비용 항목 유형 값 테스트"""
        assert CostItemType.RETRY.value == "retry"
        assert CostItemType.API_CALL.value == "api_call"
        assert CostItemType.COMPUTE_MINUTE.value == "compute_minute"
        assert CostItemType.STORAGE.value == "storage"
        assert CostItemType.NETWORK.value == "network"


class TestCostItem:
    """CostItem 데이터 클래스 테스트"""
    
    def test_cost_item_creation(self):
        """CostItem 생성 테스트"""
        item = CostItem(
            item_type=CostItemType.RETRY,
            unit_cost=0.001,
            quantity=5,
            total_cost=0.005,
            timestamp="2025-01-01T00:00:00",
        )
        
        assert item.item_type == CostItemType.RETRY
        assert item.unit_cost == 0.001
        assert item.quantity == 5
        assert item.total_cost == 0.005
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        item = CostItem(
            item_type=CostItemType.API_CALL,
            unit_cost=0.01,
            quantity=10,
            total_cost=0.10,
            timestamp="2025-01-01T00:00:00",
        )
        
        result = item.to_dict()
        
        assert result["item_type"] == "api_call"
        assert result["unit_cost"] == 0.01
        assert result["quantity"] == 10
        assert result["total_cost"] == 0.10


class TestRecoveryBudget:
    """RecoveryBudget 데이터 클래스 테스트"""
    
    def test_budget_creation(self):
        """예산 생성 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            currency="USD",
        )
        
        assert budget.max_budget == 100.0
        assert budget.spent == 0.0
        assert budget.currency == "USD"
    
    def test_remaining_budget(self):
        """남은 예산 계산 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            spent=30.0,
        )
        
        assert budget.remaining == 70.0
    
    def test_utilization_percentage(self):
        """예산 사용률 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            spent=25.0,
        )
        
        assert budget.utilization == 25.0
    
    def test_is_exceeded_false(self):
        """예산 초과 안됨 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            spent=50.0,
        )
        
        assert budget.is_exceeded is False
    
    def test_is_exceeded_true(self):
        """예산 초과됨 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            spent=100.0,
        )
        
        assert budget.is_exceeded is True
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        budget = RecoveryBudget(
            max_budget=100.0,
            spent=40.0,
            currency="USD",
        )
        
        result = budget.to_dict()
        
        assert result["max_budget"] == 100.0
        assert result["spent"] == 40.0
        assert result["remaining"] == 60.0
        assert result["utilization"] == 40.0
        assert result["is_exceeded"] is False


class TestRecoveryItem:
    """RecoveryItem 데이터 클래스 테스트"""
    
    def test_recovery_item_creation(self):
        """RecoveryItem 생성 테스트"""
        item = RecoveryItem(
            item_id="item_001",
            item_type="payment_retry",
            estimated_cost=0.05,
            business_value=100.0,
            priority=1,
        )
        
        assert item.item_id == "item_001"
        assert item.item_type == "payment_retry"
        assert item.estimated_cost == 0.05
        assert item.business_value == 100.0
        assert item.priority == 1
    
    def test_roi_calculation(self):
        """ROI 계산 테스트"""
        item = RecoveryItem(
            item_id="item_002",
            item_type="order_retry",
            estimated_cost=0.1,
            business_value=50.0,
        )
        
        # ROI = business_value / estimated_cost = 50 / 0.1 = 500
        assert item.roi == 500.0
    
    def test_roi_zero_cost(self):
        """비용이 0일 때 ROI 테스트"""
        item = RecoveryItem(
            item_id="item_003",
            item_type="free_retry",
            estimated_cost=0.0,
            business_value=100.0,
        )
        
        assert item.roi == float('inf')
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        item = RecoveryItem(
            item_id="item_004",
            item_type="inventory_check",
            estimated_cost=0.02,
            business_value=30.0,
            priority=3,
        )
        
        result = item.to_dict()
        
        assert result["item_id"] == "item_004"
        assert result["item_type"] == "inventory_check"
        assert result["estimated_cost"] == 0.02
        assert result["business_value"] == 30.0
        assert result["roi"] == 1500.0  # 30 / 0.02


class TestCostReport:
    """CostReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = CostReport(
            report_id="report_001",
            generated_at="2025-01-01T12:00:00",
            budget_summary={
                "max_budget": 100.0,
                "spent": 25.0,
                "remaining": 75.0,
                "utilization": 25.0,
                "is_exceeded": False,
            },
            by_type={
                "retry": {"count": 10, "cost": 0.1},
                "api_call": {"count": 5, "cost": 0.05},
            },
            timeline=[],
            recommendations=["Consider reducing retry count"],
        )
        
        assert report.report_id == "report_001"
        assert report.budget_summary["max_budget"] == 100.0
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        report = CostReport(
            report_id="report_002",
            generated_at="2025-01-01T12:00:00",
            budget_summary={"max_budget": 50.0},
            by_type={},
            timeline=[],
            recommendations=[],
        )
        
        result = report.to_dict()
        
        assert result["report_id"] == "report_002"
        assert "budget_summary" in result
    
    def test_to_markdown(self):
        """마크다운 변환 테스트"""
        report = CostReport(
            report_id="report_003",
            generated_at="2025-01-01T12:00:00",
            budget_summary={
                "max_budget": 100.0,
                "spent": 30.0,
                "remaining": 70.0,
                "utilization": 30.0,
                "is_exceeded": False,
            },
            by_type={"retry": {"count": 5, "cost": 0.05}},
            timeline=[],
            recommendations=["Optimize retries"],
        )
        
        md = report.to_markdown()
        
        assert "FinOps" in md
        assert "Budget Summary" in md
        assert "$100.00" in md or "100" in md


class TestBudgetExceededError:
    """BudgetExceededError 예외 테스트"""
    
    def test_exception_creation(self):
        """예외 생성 테스트"""
        budget = RecoveryBudget(max_budget=100.0, spent=95.0)
        error = BudgetExceededError(
            message="Budget exceeded",
            budget=budget,
            attempted_cost=10.0,
        )
        
        assert str(error) == "Budget exceeded"
        assert error.budget == budget
        assert error.attempted_cost == 10.0


class TestFinOpsController:
    """FinOpsController 테스트"""
    
    def test_controller_initialization(self):
        """컨트롤러 초기화 테스트"""
        controller = FinOpsController(
            max_budget=100.0,
            on_exceeded=BudgetExceededAction.ALERT_ONLY,
        )
        
        assert controller.budget.max_budget == 100.0
        assert controller.on_exceeded == BudgetExceededAction.ALERT_ONLY
    
    def test_record_cost(self):
        """비용 기록 테스트"""
        controller = FinOpsController(max_budget=100.0)
        
        success, cost = controller.record_cost(
            item_type=CostItemType.RETRY,
            quantity=10,
        )
        
        assert controller.budget.spent > 0
    
    def test_from_dna(self):
        """DNA 설정에서 생성 테스트"""
        finops_config = {
            "max_recovery_budget": 50.0,
            "budget_currency": "KRW",
            "on_budget_exceeded": "hard_stop",
            "cost_per_retry": 0.002,
        }
        
        controller = FinOpsController.from_dna(finops_config)
        
        assert controller.budget.max_budget == 50.0
        assert controller.budget.currency == "KRW"


class TestBudgetExceededAction:
    """BudgetExceededAction 열거형 테스트"""
    
    def test_action_values(self):
        """동작 정책 값 테스트"""
        assert BudgetExceededAction.HARD_STOP.value == "hard_stop"
        assert BudgetExceededAction.GRACEFUL_DEGRADATION.value == "graceful_degradation"
        assert BudgetExceededAction.ALERT_ONLY.value == "alert_only"
        assert BudgetExceededAction.PRIORITY_BASED.value == "priority_based"
