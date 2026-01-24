"""
Chaos Blast Radius Settings - Pydantic v2.

Chaos 실험의 폭발 반경(Blast Radius) 제어 설정입니다.

Replaces:
- services/chaos/blast_radius.py:BlastRadiusPolicy (하드코딩된 기본값)
- services/chaos/base/models.py:max_traffic_percent 관련 설정

Environment Variables:
    SELFHEALING_CHAOS_BLAST_RADIUS_INSTANCE_MAX_CONCURRENT=5
    SELFHEALING_CHAOS_BLAST_RADIUS_SERVICE_MAX_CONCURRENT=2
    SELFHEALING_CHAOS_BLAST_RADIUS_REGION_MAX_CONCURRENT=1
    SELFHEALING_CHAOS_BLAST_RADIUS_MAX_TRAFFIC_PERCENT_SERVICE=50.0
    SELFHEALING_CHAOS_BLAST_RADIUS_MAX_TRAFFIC_PERCENT_REGION=10.0

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [13])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §6.12, §12.1, §16.6
"""

import logging
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ChaosBlastRadiusSettings(BaseSettings):
    """
    Chaos 폭발 반경(Blast Radius) 설정.

    Chaos 실험의 영향 범위를 제한하여 안전한 실험을 보장합니다.

    Levels:
    - INSTANCE: 단일 Pod/인스턴스 (최저 위험)
    - SERVICE: 전체 서비스 (중간 위험)
    - REGION: 전체 리전/AZ (최고 위험, 승인 필요)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CHAOS_BLAST_RADIUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Concurrent Limits (from blast_radius.py#L65-71)
    # ==========================================================================
    instance_max_concurrent: int = Field(
        default=5,
        ge=1,
        le=20,
        description="INSTANCE 레벨 동시 실험 최대 수",
    )

    service_max_concurrent: int = Field(
        default=2,
        ge=1,
        le=10,
        description="SERVICE 레벨 동시 실험 최대 수",
    )

    region_max_concurrent: int = Field(
        default=1,
        ge=1,
        le=3,
        description="REGION 레벨 동시 실험 최대 수 (CRITICAL)",
    )

    # ==========================================================================
    # Auto-Approval (from blast_radius.py#L75-81)
    # ==========================================================================
    instance_auto_approve: bool = Field(
        default=True,
        description="INSTANCE 레벨 자동 승인 여부",
    )

    service_auto_approve: bool = Field(
        default=False,
        description="SERVICE 레벨 자동 승인 여부",
    )

    region_auto_approve: bool = Field(
        default=False,
        description="REGION 레벨 자동 승인 여부 (항상 False 권장)",
    )

    # ==========================================================================
    # Time-based Restrictions (from blast_radius.py#L85-91)
    # ==========================================================================
    allowed_hours_start: int = Field(
        default=2,
        ge=0,
        le=23,
        description="실험 허용 시작 시간 (UTC, 기본 02:00)",
    )

    allowed_hours_end: int = Field(
        default=6,
        ge=0,
        le=23,
        description="실험 허용 종료 시간 (UTC, 기본 06:00)",
    )

    allow_outside_window: bool = Field(
        default=False,
        description="유지보수 윈도우 외 실험 허용 여부",
    )

    # ==========================================================================
    # Traffic Restrictions (from blast_radius.py#L95-101)
    # ==========================================================================
    max_traffic_percent_instance: float = Field(
        default=100.0,
        ge=0.0,
        le=100.0,
        description="INSTANCE 레벨 최대 트래픽 영향 (%)",
    )

    max_traffic_percent_service: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="SERVICE 레벨 최대 트래픽 영향 (%)",
    )

    max_traffic_percent_region: float = Field(
        default=10.0,
        ge=0.0,
        le=50.0,
        description="REGION 레벨 최대 트래픽 영향 (%, 50% 제한)",
    )

    # ==========================================================================
    # Safety Limits (from blast_radius.py#L104-107)
    # ==========================================================================
    excluded_services: List[str] = Field(
        default_factory=list,
        description="실험 대상에서 제외할 서비스 목록",
    )

    excluded_domains: List[str] = Field(
        default_factory=list,
        description="실험 대상에서 제외할 도메인 목록",
    )

    @field_validator("region_auto_approve")
    @classmethod
    def warn_region_auto_approve(cls, v: bool) -> bool:
        """REGION 자동 승인은 위험함."""
        if v:
            logger.warning(
                "[ChaosBlastRadius] region_auto_approve=True는 매우 위험합니다. "
                "REGION 레벨 실험은 항상 수동 승인을 권장합니다."
            )
        return v

    @field_validator("max_traffic_percent_region")
    @classmethod
    def warn_high_region_traffic(cls, v: float) -> float:
        """REGION 트래픽이 높으면 경고."""
        if v > 20.0:
            logger.warning(
                f"[ChaosBlastRadius] max_traffic_percent_region={v}%는 위험한 수준입니다. "
                "10% 이하를 권장합니다."
            )
        return v


# Singleton instance (cached)
_settings: Optional[ChaosBlastRadiusSettings] = None


def get_chaos_blast_radius_settings() -> ChaosBlastRadiusSettings:
    """Get cached ChaosBlastRadiusSettings instance."""
    global _settings
    if _settings is None:
        _settings = ChaosBlastRadiusSettings()
    return _settings


def reset_chaos_blast_radius_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
