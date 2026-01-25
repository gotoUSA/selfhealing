"""
Recovery Tasks Settings - Pydantic v2.

Celery 복구 태스크별 재시도 전략 설정입니다.

각 복구 태스크(check_recovery_trigger, execute_recovery_step, 
monitor_active_recovery, cleanup_stale_sessions, run_health_checks)의
max_retries, default_retry_delay를 개별적으로 설정할 수 있습니다.

Environment Variables:
    SELFHEALING_RECOVERY_TASKS_CHECK_TRIGGER_MAX_RETRIES=3
    SELFHEALING_RECOVERY_TASKS_CHECK_TRIGGER_RETRY_DELAY=60
    SELFHEALING_RECOVERY_TASKS_EXECUTE_STEP_MAX_RETRIES=3
    SELFHEALING_RECOVERY_TASKS_EXECUTE_STEP_RETRY_DELAY=30
    SELFHEALING_RECOVERY_TASKS_MONITOR_RECOVERY_MAX_RETRIES=3
    SELFHEALING_RECOVERY_TASKS_MONITOR_RECOVERY_RETRY_DELAY=30
    SELFHEALING_RECOVERY_TASKS_CLEANUP_STALE_MAX_RETRIES=2
    SELFHEALING_RECOVERY_TASKS_CLEANUP_STALE_RETRY_DELAY=15
    SELFHEALING_RECOVERY_TASKS_HEALTH_CHECK_MAX_RETRIES=1
    SELFHEALING_RECOVERY_TASKS_HEALTH_CHECK_RETRY_DELAY=60
"""

import logging
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RecoveryTasksSettings(BaseSettings):
    """
    복구 태스크별 Celery 재시도 설정.

    각 태스크마다 독립적인 max_retries, default_retry_delay 설정을 지원합니다.
    Celery 데코레이터에서 동적으로 적용하거나, 런타임 self.retry() 호출 시 사용합니다.

    태스크 목록:
    - check_recovery_trigger: 복구 트리거 조건 확인
    - execute_recovery_step: 복구 단계 실행
    - monitor_active_recovery: 활성 복구 세션 모니터링
    - cleanup_stale_sessions: 방치된 복구 세션 정리
    - run_health_checks: 헬스 체크 실행
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RECOVERY_TASKS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # check_recovery_trigger 태스크 설정
    # ==========================================================================
    check_trigger_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="check_recovery_trigger 최대 재시도 횟수",
    )
    check_trigger_retry_delay: int = Field(
        default=60,
        ge=5,
        le=600,
        description="check_recovery_trigger 재시도 지연 (초)",
    )

    # ==========================================================================
    # execute_recovery_step 태스크 설정
    # ==========================================================================
    execute_step_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="execute_recovery_step 최대 재시도 횟수",
    )
    execute_step_retry_delay: int = Field(
        default=30,
        ge=5,
        le=600,
        description="execute_recovery_step 재시도 지연 (초)",
    )

    # ==========================================================================
    # monitor_active_recovery 태스크 설정
    # ==========================================================================
    monitor_recovery_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="monitor_active_recovery 최대 재시도 횟수",
    )
    monitor_recovery_retry_delay: int = Field(
        default=30,
        ge=5,
        le=600,
        description="monitor_active_recovery 재시도 지연 (초)",
    )

    # ==========================================================================
    # cleanup_stale_sessions 태스크 설정
    # ==========================================================================
    cleanup_stale_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="cleanup_stale_sessions 최대 재시도 횟수",
    )
    cleanup_stale_retry_delay: int = Field(
        default=15,
        ge=5,
        le=300,
        description="cleanup_stale_sessions 재시도 지연 (초)",
    )

    # ==========================================================================
    # run_health_checks 태스크 설정
    # ==========================================================================
    health_check_max_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description="run_health_checks 최대 재시도 횟수 (빠른 피드백 필요)",
    )
    health_check_retry_delay: int = Field(
        default=60,
        ge=10,
        le=300,
        description="run_health_checks 재시도 지연 (초)",
    )

    # ==========================================================================
    # 태스크 실행 간격 설정 (CeleryTaskSettings와 중복이나 복구 전용으로 분리)
    # ==========================================================================
    trigger_check_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="트리거 체크 주기 (초)",
    )
    health_monitor_interval: int = Field(
        default=30,
        ge=10,
        le=120,
        description="헬스 모니터 주기 (초)",
    )
    stale_check_interval: int = Field(
        default=10,
        ge=1,
        le=60,
        description="스테일 세션 체크 주기 (분)",
    )

    @field_validator("check_trigger_max_retries", "execute_step_max_retries")
    @classmethod
    def validate_critical_task_retries(cls, v: int) -> int:
        """중요 태스크 재시도 최소 1회 보장."""
        if v < 1:
            logger.warning(
                f"[RecoveryTasksSettings] Critical task max_retries={v} is low, "
                "consider using >= 1 for resilience"
            )
        return v

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "RecoveryTasksSettings":
        """재시도 지연이 너무 짧으면 경고."""
        delays = [
            ("check_trigger", self.check_trigger_retry_delay),
            ("execute_step", self.execute_step_retry_delay),
            ("monitor_recovery", self.monitor_recovery_retry_delay),
        ]
        for name, delay in delays:
            if delay < 10:
                logger.warning(
                    f"[RecoveryTasksSettings] {name}_retry_delay={delay}s is very short"
                )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[RecoveryTasksSettings] = None


def get_recovery_tasks_settings() -> RecoveryTasksSettings:
    """캐시된 RecoveryTasksSettings 인스턴스 반환."""
    global _settings
    if _settings is None:
        _settings = RecoveryTasksSettings()
    return _settings


def reset_recovery_tasks_settings() -> None:
    """캐시 초기화 (테스트용)."""
    global _settings
    _settings = None
