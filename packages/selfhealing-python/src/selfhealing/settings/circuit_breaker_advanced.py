"""
Circuit Breaker Advanced Protection Settings - Pydantic v2.

Single Source of Truth for circuit breaker advanced protection.
Replaces: core/config.py:CircuitBreakerAdvancedConfig (lines 540-605)
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CircuitBreakerAdvancedSettings(BaseSettings):
    """
    Circuit Breaker 고급 보호 설정.
    
    이 설정은 RuntimeConfigManager를 통해 중앙 관리됩니다.
    서버 재시작 없이 API로 변경 가능합니다.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CB_ADV_",
        env_file=".env",
        extra="ignore",
    )
    
    # 전체 활성화
    enabled: bool = Field(
        default=True,
        description="고급 보호 기능 활성화 여부",
    )
    
    # =========================================================================
    # Load Shedding
    # =========================================================================
    load_shedding_enabled: bool = Field(
        default=True,
        description="Load Shedding 활성화",
    )
    load_shedding_trigger_threshold: float = Field(
        default=30.0,
        ge=0.0,
        le=100.0,
        description="Load Shedding 트리거 임계값 (%)",
    )
    
    # =========================================================================
    # Adaptive Threshold (Emergency Level 연동)
    # =========================================================================
    adaptive_threshold_enabled: bool = Field(
        default=True,
        description="Adaptive Threshold 활성화",
    )
    adaptive_base_failure_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="기본 실패 횟수 임계값",
    )
    adaptive_base_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="기본 관찰 윈도우 (초)",
    )
    
    # =========================================================================
    # Canary Recovery
    # =========================================================================
    canary_recovery_enabled: bool = Field(
        default=True,
        description="Canary Recovery 활성화",
    )
    canary_default_stages: int = Field(
        default=4,
        ge=1,
        le=10,
        description="기본 Canary 단계 수 (10% → 30% → 60% → 100%)",
    )
    canary_stage_duration_seconds: int = Field(
        default=5,
        ge=1,
        le=300,
        description="각 Canary 단계 지속 시간 (초)",
    )
    canary_strict_mode_for_critical: bool = Field(
        default=True,
        description="critical 서비스는 100% 성공률 요구",
    )
    
    # =========================================================================
    # Blast Radius 연동
    # =========================================================================
    blast_radius_integration: bool = Field(
        default=True,
        description="Blast Radius 연동 활성화",
    )
    blast_radius_block_on_critical: bool = Field(
        default=True,
        description="CRITICAL 시 자동 OPEN 차단",
    )
    
    # =========================================================================
    # Freeze Mode
    # =========================================================================
    freeze_on_lockdown: bool = Field(
        default=True,
        description="LOCKDOWN 시 Freeze Mode 활성화",
    )
    allow_manual_override_in_lockdown: bool = Field(
        default=True,
        description="LOCKDOWN 중 수동 조작 허용",
    )
    
    # =========================================================================
    # Panic Threshold
    # =========================================================================
    panic_threshold_enabled: bool = Field(
        default=True,
        description="Panic Threshold 활성화",
    )
    panic_threshold_percent: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="OPEN CB 비율 임계값 (70% 이상이면 Panic)",
    )
    panic_threshold_action: str = Field(
        default="freeze",
        description='Panic 발생 시 행동 ("freeze" | "alert_only")',
    )
    
    # =========================================================================
    # Open Strategy
    # =========================================================================
    default_open_strategy: str = Field(
        default="immediate",
        description='Open 전략 ("immediate" | "graceful")',
    )
    graceful_drain_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Graceful Open 시 drain timeout (초)",
    )


# =============================================================================
# Singleton pattern
# =============================================================================
_settings: Optional[CircuitBreakerAdvancedSettings] = None


def get_circuit_breaker_advanced_settings() -> CircuitBreakerAdvancedSettings:
    """Get cached CircuitBreakerAdvancedSettings instance."""
    global _settings
    if _settings is None:
        _settings = CircuitBreakerAdvancedSettings()
    return _settings


def reset_circuit_breaker_advanced_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
