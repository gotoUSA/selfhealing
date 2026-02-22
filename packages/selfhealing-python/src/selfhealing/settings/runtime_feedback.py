"""
RuntimeFeedbackLoop Settings - Pydantic v2.

실시간 피드백 루프 자율 조정 설정.
연속 실패, 롤백 쿨다운, 조정 후 대기 시간 등을 환경변수로 설정 가능.

Environment Variables:
    SELFHEALING_RUNTIME_MAX_CONSECUTIVE_FAILURES=3
    SELFHEALING_RUNTIME_ROLLBACK_COOLDOWN=120
    SELFHEALING_RUNTIME_ADJUSTMENT_WAIT=30
"""

import structlog

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class RuntimeFeedbackSettings(BaseSettings):
    """
    RuntimeFeedbackLoop 설정.

    자율 조정 실패 시 자동 롤백 및 피드백 루프 일시 정지를 위한 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RUNTIME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 연속 실패 관련 설정
    # ==========================================================================
    max_consecutive_failures: int = Field(
        default=3,
        ge=1,
        le=20,
        description="최대 연속 실패 횟수. 초과 시 피드백 루프 자동 일시 정지.",
    )

    # ==========================================================================
    # 롤백 관련 설정
    # ==========================================================================
    rollback_cooldown: int = Field(
        default=120,
        ge=10,
        le=3600,
        description="롤백 후 안정화 대기 시간 (초). 이 시간 동안 추가 조정 차단.",
    )

    # ==========================================================================
    # 조정 후 대기 설정
    # ==========================================================================
    adjustment_wait: int = Field(
        default=30,
        ge=5,
        le=600,
        description="조정 적용 후 효과 확인 대기 시간 (초). 메트릭 수집용.",
    )


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: RuntimeFeedbackSettings | None = None


def get_runtime_feedback_settings() -> RuntimeFeedbackSettings:
    """
    캐시된 RuntimeFeedbackSettings 인스턴스 반환.

    Returns:
        RuntimeFeedbackSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = RuntimeFeedbackSettings()
        logger.debug(
            "[RuntimeFeedbackSettings] Loaded: "
            f"max_consecutive_failures={_settings.max_consecutive_failures}, "
            f"rollback_cooldown={_settings.rollback_cooldown}s, "
            f"adjustment_wait={_settings.adjustment_wait}s"
        )
    return _settings


def reset_runtime_feedback_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("runtime_feedback_settings.reset")
