"""
Cell Topology — 논리적 트래픽 격벽 관리.

Consistent Hash Ring 기반으로 서비스/테넌트를 Cell에 할당하고,
Cell별 Bulkhead 격벽을 통해 논리적 트래픽 격리를 구현합니다.

Cell은 논리적 동시성 풀이며, DB/Redis/캐시 클러스터를 공유합니다.
물리적 리전 분리는 multiregion/ 모듈이 담당합니다.
"""

from selfhealing.services.cell_topology.health import (
    CellHealthAggregator,
    CellHealthSnapshot,
    get_cell_health_aggregator,
    reset_cell_health_aggregator,
    setup_cell_health_scheduler,
)
from selfhealing.services.cell_topology.models import (
    CELL_STATE_PRIORITY,
    CellInfo,
    CellState,
)
from selfhealing.services.cell_topology.policy import (
    CellEvacuationPolicy,
    EvacuationRecord,
    get_cell_evacuation_policy,
    reset_cell_evacuation_policy,
)
from selfhealing.services.cell_topology.registry import (
    CellRegistry,
    get_cell_registry,
    reset_cell_registry,
)

__all__ = [
    "CELL_STATE_PRIORITY",
    "CellEvacuationPolicy",
    "CellHealthAggregator",
    "CellHealthSnapshot",
    "CellInfo",
    "CellRegistry",
    "CellState",
    "EvacuationRecord",
    "get_cell_evacuation_policy",
    "get_cell_health_aggregator",
    "get_cell_registry",
    "reset_cell_evacuation_policy",
    "reset_cell_health_aggregator",
    "reset_cell_registry",
    "setup_cell_health_scheduler",
]
