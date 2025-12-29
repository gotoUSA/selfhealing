"""
FinOps DNA - 복구 비용 최적화 및 추적

Phase 2 구현: 대기업 M&A Exit을 위한 CFO 정조준 기능

기능:
1. 복구 비용 실시간 추적
2. 예산 상한 자동 적용
3. ROI 기반 우선순위 결정
4. Graceful Degradation
5. 비용 리포트 생성

참조:
- docs/self_healing/32_DNA_ENTERPRISE_FEATURES.md (Section 1)
- docs/self_healing/29_STAGE_DNA_EVOLUTION_MASTER.md

비즈니스 가치: +$80M (CFO / 비용 민감 기업)
"""

from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import threading
import json
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# 예산 초과 정책
# =============================================================================

class BudgetExceededAction(Enum):
    """예산 초과 시 동작 정책"""
    HARD_STOP = "hard_stop"                     # 즉시 중단
    GRACEFUL_DEGRADATION = "graceful_degradation"  # 점진적 축소
    ALERT_ONLY = "alert_only"                   # 알림만, 계속 진행
    PRIORITY_BASED = "priority_based"           # 우선순위 기반 선별 처리


class CostItemType(Enum):
    """비용 항목 유형"""
    RETRY = "retry"
    API_CALL = "api_call"
    COMPUTE_MINUTE = "compute_minute"
    STORAGE = "storage"
    NETWORK = "network"
    EXTERNAL_SERVICE = "external_service"
    CUSTOM = "custom"


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class CostItem:
    """비용 항목"""
    item_type: CostItemType
    unit_cost: float
    quantity: int
    total_cost: float
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_type": self.item_type.value,
            "unit_cost": self.unit_cost,
            "quantity": self.quantity,
            "total_cost": self.total_cost,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class RecoveryBudget:
    """복구 예산"""
    max_budget: float
    currency: str = "USD"
    spent: float = 0.0
    items: List[CostItem] = field(default_factory=list)
    
    @property
    def remaining(self) -> float:
        """남은 예산"""
        return max(0, self.max_budget - self.spent)
    
    @property
    def utilization(self) -> float:
        """예산 사용률 (%)"""
        if self.max_budget == 0:
            return 0.0
        return (self.spent / self.max_budget) * 100
    
    @property
    def is_exceeded(self) -> bool:
        """예산 초과 여부"""
        return self.spent >= self.max_budget
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_budget": self.max_budget,
            "currency": self.currency,
            "spent": self.spent,
            "remaining": self.remaining,
            "utilization": self.utilization,
            "is_exceeded": self.is_exceeded,
            "item_count": len(self.items),
        }


@dataclass
class RecoveryItem:
    """복구 대상 아이템 (ROI 계산용)"""
    item_id: str
    item_type: str
    estimated_cost: float
    business_value: float
    priority: int = 5  # 1 (highest) ~ 10 (lowest)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def roi(self) -> float:
        """ROI (투자 대비 수익)"""
        if self.estimated_cost == 0:
            return float('inf')
        return self.business_value / self.estimated_cost
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "estimated_cost": self.estimated_cost,
            "business_value": self.business_value,
            "priority": self.priority,
            "roi": self.roi,
        }


@dataclass
class CostReport:
    """비용 리포트"""
    report_id: str
    generated_at: str
    budget_summary: Dict[str, Any]
    by_type: Dict[str, Dict[str, float]]
    timeline: List[Dict[str, Any]]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "budget_summary": self.budget_summary,
            "by_type": self.by_type,
            "recommendations": self.recommendations,
        }
    
    def to_markdown(self) -> str:
        """마크다운 리포트 생성"""
        lines = [
            "# 💰 FinOps Recovery Cost Report",
            "",
            f"📅 **Generated**: {self.generated_at}",
            "",
            "## Budget Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Max Budget | ${self.budget_summary.get('max_budget', 0):.2f} |",
            f"| Spent | ${self.budget_summary.get('spent', 0):.2f} |",
            f"| Remaining | ${self.budget_summary.get('remaining', 0):.2f} |",
            f"| Utilization | {self.budget_summary.get('utilization', 0):.1f}% |",
            f"| Status | {'⚠️ EXCEEDED' if self.budget_summary.get('is_exceeded') else '✅ OK'} |",
            "",
            "## Cost by Type",
            "",
            "| Type | Count | Cost |",
            "|------|-------|------|",
        ]
        
        for item_type, data in self.by_type.items():
            lines.append(
                f"| {item_type} | {data.get('count', 0)} | ${data.get('cost', 0):.4f} |"
            )
        
        lines.extend([
            "",
            "## Recommendations",
            "",
        ])
        
        for rec in self.recommendations:
            lines.append(f"- {rec}")
        
        return "\n".join(lines)


# =============================================================================
# 예외 클래스
# =============================================================================

class BudgetExceededError(Exception):
    """예산 초과 예외"""
    
    def __init__(
        self,
        message: str,
        budget: RecoveryBudget,
        attempted_cost: float
    ):
        super().__init__(message)
        self.budget = budget
        self.attempted_cost = attempted_cost


# =============================================================================
# FinOps Controller
# =============================================================================

class FinOpsController:
    """
    FinOps 컨트롤러
    
    복구 비용을 추적하고 예산 제한을 적용합니다.
    """
    
    # 기본 단위 비용 (USD)
    DEFAULT_COST_RATES = {
        CostItemType.RETRY: 0.001,
        CostItemType.API_CALL: 0.01,
        CostItemType.COMPUTE_MINUTE: 0.05,
        CostItemType.STORAGE: 0.02,
        CostItemType.NETWORK: 0.005,
        CostItemType.EXTERNAL_SERVICE: 0.1,
        CostItemType.CUSTOM: 0.01,
    }
    
    def __init__(
        self,
        max_budget: float = 1000.0,
        currency: str = "USD",
        on_exceeded: BudgetExceededAction = BudgetExceededAction.GRACEFUL_DEGRADATION,
        cost_rates: Dict[str, float] = None,
        alert_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        초기화
        
        Args:
            max_budget: 최대 복구 예산
            currency: 통화
            on_exceeded: 예산 초과 시 동작
            cost_rates: 단위 비용 설정 (문자열 키)
            alert_callback: 알림 콜백 함수
        """
        self.budget = RecoveryBudget(
            max_budget=max_budget,
            currency=currency,
        )
        
        self.on_exceeded = on_exceeded
        self.alert_callback = alert_callback
        
        # 비용 설정
        self.cost_rates = dict(self.DEFAULT_COST_RATES)
        if cost_rates:
            for key, value in cost_rates.items():
                try:
                    cost_type = CostItemType(key) if isinstance(key, str) else key
                    self.cost_rates[cost_type] = value
                except ValueError:
                    # 커스텀 타입은 그대로 저장
                    self.cost_rates[key] = value
        
        self._lock = threading.Lock()
        self._history: List[CostItem] = []
    
    @classmethod
    def from_dna(cls, finops_config: Dict[str, Any]) -> "FinOpsController":
        """
        Stage DNA의 finops 설정에서 생성
        
        Args:
            finops_config: STAGE_DNA["finops"] 딕셔너리
            
        Returns:
            FinOpsController 인스턴스
        """
        cost_rates = {}
        
        # 비용 설정 파싱
        if "cost_per_retry" in finops_config:
            cost_rates[CostItemType.RETRY] = finops_config["cost_per_retry"]
        if "cost_per_api_call" in finops_config:
            cost_rates[CostItemType.API_CALL] = finops_config["cost_per_api_call"]
        if "cost_per_compute_minute" in finops_config:
            cost_rates[CostItemType.COMPUTE_MINUTE] = finops_config["cost_per_compute_minute"]
        
        return cls(
            max_budget=finops_config.get("max_recovery_budget", 1000.0),
            currency=finops_config.get("budget_currency", "USD"),
            on_exceeded=BudgetExceededAction(
                finops_config.get("on_budget_exceeded", "graceful_degradation")
            ),
            cost_rates=cost_rates,
        )
    
    def record_cost(
        self,
        item_type: CostItemType,
        quantity: int = 1,
        unit_cost: Optional[float] = None,
        metadata: Dict[str, Any] = None
    ) -> Tuple[bool, float]:
        """
        비용 기록
        
        Args:
            item_type: 비용 항목 유형
            quantity: 수량
            unit_cost: 단위 비용 (미지정 시 기본값 사용)
            metadata: 추가 메타데이터
            
        Returns:
            (성공 여부, 기록된 비용)
            
        Raises:
            BudgetExceededError: HARD_STOP 모드에서 예산 초과 시
        """
        if unit_cost is None:
            unit_cost = self.cost_rates.get(item_type, 0.01)
        
        total_cost = unit_cost * quantity
        
        with self._lock:
            # 예산 체크
            if self.budget.spent + total_cost > self.budget.max_budget:
                return self._handle_budget_exceeded(total_cost, item_type, quantity)
            
            # 비용 기록
            item = CostItem(
                item_type=item_type,
                unit_cost=unit_cost,
                quantity=quantity,
                total_cost=total_cost,
                timestamp=datetime.now().isoformat(),
                metadata=metadata or {},
            )
            
            self.budget.items.append(item)
            self.budget.spent += total_cost
            self._history.append(item)
            
            # 경고 임계값 체크 (80%, 90%)
            self._check_warning_thresholds()
            
            return True, total_cost
    
    def _handle_budget_exceeded(
        self,
        attempted_cost: float,
        item_type: CostItemType,
        quantity: int
    ) -> Tuple[bool, float]:
        """예산 초과 처리"""
        message = (
            f"Budget exceeded! Attempted: ${attempted_cost:.4f}, "
            f"Remaining: ${self.budget.remaining:.4f}"
        )
        logger.warning(message)
        
        if self.on_exceeded == BudgetExceededAction.HARD_STOP:
            raise BudgetExceededError(
                f"Recovery budget exceeded: ${self.budget.spent:.2f} / ${self.budget.max_budget:.2f}",
                self.budget,
                attempted_cost,
            )
        
        elif self.on_exceeded == BudgetExceededAction.GRACEFUL_DEGRADATION:
            # 남은 예산으로 부분 처리
            if self.budget.remaining > 0:
                partial = self.budget.remaining
                
                # 부분 수량 계산
                unit_cost = self.cost_rates.get(item_type, 0.01)
                partial_quantity = int(self.budget.remaining / unit_cost)
                
                if partial_quantity > 0:
                    item = CostItem(
                        item_type=item_type,
                        unit_cost=unit_cost,
                        quantity=min(partial_quantity, quantity),
                        total_cost=min(partial_quantity * unit_cost, self.budget.remaining),
                        timestamp=datetime.now().isoformat(),
                        metadata={"partial": True, "original_quantity": quantity},
                    )
                    
                    self.budget.items.append(item)
                    self.budget.spent = self.budget.max_budget
                    self._history.append(item)
                    
                    logger.info(f"Partial recovery: {partial_quantity}/{quantity} items")
                    return True, item.total_cost
                
            return False, 0.0
        
        elif self.on_exceeded == BudgetExceededAction.ALERT_ONLY:
            # 알림만 보내고 계속 진행
            self._send_budget_alert("EXCEEDED", attempted_cost)
            
            item = CostItem(
                item_type=item_type,
                unit_cost=self.cost_rates.get(item_type, 0.01),
                quantity=quantity,
                total_cost=attempted_cost,
                timestamp=datetime.now().isoformat(),
                metadata={"over_budget": True},
            )
            
            self.budget.items.append(item)
            self.budget.spent += attempted_cost
            self._history.append(item)
            
            return True, attempted_cost
        
        elif self.on_exceeded == BudgetExceededAction.PRIORITY_BASED:
            # 우선순위 기반은 외부에서 prioritize_recovery 사용
            return False, 0.0
        
        return False, 0.0
    
    def _check_warning_thresholds(self):
        """경고 임계값 체크"""
        utilization = self.budget.utilization
        
        if 90 <= utilization < 100:
            self._send_budget_alert("WARNING_90", self.budget.spent)
        elif 80 <= utilization < 90:
            self._send_budget_alert("WARNING_80", self.budget.spent)
    
    def _send_budget_alert(self, alert_type: str, amount: float):
        """예산 알림 발송"""
        message = (
            f"[FINOPS ALERT] {alert_type}\n"
            f"  Max Budget: ${self.budget.max_budget:.2f}\n"
            f"  Current Spent: ${self.budget.spent:.2f}\n"
            f"  Utilization: {self.budget.utilization:.1f}%"
        )
        
        if alert_type == "EXCEEDED":
            logger.critical(message)
        else:
            logger.warning(message)
        
        if self.alert_callback:
            try:
                self.alert_callback(message)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")
    
    def prioritize_recovery(
        self,
        items: List[RecoveryItem]
    ) -> List[RecoveryItem]:
        """
        ROI 기반 복구 우선순위 정렬
        
        예산 내에서 처리 가능한 항목만 반환
        
        Args:
            items: 복구 대상 아이템 목록
            
        Returns:
            우선순위 정렬된 처리 가능 아이템 목록
        """
        # ROI 높은 순, 우선순위 낮은 순 정렬
        sorted_items = sorted(items, key=lambda x: (-x.roi, x.priority))
        
        # 예산 내에서 처리 가능한 항목 필터링
        affordable = []
        remaining = self.budget.remaining
        
        for item in sorted_items:
            if item.estimated_cost <= remaining:
                affordable.append(item)
                remaining -= item.estimated_cost
        
        return affordable
    
    def estimate_recovery_cost(
        self,
        dlq_count: int,
        avg_retries: int = 3,
        external_api_rate: float = 0.5
    ) -> Dict[str, Any]:
        """
        복구 비용 예측
        
        Args:
            dlq_count: DLQ 메시지 수
            avg_retries: 평균 재시도 횟수
            external_api_rate: 외부 API 호출 비율
            
        Returns:
            예측 비용 상세
        """
        retry_cost = dlq_count * avg_retries * self.cost_rates.get(CostItemType.RETRY, 0.001)
        api_cost = dlq_count * external_api_rate * self.cost_rates.get(CostItemType.API_CALL, 0.01)
        compute_cost = (dlq_count / 100) * self.cost_rates.get(CostItemType.COMPUTE_MINUTE, 0.05)
        
        total = retry_cost + api_cost + compute_cost
        
        return {
            "dlq_count": dlq_count,
            "retry_cost": retry_cost,
            "api_cost": api_cost,
            "compute_cost": compute_cost,
            "total_estimated": total,
            "within_budget": total <= self.budget.remaining,
            "budget_utilization_after": (self.budget.spent + total) / self.budget.max_budget * 100,
            "recommendation": self._get_cost_recommendation(total),
        }
    
    def _get_cost_recommendation(self, estimated_cost: float) -> str:
        """비용 기반 추천"""
        utilization_after = (self.budget.spent + estimated_cost) / self.budget.max_budget * 100
        
        if estimated_cost > self.budget.remaining:
            return "⚠️ 예산 초과 예상. 우선순위 기반 부분 복구 권장"
        elif utilization_after > 90:
            return "🟡 예산 90% 이상 사용 예상. 신중한 복구 권장"
        elif utilization_after > 70:
            return "🟢 예산 범위 내. 복구 진행 가능"
        else:
            return "✅ 충분한 예산. 전체 복구 가능"
    
    def get_cost_report(self) -> CostReport:
        """
        비용 리포트 생성
        
        Returns:
            CostReport 객체
        """
        # 타입별 집계
        by_type: Dict[str, Dict[str, float]] = {}
        for item in self.budget.items:
            type_key = item.item_type.value
            if type_key not in by_type:
                by_type[type_key] = {"count": 0, "cost": 0.0}
            by_type[type_key]["count"] += item.quantity
            by_type[type_key]["cost"] += item.total_cost
        
        # 타임라인 (최근 10건)
        timeline = [item.to_dict() for item in self._history[-10:]]
        
        # 추천사항 생성
        recommendations = self._generate_recommendations()
        
        return CostReport(
            report_id=f"finops-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            budget_summary=self.budget.to_dict(),
            by_type=by_type,
            timeline=timeline,
            recommendations=recommendations,
        )
    
    def _generate_recommendations(self) -> List[str]:
        """추천사항 생성"""
        recommendations = []
        
        if self.budget.is_exceeded:
            recommendations.append(
                "🔴 예산이 초과되었습니다. 복구 전략 재검토 필요"
            )
        elif self.budget.utilization > 90:
            recommendations.append(
                "🟡 예산 사용률 90% 이상. 추가 복구 시 주의 필요"
            )
        elif self.budget.utilization > 70:
            recommendations.append(
                "🟢 예산 사용률 70% 이상. 효율적인 사용 중"
            )
        else:
            recommendations.append(
                "✅ 예산 여유 있음. 필요한 복구 진행 가능"
            )
        
        # 비용 유형별 분석
        if self.budget.items:
            from collections import Counter
            type_counts = Counter(item.item_type for item in self.budget.items)
            top_type = type_counts.most_common(1)[0]
            recommendations.append(
                f"📊 가장 많은 비용 유형: {top_type[0].value} ({top_type[1]}건)"
            )
        
        return recommendations
    
    def reset(self):
        """예산 초기화"""
        with self._lock:
            self.budget.spent = 0.0
            self.budget.items = []
            self._history = []
            logger.info("FinOps budget reset")


# =============================================================================
# DNA 검증 확장
# =============================================================================

def validate_finops_dna(finops_config: Dict[str, Any]) -> List[str]:
    """
    FinOps DNA 설정 검증
    
    Args:
        finops_config: STAGE_DNA["finops"] 딕셔너리
        
    Returns:
        경고 메시지 목록
    """
    warnings = []
    
    # 필수 필드 체크
    if "max_recovery_budget" not in finops_config:
        warnings.append("max_recovery_budget 설정 필요")
    
    budget = finops_config.get("max_recovery_budget", 0)
    if budget <= 0:
        warnings.append("max_recovery_budget는 양수여야 함")
    
    # 비용 설정 검증
    for cost_key in ["cost_per_retry", "cost_per_api_call", "cost_per_compute_minute"]:
        if cost_key in finops_config:
            if finops_config[cost_key] < 0:
                warnings.append(f"{cost_key}는 음수일 수 없음")
    
    # 예산 초과 동작 검증
    valid_actions = {"hard_stop", "graceful_degradation", "alert_only", "priority_based"}
    action = finops_config.get("on_budget_exceeded", "graceful_degradation")
    if action not in valid_actions:
        warnings.append(f"on_budget_exceeded는 {valid_actions} 중 하나여야 함")
    
    # 우선순위 검증
    priority = finops_config.get("recovery_priority", {})
    for item, config in priority.items():
        if not isinstance(config, dict):
            warnings.append(f"{item}의 설정은 딕셔너리여야 함")
            continue
        if "value" not in config:
            warnings.append(f"{item}의 value 설정 필요")
        if "priority" not in config:
            warnings.append(f"{item}의 priority 설정 필요")
    
    return warnings


# =============================================================================
# 리포트 생성 헬퍼
# =============================================================================

def generate_finops_report_section(controller: FinOpsController) -> str:
    """
    리포트 섹션 생성 (BaseReport 통합용)
    
    Args:
        controller: FinOpsController 인스턴스
        
    Returns:
        마크다운 형식 리포트 섹션
    """
    report = controller.get_cost_report()
    return report.to_markdown()


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트 (테스트용)"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="FinOps DNA - 복구 비용 추적 테스트"
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=100.0,
        help="최대 예산 (USD)",
    )
    parser.add_argument(
        "--simulate",
        type=int,
        default=50,
        help="시뮬레이션할 복구 작업 수",
    )
    
    args = parser.parse_args()
    
    # 컨트롤러 생성
    controller = FinOpsController(max_budget=args.budget)
    
    print(f"\n💰 FinOps Simulation")
    print(f"Budget: ${args.budget:.2f}")
    print(f"Simulating {args.simulate} recovery operations...\n")
    
    # 시뮬레이션
    import random
    
    for i in range(args.simulate):
        item_type = random.choice(list(CostItemType))
        quantity = random.randint(1, 10)
        
        try:
            success, cost = controller.record_cost(item_type, quantity)
            if success:
                print(f"  [{i+1}] {item_type.value}: {quantity} units = ${cost:.4f}")
            else:
                print(f"  [{i+1}] {item_type.value}: SKIPPED (budget exceeded)")
                break
        except BudgetExceededError as e:
            print(f"  [{i+1}] BUDGET EXCEEDED!")
            break
    
    # 리포트 출력
    report = controller.get_cost_report()
    print("\n" + report.to_markdown())


if __name__ == "__main__":
    main()
