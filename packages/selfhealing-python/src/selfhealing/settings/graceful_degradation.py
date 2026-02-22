"""
Graceful Degradation Settings - Pydantic v2.

Hash chain 저하 수준 및 Fallback 체인 설정.
Redis 장애 시 단계적 저하 및 복구 설정을 정의합니다.

Source:
- audit/graceful_degradation/enums.py (FallbackConfig, CircuitBreakerConfig)

Environment Variables:
    SELFHEALING_GRACEFUL_DEGRADATION_REDIS_TIMEOUT_SECONDS=5.0
    SELFHEALING_GRACEFUL_DEGRADATION_REPLICA_TIMEOUT_SECONDS=3.0
    SELFHEALING_GRACEFUL_DEGRADATION_MEMORY_MAX_ENTRIES=10000
    SELFHEALING_GRACEFUL_DEGRADATION_CB_FAILURE_THRESHOLD=5
    SELFHEALING_GRACEFUL_DEGRADATION_CB_RECOVERY_TIMEOUT_SECONDS=30.0
    SELFHEALING_GRACEFUL_DEGRADATION_CB_HALF_OPEN_REQUESTS=3
    SELFHEALING_GRACEFUL_DEGRADATION_CB_SUCCESS_THRESHOLD=2
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class GracefulDegradationSettings(BaseSettings):
    """
    Graceful Degradation 설정.

    Redis 장애 시 Fallback 체인 및 Circuit Breaker 설정을 정의합니다.
    저하 수준:
    - NORMAL: Redis 사용
    - DEGRADED: 로컬 Fallback 사용
    - EMERGENCY: 메모리 전용
    - READONLY: 읽기 전용
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_GRACEFUL_DEGRADATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Fallback Config (from enums.py FallbackConfig lines 45-48)
    # ==========================================================================
    redis_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Redis 연결 타임아웃 (초)",
    )
    replica_timeout_seconds: float = Field(
        default=3.0,
        ge=0.5,
        le=30.0,
        description="Replica 연결 타임아웃 (초)",
    )
    memory_max_entries: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="메모리 Fallback 최대 항목 수",
    )
    key_prefix: str = Field(
        default="selfhealing:",
        min_length=1,
        max_length=50,
        description="Redis 키 접두어",
    )

    # ==========================================================================
    # Circuit Breaker Config (from enums.py CircuitBreakerConfig lines 55-58)
    # ==========================================================================
    cb_failure_threshold: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Circuit Breaker OPEN 임계값",
    )
    cb_recovery_timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Circuit Breaker 복구 대기 시간 (초)",
    )
    cb_half_open_requests: int = Field(
        default=3,
        ge=1,
        le=20,
        description="HALF_OPEN 상태에서 허용하는 요청 수",
    )
    cb_success_threshold: int = Field(
        default=2,
        ge=1,
        le=20,
        description="HALF_OPEN에서 CLOSED로 전환하는 성공 횟수",
    )

    @field_validator("redis_timeout_seconds")
    @classmethod
    def validate_redis_timeout(cls, v: float) -> float:
        """redis_timeout이 너무 길면 경고."""
        if v > 10.0:
            logger.warning(
                "graceful_degradation_settings.high_consider_using_responsiveness",
                v=v,
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: GracefulDegradationSettings | None = None


def get_graceful_degradation_settings() -> GracefulDegradationSettings:
    """
    캐시된 GracefulDegradationSettings 인스턴스 반환.

    Returns:
        GracefulDegradationSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = GracefulDegradationSettings()
    return _settings


def reset_graceful_degradation_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
