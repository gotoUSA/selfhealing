"""
Critical Worker Settings - Pydantic v2.

P0 우선순위 태스크 전용 Worker 설정입니다.

Replaces:
- services/coordination/critical_worker.py:CriticalPathDedicatedWorkerConfig 기본값들

Environment Variables:
    SELFHEALING_CRITICALWORKER_CRITICAL_QUEUE_NAME=selfhealing.critical
    SELFHEALING_CRITICALWORKER_CRITICAL_WORKER_COUNT=2
    SELFHEALING_CRITICALWORKER_DEPLOYMENT_ENV=STANDARD
    SELFHEALING_CRITICALWORKER_POOL_MINIMAL_WORKER_COUNT=2
    SELFHEALING_CRITICALWORKER_POOL_STANDARD_WORKER_COUNT=4
    SELFHEALING_CRITICALWORKER_POOL_HIGH_AVAILABILITY_WORKER_COUNT=4
    SELFHEALING_CRITICALWORKER_POOL_BURST_WORKER_COUNT=2
    SELFHEALING_CRITICALWORKER_POOL_ENTERPRISE_WORKER_COUNT=8
"""

import logging
from enum import Enum
from typing import Dict, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DeploymentEnvironment(str, Enum):
    """
    배포 환경 유형.
    
    Worker Pool 설정을 환경에 따라 자동 조정합니다.
    """
    MINIMAL = "MINIMAL"
    """최소 리소스 환경 (개발, 테스트)"""
    
    STANDARD = "STANDARD"
    """표준 운영 환경"""
    
    HIGH_AVAILABILITY = "HIGH_AVAILABILITY"
    """고가용성 환경 (중요 서비스)"""
    
    BURST = "BURST"
    """버스트 대응 환경 (트래픽 급증 대비)"""
    
    ENTERPRISE = "ENTERPRISE"
    """엔터프라이즈 환경 (대규모 트래픽)"""


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

    # ==========================================================================
    # Deployment Environment (환경별 자동 설정)
    # ==========================================================================
    deployment_env: DeploymentEnvironment = Field(
        default=DeploymentEnvironment.STANDARD,
        description="배포 환경 (MINIMAL, STANDARD, HIGH_AVAILABILITY, BURST, ENTERPRISE)",
    )

    # ==========================================================================
    # Environment-specific Worker Pool Settings (MINIMAL)
    # ==========================================================================
    pool_minimal_worker_count: int = Field(
        default=2,
        ge=1,
        le=4,
        description="MINIMAL 환경 Worker 수",
    )
    pool_minimal_concurrency: int = Field(
        default=2,
        ge=1,
        le=4,
        description="MINIMAL 환경 동시성",
    )
    pool_minimal_prefetch_multiplier: int = Field(
        default=1,
        ge=1,
        le=2,
        description="MINIMAL 환경 프리페치 배수",
    )

    # ==========================================================================
    # Environment-specific Worker Pool Settings (STANDARD)
    # ==========================================================================
    pool_standard_worker_count: int = Field(
        default=4,
        ge=2,
        le=8,
        description="STANDARD 환경 Worker 수",
    )
    pool_standard_concurrency: int = Field(
        default=4,
        ge=2,
        le=8,
        description="STANDARD 환경 동시성",
    )
    pool_standard_prefetch_multiplier: int = Field(
        default=2,
        ge=1,
        le=4,
        description="STANDARD 환경 프리페치 배수",
    )

    # ==========================================================================
    # Environment-specific Worker Pool Settings (HIGH_AVAILABILITY)
    # ==========================================================================
    pool_high_availability_worker_count: int = Field(
        default=4,
        ge=2,
        le=12,
        description="HIGH_AVAILABILITY 환경 Worker 수",
    )
    pool_high_availability_concurrency: int = Field(
        default=4,
        ge=2,
        le=12,
        description="HIGH_AVAILABILITY 환경 동시성",
    )
    pool_high_availability_prefetch_multiplier: int = Field(
        default=2,
        ge=1,
        le=4,
        description="HIGH_AVAILABILITY 환경 프리페치 배수",
    )

    # ==========================================================================
    # Environment-specific Worker Pool Settings (BURST)
    # ==========================================================================
    pool_burst_worker_count: int = Field(
        default=2,
        ge=1,
        le=8,
        description="BURST 환경 Worker 수 (적은 워커, 높은 동시성)",
    )
    pool_burst_concurrency: int = Field(
        default=4,
        ge=2,
        le=16,
        description="BURST 환경 동시성",
    )
    pool_burst_prefetch_multiplier: int = Field(
        default=4,
        ge=1,
        le=8,
        description="BURST 환경 프리페치 배수",
    )

    # ==========================================================================
    # Environment-specific Worker Pool Settings (ENTERPRISE)
    # ==========================================================================
    pool_enterprise_worker_count: int = Field(
        default=8,
        ge=4,
        le=32,
        description="ENTERPRISE 환경 Worker 수",
    )
    pool_enterprise_concurrency: int = Field(
        default=8,
        ge=4,
        le=32,
        description="ENTERPRISE 환경 동시성",
    )
    pool_enterprise_prefetch_multiplier: int = Field(
        default=4,
        ge=1,
        le=8,
        description="ENTERPRISE 환경 프리페치 배수",
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

    def get_pool_config_for_env(
        self, env: Optional[DeploymentEnvironment] = None
    ) -> Dict[str, int]:
        """
        환경에 따른 Worker Pool 설정 반환.
        
        Args:
            env: 배포 환경 (기본값: self.deployment_env)
        
        Returns:
            {"worker_count": N, "concurrency": N, "prefetch_multiplier": N}
        """
        env = env or self.deployment_env
        
        pool_configs = {
            DeploymentEnvironment.MINIMAL: {
                "worker_count": self.pool_minimal_worker_count,
                "concurrency": self.pool_minimal_concurrency,
                "prefetch_multiplier": self.pool_minimal_prefetch_multiplier,
            },
            DeploymentEnvironment.STANDARD: {
                "worker_count": self.pool_standard_worker_count,
                "concurrency": self.pool_standard_concurrency,
                "prefetch_multiplier": self.pool_standard_prefetch_multiplier,
            },
            DeploymentEnvironment.HIGH_AVAILABILITY: {
                "worker_count": self.pool_high_availability_worker_count,
                "concurrency": self.pool_high_availability_concurrency,
                "prefetch_multiplier": self.pool_high_availability_prefetch_multiplier,
            },
            DeploymentEnvironment.BURST: {
                "worker_count": self.pool_burst_worker_count,
                "concurrency": self.pool_burst_concurrency,
                "prefetch_multiplier": self.pool_burst_prefetch_multiplier,
            },
            DeploymentEnvironment.ENTERPRISE: {
                "worker_count": self.pool_enterprise_worker_count,
                "concurrency": self.pool_enterprise_concurrency,
                "prefetch_multiplier": self.pool_enterprise_prefetch_multiplier,
            },
        }
        
        return pool_configs.get(env, pool_configs[DeploymentEnvironment.STANDARD])


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
