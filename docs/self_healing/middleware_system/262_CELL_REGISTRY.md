# 262. Cell Registry — Consistent Hash 기반 Cell 할당

> **Version**: 1.2.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/registry.py`

---

## 0. 요약

> ⚠️ **격리 범위 (Isolation Scope)**: Cell은 **논리적 트래픽 격벽(Logical Traffic Isolation)**이다.
> DB, Redis, 캐시 클러스터를 공유하며, 물리적 데이터 파티셔닝(Physical Sharding)이 **아니다**.
> 물리적 리전 분리는 `multiregion/` 모듈이 담당한다.

`CellRegistry`는 서비스/테넌트를 **Consistent Hash Ring**으로 Cell에 할당하고, Cell 상태(ACTIVE/WARMUP/DRAINING/ISOLATED)를 관리한다. `BulkheadRegistry.get_or_create()`를 사용해 Cell별 격벽을 자동 생성한다.

**v1.2.0 주요 변경 (Cross-Language 확장)**:
- **Envoy Proxy 통합**: nginx → Envoy 마이그레이션 계획, Ring Hash LB + Lua Cell 라우팅 (§11)
- **Proto CellService 추가**: `selfhealing.proto`에 Cell 전용 gRPC 서비스 정의 (§12)
- **Go Thin Client Ring Cache**: `IPCStateCache` 패턴 기반 네이티브 속도 Ring 스냅샷 캐싱 (§13)
- **K8s Envoy Sidecar 배포**: 기존 sidecar-injection 패턴 확장 (§14)

**v1.1.0 주요 변경 (리뷰 반영)**:
- **멀티-프로세스 동기화**: L1(Memory) + L2(Redis) + Pub/Sub + 주기적 Reconciliation (§7)
- **Logical Isolation 명시**: Cell ≠ Physical Shard 혼동 방지 (§0, §1.3)
- **서비스 Heartbeat**: `assign_service` → TTL 기반 동적 할당으로 변경 (§8)
- **동적 스케일링**: 런타임 Ring 리사이징 + WARMUP 상태 (§9, §10)

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

### 1.3 격리 범위 — Logical vs Physical

Cell Topology는 **애플리케이션 레벨의 논리적 격벽**이며, 데이터의 물리적 파티셔닝과는 별개 레이어이다.

| 구분 | Cell Topology (본 문서) | Multi-Region (`multiregion/`) |
|------|------------------------|-------------------------------|
| **격리 단위** | 논리적 동시성 풀 (Bulkhead) | 물리적 리전/클러스터 |
| **DB 공유** | ✅ 동일 PostgreSQL 클러스터 공유 | ❌ 리전별 독립 DB |
| **Redis 공유** | ✅ 동일 Redis 클러스터 공유 | ❌ 리전별 독립 Redis + 복제 |
| **캐시 공유** | ✅ 공유 | ❌ 리전별 독립 |
| **Cell 이동 시** | Bulkhead 풀만 변경 (데이터 이동 없음) | 데이터 복제 + 충돌 해결 필요 |
| **구현 위치** | `services/cell_topology/` | `multiregion/replicator.py` |

**결론**: Cell-3에서 Cell-4로 트래픽이 우회되더라도 DB 커넥션, 캐시, 세션은 그대로 유지된다.
동일 인프라를 공유하므로 데이터 정합성 문제가 발생하지 않는다.

> 📌 **미래 확장**: 물리적 Cell 격리가 필요해지면 `ResilientStorageBackend`의
> Namespace 지원(`get_effective_key_prefix`)을 활용하여 Cell별 키 프리픽스
> (예: `cell-3:cb:user-service`)로 논리적 분리를 먼저 도입할 수 있다.

---

## 2. Cell 상태 모델

```python
from enum import Enum


class CellState(str, Enum):
    """Cell 상태."""

    ACTIVE = "active"
    """정상 동작 — 트래픽 100% 수신 중."""

    WARMUP = "warmup"
    """예열 중 — 트래픽 점진적 투입 (percentage 기반)."""

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

    warmup_percentage: float = 0.0
    """WARMUP 상태일 때 트래픽 투입 비율 (0.0~100.0). ACTIVE일 때는 무시."""

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
    2. Cell 상태 관리 (ACTIVE/WARMUP/DRAINING/ISOLATED)
    3. BulkheadRegistry 연동 — Cell별 격벽 자동 생성
    4. Cell 목록 및 상태 조회
    5. L1(Memory) + L2(Redis) 2-Tier 상태 동기화
    6. 서비스 Heartbeat 기반 동적 할당/만료
    7. 런타임 동적 Ring 리사이징

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

        # Ring을 순회하며 ACTIVE/WARMUP Cell 찾기
        for offset in range(len(ring)):
            idx = (lo + offset) % len(ring)
            cell_id = ring[idx][1]
            cell = self._cells.get(cell_id)
            if not cell:
                continue

            if cell.state == CellState.ACTIVE:
                return cell_id

            # WARMUP Cell: percentage 기반 확률적 라우팅
            if cell.state == CellState.WARMUP:
                if (hash_val % 100) < cell.warmup_percentage:
                    return cell_id
                continue  # percentage 밖이면 다음 ACTIVE Cell로

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
        서비스를 Cell에 할당하고 Heartbeat를 갱신.

        매 요청마다 호출하지 않고, CellTagger 미들웨어의
        백그라운드 Heartbeat 스레드가 30초 주기로 호출한다.
        TTL 만료(5분) 시 CellHealthAggregator가 자동 제거.

        Args:
            service_name: 서비스 이름

        Returns:
            할당된 cell_id
        """
        cell_id = self.get_cell_for_key(service_name)
        cell = self._cells.get(cell_id)
        if cell:
            cell.assigned_services.add(service_name)
            # L2(Redis)에 Heartbeat 기록 — TTL 자동 만료
            self._record_service_heartbeat(cell_id, service_name)
        return cell_id

    def _record_service_heartbeat(
        self, cell_id: str, service_name: str
    ) -> None:
        """
        Redis ZADD로 서비스 Heartbeat 기록.

        키: selfhealing:cell:{cell_id}:services
        Score: 현재 timestamp
        TTL: 서비스가 5분간 Heartbeat 없으면 자동 만료.
        """
        try:
            import time

            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            key = f"selfhealing:cell:{cell_id}:services"
            redis.zadd(key, {service_name: time.time()})
        except Exception as e:
            logger.debug(f"Service heartbeat recording failed: {e}")

    def _evict_expired_services(
        self, cell_id: str, ttl_seconds: float = 300.0
    ) -> list[str]:
        """
        TTL 만료된 서비스를 Cell에서 제거.

        CellHealthAggregator가 Reconciliation 시점에 호출.
        5분(300초) 이상 Heartbeat가 없는 서비스를 ZRANGEBYSCORE로 탐지.

        Returns:
            제거된 서비스 목록
        """
        evicted: list[str] = []
        try:
            import time

            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            key = f"selfhealing:cell:{cell_id}:services"
            cutoff = time.time() - ttl_seconds

            # 만료된 서비스 조회
            expired = redis.zrangebyscore(key, "-inf", cutoff)
            if expired:
                redis.zrem(key, *expired)

                # L1 메모리에서도 제거
                cell = self._cells.get(cell_id)
                if cell:
                    for svc in expired:
                        svc_str = svc if isinstance(svc, str) else svc.decode()
                        cell.assigned_services.discard(svc_str)
                        evicted.append(svc_str)

                logger.info(
                    f"Evicted {len(evicted)} expired services from {cell_id}: {evicted}"
                )
        except Exception as e:
            logger.debug(f"Service eviction failed for {cell_id}: {e}")

        return evicted

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

    # ── L1/L2 동기화 (§7 참조) ──────────────────────────────

    def _sync_state_to_redis(self, cell_id: str) -> None:
        """
        Cell 상태를 L2(Redis Hash)에 기록.

        set_cell_state() 호출 후 자동 실행.
        Redis Pub/Sub로 다른 워커에 즉시 전파.
        """
        try:
            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            cell = self._cells.get(cell_id)
            if not cell:
                return

            key = f"selfhealing:cell:state:{cell_id}"
            redis.hset(key, mapping={
                "state": cell.state.value,
                "health_score": str(cell.health_score),
                "warmup_percentage": str(cell.warmup_percentage),
            })

            # Pub/Sub 즉시 전파
            redis.publish(
                "selfhealing:cell:state_changed",
                f"{cell_id}:{cell.state.value}",
            )
        except Exception as e:
            logger.warning(f"L2 state sync failed for {cell_id}: {e}")

    def _load_all_states_from_redis(self) -> int:
        """
        L2(Redis)에서 모든 Cell 상태를 L1에 로드 (Anti-entropy Reconciliation).

        워커 시작 시 1회 + 주기적 폴링 (10~30초)으로 호출.
        Pub/Sub 이벤트 누락 시 보정 역할.

        Returns:
            동기화된 Cell 수
        """
        synced = 0
        try:
            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            for cell_id in self._cells:
                key = f"selfhealing:cell:state:{cell_id}"
                data = redis.hgetall(key)
                if not data:
                    continue

                cell = self._cells[cell_id]
                state_str = data.get("state") or data.get(b"state")
                if state_str:
                    if isinstance(state_str, bytes):
                        state_str = state_str.decode()
                    new_state = CellState(state_str)
                    # Most Restrictive Wins (DriftReconciler 패턴)
                    # ISOLATED(3) > DRAINING(2) > WARMUP(1) > ACTIVE(0)
                    state_priority = {
                        CellState.ACTIVE: 0,
                        CellState.WARMUP: 1,
                        CellState.DRAINING: 2,
                        CellState.ISOLATED: 3,
                    }
                    if state_priority.get(new_state, 0) >= state_priority.get(
                        cell.state, 0
                    ):
                        cell.state = new_state

                health_str = data.get("health_score") or data.get(b"health_score")
                if health_str:
                    if isinstance(health_str, bytes):
                        health_str = health_str.decode()
                    cell.health_score = max(0.0, min(1.0, float(health_str)))

                warmup_str = data.get("warmup_percentage") or data.get(
                    b"warmup_percentage"
                )
                if warmup_str:
                    if isinstance(warmup_str, bytes):
                        warmup_str = warmup_str.decode()
                    cell.warmup_percentage = max(0.0, min(100.0, float(warmup_str)))

                synced += 1
        except Exception as e:
            logger.warning(f"L2 state load failed: {e}")

        return synced

    def _subscribe_state_changes(self) -> None:
        """
        Redis Pub/Sub으로 Cell 상태 변경 이벤트 구독.

        Tier 1 (즉시 동기화) — 다른 워커의 상태 변경을 실시간 수신.
        Pub/Sub은 At-most-once이므로 _load_all_states_from_redis()로 보정.
        """
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.CONFIG_UPDATED,
                self._on_cell_state_event,
            )
            logger.info("[CellRegistry] Subscribed to cell state change events")
        except Exception as e:
            logger.warning(f"[CellRegistry] Event subscription failed: {e}")

    def _on_cell_state_event(self, event) -> None:
        """Cell 상태 변경 이벤트 핸들러."""
        data = getattr(event, "data", {}) or {}
        if data.get("config_type") != "cell_topology":
            return

        cell_id = data.get("cell_id")
        new_state_str = data.get("state")
        if cell_id and new_state_str and cell_id in self._cells:
            try:
                self._cells[cell_id].state = CellState(new_state_str)
            except ValueError:
                pass

    # ── 동적 스케일링 (§9 참조) ────────────────────────────

    def add_cells(self, count: int) -> list[str]:
        """
        런타임에 Cell을 추가하고 Hash Ring을 리빌딩.

        새 Cell은 WARMUP 상태로 시작하여 점진적으로
        트래픽을 투입받는다.

        Args:
            count: 추가할 Cell 수

        Returns:
            추가된 cell_id 목록
        """
        with self._lock:
            added: list[str] = []
            current_count = len(self._cells)

            for i in range(count):
                cell_id = f"{self._settings.cell_prefix}-{current_count + i}"
                cell = CellInfo(
                    cell_id=cell_id,
                    state=CellState.WARMUP,
                    warmup_percentage=self._settings.warmup_initial_percentage,
                )
                self._cells[cell_id] = cell
                added.append(cell_id)

            # Copy-on-Write Ring 리빌딩 (GIL-safe atomic swap)
            self._build_hash_ring()

            # 새 Cell에 Bulkhead 등록
            if self._settings.bulkhead_isolation_enabled:
                self._register_cell_bulkheads()

            # L2에 새 Cell 상태 기록
            for cell_id in added:
                self._sync_state_to_redis(cell_id)

            logger.info(
                f"Added {count} cells (WARMUP): {added}. "
                f"Total: {len(self._cells)} cells"
            )
            return added

    def remove_cells(self, cell_ids: list[str]) -> list[str]:
        """
        Cell을 제거하기 전 DRAINING → ISOLATED → 삭제.

        즉시 삭제하지 않고 DRAINING으로 전환만 수행.
        실제 삭제는 CellEvacuationPolicy가 드레인 완료 후 호출.

        Args:
            cell_ids: 제거할 Cell ID 목록

        Returns:
            DRAINING으로 전환된 cell_id 목록
        """
        drained: list[str] = []
        for cell_id in cell_ids:
            if self.set_cell_state(cell_id, CellState.DRAINING, reason="scale_in"):
                self._sync_state_to_redis(cell_id)
                drained.append(cell_id)
        return drained
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

### 5.3 WARMUP Cell 점진적 라우팅

```
cell-8 상태: WARMUP (warmup_percentage=20)
key="user-12345" → hash=37 → cell-8 (WARMUP, 37 >= 20) → skip → cell-0 (ACTIVE)
key="user-67890" → hash=15 → cell-8 (WARMUP, 15 < 20)  → ✅ cell-8 선택
```

`hash_val % 100 < warmup_percentage` 조건으로 확률적 라우팅.
20% → 50% → 80% → 100%(ACTIVE) 순서로 점진적 프로모션.

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
| `adapters/memory/drift_reconciliation.py` | Most Restrictive Wins 전략 참조 |
| `adapters/memory/layered_repository/` | L1/L2 동기화 패턴 참조 |
| `services/canary/feature_flag.py` | WARMUP percentage 패턴 참조 |
| `coordination/redis_elector.py` | Heartbeat + TTL Lease 패턴 참조 |

---

## 7. 멀티-프로세스 상태 동기화

> **리뷰 반영**: 워커 간 Brain Split 방지를 위한 L1/L2 동기화 설계.

### 7.1 문제

Django(Gunicorn/uWSGI)와 Celery는 멀티-프로세스로 동작한다.
`self._cells`가 인메모리(dict)이므로, A 프로세스에서 `cell-3`을 DRAINING으로
변경해도 B 프로세스는 여전히 ACTIVE로 인식한다.

### 7.2 해결 — L1(Memory) + L2(Redis) + Pub/Sub

기존 `LayeredRepositoryBase` + `RegionalIsolationGate` 패턴을 그대로 따른다.

```
┌─ Worker A ──────────┐   ┌─ Worker B ──────────┐
│ L1: _cells (dict)   │   │ L1: _cells (dict)   │
│ threading.RLock      │   │ threading.RLock      │
└───────┬─────────────┘   └───────┬─────────────┘
        │ 즉시 Pub/Sub + 주기적 Poll │
        ▼                          ▼
┌──────────────────────────────────────────────┐
│ L2: Redis Hash  selfhealing:cell:state:{id}  │
│ Pub/Sub: selfhealing:cell:state_changed       │
└──────────────────────────────────────────────┘
```

### 7.3 3-Tier 동기화 메커니즘

| Tier | 메커니즘 | 지연 | 참조 패턴 |
|------|----------|------|----------|
| **Tier 1 (즉시)** | `set_cell_state()` → Redis Hash 기록 + Pub/Sub 발행 | < 1초 | `RegionalIsolationGate.isolate_region()` |
| **Tier 2 (주기적)** | 백그라운드 스레드가 10~30초 주기로 Redis Hash 전체 Pull | 10~30초 | `LayeredRepository._reconcile_all_drift()` |
| **Tier 3 (안전장치)** | "Most Restrictive Wins" — L1과 L2 불일치 시 더 제한적인 상태 채택 | N/A | `DriftReconciler.reconcile()` |

### 7.4 Anti-entropy Reconciliation

Pub/Sub은 본질적으로 **At-most-once** 전송이다. 네트워크 단절이나
워커 재시작 시 이벤트 누락이 발생할 수 있다.

이를 보정하기 위해 `_load_all_states_from_redis()`가 주기적으로 실행된다:

```python
# 10~30초 주기 폴링 (DriftReconciler Jitter 적용)
def _reconciliation_loop(self) -> None:
    """백그라운드 Anti-entropy 루프."""
    while self._running:
        jitter = calculate_jitter(max_delay_seconds=5.0)
        time.sleep(self._reconciliation_interval + jitter)
        synced = self._load_all_states_from_redis()
        logger.debug(f"Reconciliation: synced {synced} cells")
```

**Thundering Herd 방지**: `DriftReconciler`의 Jitter 패턴(`drift_reconciliation.py` L200)을
적용하여 모든 워커가 동시에 Redis를 폴링하는 것을 방지한다.

### 7.5 상태 우선순위 (Most Restrictive Wins)

`DriftReconciler`의 CB 상태 우선순위(`open > half_open > closed`)와 동일 패턴:

```
ISOLATED (3) > DRAINING (2) > WARMUP (1) > ACTIVE (0)
```

L1과 L2가 불일치할 때, **더 제한적인 상태가 항상 승리**한다.
이는 안전 측면에서 보수적인 선택이다: 실제로는 ACTIVE인데
DRAINING으로 잘못 판단하면 트래픽이 우회되지만,
DRAINING인데 ACTIVE로 잘못 판단하면 장애 Cell로 트래픽이 유입된다.

### 7.6 Redis 키 설계

| 키 패턴 | 타입 | TTL | 용도 |
|---------|------|-----|------|
| `selfhealing:cell:state:{cell_id}` | Hash | 없음 | Cell 상태, 건강도, warmup_percentage |
| `selfhealing:cell:state_changed` | Pub/Sub 채널 | N/A | 상태 변경 즉시 전파 |
| `selfhealing:cell:{cell_id}:services` | Sorted Set | 없음 (Score=timestamp) | 서비스 Heartbeat (§8) |

---

## 8. 서비스 Heartbeat 기반 동적 할당

> **리뷰 반영**: `assign_service`를 정적 `set.add()`에서 TTL 기반 Heartbeat로 변경.

### 8.1 문제

기존 `assigned_services: set[str]`는:
- 인메모리 전용 → 워커 재시작 시 손실
- 단방향 추가만 가능 → 서비스 제거/만료 메커니즘 없음
- 장기 가동 시 Deprecated 서비스가 누적 → 건강도 집계 왜곡

### 8.2 해결 — Redis ZADD + TTL 자동 만료

`RedisLeaderElector`의 Lease TTL 패턴과 `RedisDLQRepository`의 ZADD 패턴을 결합:

```
CellTagger 미들웨어 (백그라운드, 30초 주기)
  → assign_service("user-service")
    → ZADD selfhealing:cell:cell-3:services {"user-service": 1740000000.0}

CellHealthAggregator (Reconciliation 시점)
  → _evict_expired_services("cell-3", ttl_seconds=300)
    → ZRANGEBYSCORE selfhealing:cell:cell-3:services -inf (now - 300)
    → ZREM expired services
    → L1 assigned_services.discard()
```

### 8.3 호출 흐름

```
요청 처리 (Hot Path) ── Lock-free, Redis 호출 없음
  → get_cell_for_key(key) → cell_id

백그라운드 Heartbeat (30초 주기) ── 별도 스레드
  → assign_service(service_name)
    → L1: cell.assigned_services.add()
    → L2: Redis ZADD (score=timestamp)

Reconciliation (1~5분 주기) ── CellHealthAggregator
  → _evict_expired_services(cell_id, ttl_seconds=300)
    → Redis ZRANGEBYSCORE (만료 서비스 탐지)
    → Redis ZREM + L1 discard
```

**핵심**: Hot Path(`get_cell_for_key`)에는 Redis 호출이 **전혀 없다**.
Heartbeat는 백그라운드에서 30초마다 실행되므로 성능 영향 없음.

### 8.4 생명주기 정리

| 시점 | 동작 | 호출자 |
|------|------|---------|
| 시스템 부트스트랩 | 알려진 서비스 일괄 등록 | AppConfig.ready() |
| 런타임 (30초 주기) | 서비스 Heartbeat 갱신 | CellTagger 미들웨어 |
| Reconciliation (1~5분) | 만료 서비스 퇴출 | CellHealthAggregator |
| 서비스 Deprecated | 5분 무응답 → 자동 만료 | TTL 기반 자동 |

---

## 9. 동적 스케일링 (Runtime Ring Resizing)

> **리뷰 반영**: 런타임 Cell 추가/제거 + Thundering Herd 방지.

### 9.1 Scale-Out 흐름

```
운영자/오토스케일러 → add_cells(2)
  → cell-8 (WARMUP, 10%), cell-9 (WARMUP, 10%) 생성
  → Copy-on-Write Ring 리빌딩
  → L2(Redis) 상태 기록 + Pub/Sub 전파
  → BulkheadRegistry.get_or_create("cell-8"), ...

CellEvacuationPolicy (주기적)
  → cell-8 건강도 정상 확인
  → set_warmup_percentage("cell-8", 30)
  → set_warmup_percentage("cell-8", 60)
  → set_warmup_percentage("cell-8", 100)
  → set_cell_state("cell-8", ACTIVE)        ← 프로모션 완료
```

### 9.2 Scale-In 흐름

```
운영자/오토스케일러 → remove_cells(["cell-8", "cell-9"])
  → cell-8, cell-9 → DRAINING 전환
  → 신규 트래픽 차단, 기존 요청 완료 대기

CellEvacuationPolicy (드레인 완료 확인 후)
  → _finalize_remove("cell-8")
    → del self._cells["cell-8"]
    → Ring 리빌딩
```

### 9.3 WARMUP 상태 — Thundering Herd 방지

새 Cell을 즉시 ACTIVE로 두면 캐시 히트율 0%, 커넥션 풀 콜드 상태에서
tthundering Herd가 발생한다.

**해결**: `CanaryRolloutService`의 단계별 배포 패턴과 동일하게 점진적 프로모션:

| 단계 | warmup_percentage | 트래픽 비율 | 참조 패턴 |
|------|-------------------|-------------|----------|
| Stage 1 | 10% | ~2% 전체 트래픽 | `CanaryStage(percentage=10)` |
| Stage 2 | 30% | ~6% 전체 트래픽 | |
| Stage 3 | 60% | ~12% 전체 트래픽 | |
| Stage 4 | 100% → ACTIVE 전환 | 균등 분배 | `CanaryStage(percentage=100)` |

각 단계에서 건강도를 확인하고, 이상 시 즉시 ISOLATED로 전환.

### 9.4 Copy-on-Write Ring 리빌딩

```python
def _build_hash_ring(self) -> None:
    # 새 리스트를 별도로 구성
    new_ring: list[tuple[int, str]] = []
    for cell_id in self._cells:
        for vnode_idx in range(150):
            key = f"{cell_id}:vnode-{vnode_idx}"
            new_ring.append((self._hash(key), cell_id))
    new_ring.sort(key=lambda x: x[0])

    # Atomic reference swap (GIL-safe)
    self._hash_ring = new_ring
```

`self._hash_ring = new_ring`은 Python GIL 덕분에 원자적 참조 교체이다.
읽기 중인 다른 스레드는 이전 Ring을 안전하게 사용 완료한 후 새 Ring을 참조한다.

### 9.5 Ring 변경 전파

`GlobalConfigPropagator`의 `TIER_1_IMMEDIATE` 전파를 활용:

```python
# add_cells() 호출 후
propagator = get_global_config_propagator()
propagator.propagate(GlobalConfigChange(
    config_type="cell_topology",
    config_key="cell_count",
    new_value=len(self._cells),
    previous_value=previous_count,
    scope=ConfigScope.GLOBAL,
    tier=PropagationTier.TIER_1_IMMEDIATE,
))
```

모든 워커가 1초 내에 Ring을 리빌딩하도록 보장.
TIER_1 전파 실패 시 Tier 2 주기적 Reconciliation(§7.4)이 보정.

### 9.6 CellTopologySettings 추가 필드

```python
# 동적 스케일링 설정
warmup_initial_percentage: float = 10.0
"""새 Cell 투입 시 초기 트래픽 비율 (%)."""

warmup_step_percentage: float = 20.0
"""프로모션 단계별 증가량 (%)."""

warmup_step_interval_seconds: float = 60.0
"""프로모션 단계 간 대기 시간 (초)."""

reconciliation_interval_seconds: float = 15.0
"""Anti-entropy Reconciliation 주기 (초)."""

service_heartbeat_interval_seconds: float = 30.0
"""서비스 Heartbeat 갱신 주기 (초)."""

service_heartbeat_ttl_seconds: float = 300.0
"""서비스 Heartbeat 만료 시간 (초). 기본 5분."""
```

---

## 10. 상태 전이 다이어그램

```
                    add_cells()
                        │
                        ▼
                ┌──────────────┐
                │   WARMUP     │ ← 트래픽 점진적 투입 (10% → 30% → 60% → 100%)
                │  (10~100%)   │
                └──────┬───────┘
                       │ warmup_percentage == 100%
                       ▼
┌──────────────────────────────────────┐
│              ACTIVE                  │ ← 정상 동작, 트래픽 100%
│         (정상 운영 상태)                │
└──────┬────────────────┬──────────────┘
       │                │
       │ 장애 감지       │ scale_in / 장애
       │ (health < 0.3)  │
       ▼                ▼
┌─────────────┐  ┌──────────────┐
│  DRAINING   │  │  ISOLATED    │
│ (신규 차단,  │  │ (모든 트래픽  │
│  기존 완료)  │  │   차단)      │
└──────┬──────┘  └──────┬───────┘
       │                │
       │ 드레인 완료      │ 장애 해결
       ▼                ▼
   Cell 삭제        WARMUP으로 복귀
   (scale_in)      (점진적 재투입)
```

**전이 규칙**:
- `WARMUP → ACTIVE`: warmup_percentage가 100%에 도달하고 건강도 정상일 때
- `ACTIVE → DRAINING`: 계획된 스케일인 또는 대피
- `ACTIVE → ISOLATED`: 긴급 장애 격리
- `ISOLATED → WARMUP`: 장애 해결 후 점진적 재투입 (즉시 ACTIVE가 아님)
- `DRAINING → 삭제`: 모든 진행 중 요청 완료 후

---

## 11. Envoy Proxy 통합 계획

> 현재 nginx 리버스 프록시(`nginx/nginx.conf`)를 Envoy로 마이그레이션하여
> Cell 인지 라우팅, Ring Hash LB, gRPC 네이티브 지원을 확보한다.

### 11.1 현재 nginx 구성 분석

현재 프록시 인프라 (`nginx/nginx.conf`):

```nginx
# nginx/nginx.conf — 현재 구성
upstream django_app {
    server web:8000;
    keepalive 16;           # ← Envoy: cluster.upstream_connection_options
}

server {
    listen 80;
    client_max_body_size 20M;  # ← Envoy: max_request_bytes

    # Gzip 압축
    gzip on;
    gzip_comp_level 5;
    gzip_types text/plain text/css application/javascript application/json;

    # Proxy 설정
    location / {
        proxy_pass http://django_app;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_connect_timeout 3s;   # ← Envoy: connect_timeout
        proxy_read_timeout 30s;     # ← Envoy: timeout
    }

    # API (no-cache)
    location /api/ {
        proxy_pass http://django_app;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
    }

    # Health check
    location /health/ {
        return 200 "OK\n";
    }
}
```

### 11.2 Envoy 설정 매핑 (envoy.yaml 계획)

nginx 기능을 1:1 매핑하면서 Cell 라우팅을 추가한다:

| nginx 기능 | Envoy 매핑 | 비고 |
|---|---|---|
| `keepalive 16` | `cluster.max_requests_per_connection` + `upstream_connection_options` | HTTP/2 멀티플렉싱으로 자연 대체 |
| `gzip on` | `http_filters: envoy.filters.http.compressor` (gzip) | 동일 |
| `proxy_connect_timeout 3s` | `cluster.connect_timeout: 3s` | 동일 |
| `proxy_read_timeout 30s` | `route.timeout: 30s` | 동일 |
| `client_max_body_size 20M` | `http_connection_manager.max_request_bytes: 20971520` | 동일 |
| `X-Forwarded-For` | `use_remote_address: true` + `xff_num_trusted_hops` | 자동 지원 |
| `X-Deadline-Remaining ""` | `request_headers_to_remove` | 동일 |
| `/static/` → alias | `route.direct_response` 또는 별도 file serving | 정적 파일 서빙 |
| `/health/` → 200 | `route.direct_response: 200` | 동일 |
| — | **Ring Hash LB** | 🆕 Cell 할당 키 기반 LB |
| — | **Lua Filter (Cell 라우팅)** | 🆕 요청 → Cell 매핑 |
| — | **gRPC 네이티브** | 🆕 HTTP/2 + gRPC 직접 지원 |

### 11.3 Envoy Cell 라우팅 아키텍처

```
                     ┌─────────────────────────────┐
                     │     Envoy Front Proxy        │
                     │                               │
  Request ──────────►│  1. Lua Filter                │
  (X-Tenant-Id,      │     - 요청에서 Cell 키 추출     │
   X-Service-Name)   │     - X-Cell-Id 헤더 삽입      │
                     │                               │
                     │  2. Ring Hash LB              │
                     │     - X-Cell-Id를 해시 키로     │
                     │     - upstream Cell 선택       │
                     │                               │
                     │  3. Compressor (gzip)         │
                     │  4. Router                    │
                     └──────────┬────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │ Cell-1   │ │ Cell-2   │ │ Cell-3   │
              │ (web:8000)│ │ (web:8001)│ │ (web:8002)│
              └──────────┘ └──────────┘ └──────────┘
```

### 11.4 Envoy 핵심 설정 (계획)

```yaml
# envoy.yaml — Cell 인지 라우팅 설정 (계획)
static_resources:
  listeners:
    - name: main_listener
      address:
        socket_address: { address: 0.0.0.0, port_value: 8000 }
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                use_remote_address: true  # X-Forwarded-For 자동

                # nginx의 client_max_body_size 20M 대응
                stream_idle_timeout: 30s

                http_filters:
                  # 🆕 Cell 라우팅 Lua Filter
                  - name: envoy.filters.http.lua
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
                      inline_code: |
                        function envoy_on_request(request_handle)
                          -- Cell 키 추출 (tenant-id 또는 service-name)
                          local tenant = request_handle:headers():get("x-tenant-id")
                          local service = request_handle:headers():get("x-service-name")
                          local cell_key = tenant or service or "default"

                          -- Ring Hash LB가 사용할 헤더 삽입
                          request_handle:headers():add("x-cell-hash-key", cell_key)
                        end

                  # nginx의 gzip 대응
                  - name: envoy.filters.http.compressor
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.compressor.v3.Compressor
                      response_direction_config:
                        common_config:
                          min_content_length: 1024  # nginx gzip_min_length
                        disable_on_etag_header: true
                      compressor_library:
                        name: text_optimized
                        typed_config:
                          "@type": type.googleapis.com/envoy.extensions.compression.gzip.compressor.v3.Gzip
                          compression_level: COMPRESSION_LEVEL_5  # nginx gzip_comp_level 5

                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: backend
                      domains: ["*"]
                      # nginx의 X-Deadline-Remaining 제거 대응
                      request_headers_to_remove: ["x-deadline-remaining"]
                      routes:
                        # nginx /health/ 직접 응답 대응
                        - match: { prefix: "/health/" }
                          direct_response: { status: 200, body: { inline_string: "OK\n" } }

                        # nginx /static/ 대응 (별도 서빙 또는 S3)
                        - match: { prefix: "/static/" }
                          route:
                            cluster: static_files
                          response_headers_to_add:
                            - header: { key: "Cache-Control", value: "public, immutable" }

                        # nginx /api/ no-cache 대응
                        - match: { prefix: "/api/" }
                          route:
                            cluster: django_cells
                            timeout: 30s  # nginx proxy_read_timeout
                            hash_policy:
                              - header:
                                  header_name: x-cell-hash-key  # Lua가 삽입한 키
                          response_headers_to_add:
                            - header: { key: "Cache-Control", value: "no-store, no-cache, must-revalidate" }

                        # 기본 라우트
                        - match: { prefix: "/" }
                          route:
                            cluster: django_cells
                            timeout: 30s
                            hash_policy:
                              - header:
                                  header_name: x-cell-hash-key

  clusters:
    - name: django_cells
      connect_timeout: 3s  # nginx proxy_connect_timeout 대응
      type: STRICT_DNS
      lb_policy: RING_HASH  # 🆕 Consistent Hash Ring
      ring_hash_lb_config:
        minimum_ring_size: 1024
        maximum_ring_size: 8388608
      load_assignment:
        cluster_name: django_cells
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: web, port_value: 8000 }
      # nginx keepalive 16 대응
      upstream_connection_options:
        tcp_keepalive:
          keepalive_probes: 3
          keepalive_time: 60
          keepalive_interval: 10
```

### 11.5 3-Layer 공존 아키텍처

Envoy는 기존 Python 라우팅과 nginx를 **대체가 아닌 보강**한다:

```
Layer 1: Envoy Front Proxy    ← 인프라 레벨 Ring Hash LB (요청 분배)
Layer 2: Python CellRegistry  ← 애플리케이션 레벨 Cell 할당 (본 문서)
Layer 3: Bulkhead Pool        ← 런타임 동시성 격벽 (BulkheadRegistry)
```

- **Layer 1 (Envoy)**: 네트워크 레벨에서 요청을 올바른 Cell 서버로 라우팅
- **Layer 2 (Python)**: 애플리케이션 내부에서 서비스→Cell 매핑 결정 (Ring 빌드, 상태 관리)
- **Layer 3 (Bulkhead)**: Cell 내부 동시성 제한 (semaphore, thread pool)

> ⚠️ Python `CellRegistry`는 **영구 유지**. Envoy Ring Hash는 네트워크 라우팅 최적화이며,
> Cell 상태 관리(WARMUP/DRAINING/ISOLATED), Ring 빌드, Heartbeat 로직은
> 모두 Python 엔진이 권위(authoritative) 소스로 남는다.

### 11.6 docker-compose.yml 확장 (계획)

현재 `docker-compose.yml`의 nginx 서비스를 Envoy로 대체:

```yaml
# 현재 (nginx)
nginx:
  image: nginx:alpine
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf
  ports: ["8000:80"]
  depends_on: [web]

# 계획 (envoy)
envoy:
  image: envoyproxy/envoy:v1.30-latest
  volumes:
    - ./docker/envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro
  ports:
    - "8000:8000"   # HTTP
    - "8001:8001"   # Admin
    - "50052:50052" # gRPC passthrough
  depends_on: [web]
  command: ["envoy", "-c", "/etc/envoy/envoy.yaml", "--log-level", "info"]
```

---

## 12. Proto CellService 확장

> `selfhealing.proto`에 Cell 전용 gRPC 서비스를 추가하여
> Cross-Language 클라이언트가 Cell 할당/상태를 조회할 수 있게 한다.

### 12.1 현재 Proto 구조 분석

`selfhealing.proto` (약 310줄)의 기존 서비스:

| 서비스 | RPC 수 | 역할 |
|---|---|---|
| `CircuitBreakerService` | 6 RPCs | CB 상태 조회/제어 |
| `DLQService` | 4 RPCs | Dead Letter Queue 관리 |
| `BufferService` | 3 RPCs | 이벤트 버퍼링 |
| `EventService` | 2 RPCs | Server-Side 스트리밍 이벤트 |
| `LearningService` | 4 RPCs | 패턴 학습/추천 |
| `HealthService` | 2 RPCs | 헬스 체크 |

기존 패턴 (`selfhealing.proto` L11-13):
```protobuf
option go_package = "github.com/myproject/selfhealing-go/v1";
option java_package = "com.selfhealing.v1";
option java_multiple_files = true;
```

→ Go, Java 클라이언트 코드 자동 생성 준비 완료.

### 12.2 CellService RPC 정의 (계획)

기존 `EventService`의 Server-Side Streaming 패턴을 따라 설계:

```protobuf
// ── Cell Topology Service ──────────────────────────────
// CellRegistry의 Cross-Language 인터페이스.
// Python CellRegistry가 권위 소스(authoritative source)이며,
// 이 서비스는 조회/구독 전용(read-only) 인터페이스를 제공한다.

service CellService {
  // 키(서비스명/테넌트ID)로 할당된 Cell 조회
  // Python CellRegistry.get_cell_for_key()의 gRPC 인터페이스
  rpc GetCellForKey(GetCellForKeyRequest) returns (GetCellForKeyResponse);

  // 현재 Ring 스냅샷 전체 조회
  // Go Thin Client가 로컬 Ring Cache 초기화에 사용
  rpc GetRingSnapshot(Empty) returns (RingSnapshotResponse);

  // Cell 상태 변경 실시간 스트리밍
  // EventService.SubscribeEvents 패턴과 동일한 Server-Side Streaming
  rpc SubscribeCellStateChanges(CellSubscription) returns (stream CellStateEvent);

  // 서비스 등록 (Heartbeat 갱신)
  // CellRegistry.assign_service()의 gRPC 인터페이스
  rpc RegisterService(RegisterServiceRequest) returns (RegisterServiceResponse);
}
```

### 12.3 CellService 메시지 정의 (계획)

기존 메시지 패턴(`ShouldAllowRequest/Response`, `EventSubscription`)을 따라 설계:

```protobuf
// ── Cell Messages ──────────────────────────────────────

message GetCellForKeyRequest {
  string key = 1;                    // 서비스명 또는 테넌트 ID
  TraceContext trace_context = 2;    // 기존 TraceContext 재사용
}

message GetCellForKeyResponse {
  string cell_name = 1;              // e.g. "cell-1"
  string cell_state = 2;             // "active", "warmup", "draining", "isolated"
  float warmup_percentage = 3;       // WARMUP 상태일 때 현재 투입 비율
  string fallback_cell_name = 4;     // WARMUP/DRAINING 시 대체 Cell
}

message RingSnapshotResponse {
  repeated CellInfo cells = 1;       // 전체 Cell 목록
  int32 virtual_nodes = 2;           // 가상 노드 수 (기본 150)
  string ring_hash = 3;              // Ring 버전 해시 (변경 감지용)
  string timestamp = 4;              // 스냅샷 생성 시각
}

message CellInfo {
  string name = 1;                   // Cell 이름
  string state = 2;                  // CellState enum 값
  float warmup_percentage = 3;       // WARMUP 투입 비율
  int32 weight = 4;                  // Ring 가중치
  repeated string services = 5;      // 할당된 서비스 목록
  string last_heartbeat = 6;         // 마지막 Heartbeat 시각
}

message CellSubscription {
  string client_id = 1;              // 구독 클라이언트 식별
  repeated string cell_names = 2;    // 특정 Cell만 구독 (빈 배열 = 전체)
}

message CellStateEvent {
  string cell_name = 1;              // 변경된 Cell
  string previous_state = 2;         // 이전 상태
  string new_state = 3;              // 새 상태
  float warmup_percentage = 4;       // 현재 WARMUP 비율
  string ring_hash = 5;              // 변경 후 Ring 해시
  string timestamp = 6;              // 이벤트 시각
}

message RegisterServiceRequest {
  string service_name = 1;           // 등록할 서비스명
  string cell_name = 2;              // 특정 Cell 지정 (빈 = 자동 할당)
  TraceContext trace_context = 3;
}

message RegisterServiceResponse {
  bool success = 1;
  string assigned_cell = 2;          // 할당된 Cell 이름
  string message = 3;
}
```

### 12.4 CellServicer 구현 패턴

기존 `CircuitBreakerServicer`(`grpc_server.py` L444-480) 패턴을 따른다:

```python
# grpc_server.py에 추가 예정 — 기존 Servicer 패턴 준수
# 참조: CircuitBreakerServicer (grpc_server.py L444-480)
#   - SidecarGRPCServer 인스턴스 주입
#   - self._server.handle_*() 핸들러로 위임

if GRPC_AVAILABLE:

    class CellServicer:
        """CellService gRPC 구현체.

        기존 CircuitBreakerServicer와 동일한 패턴:
        - __init__에서 SidecarGRPCServer 주입
        - 각 RPC → self._server.handle_cell_*() 핸들러 위임
        """
        def __init__(self, server: SidecarGRPCServer):
            self._server = server

        def GetCellForKey(self, request: Any, context: Any) -> Any:
            return self._server.handle_get_cell_for_key(
                request.key, context)

        def GetRingSnapshot(self, request: Any, context: Any) -> Any:
            return self._server.handle_get_ring_snapshot(context)

        def SubscribeCellStateChanges(
            self, request: Any, context: Any
        ) -> Iterator[Any]:
            # EventServicer.SubscribeEvents (L489-493) 패턴과 동일
            yield from self._server.handle_subscribe_cell_changes(
                request.cell_names, request.client_id, context)

        def RegisterService(self, request: Any, context: Any) -> Any:
            return self._server.handle_register_service(
                request.service_name, request.cell_name, context)
```

### 12.5 _register_services() 확장

현재 `_register_services()` (`grpc_server.py` L194-196)는 stub:

```python
# 현재
def _register_services(self) -> None:
    logger.debug("[GRPCServer] Services registered (stub)")

# 확장 후
def _register_services(self) -> None:
    # 기존 서비스
    selfhealing_pb2_grpc.add_CircuitBreakerServiceServicer_to_server(
        CircuitBreakerServicer(self), self._server)
    selfhealing_pb2_grpc.add_EventServiceServicer_to_server(
        EventServicer(self), self._server)
    selfhealing_pb2_grpc.add_HealthServiceServicer_to_server(
        HealthServicer(self), self._server)
    # 🆕 Cell 서비스
    selfhealing_pb2_grpc.add_CellServiceServicer_to_server(
        CellServicer(self), self._server)
    logger.info("[GRPCServer] Services registered (4 servicers)")
```

---

## 13. Cross-Language Thin Client — Ring Cache

> Go/Java 등 타언어 클라이언트가 매 요청마다 gRPC 호출 없이
> 로컬에서 네이티브 속도로 Cell 할당을 수행할 수 있도록
> Ring 스냅샷을 캐싱하는 패턴을 정의한다.

### 13.1 참조 패턴 — IPCStateCache

`IPCStateCache` (`cb_state_cache.py` L70-128)가 **정확히 동일한 문제**를 해결한다:

```python
# cb_state_cache.py L70-128 — 참조 패턴
class IPCStateCache:
    DEFAULT_TTL_SECONDS = 5.0    # TTL 기반 만료
    MAX_ENTRIES = 10000          # 메모리 상한

    def __init__(self, ttl_seconds, enable_event_invalidation=True):
        self._cache: dict[str, IPCCacheEntry] = {}  # 로컬 캐시
        self._lock = threading.RLock()               # Thread-safe
        self._ttl = ttl_seconds

        if enable_event_invalidation:
            self._register_invalidation_handlers()   # 이벤트 구독

    def _register_invalidation_handlers(self):
        bus = get_event_bus()
        for event_type in [
            EventType.CIRCUIT_BREAKER_OPENED,
            EventType.CIRCUIT_BREAKER_CLOSED,
            EventType.CIRCUIT_BREAKER_HALF_OPENED,
        ]:
            bus.subscribe(event_type, self._on_state_change)  # 변경 시 즉시 무효화

    def _on_state_change(self, event):
        service_name = event.data.get("service_name")
        if service_name:
            self.invalidate(service_name)  # 해당 키만 무효화
```

**패턴 매핑**:

| IPCStateCache (Python, CB) | Go Thin Client (Cell Ring) |
|---|---|
| `dict[str, IPCCacheEntry]` | `sync.RWMutex` + `map[string]*CellInfo` |
| `TTL 5s` → `get()` 만료 체크 | `TTL 30s` (Ring은 CB보다 변경 빈도 낮음) |
| `EventBus.subscribe()` → `invalidate()` | `SubscribeCellStateChanges` gRPC stream → rebuild ring |
| `get_or_set(key, factory)` | `GetCellForKey(key)` — 캐시 miss 시 로컬 Ring에서 해시 |
| `invalidate_all()` | Ring hash 변경 감지 → 전체 스냅샷 재로드 |

### 13.2 Go Thin Client 구조 (계획)

```go
// go_package: github.com/myproject/selfhealing-go/v1
// selfhealing.proto의 option go_package에서 이미 선언됨

package selfhealing

import (
    "sync"
    "time"
    pb "github.com/myproject/selfhealing-go/v1"
)

// CellRingCache — IPCStateCache(cb_state_cache.py) 패턴의 Go 구현
// TTL 기반 로컬 캐시 + gRPC 스트리밍 무효화
type CellRingCache struct {
    mu          sync.RWMutex
    cells       map[string]*pb.CellInfo   // 로컬 Ring 스냅샷
    ringHash    string                     // 변경 감지용 해시
    ttl         time.Duration              // 기본 30s (CB의 5s보다 긴 이유: Ring 변경 빈도 낮음)
    lastRefresh time.Time
    client      pb.CellServiceClient       // gRPC 클라이언트
}

// NewCellRingCache — IPCStateCache.__init__ 패턴
func NewCellRingCache(conn *grpc.ClientConn) *CellRingCache {
    cache := &CellRingCache{
        cells:  make(map[string]*pb.CellInfo),
        ttl:    30 * time.Second,
        client: pb.NewCellServiceClient(conn),
    }
    // IPCStateCache._register_invalidation_handlers() 패턴
    // EventBus.subscribe() → gRPC Server-Side Streaming으로 대체
    go cache.subscribeStateChanges()
    return cache
}

// GetCellForKey — IPCStateCache.get_or_set() 패턴
// 로컬 Ring에서 O(log N) 해싱, gRPC 호출 없음
func (c *CellRingCache) GetCellForKey(key string) string {
    c.mu.RLock()
    defer c.mu.RUnlock()

    if c.isExpired() {
        c.mu.RUnlock()
        c.refreshSnapshot()  // TTL 만료 시 전체 재로드
        c.mu.RLock()
    }

    // Consistent Hash Ring에서 키 해싱 (네이티브 속도)
    return c.hashToCell(key)
}

// subscribeStateChanges — EventStreamProxy.iter_events() 패턴
// cb_state_cache.py _on_state_change() → gRPC streaming 버전
func (c *CellRingCache) subscribeStateChanges() {
    stream, err := c.client.SubscribeCellStateChanges(
        context.Background(),
        &pb.CellSubscription{ClientId: hostname()},
    )
    if err != nil {
        log.Printf("[CellRingCache] subscribe failed: %v", err)
        return
    }

    for {
        event, err := stream.Recv()
        if err != nil {
            // 재연결 로직 (exponential backoff)
            time.Sleep(backoff())
            go c.subscribeStateChanges()
            return
        }

        // IPCStateCache.invalidate() 패턴과 동일
        if event.RingHash != c.ringHash {
            c.refreshSnapshot()  // Ring 버전 변경 → 전체 재로드
        }
    }
}

// refreshSnapshot — IPCStateCache.invalidate_all() + factory reload 패턴
func (c *CellRingCache) refreshSnapshot() {
    resp, err := c.client.GetRingSnapshot(
        context.Background(), &pb.Empty{})
    if err != nil {
        log.Printf("[CellRingCache] snapshot refresh failed: %v", err)
        return
    }

    c.mu.Lock()
    defer c.mu.Unlock()
    c.cells = make(map[string]*pb.CellInfo, len(resp.Cells))
    for _, cell := range resp.Cells {
        c.cells[cell.Name] = cell
    }
    c.ringHash = resp.RingHash
    c.lastRefresh = time.Now()
}
```

### 13.3 성능 비교

| 방식 | 레이턴시 | 처리량 | 사용 시점 |
|---|---|---|---|
| gRPC 매번 호출 (`GetCellForKey`) | ~500μs (네트워크 RTT) | ~2K RPS/연결 | 초기 개발, 저트래픽 |
| Go Ring Cache (본 설계) | ~1-5μs (로컬 해시) | 수백만 RPS | 프로덕션, 고트래픽 |
| mmap 공유 메모리 | ~10μs (참조: `cb_state_snapshot.py`) | 수백만 RPS | 동일 호스트 최적화 |

> 📌 `cb_state_snapshot.py`의 mmap 패턴 (24-byte 헤더 + 72-byte 엔트리, Lock-free reads)은
> Go Thin Client의 **동일 호스트** 최적화 시 참조할 수 있다.
> 단, Cross-Language에서는 gRPC 스트리밍이 더 범용적이다.

### 13.4 Python 엔진의 역할 (영구)

Go Thin Client는 **읽기 전용 캐시**이다. 모든 쓰기 연산은 Python 엔진이 담당:

| 연산 | 담당 | 이유 |
|---|---|---|
| Ring 빌드/리사이즈 (§9) | Python `CellRegistry` | Ring은 복잡한 상태 머신 (WARMUP/DRAINING) |
| Heartbeat 관리 (§8) | Python + Redis ZADD | TTL 계산, eviction 로직 |
| Cell 상태 전이 (§10) | Python `CellRegistry` | 건강도 기반 자동 프로모션/격리 |
| 구성 전파 (§9.5) | Python `GlobalConfigPropagator` | TIER_1 즉시 전파 |
| **Cell 조회 (읽기)** | **Go 로컬 Ring** | **네이티브 해시, ~1-5μs** |
| **상태 구독 (읽기)** | **Go gRPC Stream** | **EventStreamProxy 패턴** |

---

## 14. K8s Envoy Sidecar 배포

> 기존 `k8s/sidecar-injection.yaml`의 패턴을 확장하여
> Envoy sidecar를 Pod에 추가한다.

### 14.1 현재 Sidecar 패턴 분석

`k8s/sidecar-injection.yaml`의 기존 구조:

```
Pod
├── app (Go/Java 앱)           ← containerPort: 8080
├── selfhealing-sidecar (Python) ← containerPort: 50051 (gRPC), 9090 (metrics)
└── Volume: selfhealing-socket   ← emptyDir (Memory), UDS 공유
```

핵심 패턴:
- `emptyDir { medium: Memory }` — UDS 소켓 공유 (`/tmp/selfhealing.sock`)
- `SELFHEALING_GRPC_ADDR: "localhost:50051"` — 앱→사이드카 gRPC fallback
- `ServiceMonitor` — Prometheus 메트릭 수집 (30s 간격)
- **리소스 제한**: 사이드카 64Mi~128Mi CPU 50m~200m

### 14.2 Envoy Sidecar 추가 (계획)

```yaml
# k8s/sidecar-injection.yaml 확장 — Envoy 추가
spec:
  template:
    spec:
      containers:
        # 기존: 메인 앱
        - name: app
          image: my-go-app:latest
          ports:
            - containerPort: 8080
          env:
            - name: SELFHEALING_SOCKET
              value: "/tmp/selfhealing.sock"
            - name: SELFHEALING_GRPC_ADDR
              value: "localhost:50051"

        # 기존: Python selfhealing 사이드카
        - name: selfhealing-sidecar
          image: selfhealing-sidecar:latest
          ports:
            - containerPort: 50051  # gRPC
            - containerPort: 9090  # metrics
          resources:
            requests: { memory: "64Mi", cpu: "50m" }
            limits:   { memory: "128Mi", cpu: "200m" }
          volumeMounts:
            - name: selfhealing-socket
              mountPath: /tmp

        # 🆕 Envoy sidecar
        - name: envoy-sidecar
          image: envoyproxy/envoy:v1.30-latest
          ports:
            - containerPort: 15001  # inbound
            - containerPort: 15006  # outbound
            - containerPort: 9901   # admin
          args:
            - "envoy"
            - "-c"
            - "/etc/envoy/envoy-sidecar.yaml"
            - "--log-level"
            - "warn"
          resources:
            requests: { memory: "32Mi", cpu: "25m" }
            limits:   { memory: "64Mi", cpu: "100m" }
          volumeMounts:
            - name: envoy-config
              mountPath: /etc/envoy
          readinessProbe:
            httpGet:
              path: /ready
              port: 9901
            initialDelaySeconds: 2
            periodSeconds: 5

      volumes:
        # 기존: UDS 소켓
        - name: selfhealing-socket
          emptyDir: { medium: Memory, sizeLimit: "1Mi" }
        # 🆕 Envoy 설정
        - name: envoy-config
          configMap:
            name: envoy-sidecar-config
```

### 14.3 3-Container Pod 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                     Pod                              │
│                                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────┐ │
│  │  App (Go)    │  │  Selfhealing  │  │  Envoy    │ │
│  │  :8080       │◄─┤  Sidecar (Py) │  │  Sidecar  │ │
│  │              │  │  :50051 gRPC  │  │  :15001   │ │
│  │              │  │  :9090 metrics│  │  :9901    │ │
│  └──────┬───────┘  └──────┬────────┘  └─────┬─────┘ │
│         │    UDS           │                  │       │
│         ├─────────────────►│                  │       │
│         │  /tmp/selfhealing.sock              │       │
│         │                                     │       │
│         │         localhost:50051              │       │
│         ├────────────────────────────────────►│       │
│         │                                     │       │
└─────────┴─────────────────────────────────────┴──────┘

트래픽 흐름:
  외부 → Envoy(:15001) → App(:8080)       # inbound
  App → Envoy(:15006) → 외부 서비스        # outbound (Cell-aware)
  App → UDS/gRPC → Selfhealing Sidecar    # Cell 조회, CB 체크
```

### 14.4 리소스 요약

| Container | Memory (req/limit) | CPU (req/limit) | 역할 |
|---|---|---|---|
| app | 256Mi / 512Mi | 250m / 500m | 비즈니스 로직 |
| selfhealing-sidecar | 64Mi / 128Mi | 50m / 200m | Python 엔진 (CB, DLQ, Cell) |
| envoy-sidecar | 32Mi / 64Mi | 25m / 100m | 네트워크 프록시 (Ring Hash LB) |
| **합계** | **352Mi / 704Mi** | **325m / 800m** | — |

> Envoy sidecar의 추가 비용은 32-64Mi / 25-100m으로 매우 경량이다.
> `k8s/sidecar-injection.yaml`의 기존 selfhealing-sidecar (64-128Mi)보다 작다.
