"""
Batch Settings - Pydantic v2.

배치 처리 크기 및 플러시 간격 설정입니다.

Replaces:
- services/async_logger.py:BATCH_SIZE, FLUSH_INTERVAL
- services/dlq_models.py:batch_size
- services/dlq/replay_operations.py:batch_size
- coordination/redis_key_guard.py:batch_size

Environment Variables:
    SELFHEALING_BATCH_DEFAULT_BATCH_SIZE=100
    SELFHEALING_BATCH_LOGGER_BATCH_SIZE=10
    SELFHEALING_BATCH_FLUSH_INTERVAL=5.0

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [19])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §3.5
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class BatchSettings(BaseSettings):
    """
    배치 처리 설정.

    용도별 배치 크기:
    - default_batch_size: 일반 배치 작업 (100)
    - logger_batch_size: 비동기 로깅 배치 (10)
    - dlq_batch_size: DLQ 리플레이 배치 (50)
    - redis_scan_batch_size: Redis 스캔 배치 (100)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Default Batch Size - multiple files
    # ==========================================================================
    default_batch_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="일반 배치 작업 기본 크기",
    )

    # ==========================================================================
    # Logger Batch - from async_logger.py
    # ==========================================================================
    logger_batch_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="비동기 로거 배치 크기",
    )

    # ==========================================================================
    # Flush Interval - from async_logger.py
    # ==========================================================================
    flush_interval: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="배치 플러시 간격 (초)",
    )

    # ==========================================================================
    # DLQ Batch - from dlq/replay_operations.py
    # ==========================================================================
    dlq_batch_size: int = Field(
        default=50,
        ge=10,
        le=500,
        description="DLQ 리플레이 배치 크기",
    )

    # ==========================================================================
    # Redis Scan Batch - from redis_key_guard.py
    # ==========================================================================
    redis_scan_batch_size: int = Field(
        default=100,
        ge=50,
        le=1000,
        description="Redis SCAN 명령 배치 크기",
    )

    # ==========================================================================
    # Audit Batch - from audit/config.py
    # ==========================================================================
    audit_batch_size: int = Field(
        default=100,
        ge=10,
        le=500,
        description="감사 로그 배치 크기",
    )

    audit_flush_interval: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="감사 로그 플러시 간격 (초)",
    )

    # ==========================================================================
    # Async Logger Config - from audit/audit_integration.py AsyncLoggerConfig
    # ==========================================================================
    async_logger_batch_size: int = Field(
        default=5,
        ge=1,
        le=100,
        description="AsyncLogger 배치 크기",
    )

    async_logger_flush_interval: float = Field(
        default=2.0,
        ge=0.5,
        le=30.0,
        description="AsyncLogger 플러시 간격 (초)",
    )

    async_logger_max_queue_size: int = Field(
        default=5000,
        ge=100,
        le=100000,
        description="AsyncLogger 최대 큐 크기",
    )


# ==========================================================================
# Singleton 관리
# ==========================================================================
_batch_settings: BatchSettings | None = None


def get_batch_settings() -> BatchSettings:
    """Get cached BatchSettings instance."""
    global _batch_settings
    if _batch_settings is None:
        _batch_settings = BatchSettings()
    return _batch_settings


def reset_batch_settings() -> None:
    """Reset cached settings (for testing)."""
    global _batch_settings
    _batch_settings = None
