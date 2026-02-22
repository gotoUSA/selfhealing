"""
Corruption Shield Settings - Pydantic v2.

데이터 무결성 보호를 위한 Corruption Shield 설정입니다.

Replaces:
- services/corruption_shield/config.py:CorruptionShieldConfig (하드코딩된 기본값)

Environment Variables:
    SELFHEALING_CORRUPTION_SHIELD_Z_SCORE_THRESHOLD=3.0
    SELFHEALING_CORRUPTION_SHIELD_IQR_MULTIPLIER=1.5
    SELFHEALING_CORRUPTION_SHIELD_MIN_SAMPLES_FOR_ANOMALY=10
    SELFHEALING_CORRUPTION_SHIELD_MAX_AMOUNT=100000000

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [14])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §9.5
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class CorruptionShieldSettings(BaseSettings):
    """
    Corruption Shield 설정.

    3계층 데이터 무결성 보호:
    - L1: 스키마 검증 (필수 필드, 타입)
    - L2: 비즈니스 규칙 (금액 범위, 허용 상태)
    - L3: 이상치 탐지 (Z-Score, IQR)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CORRUPTION_SHIELD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Layer Enable/Disable (from corruption_shield/config.py)
    # ==========================================================================
    l1_enabled: bool = Field(
        default=True,
        description="L1 스키마 검증 활성화",
    )

    l2_enabled: bool = Field(
        default=True,
        description="L2 비즈니스 규칙 검증 활성화",
    )

    l3_enabled: bool = Field(
        default=True,
        description="L3 이상치 탐지 활성화",
    )

    # ==========================================================================
    # L1: Schema Validation (from corruption_shield/config.py)
    # ==========================================================================
    required_fields: list[str] = Field(
        default_factory=lambda: ["amount", "order_id"],
        description="필수 필드 목록",
    )

    max_string_length: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="문자열 최대 길이",
    )

    # ==========================================================================
    # L2: Business Rules (from corruption_shield/config.py#L25-26)
    # ==========================================================================
    min_amount: int = Field(
        default=100,
        ge=0,
        le=10000,
        description="최소 금액 (원)",
    )

    max_amount: int = Field(
        default=100_000_000,
        ge=100000,
        le=1_000_000_000,
        description="최대 금액 (원, 기본 1억)",
    )

    allowed_statuses: list[str] = Field(
        default_factory=lambda: ["DONE", "CANCELED", "PENDING"],
        description="허용되는 상태 값 목록",
    )

    # ==========================================================================
    # L3: Anomaly Detection (from corruption_shield/config.py#L29-31)
    # ==========================================================================
    z_score_threshold: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Z-Score 임계치 (표준편차 기준)",
    )

    iqr_multiplier: float = Field(
        default=1.5,
        ge=1.0,
        le=5.0,
        description="IQR 이상치 배수",
    )

    min_samples_for_anomaly: int = Field(
        default=10,
        ge=5,
        le=1000,
        description="이상치 탐지에 필요한 최소 샘플 수",
    )

    # ==========================================================================
    # Quarantine Settings (from 91 문서)
    # ==========================================================================
    quarantine_ttl_seconds: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="격리된 데이터 보관 기간 (초)",
    )

    # ==========================================================================
    # Logging Settings (from corruption_shield/config.py#L34-35)
    # ==========================================================================
    log_violations: bool = Field(
        default=True,
        description="위반 사항 로깅 활성화",
    )

    log_to_security_incident: bool = Field(
        default=True,
        description="보안 인시던트로 기록",
    )

    @field_validator("z_score_threshold")
    @classmethod
    def validate_z_score(cls, v: float) -> float:
        """Z-Score가 너무 낮으면 경고."""
        if v < 2.0:
            logger.warning(
                "corruption_shield.매우_민감합니다_오탐_false",
                v=v,
            )
        return v

    @field_validator("min_amount", "max_amount")
    @classmethod
    def validate_amount_range(cls, v: int, info) -> int:
        """금액 범위 로깅."""
        if info.field_name == "max_amount" and v > 500_000_000:
            logger.info(
                "corruption_shield.원으로_설정됨_대규모_거래가",
                v=v,
            )
        return v

    # =========================================================================
    # 하위 호환 메서드 (기존 CorruptionShieldConfig 인터페이스)
    # =========================================================================
    @classmethod
    def from_dict(cls, data: dict) -> "CorruptionShieldSettings":
        """Create settings from dictionary (backward compat with CorruptionShieldConfig)."""
        return cls(
            l1_enabled=data.get("l1_enabled", True),
            l2_enabled=data.get("l2_enabled", True),
            l3_enabled=data.get("l3_enabled", True),
            required_fields=data.get("required_fields", ["amount", "order_id"]),
            max_string_length=data.get("max_string_length", 1000),
            min_amount=data.get("min_amount", 100),
            max_amount=data.get("max_amount", 100_000_000),
            allowed_statuses=list(
                data.get("allowed_statuses", ["DONE", "CANCELED", "PENDING"])
            ),
            z_score_threshold=data.get("z_score_threshold", 3.0),
            iqr_multiplier=data.get("iqr_multiplier", 1.5),
            min_samples_for_anomaly=data.get("min_samples_for_anomaly", 10),
            log_violations=data.get("log_violations", True),
            log_to_security_incident=data.get("log_to_security_incident", True),
        )


# Singleton instance (cached)
_settings: CorruptionShieldSettings | None = None


def get_corruption_shield_settings() -> CorruptionShieldSettings:
    """Get cached CorruptionShieldSettings instance."""
    global _settings
    if _settings is None:
        _settings = CorruptionShieldSettings()
    return _settings


def reset_corruption_shield_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
