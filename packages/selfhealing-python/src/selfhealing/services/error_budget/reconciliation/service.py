"""
Error Budget Reconciliation Service.

Main service orchestrating reconciliation workflow.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from selfhealing.core.timezone import now

from .enums import ApplyMode, ReconciliationStatus
from .models import ExcludedPeriod, FailSafePeriod, ReconciliationConfig, ShadowBudget
from .period_tracker import FailSafePeriodTracker
from .shadow_calculator import ShadowBudgetCalculator

logger = logging.getLogger(__name__)


class ErrorBudgetReconciliationService:
    """
    Error Budget Reconciliation Service.
    
    Circuit Breaker Fail-Safe 기간 동안 누락된 에러 소진량을 사후 정산하여
    Shadow Budget을 계산하고, 승인 시 Primary Budget에 반영합니다.
    
    Workflow:
    1. Fail-Safe 기간 감지/기록 (FailSafePeriodTracker)
    2. Shadow Budget 계산 (ShadowBudgetCalculator)
    3. 승인/거부 대기 (4-Eyes Principle)
    4. Primary Budget에 적용 또는 Excluded Period 생성
    """
    
    def __init__(
        self,
        config: Optional[ReconciliationConfig] = None,
        period_tracker: Optional[FailSafePeriodTracker] = None,
        shadow_calculator: Optional[ShadowBudgetCalculator] = None,
        get_current_budget: Optional[Callable[[], Dict]] = None,
        apply_adjustment: Optional[Callable[[float], None]] = None,
    ):
        """
        초기화.
        
        Args:
            config: Reconciliation 설정
            period_tracker: Fail-Safe 기간 추적기
            shadow_calculator: Shadow Budget 계산기
            get_current_budget: 현재 Budget 상태 조회 함수
            apply_adjustment: Primary Budget에 조정 적용 함수
        """
        self._config = config or ReconciliationConfig()
        self._period_tracker = period_tracker or FailSafePeriodTracker()
        self._shadow_calculator = shadow_calculator or ShadowBudgetCalculator()
        self._get_current_budget = get_current_budget
        self._apply_adjustment = apply_adjustment
        
        # 상태 저장소
        self._shadow_budgets: Dict[str, ShadowBudget] = {}
        self._excluded_periods: Dict[str, ExcludedPeriod] = {}
        self._lock = threading.RLock()
    
    # =========================================================================
    # Period Tracker Integration
    # =========================================================================
    
    def start_failsafe_period(
        self,
        service_name: str,
        reason: str,
        triggered_by: str = "system",
    ) -> FailSafePeriod:
        """Fail-Safe 기간 시작."""
        return self._period_tracker.start_period(service_name, reason, triggered_by)
    
    def end_failsafe_period(
        self,
        period_id: str,
        auto_calculate_shadow: bool = True,
    ) -> Optional[FailSafePeriod]:
        """
        Fail-Safe 기간 종료 및 Shadow Budget 자동 계산.
        """
        period = self._period_tracker.end_period(period_id)
        
        if period and self._config.enabled and auto_calculate_shadow:
            # Shadow Budget 자동 계산
            self.calculate_shadow_budget(period_id)
        
        return period
    
    def record_fail_open(self, period_id: str) -> None:
        """Fail-Open 이벤트 기록."""
        self._period_tracker.record_fail_open(period_id)
    
    def get_active_periods(self) -> List[FailSafePeriod]:
        """활성 Fail-Safe 기간 목록."""
        return self._period_tracker.get_active_periods()
    
    # =========================================================================
    # Shadow Budget Calculation
    # =========================================================================
    
    def calculate_shadow_budget(
        self,
        failsafe_period_id: str,
    ) -> Optional[ShadowBudget]:
        """
        특정 Fail-Safe 기간에 대한 Shadow Budget 계산.
        
        기간 종료 후 호출되어야 하며, 로그/메트릭에서 에러를 추정합니다.
        """
        with self._lock:
            period = self._period_tracker.get_period(failsafe_period_id)
            if not period:
                logger.warning(f"[Reconciliation] Period not found: {failsafe_period_id}")
                return None
            
            if not period.ended_at:
                logger.warning(f"[Reconciliation] Period not ended yet: {failsafe_period_id}")
                return None
            
            # 현재 Budget 상태 조회
            budget_status = self._get_current_budget_status()
            
            # Shadow Budget 계산
            shadow = self._shadow_calculator.calculate_shadow_budget(
                failsafe_period=period,
                primary_remaining_percent=budget_status.get("remaining_percent", 100.0),
                primary_consumed_minutes=budget_status.get("consumed_minutes", 0.0),
                budget_total_minutes=budget_status.get("total_minutes", 43.2),
            )
            
            self._shadow_budgets[shadow.calculation_id] = shadow
        
        return shadow
    
    def _get_current_budget_status(self) -> Dict[str, Any]:
        """현재 Budget 상태 조회."""
        if self._get_current_budget:
            try:
                return self._get_current_budget()
            except Exception as e:
                logger.error(f"[Reconciliation] Failed to get current budget: {e}")
        
        # 기본값
        return {
            "remaining_percent": 100.0,
            "consumed_minutes": 0.0,
            "total_minutes": 43.2,
        }
    
    def get_pending_shadow_budgets(self) -> List[ShadowBudget]:
        """승인 대기 중인 Shadow Budget 목록."""
        with self._lock:
            return [
                sb for sb in self._shadow_budgets.values()
                if sb.status == ReconciliationStatus.CALCULATED
            ]
    
    def get_shadow_budget(self, calculation_id: str) -> Optional[ShadowBudget]:
        """특정 Shadow Budget 조회."""
        with self._lock:
            return self._shadow_budgets.get(calculation_id)
    
    def get_all_shadow_budgets(self, limit: int = 50) -> List[ShadowBudget]:
        """모든 Shadow Budget 조회."""
        with self._lock:
            budgets = list(self._shadow_budgets.values())
            return sorted(budgets, key=lambda x: x.calculated_at, reverse=True)[:limit]
    
    # =========================================================================
    # Approval & Application
    # =========================================================================
    
    def approve_shadow_budget(
        self,
        calculation_id: str,
        approved_by: str,
        justification: str,
    ) -> Optional[ShadowBudget]:
        """
        Shadow Budget 승인.
        
        승인 후 Primary Budget에 반영됩니다.
        Capped 모드인 경우 최대 N%까지만 반영됩니다.
        """
        with self._lock:
            shadow = self._shadow_budgets.get(calculation_id)
            if not shadow:
                logger.warning(f"[Reconciliation] Shadow budget not found: {calculation_id}")
                return None
            
            if shadow.status != ReconciliationStatus.CALCULATED:
                logger.warning(f"[Reconciliation] Invalid status for approval: {shadow.status}")
                return None
            
            # 승인 기록
            shadow.status = ReconciliationStatus.APPROVED
            shadow.reviewed_by = approved_by
            shadow.reviewed_at = now()
            shadow.review_justification = justification
            
            logger.info(
                f"[Reconciliation] Shadow budget approved: {calculation_id}, "
                f"by: {approved_by}, adjustment: {shadow.adjustment_percent:.2f}%"
            )
            
            # Primary Budget에 적용
            self._apply_to_primary(shadow)
            
            return shadow
    
    def _apply_to_primary(self, shadow: ShadowBudget) -> None:
        """Primary Budget에 조정 적용 + 이력 저장."""
        adjustment = shadow.adjustment_percent
        
        # Capped 모드 적용
        if self._config.apply_mode == ApplyMode.CAPPED:
            max_adj = self._config.max_adjustment_percent_per_cycle
            if adjustment > max_adj:
                logger.info(
                    f"[Reconciliation] Capping adjustment: {adjustment:.2f}% -> {max_adj:.2f}%"
                )
                adjustment = max_adj
        
        # 적용 콜백 호출
        if self._apply_adjustment:
            try:
                self._apply_adjustment(adjustment)
                shadow.status = ReconciliationStatus.APPLIED
                logger.info(f"[Reconciliation] Applied adjustment: {adjustment:.2f}%")
            except Exception as e:
                logger.error(f"[Reconciliation] Failed to apply adjustment: {e}")
        else:
            shadow.status = ReconciliationStatus.APPLIED
            logger.info(f"[Reconciliation] Adjustment recorded (no apply callback): {adjustment:.2f}%")
        
        # ConfigHistory에 기록
        self._save_reconciliation_to_history(shadow, adjustment)

    def _save_reconciliation_to_history(
        self,
        shadow: ShadowBudget,
        adjustment: float,
    ) -> None:
        """
        Reconciliation 결과를 ConfigHistory에 저장.
        
        Graceful Degradation: History 저장 실패해도 설정 변경은 성공.
        """
        try:
            from selfhealing.services.config_history import get_config_history_service
            history_service = get_config_history_service()
            history_service.save_version(
                config_type="error_budget",
                values={
                    "reconciliation_id": shadow.calculation_id,
                    "failsafe_period_id": shadow.failsafe_period_id,
                    "adjustment_percent": round(adjustment, 2),
                    "primary_remaining_before": round(shadow.primary_remaining_percent, 2),
                    "primary_remaining_after": round(shadow.primary_remaining_percent - adjustment, 2),
                    "shadow_remaining_percent": round(shadow.shadow_remaining_percent, 2),
                    "estimated_errors": shadow.estimated_errors,
                    "log_source": shadow.log_source,
                    "apply_mode": self._config.apply_mode.value,
                },
                changed_by=shadow.reviewed_by or "system",
                reason=f"Shadow Budget Reconciliation: {shadow.review_justification or 'approved'}",
            )
            logger.debug(
                f"[Reconciliation] Saved to history: calculation_id={shadow.calculation_id}"
            )
        except Exception as e:
            # Graceful Degradation - 히스토리 저장 실패해도 설정 변경은 성공
            logger.warning(f"[Reconciliation] Failed to save history: {e}")
    
    def reject_shadow_budget(
        self,
        calculation_id: str,
        rejected_by: str,
        reason: str,
    ) -> Optional[ShadowBudget]:
        """
        Shadow Budget 거부 (Excluded Period로 처리).
        """
        with self._lock:
            shadow = self._shadow_budgets.get(calculation_id)
            if not shadow:
                return None
            
            if shadow.status != ReconciliationStatus.CALCULATED:
                return None
            
            # 거부 기록
            shadow.status = ReconciliationStatus.REJECTED
            shadow.reviewed_by = rejected_by
            shadow.reviewed_at = now()
            shadow.review_justification = reason
            
            # Excluded Period 생성
            exclusion = ExcludedPeriod(
                exclusion_id=str(uuid.uuid4()),
                started_at=shadow.failsafe_period_start,
                ended_at=shadow.failsafe_period_end,
                reason=reason,
                excluded_by=rejected_by,
                excluded_at=now(),
                failsafe_period_id=shadow.failsafe_period_id,
            )
            self._excluded_periods[exclusion.exclusion_id] = exclusion
            
            logger.info(
                f"[Reconciliation] Shadow budget rejected: {calculation_id}, "
                f"by: {rejected_by}, excluded period created"
            )
            
            return shadow
    
    # =========================================================================
    # Excluded Periods
    # =========================================================================
    
    def exclude_period(
        self,
        start: datetime,
        end: datetime,
        reason: str,
        excluded_by: str,
        notes: str = "",
    ) -> ExcludedPeriod:
        """기간을 Budget 계산에서 제외."""
        with self._lock:
            exclusion = ExcludedPeriod(
                exclusion_id=str(uuid.uuid4()),
                started_at=start,
                ended_at=end,
                reason=reason,
                excluded_by=excluded_by,
                excluded_at=now(),
                notes=notes,
            )
            self._excluded_periods[exclusion.exclusion_id] = exclusion
            
            logger.info(
                f"[Reconciliation] Period excluded: {exclusion.exclusion_id}, "
                f"by: {excluded_by}, reason: {reason}"
            )
            
            return exclusion
    
    def get_excluded_periods(self, limit: int = 50) -> List[ExcludedPeriod]:
        """제외된 기간 목록 조회."""
        with self._lock:
            periods = list(self._excluded_periods.values())
            return sorted(periods, key=lambda x: x.excluded_at, reverse=True)[:limit]
    
    def remove_exclusion(self, exclusion_id: str) -> bool:
        """제외 기간 삭제 (재포함)."""
        with self._lock:
            if exclusion_id in self._excluded_periods:
                del self._excluded_periods[exclusion_id]
                logger.info(f"[Reconciliation] Exclusion removed: {exclusion_id}")
                return True
            return False
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self) -> ReconciliationConfig:
        """설정 조회."""
        return self._config
    
    def update_config(self, **kwargs) -> ReconciliationConfig:
        """설정 업데이트."""
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        return self._config
    
    # =========================================================================
    # Status
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """전체 상태 조회."""
        with self._lock:
            pending = [sb for sb in self._shadow_budgets.values() if sb.status == ReconciliationStatus.CALCULATED]
            applied = [sb for sb in self._shadow_budgets.values() if sb.status == ReconciliationStatus.APPLIED]
            
            return {
                "enabled": self._config.enabled,
                "period_tracker": self._period_tracker.get_status(),
                "shadow_budgets": {
                    "pending_count": len(pending),
                    "applied_count": len(applied),
                    "total_count": len(self._shadow_budgets),
                },
                "excluded_periods_count": len(self._excluded_periods),
                "config": self._config.to_dict(),
            }
