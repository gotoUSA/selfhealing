"""
Audit Settings - Pydantic v2.

감사 로그 및 설정 이력 관련 설정입니다.

Replaces:
- services/pending_config.py:MAX_HISTORY
- services/config_history.py:MAX_HISTORY_ENTRIES
- core/safe_defaults.py:audit_log_retention_days

Environment Variables:
    SELFHEALING_AUDIT_MAX_HISTORY=100
    SELFHEALING_AUDIT_RETENTION_DAYS=90
    SELFHEALING_AUDIT_CONFIG_HISTORY_ENTRIES=50

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [20])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §3.9
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class AuditSettings(BaseSettings):
    """
    감사 로그 및 이력 관리 설정.

    보관 정책:
    - max_history: Pending Config 변경 이력 (100개)
    - config_history_entries: 설정 버전 이력 (50개)
    - retention_days: 감사 로그 보관 기간 (90일)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUDIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Pending Config History - from pending_config.py
    # ==========================================================================
    max_history: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Pending Config 변경 이력 최대 보관 수",
    )

    # ==========================================================================
    # Config History - from config_history.py
    # ==========================================================================
    config_history_entries: int = Field(
        default=50,
        ge=10,
        le=500,
        description="설정 버전 이력 최대 보관 수",
    )

    # ==========================================================================
    # Retention - from safe_defaults.py
    # ==========================================================================
    retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="감사 로그 보관 기간 (일)",
    )

    # ==========================================================================
    # Event Bus History - from event_bus.py
    # ==========================================================================
    event_history_max: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="이벤트 버스 이력 최대 보관 수",
    )

    # ==========================================================================
    # Cascade Detector History - from cascade_detector.py
    # ==========================================================================
    cascade_history_max: int = Field(
        default=100,
        ge=50,
        le=500,
        description="Cascade 감지 이력 최대 보관 수",
    )

    # ==========================================================================
    # Pool Monitor History - from pool_monitor.py
    # ==========================================================================
    pool_stats_history_max: int = Field(
        default=100,
        ge=50,
        le=500,
        description="Pool 모니터 통계 이력 최대 보관 수",
    )

    # ==========================================================================
    # Self-Audit - from self_audit.py (Phase 3 리팩토링)
    # ==========================================================================
    self_audit_max_recent_events: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Self-Audit 최근 이벤트 최대 보관 수",
    )

    self_audit_default_limit: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Self-Audit 이벤트 조회 기본 limit",
    )

    self_audit_max_failure_rate: float = Field(
        default=0.1,
        ge=0.01,
        le=1.0,
        description="Self-Audit 헬스 체크 최대 허용 실패율 (0.1 = 10%)",
    )

    # ==========================================================================
    # Cascade Load Shedding - from cascade_load_shedding.py (Phase 3 리팩토링)
    # ==========================================================================
    cascade_rate_window_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Cascade Load Shedding Rate Limit 윈도우 크기 (초)",
    )

    # ==========================================================================
    # Compliance Retention - from audit/config.py
    # ==========================================================================
    compliance_max_retention_days: int = Field(
        default=365,
        ge=90,
        le=2555,
        description="법적 요구사항에 따른 최대 보관 기간 (일). 기본 1년, 최대 7년.",
    )

    # ==========================================================================
    # Redis Buffer TTL - from adapters/audit/redis_buffer.py
    # ==========================================================================
    buffer_redis_ttl: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="Redis Audit 버퍼 TTL (초). 기본 24시간.",
    )

    # ==========================================================================
    # Throttle Audit Sampling - 감사 이벤트 샘플링 비율
    # ==========================================================================
    sampling_rate_limit_adjusted: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Limit 조정 이벤트 샘플링 비율 (0.1 = 10%). 고빈도 이벤트 제어용.",
    )

    sampling_rate_429: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="429 응답 이벤트 샘플링 비율 (0.5 = 50%). Rate limiting 시 과다 로깅 방지.",
    )


# ==========================================================================
# Singleton 관리
# ==========================================================================
_audit_settings: AuditSettings | None = None


def get_audit_settings() -> AuditSettings:
    """Get cached AuditSettings instance."""
    global _audit_settings
    if _audit_settings is None:
        _audit_settings = AuditSettings()
    return _audit_settings


def reset_audit_settings() -> None:
    """Reset cached settings (for testing)."""
    global _audit_settings
    _audit_settings = None
