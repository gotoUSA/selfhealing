"""
Cell Topology Settings - Pydantic v2.

Cell 토폴로지 기반 논리적 트래픽 격벽 설정입니다.
Consistent Hash Ring으로 서비스/테넌트를 Cell에 할당하고,
Cell별 Bulkhead 격벽을 관리합니다.

Environment Variables:
    SELFHEALING_CELL_TOPOLOGY_ENABLED=false
    SELFHEALING_CELL_TOPOLOGY_CELL_COUNT=8
    SELFHEALING_CELL_TOPOLOGY_CELL_PREFIX=cell
    SELFHEALING_CELL_TOPOLOGY_BULKHEAD_ISOLATION_ENABLED=false
    SELFHEALING_CELL_TOPOLOGY_BULKHEAD_MAX_CONCURRENT_PER_CELL=100
    SELFHEALING_CELL_TOPOLOGY_WARMUP_INITIAL_PERCENTAGE=10.0
    SELFHEALING_CELL_TOPOLOGY_RECONCILIATION_INTERVAL_SECONDS=15.0
    SELFHEALING_CELL_TOPOLOGY_SERVICE_HEARTBEAT_TTL_SECONDS=300.0
"""

from __future__ import annotations

import logging
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CellTopologySettings(BaseSettings):
    """Cell 토폴로지 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CELL_TOPOLOGY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 마스터 토글
    # ==========================================================================
    enabled: bool = Field(
        default=False,
        description="Cell 토폴로지 전체 마스터 스위치",
    )

    tagging_enabled: bool = Field(
        default=False,
        description="요청/태스크에 cell_id 태깅 활성화",
    )

    bulkhead_isolation_enabled: bool = Field(
        default=False,
        description="Cell 단위 Bulkhead 격벽 활성화",
    )

    evacuation_enabled: bool = Field(
        default=False,
        description="Cell 대피 기능 활성화",
    )

    # ==========================================================================
    # Cell 구성
    # ==========================================================================
    cell_count: int = Field(
        default=8,
        ge=1,
        le=256,
        description="Cell 수",
    )

    cell_prefix: str = Field(
        default="cell",
        description="Cell 이름 접두사 (예: 'cell' → 'cell-0', 'cell-1')",
    )

    # ==========================================================================
    # Bulkhead 설정
    # ==========================================================================
    bulkhead_max_concurrent_per_cell: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Cell별 Bulkhead 최대 동시 요청 수",
    )

    bulkhead_type: str = Field(
        default="semaphore",
        description="Bulkhead 유형 ('semaphore' 또는 'thread_pool')",
    )

    # ==========================================================================
    # 대피/건강 체크
    # ==========================================================================
    evacuation_health_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="대피 트리거 건강도 임계값 (이 값 미만이면 대피 시작)",
    )

    evacuation_traffic_drain_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="트래픽 드레인 대기 시간 (초)",
    )

    health_check_interval_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
        description="건강 체크 주기 (초)",
    )

    # ==========================================================================
    # 메트릭 / Prometheus
    # ==========================================================================
    metrics_enabled: bool = Field(
        default=True,
        description="Prometheus 메트릭 수집 활성화",
    )

    prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Prometheus HTTP API 엔드포인트 URL",
    )

    # ==========================================================================
    # 동적 스케일링
    # ==========================================================================
    warmup_initial_percentage: float = Field(
        default=10.0,
        ge=0.0,
        le=100.0,
        description="새 Cell 투입 시 초기 트래픽 비율 (%)",
    )

    warmup_step_percentage: float = Field(
        default=20.0,
        ge=1.0,
        le=100.0,
        description="프로모션 단계별 증가량 (%)",
    )

    warmup_step_interval_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=3600.0,
        description="프로모션 단계 간 대기 시간 (초)",
    )

    # ==========================================================================
    # Anti-entropy Reconciliation
    # ==========================================================================
    reconciliation_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=300.0,
        description="Anti-entropy Reconciliation 주기 (초)",
    )

    # ==========================================================================
    # 서비스 Heartbeat
    # ==========================================================================
    service_heartbeat_interval_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        description="서비스 Heartbeat 갱신 주기 (초)",
    )

    service_heartbeat_ttl_seconds: float = Field(
        default=300.0,
        ge=10.0,
        le=3600.0,
        description="서비스 Heartbeat 만료 시간 (초). 기본 5분.",
    )


# =============================================================================
# Singleton
# =============================================================================

_settings: CellTopologySettings | None = None
_settings_lock = threading.Lock()


def get_cell_topology_settings() -> CellTopologySettings:
    """CellTopologySettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = CellTopologySettings()
    return _settings


def reset_cell_topology_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
