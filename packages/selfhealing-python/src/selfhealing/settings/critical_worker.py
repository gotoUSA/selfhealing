"""
Critical Worker Settings - Pydantic v2.

P0 우선순위 태스크 전용 Worker 설정입니다.

Replaces:
- services/coordination/critical_worker.py:CriticalPathDedicatedWorkerConfig 기본값들

Environment Variables:
    SELFHEALING_CRITICALWORKER_CRITICAL_QUEUE_NAME=selfhealing.critical
    SELFHEALING_CRITICALWORKER_CRITICAL_WORKER_COUNT=2

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 2 [11])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §14.1
- docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.2
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CriticalWorkerSettings(BaseSettings):
    """
    Critical Path 전용 Worker 설정.

    일반 Worker 그룹 외에, 오직 P0 전용 태스크만 처리하는
    Small-size 전용 Worker를 별도로 운영합니다.

    Reference:
        k8s/celery-critical-worker.yaml
        77_RECOVERY_COORDINATOR.md#11.2
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CRITICALWORKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Queue Names (from critical_worker.py)
    # ==========================================================================
    critical_queue_name: str = Field(
        default="selfhealing.critical",
        description="P0 전용 큐 (Abort, Kill Switch 전용)",
    )

    high_priority_queue_name: str = Field(
        default="selfhealing.high",
        description="P1-P2 고우선순위 큐 (Escalation, Recovery)",
    )

    default_queue_name: str = Field(
        default="selfhealing.default",
        description="P3+ 일반 큐 (Alert, Audit, Archive)",
    )

    recovery_queue_name: str = Field(
        default="selfhealing.recovery",
        description="복구 전용 큐",
    )

    notification_queue_name: str = Field(
        default="selfhealing.notifications",
        description="알림 전용 큐",
    )

    maintenance_queue_name: str = Field(
        default="selfhealing.maintenance",
        description="유지보수 태스크 큐",
    )

    # ==========================================================================
    # Worker Counts
    # ==========================================================================
    critical_worker_count: int = Field(
        default=2,
        ge=1,
        le=10,
        description="전용 Worker 수 (최소 1개 보장)",
    )

    high_priority_worker_count: int = Field(
        default=4,
        ge=1,
        le=20,
        description="고우선순위 Worker 수",
    )

    default_worker_count: int = Field(
        default=8,
        ge=1,
        le=50,
        description="일반 Worker 수",
    )

    # ==========================================================================
    # Concurrency Settings (per queue)
    # ==========================================================================
    critical_concurrency: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Critical 큐 동시성 (Worker당 처리량)",
    )

    high_priority_concurrency: int = Field(
        default=4,
        ge=1,
        le=20,
        description="High Priority 큐 동시성",
    )

    default_concurrency: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Default 큐 동시성",
    )

    # ==========================================================================
    # Prefetch Settings
    # ==========================================================================
    critical_prefetch_multiplier: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Critical 큐 프리페치 배수",
    )

    high_priority_prefetch_multiplier: int = Field(
        default=2,
        ge=1,
        le=8,
        description="High Priority 큐 프리페치 배수",
    )

    default_prefetch_multiplier: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Default 큐 프리페치 배수",
    )

    # ==========================================================================
    # Task Timeout (93문서 필드)
    # ==========================================================================
    task_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="태스크 타임아웃 (초)",
    )

    @field_validator("critical_worker_count")
    @classmethod
    def validate_critical_worker_count(cls, v: int) -> int:
        """최소 1개 Worker 보장."""
        if v < 1:
            logger.warning(
                f"[SafeDefault] critical_worker_count={v} is invalid, using 1"
            )
            return 1
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[CriticalWorkerSettings] = None


def get_critical_worker_settings() -> CriticalWorkerSettings:
    """Get cached CriticalWorkerSettings instance."""
    global _settings
    if _settings is None:
        _settings = CriticalWorkerSettings()
    return _settings


def reset_critical_worker_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
