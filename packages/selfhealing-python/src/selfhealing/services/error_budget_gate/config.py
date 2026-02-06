"""
Error Budget Gate Configuration.

에러 예산 게이트 설정 및 상태 정의.

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


@dataclass
class ErrorBudgetGateConfig:
    """
    에러 예산 게이트 설정.

    Attributes:
        enabled: 게이트 활성화 여부 (False면 항상 자동화 허용)
        critical_threshold_percent: 이 값 미만이면 자동화 차단 (기본: 10%)
        warning_threshold_percent: 이 값 미만이면 경고 표시 (기본: 20%)
        threshold_hysteresis_buffer_percent: 임계치 복구 시 적용되는 버퍼 (플래핑 방지, 기본: 2%)
        fail_open: 에러 예산 조회 실패 시 자동화 허용 여부 (기본: True)
        cache_ttl_seconds: 에러 예산 캐시 TTL (기본: 30초)
        fail_open_rate_limit_enabled: Fail-Open 시 Rate Limit 적용 여부 (기본: True)
        fail_open_rate_limit_per_minute: Fail-Open 시 분당 최대 허용 횟수 (기본: 10)
        fail_open_rate_limit_window_seconds: Rate Limit 슬라이딩 윈도우 크기 (기본: 60초)
        circuit_breaker_enabled: Circuit Breaker 활성화 여부 (기본: True)
        circuit_breaker_failure_threshold: 연속 실패 횟수 임계값 (기본: 5)
        circuit_breaker_recovery_timeout: 회로 복구 대기 시간 초 (기본: 30)
        alert_on_fail_open: Fail-Open 발동 시 알림 발송 여부 (기본: True)
        alert_cooldown_seconds: 동일 알림 재발송 쿨다운 (기본: 300초)
    """

    enabled: bool = True
    critical_threshold_percent: float = 10.0
    warning_threshold_percent: float = 20.0
    threshold_hysteresis_buffer_percent: float = 2.0
    fail_open: bool = True
    cache_ttl_seconds: int = 30
    # Fail-Open Rate Limiting (최소한의 제약이 있는 방임)
    fail_open_rate_limit_enabled: bool = True
    fail_open_rate_limit_per_minute: int = 10
    fail_open_rate_limit_window_seconds: int = 60
    # Circuit Breaker (빠른 실패 처리)
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 30
    # 알림 설정
    alert_on_fail_open: bool = True
    alert_cooldown_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "critical_threshold_percent": self.critical_threshold_percent,
            "warning_threshold_percent": self.warning_threshold_percent,
            "threshold_hysteresis_buffer_percent": self.threshold_hysteresis_buffer_percent,
            "fail_open": self.fail_open,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "fail_open_rate_limit_enabled": self.fail_open_rate_limit_enabled,
            "fail_open_rate_limit_per_minute": self.fail_open_rate_limit_per_minute,
            "fail_open_rate_limit_window_seconds": self.fail_open_rate_limit_window_seconds,
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
            "circuit_breaker_failure_threshold": self.circuit_breaker_failure_threshold,
            "circuit_breaker_recovery_timeout": self.circuit_breaker_recovery_timeout,
            "alert_on_fail_open": self.alert_on_fail_open,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorBudgetGateConfig:
        return cls(
            enabled=data.get("enabled", True),
            critical_threshold_percent=data.get("critical_threshold_percent", 10.0),
            warning_threshold_percent=data.get("warning_threshold_percent", 20.0),
            threshold_hysteresis_buffer_percent=data.get("threshold_hysteresis_buffer_percent", 2.0),
            fail_open=data.get("fail_open", True),
            cache_ttl_seconds=data.get("cache_ttl_seconds", 30),
            fail_open_rate_limit_enabled=data.get("fail_open_rate_limit_enabled", True),
            fail_open_rate_limit_per_minute=data.get("fail_open_rate_limit_per_minute", 10),
            fail_open_rate_limit_window_seconds=data.get("fail_open_rate_limit_window_seconds", 60),
            circuit_breaker_enabled=data.get("circuit_breaker_enabled", True),
            circuit_breaker_failure_threshold=data.get("circuit_breaker_failure_threshold", 5),
            circuit_breaker_recovery_timeout=data.get("circuit_breaker_recovery_timeout", 30),
            alert_on_fail_open=data.get("alert_on_fail_open", True),
            alert_cooldown_seconds=data.get("alert_cooldown_seconds", 300),
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
        return result


__all__ = [
    "ErrorBudgetGateConfig",
    "GateStatus",
    "GateCheckResult",
]
