# 262. Cell Registry — Consistent Hash 기반 Cell 할당

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/registry.py`

---

## 0. 요약

`CellRegistry`는 서비스/테넌트를 **Consistent Hash Ring**으로 Cell에 할당하고, Cell 상태(ACTIVE/DRAINING/ISOLATED)를 관리한다. `BulkheadRegistry.get_or_create()`를 사용해 Cell별 격벽을 자동 생성한다.

---

## 1. 설계 근거

### 1.1 Consistent Hash 선택 이유

- **균일 분배**: N개 Cell에 서비스/테넌트를 균등 분배
- **최소 재배치**: Cell 수 변경 시 영향 최소화 (약 1/N만 이동)
- **결정적**: 동일 키 → 항상 동일 Cell (모든 노드에서 일관된 결과)

### 1.2 기존 패턴 참조

**`BulkheadRegistry`** (`resilience/bulkhead/registry.py` L40):
- 싱글톤 패턴 (`__new__`, `_lock`)
- Settings 주입 (`BulkheadSettings`)
- `get_or_create()` — 이름으로 Bulkhead 생성/조회
- `get_all_states()` — 전체 상태 조회

`CellRegistry`도 동일 패턴을 따른다.

---

## 2. Cell 상태 모델

```python
from enum import Enum


class CellState(str, Enum):
    """Cell 상태."""

    ACTIVE = "active"
    """정상 동작 — 트래픽 수신 중."""

    DRAINING = "draining"
    """대피 중 — 신규 트래픽 차단, 기존 요청 완료 대기."""

    ISOLATED = "isolated"
    """격리됨 — 모든 트래픽 차단."""
```

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CellInfo:
    """Cell 정보."""

    cell_id: str
    """Cell 식별자. 예: 'cell-0', 'cell-3'."""

    state: CellState = CellState.ACTIVE
    """현재 상태."""

    assigned_services: set[str] = field(default_factory=set)
    """할당된 서비스 목록."""

    health_score: float = 1.0
    """건강도 (0.0~1.0). CellHealthAggregator가 갱신."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """생성 시각."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
```

---

## 3. CellRegistry 구현

```python
"""
Cell Registry — Consistent Hash 기반 Cell 할당.

서비스/테넌트를 Cell에 할당하고, Cell별 Bulkhead를 자동 관리합니다.

의존성:
- BulkheadRegistry: Cell별 격벽 생성 (bulkhead_isolation_enabled=True일 때)
- CellTopologySettings: 설정 주입
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

from selfhealing.settings.cell_topology import CellTopologySettings

logger = logging.getLogger(__name__)


class CellRegistry:
    """
    Cell Registry — Consistent Hash Ring 기반 Cell 할당.

    기능:
    1. Consistent Hash Ring으로 서비스/테넌트를 Cell에 할당
    2. Cell 상태 관리 (ACTIVE/DRAINING/ISOLATED)
    3. BulkheadRegistry 연동 — Cell별 격벽 자동 생성
    4. Cell 목록 및 상태 조회

    사용 예시:
        registry = get_cell_registry()
        cell_id = registry.get_cell_for_key("user-12345")
        cell_info = registry.get_cell_info(cell_id)
    """

    def __init__(self, settings: CellTopologySettings | None = None):
        """
        Args:
            settings: Cell Topology 설정
        """
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._lock = threading.RLock()
        self._cells: dict[str, CellInfo] = {}
        self._hash_ring: list[tuple[int, str]] = []

        self._initialize_cells()

    def _initialize_cells(self) -> None:
        """Cell 초기화 및 Hash Ring 구성."""
        for i in range(self._settings.cell_count):
            cell_id = f"{self._settings.cell_prefix}-{i}"
            self._cells[cell_id] = CellInfo(cell_id=cell_id)

        self._build_hash_ring()

        # Bulkhead 자동 등록
        if self._settings.bulkhead_isolation_enabled:
            self._register_cell_bulkheads()

        logger.info(
            f"CellRegistry initialized: {self._settings.cell_count} cells, "
            f"bulkhead={self._settings.bulkhead_isolation_enabled}"
        )

    def _build_hash_ring(self) -> None:
        """
        Consistent Hash Ring 구성.

        각 Cell에 대해 가상 노드(vnode) 150개를 생성하여
        균일 분배를 보장합니다.
        """
        vnodes_per_cell = 150
        ring: list[tuple[int, str]] = []

        for cell_id in self._cells:
            for vnode_idx in range(vnodes_per_cell):
                key = f"{cell_id}:vnode-{vnode_idx}"
                hash_val = self._hash(key)
                ring.append((hash_val, cell_id))

        ring.sort(key=lambda x: x[0])
        self._hash_ring = ring

    @staticmethod
    def _hash(key: str) -> int:
        """SHA-256 기반 해시."""
        return int(hashlib.sha256(key.encode()).hexdigest(), 16)

    def get_cell_for_key(self, key: str) -> str:
        """
        키를 Consistent Hash Ring에서 Cell에 할당.

        DRAINING/ISOLATED Cell은 건너뛰고 다음 ACTIVE Cell을 반환합니다.

        Args:
            key: 할당 키 (서비스명, 테넌트ID, user_id 등)

        Returns:
            cell_id (예: "cell-3")
        """
        if not self._settings.enabled:
            return f"{self._settings.cell_prefix}-0"  # 비활성 시 기본 Cell

        hash_val = self._hash(key)
        ring = self._hash_ring

        if not ring:
            return f"{self._settings.cell_prefix}-0"

        # Binary search로 위치 찾기
        lo, hi = 0, len(ring) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if ring[mid][0] < hash_val:
                lo = mid + 1
            else:
                hi = mid

        # Ring을 순회하며 ACTIVE Cell 찾기
        for offset in range(len(ring)):
            idx = (lo + offset) % len(ring)
            cell_id = ring[idx][1]
            cell = self._cells.get(cell_id)
            if cell and cell.state == CellState.ACTIVE:
                return cell_id

        # 모든 Cell이 비활성이면 첫 번째 반환 (최후의 수단)
        return ring[lo % len(ring)][1]

    def get_cell_info(self, cell_id: str) -> CellInfo | None:
        """Cell 정보 조회."""
        return self._cells.get(cell_id)

    def get_all_cells(self) -> dict[str, CellInfo]:
        """모든 Cell 정보 조회."""
        return dict(self._cells)

    def get_active_cells(self) -> list[str]:
        """ACTIVE 상태 Cell ID 목록."""
        return [
            cell_id
            for cell_id, info in self._cells.items()
            if info.state == CellState.ACTIVE
        ]

    def set_cell_state(self, cell_id: str, state: CellState, reason: str = "") -> bool:
        """
        Cell 상태 변경.

        Args:
            cell_id: Cell 식별자
            state: 새 상태
            reason: 변경 사유

        Returns:
            변경 성공 여부
        """
        with self._lock:
            cell = self._cells.get(cell_id)
            if not cell:
                logger.warning(f"Cell not found: {cell_id}")
                return False

            old_state = cell.state
            cell.state = state
            cell.metadata["last_state_change"] = {
                "from": old_state.value,
                "to": state.value,
                "reason": reason,
            }

            logger.info(
                f"Cell state changed: {cell_id} "
                f"{old_state.value} → {state.value} ({reason})"
            )
            return True

    def update_health_score(self, cell_id: str, score: float) -> None:
        """Cell 건강도 업데이트. CellHealthAggregator가 호출."""
        cell = self._cells.get(cell_id)
        if cell:
            cell.health_score = max(0.0, min(1.0, score))

    def assign_service(self, service_name: str) -> str:
        """
        서비스를 Cell에 할당하고 할당된 cell_id 반환.

        Args:
            service_name: 서비스 이름

        Returns:
            할당된 cell_id
        """
        cell_id = self.get_cell_for_key(service_name)
        cell = self._cells.get(cell_id)
        if cell:
            cell.assigned_services.add(service_name)
        return cell_id

    def _register_cell_bulkheads(self) -> None:
        """
        BulkheadRegistry에 Cell별 Bulkhead 등록.

        BulkheadRegistry.get_or_create()를 사용하여
        Cell별 격벽을 자동 생성합니다.
        """
        try:
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            bulkhead_registry = get_bulkhead_registry()
            for cell_id in self._cells:
                bulkhead_registry.get_or_create(
                    name=cell_id,
                    max_concurrent=self._settings.bulkhead_max_concurrent_per_cell,
                    bulkhead_type=self._settings.bulkhead_type,
                )

            logger.info(
                f"Registered {len(self._cells)} Cell Bulkheads "
                f"(max_concurrent={self._settings.bulkhead_max_concurrent_per_cell})"
            )
        except ImportError:
            logger.warning("BulkheadRegistry not available, skipping Cell Bulkhead registration")
        except Exception as e:
            logger.error(f"Cell Bulkhead registration failed: {e}")
```

---

## 4. BulkheadRegistry 연동 상세

### 4.1 호출 흐름

```
CellRegistry.__init__()
  → _initialize_cells()
    → _register_cell_bulkheads()                           [bulkhead_isolation_enabled=True일 때]
      → BulkheadRegistry.get_or_create("cell-0", 100)     [registry.py L196]
      → BulkheadRegistry.get_or_create("cell-1", 100)
      → ...
      → BulkheadRegistry.get_or_create("cell-7", 100)
```

### 4.2 `BulkheadRegistry.get_or_create()` 내부 동작

**파일**: `resilience/bulkhead/registry.py` L196

```python
def get_or_create(
    self,
    name: str,
    max_concurrent: int | None = None,
    bulkhead_type: str = "semaphore",
) -> Bulkhead:
```

- `name="cell-0"` → `SemaphoreBulkhead(name="cell-0", max_concurrent=100)` 생성
- 이미 존재하면 기존 Bulkhead 반환 (idempotent)
- `BulkheadRegistry.get_all_states()`에 Cell Bulkhead 상태 포함

### 4.3 TrafficGate 연동

`TrafficGate.should_allow(bulkhead_name="cell-3")`을 호출하면:

**파일**: `scaling/traffic_gate.py` L193 → L220 (Bulkhead 격리 단계)

```python
# should_allow() 내부 Stage 1: Bulkhead 격리
if bulkhead_name:
    bulkhead = self._get_bulkhead(bulkhead_name)
    if bulkhead and not bulkhead.try_acquire(timeout=bulkhead_timeout):
        return TrafficDecision(
            allowed=False,
            reason=f"Bulkhead '{bulkhead_name}' full",
            level=self.get_level(),
            gate="bulkhead",
        )
```

→ Cell Bulkhead가 가득 차면 해당 Cell의 트래픽만 차단, 다른 Cell은 영향 없음.

---

## 5. Consistent Hash 동작 예시

### 5.1 8-Cell 할당

```
Cell Count: 8 (cell-0 ~ cell-7)
VNodes per Cell: 150
Total Ring Entries: 1,200

key="user-service"     → hash → cell-3
key="order-service"    → hash → cell-7
key="payment-service"  → hash → cell-1
key="user-12345"       → hash → cell-5
key="user-12346"       → hash → cell-2
```

### 5.2 Cell 장애 시 우회

```
cell-3 상태: DRAINING
key="user-service" → hash → cell-3 (DRAINING) → skip → cell-4 (ACTIVE)
```

Ring에서 다음 ACTIVE Cell로 자동 우회. 기존 요청은 cell-3에서 완료 후 드레인.

---

## 6. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정, 구조) |
| `263_CELL_TAGGER.md` | `get_cell_for_key()` 호출자 |
| `264_CELL_HEALTH.md` | `update_health_score()` 호출자 |
| `265_CELL_EVACUATION_POLICY.md` | `set_cell_state()` 호출자 |
| `resilience/bulkhead/registry.py` | Bulkhead 등록 대상 (변경 없음) |
| `scaling/traffic_gate.py` | Cell Bulkhead 확인 (변경 없음) |
