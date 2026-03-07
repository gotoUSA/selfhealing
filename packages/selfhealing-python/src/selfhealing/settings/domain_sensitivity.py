"""
Domain Sensitivity Settings - Pydantic v2.

도메인별 Error Budget 민감도 가중치 설정입니다.
도메인 목록은 코드에 고정되지 않고, 설정(환경변수/.env)으로 관리합니다.

Replaces:
- services/error_budget/constants.py:DEFAULT_DOMAIN_SENSITIVITY
- services/error_budget/constants.py:DEFAULT_LEVEL_MULTIPLIERS

Environment Variables:
    SELFHEALING_DOMAIN_SENSITIVITY_DOMAINS='{"payment": 10.0, "order": 5.0}'
    SELFHEALING_DOMAIN_SENSITIVITY_DEFAULT_SENSITIVITY=1.0

"""


import structlog
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()

# ==========================================================================
# 도메인별 가중치 기본값 (SSOT)
# 코드 변경 없이 환경변수로 오버라이드 / 확장 가능
# ==========================================================================
DEFAULT_DOMAIN_WEIGHTS: dict[str, float] = {
    "payment": 10.0,
    "order": 5.0,
    "inventory": 3.0,
    "notification": 1.5,
    "analytics": 1.0,
}

# 도메인 가중치 허용 범위
DOMAIN_WEIGHT_MIN: float = 0.1
DOMAIN_WEIGHT_MAX: float = 100.0


class DomainSensitivitySettings(BaseSettings):
    """
    도메인별 민감도 가중치 설정.

    도메인 목록과 가중치를 dict 단일 필드로 관리합니다.
    새 도메인 추가 시 코드 변경 없이 환경변수만 수정하면 됩니다.

    환경변수 예시:
        SELFHEALING_DOMAIN_SENSITIVITY_DOMAINS='{"payment": 10.0, "logistics": 2.0}'

    비상 레벨 승수:
    - NORMAL: 1.0 (평상시)
    - LEVEL_1: 1.5 (경미한 장애)
    - LEVEL_2: 3.0 (중요 장애)
    - LEVEL_3: 5.0 (심각한 장애)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DOMAIN_SENSITIVITY_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Domain Sensitivity Weights - 단일 dict로 관리
    # ==========================================================================
    domains: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_DOMAIN_WEIGHTS),
        description=("도메인별 민감도 가중치. " "환경변수: SELFHEALING_DOMAIN_SENSITIVITY_DOMAINS='{\"payment\": 10.0}'"),
    )

    default_sensitivity: float = Field(
        default=1.0,
        ge=DOMAIN_WEIGHT_MIN,
        le=DOMAIN_WEIGHT_MAX,
        description="알 수 없는 도메인의 기본 민감도",
    )

    # ==========================================================================
    # Emergency Level Multipliers
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

    @model_validator(mode="after")
    def _validate_domain_weights(self) -> "DomainSensitivitySettings":
        """모든 도메인 가중치가 허용 범위 내인지 검증."""
        for domain, weight in self.domains.items():
            if not (DOMAIN_WEIGHT_MIN <= weight <= DOMAIN_WEIGHT_MAX):
                raise ValueError(
                    f"도메인 '{domain}' 가중치 {weight}은(는) "
                    f"허용 범위 [{DOMAIN_WEIGHT_MIN}, {DOMAIN_WEIGHT_MAX}]를 벗어남"
                )
        return self

    def get_domain_weight(self, domain: str) -> float:
        """도메인명으로 가중치 조회."""
        return self.domains.get(domain.lower(), self.default_sensitivity)

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
        return dict(self.domains)

    def as_level_dict(self) -> dict[str, float]:
        """레벨 승수 딕셔너리 반환."""
        return {
            "NORMAL": self.level_multiplier_normal,
            "LEVEL_1": self.level_multiplier_level_1,
            "LEVEL_2": self.level_multiplier_level_2,
            "LEVEL_3": self.level_multiplier_level_3,
        }


def get_domain_sensitivity_settings() -> "DomainSensitivitySettings":
    from selfhealing.settings.root import get_config

    return get_config().security_group.domain_sensitivity


def reset_domain_sensitivity_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().security_group.__dict__["domain_sensitivity"]
    except KeyError:
        pass
