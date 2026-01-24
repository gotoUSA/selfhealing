"""
Error Budget Propagation Settings - Pydantic v2.

도메인 간 Error Budget 전파 설정을 관리합니다.

Replaces:
- services/error_budget/constants.py:DEFAULT_PROPAGATION_DECAY
- services/error_budget/constants.py:DEFAULT_PROPAGATION_MAX_HOPS
- services/error_budget/propagation.py:PropagationConfig

Environment Variables:
    SELFHEALING_ERRORBUDGET_PROPAGATION_DECAY_PER_HOP=0.5
    SELFHEALING_ERRORBUDGET_PROPAGATION_MAX_HOPS=3
    SELFHEALING_ERRORBUDGET_PROPAGATION_BASE_MULTIPLIER=5.0

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 2 [7])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §9.1
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ErrorBudgetPropagationSettings(BaseSettings):
    """
    도메인 전파 설정.

    장애 도메인에서 다른 도메인으로 Error Budget 영향을 전파하는 설정입니다.

    Attributes:
        decay_per_hop: 홉당 감쇠율 (1-hop: 50% 감쇠)
        max_hops: 최대 전파 홉 수
        base_multiplier: 장애 도메인 기본 가중치
        min_multiplier: 최소 가중치 (감쇠 하한)
        enabled: 전파 활성화 여부
        propagation_delay_ms: 전파 지연 시간 (ms)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ERRORBUDGET_PROPAGATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Propagation Settings (from constants.py)
    # ==========================================================================
    decay_per_hop: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="홉당 감쇠율 (1-hop: 50% 감쇠, 2-hop: 25% 감쇠)",
    )

    max_hops: int = Field(
        default=3,
        ge=1,
        le=10,
        description="최대 전파 홉 수 (순환 참조 방지 및 성능 제한)",
    )

    base_multiplier: float = Field(
        default=5.0,
        ge=1.0,
        le=20.0,
        description="장애 도메인 기본 가중치",
    )

    min_multiplier: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="최소 가중치 (감쇠 하한)",
    )

    # ==========================================================================
    # Enable/Disable
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="전파 기능 활성화 여부",
    )

    # ==========================================================================
    # Timing Settings (92문서 §5.3.1)
    # ==========================================================================
    propagation_delay_ms: int = Field(
        default=100,
        ge=0,
        le=5000,
        description="전파 지연 시간 (ms) - 급격한 전파 방지",
    )

    @field_validator("min_multiplier")
    @classmethod
    def validate_min_multiplier(cls, v: float, info) -> float:
        """min_multiplier가 base_multiplier보다 크면 경고."""
        # 다른 필드 접근이 어려우므로 기본 검증만 수행
        if v > 5.0:
            logger.warning(
                f"[SafeDefault] High min_multiplier={v}, consider lower values"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[ErrorBudgetPropagationSettings] = None


def get_error_budget_propagation_settings() -> ErrorBudgetPropagationSettings:
    """Get cached ErrorBudgetPropagationSettings instance."""
    global _settings
    if _settings is None:
        _settings = ErrorBudgetPropagationSettings()
    return _settings


def reset_error_budget_propagation_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
