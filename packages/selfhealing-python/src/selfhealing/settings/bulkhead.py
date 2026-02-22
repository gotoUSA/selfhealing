"""
Bulkhead Settings - Pydantic v2.

도메인별 리소스 격리 설정입니다.
ConnectionType에 따른 기본 설정과 커스텀 도메인 설정을 지원합니다.

Environment Variables:
    SELFHEALING_BULKHEAD_ENABLED=true
    SELFHEALING_BULKHEAD_DATABASE_MAX_CONCURRENT=10
    SELFHEALING_BULKHEAD_CACHE_MAX_CONCURRENT=20
    SELFHEALING_BULKHEAD_EXTERNAL_API_MAX_WORKERS=5
    SELFHEALING_BULKHEAD_EXTERNAL_API_QUEUE_SIZE=10
    SELFHEALING_BULKHEAD_MESSAGE_QUEUE_MAX_CONCURRENT=15
    SELFHEALING_BULKHEAD_DEFAULT_MAX_CONCURRENT=10
    SELFHEALING_BULKHEAD_DEFAULT_ACQUIRE_TIMEOUT=5.0
"""

from __future__ import annotations

import structlog
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class BulkheadSettings(BaseSettings):
    """격벽 패턴 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BULKHEAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Global Settings
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="격벽 패턴 활성화 여부",
    )

    # ==========================================================================
    # ConnectionType별 설정
    # ==========================================================================
    database_max_concurrent: int = Field(
        default=10,
        ge=1,
        le=100,
        description="DATABASE 타입 최대 동시 실행 수",
    )

    cache_max_concurrent: int = Field(
        default=20,
        ge=1,
        le=200,
        description="CACHE 타입 최대 동시 실행 수",
    )

    external_api_max_workers: int = Field(
        default=5,
        ge=1,
        le=50,
        description="EXTERNAL_API 타입 스레드 풀 워커 수",
    )

    external_api_queue_size: int = Field(
        default=10,
        ge=0,
        le=100,
        description="EXTERNAL_API 타입 대기 큐 크기",
    )

    message_queue_max_concurrent: int = Field(
        default=15,
        ge=1,
        le=100,
        description="MESSAGE_QUEUE 타입 최대 동시 실행 수",
    )

    # ==========================================================================
    # 커스텀 도메인용 기본값
    # ==========================================================================
    default_max_concurrent: int = Field(
        default=10,
        ge=1,
        le=100,
        description="커스텀 도메인 기본 최대 동시 실행 수",
    )

    # ==========================================================================
    # Timeout 설정
    # ==========================================================================
    default_acquire_timeout: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="기본 리소스 획득 타임아웃 (초)",
    )

    # ==========================================================================
    # 멀티 인스턴스 지원 (DB alias, 캐시 인스턴스별)
    # ==========================================================================
    database_aliases: dict[str, int] = Field(
        default_factory=lambda: {
            "default": 10,
            "replica": 15,  # replica는 읽기 전용이므로 더 많이 허용
        },
        description="DB alias별 max_concurrent 설정",
    )

    cache_instances: dict[str, int] = Field(
        default_factory=lambda: {
            "default": 20,
            "session": 10,
        },
        description="캐시 인스턴스별 max_concurrent 설정",
    )

    # ==========================================================================
    # ML/LLM 추론 전용 격벽 설정
    # ==========================================================================
    ml_inference_max_workers: int = Field(
        default=3,
        ge=1,
        le=20,
        description="ML/LLM 추론 전용 스레드 풀 워커 수",
    )

    ml_inference_queue_size: int = Field(
        default=5,
        ge=0,
        le=50,
        description="ML/LLM 추론 전용 대기 큐 크기",
    )

    ml_inference_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        description="ML/LLM 추론 타임아웃 (초)",
    )


# =============================================================================
# Singleton
# =============================================================================

_settings: BulkheadSettings | None = None
_settings_lock = threading.Lock()


def get_bulkhead_settings() -> BulkheadSettings:
    """BulkheadSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = BulkheadSettings()
    return _settings


def reset_bulkhead_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
