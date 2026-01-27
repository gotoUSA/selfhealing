"""
Recovery Coordinator Settings - Pydantic v2.

RecoveryCoordinator의 복구 단계별 기본 설정입니다.

각 복구 레벨(LEVEL_1, LEVEL_2, LEVEL_3)별 단계 파라미터:
- wait_after_seconds: 단계 완료 후 대기 시간
- duration_minutes: 헬스 체크 지속 시간
- success_threshold: 성공률 임계값
- error_rate_threshold: 에러율 임계값

또한 안정성 검사 기본값도 포함합니다.

Environment Variables:
    SELFHEALING_RECOVERY_COORD_LEVEL3_HEALTH_CHECK_DURATION_MINUTES=5
    SELFHEALING_RECOVERY_COORD_LEVEL3_HEALTH_CHECK_SUCCESS_THRESHOLD=0.95
    SELFHEALING_RECOVERY_COORD_LEVEL3_HEALTH_CHECK_ERROR_RATE_THRESHOLD=0.1
    SELFHEALING_RECOVERY_COORD_LEVEL3_CANARY_RESUME_WAIT_AFTER=60
    SELFHEALING_RECOVERY_COORD_LEVEL3_GOVERNANCE_NORMAL_WAIT_AFTER=300
    SELFHEALING_RECOVERY_COORD_STABILITY_CHECK_DURATION_MINUTES=10
    SELFHEALING_RECOVERY_COORD_STABILITY_CHECK_ERROR_RATE_THRESHOLD=0.1
"""

import logging

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RecoveryCoordinatorSettings(BaseSettings):
    """
    RecoveryCoordinator 복구 단계 설정.

    LEVEL별 RecoveryStep 파라미터와 안정성 검사 기본값을 정의합니다.
    RecoveryCoordinator.DEFAULT_RECOVERY_STEPS의 기본값을 환경변수로 오버라이드 가능하게 합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RECOVERY_COORD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # LEVEL_3 (가장 심각한 레벨) 복구 단계 설정
    # ==========================================================================
    level3_budget_reset_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_3 BUDGET_RESET 단계 완료 후 대기 시간 (초)",
    )
    level3_health_check_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_3 HEALTH_CHECK 단계 완료 후 대기 시간 (초)",
    )
    level3_health_check_duration_minutes: int = Field(
        default=5,
        ge=1,
        le=30,
        description="LEVEL_3 HEALTH_CHECK 지속 시간 (분)",
    )
    level3_health_check_success_threshold: float = Field(
        default=0.95,
        ge=0.8,
        le=1.0,
        description="LEVEL_3 HEALTH_CHECK 성공률 임계값",
    )
    level3_health_check_error_rate_threshold: float = Field(
        default=0.1,
        ge=0.01,
        le=0.3,
        description="LEVEL_3 HEALTH_CHECK 에러율 임계값",
    )
    level3_canary_resume_wait_after: int = Field(
        default=60,
        ge=0,
        le=600,
        description="LEVEL_3 CANARY_RESUME 단계 완료 후 대기 시간 (초)",
    )
    level3_governance_normal_wait_after: int = Field(
        default=300,
        ge=0,
        le=900,
        description="LEVEL_3 GOVERNANCE_NORMAL 단계 완료 후 대기 시간 (초, 5분 안정화)",
    )

    # ==========================================================================
    # LEVEL_2 (중간 레벨) 복구 단계 설정
    # ==========================================================================
    level2_budget_reset_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_2 BUDGET_RESET 단계 완료 후 대기 시간 (초)",
    )
    level2_health_check_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_2 HEALTH_CHECK 단계 완료 후 대기 시간 (초)",
    )
    level2_health_check_duration_minutes: int = Field(
        default=3,
        ge=1,
        le=30,
        description="LEVEL_2 HEALTH_CHECK 지속 시간 (분)",
    )
    level2_health_check_success_threshold: float = Field(
        default=0.95,
        ge=0.8,
        le=1.0,
        description="LEVEL_2 HEALTH_CHECK 성공률 임계값",
    )
    level2_health_check_error_rate_threshold: float = Field(
        default=0.15,
        ge=0.01,
        le=0.3,
        description="LEVEL_2 HEALTH_CHECK 에러율 임계값",
    )
    level2_canary_resume_wait_after: int = Field(
        default=30,
        ge=0,
        le=600,
        description="LEVEL_2 CANARY_RESUME 단계 완료 후 대기 시간 (초)",
    )

    # ==========================================================================
    # LEVEL_1 (경미한 레벨) 복구 단계 설정
    # ==========================================================================
    level1_budget_reset_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_1 BUDGET_RESET 단계 완료 후 대기 시간 (초)",
    )
    level1_health_check_wait_after: int = Field(
        default=0,
        ge=0,
        le=300,
        description="LEVEL_1 HEALTH_CHECK 단계 완료 후 대기 시간 (초)",
    )
    level1_health_check_duration_minutes: int = Field(
        default=2,
        ge=1,
        le=30,
        description="LEVEL_1 HEALTH_CHECK 지속 시간 (분)",
    )
    level1_health_check_success_threshold: float = Field(
        default=0.90,
        ge=0.8,
        le=1.0,
        description="LEVEL_1 HEALTH_CHECK 성공률 임계값",
    )
    level1_health_check_error_rate_threshold: float = Field(
        default=0.2,
        ge=0.01,
        le=0.5,
        description="LEVEL_1 HEALTH_CHECK 에러율 임계값",
    )

    # ==========================================================================
    # 안정성 검사 기본 설정 (전체 복구 세션 수준)
    # ==========================================================================
    stability_check_duration_minutes: int = Field(
        default=10,
        ge=1,
        le=60,
        description="안정성 확인에 필요한 기본 시간 (분)",
    )
    stability_check_error_rate_threshold: float = Field(
        default=0.1,
        ge=0.01,
        le=0.3,
        description="안정성 확인 에러율 임계값",
    )
    stability_check_success_rate_threshold: float = Field(
        default=0.95,
        ge=0.8,
        le=1.0,
        description="안정성 확인 성공률 임계값",
    )

    # ==========================================================================
    # 복구 세션 전역 설정
    # ==========================================================================
    max_recovery_session_duration_minutes: int = Field(
        default=120,
        ge=30,
        le=480,
        description="복구 세션 최대 지속 시간 (분). 초과 시 자동 중단",
    )
    step_execution_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=1800,
        description="단일 복구 단계 실행 타임아웃 (초)",
    )

    @field_validator(
        "level3_health_check_success_threshold",
        "level2_health_check_success_threshold",
        "level1_health_check_success_threshold",
    )
    @classmethod
    def validate_success_threshold(cls, v: float) -> float:
        """성공률 임계값이 너무 낮으면 경고."""
        if v < 0.9:
            logger.warning(
                f"[RecoveryCoordinatorSettings] Success threshold {v} is low, "
                "consider using >= 0.9 for production stability"
            )
        return v

    @model_validator(mode="after")
    def validate_level_consistency(self) -> "RecoveryCoordinatorSettings":
        """레벨별 설정 일관성 검증 (LEVEL_3 > LEVEL_2 > LEVEL_1)."""
        # LEVEL_3가 가장 엄격해야 함
        if (
            self.level3_health_check_success_threshold
            < self.level2_health_check_success_threshold
        ):
            logger.warning(
                "[RecoveryCoordinatorSettings] LEVEL_3 success_threshold should be >= LEVEL_2"
            )
        if (
            self.level3_health_check_error_rate_threshold
            > self.level2_health_check_error_rate_threshold
        ):
            logger.warning(
                "[RecoveryCoordinatorSettings] LEVEL_3 error_rate_threshold should be <= LEVEL_2"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: RecoveryCoordinatorSettings | None = None


def get_recovery_coordinator_settings() -> RecoveryCoordinatorSettings:
    """캐시된 RecoveryCoordinatorSettings 인스턴스 반환."""
    global _settings
    if _settings is None:
        _settings = RecoveryCoordinatorSettings()
    return _settings


def reset_recovery_coordinator_settings() -> None:
    """캐시 초기화 (테스트용)."""
    global _settings
    _settings = None
