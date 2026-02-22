"""
Cell Registry — Consistent Hash 기반 Cell 할당.

서비스/테넌트를 Cell에 할당하고, Cell별 Bulkhead를 자동 관리합니다.

의존성:
- BulkheadRegistry: Cell별 격벽 생성 (bulkhead_isolation_enabled=True일 때)
- CellTopologySettings: 설정 주입
"""

from __future__ import annotations

import hashlib
import structlog
import threading
from typing import Any

from selfhealing.services.cell_topology.models import (
    CELL_STATE_PRIORITY,
    CellInfo,
    CellState,
)
from selfhealing.settings.cell_topology import CellTopologySettings

logger = structlog.get_logger()

VNODES_PER_CELL = 150
"""Consistent Hash Ring의 Cell당 가상 노드 수."""


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

        각 Cell에 대해 가상 노드(vnode)를 생성하여
        균일 분배를 보장합니다.
        Copy-on-Write 방식으로 새 리스트를 구성 후
        원자적 참조 교체(GIL-safe)합니다.
        """
        ring: list[tuple[int, str]] = []

        for cell_id in self._cells:
            for vnode_idx in range(VNODES_PER_CELL):
                key = f"{cell_id}:vnode-{vnode_idx}"
                hash_val = self._hash(key)
                ring.append((hash_val, cell_id))

        ring.sort(key=lambda x: x[0])
        # Atomic reference swap (GIL-safe)
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
        return [cell_id for cell_id, info in self._cells.items() if info.state == CellState.ACTIVE]

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
                logger.warning(
                    "cell_registry.cell_not_found",
                    cell_id=cell_id,
                )
                return False

            old_state = cell.state
            cell.state = state
            cell.metadata["last_state_change"] = {
                "from": old_state.value,
                "to": state.value,
                "reason": reason,
            }

            logger.info(
                "cell_registry.state_changed",
                cell_id=cell_id,
                old_state=old_state.value,
                new_state=state.value,
                reason=reason,
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

    def _record_service_heartbeat(self, cell_id: str, service_name: str) -> None:
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
            if redis is None:
                return
            key = f"selfhealing:cell:{cell_id}:services"
            redis.zadd(key, {service_name: time.time()})
        except Exception as e:
            logger.debug(
                "cell_registry.heartbeat_failed",
                error=e,
            )

    def _evict_expired_services(self, cell_id: str, ttl_seconds: float = 300.0) -> list[str]:
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
            if redis is None:
                return evicted
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
                    "cell_registry.services_evicted",
                    count=len(evicted),
                    cell_id=cell_id,
                    evicted=evicted,
                )
        except Exception as e:
            logger.debug(
                "service_eviction_failed",
                cell_id=cell_id,
                error=e,
            )

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
                f"(max_concurrent="
                f"{self._settings.bulkhead_max_concurrent_per_cell})"
            )
        except ImportError:
            logger.warning("cell_registry.bulkhead_registry_unavailable")
        except Exception as e:
            logger.error(
                "cell_bulkhead_registration_failed",
                error=e,
            )

    # ── L1/L2 동기화 ────────────────────────────────────────

    def _sync_state_to_redis(self, cell_id: str) -> None:
        """
        Cell 상태를 L2(Redis Hash)에 기록.

        set_cell_state() 호출 후 자동 실행.
        Redis Pub/Sub로 다른 워커에 즉시 전파.
        """
        try:
            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            if redis is None:
                return
            cell = self._cells.get(cell_id)
            if not cell:
                return

            key = f"selfhealing:cell:state:{cell_id}"
            redis.hset(
                key,
                mapping={
                    "state": cell.state.value,
                    "health_score": str(cell.health_score),
                    "warmup_percentage": str(cell.warmup_percentage),
                },
            )

            # Pub/Sub 즉시 전파
            redis.publish(
                "selfhealing:cell:state_changed",
                f"{cell_id}:{cell.state.value}",
            )
        except Exception as e:
            logger.warning(
                "state_sync_failed",
                cell_id=cell_id,
                error=e,
            )

    def _load_all_states_from_redis(self) -> int:
        """
        L2(Redis)에서 모든 Cell 상태를 L1에 로드 (Anti-entropy Reconciliation).

        워커 시작 시 1회 + 주기적 폴링 (10~30초)으로 호출.
        Pub/Sub 이벤트 누락 시 보정 역할.

        "Most Restrictive Wins" 전략:
        L1과 L2가 불일치할 때 더 제한적인 상태가 항상 승리.

        Returns:
            동기화된 Cell 수
        """
        synced = 0
        try:
            from selfhealing.adapters.redis import get_redis_client

            redis = get_redis_client()
            if redis is None:
                return synced
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
                    # Most Restrictive Wins
                    if CELL_STATE_PRIORITY.get(new_state, 0) >= CELL_STATE_PRIORITY.get(cell.state, 0):
                        cell.state = new_state

                health_str = data.get("health_score") or data.get(b"health_score")
                if health_str:
                    if isinstance(health_str, bytes):
                        health_str = health_str.decode()
                    cell.health_score = max(0.0, min(1.0, float(health_str)))

                warmup_str = data.get("warmup_percentage") or data.get(b"warmup_percentage")
                if warmup_str:
                    if isinstance(warmup_str, bytes):
                        warmup_str = warmup_str.decode()
                    cell.warmup_percentage = max(0.0, min(100.0, float(warmup_str)))

                synced += 1
        except Exception as e:
            logger.warning(
                "state_load_failed",
                error=e,
            )

        return synced

    def _subscribe_state_changes(self) -> None:
        """
        EventBus로 Cell 상태 변경 이벤트 구독.

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
            logger.info("cell_registry.subscribed_cell_state_change")
        except Exception as e:
            logger.warning(
                "cell_registry.event_subscription_failed",
                error=e,
            )

    def _on_cell_state_event(self, event: Any) -> None:
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

    # ── 동적 스케일링 ──────────────────────────────────────

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
                "added_cells_warmup_total",
                count=count,
                added=added,
                count_2=len(self._cells),
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


# =============================================================================
# Singleton
# =============================================================================

_registry: CellRegistry | None = None
_registry_lock = threading.Lock()


def get_cell_registry() -> CellRegistry:
    """CellRegistry 싱글톤 반환."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = CellRegistry()
    return _registry


def reset_cell_registry() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _registry
    with _registry_lock:
        _registry = None
