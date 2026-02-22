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
    SELFHEALING_CELL_TOPOLOGY_EVACUATION_HEALTH_THRESHOLD=0.3
    SELFHEALING_CELL_TOPOLOGY_RECOVERY_HEALTH_THRESHOLD=0.7
    SELFHEALING_CELL_TOPOLOGY_EVACUATION_CONSECUTIVE_COUNT=3
    SELFHEALING_CELL_TOPOLOGY_RECOVERY_CONSECUTIVE_COUNT=5
    SELFHEALING_CELL_TOPOLOGY_EVACUATION_DRAIN_GRACE_SECONDS=2.0
    SELFHEALING_CELL_TOPOLOGY_MAX_EVACUATED_RATIO=0.25
    SELFHEALING_CELL_TOPOLOGY_ISOLATION_NOTIFICATION_DURATION_SECONDS=3600
    SELFHEALING_CELL_TOPOLOGY_EVACUATION_HISTORY_MAX_SIZE=1000
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
        description="대피 트리거 건강도 임계값 (이 값 이하이면 대피 카운터 증가)",
    )

    recovery_health_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="복구 트리거 건강도 임계값 (이 값 이상이면 복구 카운터 증가)",
    )

    evacuation_consecutive_count: int = Field(
        default=3,
        ge=1,
        le=30,
        description="연속 N회 임계치 이하 시 대피 트리거 (히스테리시스)",
    )

    recovery_consecutive_count: int = Field(
        default=5,
        ge=1,
        le=30,
        description="연속 N회 임계치 이상 시 자동 복구 트리거 (히스테리시스)",
    )

    evacuation_traffic_drain_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="트래픽 드레인 대기 시간 (초)",
    )

    evacuation_drain_grace_seconds: float = Field(
        default=2.0,
        ge=0.0,
        le=30.0,
        description="NTP Drift 허용 버퍼 (초). 드레인 시간 경과 판단 시 추가 여유.",
    )

    max_evacuated_ratio: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description=("전체 Cell 중 최대 격리 허용 비율 (Cascading Failure 방지). " "예: 0.25 → 8 Cell 기준 최대 2개만 격리"),
    )

    isolation_notification_duration_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="격리 통보 시 RegionalIsolationGate에 전달하는 격리 예상 지속 시간 (초)",
    )

    evacuation_history_max_size: int = Field(
        default=1000,
        ge=10,
        le=100000,
        description="CellEvacuationPolicy 인메모리 대피 이력 최대 보관 건수",
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

    # ==========================================================================
    # Trust Boundary 제어
    # ==========================================================================
    internal_dns_suffixes: list[str] = Field(
        default=[".svc.cluster.local", ".internal"],
        description=(
            "내부 서비스로 간주할 DNS 접미사. "
            "이 접미사에 해당하는 호스트에만 OTel Baggage(cell_id 등)를 전파한다. "
            "예: Kubernetes 환경에서 .svc.cluster.local"
        ),
    )

    trusted_source_cidrs: list[str] = Field(
        default=[
            "10.0.0.0/8",  # RFC 1918 Class A — K8s Pod/Service CIDR 기본값
            "172.16.0.0/12",  # RFC 1918 Class B
            "192.168.0.0/16",  # RFC 1918 Class C
            "127.0.0.0/8",  # Loopback (개발 환경)
        ],
        description=(
            "cell_id 전파를 신뢰할 소스 CIDR 목록. "
            "이 대역에서 온 요청만 상위 서비스의 cell_id를 수용한다. "
            "퍼블릭 인터넷에서 온 요청은 로컬 해싱으로 폴백."
        ),
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
