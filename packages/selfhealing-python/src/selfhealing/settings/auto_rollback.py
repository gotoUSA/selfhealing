"""
AutoRollbackGuard Settings - Pydantic v2.

자율 조정 실패 대비 독립 안전장치 설정.
에러율/레이턴시 임계값 및 연속 실패 횟수를 환경변수로 설정 가능.

Environment Variables:
    SELFHEALING_ROLLBACK_ERROR_RATE_MAJOR=0.1
    SELFHEALING_ROLLBACK_ERROR_RATE_CRITICAL=0.3
    SELFHEALING_ROLLBACK_LATENCY_MAJOR_MS=5000
    SELFHEALING_ROLLBACK_LATENCY_CRITICAL_MS=10000
    SELFHEALING_ROLLBACK_MAX_HEALTH_HISTORY=10000
    SELFHEALING_ROLLBACK_FAILURES_ALERT=3
    SELFHEALING_ROLLBACK_FAILURES_EMERGENCY=5
"""

import logging

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AutoRollbackSettings(BaseSettings):
    """
    AutoRollbackGuard 설정.

    시스템 상태 저하 수준 판단 및 긴급 복구 트리거를 위한 임계값 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ROLLBACK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 에러율 임계값 (0.0 ~ 1.0)
    # ==========================================================================
    error_rate_major: float = Field(
        default=0.1,
        ge=0.01,
        le=0.5,
        description="Major 등급 에러율 임계값. 이 이상이면 롤백 고려 시작.",
    )
    error_rate_critical: float = Field(
        default=0.3,
        ge=0.1,
        le=0.9,
        description="Critical 등급 에러율 임계값. 이 이상이면 즉시 롤백.",
    )

    # ==========================================================================
    # 레이턴시 임계값 (밀리초)
    # ==========================================================================
    latency_major_ms: int = Field(
        default=5000,
        ge=500,
        le=30000,
        description="Major 등급 P99 레이턴시 임계값 (ms). 5초 기본.",
    )
    latency_critical_ms: int = Field(
        default=10000,
        ge=1000,
        le=60000,
        description="Critical 등급 P99 레이턴시 임계값 (ms). 10초 기본.",
    )

    # ==========================================================================
    # 헬스체크 히스토리 크기 (Phase 2: 238_PREDICTIVE_ANOMALY_FORECASTER)
    # ==========================================================================
    max_health_history: int = Field(
        default=10000,
        ge=100,
        le=10000,
        description="헬스체크 이력 최대 개수. 30초 간격 기준 약 83시간 커버.",
    )

    # ==========================================================================
    # 연속 실패 임계값
    # ==========================================================================
    failures_alert: int = Field(
        default=3,
        ge=1,
        le=20,
        description="알림 발생 연속 실패 횟수. ALERT 상태 진입.",
    )
    failures_emergency: int = Field(
        default=5,
        ge=2,
        le=30,
        description="긴급 상태 진입 연속 실패 횟수. EMERGENCY 상태 진입.",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "AutoRollbackSettings":
        """임계값 순서 검증: major < critical."""
        if self.error_rate_major >= self.error_rate_critical:
            raise ValueError(
                f"error_rate_major ({self.error_rate_major}) must be less than "
                f"error_rate_critical ({self.error_rate_critical})"
            )
        if self.latency_major_ms >= self.latency_critical_ms:
            raise ValueError(
                f"latency_major_ms ({self.latency_major_ms}) must be less than "
                f"latency_critical_ms ({self.latency_critical_ms})"
            )
        if self.failures_alert >= self.failures_emergency:
            raise ValueError(
                f"failures_alert ({self.failures_alert}) must be less than " f"failures_emergency ({self.failures_emergency})"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: AutoRollbackSettings | None = None


def get_auto_rollback_settings() -> AutoRollbackSettings:
    """
    캐시된 AutoRollbackSettings 인스턴스 반환.

    Returns:
        AutoRollbackSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = AutoRollbackSettings()
        logger.debug(
            "[AutoRollbackSettings] Loaded: "
            f"error_rate={_settings.error_rate_major}/{_settings.error_rate_critical}, "
            f"latency={_settings.latency_major_ms}/{_settings.latency_critical_ms}ms"
        )
    return _settings


def reset_auto_rollback_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("[AutoRollbackSettings] Reset")
