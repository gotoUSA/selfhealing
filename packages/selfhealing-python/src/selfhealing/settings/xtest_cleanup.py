"""
X-Test Artifact Cleanup Settings

X-Test 세션 종료 후 테스트 아티팩트(CB 상태, DLQ 항목, Idempotency 키 등) 자동 정리 설정.

Environment Variables:
    SELFHEALING_XTEST_CLEANUP_SESSION_TTL_HOURS=4
    SELFHEALING_XTEST_CLEANUP_INTERVAL_MINUTES=30
    SELFHEALING_XTEST_CLEANUP_CB_AUTO_RESTORE=true
    SELFHEALING_XTEST_CLEANUP_DLQ_AUTO_PURGE=true
    SELFHEALING_XTEST_CLEANUP_IDEMPOTENCY_AUTO_CLEAR=true
    SELFHEALING_XTEST_CLEANUP_RATE_LIMIT_AUTO_RESET=true
    SELFHEALING_XTEST_CLEANUP_MAX_RETRIES=2
    SELFHEALING_XTEST_CLEANUP_RETRY_DELAY=60
"""

from __future__ import annotations

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class XTestCleanupSettings(BaseSettings):
    """
    X-Test 아티팩트 자동 정리 설정.

    X-Test 세션 만료 시간, 정리 주기, 컴포넌트별 자동 정리 활성화 여부를 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_XTEST_CLEANUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 세션 TTL 설정
    # ==========================================================================
    session_ttl_hours: int = Field(
        default=4,
        ge=1,
        le=24,
        description="X-Test 세션 만료 시간 (시간 단위, 긴 시나리오 테스트 고려)",
    )

    # ==========================================================================
    # 정리 주기 설정
    # ==========================================================================
    cleanup_interval_minutes: int = Field(
        default=30,
        ge=5,
        le=120,
        description="자동 정리 태스크 실행 주기 (분)",
    )

    # ==========================================================================
    # 컴포넌트별 자동 정리 활성화
    # ==========================================================================
    cb_auto_restore: bool = Field(
        default=True,
        description="Circuit Breaker 상태 자동 원복 활성화",
    )

    dlq_auto_purge: bool = Field(
        default=True,
        description="DLQ X-Test 항목 자동 삭제 활성화",
    )

    idempotency_auto_clear: bool = Field(
        default=True,
        description="Idempotency 키 자동 삭제 활성화",
    )

    rate_limit_auto_reset: bool = Field(
        default=True,
        description="Rate Limit 카운터 자동 초기화 활성화",
    )

    # ==========================================================================
    # Celery Task 재시도 설정
    # ==========================================================================
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="정리 태스크 최대 재시도 횟수",
    )

    retry_delay: int = Field(
        default=60,
        ge=10,
        le=600,
        description="정리 태스크 재시도 지연 (초)",
    )

    # ==========================================================================
    # Redis 키 접두사
    # ==========================================================================
    redis_session_prefix: str = Field(
        default="xtest:session:",
        description="X-Test 세션 메타데이터 Redis 키 접두사",
    )

    redis_active_sessions_key: str = Field(
        default="xtest:session:active",
        description="활성 X-Test 세션 ID 목록 Redis 키",
    )

    @field_validator("session_ttl_hours")
    @classmethod
    def validate_session_ttl(cls, v: int) -> int:
        """세션 TTL 검증."""
        if v < 1:
            logger.warning(
                "x_test_cleanup.too_low_using",
                v=v,
            )
            return 1
        return v

    @field_validator("cleanup_interval_minutes")
    @classmethod
    def validate_cleanup_interval(cls, v: int) -> int:
        """정리 주기 검증."""
        if v < 5:
            logger.warning(
                "x_test_cleanup.too_low_using",
                v=v,
            )
            return 5
        return v


# =============================================================================
# Settings Instance Factory
# =============================================================================

_xtest_cleanup_settings: XTestCleanupSettings | None = None


def get_xtest_cleanup_settings() -> XTestCleanupSettings:
    """
    X-Test Cleanup 설정 싱글톤 인스턴스 반환.

    Returns:
        XTestCleanupSettings 인스턴스
    """
    global _xtest_cleanup_settings
    if _xtest_cleanup_settings is None:
        _xtest_cleanup_settings = XTestCleanupSettings()
        logger.debug(
            "x_test_cleanup.settings_loaded",
            _xtest_cleanup_settings=_xtest_cleanup_settings.session_ttl_hours,
            cleanup_interval_minutes=_xtest_cleanup_settings.cleanup_interval_minutes,
        )
    return _xtest_cleanup_settings


def reset_xtest_cleanup_settings() -> None:
    """설정 캐시 초기화 (테스트용)."""
    global _xtest_cleanup_settings
    _xtest_cleanup_settings = None


__all__ = [
    "XTestCleanupSettings",
    "get_xtest_cleanup_settings",
    "reset_xtest_cleanup_settings",
]
