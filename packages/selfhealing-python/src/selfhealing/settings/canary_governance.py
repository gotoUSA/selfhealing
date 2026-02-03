"""
Canary Governance Settings.

Canary 롤아웃의 거버넌스 관련 설정 중앙화.

Environment Variables:
    SELFHEALING_CANARY_GOV_ZOMBIE_EXEMPT_TRIGGERS='["error_budget","governance"]'
    SELFHEALING_CANARY_GOV_RESUME_WHITELIST_TRIGGERS='["error_budget"]'
    SELFHEALING_CANARY_GOV_GOVERNANCE_CHECK_ON_MANUAL_PROMOTE=true
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CanaryGovernanceSettings(BaseSettings):
    """
    Canary Governance 설정.

    거버넌스 체크, Zombie 판정, Resume 필터링 관련 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CANARY_GOV_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Zombie 판정 제외 설정
    # ==========================================================================
    zombie_exempt_triggers: list[str] = Field(
        default=["error_budget", "governance"],
        description="Zombie 판정에서 제외할 pause_triggered_by 값 목록",
    )

    # ==========================================================================
    # Resume Whitelist 설정
    # ==========================================================================
    resume_whitelist_triggers: list[str] = Field(
        default=["error_budget"],
        description="자동 재개 허용 pause_triggered_by 목록",
    )

    # ==========================================================================
    # 수동 프로모션 거버넌스 체크 설정
    # ==========================================================================
    governance_check_on_manual_promote: bool = Field(
        default=True,
        description="수동 프로모션 시에도 거버넌스 체크 적용 여부",
    )

    # ==========================================================================
    # Pause 트리거 우선순위
    # ==========================================================================
    pause_trigger_priority: dict[str, int] = Field(
        default={
            "metrics": 100,  # 직접적 장애 (에러율/레이턴시)
            "interlock": 90,  # Safety Interlock
            "error_budget": 80,  # 거버넌스 (에러 예산)
            "governance": 75,  # 거버넌스 체크 실패 (Kill Switch, Emergency 등)
            "chaos_guard": 70,  # Chaos 실험 충돌
            "manual": 10,  # 수동 중지
        },
        description="pause_triggered_by 값별 우선순위 (높을수록 우선)",
    )

    # ==========================================================================
    # Resume 쓰로틀링 설정
    # ==========================================================================
    resume_max_batch_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description="한 번에 재개할 최대 롤아웃 수",
    )
    resume_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="배치 간 대기 시간 (초)",
    )
    resume_staggered_enabled: bool = Field(
        default=True,
        description="순차 재개 활성화",
    )


# Singleton
_settings: CanaryGovernanceSettings | None = None


def get_canary_governance_settings() -> CanaryGovernanceSettings:
    """CanaryGovernanceSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = CanaryGovernanceSettings()
    return _settings


def reset_canary_governance_settings() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _settings
    _settings = None
