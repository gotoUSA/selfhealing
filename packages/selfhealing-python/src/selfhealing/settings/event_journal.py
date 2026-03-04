"""
Event Journal Settings - Pydantic v2.

Single Source of Truth for Event Journal configuration.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventJournalSettings(BaseSettings):
    """
    Event Journal 설정.

    Environment variables:
        SELFHEALING_JOURNAL_ENABLED=true
        SELFHEALING_JOURNAL_TTL_DAYS=30
        SELFHEALING_JOURNAL_MAX_ENTRIES_MEMORY=10000
        ...
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_JOURNAL_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="EventJournal 활성화 여부",
    )
    ttl_days: int = Field(
        default=30,
        ge=7,
        le=365,
        description="Redis 저장소 TTL (일)",
    )
    max_entries_memory: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="InMemory 어댑터 최대 엔트리 수",
    )
    max_query_limit: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="query() 최대 반환 건수 상한",
    )
    backend: str = Field(
        default="memory",
        description="저장소 백엔드 (memory, redis)",
    )


# =============================================================================
# Singleton pattern
# =============================================================================
_settings: EventJournalSettings | None = None


def get_event_journal_settings() -> EventJournalSettings:
    """Get cached EventJournalSettings instance."""
    global _settings
    if _settings is None:
        _settings = EventJournalSettings()
    return _settings


def reset_event_journal_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
