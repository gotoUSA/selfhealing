"""
Error Budget Gate Settings - Pydantic v2.

에러 예산 게이트 설정.
자동화 허용/차단을 에러 예산 기반으로 결정하는 게이트 설정.

Moved from: services/error_budget_gate/config.py (BaseSettings 전환)

Environment Variables:
    SELFHEALING_ERROR_BUDGET_GATE_ENABLED=true
    SELFHEALING_ERROR_BUDGET_GATE_CRITICAL_THRESHOLD_PERCENT=10.0
    SELFHEALING_ERROR_BUDGET_GATE_FAIL_OPEN=true

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ErrorBudgetGateSettings(BaseSettings):
    """
    에러 예산 게이트 설정.

    Attributes:
        enabled: 게이트 활성화 여부 (False면 항상 자동화 허용)
        critical_threshold_percent: 이 값 미만이면 자동화 차단 (기본: 10%)
        warning_threshold_percent: 이 값 미만이면 경고 표시 (기본: 20%)
        threshold_hysteresis_buffer_percent: 임계치 복구 시 버퍼 (플래핑 방지, 기본: 2%)
        fail_open: 에러 예산 조회 실패 시 자동화 허용 여부 (기본: True)
        cache_ttl_seconds: 에러 예산 캐시 TTL (기본: 30초)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ERROR_BUDGET_GATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=True,
        description="게이트 활성화 여부 (False면 항상 자동화 허용)",
    )
    critical_threshold_percent: float = Field(
        default=10.0,
        ge=0.0,
        le=100.0,
        description="이 값 미만이면 자동화 차단 (%)",
    )
    warning_threshold_percent: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="이 값 미만이면 경고 표시 (%)",
    )
    threshold_hysteresis_buffer_percent: float = Field(
        default=2.0,
        ge=0.0,
        le=10.0,
        description="임계치 복구 시 적용되는 버퍼 (플래핑 방지, %)",
    )
    fail_open: bool = Field(
        default=True,
        description="에러 예산 조회 실패 시 자동화 허용 여부",
    )
    cache_ttl_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="에러 예산 캐시 TTL (초)",
    )

    # Fail-Open Rate Limiting (최소한의 제약이 있는 방임)
    fail_open_rate_limit_enabled: bool = Field(
        default=True,
        description="Fail-Open 시 Rate Limit 적용 여부",
    )
    fail_open_rate_limit_per_minute: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Fail-Open 시 분당 최대 허용 횟수",
    )
    fail_open_rate_limit_window_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Rate Limit 슬라이딩 윈도우 크기 (초)",
    )

    # Circuit Breaker (빠른 실패 처리)
    circuit_breaker_enabled: bool = Field(
        default=True,
        description="Circuit Breaker 활성화 여부",
    )
    circuit_breaker_failure_threshold: int = Field(
        default=5,
        ge=1,
        le=50,
        description="연속 실패 횟수 임계값",
    )
    circuit_breaker_recovery_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        description="회로 복구 대기 시간 (초)",
    )

    # 알림 설정
    alert_on_fail_open: bool = Field(
        default=True,
        description="Fail-Open 발동 시 알림 발송 여부",
    )
    alert_cooldown_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="동일 알림 재발송 쿨다운 (초)",
    )

    def to_dict(self) -> dict[str, Any]:
        """설정을 딕셔너리로 변환."""
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
    def from_dict(cls, data: dict[str, Any]) -> "ErrorBudgetGateSettings":
        """딕셔너리에서 설정 생성 (runtime config 지원)."""
        valid_keys = {k: v for k, v in data.items() if k in cls.model_fields}
        return cls(**valid_keys)


# =============================================================================
# Singleton
# =============================================================================

_settings: ErrorBudgetGateSettings | None = None


def get_error_budget_gate_settings() -> ErrorBudgetGateSettings:
    """Get cached ErrorBudgetGateSettings instance."""
    global _settings
    if _settings is None:
        _settings = ErrorBudgetGateSettings()
    return _settings


def reset_error_budget_gate_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
