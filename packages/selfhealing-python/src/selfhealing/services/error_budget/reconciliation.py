"""
Error Budget Reconciliation Service

Fail-Safe 기간 동안 누락된 에러 데이터를 사후 정정(Reconciliation)합니다.

핵심 원칙:
- "시스템은 계산하고, 반영은 사람이 결정한다"
- Shadow Budget: 참고용 사후 계산 (Primary 덮어쓰기 X)
- Excluded Period: 운영자가 명시적으로 특정 기간을 계산에서 제외
- 운영자 승인 후 Primary Budget에 반영 가능

업계 관행:
- Google SRE: Error Budget은 "정확한 회계"가 아닌 "의사결정 도구"
- Netflix: 알려진 장애 기간은 SLO 계산에서 명시적으로 제외
- Datadog: 에이전트 복구 시 버퍼된 데이터 일괄 전송 (시간 제한)

Reference:
- docs/self_healing/12_ERROR_BUDGET.md (Section 13)
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ReconciliationStatus(str, Enum):
    """Reconciliation 상태."""
    
    PENDING = "pending"
    """대기 중 - 아직 Shadow Budget 계산 전."""
    
    CALCULATED = "calculated"
    """계산 완료 - Shadow Budget 계산됨, 운영자 승인 대기."""
    
    APPROVED = "approved"
    """승인됨 - 운영자가 Primary Budget에 반영 승인."""
    
    REJECTED = "rejected"
    """거부됨 - 운영자가 반영 거부 (Excluded Period로 처리)."""
    
    APPLIED = "applied"
    """적용됨 - Primary Budget에 반영 완료."""
    
    EXCLUDED = "excluded"
    """제외됨 - 해당 기간은 Budget 계산에서 제외."""


class ApplyMode(str, Enum):
    """Shadow Budget 적용 방식."""
    
    IMMEDIATE = "immediate"
    """즉시 전액 반영."""
    
    CAPPED = "capped"
    """최대 N% 포인트까지만 반영 (나머지는 다음 주기)."""
    
    GRADUAL = "gradual"
    """점진적 반영 (시간에 따라 분산)."""


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class FailSafePeriod:
    """Fail-Safe 발동 기간 기록."""
    
    period_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    
    # 원인 정보
    trigger_reason: str = ""
    trigger_component: str = "error_budget_gate"
    
    # 통계
    fail_open_count: int = 0
    rate_limit_exceeded_count: int = 0
    
    # 상태
    is_active: bool = True
    
    @property
    def duration_seconds(self) -> float:
        """기간 (초)."""
        end = self.ended_at or now()
        return (end - self.started_at).total_seconds()
    
    @property
    def duration_minutes(self) -> float:
        """기간 (분)."""
        return self.duration_seconds / 60.0
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "period_id": self.period_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_minutes": round(self.duration_minutes, 2),
            "trigger_reason": self.trigger_reason,
            "trigger_component": self.trigger_component,
            "fail_open_count": self.fail_open_count,
            "rate_limit_exceeded_count": self.rate_limit_exceeded_count,
            "is_active": self.is_active,
        }


@dataclass
class ShadowBudget:
    """Shadow Budget 계산 결과."""
    
    calculation_id: str
    calculated_at: datetime
    
    # Fail-Safe 기간 참조
    failsafe_period_id: str
    failsafe_period_start: datetime
    failsafe_period_end: datetime
    
    # Primary Budget (현재 공식 값)
    primary_remaining_percent: float
    primary_consumed_minutes: float
    
    # Shadow Budget (사후 계산 값)
    shadow_remaining_percent: float
    shadow_consumed_minutes: float
    
    # 차이
    adjustment_percent: float  # Primary - Shadow
    adjustment_minutes: float
    
    # 데이터 소스
    estimated_errors: int = 0
    log_source: str = ""  # "prometheus", "dlq", "application_logs"
    
    # 상태
    status: ReconciliationStatus = ReconciliationStatus.PENDING
    
    # 승인 정보
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_justification: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "calculation_id": self.calculation_id,
            "calculated_at": self.calculated_at.isoformat(),
            "failsafe_period": {
                "period_id": self.failsafe_period_id,
                "start": self.failsafe_period_start.isoformat(),
                "end": self.failsafe_period_end.isoformat(),
            },
            "primary_budget": {
                "remaining_percent": round(self.primary_remaining_percent, 2),
                "consumed_minutes": round(self.primary_consumed_minutes, 2),
            },
            "shadow_budget": {
                "remaining_percent": round(self.shadow_remaining_percent, 2),
                "consumed_minutes": round(self.shadow_consumed_minutes, 2),
            },
            "adjustment": {
                "percent": round(self.adjustment_percent, 2),
                "minutes": round(self.adjustment_minutes, 2),
            },
            "estimated_errors": self.estimated_errors,
            "log_source": self.log_source,
            "status": self.status.value,
            "review": {
                "reviewed_by": self.reviewed_by,
                "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
                "justification": self.review_justification,
            } if self.reviewed_by else None,
        }


@dataclass
class ExcludedPeriod:
    """Budget 계산에서 제외된 기간."""
    
    exclusion_id: str
    started_at: datetime
    ended_at: datetime
    
    # 제외 사유
    reason: str
    excluded_by: str
    excluded_at: datetime
    
    # 연관 Fail-Safe 기간
    failsafe_period_id: Optional[str] = None
    
    # 메모
    notes: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "exclusion_id": self.exclusion_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_minutes": round((self.ended_at - self.started_at).total_seconds() / 60, 2),
            "reason": self.reason,
            "excluded_by": self.excluded_by,
            "excluded_at": self.excluded_at.isoformat(),
            "failsafe_period_id": self.failsafe_period_id,
            "notes": self.notes,
        }


# =============================================================================
# Fail-Safe Period Tracker
# =============================================================================


class FailSafePeriodTracker:
    """
    Fail-Safe 발동 기간 추적기.
    
    Error Budget Gate가 Fail-Open 상태가 되면 기간을 기록합니다.
    복구 후 이 기간들을 Reconciliation에 사용합니다.
    """
    
    def __init__(self, max_periods: int = 100):
        """
        초기화.
        
        Args:
            max_periods: 보관할 최대 기간 수
        """
        self._max_periods = max_periods
        self._periods: List[FailSafePeriod] = []
        self._active_period: Optional[FailSafePeriod] = None
        self._lock = threading.RLock()
    
    def start_period(self, reason: str, component: str = "error_budget_gate") -> FailSafePeriod:
        """
        Fail-Safe 기간 시작.
        
        Args:
            reason: 발동 사유
            component: 발동 컴포넌트
            
        Returns:
            생성된 FailSafePeriod
        """
        with self._lock:
            # 이미 활성 기간이 있으면 종료
            if self._active_period:
                self._end_current_period()
            
            period = FailSafePeriod(
                period_id=str(uuid.uuid4()),
                started_at=now(),
                trigger_reason=reason,
                trigger_component=component,
            )
            
            self._active_period = period
            self._periods.append(period)
            
            # 최대 개수 유지
            if len(self._periods) > self._max_periods:
                self._periods = self._periods[-self._max_periods:]
            
            logger.info(
                f"[FailSafeTracker] Period started: {period.period_id}, "
                f"reason: {reason}"
            )
            
            return period
    
    def end_period(self) -> Optional[FailSafePeriod]:
        """
        현재 Fail-Safe 기간 종료.
        
        Returns:
            종료된 FailSafePeriod (없으면 None)
        """
        with self._lock:
            return self._end_current_period()
    
    def _end_current_period(self) -> Optional[FailSafePeriod]:
        """내부: 현재 기간 종료."""
        if not self._active_period:
            return None
        
        period = self._active_period
        period.ended_at = now()
        period.is_active = False
        self._active_period = None
        
        logger.info(
            f"[FailSafeTracker] Period ended: {period.period_id}, "
            f"duration: {period.duration_minutes:.1f} min"
        )
        
        return period
    
    def record_fail_open(self) -> None:
        """Fail-Open 발생 기록."""
        with self._lock:
            if self._active_period:
                self._active_period.fail_open_count += 1
    
    def record_rate_limit_exceeded(self) -> None:
        """Rate Limit 초과 기록."""
        with self._lock:
            if self._active_period:
                self._active_period.rate_limit_exceeded_count += 1
    
    def get_active_period(self) -> Optional[FailSafePeriod]:
        """현재 활성 기간 조회."""
        with self._lock:
            return self._active_period
    
    def get_unreconciled_periods(self) -> List[FailSafePeriod]:
        """Reconciliation 대상 기간 조회 (종료된 기간 중 미처리)."""
        with self._lock:
            return [p for p in self._periods if not p.is_active]
    
    def get_periods_in_range(
        self,
        start: datetime,
        end: datetime,
    ) -> List[FailSafePeriod]:
        """특정 기간 내 Fail-Safe 기간 조회."""
        with self._lock:
            result = []
            for period in self._periods:
                # 기간이 겹치는지 확인
                period_end = period.ended_at or now()
                if period.started_at <= end and period_end >= start:
                    result.append(period)
            return result
    
    def get_all_periods(self, limit: int = 50) -> List[FailSafePeriod]:
        """모든 기간 조회."""
        with self._lock:
            return list(reversed(self._periods[-limit:]))
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 조회."""
        with self._lock:
            return {
                "active_period": self._active_period.to_dict() if self._active_period else None,
                "total_periods": len(self._periods),
                "unreconciled_count": len([p for p in self._periods if not p.is_active]),
            }


# =============================================================================
# Shadow Budget Calculator
# =============================================================================


class ShadowBudgetCalculator:
    """
    Shadow Budget 계산기.
    
    Fail-Safe 기간 동안 누락된 에러를 로그에서 읽어와 
    "실제로 얼마나 소진되었을지" 사후 계산합니다.
    """
    
    def __init__(
        self,
        get_error_logs: Optional[Callable[[datetime, datetime], List[Dict]]] = None,
        get_prometheus_errors: Optional[Callable[[datetime, datetime], int]] = None,
        get_dlq_entries: Optional[Callable[[datetime, datetime], int]] = None,
    ):
        """
        초기화.
        
        Args:
            get_error_logs: 애플리케이션 로그에서 에러 조회 함수
            get_prometheus_errors: Prometheus에서 에러 카운트 조회 함수
            get_dlq_entries: DLQ 엔트리 수 조회 함수
        """
        self._get_error_logs = get_error_logs
        self._get_prometheus_errors = get_prometheus_errors
        self._get_dlq_entries = get_dlq_entries
    
    def calculate_shadow_budget(
        self,
        failsafe_period: FailSafePeriod,
        primary_remaining_percent: float,
        primary_consumed_minutes: float,
        budget_total_minutes: float = 43.2,  # 99.9% SLO, 30일 기준
    ) -> ShadowBudget:
        """
        Shadow Budget 계산.
        
        Args:
            failsafe_period: Fail-Safe 기간
            primary_remaining_percent: 현재 Primary Budget 잔여율
            primary_consumed_minutes: 현재 Primary Budget 소진량 (분)
            budget_total_minutes: 전체 Budget (분)
            
        Returns:
            ShadowBudget 계산 결과
        """
        period_start = failsafe_period.started_at
        period_end = failsafe_period.ended_at or now()
        
        # 에러 수 추정 (여러 소스에서)
        estimated_errors, log_source = self._estimate_errors(period_start, period_end)
        
        # 에러를 Budget 소진량으로 변환
        # 단순화: 에러 1개 = 0.001분 소진 (실제로는 에러 심각도 등에 따라 가중치)
        error_weight_minutes = 0.001
        additional_consumed = estimated_errors * error_weight_minutes
        
        # Shadow Budget 계산
        shadow_consumed = primary_consumed_minutes + additional_consumed
        shadow_remaining = budget_total_minutes - shadow_consumed
        shadow_remaining_percent = (shadow_remaining / budget_total_minutes) * 100 if budget_total_minutes > 0 else 0
        
        # 차이 계산
        adjustment_percent = primary_remaining_percent - shadow_remaining_percent
        adjustment_minutes = additional_consumed
        
        shadow_budget = ShadowBudget(
            calculation_id=str(uuid.uuid4()),
            calculated_at=now(),
            failsafe_period_id=failsafe_period.period_id,
            failsafe_period_start=period_start,
            failsafe_period_end=period_end,
            primary_remaining_percent=primary_remaining_percent,
            primary_consumed_minutes=primary_consumed_minutes,
            shadow_remaining_percent=shadow_remaining_percent,
            shadow_consumed_minutes=shadow_consumed,
            adjustment_percent=adjustment_percent,
            adjustment_minutes=adjustment_minutes,
            estimated_errors=estimated_errors,
            log_source=log_source,
            status=ReconciliationStatus.CALCULATED,
        )
        
        logger.info(
            f"[ShadowBudget] Calculated for period {failsafe_period.period_id}: "
            f"estimated_errors={estimated_errors}, adjustment={adjustment_percent:.2f}%"
        )
        
        return shadow_budget
    
    def _estimate_errors(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[int, str]:
        """
        기간 내 에러 수 추정.
        
        여러 소스를 시도하고 가장 신뢰할 수 있는 결과를 반환합니다.
        
        Returns:
            (에러 수, 데이터 소스)
        """
        # 1. Prometheus (가장 신뢰)
        if self._get_prometheus_errors:
            try:
                count = self._get_prometheus_errors(start, end)
                if count is not None and count >= 0:
                    return count, "prometheus"
            except Exception as e:
                logger.warning(f"[ShadowBudget] Prometheus query failed: {e}")
        
        # 2. DLQ 엔트리
        if self._get_dlq_entries:
            try:
                count = self._get_dlq_entries(start, end)
                if count is not None and count >= 0:
                    return count, "dlq"
            except Exception as e:
                logger.warning(f"[ShadowBudget] DLQ query failed: {e}")
        
        # 3. 애플리케이션 로그
        if self._get_error_logs:
            try:
                logs = self._get_error_logs(start, end)
                if logs:
                    return len(logs), "application_logs"
            except Exception as e:
                logger.warning(f"[ShadowBudget] Log query failed: {e}")
        
        # 4. 기본값: Fail-Open 횟수 기반 추정
        # 보수적으로 Fail-Open 1회당 10개 에러 가정
        logger.info("[ShadowBudget] Using fallback estimation based on fail_open_count")
        return 0, "none_available"


# =============================================================================
# Reconciliation Service
# =============================================================================


@dataclass
class ReconciliationConfig:
    """Reconciliation 설정."""
    
    enabled: bool = True
    auto_calculate: bool = True  # Circuit Breaker 복구 시 자동 계산
    auto_apply: bool = False  # 자동 적용 (권장하지 않음)
    
    # Capped 적용 설정
    apply_mode: ApplyMode = ApplyMode.CAPPED
    max_adjustment_percent_per_cycle: float = 10.0  # 한 번에 최대 10%p
    
    # 제외 기간 자동 생성
    auto_exclude_short_periods: bool = True
    short_period_threshold_seconds: float = 60.0  # 1분 미만은 자동 제외
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_calculate": self.auto_calculate,
            "auto_apply": self.auto_apply,
            "apply_mode": self.apply_mode.value,
            "max_adjustment_percent_per_cycle": self.max_adjustment_percent_per_cycle,
            "auto_exclude_short_periods": self.auto_exclude_short_periods,
            "short_period_threshold_seconds": self.short_period_threshold_seconds,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReconciliationConfig":
        return cls(
            enabled=data.get("enabled", True),
            auto_calculate=data.get("auto_calculate", True),
            auto_apply=data.get("auto_apply", False),
            apply_mode=ApplyMode(data.get("apply_mode", "capped")),
            max_adjustment_percent_per_cycle=data.get("max_adjustment_percent_per_cycle", 10.0),
            auto_exclude_short_periods=data.get("auto_exclude_short_periods", True),
            short_period_threshold_seconds=data.get("short_period_threshold_seconds", 60.0),
        )


class ErrorBudgetReconciliationService:
    """
    Error Budget Reconciliation 서비스.
    
    핵심 원칙: "시스템은 계산하고, 반영은 사람이 결정한다."
    
    - Shadow Budget을 자동 계산하여 운영자에게 제시
    - 운영자가 승인하면 Primary Budget에 반영
    - 운영자가 거부하면 Excluded Period로 처리
    
    Usage:
        service = get_reconciliation_service()
        
        # Shadow Budget 조회
        shadows = service.get_pending_shadow_budgets()
        
        # 운영자 승인
        service.approve_shadow_budget(
            calculation_id="xxx",
            approved_by="ops_lead",
            justification="로그 확인 완료, 반영함"
        )
        
        # 또는 제외 처리
        service.exclude_period(
            calculation_id="xxx",
            excluded_by="ops_lead",
            reason="Chaos Engineering 실험 기간"
        )
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
            get_current_budget: 현재 Budget 조회 함수
            apply_adjustment: Budget 조정 적용 함수
        """
        self._config = config or ReconciliationConfig()
        self._period_tracker = period_tracker or FailSafePeriodTracker()
        self._shadow_calculator = shadow_calculator or ShadowBudgetCalculator()
        self._get_current_budget = get_current_budget
        self._apply_adjustment = apply_adjustment
        
        self._shadow_budgets: Dict[str, ShadowBudget] = {}
        self._excluded_periods: Dict[str, ExcludedPeriod] = {}
        self._lock = threading.RLock()
    
    # =========================================================================
    # Period Tracking
    # =========================================================================
    
    def on_failsafe_started(self, reason: str, component: str = "error_budget_gate") -> FailSafePeriod:
        """Fail-Safe 시작 시 호출."""
        return self._period_tracker.start_period(reason, component)
    
    def on_failsafe_ended(self) -> Optional[ShadowBudget]:
        """
        Fail-Safe 종료 시 호출.
        
        자동 계산이 활성화되어 있으면 Shadow Budget을 즉시 계산합니다.
        """
        period = self._period_tracker.end_period()
        if not period:
            return None
        
        # 짧은 기간 자동 제외
        if self._config.auto_exclude_short_periods:
            if period.duration_seconds < self._config.short_period_threshold_seconds:
                self._auto_exclude_short_period(period)
                return None
        
        # 자동 계산
        if self._config.auto_calculate:
            return self._calculate_shadow_budget_for_period(period)
        
        return None
    
    def _auto_exclude_short_period(self, period: FailSafePeriod) -> None:
        """짧은 기간 자동 제외."""
        with self._lock:
            exclusion = ExcludedPeriod(
                exclusion_id=str(uuid.uuid4()),
                started_at=period.started_at,
                ended_at=period.ended_at or now(),
                reason=f"Auto-excluded: duration {period.duration_seconds:.1f}s < threshold",
                excluded_by="system",
                excluded_at=now(),
                failsafe_period_id=period.period_id,
            )
            self._excluded_periods[exclusion.exclusion_id] = exclusion
            logger.info(f"[Reconciliation] Auto-excluded short period: {period.period_id}")
    
    # =========================================================================
    # Shadow Budget Management
    # =========================================================================
    
    def calculate_shadow_budget(self, period_id: str) -> Optional[ShadowBudget]:
        """특정 Fail-Safe 기간에 대한 Shadow Budget 계산."""
        periods = self._period_tracker.get_all_periods()
        period = next((p for p in periods if p.period_id == period_id), None)
        
        if not period:
            logger.warning(f"[Reconciliation] Period not found: {period_id}")
            return None
        
        if period.is_active:
            logger.warning(f"[Reconciliation] Cannot calculate for active period: {period_id}")
            return None
        
        return self._calculate_shadow_budget_for_period(period)
    
    def _calculate_shadow_budget_for_period(self, period: FailSafePeriod) -> Optional[ShadowBudget]:
        """기간에 대한 Shadow Budget 계산 (내부)."""
        # 현재 Budget 조회
        current_budget = self._get_current_budget_status()
        if not current_budget:
            logger.warning("[Reconciliation] Cannot get current budget status")
            return None
        
        primary_remaining = current_budget.get("remaining_percent", 100.0)
        primary_consumed = current_budget.get("consumed_minutes", 0.0)
        budget_total = current_budget.get("total_minutes", 43.2)
        
        # Shadow Budget 계산
        shadow = self._shadow_calculator.calculate_shadow_budget(
            failsafe_period=period,
            primary_remaining_percent=primary_remaining,
            primary_consumed_minutes=primary_consumed,
            budget_total_minutes=budget_total,
        )
        
        # 저장
        with self._lock:
            self._shadow_budgets[shadow.calculation_id] = shadow
        
        return shadow
    
    def _get_current_budget_status(self) -> Optional[Dict]:
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
                # 나머지는 다음 주기에 처리 (현재는 단순화를 위해 무시)
        
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


# =============================================================================
# Singleton Factory
# =============================================================================


_reconciliation_service: Optional[ErrorBudgetReconciliationService] = None
_period_tracker: Optional[FailSafePeriodTracker] = None


def get_period_tracker() -> FailSafePeriodTracker:
    """FailSafePeriodTracker 싱글톤 인스턴스 반환."""
    global _period_tracker
    if _period_tracker is None:
        _period_tracker = FailSafePeriodTracker()
    return _period_tracker


def get_reconciliation_service() -> ErrorBudgetReconciliationService:
    """ErrorBudgetReconciliationService 싱글톤 인스턴스 반환."""
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = ErrorBudgetReconciliationService(
            period_tracker=get_period_tracker(),
        )
    return _reconciliation_service


def configure_reconciliation_service(
    config: Optional[ReconciliationConfig] = None,
    get_error_logs: Optional[Callable] = None,
    get_prometheus_errors: Optional[Callable] = None,
    get_dlq_entries: Optional[Callable] = None,
    get_current_budget: Optional[Callable] = None,
    apply_adjustment: Optional[Callable] = None,
) -> ErrorBudgetReconciliationService:
    """Reconciliation 서비스 설정."""
    global _reconciliation_service, _period_tracker
    
    if _period_tracker is None:
        _period_tracker = FailSafePeriodTracker()
    
    shadow_calculator = ShadowBudgetCalculator(
        get_error_logs=get_error_logs,
        get_prometheus_errors=get_prometheus_errors,
        get_dlq_entries=get_dlq_entries,
    )
    
    _reconciliation_service = ErrorBudgetReconciliationService(
        config=config or ReconciliationConfig(),
        period_tracker=_period_tracker,
        shadow_calculator=shadow_calculator,
        get_current_budget=get_current_budget,
        apply_adjustment=apply_adjustment,
    )
    
    return _reconciliation_service
