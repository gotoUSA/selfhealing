"""
Cascade Retention Settings - Pydantic v2.

Cascade 데이터의 Hot/Warm/Cold 계층별 보관 정책 설정입니다.

Replaces:
- audit/cascade_config.py:CascadeRetentionConfig (하드코딩된 기본값)

Environment Variables:
    SELFHEALING_CASCADE_RETENTION_HOT_RETENTION_DAYS=7
    SELFHEALING_CASCADE_RETENTION_WARM_RETENTION_DAYS=90
    SELFHEALING_CASCADE_RETENTION_COLD_RETENTION_DAYS=365

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [16])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §14.2
- docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

import logging

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CascadeRetentionSettings(BaseSettings):
    """
    Cascade 데이터 보관 정책 설정.

    Tiered Storage 모델:
    - Hot (Redis): 실시간 조회용, 짧은 보관 (7일)
    - Warm (PostgreSQL): 복잡한 쿼리, 중간 보관 (90일)
    - Cold (Archive): 법적 요구사항, 장기 보관 (365일)

    데이터 흐름:
    Hot → Warm → Cold → 삭제
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CASCADE_RETENTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Hot Tier (Redis) - from cascade_config.py
    # ==========================================================================
    hot_retention_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="Redis 내 보관 기간 (일). 빠른 조회용.",
    )

    hot_max_count: int = Field(
        default=10000,
        ge=1000,
        le=100000,
        description="Redis 내 최대 이벤트 수 (메모리 제한)",
    )

    # ==========================================================================
    # Warm Tier (PostgreSQL) - from cascade_config.py
    # ==========================================================================
    warm_retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="PostgreSQL 내 보관 기간 (일). Audit 대응용.",
    )

    # ==========================================================================
    # Cold Tier (Archive) - from cascade_config.py
    # ==========================================================================
    cold_retention_days: int = Field(
        default=365,
        ge=180,
        le=2555,  # 7년
        description="아카이브 보관 기간 (일). 법적 요구사항.",
    )

    # ==========================================================================
    # Index & Anchor - from cascade_config.py
    # ==========================================================================
    index_retention_days: int = Field(
        default=30,
        ge=7,
        le=90,
        description="인덱스 키 보관 기간 (일)",
    )

    anchor_retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="체크포인트(Anchor) 보관 기간 (일)",
    )

    # ==========================================================================
    # Buffer Settings (from cascade_config.py#L274-281)
    # ==========================================================================
    buffer_warning_threshold: float = Field(
        default=0.7,
        ge=0.5,
        le=0.9,
        description="버퍼 사용률 경고 임계치 (70%)",
    )

    buffer_critical_threshold: float = Field(
        default=0.9,
        ge=0.7,
        le=0.99,
        description="버퍼 사용률 위험 임계치 (90%)",
    )

    # ==========================================================================
    # Rate Limiting (from cascade_config.py#L288)
    # ==========================================================================
    max_events_per_second: int = Field(
        default=10000,
        ge=1,
        le=1000000,
        description=(
            "초당 최대 감사 이벤트 수 임계값. " "대기업 환경에서는 100,000+ 권장. " "이 값 초과 시 샘플링 또는 경고 발생."
        ),
    )

    # ==========================================================================
    # Cascade Auditor - from audit/cascade_auditor.py
    # ==========================================================================
    max_cascade_index_size: int = Field(
        default=10000,
        ge=1000,
        le=100000,
        description="Cascade 인덱스 최대 크기. Redis 메모리 사용량 제한용.",
    )

    @model_validator(mode="after")
    def validate_tier_order(self) -> "CascadeRetentionSettings":
        """보관 기간 순서 검증: Hot < Warm < Cold."""
        if self.hot_retention_days >= self.warm_retention_days:
            logger.warning(
                f"[CascadeRetention] hot_retention_days({self.hot_retention_days}) >= "
                f"warm_retention_days({self.warm_retention_days}). "
                "Hot tier가 Warm보다 짧아야 합니다."
            )
        if self.warm_retention_days >= self.cold_retention_days:
            logger.warning(
                f"[CascadeRetention] warm_retention_days({self.warm_retention_days}) >= "
                f"cold_retention_days({self.cold_retention_days}). "
                "Warm tier가 Cold보다 짧아야 합니다."
            )
        return self

    @field_validator("buffer_critical_threshold")
    @classmethod
    def validate_buffer_order(cls, v: float, info) -> float:
        """Critical이 Warning보다 커야 함."""
        # Note: cross-field validation은 model_validator에서 더 적합하지만
        # 여기서는 경고만 발생
        if v <= 0.7:
            logger.warning(f"[CascadeRetention] buffer_critical_threshold={v}는 낮습니다. " "0.85 이상을 권장합니다.")
        return v


# Singleton instance (cached)
_settings: CascadeRetentionSettings | None = None


def get_cascade_retention_settings() -> CascadeRetentionSettings:
    """Get cached CascadeRetentionSettings instance."""
    global _settings
    if _settings is None:
        _settings = CascadeRetentionSettings()
    return _settings


def reset_cascade_retention_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
