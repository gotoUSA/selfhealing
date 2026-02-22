"""
Anti-Flapping Settings - Pydantic v2.

플래핑 방지를 위한 히스테리시스 가드 설정입니다.

Replaces:
- services/coordination/anti_flapping.py:EMERGENCY_LEVEL_COOLDOWN_SECONDS
- services/coordination/anti_flapping.py:AntiFlappingGuard 기본값들

Environment Variables:
    SELFHEALING_ANTI_FLAPPING_LEVEL_COOLDOWN_SECONDS=300
    SELFHEALING_ANTI_FLAPPING_RECOVERY_HYSTERESIS_FACTOR=1.15

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 2 [8])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §8.4
- docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class AntiFlappingSettings(BaseSettings):
    """
    플래핑 방지 히스테리시스 가드 설정.

    레벨이 빈번하게 변하며 시스템이 요동치는 플래핑 현상을 방지합니다.

    Features:
    - Emergency Level 전환 간 최소 대기 시간 (쿨다운)
    - 복구 후 재활성화 제한 (Post-Recovery Cooldown)
    - 플래핑 감지 및 자동 잠금
    - Recovery Hysteresis Factor: 복구 시 추가 안정화 시간 적용
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ANTI_FLAPPING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Cooldown Settings (from anti_flapping.py)
    # ==========================================================================
    level_cooldown_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="레벨 전환 후 다음 전환까지의 최소 대기 시간 (초)",
    )

    cooldown_after_recovery_seconds: int = Field(
        default=600,
        ge=60,
        le=7200,
        description="복구 완료 후 재활성화 제한 시간 (초)",
    )

    # ==========================================================================
    # Stability Settings
    # ==========================================================================
    min_stable_duration_before_recovery_seconds: int = Field(
        default=600,
        ge=60,
        le=7200,
        description="복구 전 최소 안정 유지 시간 (초)",
    )

    max_level_transitions_per_hour: int = Field(
        default=3,
        ge=1,
        le=20,
        description="시간당 최대 전환 횟수 (플래핑 감지 임계값)",
    )

    # ==========================================================================
    # Lockout Settings
    # ==========================================================================
    flapping_lockout_minutes: int = Field(
        default=30,
        ge=5,
        le=180,
        description="플래핑 감지 시 강제 잠금 시간 (분)",
    )

    # ==========================================================================
    # Hysteresis Settings (72번 문서 §5.1.1)
    # ==========================================================================
    recovery_hysteresis_factor: float = Field(
        default=1.15,
        ge=1.0,
        le=2.0,
        description=(
            "복구 윈도우 히스테리시스 팩터. "
            "1.15 = 복구 조건 확인에 15% 더 긴 시간 필요 (권장)"
        ),
    )

    # ==========================================================================
    # AntiFlappingWindow Settings (from services/idempotency_service.py)
    # ==========================================================================
    window_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="AntiFlappingWindow 슬라이딩 윈도우 크기 (초)",
    )

    similarity_threshold: float = Field(
        default=0.01,
        ge=0.001,
        le=0.5,
        description="유사 판정 임계값 (0.01 = 1% 이내 = 유사)",
    )

    max_similar_changes: int = Field(
        default=3,
        ge=1,
        le=20,
        description="윈도우 내 최대 유사 변경 횟수 (초과 시 플래핑 판정)",
    )

    @field_validator("recovery_hysteresis_factor")
    @classmethod
    def validate_hysteresis_factor(cls, v: float) -> float:
        """히스테리시스 팩터 경고."""
        if v < 1.1:
            logger.warning(
                "safe_default.low_recommend_stability",
                v=v,
            )
        if v > 1.5:
            logger.warning(
                "safe_default.high_delay_recovery_too",
                v=v,
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: AntiFlappingSettings | None = None


def get_anti_flapping_settings() -> AntiFlappingSettings:
    """Get cached AntiFlappingSettings instance."""
    global _settings
    if _settings is None:
        _settings = AntiFlappingSettings()
    return _settings


def reset_anti_flapping_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
