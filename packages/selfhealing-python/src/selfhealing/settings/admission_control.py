"""
Admission Control Settings - Pydantic v2.

HTTP 요청 유입 제어(Admission Control) 설정입니다.
Tier별 Bulkhead 격벽 동시 실행 수와 활성화 여부를 관리합니다.

Environment Variables:
    SELFHEALING_ADMISSION_CONTROL_ENABLED=true
    SELFHEALING_ADMISSION_CONTROL_TIER_CRITICAL_MAX_CONCURRENT=100
    SELFHEALING_ADMISSION_CONTROL_TIER_STANDARD_MAX_CONCURRENT=50
    SELFHEALING_ADMISSION_CONTROL_TIER_NON_ESSENTIAL_MAX_CONCURRENT=20
"""

from __future__ import annotations

import structlog
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class AdmissionControlSettings(BaseSettings):
    """HTTP 유입 제어 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ADMISSION_CONTROL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=True,
        description="Admission Control 활성화 여부",
    )

    # =========================================================================
    # Tier별 Bulkhead 최대 동시 실행 수
    # =========================================================================
    tier_critical_max_concurrent: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="critical tier 격벽 최대 동시 실행 수",
    )

    tier_standard_max_concurrent: int = Field(
        default=50,
        ge=1,
        le=500,
        description="standard tier 격벽 최대 동시 실행 수",
    )

    tier_non_essential_max_concurrent: int = Field(
        default=20,
        ge=1,
        le=200,
        description="non_essential tier 격벽 최대 동시 실행 수",
    )

    # =========================================================================
    # Tier별 Bulkhead 획득 대기 Timeout (초)
    # 0이면 즉시 실패 (Zero-Wait). Micro-burst 흡수를 위해
    # critical/standard에만 짧은 대기를 부여하고,
    # non_essential은 부하 시 가장 먼저 차단되므로 Zero-Wait 유지.
    # =========================================================================
    tier_critical_bulkhead_timeout_seconds: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="critical tier Bulkhead 획득 대기 시간 (초). 0이면 즉시 실패.",
    )

    tier_standard_bulkhead_timeout_seconds: float = Field(
        default=0.03,
        ge=0.0,
        le=1.0,
        description="standard tier Bulkhead 획득 대기 시간 (초). 0이면 즉시 실패.",
    )

    tier_non_essential_bulkhead_timeout_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="non_essential tier Bulkhead 획득 대기 시간 (초). 기본 Zero-Wait.",
    )

    def get_tier_max_concurrent(self, tier_id: str) -> int:
        """tier_id에 대응하는 Bulkhead 최대 동시 실행 수 반환."""
        tier_map = {
            "critical": self.tier_critical_max_concurrent,
            "standard": self.tier_standard_max_concurrent,
            "non_essential": self.tier_non_essential_max_concurrent,
        }
        return tier_map.get(tier_id, self.tier_standard_max_concurrent)

    def get_tier_bulkhead_timeout(self, tier_id: str) -> float | None:
        """tier별 Bulkhead 대기 timeout 반환. 0이면 None(즉시 실패)."""
        tier_map = {
            "critical": self.tier_critical_bulkhead_timeout_seconds,
            "standard": self.tier_standard_bulkhead_timeout_seconds,
            "non_essential": self.tier_non_essential_bulkhead_timeout_seconds,
        }
        value = tier_map.get(tier_id, 0.0)
        return value if value > 0 else None


# =============================================================================
# Singleton
# =============================================================================

_settings: AdmissionControlSettings | None = None
_settings_lock = threading.Lock()


def get_admission_control_settings() -> AdmissionControlSettings:
    """AdmissionControlSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = AdmissionControlSettings()
    return _settings


def reset_admission_control_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
