"""
SafeGauge Settings - Pydantic v2.

SafeGauge 레이블 조합 관리 설정입니다.
Prometheus 메트릭 카디널리티 폭발을 방지합니다.

Replaces:
- metrics/safe_gauge/core.py:DEFAULT_MAX_LABEL_COMBINATIONS

Environment Variables:
    SELFHEALING_SAFE_GAUGE_MAX_LABEL_COMBINATIONS=1000

Usage:
    from selfhealing.settings.safe_gauge import get_safe_gauge_settings
    settings = get_safe_gauge_settings()
    max_combinations = settings.max_label_combinations
"""

import structlog

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class SafeGaugeSettings(BaseSettings):
    """
    SafeGauge 설정.

    LRU 캐시 기반의 레이블 조합 관리 설정입니다.
    카디널리티 폭발(Cardinality Explosion)을 방지합니다.

    환경별 권장값:
    - 단일 서버: 1000 (기본값)
    - K8s 10 Pods: 500
    - K8s 100+ Pods: 200

    Attributes:
        max_label_combinations: 캐시할 최대 레이블 조합 수
        eviction_warning_threshold: Eviction 경고 임계치 (%)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SAFE_GAUGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Label Combinations - from metrics/safe_gauge/core.py
    # ==========================================================================
    max_label_combinations: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="캐시할 최대 레이블 조합 수. 초과 시 LRU로 오래된 조합 제거.",
    )

    # ==========================================================================
    # Eviction Monitoring
    # ==========================================================================
    eviction_warning_threshold: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        description="Eviction 경고 임계치. 80%에 도달하면 경고 로그.",
    )

    @field_validator("max_label_combinations")
    @classmethod
    def validate_max_label_combinations(cls, v: int) -> int:
        """레이블 조합 수가 적절한지 경고."""
        if v > 5000:
            logger.warning(
                f"[SafeGauge] max_label_combinations={v}는 메모리 사용량이 클 수 있습니다. "
                "K8s 환경에서는 500 이하를 권장합니다."
            )
        return v


# ==========================================================================
# Singleton 관리
# ==========================================================================
_safe_gauge_settings: SafeGaugeSettings | None = None


def get_safe_gauge_settings() -> SafeGaugeSettings:
    """Get cached SafeGaugeSettings instance."""
    global _safe_gauge_settings
    if _safe_gauge_settings is None:
        _safe_gauge_settings = SafeGaugeSettings()
    return _safe_gauge_settings


def reset_safe_gauge_settings() -> None:
    """Reset cached settings (for testing)."""
    global _safe_gauge_settings
    _safe_gauge_settings = None
