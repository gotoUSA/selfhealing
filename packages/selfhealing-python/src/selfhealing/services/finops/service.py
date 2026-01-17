"""
FinOps DNA Service - 비용 관리 서비스
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from threading import Lock

from .models import CostBudget, CostRecord, CostReport, CostAlert, CostTier

logger = logging.getLogger(__name__)


# 기본 작업별 비용 (USD)
DEFAULT_OPERATION_COSTS: Dict[str, Decimal] = {
    "retry": Decimal("0.001"),
    "circuit_breaker_check": Decimal("0.0001"),
    "dlq_enqueue": Decimal("0.005"),
    "dlq_replay": Decimal("0.01"),
    "health_check": Decimal("0.0001"),
    "rollback": Decimal("0.05"),
    "emergency_mode": Decimal("0.10"),
    "chaos_test": Decimal("0.02"),
}


class FinOpsService:
    """
    FinOps DNA 서비스
    
    복구 비용을 추적하고 예산을 관리합니다.
    """
    
    _instance: Optional["FinOpsService"] = None
    _lock = Lock()
    
    def __new__(cls) -> "FinOpsService":
        """싱글톤 패턴"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._budgets: Dict[str, CostBudget] = {}
        self._records: List[CostRecord] = []
        self._alerts: List[CostAlert] = []
        self._operation_costs = DEFAULT_OPERATION_COSTS.copy()
        self._enabled = True
        self._initialized = True
        
        logger.info("FinOpsService initialized")
    
    def set_budget(
        self,
        stage_name: str,
        max_budget: Decimal,
        alert_threshold: float = 0.8,
        hard_limit: bool = True,
        reset_period: str = "daily",
    ) -> CostBudget:
        """
        Stage별 예산 설정
        
        Args:
            stage_name: Stage 이름
            max_budget: 최대 예산 (USD)
            alert_threshold: 알림 임계값 (0.0 ~ 1.0)
            hard_limit: 예산 초과 시 차단 여부
            reset_period: 리셋 주기 (daily, weekly, monthly)
        
        Returns:
            CostBudget: 설정된 예산
        """
        budget = CostBudget(
            stage_name=stage_name,
            max_budget=max_budget,
            alert_threshold=alert_threshold,
            hard_limit=hard_limit,
            reset_period=reset_period,
        )
        self._budgets[stage_name] = budget
        logger.info(f"Budget set for {stage_name}: ${max_budget}")
        return budget
    
    def get_budget(self, stage_name: str) -> Optional[CostBudget]:
        """Stage 예산 조회"""
        return self._budgets.get(stage_name)
    
    def record_cost(
        self,
        operation: str,
        stage_name: str,
        cost: Optional[Decimal] = None,
        success: bool = True,
        metadata: Optional[Dict] = None,
    ) -> CostRecord:
        """
        비용 기록
        
        Args:
            operation: 작업 유형
            stage_name: Stage 이름
            cost: 비용 (None이면 기본값 사용)
            success: 성공 여부
            metadata: 추가 메타데이터
        
        Returns:
            CostRecord: 기록된 비용
            
        Raises:
            ValueError: 예산 초과 시 (hard_limit=True인 경우)
        """
        if not self._enabled:
            return CostRecord(
                operation=operation,
                cost=Decimal("0"),
                stage_name=stage_name,
                success=success,
            )
        
        # 비용 결정
        if cost is None:
            cost = self._operation_costs.get(operation, Decimal("0.001"))
        
        # 예산 체크
        budget = self._budgets.get(stage_name)
        if budget:
            new_total = budget.current_spent + cost
            
            # 알림 체크
            if budget.should_alert and not any(
                a.stage_name == stage_name and a.alert_type == "threshold"
                for a in self._alerts[-10:]  # 최근 10개만 체크
            ):
                self._create_alert(
                    alert_type="threshold",
                    stage_name=stage_name,
                    message=f"Budget usage at {budget.usage_percent:.1f}%",
                    current_cost=budget.current_spent,
                    budget_limit=budget.max_budget,
                    severity="warning",
                )
            
            # 예산 초과 체크
            if new_total > budget.max_budget:
                if budget.hard_limit:
                    self._create_alert(
                        alert_type="over_budget",
                        stage_name=stage_name,
                        message=f"Budget exceeded: ${new_total} > ${budget.max_budget}",
                        current_cost=new_total,
                        budget_limit=budget.max_budget,
                        severity="critical",
                    )
                    raise ValueError(
                        f"Budget exceeded for {stage_name}: "
                        f"${new_total} > ${budget.max_budget}"
                    )
            
            budget.current_spent = new_total
        
        # 기록 저장
        record = CostRecord(
            operation=operation,
            cost=cost,
            stage_name=stage_name,
            success=success,
            metadata=metadata or {},
        )
        self._records.append(record)
        
        return record
    
    def _create_alert(
        self,
        alert_type: str,
        stage_name: str,
        message: str,
        current_cost: Decimal,
        budget_limit: Decimal,
        severity: str = "warning",
    ) -> CostAlert:
        """알림 생성"""
        alert = CostAlert(
            alert_type=alert_type,
            stage_name=stage_name,
            message=message,
            current_cost=current_cost,
            budget_limit=budget_limit,
            severity=severity,
        )
        self._alerts.append(alert)
        logger.warning(f"Cost alert: {message}")
        
        # Audit 로깅 (Phase 3)
        self._log_finops_audit(
            stage_name=stage_name,
            alert_type=alert_type,
            current_cost=float(current_cost),
            budget_limit=float(budget_limit),
            usage_percent=(float(current_cost) / float(budget_limit) * 100) if budget_limit > 0 else None,
            severity=severity,
            message=message,
        )
        
        return alert
    
    def _log_finops_audit(
        self,
        stage_name: str,
        alert_type: str,
        current_cost: Optional[float] = None,
        budget_limit: Optional[float] = None,
        usage_percent: Optional[float] = None,
        operation: Optional[str] = None,
        severity: str = "warning",
        message: Optional[str] = None,
    ) -> None:
        """Audit 헬퍼를 통해 FinOps 이벤트 기록."""
        try:
            from selfhealing.services.audit_helpers import log_finops_audit
            
            log_finops_audit(
                stage_name=stage_name,
                alert_type=alert_type,
                current_cost=current_cost,
                budget_limit=budget_limit,
                usage_percent=usage_percent,
                operation=operation,
                severity=severity,
                message=message,
            )
        except Exception as e:
            logger.debug(f"[FinOpsService] Audit logging failed: {e}")
    
    def get_cost_tier(self, cost: Decimal) -> CostTier:
        """비용 계층 반환"""
        if cost <= 0:
            return CostTier.FREE
        elif cost < Decimal("0.001"):
            return CostTier.LOW
        elif cost < Decimal("0.01"):
            return CostTier.MEDIUM
        elif cost < Decimal("0.10"):
            return CostTier.HIGH
        else:
            return CostTier.CRITICAL
    
    def generate_report(
        self,
        period: str = "daily",
        stage_name: Optional[str] = None,
    ) -> CostReport:
        """
        비용 리포트 생성
        
        Args:
            period: 리포트 기간 (daily, weekly, monthly)
            stage_name: 특정 Stage만 (None이면 전체)
        
        Returns:
            CostReport: 비용 리포트
        """
        now = datetime.now(timezone.utc)
        
        if period == "daily":
            start_date = now - timedelta(days=1)
        elif period == "weekly":
            start_date = now - timedelta(weeks=1)
        elif period == "monthly":
            start_date = now - timedelta(days=30)
        else:
            start_date = now - timedelta(days=1)
        
        # 해당 기간의 기록 필터링
        filtered_records = [
            r for r in self._records
            if r.timestamp >= start_date
            and (stage_name is None or r.stage_name == stage_name)
        ]
        
        # 집계
        total_cost = Decimal("0.00")
        by_stage: Dict[str, Decimal] = {}
        by_operation: Dict[str, Decimal] = {}
        success_count = 0
        
        for record in filtered_records:
            total_cost += record.cost
            by_stage[record.stage_name] = by_stage.get(
                record.stage_name, Decimal("0")
            ) + record.cost
            by_operation[record.operation] = by_operation.get(
                record.operation, Decimal("0")
            ) + record.cost
            if record.success:
                success_count += 1
        
        success_rate = (
            (success_count / len(filtered_records) * 100)
            if filtered_records else 100.0
        )
        
        return CostReport(
            period=period,
            start_date=start_date,
            end_date=now,
            total_cost=total_cost,
            by_stage=by_stage,
            by_operation=by_operation,
            record_count=len(filtered_records),
            success_rate=success_rate,
        )
    
    def get_alerts(
        self,
        stage_name: Optional[str] = None,
        unacknowledged_only: bool = False,
    ) -> List[CostAlert]:
        """알림 조회"""
        alerts = self._alerts
        if stage_name:
            alerts = [a for a in alerts if a.stage_name == stage_name]
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        return alerts
    
    def acknowledge_alert(self, index: int) -> bool:
        """알림 확인 처리"""
        if 0 <= index < len(self._alerts):
            self._alerts[index].acknowledged = True
            return True
        return False
    
    def reset_budget(self, stage_name: str) -> bool:
        """예산 리셋"""
        budget = self._budgets.get(stage_name)
        if budget:
            budget.current_spent = Decimal("0.00")
            budget.last_reset = datetime.now(timezone.utc)
            logger.info(f"Budget reset for {stage_name}")
            return True
        return False
    
    def get_all_budgets(self) -> Dict[str, Dict]:
        """모든 예산 조회"""
        return {
            name: budget.to_dict()
            for name, budget in self._budgets.items()
        }
    
    def set_operation_cost(self, operation: str, cost: Decimal) -> None:
        """작업별 비용 설정"""
        self._operation_costs[operation] = cost
    
    def enable(self) -> None:
        """서비스 활성화"""
        self._enabled = True
        
    def disable(self) -> None:
        """서비스 비활성화"""
        self._enabled = False
    
    def clear(self) -> None:
        """모든 데이터 초기화 (테스트용)"""
        self._budgets.clear()
        self._records.clear()
        self._alerts.clear()
    
    # =========================================================================
    # Chaos Budget 전용 - 카오스 실험 비용 관리
    # =========================================================================
    
    # 가중치 폭발 방지
    MAX_CHAOS_WEIGHT_MULTIPLIER: float = 10.0
    
    # Chaos Budget 전용 stage 이름
    CHAOS_BUDGET_STAGE_NAME: str = "_chaos_global_pool"
    
    def set_chaos_budget(
        self,
        max_budget: Decimal,
        alert_threshold: float = 0.8,
        hard_limit: bool = True,
        reset_period: str = "monthly",
    ) -> CostBudget:
        """
        전역 카오스 실험 예산 설정.
        
        Args:
            max_budget: 월간 최대 예산 (USD)
            alert_threshold: 알림 임계값 (0.0 ~ 1.0)
            hard_limit: 예산 초과 시 실험 차단 여부
            reset_period: 리셋 주기 (daily, weekly, monthly)
            
        Returns:
            CostBudget: 설정된 예산
        """
        budget = self.set_budget(
            stage_name=self.CHAOS_BUDGET_STAGE_NAME,
            max_budget=max_budget,
            alert_threshold=alert_threshold,
            hard_limit=hard_limit,
            reset_period=reset_period,
        )
        logger.info(f"[FinOps] Chaos budget set: ${max_budget}")
        return budget
    
    def get_chaos_budget(self) -> Optional[CostBudget]:
        """
        전역 카오스 예산 조회.
        
        Returns:
            CostBudget or None if not configured
        """
        return self.get_budget(self.CHAOS_BUDGET_STAGE_NAME)
    
    def set_domain_weight(self, domain: str, weight: float) -> None:
        """
        도메인별 비용 가중치 설정.
        
        높은 가중치 = 높은 위험 도메인 = 더 많은 예산 소진
        
        Args:
            domain: 도메인 이름 (e.g., "payment", "order")
            weight: 가중치 배수 (기본 1.0, 최대 MAX_CHAOS_WEIGHT_MULTIPLIER)
        """
        if not hasattr(self, "_domain_weights"):
            self._domain_weights: Dict[str, float] = {}
        
        # Cap weight
        capped_weight = min(weight, self.MAX_CHAOS_WEIGHT_MULTIPLIER)
        self._domain_weights[domain.lower()] = capped_weight
        logger.info(f"[FinOps] Domain weight set: {domain}={capped_weight}x")
    
    def get_domain_weight(self, domain: str) -> float:
        """도메인 가중치 조회 (없으면 1.0)."""
        if not hasattr(self, "_domain_weights"):
            return 1.0
        return self._domain_weights.get(domain.lower(), 1.0)
    
    def record_chaos_cost(
        self,
        experiment_id: str,
        experiment_type: str,
        target_domain: str,
        success: bool = True,
        dry_run: bool = False,
    ) -> Optional[CostRecord]:
        """
        카오스 실험 비용 기록 (가중치 적용).
        
        Args:
            experiment_id: 실험 ID
            experiment_type: 실험 유형
            target_domain: 대상 도메인
            success: 실험 성공 여부
            dry_run: Dry Run 여부 (True이면 비용 기록 안 함)
            
        Returns:
            CostRecord or None if chaos budget not configured or dry_run
            
        Raises:
            ValueError: 예산 초과 시 (hard_limit=True인 경우)
        """
        if dry_run:
            logger.debug(f"[FinOps] Chaos cost skipped for dry_run experiment: {experiment_id}")
            return None
        
        chaos_budget = self.get_chaos_budget()
        if chaos_budget is None:
            logger.debug("[FinOps] No chaos budget configured, skipping cost recording")
            return None
        
        # 기본 비용
        base_cost = self._operation_costs.get("chaos_test", Decimal("0.02"))
        
        # 도메인 가중치 적용
        domain_weight = self.get_domain_weight(target_domain)
        final_cost = base_cost * Decimal(str(domain_weight))
        
        # 예산에서 차감
        return self.record_cost(
            operation="chaos_test",
            stage_name=self.CHAOS_BUDGET_STAGE_NAME,
            cost=final_cost,
            success=success,
            metadata={
                "experiment_id": experiment_id,
                "experiment_type": experiment_type,
                "target_domain": target_domain,
                "domain_weight": domain_weight,
                "base_cost": str(base_cost),
                "final_cost": str(final_cost),
            },
        )
    
    def get_chaos_budget_status(self) -> Dict[str, any]:
        """
        카오스 예산 현재 상태 조회.
        
        Returns:
            Dict with budget status or empty if not configured
        """
        budget = self.get_chaos_budget()
        if budget is None:
            return {
                "configured": False,
            }
        
        return {
            "configured": True,
            "max_budget": str(budget.max_budget),
            "current_spent": str(budget.current_spent),
            "remaining": str(budget.remaining),
            "usage_percent": budget.usage_percent,
            "alert_threshold": budget.alert_threshold,
            "should_alert": budget.should_alert,
            "is_over_budget": budget.is_over_budget,
            "hard_limit": budget.hard_limit,
            "reset_period": budget.reset_period,
        }

