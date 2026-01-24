"""
Recovery Shutdown Settings - Pydantic v2.

Single Source of Truth for recovery-aware shutdown configuration.

Replaces:
- services/coordination/recovery_shutdown.py:RecoveryAwareShutdownConfig

Environment Variables:
    SELFHEALING_SHUTDOWN_DRAIN_TIMEOUT=30.0
    SELFHEALING_SHUTDOWN_RECOVERY_EXTENSION=300.0
    SELFHEALING_SHUTDOWN_MAX_WAIT=600.0
    ... etc

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md
- docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.3
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RecoveryShutdownSettings(BaseSettings):
    """
    Recovery-aware Shutdown configuration with validation.

    K8s preStop 훅에서 현재 진행 중인 Recovery Session이 있는지 확인하고,
    있다면 종료를 지연시켜 복구 프로세스를 물리적으로 보호합니다.

    All defaults match:
    - services/coordination/recovery_shutdown.py:RecoveryAwareShutdownConfig
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SHUTDOWN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Timeout Settings
    # ==========================================================================
    default_drain_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="기본 drain 타임아웃 (초)",
    )
    recovery_extension_seconds: float = Field(
        default=300.0,
        ge=60.0,
        le=1800.0,
        description="Recovery Session 진행 중일 때 추가 대기 시간 (초)",
    )
    max_shutdown_wait_seconds: float = Field(
        default=600.0,
        ge=60.0,
        le=1800.0,
        description="최대 대기 시간 (초). K8s terminationGracePeriodSeconds와 일치해야 함",
    )

    # ==========================================================================
    # Check Interval Settings
    # ==========================================================================
    recovery_check_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="Recovery Session 체크 간격 (초)",
    )
    log_interval_seconds: float = Field(
        default=15.0,
        ge=5.0,
        le=60.0,
        description="로그 출력 간격 (초)",
    )

    # ==========================================================================
    # Behavior Settings
    # ==========================================================================
    allow_force_shutdown: bool = Field(
        default=True,
        description="최대 대기 시간 초과 시 강제 종료 허용 여부",
    )

    @field_validator("max_shutdown_wait_seconds")
    @classmethod
    def validate_max_wait_ge_drain(
        cls, v: float, info
    ) -> float:
        """Ensure max_shutdown_wait >= drain_timeout + extension."""
        drain = info.data.get("default_drain_timeout_seconds", 30.0)
        extension = info.data.get("recovery_extension_seconds", 300.0)
        min_required = drain + extension
        
        if v < min_required:
            logger.warning(
                f"max_shutdown_wait_seconds ({v}) < "
                f"drain_timeout ({drain}) + recovery_extension ({extension}) = {min_required}. "
                "Recovery may be interrupted."
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: Optional[RecoveryShutdownSettings] = None


def get_recovery_shutdown_settings() -> RecoveryShutdownSettings:
    """
    Get cached RecoveryShutdownSettings instance.

    Returns:
        RecoveryShutdownSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = RecoveryShutdownSettings()
    return _settings


def reset_recovery_shutdown_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
