# 265. Cell Evacuation Policy — Tick-Based State Machine + Hysteresis

> **Version**: 2.1.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Done
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/policy.py`

---

## 0. 요약

`CellEvacuationPolicy`는 Cell 건강도가 임계치 이하로 떨어졌을 때, **트래픽 드레인 → Cell 격리 → 서비스 재배치**를 수행한다.

**v2.0 핵심 변경** (아키텍처 리뷰 반영):

| # | 변경 | 근거 |
|---|------|------|
| R1 | **히스테리시스** — 연속 3회 이하/5회 이상 카운터 도입 | 264 §5.2 책임 분리(측정/판단) 스펙, 상태 전이 시 양방향 카운터 리셋 |
| R2 | **Tick-Based State Machine** — `threading.Thread` + `time.sleep()` 제거 | 리더 크래시 시 DRAINING 영구 고착 방지, `metadata['last_state_change']` 활용 |
| R3 | **`bulkhead.max_concurrent = 0` 제거** | `get_cell_for_key()` Hash Ring이 DRAINING Cell을 전역 skip, 로컬 Bulkhead 조작은 불완전 |
| R4 | **`max_evacuated_ratio=0.25`** Global Hard Limit | Cascading Failure 방지, 8 Cell 기준 최대 2개만 격리 |
| R5 | **CellRegistry = SoT**, Gate/Blast는 Fire-and-forget 통보 | `isolate_region()`/`set_policy()` 호출은 Celery `apply_async` 비동기 위임 |

토글: `CellTopologySettings.evacuation_enabled=True`일 때만 동작.

---

## 1. 대피 흐름 — Tick-Based State Machine

v1의 `threading.Thread` + `time.sleep(30)` 파이프라인을 **tick 기반 상태 머신**으로 교체한다.
`evaluate()`는 LeaderScheduler의 `aggregate_all()` 루프에서 매 tick(`health_check_interval_seconds=10`)마다 호출되며,
각 Cell의 **현재 상태(CellState)**와 **metadata 시간값**을 기반으로 다음 전이를 결정한다.

```
LeaderScheduler @scheduler.job(interval=10s)
  → CellHealthAggregator.aggregate_all()
    → for each cell: compute_health() → update_health_score()
    → CellEvacuationPolicy.evaluate(cell_id, score)
        ┌─ ACTIVE 상태:
        │   health ≤ 0.3 → below_count++ (metadata 영속화)
        │   below_count ≥ 3 (연속) → DRAINING 전환
        │   health > 0.3 → below_count = 0 (리셋)
        │
        ├─ DRAINING 상태:
        │   elapsed = now - metadata['last_state_change'] 시각
        │   elapsed ≥ drain_seconds + grace_buffer(2s)
        │     → ISOLATED 전환 (SoT: CellRegistry)
        │     → Fire-and-forget 통보 (Celery: Gate/Blast)
        │
        ├─ ISOLATED 상태:
        │   health ≥ 0.7 → above_count++ (metadata 영속화)
        │   above_count ≥ 5 (연속) → ACTIVE 복구
        │   health < 0.7 → above_count = 0 (리셋)
        │
        └─ WARMUP 상태: 대피 평가 대상 아님 (skip)
```

**v1 대비 핵심 차이점**:
- 스레드 없음 — `evaluate()` 자체가 멱등성(Idempotent) 상태 전이 함수
- 리더 크래시 복원 — 새 리더가 Redis L2의 `CellState` + `metadata['last_state_change']`를 읽고 파이프라인 재개
- `_active_evacuations` 인메모리 딕셔너리 불필요 — `CellState`가 SSOT

---

## 2. 기존 연동점 — 코드 근거

### 2.1 `CellRegistry.get_cell_for_key()` — 라우팅 차단 (핵심 메커니즘)

**파일**: `services/cell_topology/registry.py` L137-150

```python
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

# DRAINING/ISOLATED Cell은 어떤 조건에도 해당하지 않아 자동 skip
```

**DRAINING/ISOLATED Cell은 `ACTIVE`도 `WARMUP`도 아니므로 Ring 순회 시 자동으로 건너뛴다.**
이것이 신규 트래픽 차단의 **유일하고 완전한 메커니즘**이다.

L1(인메모리) + Pub/Sub 즉시 전파(`_sync_state_to_redis` → `selfhealing:cell:state_changed`)로
모든 워커가 상태 변경을 수신하므로, **클러스터 전역에서 동작**한다.

### 2.2 `CellRegistry.set_cell_state()` — 상태 전이 + 시간 기록

**파일**: `services/cell_topology/registry.py` L169-196

```python
def set_cell_state(self, cell_id: str, state: CellState, reason: str = "") -> bool:
    with self._lock:
        cell = self._cells.get(cell_id)
        if not cell:
            return False

        old_state = cell.state
        cell.state = state
        cell.metadata["last_state_change"] = {
            "from": old_state.value,
            "to": state.value,
            "reason": reason,
        }
        return True
```

`metadata['last_state_change']`는 L2(Redis Hash)로 동기화되므로, 리더 교체 후에도 드레인 시간 경과를 판단할 수 있다.
State Machine의 시간 기반 전이(DRAINING → ISOLATED)가 이 값에 의존한다.

### 2.3 `RegionalIsolationGate.isolate_region()` — 감사 로그/이벤트 통보용

**파일**: `services/isolation/regional_gate.py` L147

```python
def isolate_region(
    self,
    region: str,           # ← cell_id 전달 (예: "cell-3")
    reason: str,
    duration_seconds: int = 300,
) -> bool:
```

**역할 변경 (v2)**: 라우팅 차단의 제어 주체가 아닌, **감사 로그(`log_region_isolation_audit()`)와 전역 이벤트 발행 목적의 단방향 통보로만 사용**.
호출 실패가 대피 파이프라인을 중단시키지 않는다 (Fire-and-forget).

### 2.4 `RegionalIsolationGate.restore_region()` — 감사 로그/이벤트 통보용

**파일**: `services/isolation/regional_gate.py` L275

```python
def restore_region(self, region: str) -> bool:
```

복구 시 `restore_region("cell-3")` 호출. 마찬가지로 Fire-and-forget 통보.

### 2.5 `BlastRadiusService.set_policy()` — 감사 로그 통보용

**파일**: `services/blast_radius/service.py` L65

```python
def set_policy(
    self,
    stage_name: str,           # ← cell_id
    level: BlastRadiusLevel,
    affected_services: list[str] | None = None,
    max_affected_percentage: float = 10.0,
    auto_isolate: bool = True,
) -> BlastRadiusPolicy:
```

**역할 변경 (v2)**: CellRegistry 상태 전이 완료 후, 감사 로그(`log_blast_radius_audit()`, service.py L95) 기록 목적으로만 호출.

### 2.6 `bulkhead.max_concurrent = 0` — v2에서 제거됨

**근거**: `BulkheadRegistry._bulkheads`는 워커의 **로컬 인메모리** `dict[str, Bulkhead]`이다 (`resilience/bulkhead/registry.py`).
리더 워커 1대에서 `max_concurrent = 0`을 설정해도 나머지 N-1대 워커의 Bulkhead에는 영향이 없다.

§2.1에서 증명했듯이 `get_cell_for_key()`의 Hash Ring이 DRAINING Cell을 **모든 워커에서 전역 skip**하므로,
로컬 Bulkhead 조작은 불완전하고 상태 불일치를 유발하는 중복 메커니즘이다. 따라서 v2에서 완전히 제거한다.

---

## 3. CellEvacuationPolicy 구현

```python
"""
Cell Evacuation Policy — Tick-Based State Machine + Hysteresis.

LeaderScheduler의 aggregate_all() 루프에서 매 tick마다 호출되는
멱등성 상태 전이 함수입니다.

아키텍처 결정:
- R1: 히스테리시스 — 연속 카운터(CellInfo.metadata) + 상태 전이 시 양방향 리셋
- R2: Tick-Based State Machine — threading.Thread/time.sleep() 제거
- R3: bulkhead.max_concurrent = 0 제거 — Hash Ring 라우팅이 전역 차단 담당
- R4: max_evacuated_ratio — Cascading Failure 방지 하드 리미트
- R5: CellRegistry = SoT — Gate/Blast는 Celery Fire-and-forget 통보

의존성:
- CellRegistry: Cell 상태 관리 (SoT, Control Plane)
- RegionalIsolationGate: 감사 로그/이벤트 발행 (통보, Fire-and-forget)
- BlastRadiusService: 감사 로그 (통보, Fire-and-forget)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.cell_topology.models import CellInfo
    from selfhealing.services.cell_topology.registry import CellRegistry
    from selfhealing.settings.cell_topology import CellTopologySettings

logger = logging.getLogger(__name__)


@dataclass
class EvacuationRecord:
    """대피 기록."""

    cell_id: str
    trigger_health_score: float
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    reason: str = ""
    affected_services: list[str] = field(default_factory=list)


class CellEvacuationPolicy:
    """
    Cell 대피 정책 — Tick-Based State Machine.

    evaluate()는 LeaderScheduler의 aggregate_all() 루프에서
    매 tick(health_check_interval_seconds=10초)마다 호출됩니다.

    각 호출에서 Cell의 현재 상태(CellState)와 metadata를 읽고,
    다음 상태 전이를 결정합니다. 스레드를 생성하지 않으며,
    모든 상태는 CellInfo.metadata에 영속화되므로 리더 교체에 안전합니다.

    토글:
    - CellTopologySettings.evacuation_enabled=True일 때만 동작
    - False이면 evaluate() 즉시 반환

    사용:
        # LeaderScheduler의 aggregate_all() 내부에서 호출
        policy = get_cell_evacuation_policy()
        for cell_id, info in registry.get_all_cells().items():
            policy.evaluate(cell_id, info.health_score)
    """

    def __init__(self, settings: CellTopologySettings | None = None):
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._evacuation_history: deque[EvacuationRecord] = deque(
            maxlen=self._settings.evacuation_history_max_size,
        )

    # =========================================================================
    # evaluate() — 상태 머신 Tick
    # =========================================================================

    def evaluate(self, cell_id: str, health_score: float) -> bool:
        """
        상태 머신 Tick — 매 호출마다 Cell의 다음 전이를 결정.

        멱등성: 같은 상태에서 같은 입력이면 같은 결과.
        리더 교체 안전: CellInfo.metadata에 모든 카운터/시간 영속화.

        Args:
            cell_id: Cell 식별자
            health_score: 현재 건강도 (0.0~1.0)

        Returns:
            상태 전이 발생 여부
        """
        if not self._settings.enabled or not self._settings.evacuation_enabled:
            return False

        from selfhealing.services.cell_topology import get_cell_registry
        from selfhealing.services.cell_topology.models import CellState

        registry = get_cell_registry()
        cell = registry.get_cell_info(cell_id)
        if not cell:
            return False

        # WARMUP Cell은 대피 평가 대상 아님
        if cell.state == CellState.WARMUP:
            return False

        if cell.state == CellState.ACTIVE:
            return self._tick_active(cell_id, health_score, cell, registry)

        if cell.state == CellState.DRAINING:
            return self._tick_draining(cell_id, cell, registry)

        if cell.state == CellState.ISOLATED:
            return self._tick_isolated(cell_id, health_score, cell, registry)

        return False

    # =========================================================================
    # State: ACTIVE — 히스테리시스 기반 대피 판단
    # =========================================================================

    def _tick_active(
        self,
        cell_id: str,
        health_score: float,
        cell: Any,
        registry: Any,
    ) -> bool:
        """
        ACTIVE 상태 Cell의 대피 필요 여부 평가.

        히스테리시스: 연속 evacuation_consecutive_count(기본 3)회
        임계치 이하일 때만 DRAINING으로 전환.
        임계치를 초과하면 below_count를 즉시 0으로 리셋.

        264 문서 설계 원칙:
        - 264는 온도계(측정): EWMA 스무딩된 health_score 제공
        - 265는 자동온도조절기(판단): 히스테리시스로 플래핑 방지
        """
        from selfhealing.services.cell_topology.models import CellState

        threshold = self._settings.evacuation_health_threshold

        if health_score <= threshold:
            # 임계치 이하 — 카운터 증가
            below_count = cell.metadata.get("evacuation_below_count", 0) + 1
            cell.metadata["evacuation_below_count"] = below_count

            if below_count < self._settings.evacuation_consecutive_count:
                logger.debug(
                    f"Cell {cell_id} health={health_score:.2f} ≤ {threshold}, "
                    f"below_count={below_count}/"
                    f"{self._settings.evacuation_consecutive_count}"
                )
                return False

            # === Global Evacuation Limit — Cascading Failure 방지 ===
            total_cells = len(registry.get_all_cells())
            active_cells = len(registry.get_active_cells())
            evacuated_ratio = 1.0 - (active_cells / total_cells) if total_cells > 0 else 0.0

            if evacuated_ratio >= self._settings.max_evacuated_ratio:
                logger.critical(
                    f"[SEV-1] Global evacuation limit reached: "
                    f"{evacuated_ratio:.0%} evacuated "
                    f"(max {self._settings.max_evacuated_ratio:.0%}). "
                    f"Refusing to evacuate {cell_id}. "
                    f"Active={active_cells}/{total_cells}"
                )
                # TODO: UnifiedNotificationManager 또는 SecurityNotificationService를
                # 통해 CRITICAL 등급 알림 발행 (PagerDuty/Slack 연동).
                # 기존 패턴 참조: services/security_notification/ (CRITICAL → PagerDuty)
                # 메트릭: cell_evacuation_global_limit_hit_total.inc()
                return False

            # === 연속 카운터 도달 — DRAINING 전환 ===
            reason = (
                f"Health score {health_score:.2f} ≤ {threshold} "
                f"for {below_count} consecutive ticks"
            )
            logger.warning(
                f"Cell {cell_id}: {reason}, transitioning ACTIVE → DRAINING"
            )

            # 상태 전이 시 양방향 카운터 리셋 (유령 카운터 방지)
            cell.metadata["evacuation_below_count"] = 0
            cell.metadata["recovery_above_count"] = 0

            # 영향 서비스 목록 기록
            cell.metadata["evacuation_affected_services"] = list(
                cell.assigned_services
            )
            cell.metadata["evacuation_trigger_score"] = health_score

            # === SoT: CellRegistry 상태 전환 ===
            registry.set_cell_state(cell_id, CellState.DRAINING, reason)

            # DRAINING 전환 시점을 즉시 기록 — _tick_draining()이
            # 첫 tick에서 시간을 기록하는 지연 없이 바로 드레인 타이머 시작
            cell.metadata["last_state_change_time"] = time.time()

            # 신규 트래픽 차단은 CellRegistry.get_cell_for_key()의
            # Hash Ring 순회에서 DRAINING Cell이 자동 skip됨으로써 달성된다.
            # (registry.py L137-150: ACTIVE/WARMUP만 반환, DRAINING/ISOLATED는 implicit skip)
            # 이 차단은 L1 Pub/Sub 전파로 모든 워커에서 즉시 동작한다.
            logger.info(
                f"Cell {cell_id}: DRAINING 전환 완료 — "
                f"신규 트래픽은 Hash Ring 라우팅 레벨에서 차단됨 "
                f"(registry.py get_cell_for_key, L1 Pub/Sub 전파)"
            )

            # 대피 이력 기록
            self._evacuation_history.append(
                EvacuationRecord(
                    cell_id=cell_id,
                    trigger_health_score=health_score,
                    reason=reason,
                    affected_services=list(cell.assigned_services),
                )
            )
            return True
        else:
            # 임계치 초과 — 카운터 리셋
            if cell.metadata.get("evacuation_below_count", 0) > 0:
                cell.metadata["evacuation_below_count"] = 0
            return False

    # =========================================================================
    # State: DRAINING — 드레인 시간 경과 기반 ISOLATED 전환
    # =========================================================================

    def _tick_draining(
        self,
        cell_id: str,
        cell: Any,
        registry: Any,
    ) -> bool:
        """
        DRAINING 상태 Cell의 드레인 시간 경과 확인.

        metadata['last_state_change']의 시간값과 현재 시각을 비교하여,
        drain_seconds + grace_buffer(NTP drift 허용)가 경과했으면
        ISOLATED로 전환한다.

        리더 교체 시에도 Redis L2에 동기화된 metadata를 읽어
        파이프라인을 안전하게 재개할 수 있다.
        """
        from selfhealing.services.cell_topology.models import CellState

        last_change = cell.metadata.get("last_state_change", {})
        if last_change.get("to") != CellState.DRAINING.value:
            # metadata가 없거나 불일치 — 보수적으로 skip
            logger.warning(
                f"Cell {cell_id}: DRAINING but metadata mismatch, "
                f"waiting for next tick"
            )
            return False

        # 시간 경과 판단 — time.time() + Grace Buffer
        # Grace Buffer: 워커 간 NTP drift 허용 (기본 2초)
        # 프로젝트 전체가 time.time()/time.monotonic() 통일이므로
        # redis.time() 대신 Grace Buffer 방식 채택
        drain_started = cell.metadata.get("last_state_change_time")
        if drain_started is None:
            # 시간 기록이 없으면 현재 시각 기록 후 다음 tick 대기
            cell.metadata["last_state_change_time"] = time.time()
            return False

        elapsed = time.time() - drain_started
        required = (
            self._settings.evacuation_traffic_drain_seconds
            + self._settings.evacuation_drain_grace_seconds
        )

        if elapsed < required:
            logger.debug(
                f"Cell {cell_id}: DRAINING elapsed={elapsed:.1f}s "
                f"< required={required:.1f}s, waiting"
            )
            return False

        # === 드레인 완료 — ISOLATED 전환 ===
        reason = (
            f"Drain period elapsed ({elapsed:.1f}s ≥ {required:.1f}s)"
        )
        logger.info(
            f"Cell {cell_id}: drain complete, transitioning "
            f"DRAINING → ISOLATED"
        )

        # 양방향 카운터 리셋
        cell.metadata["evacuation_below_count"] = 0
        cell.metadata["recovery_above_count"] = 0

        # === SoT: CellRegistry 상태 전환 (가장 먼저 실행) ===
        registry.set_cell_state(cell_id, CellState.ISOLATED, reason)

        # === Fire-and-forget: 감사 로그 및 이벤트 발행 ===
        # 통보 실패가 대피 파이프라인에 영향 없음.
        # Celery apply_async 패턴 (services/throttle/sla_notification.py 참조)
        self._notify_isolation_gate(
            cell_id,
            reason,
            duration_seconds=self._settings.isolation_notification_duration_seconds,
        )
        self._notify_blast_radius(
            cell_id,
            cell.metadata.get("evacuation_affected_services", []),
        )

        # 서비스 재배치 로깅
        affected = cell.metadata.get("evacuation_affected_services", [])
        logger.info(
            f"Cell {cell_id}: ISOLATED — "
            f"{len(affected)} services redistributed via "
            f"Consistent Hash Ring (get_cell_for_key auto-skip)"
        )
        for svc in affected:
            new_cell = registry.get_cell_for_key(svc)
            logger.info(f"  Service '{svc}': {cell_id} → {new_cell}")

        return True

    # =========================================================================
    # State: ISOLATED — 히스테리시스 기반 자동 복구
    # =========================================================================

    def _tick_isolated(
        self,
        cell_id: str,
        health_score: float,
        cell: Any,
        registry: Any,
    ) -> bool:
        """
        ISOLATED 상태 Cell의 자동 복구 판단.

        히스테리시스: 연속 recovery_consecutive_count(기본 5)회
        recovery_health_threshold(기본 0.7) 이상일 때만 ACTIVE로 복구.
        비대칭 설계: 대피(3회)는 빠르게, 복구(5회)는 보수적으로.
        """
        from selfhealing.services.cell_topology.models import CellState

        recovery_threshold = self._settings.recovery_health_threshold

        if health_score >= recovery_threshold:
            above_count = cell.metadata.get("recovery_above_count", 0) + 1
            cell.metadata["recovery_above_count"] = above_count

            if above_count < self._settings.recovery_consecutive_count:
                logger.debug(
                    f"Cell {cell_id} health={health_score:.2f} ≥ "
                    f"{recovery_threshold}, above_count={above_count}/"
                    f"{self._settings.recovery_consecutive_count}"
                )
                return False

            # === 연속 카운터 도달 — ACTIVE 복구 ===
            reason = (
                f"Health score {health_score:.2f} ≥ {recovery_threshold} "
                f"for {above_count} consecutive ticks"
            )
            logger.info(
                f"Cell {cell_id}: {reason}, restoring ISOLATED → ACTIVE"
            )

            # 상태 전이 시 양방향 카운터 리셋
            cell.metadata["evacuation_below_count"] = 0
            cell.metadata["recovery_above_count"] = 0

            # === SoT: CellRegistry 상태 전환 ===
            registry.set_cell_state(cell_id, CellState.ACTIVE, reason)

            # === Fire-and-forget: 감사 로그 통보 ===
            self._notify_restore_region(cell_id)

            # 대피 이력 완료 기록
            for record in reversed(self._evacuation_history):
                if record.cell_id == cell_id and record.completed_at is None:
                    record.completed_at = datetime.now(timezone.utc)
                    break

            logger.info(f"Cell {cell_id}: restored to ACTIVE")
            return True
        else:
            # 임계치 미달 — 카운터 리셋
            if cell.metadata.get("recovery_above_count", 0) > 0:
                cell.metadata["recovery_above_count"] = 0
            return False

    # =========================================================================
    # 수동 복구
    # =========================================================================

    def restore_cell(self, cell_id: str) -> bool:
        """
        Cell 수동 복구.

        자동 복구 히스테리시스를 무시하고 즉시 ACTIVE로 전환합니다.
        관리자 개입 시 사용.

        Args:
            cell_id: Cell 식별자

        Returns:
            복원 성공 여부
        """
        if not self._settings.enabled:
            return False

        try:
            from selfhealing.services.cell_topology import get_cell_registry
            from selfhealing.services.cell_topology.models import CellState

            registry = get_cell_registry()
            cell = registry.get_cell_info(cell_id)
            if not cell:
                return False

            # 양방향 카운터 리셋
            cell.metadata["evacuation_below_count"] = 0
            cell.metadata["recovery_above_count"] = 0

            # === SoT: CellRegistry 상태 전환 ===
            registry.set_cell_state(
                cell_id, CellState.ACTIVE, "Manual restoration"
            )

            # === Fire-and-forget: 감사 로그 통보 ===
            self._notify_restore_region(cell_id)

            logger.info(f"Cell {cell_id}: manually restored to ACTIVE")
            return True

        except Exception as e:
            logger.error(f"Cell {cell_id}: manual restore failed: {e}")
            return False

    # =========================================================================
    # Fire-and-forget 통보 — Celery apply_async
    # =========================================================================
    # 패턴 참조: services/throttle/sla_notification.py
    # Celery 미사용 환경에서는 동기 폴백 (try/except 보호)

    def _notify_isolation_gate(
        self,
        cell_id: str,
        reason: str,
        *,
        duration_seconds: int = 3600,
    ) -> None:
        """RegionalIsolationGate 격리 통보 (Fire-and-forget)."""
        try:
            from selfhealing.adapters.celery.tasks import (
                notify_cell_isolation,
            )

            notify_cell_isolation.apply_async(
                kwargs={
                    "cell_id": cell_id,
                    "reason": reason,
                    "duration_seconds": duration_seconds,
                },
            )
        except ImportError:
            # Celery 미사용: 동기 폴백
            self._notify_isolation_gate_sync(
                cell_id, reason, duration_seconds=duration_seconds,
            )
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: isolation gate async notify failed: {e}"
            )
            self._notify_isolation_gate_sync(
                cell_id, reason, duration_seconds=duration_seconds,
            )

    def _notify_isolation_gate_sync(
        self,
        cell_id: str,
        reason: str,
        *,
        duration_seconds: int = 3600,
    ) -> None:
        """RegionalIsolationGate 동기 폴백."""
        try:
            from selfhealing.services.isolation.regional_gate import (
                get_regional_isolation_gate,
            )

            gate = get_regional_isolation_gate()
            gate.isolate_region(
                region=cell_id,
                reason=reason,
                duration_seconds=duration_seconds,
            )
        except ImportError:
            logger.debug("RegionalIsolationGate not available")
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: isolation gate sync notify failed: {e}"
            )

    def _notify_blast_radius(
        self, cell_id: str, affected_services: list[str]
    ) -> None:
        """BlastRadiusService 정책 설정 통보 (Fire-and-forget)."""
        try:
            from selfhealing.adapters.celery.tasks import (
                notify_cell_blast_radius,
            )

            notify_cell_blast_radius.apply_async(
                kwargs={
                    "cell_id": cell_id,
                    "affected_services": affected_services,
                },
            )
        except ImportError:
            self._notify_blast_radius_sync(cell_id, affected_services)
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: blast radius async notify failed: {e}"
            )
            self._notify_blast_radius_sync(cell_id, affected_services)

    def _notify_blast_radius_sync(
        self, cell_id: str, affected_services: list[str]
    ) -> None:
        """BlastRadiusService 동기 폴백."""
        try:
            from selfhealing.services.blast_radius.models import (
                BlastRadiusLevel,
            )
            from selfhealing.services.blast_radius.service import (
                BlastRadiusService,
            )

            blast_service = BlastRadiusService()
            blast_service.set_policy(
                stage_name=cell_id,
                level=BlastRadiusLevel.CRITICAL,
                affected_services=affected_services,
                max_affected_percentage=0.0,
                auto_isolate=True,
            )
        except ImportError:
            pass
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: blast radius sync notify failed: {e}"
            )

    def _notify_restore_region(self, cell_id: str) -> None:
        """RegionalIsolationGate 복구 통보 (Fire-and-forget)."""
        try:
            from selfhealing.adapters.celery.tasks import (
                notify_cell_restoration,
            )

            notify_cell_restoration.apply_async(
                kwargs={"cell_id": cell_id},
            )
        except ImportError:
            self._notify_restore_region_sync(cell_id)
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: restore region async notify failed: {e}"
            )
            self._notify_restore_region_sync(cell_id)

    def _notify_restore_region_sync(self, cell_id: str) -> None:
        """RegionalIsolationGate 복구 동기 폴백."""
        try:
            from selfhealing.services.isolation.regional_gate import (
                get_regional_isolation_gate,
            )

            gate = get_regional_isolation_gate()
            gate.restore_region(cell_id)
        except ImportError:
            logger.debug("RegionalIsolationGate not available")
        except Exception as e:
            logger.warning(
                f"Cell {cell_id}: restore region sync notify failed: {e}"
            )

    # =========================================================================
    # 조회
    # =========================================================================

    def get_evacuation_history(self) -> list[EvacuationRecord]:
        """대피 이력."""
        return list(self._evacuation_history)


# =============================================================================
# Singleton
# =============================================================================

import threading

_policy: CellEvacuationPolicy | None = None
_policy_lock = threading.Lock()


def get_cell_evacuation_policy() -> CellEvacuationPolicy:
    """CellEvacuationPolicy 싱글톤 반환."""
    global _policy
    if _policy is None:
        with _policy_lock:
            if _policy is None:
                _policy = CellEvacuationPolicy()
    return _policy


def reset_cell_evacuation_policy() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _policy
    with _policy_lock:
        _policy = None
```

---

## 4. 대피 상태 전이 다이어그램

```
                  ┌─ below_count++ ─┐
                  │                 │ (health ≤ 0.3, 연속 < 3회)
                  ▼                 │
ACTIVE ──[below_count ≥ 3]──→ DRAINING ──[elapsed ≥ 32s]──→ ISOLATED
  ↑                                                            │  ▲
  │                  ┌── above_count++ ──┐                     │  │
  │                  │                   │ (health ≥ 0.7,      │  │
  │                  │                   │  연속 < 5회)         │  │
  │                  ▼                   │                     │  │
  ├─[above_count ≥ 5]───────────────────────────────────────────┘  │
  │  (자동 복구)                                                    │
  └─[restore_cell()]────────────────────────────────────────────────┘
     (수동 복구)
```

### 4.1 상태별 동작 요약

| 상태 | 트래픽 차단 메커니즘 | RegionalIsolationGate | Hash Ring | 히스테리시스 |
|------|---------------------|----------------------|-----------|------------|
| ACTIVE | 없음 | 격리 없음 | 포함 | `below_count` 추적 |
| DRAINING | `get_cell_for_key()` Hash Ring auto-skip | 격리 없음 | **skip** (신규 차단) | 시간 경과 대기 |
| ISOLATED | `get_cell_for_key()` Hash Ring auto-skip | 감사 로그 기록됨 | **skip** (완전 격리) | `above_count` 추적 |

### 4.2 히스테리시스 파라미터 (264 §5.2 스펙 구현)

| 파라미터 | 설정 키 | 기본값 | 설명 |
|----------|---------|--------|------|
| 대피 임계치 | `evacuation_health_threshold` | `0.3` | 건강도 이하 시 below_count++ |
| 복구 임계치 | `recovery_health_threshold` | `0.7` | 건강도 이상 시 above_count++ |
| 대피 연속 횟수 | `evacuation_consecutive_count` | `3` | 연속 3회 → DRAINING |
| 복구 연속 횟수 | `recovery_consecutive_count` | `5` | 연속 5회 → ACTIVE |
| 데드존 | (0.3, 0.7) | — | 상태 전환 없음, 양쪽 카운터 리셋 |
| 비대칭 | 대피 3회 / 복구 5회 | — | 대피는 빠르게, 복구는 보수적 |

### 4.3 카운터 영속화 — `CellInfo.metadata`

```python
# 리더 교체에 안전한 카운터 영속화
cell.metadata["evacuation_below_count"] = 3   # Redis L2 동기화됨
cell.metadata["recovery_above_count"] = 5     # Redis L2 동기화됨
```

**양방향 리셋 규칙**: 상태 전이가 발생하는 순간, **양쪽 카운터를 모두 0으로 초기화**한다.
이를 통해 유령 카운터(Phantom Counter)에 의한 의도치 않은 재대피/재복구를 방지한다.

```python
# 모든 상태 전이 시점 (ACTIVE→DRAINING, DRAINING→ISOLATED, ISOLATED→ACTIVE)
cell.metadata["evacuation_below_count"] = 0
cell.metadata["recovery_above_count"] = 0
```

---

## 5. 안전장치

### 5.1 Global Evacuation Limit — Cascading Failure 방지

**설정**: `max_evacuated_ratio=0.25` (기본값)

8 Cell 기준 최대 2개 Cell만 격리 허용. 잔존 6개 Cell이 항상 트래픽을 처리한다.

```python
# evaluate() → _tick_active() 내부
total_cells = len(registry.get_all_cells())
active_cells = len(registry.get_active_cells())
evacuated_ratio = 1.0 - (active_cells / total_cells)

if evacuated_ratio >= self._settings.max_evacuated_ratio:
    logger.critical(
        f"[SEV-1] Global evacuation limit reached: "
        f"{evacuated_ratio:.0%} (max {self._settings.max_evacuated_ratio:.0%}). "
        f"Refusing to evacuate {cell_id}"
    )
    # TODO: UnifiedNotificationManager 또는 SecurityNotificationService를
    # 통해 CRITICAL 등급 알림 발행 (PagerDuty/Slack 연동).
    # 기존 패턴 참조: services/security_notification/ (CRITICAL → PagerDuty)
    # 메트릭: cell_evacuation_global_limit_hit_total.inc()
    return False
```

**설계 철학**: 전체 클러스터가 위험할 때는 **개별 Cell의 장애를 감수하며 버틴다** (Google SRE "에러 예산 소진 시 변경 동결" 패턴).

### 5.2 NTP Drift Grace Buffer

**설정**: `evacuation_drain_grace_seconds=2.0` (기본값)

DRAINING → ISOLATED 전이 시 `drain_seconds + grace_buffer`를 요구하여,
워커 간 시계 오차에 의한 성급한 전이를 방지한다.

```python
required = (
    self._settings.evacuation_traffic_drain_seconds     # 30
    + self._settings.evacuation_drain_grace_seconds      # 2
)  # = 32초
```

**선택 근거**: 프로젝트 전체에서 `redis.time()`을 사용하는 곳이 없고, 모든 시간 기록이
`time.time()`/`time.monotonic()`으로 통일되어 있다 (health.py `_leader_since`, registry.py `zadd`).
새로운 시간 소스를 도입하면 일관성이 깨지므로, Grace Buffer 방식을 채택했다.
향후 멀티리전 대피 지원 시 논리적 시계(Lamport Clock) 도입을 재검토한다.

### 5.3 수동 복구 경로

```python
# 관리자가 히스테리시스를 무시하고 즉시 Cell 복구
policy = get_cell_evacuation_policy()
policy.restore_cell("cell-3")
```

### 5.4 리더 크래시 복원력

Tick-Based State Machine 설계에 의해, 리더가 교체되어도:

1. 새 리더가 Redis L2에서 `CellState.DRAINING` + `metadata['last_state_change_time']`을 로드
2. `_tick_draining()`이 경과 시간을 계산하여 파이프라인을 재개
3. 인메모리 `_active_evacuations` 딕셔너리 의존성 없음 — `CellState`가 SSOT

---

## 6. Source of Truth — 역할 분리

### 6.1 CellRegistry = 유일한 제어 평면 (Control Plane)

| 기능 | 담당 | 코드 근거 |
|------|------|----------|
| **라우팅 차단** | `get_cell_for_key()` Hash Ring skip | `registry.py` L137-150 |
| **서비스 재배치** | Hash Ring → 다음 ACTIVE Cell 자동 반환 | `registry.py` L137-150 |
| **상태 전파** | L1(Memory) + Pub/Sub + L2(Redis) 3-Tier | `registry.py` L321-405 |
| **복구 제어** | `set_cell_state(ACTIVE)` | `registry.py` L169 |

### 6.2 RegionalIsolationGate / BlastRadiusService = 단방향 통보

| 기능 | 담당 | 호출 방식 |
|------|------|----------|
| **감사 로그** | `log_region_isolation_audit()` | Fire-and-forget (Celery `apply_async`) |
| **전역 이벤트** | Redis Pub/Sub 이벤트 발행 | Fire-and-forget |
| **Blast Radius 기록** | `log_blast_radius_audit()` | Fire-and-forget |

**핵심 원칙**: Gate/Blast 호출 실패는 대피 파이프라인을 **절대 중단시키지 않는다**.
CellRegistry `set_cell_state()`가 가장 먼저 실행되고, 통보는 이후에 비동기로 처리된다.

### 6.3 Fire-and-forget 통보 패턴

기존 `services/throttle/sla_notification.py`의 Celery + 동기 폴백 패턴을 그대로 적용:

```python
# Celery 사용 환경: apply_async (비동기, tick 루프 비블로킹)
try:
    notify_cell_isolation.apply_async(kwargs={...})
except ImportError:
    # Celery 미사용 환경: 동기 폴백 (try/except 보호)
    self._notify_isolation_gate_sync(cell_id, reason)
```

**선택 근거**: `evaluate()`는 `health_check_interval_seconds=10초` tick 내에서 실행된다.
동기식 `isolate_region()`의 Redis 왕복이 3-5초 걸리면 tick의 30-50%가 소비되어
다른 Cell의 건강도 평가가 지연된다. Celery `apply_async`로 메인 루프와 생명주기를 분리한다.

---

## 7. Audit 통합

기존 `RegionalIsolationGate.isolate_region()`과 `BlastRadiusService.set_policy()`가 내부적으로 Audit 로그를 기록하므로, 대피 과정의 Audit는 **Fire-and-forget 통보를 통해 자동 기록됨**:

- `isolate_region()` → `log_region_isolation_audit()` (regional_gate.py)
- `set_policy()` → `log_blast_radius_audit()` (service.py L95)
- `restore_region()` → 복구 감사 로그

통보 실패 시 Audit 로그가 누락될 수 있으나, CellRegistry의 `metadata['last_state_change']`에
모든 상태 전이 이력이 기록되므로 감사 추적이 가능하다.

---

## 8. 설정 변경 범위

### 8.1 `settings/cell_topology.py` — 신규 필드

```python
# 기존 유지
evacuation_health_threshold: float = 0.3    # 대피 임계치
evacuation_traffic_drain_seconds: int = 30   # 드레인 시간

# v2 신규 추가
recovery_health_threshold: float = 0.7      # 복구 임계치 (R1)
evacuation_consecutive_count: int = 3        # 대피 연속 횟수 (R1)
recovery_consecutive_count: int = 5          # 복구 연속 횟수 (R1)
evacuation_drain_grace_seconds: float = 2.0  # NTP Drift 버퍼 (R2)
max_evacuated_ratio: float = 0.25            # Global Hard Limit (R4)

# v2.1 리팩토링 추가
isolation_notification_duration_seconds: int = 3600  # 격리 통보 지속 시간
evacuation_history_max_size: int = 1000              # 인메모리 이력 최대 보관 건수
```

**환경변수**:
```
SELFHEALING_CELL_TOPOLOGY_RECOVERY_HEALTH_THRESHOLD=0.7
SELFHEALING_CELL_TOPOLOGY_EVACUATION_CONSECUTIVE_COUNT=3
SELFHEALING_CELL_TOPOLOGY_RECOVERY_CONSECUTIVE_COUNT=5
SELFHEALING_CELL_TOPOLOGY_EVACUATION_DRAIN_GRACE_SECONDS=2.0
SELFHEALING_CELL_TOPOLOGY_MAX_EVACUATED_RATIO=0.25
SELFHEALING_CELL_TOPOLOGY_ISOLATION_NOTIFICATION_DURATION_SECONDS=3600
SELFHEALING_CELL_TOPOLOGY_EVACUATION_HISTORY_MAX_SIZE=1000
```

---

## 9. 변경 범위

| 파일 | 변경 | 유형 | 리뷰 반영 |
|------|------|------|----------|
| `services/cell_topology/policy.py` | 전면 재작성 | 필수 | R1~R5 전체 |
| `services/cell_topology/health.py` | `aggregate_all()` → `evaluate()` 호출 추가 | 필수 | §1 설계 반영 |
| `settings/cell_topology.py` | 7개 필드 추가 | 필수 | R1, R2, R4 + v2.1 |
| `adapters/celery/tasks.py` | Celery 태스크 3개 추가 | 필수 | R5 |

**기존 파일 변경 없음**:
- `CellRegistry`: `set_cell_state()`, `get_cell_for_key()` 그대로 사용
- `RegionalIsolationGate`: `isolate_region()`/`restore_region()` (통보 목적만)
- `BlastRadiusService`: `set_policy()` (통보 목적만)
- ~~`BulkheadRegistry`~~: **더 이상 사용하지 않음** (R3: 로컬 Bulkhead 조작 제거)
- ~~`TrafficGate`~~: **더 이상 직접 호출하지 않음** (Hash Ring이 대체)

---

## 10. v1 → v2 제거 항목

| 제거 항목 | 근거 |
|----------|------|
| `EvacuationPhase` Enum | `CellState`가 SSOT — 별도 Phase 불필요 |
| `threading.Thread` + `time.sleep()` | Tick-Based State Machine으로 대체 (R2) |
| `_active_evacuations` 인메모리 dict | `CellState`가 SSOT — 리더 교체 시 유실 위험 제거 |
| `bulkhead.max_concurrent = 0` | Hash Ring 라우팅이 전역 차단 (R3) |
| `BulkheadRegistry` import/사용 | 로컬 메모리 조작 불완전 (R3) |
| 동기식 `isolate_region()`/`set_policy()` 호출 | Celery Fire-and-forget으로 대체 (R5) |

---

## 11. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정, 토글) |
| `262_CELL_REGISTRY.md` | `set_cell_state()`, `get_cell_for_key()` 사용 (SoT) |
| `263_CELL_TAGGER.md` | Cell 태깅으로 트래픽 라우팅 |
| `264_CELL_HEALTH.md` | 건강도 수집 (측정), 히스테리시스 스펙 정의 (§5.2) |
| `services/isolation/regional_gate.py` | `isolate_region()`/`restore_region()` (통보용, 변경 없음) |
| `services/blast_radius/service.py` | `set_policy()` (통보용, 변경 없음) |
| `services/throttle/sla_notification.py` | Celery Fire-and-forget 패턴 참조 |
| `services/security_notification/` | Critical 알림 라우팅 패턴 참조 (PagerDuty) |
