"""
Error Budget Gate Configuration (하위 호환 re-export).

실제 정의: selfhealing.settings.error_budget_gate

기존 ErrorBudgetGateConfig(dataclass) → ErrorBudgetGateSettings(BaseSettings) 으로 통합.
ErrorBudgetGateConfig 이름은 하위 호환을 위해 alias 로 유지.
GateStatus, GateCheckResult 는 순수 데이터 객체이므로 이 파일에 그대로 유지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.settings.error_budget_gate import (  # noqa: F401
    ErrorBudgetGateSettings as ErrorBudgetGateConfig,
    get_error_budget_gate_settings,
    reset_error_budget_gate_settings,
)


class GateStatus(str, Enum):
    """게이트 상태."""

    OPEN = "open"
    """자동화 허용 - 에러 예산 충분."""

    WARNING = "warning"
    """자동화 허용 (경고) - 에러 예산 낮음."""

    BLOCKED = "blocked"
    """자동화 차단 - 에러 예산 위험 수준, 수동 모드 강제."""

    FAIL_OPEN = "fail_open"
    """자동화 허용 (장애 복구 모드) - 에러 예산 조회 실패."""

    FAIL_OPEN_RATE_LIMITED = "fail_open_rate_limited"
    """자동화 차단 (Rate Limit 초과) - Fail-Open 상황에서 과도한 요청."""

    DISABLED = "disabled"
    """게이트 비활성화 - 항상 자동화 허용."""


@dataclass
class GateCheckResult:
    """게이트 체크 결과."""

    allowed: bool
    """자동화 허용 여부."""

    status: GateStatus
    """게이트 상태."""

    error_budget_percent: float | None = None
    """현재 에러 예산 잔여율 (%)."""

    threshold_percent: float = 10.0
    """차단 임계값 (%)."""

    reason: str = ""
    """상태 설명."""

    recommendation: str = ""
    """권장 조치."""

    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """체크 시각."""

    fail_open_triggered: bool = False
    """Fail-open이 발동되었는지 여부."""

    rate_limit_remaining: int | None = None
    """Rate limit 잔여 횟수 (Fail-Open 시에만 유효)."""

    rate_limit_reset_at: datetime | None = None
    """Rate limit 리셋 시각."""

    tier_id: str | None = None
    """판정 대상 티어 (None이면 글로벌)."""

    region: str | None = None
    """판정 대상 리전 (None이면 글로벌)."""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "allowed": self.allowed,
            "status": self.status.value,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "checked_at": self.checked_at.isoformat(),
            "fail_open_triggered": self.fail_open_triggered,
        }
        if self.rate_limit_remaining is not None:
            result["rate_limit_remaining"] = self.rate_limit_remaining
        if self.rate_limit_reset_at is not None:
            result["rate_limit_reset_at"] = self.rate_limit_reset_at.isoformat()
        if self.tier_id is not None:
            result["tier_id"] = self.tier_id
        if self.region is not None:
            result["region"] = self.region
        return result


__all__ = [
    "ErrorBudgetGateConfig",
    "GateStatus",
    "GateCheckResult",
]
