"""
Reconciliation Data Models.

Dataclasses for fail-safe periods, shadow budgets, and configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from selfhealing.core.timezone import now

from .enums import ReconciliationStatus, ApplyMode


@dataclass
class FailSafePeriod:
    """Fail-Safe 발동 기간 기록."""
    
    period_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    
    # 대상 서비스 (도메인 프리 설계)
    service_name: str = ""
    
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
    
    # 정확도 검증 (Phase 8: Accuracy Audit)
    # Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §5.2.2
    verified_at: Optional[datetime] = None
    accuracy_variance_percent: Optional[float] = None
    
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
    
    # 투명성 강화: 제외 당시 원본 데이터 (Phase 7)
    # Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §5.2.1
    original_estimated_errors: Optional[int] = None
    original_log_source: Optional[str] = None
    original_adjustment_percent: Optional[float] = None
    
    # 메모 (deprecated, 전용 필드 사용 권장)
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
            "original_estimated_errors": self.original_estimated_errors,
            "original_log_source": self.original_log_source,
            "original_adjustment_percent": self.original_adjustment_percent,
            "notes": self.notes,
        }


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
