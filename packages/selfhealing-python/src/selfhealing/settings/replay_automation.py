"""
Replay Automation Settings - Pydantic v2.

Single Source of Truth for replay automation configuration.
Replaces:
- core/config.py:ReplayAutomationConfig (lines 434-495)
"""

from typing import Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReplayAutomationSettings(BaseSettings):
    """
    DLQ Replay 자동화 설정.
    
    Track 1: CB 복구 시 이벤트 기반 자동 Replay
    Track 2: Scheduled Batch (기존 5분 주기)
    Track 3: Traffic-Aware Replay (향후 구현)
    Phase 4: 도메인별 차등 정책
    
    Environment variables:
        SELFHEALING_REPLAY_TRACK1_ENABLED=true
        SELFHEALING_REPLAY_TRACK1_MAX_ITEMS=50
        ...
    """
    
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_REPLAY_",
        env_file=".env",
        extra="ignore",
    )
    
    # =========================================================================
    # Track 1: Event-Driven Replay (CB CLOSED 이벤트 기반)
    # =========================================================================
    track1_enabled: bool = Field(
        default=True,
        description="Track 1 활성화 여부",
    )
    track1_max_items: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="CB 복구 시 최대 replay 건수",
    )
    
    # =========================================================================
    # Track 2: Scheduled Batch Replay (기존 5분 주기 Beat)
    # =========================================================================
    track2_enabled: bool = Field(
        default=True,
        description="Track 2 활성화 여부",
    )
    track2_max_items: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="배치당 최대 replay 건수",
    )
    
    # =========================================================================
    # Track 3: Traffic-Aware Replay (향후 구현)
    # =========================================================================
    track3_enabled: bool = Field(
        default=False,
        description="Track 3 활성화 여부 (기본: 비활성)",
    )
    track3_max_items: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="트래픽 정상화 시 최대 replay 건수",
    )
    
    # =========================================================================
    # Adaptive Mode (동적 max_items 조정)
    # =========================================================================
    adaptive_enabled: bool = Field(
        default=False,
        description="Adaptive 모드 활성화",
    )
    adaptive_min_items: int = Field(
        default=10,
        ge=1,
        le=100,
        description="최소 batch size",
    )
    adaptive_max_items: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="최대 batch size",
    )
    adaptive_failure_threshold: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="실패율 임계값 (0.2 = 20%)",
    )
    
    # =========================================================================
    # Phase 4: Domain Priority Policy (도메인별 차등 정책)
    # =========================================================================
    priority_enabled: bool = Field(
        default=False,
        description="우선순위 기반 배치 처리 활성화",
    )
    domain_priorities: Dict[str, str] = Field(
        default_factory=dict,
        description='도메인별 우선순위 {"payment": "critical", "notification": "low"}',
    )
    domain_max_retries: Dict[str, int] = Field(
        default_factory=dict,
        description='도메인별 max_retries 오버라이드 {"payment": 10}',
    )
    domain_on_circuit_close: Dict[str, bool] = Field(
        default_factory=dict,
        description='도메인별 Track 1 트리거 여부 {"payment": True}',
    )


# =============================================================================
# Singleton pattern
# =============================================================================
_settings: Optional[ReplayAutomationSettings] = None


def get_replay_automation_settings() -> ReplayAutomationSettings:
    """Get cached ReplayAutomationSettings instance."""
    global _settings
    if _settings is None:
        _settings = ReplayAutomationSettings()
    return _settings


def reset_replay_automation_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None


# Legacy alias
ReplayAutomationConfig = ReplayAutomationSettings
