"""
FinOps DNA Models - 비용 관련 데이터 모델
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class CostTier(str, Enum):
    """비용 계층"""

    FREE = "free"  # 무료 (기본 재시도)
    LOW = "low"  # 저비용 ($0.001 미만)
    MEDIUM = "medium"  # 중간 비용 ($0.001 ~ $0.01)
    HIGH = "high"  # 고비용 ($0.01 ~ $0.10)
    CRITICAL = "critical"  # 고위험 ($0.10 이상)


@dataclass
class CostBudget:
    """비용 예산 설정"""

    stage_name: str
    max_budget: Decimal = Decimal("10.00")
    current_spent: Decimal = Decimal("0.00")
    alert_threshold: float = 0.8  # 80%에서 알림
    hard_limit: bool = True  # True면 예산 초과 시 차단
    reset_period: str = "daily"  # daily, weekly, monthly
    last_reset: datetime = field(default_factory=datetime.now)

    @property
    def remaining(self) -> Decimal:
        """남은 예산"""
        return self.max_budget - self.current_spent

    @property
    def usage_percent(self) -> float:
        """사용률 (%)"""
        if self.max_budget == 0:
            return 100.0
        return float(self.current_spent / self.max_budget * 100)

    @property
    def is_over_budget(self) -> bool:
        """예산 초과 여부"""
        return self.current_spent >= self.max_budget

    @property
    def should_alert(self) -> bool:
        """알림 필요 여부"""
        return self.usage_percent >= (self.alert_threshold * 100)

    def to_dict(self) -> dict:
        return {
            "stage_name": self.stage_name,
            "max_budget": str(self.max_budget),
            "current_spent": str(self.current_spent),
            "remaining": str(self.remaining),
            "usage_percent": self.usage_percent,
            "is_over_budget": self.is_over_budget,
            "should_alert": self.should_alert,
            "alert_threshold": self.alert_threshold,
            "hard_limit": self.hard_limit,
            "reset_period": self.reset_period,
            "last_reset": self.last_reset.isoformat(),
        }


@dataclass
class CostRecord:
    """비용 기록"""

    operation: str
    cost: Decimal
    stage_name: str
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "cost": str(self.cost),
            "stage_name": self.stage_name,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "metadata": self.metadata,
        }


@dataclass
class CostReport:
    """비용 리포트"""

    period: str  # daily, weekly, monthly
    start_date: datetime
    end_date: datetime
    total_cost: Decimal = Decimal("0.00")
    by_stage: dict[str, Decimal] = field(default_factory=dict)
    by_operation: dict[str, Decimal] = field(default_factory=dict)
    record_count: int = 0
    success_rate: float = 100.0

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_cost": str(self.total_cost),
            "by_stage": {k: str(v) for k, v in self.by_stage.items()},
            "by_operation": {k: str(v) for k, v in self.by_operation.items()},
            "record_count": self.record_count,
            "success_rate": self.success_rate,
        }


@dataclass
class CostAlert:
    """비용 알림"""

    alert_type: str  # threshold, over_budget, anomaly
    stage_name: str
    message: str
    current_cost: Decimal
    budget_limit: Decimal
    timestamp: datetime = field(default_factory=datetime.now)
    severity: str = "warning"  # info, warning, critical
    acknowledged: bool = False

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type,
            "stage_name": self.stage_name,
            "message": self.message,
            "current_cost": str(self.current_cost),
            "budget_limit": str(self.budget_limit),
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity,
            "acknowledged": self.acknowledged,
        }
