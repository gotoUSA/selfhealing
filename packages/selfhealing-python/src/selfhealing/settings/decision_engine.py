"""
DecisionEngine Settings - Pydantic v2.

조정 결정 엔진 설정.
최소 변경 비율, 샘플 수 기반 신뢰도, 변동성 기반 안정성 계수 설정.

Environment Variables:
    SELFHEALING_DECISION_MIN_CHANGE_RATIO=0.05
    SELFHEALING_DECISION_CONFIDENCE_SAMPLES_VERY_LOW=5
    SELFHEALING_DECISION_CONFIDENCE_SAMPLES_LOW=20
    SELFHEALING_DECISION_CONFIDENCE_SAMPLES_MEDIUM=50
    SELFHEALING_DECISION_CONFIDENCE_SAMPLES_HIGH=100
    SELFHEALING_DECISION_CONFIDENCE_VALUE_VERY_LOW=0.3
    SELFHEALING_DECISION_CONFIDENCE_VALUE_LOW=0.5
    SELFHEALING_DECISION_CONFIDENCE_VALUE_MEDIUM=0.65
    SELFHEALING_DECISION_CONFIDENCE_VALUE_HIGH=0.75
    SELFHEALING_DECISION_CONFIDENCE_VALUE_VERY_HIGH=0.9
    SELFHEALING_DECISION_STABILITY_CV_HIGH=0.5
    SELFHEALING_DECISION_STABILITY_CV_MEDIUM=0.2
    SELFHEALING_DECISION_STABILITY_FACTOR_UNSTABLE=0.7
    SELFHEALING_DECISION_STABILITY_FACTOR_MODERATE=0.85
    SELFHEALING_DECISION_STABILITY_FACTOR_STABLE=1.0
"""

import logging
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DecisionEngineSettings(BaseSettings):
    """
    DecisionEngine 설정.

    메트릭 분석 기반 조정 결정에 필요한 임계값 및 신뢰도 매핑 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DECISION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 최소 변경 비율
    # ==========================================================================
    min_change_ratio: float = Field(
        default=0.05,
        ge=0.01,
        le=0.5,
        description="변경이 의미있으려면 최소 이 비율 이상 변경 필요 (0.05 = 5%)",
    )

    # ==========================================================================
    # 샘플 수 기반 신뢰도 매핑 - 샘플 수 임계값
    # ==========================================================================
    confidence_samples_very_low: int = Field(
        default=5,
        ge=1,
        le=50,
        description="매우 낮은 신뢰도 샘플 수 임계값",
    )
    confidence_samples_low: int = Field(
        default=20,
        ge=5,
        le=100,
        description="낮은 신뢰도 샘플 수 임계값",
    )
    confidence_samples_medium: int = Field(
        default=50,
        ge=10,
        le=200,
        description="중간 신뢰도 샘플 수 임계값",
    )
    confidence_samples_high: int = Field(
        default=100,
        ge=20,
        le=500,
        description="높은 신뢰도 샘플 수 임계값",
    )

    # ==========================================================================
    # 샘플 수 기반 신뢰도 매핑 - 신뢰도 값
    # ==========================================================================
    confidence_value_very_low: float = Field(
        default=0.3,
        ge=0.1,
        le=0.5,
        description="샘플 수 < very_low 일 때 신뢰도",
    )
    confidence_value_low: float = Field(
        default=0.5,
        ge=0.3,
        le=0.7,
        description="샘플 수 < low 일 때 신뢰도",
    )
    confidence_value_medium: float = Field(
        default=0.65,
        ge=0.4,
        le=0.8,
        description="샘플 수 < medium 일 때 신뢰도",
    )
    confidence_value_high: float = Field(
        default=0.75,
        ge=0.5,
        le=0.9,
        description="샘플 수 < high 일 때 신뢰도",
    )
    confidence_value_very_high: float = Field(
        default=0.9,
        ge=0.7,
        le=1.0,
        description="샘플 수 >= high 일 때 신뢰도",
    )

    # ==========================================================================
    # 변동성(CV) 기반 안정성 계수 - CV 임계값
    # ==========================================================================
    stability_cv_high: float = Field(
        default=0.5,
        ge=0.2,
        le=1.0,
        description="불안정으로 판단되는 CV 임계값",
    )
    stability_cv_medium: float = Field(
        default=0.2,
        ge=0.05,
        le=0.5,
        description="보통 안정성으로 판단되는 CV 임계값",
    )

    # ==========================================================================
    # 변동성(CV) 기반 안정성 계수 - 계수 값
    # ==========================================================================
    stability_factor_unstable: float = Field(
        default=0.7,
        ge=0.3,
        le=0.9,
        description="CV > cv_high 일 때 안정성 계수",
    )
    stability_factor_moderate: float = Field(
        default=0.85,
        ge=0.6,
        le=0.95,
        description="CV > cv_medium 일 때 안정성 계수",
    )
    stability_factor_stable: float = Field(
        default=1.0,
        ge=0.8,
        le=1.0,
        description="CV <= cv_medium 일 때 안정성 계수",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "DecisionEngineSettings":
        """샘플 수 및 신뢰도 값 순서 검증."""
        # 샘플 수 순서 검증
        if not (
            self.confidence_samples_very_low
            < self.confidence_samples_low
            < self.confidence_samples_medium
            < self.confidence_samples_high
        ):
            raise ValueError(
                "Sample thresholds must be in ascending order: "
                "very_low < low < medium < high"
            )

        # 신뢰도 값 순서 검증
        if not (
            self.confidence_value_very_low
            < self.confidence_value_low
            < self.confidence_value_medium
            < self.confidence_value_high
            < self.confidence_value_very_high
        ):
            raise ValueError(
                "Confidence values must be in ascending order: "
                "very_low < low < medium < high < very_high"
            )

        # 안정성 계수 순서 검증
        if not (
            self.stability_factor_unstable
            < self.stability_factor_moderate
            < self.stability_factor_stable
        ):
            raise ValueError(
                "Stability factors must be in ascending order: "
                "unstable < moderate < stable"
            )

        # CV 임계값 순서 검증
        if self.stability_cv_medium >= self.stability_cv_high:
            raise ValueError(
                f"stability_cv_medium ({self.stability_cv_medium}) must be less than "
                f"stability_cv_high ({self.stability_cv_high})"
            )

        return self

    def get_sample_confidence(self, sample_count: int) -> float:
        """
        샘플 수에 따른 신뢰도 반환.

        Args:
            sample_count: 샘플 수

        Returns:
            신뢰도 값 (0.0 ~ 1.0)
        """
        if sample_count < self.confidence_samples_very_low:
            return self.confidence_value_very_low
        elif sample_count < self.confidence_samples_low:
            return self.confidence_value_low
        elif sample_count < self.confidence_samples_medium:
            return self.confidence_value_medium
        elif sample_count < self.confidence_samples_high:
            return self.confidence_value_high
        else:
            return self.confidence_value_very_high

    def get_stability_factor(self, coefficient_of_variation: float) -> float:
        """
        변동계수(CV)에 따른 안정성 계수 반환.

        Args:
            coefficient_of_variation: 변동계수 (표준편차/평균)

        Returns:
            안정성 계수 (0.0 ~ 1.0)
        """
        if coefficient_of_variation > self.stability_cv_high:
            return self.stability_factor_unstable
        elif coefficient_of_variation > self.stability_cv_medium:
            return self.stability_factor_moderate
        else:
            return self.stability_factor_stable


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[DecisionEngineSettings] = None


def get_decision_engine_settings() -> DecisionEngineSettings:
    """
    캐시된 DecisionEngineSettings 인스턴스 반환.

    Returns:
        DecisionEngineSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = DecisionEngineSettings()
        logger.debug(
            "[DecisionEngineSettings] Loaded: "
            f"min_change_ratio={_settings.min_change_ratio}"
        )
    return _settings


def reset_decision_engine_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("[DecisionEngineSettings] Reset")
