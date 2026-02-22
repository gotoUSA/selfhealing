"""
Event Buffer Settings - Pydantic v2.

요청당 감사 이벤트 버퍼 크기 설정입니다.
대규모 벌크 작업 시 이벤트 손실을 방지합니다.

Environment Variables:
    SELFHEALING_EVENT_BUFFER_MAX_EVENTS_PER_REQUEST=1000
    SELFHEALING_EVENT_BUFFER_WARNING_THRESHOLD=0.8
    SELFHEALING_EVENT_BUFFER_OVERFLOW_STRATEGY=drop_oldest
"""

from typing import Literal

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class EventBufferSettings(BaseSettings):
    """
    요청당 이벤트 버퍼 설정.

    HTTP 요청당 수집되는 감사 이벤트의 버퍼 크기를 관리합니다.
    벌크 작업(대량 생성/수정/삭제) 시 이벤트 손실을 방지합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_EVENT_BUFFER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Per-Request Buffer Limit
    # ==========================================================================
    max_events_per_request: int = Field(
        default=1000,
        ge=10,
        le=100000,
        description=(
            "요청당 최대 감사 이벤트 버퍼 크기. "
            "벌크 작업 시 10,000+ 권장. "
            "RingBuffer 사용 시 이 값은 RingBuffer capacity로 대체됨."
        ),
    )

    # ==========================================================================
    # Warning Threshold
    # ==========================================================================
    warning_threshold: float = Field(
        default=0.8,
        ge=0.5,
        le=0.95,
        description="버퍼 사용률 경고 임계치 (80%)",
    )

    # ==========================================================================
    # Overflow Strategy
    # ==========================================================================
    overflow_strategy: Literal["drop_oldest", "drop_newest", "block"] = Field(
        default="drop_oldest",
        description=(
            "버퍼 오버플로우 시 전략. "
            "drop_oldest: 가장 오래된 이벤트 삭제 (권장). "
            "drop_newest: 새 이벤트 삭제. "
            "block: 버퍼 공간 확보까지 블로킹 (비권장)."
        ),
    )

    @field_validator("overflow_strategy")
    @classmethod
    def validate_overflow_strategy(cls, v: str) -> str:
        """block 전략 사용 시 경고."""
        if v == "block":
            logger.warning(
                "[EventBuffer] overflow_strategy='block'은 메인 애플리케이션 성능에 "
                "영향을 줄 수 있습니다. 'drop_oldest'를 권장합니다."
            )
        return v


# ==========================================================================
# Singleton 관리
# ==========================================================================
_event_buffer_settings: EventBufferSettings | None = None


def get_event_buffer_settings() -> EventBufferSettings:
    """Get cached EventBufferSettings instance."""
    global _event_buffer_settings
    if _event_buffer_settings is None:
        _event_buffer_settings = EventBufferSettings()
    return _event_buffer_settings


def reset_event_buffer_settings() -> None:
    """Reset cached settings (for testing)."""
    global _event_buffer_settings
    _event_buffer_settings = None
