"""
Domain Sensitivity Settings - Pydantic v2.

도메인별 Error Budget 민감도 가중치 설정입니다.

Replaces:
- services/error_budget/constants.py:DEFAULT_DOMAIN_SENSITIVITY
- services/error_budget/constants.py:DEFAULT_LEVEL_MULTIPLIERS

Environment Variables:
    SELFHEALING_DOMAIN_SENSITIVITY_PAYMENT=10.0
    SELFHEALING_DOMAIN_SENSITIVITY_ORDER=5.0
    SELFHEALING_DOMAIN_SENSITIVITY_INVENTORY=3.0

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [23])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §9.2, §9.3
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DomainSensitivitySettings(BaseSettings):
    """
    도메인별 민감도 가중치 설정.

    도메인 가중치:
    - payment: 결제 도메인 (10.0, 가장 중요)
    - order: 주문 도메인 (5.0)
    - inventory: 재고 도메인 (3.0)
    - notification: 알림 도메인 (1.5)
    - analytics: 분석 도메인 (1.0, 가장 낮음)

    비상 레벨 승수:
    - NORMAL: 1.0 (평상시)
    - LEVEL_1: 1.5 (경미한 장애)
    - LEVEL_2: 3.0 (중요 장애)
    - LEVEL_3: 5.0 (심각한 장애)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DOMAIN_SENSITIVITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Domain Sensitivity Weights - from error_budget/constants.py
    # ==========================================================================
    payment: float = Field(
        default=10.0,
        ge=1.0,
        le=100.0,
        description="결제 도메인 민감도 (가장 중요)",
    )

    order: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="주문 도메인 민감도",
    )

    inventory: float = Field(
        default=3.0,
        ge=1.0,
        le=30.0,
        description="재고 도메인 민감도",
    )

    notification: float = Field(
        default=1.5,
        ge=0.5,
        le=10.0,
        description="알림 도메인 민감도",
    )

    analytics: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="분석 도메인 민감도 (가장 낮음)",
    )

    default_sensitivity: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="알 수 없는 도메인의 기본 민감도",
    )

    # ==========================================================================
    # Emergency Level Multipliers - from error_budget/constants.py
    # ==========================================================================
    level_multiplier_normal: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="NORMAL 레벨 승수",
    )

    level_multiplier_level_1: float = Field(
        default=1.5,
        ge=1.0,
        le=3.0,
        description="LEVEL_1 승수",
    )

    level_multiplier_level_2: float = Field(
        default=3.0,
        ge=1.5,
        le=6.0,
        description="LEVEL_2 승수",
    )

    level_multiplier_level_3: float = Field(
        default=5.0,
        ge=3.0,
        le=10.0,
        description="LEVEL_3 승수 (가장 심각)",
    )

    def get_domain_weight(self, domain: str) -> float:
        """도메인명으로 가중치 조회."""
        domain_lower = domain.lower()
        weights = {
            "payment": self.payment,
            "order": self.order,
            "inventory": self.inventory,
            "notification": self.notification,
            "analytics": self.analytics,
        }
        return weights.get(domain_lower, self.default_sensitivity)

    def get_level_multiplier(self, level: str) -> float:
        """비상 레벨로 승수 조회."""
        level_upper = level.upper()
        multipliers = {
            "NORMAL": self.level_multiplier_normal,
            "LEVEL_1": self.level_multiplier_level_1,
            "LEVEL_2": self.level_multiplier_level_2,
            "LEVEL_3": self.level_multiplier_level_3,
        }
        return multipliers.get(level_upper, self.level_multiplier_normal)

    def as_domain_dict(self) -> dict[str, float]:
        """도메인 민감도 딕셔너리 반환."""
        return {
            "payment": self.payment,
            "order": self.order,
            "inventory": self.inventory,
            "notification": self.notification,
            "analytics": self.analytics,
        }

    def as_level_dict(self) -> dict[str, float]:
        """레벨 승수 딕셔너리 반환."""
        return {
            "NORMAL": self.level_multiplier_normal,
            "LEVEL_1": self.level_multiplier_level_1,
            "LEVEL_2": self.level_multiplier_level_2,
            "LEVEL_3": self.level_multiplier_level_3,
        }


# ==========================================================================
# Singleton 관리
# ==========================================================================
_domain_sensitivity_settings: DomainSensitivitySettings | None = None


def get_domain_sensitivity_settings() -> DomainSensitivitySettings:
    """Get cached DomainSensitivitySettings instance."""
    global _domain_sensitivity_settings
    if _domain_sensitivity_settings is None:
        _domain_sensitivity_settings = DomainSensitivitySettings()
    return _domain_sensitivity_settings


def reset_domain_sensitivity_settings() -> None:
    """Reset cached settings (for testing)."""
    global _domain_sensitivity_settings
    _domain_sensitivity_settings = None
