"""
Error Budget Data Models

Error Budget 관련 데이터 클래스들을 정의합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from selfhealing.core.timezone import now
from selfhealing.services.error_budget.enums import (
    FreezeStatus,
    OverrideType,
    get_burn_rate_thresholds,
    get_error_budget_thresholds,
)


@dataclass
class ErrorBudgetStatus:
    """Error Budget 현재 상태."""

    # SLO 정보
    slo_name: str
    slo_target: float  # e.g., 0.999 (99.9%)
    window_days: int

    # Budget 상태
    budget_total_minutes: float
    budget_consumed_minutes: float
    budget_remaining_minutes: float
    budget_remaining_percent: float

    # Burn Rate
    burn_rate_1h: float = 0.0
    burn_rate_6h: float = 0.0

    # 측정 정보
    measured_at: datetime = field(default_factory=now)
    error_count_window: int = 0
    total_requests_window: int = 0

    # 리전/티어 식별
    region: str | None = None
    """버짯이 속한 리전 (None이면 글로벌 집계)."""

    tier_id: str | None = None
    """버짯이 속한 티어 (None이면 전체)."""

    @property
    def is_healthy(self) -> bool:
        """버짓이 건강한 상태인지."""
        thresholds = get_error_budget_thresholds()
        return self.budget_remaining_percent >= thresholds["healthy"]

    @property
    def is_over_budget(self) -> bool:
        """SLO 위반 상태인지 (버짓 100% 초과 소진)."""
        return self.budget_remaining_percent < 0

    @property
    def is_critical(self) -> bool:
        """버짓이 위험 상태인지."""
        thresholds = get_error_budget_thresholds()
        return self.budget_remaining_percent < thresholds["warning"]

    @property
    def has_fast_burn(self) -> bool:
        """빠른 소진이 발생 중인지."""
        thresholds = get_burn_rate_thresholds()
        return self.burn_rate_1h >= thresholds["fast_warning"]

    @property
    def has_slow_burn(self) -> bool:
        """느린 소진이 발생 중인지."""
        thresholds = get_burn_rate_thresholds()
        return self.burn_rate_6h >= thresholds["slow_warning"]

    def to_dict(self) -> dict[str, Any]:
        """API 응답용 딕셔너리 변환."""
        return {
            "slo": {
                "name": self.slo_name,
                "target": self.slo_target,
                "target_percentage": f"{self.slo_target * 100:.2f}%",
                "window_days": self.window_days,
            },
            "budget": {
                "total_minutes": round(self.budget_total_minutes, 2),
                "consumed_minutes": round(self.budget_consumed_minutes, 2),
                "remaining_minutes": round(self.budget_remaining_minutes, 2),
                "remaining_percent": round(self.budget_remaining_percent, 2),
                "is_over_budget": self.is_over_budget,
            },
            "burn_rate": {
                "rate_1h": round(self.burn_rate_1h, 2),
                "rate_6h": round(self.burn_rate_6h, 2),
                "is_fast_burn": self.has_fast_burn,
                "is_slow_burn": self.has_slow_burn,
            },
            "health": {
                "is_healthy": self.is_healthy,
                "is_critical": self.is_critical,
                "is_over_budget": self.is_over_budget,
            },
            "metrics": {
                "error_count_window": self.error_count_window,
                "total_requests_window": self.total_requests_window,
            },
            "measured_at": self.measured_at.isoformat(),
        }


@dataclass
class DeploymentVerdict:
    """배포 정책 판정 결과."""

    status: FreezeStatus
    budget_status: ErrorBudgetStatus

    # 권고 메시지
    message: str
    recommendation: str

    # 상세 정보
    reasons: list[str] = field(default_factory=list)
    allowed_deployment_types: list[str] = field(default_factory=list)

    # 타임스탬프
    evaluated_at: datetime = field(default_factory=now)

    @property
    def can_deploy(self) -> bool:
        """배포 진행 가능 여부 (권고 기준)."""
        return self.status in (FreezeStatus.PROCEED, FreezeStatus.CAUTION)

    @property
    def requires_override(self) -> bool:
        """Override가 필요한 상태인지."""
        return self.status == FreezeStatus.FREEZE_RECOMMENDED

    def to_dict(self) -> dict[str, Any]:
        """API 응답용 딕셔너리 변환."""
        return {
            "verdict": {
                "status": self.status.value,
                "can_deploy": self.can_deploy,
                "requires_override": self.requires_override,
            },
            "message": self.message,
            "recommendation": self.recommendation,
            "reasons": self.reasons,
            "allowed_deployment_types": self.allowed_deployment_types,
            "budget_status": self.budget_status.to_dict(),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass
class FreezeDecisionRecord:
    """배포 동결 결정 기록."""

    decision_id: str
    decision_type: str  # "freeze_acknowledged", "override_approved", "freeze_lifted"
    decided_by: str
    decided_at: datetime

    # 결정 당시 상태
    budget_remaining_percent: float
    freeze_status: FreezeStatus

    # 사유
    justification: str
    override_type: OverrideType | None = None

    # 유효기간 (override의 경우)
    expires_at: datetime | None = None

    # 관련 배포 정보
    deployment_id: str | None = None
    deployment_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "budget_remaining_percent": self.budget_remaining_percent,
            "freeze_status": self.freeze_status.value,
            "justification": self.justification,
            "override_type": self.override_type.value if self.override_type else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "deployment_id": self.deployment_id,
            "deployment_name": self.deployment_name,
        }
