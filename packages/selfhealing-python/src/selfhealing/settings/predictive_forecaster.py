"""
Predictive Anomaly Forecaster Settings - Pydantic v2.

시계열 예측 기반 이상 탐지 엔진의 설정.
HoltLinearForecaster, ZScoreDetector, IQRDetector, SpikeClassifier,
StateBackend 영속성 등 모든 예측 시스템 파라미터를 환경변수로 주입.

환경변수 접두사: SELFHEALING_FORECASTER_

Usage:
    from selfhealing.settings.predictive_forecaster import (
        get_predictive_forecaster_settings,
    )

    settings = get_predictive_forecaster_settings()
    alpha = settings.ewma_alpha
    beta = settings.holt_beta
"""

from __future__ import annotations

import structlog
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class PredictiveForecasterSettings(BaseSettings):
    """
    Predictive Anomaly Forecaster 설정.

    HoltLinearForecaster(이중지수평활) 기반 시계열 예측 엔진,
    ZScoreDetector/IQRDetector 이상 탐지, SpikeClassifier 급증 분류기,
    StateBackend 기반 Cold Start 방지 등의 파라미터를 관리한다.

    DomainSensitivitySettings (settings/domain_sensitivity.py)와 동일한
    Pydantic v2 BaseSettings + env_prefix 패턴.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_FORECASTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ── HoltLinearForecaster (이중지수평활 예측기) ──

    ewma_alpha: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="레벨 평활 계수 (α). EWMAForecaster smoothing에도 공유.",
    )
    holt_beta: float = Field(
        default=0.1,
        ge=0.001,
        le=1.0,
        description=(
            "Holt Linear 트렌드 평활 계수 (β). "
            "작을수록 트렌드 변화에 보수적 (0.01~0.05: 장기), "
            "클수록 트렌드 변화에 민감 (0.1~0.3: 단기)."
        ),
    )

    # ── 이상 탐지 ──

    zscore_threshold: float = Field(
        default=3.0,
        ge=1.0,
        le=5.0,
        description="Z-Score 이상 탐지 임계값 (3.0 = 99.7% 신뢰구간).",
    )
    zscore_window: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Z-Score 이동 윈도우 크기.",
    )
    iqr_multiplier: float = Field(
        default=1.5,
        ge=1.0,
        le=5.0,
        description="IQR 이상 탐지 배수 (1.5 = 일반, 3.0 = 극단적).",
    )

    # ── 히스토리 / Cold Start ──

    max_history: int = Field(
        default=10000,
        ge=100,
        le=10000,
        description="예측 히스토리 ring buffer 크기.",
    )
    warmup_samples: int = Field(
        default=30,
        ge=5,
        le=1000,
        description=("Cold Start 보호: 이 수 미만의 데이터포인트에서는 " "confidence를 0으로 반환하여 예측 기반 조치를 억제."),
    )

    # ── 예측 ──

    prediction_steps: int = Field(
        default=5,
        ge=1,
        le=30,
        description="기본 예측 스텝 수 (60초 간격 시 5 = 5분 후 예측).",
    )

    # ── 사전 조치 ──

    min_confidence_for_action: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="사전 조치 실행에 필요한 최소 예측 신뢰도.",
    )
    dry_run: bool = Field(
        default=True,
        description="도입 초기 기본값: DRY_RUN. 예측 정확도 검증 후 False로 전환.",
    )

    # ── 민감도 ──

    sensitivity_multiplier: float = Field(
        default=1.0,
        ge=0.1,
        le=100.0,
        description=(
            "도메인별 민감도 배율. "
            "DomainSensitivitySettings와 동일한 0.1~100.0 범위. "
            "payment 도메인은 10.0+, 낮은 중요도는 0.5 등. "
            "SpikeClassifier의 임계값에 반비례 적용 "
            "(높은 민감도 = 낮은 임계값 = 더 민감한 탐지)."
        ),
    )

    # ── SpikeClassifier (급증 유형 분류기) ──

    spike_error_rate_threshold: float = Field(
        default=0.05,
        ge=0.001,
        le=1.0,
        description=(
            "SpikeClassifier: 에러율 변화량이 이 값 초과 시 " "ANOMALOUS_SPIKE로 분류 (sensitivity_multiplier 적용 전 기본값)."
        ),
    )
    spike_acceleration_threshold: float = Field(
        default=2.0,
        ge=0.1,
        le=100.0,
        description="SpikeClassifier: RPS 가속도 임계값.",
    )

    # ── StateBackend 영속성 ──

    state_save_interval: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="StateBackend 저장 주기 (초). 기본 5분(300초).",
    )
    state_ttl: int = Field(
        default=259200,
        ge=3600,
        le=604800,
        description="StateBackend 상태 TTL (초). 기본 72시간(259,200초).",
    )

    @model_validator(mode="after")
    def validate_confidence_range(self) -> PredictiveForecasterSettings:
        """warmup_samples가 prediction_steps보다 충분히 큰지 검증."""
        if self.warmup_samples < self.prediction_steps:
            raise ValueError(
                f"warmup_samples({self.warmup_samples})은 " f"prediction_steps({self.prediction_steps}) 이상이어야 합니다."
            )
        return self


# ── Singleton ──

_settings: PredictiveForecasterSettings | None = None


def get_predictive_forecaster_settings() -> PredictiveForecasterSettings:
    """PredictiveForecasterSettings 싱글톤 인스턴스 반환."""
    global _settings
    if _settings is None:
        _settings = PredictiveForecasterSettings()
    return _settings


def reset_predictive_forecaster_settings() -> None:
    """싱글톤 인스턴스 초기화 (테스트 용도)."""
    global _settings
    _settings = None
