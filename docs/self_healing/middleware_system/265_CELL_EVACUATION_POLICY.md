# 265. Cell Evacuation Policy — 대피 정책 및 TrafficGate/Isolation 연동

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/policy.py`

---

## 0. 요약

`CellEvacuationPolicy`는 Cell 건강도가 임계치 이하로 떨어졌을 때, **트래픽 드레인 → Cell 격리 → 서비스 재배치**를 수행한다.
기존 `RegionalIsolationGate`, `TrafficGate`, `BlastRadiusService`를 **그대로 호출**하여 Cell 단위 대피를 구현한다.

토글: `CellTopologySettings.evacuation_enabled=True`일 때만 동작.

---

## 1. 대피 흐름

```
CellHealthAggregator.aggregate_all()
  → health_score ≤ 0.3 감지
    → CellEvacuationPolicy.evaluate(cell_id, score)
      → Phase 1: DRAINING 전환
        → CellRegistry.set_cell_state(cell_id, DRAINING)
        → TrafficGate: 해당 Cell Bulkhead에서 신규 요청 차단
      → Phase 2: 트래픽 드레인 대기 (30초)
        → 기존 요청 완료 대기
      → Phase 3: ISOLATED 전환
        → RegionalIsolationGate.isolate_region(cell_id, reason)
        → CellRegistry.set_cell_state(cell_id, ISOLATED)
      → Phase 4: 서비스 재배치
        → Consistent Hash Ring에서 ISOLATED Cell 제외
        → 해당 Cell의 서비스가 다음 ACTIVE Cell로 자동 할당
```

---

## 2. 기존 연동점 — 코드 근거

### 2.1 `RegionalIsolationGate.isolate_region()` — Cell 격리에 사용

**파일**: `services/isolation/regional_gate.py` L147

```python
def isolate_region(
    self,
    region: str,           # ← cell_id 전달 (예: "cell-3")
    reason: str,
    duration_seconds: int = 300,
) -> bool:
```

`isolate_region`의 `region` 파라미터는 문자열이므로, Cell ID를 그대로 전달할 수 있다.
Redis 키: `selfhealing:global:isolation:cell-3`

**Audit 연동**: `log_region_isolation_audit()`가 자동 호출되어 감사 로그 기록.

### 2.2 `RegionalIsolationGate.restore_region()` — Cell 복구에 사용

**파일**: `services/isolation/regional_gate.py` L275

```python
def restore_region(self, region: str) -> bool:
```

대피 해제 시 `restore_region("cell-3")` 호출.

### 2.3 `TrafficGate.should_allow()` — Cell 트래픽 차단

**파일**: `scaling/traffic_gate.py` L193

```python
def should_allow(
    self,
    priority: int = 0,
    bulkhead_name: str | None = None,   # ← cell_id
    metadata: dict[str, Any] | None = None,
    bulkhead_timeout: float | None = None,
) -> TrafficDecision:
```

DRAINING 상태 Cell의 Bulkhead를 0으로 설정하면, `should_allow(bulkhead_name="cell-3")`이 자동으로 차단한다.

### 2.4 `BlastRadiusService.set_policy()` — Cell 단위 정책

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

대피 시 Cell의 blast radius 정책을 `CRITICAL`로 설정하여, `auto_isolate=True`에 의한 자동 격리를 트리거할 수 있다.

### 2.5 `BulkheadRegistry` — Cell Bulkhead 조작

**파일**: `resilience/bulkhead/registry.py`

```python
def get_or_create(self, name: str, max_concurrent: int | None = None, ...) -> Bulkhead:   # L196
def get(self, name: str | ConnectionType) -> Bulkhead:                                     # L176
```

DRAINING 시 `get("cell-3")` → `bulkhead.max_concurrent = 0` 설정으로 신규 요청 차단.

---

## 3. CellEvacuationPolicy 구현

```python
"""
Cell Evacuation Policy — Cell 대피 정책.

건강도 임계치 기반으로 Cell 대피를 수행합니다.

대피 순서:
1. DRAINING 전환 (신규 트래픽 차단)
2. 트래픽 드레인 대기
3. ISOLATED 전환 (완전 격리)
4. 서비스 재배치 (Consistent Hash에서 제외)

의존성:
- CellRegistry: Cell 상태 관리
- RegionalIsolationGate: Cell 격리
- BulkheadRegistry: Cell Bulkhead 조작
- BlastRadiusService: Cell blast radius 정책
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EvacuationPhase(str, Enum):
    """대피 단계."""
    DRAINING = "draining"
    WAITING = "waiting"
    ISOLATING = "isolating"
    REDISTRIBUTING = "redistributing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class EvacuationRecord:
    """대피 기록."""
    cell_id: str
    trigger_health_score: float
    phase: EvacuationPhase
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    reason: str = ""
    affected_services: list[str] = field(default_factory=list)


class CellEvacuationPolicy:
    """
    Cell 대피 정책.

    CellHealthAggregator에서 건강도 임계치 이하 알림을 받으면,
    대피 프로세스를 실행합니다.

    토글:
    - CellTopologySettings.evacuation_enabled=True일 때만 동작
    - False이면 evaluate() 즉시 반환

    사용:
        policy = CellEvacuationPolicy()
        policy.evaluate("cell-3", health_score=0.2)  # 자동 대피 실행
    """

    def __init__(self, settings: Any = None):
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._lock = threading.RLock()
        self._active_evacuations: dict[str, EvacuationRecord] = {}
        self._evacuation_history: list[EvacuationRecord] = []

    def evaluate(self, cell_id: str, health_score: float) -> bool:
        """
        대피 필요 여부 평가 및 실행.

        Args:
            cell_id: Cell 식별자
            health_score: 현재 건강도 (0.0~1.0)

        Returns:
            대피 실행 여부
        """
        if not self._settings.enabled or not self._settings.evacuation_enabled:
            return False

        # 이미 대피 중이면 스킵
        if cell_id in self._active_evacuations:
            logger.debug(f"Cell {cell_id} already evacuating, skip")
            return False

        # 임계치 확인
        if health_score > self._settings.evacuation_health_threshold:
            return False

        logger.warning(
            f"Cell {cell_id} health={health_score:.2f} "
            f"≤ threshold={self._settings.evacuation_health_threshold}, "
            f"starting evacuation"
        )

        # 비동기 대피 실행
        record = EvacuationRecord(
            cell_id=cell_id,
            trigger_health_score=health_score,
            phase=EvacuationPhase.DRAINING,
            reason=f"Health score {health_score:.2f} below threshold",
        )
        self._active_evacuations[cell_id] = record

        thread = threading.Thread(
            target=self._execute_evacuation,
            args=(record,),
            daemon=True,
            name=f"cell-evacuate-{cell_id}",
        )
        thread.start()

        return True

    def _execute_evacuation(self, record: EvacuationRecord) -> None:
        """대피 프로세스 실행."""
        cell_id = record.cell_id

        try:
            # === Phase 1: DRAINING ===
            record.phase = EvacuationPhase.DRAINING
            self._phase_draining(cell_id, record)

            # === Phase 2: 트래픽 드레인 대기 ===
            record.phase = EvacuationPhase.WAITING
            drain_seconds = self._settings.evacuation_traffic_drain_seconds
            logger.info(
                f"Cell {cell_id}: waiting {drain_seconds}s for traffic drain"
            )
            time.sleep(drain_seconds)

            # === Phase 3: ISOLATED ===
            record.phase = EvacuationPhase.ISOLATING
            self._phase_isolating(cell_id, record)

            # === Phase 4: 서비스 재배치 ===
            record.phase = EvacuationPhase.REDISTRIBUTING
            self._phase_redistributing(cell_id, record)

            record.phase = EvacuationPhase.COMPLETED
            record.completed_at = datetime.now(timezone.utc)
            logger.info(f"Cell {cell_id}: evacuation completed")

        except Exception as e:
            record.phase = EvacuationPhase.FAILED
            record.reason += f" | Failed: {e}"
            logger.error(f"Cell {cell_id}: evacuation failed: {e}")
        finally:
            self._active_evacuations.pop(cell_id, None)
            self._evacuation_history.append(record)

    def _phase_draining(self, cell_id: str, record: EvacuationRecord) -> None:
        """
        Phase 1: DRAINING 전환.

        - CellRegistry 상태를 DRAINING으로 변경
        - Cell Bulkhead의 max_concurrent를 0으로 설정 (신규 요청 차단)
        """
        from selfhealing.services.cell_topology import get_cell_registry
        from selfhealing.services.cell_topology.registry import CellState

        registry = get_cell_registry()
        registry.set_cell_state(cell_id, CellState.DRAINING, record.reason)

        # Cell 내 서비스 목록 저장
        cell = registry.get_cell_info(cell_id)
        if cell:
            record.affected_services = list(cell.assigned_services)

        # Bulkhead 차단 (신규 요청 거부)
        try:
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            bulkhead_registry = get_bulkhead_registry()
            bulkhead = bulkhead_registry.get(cell_id)
            if bulkhead:
                # max_concurrent를 0으로 설정하면
                # TrafficGate.should_allow()에서 bulkhead full로 거부
                bulkhead.max_concurrent = 0
                logger.info(f"Cell {cell_id}: Bulkhead closed (max_concurrent=0)")
        except Exception as e:
            logger.warning(f"Cell {cell_id}: Bulkhead close failed: {e}")

        logger.info(f"Cell {cell_id}: Phase 1 DRAINING complete")

    def _phase_isolating(self, cell_id: str, record: EvacuationRecord) -> None:
        """
        Phase 3: ISOLATED 전환.

        - RegionalIsolationGate로 Cell 격리
        - BlastRadiusService에 CRITICAL 정책 설정
        - CellRegistry 상태를 ISOLATED로 변경
        """
        from selfhealing.services.cell_topology import get_cell_registry
        from selfhealing.services.cell_topology.registry import CellState

        registry = get_cell_registry()

        # RegionalIsolationGate 격리
        try:
            from selfhealing.services.isolation.regional_gate import (
                get_regional_isolation_gate,
            )

            gate = get_regional_isolation_gate()
            gate.isolate_region(
                region=cell_id,
                reason=record.reason,
                duration_seconds=3600,  # 1시간 (수동 해제 가능)
            )
            logger.info(f"Cell {cell_id}: RegionalIsolationGate isolated")
        except ImportError:
            logger.warning("RegionalIsolationGate not available")
        except Exception as e:
            logger.error(f"Cell {cell_id}: isolation failed: {e}")

        # BlastRadiusService 정책 설정
        try:
            from selfhealing.services.blast_radius.service import BlastRadiusService
            from selfhealing.services.blast_radius.models import BlastRadiusLevel

            blast_service = BlastRadiusService()
            blast_service.set_policy(
                stage_name=cell_id,
                level=BlastRadiusLevel.CRITICAL,
                affected_services=record.affected_services,
                max_affected_percentage=0.0,  # 영향 허용 안 함
                auto_isolate=True,
            )
            logger.info(f"Cell {cell_id}: BlastRadius policy set to CRITICAL")
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Cell {cell_id}: BlastRadius policy set failed: {e}")

        registry.set_cell_state(cell_id, CellState.ISOLATED, record.reason)
        logger.info(f"Cell {cell_id}: Phase 3 ISOLATED complete")

    def _phase_redistributing(self, cell_id: str, record: EvacuationRecord) -> None:
        """
        Phase 4: 서비스 재배치.

        ISOLATED Cell을 Hash Ring에서 제외하면,
        CellRegistry.get_cell_for_key()가 자동으로 다음 ACTIVE Cell을 반환한다.
        이미 262 CellRegistry에서 구현된 로직:
          → DRAINING/ISOLATED Cell은 건너뛰고 다음 ACTIVE Cell 반환
        """
        logger.info(
            f"Cell {cell_id}: Phase 4 REDISTRIBUTING — "
            f"{len(record.affected_services)} services will be "
            f"redistributed via Consistent Hash Ring"
        )

        # 영향받는 서비스 로그
        for svc in record.affected_services:
            from selfhealing.services.cell_topology import get_cell_registry
            registry = get_cell_registry()
            new_cell = registry.get_cell_for_key(svc)
            logger.info(f"  Service '{svc}': {cell_id} → {new_cell}")

    def restore_cell(self, cell_id: str) -> bool:
        """
        Cell 복구 (수동 호출).

        대피된 Cell을 ACTIVE로 복원합니다.

        Args:
            cell_id: Cell 식별자

        Returns:
            복원 성공 여부
        """
        if not self._settings.enabled:
            return False

        try:
            from selfhealing.services.cell_topology import get_cell_registry
            from selfhealing.services.cell_topology.registry import CellState

            registry = get_cell_registry()

            # RegionalIsolationGate 해제
            try:
                from selfhealing.services.isolation.regional_gate import (
                    get_regional_isolation_gate,
                )
                gate = get_regional_isolation_gate()
                gate.restore_region(cell_id)
            except Exception as e:
                logger.warning(f"Cell {cell_id}: isolation restore failed: {e}")

            # Bulkhead 복원
            try:
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )
                bulkhead_registry = get_bulkhead_registry()
                bulkhead = bulkhead_registry.get(cell_id)
                if bulkhead:
                    bulkhead.max_concurrent = (
                        self._settings.bulkhead_max_concurrent_per_cell
                    )
            except Exception as e:
                logger.warning(f"Cell {cell_id}: bulkhead restore failed: {e}")

            # CellRegistry ACTIVE 전환
            registry.set_cell_state(
                cell_id, CellState.ACTIVE, "Manual restoration"
            )

            logger.info(f"Cell {cell_id}: restored to ACTIVE")
            return True

        except Exception as e:
            logger.error(f"Cell {cell_id}: restore failed: {e}")
            return False

    def get_active_evacuations(self) -> dict[str, EvacuationRecord]:
        """현재 진행 중인 대피 목록."""
        return dict(self._active_evacuations)

    def get_evacuation_history(self) -> list[EvacuationRecord]:
        """대피 이력."""
        return list(self._evacuation_history)
```

---

## 4. 대피 상태 전이 다이어그램

```
ACTIVE ──[health ≤ 0.3]──→ DRAINING ──[drain 30s]──→ ISOLATED
  ↑                                                      │
  └──────────────[restore_cell()]─────────────────────────┘
```

| 상태 | TrafficGate 동작 | RegionalIsolationGate | Hash Ring |
|------|-------------------|----------------------|-----------|
| ACTIVE | Bulkhead 정상 허용 | 격리 없음 | 포함 |
| DRAINING | Bulkhead max=0, 신규 차단 | 격리 없음 | 포함 (기존 요청 완료용) |
| ISOLATED | Bulkhead max=0 | 격리됨 | **제외** (다음 ACTIVE로 우회) |

---

## 5. 안전장치

### 5.1 동시 대피 제한

```python
# evaluate() 내부
if cell_id in self._active_evacuations:
    return False  # 이미 대피 중이면 중복 실행 방지
```

### 5.2 최소 ACTIVE Cell 보장

```python
# evaluate() 내부에 추가 가능한 안전장치
active_count = len(registry.get_active_cells())
if active_count <= 2:
    logger.warning(f"Only {active_count} active cells, skipping evacuation")
    return False
```

### 5.3 수동 복구 경로

```python
# 관리자가 수동으로 Cell 복구
policy = CellEvacuationPolicy()
policy.restore_cell("cell-3")
```

### 5.4 자동 복구 (선택적 확장)

```python
# CellHealthAggregator에서 건강도 회복 감지 시
if cell.state == CellState.ISOLATED and health_score > 0.7:
    policy.restore_cell(cell_id)
```

---

## 6. Audit 통합

기존 `RegionalIsolationGate.isolate_region()`과 `BlastRadiusService.set_policy()`가 내부적으로 Audit 로그를 기록하므로, 대피 과정의 Audit는 **자동 기록됨**:

- `isolate_region()` → `log_region_isolation_audit()` (regional_gate.py)
- `set_policy()` → `log_blast_radius_audit()` (service.py L95)

---

## 7. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `services/cell_topology/policy.py` | 신규 생성 | 필수 |

**기존 파일 변경 없음**:
- `RegionalIsolationGate`: `isolate_region(cell_id)` 그대로 호출
- `BlastRadiusService`: `set_policy(cell_id)` 그대로 호출
- `BulkheadRegistry`: `get(cell_id)` 그대로 호출
- `TrafficGate`: `should_allow(bulkhead_name=cell_id)` 그대로 호출

---

## 8. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정, 토글) |
| `262_CELL_REGISTRY.md` | `set_cell_state()`, `get_cell_for_key()` 사용 |
| `263_CELL_TAGGER.md` | Cell 태깅으로 트래픽 라우팅 |
| `264_CELL_HEALTH.md` | 건강도 수집, 대피 트리거 |
| `services/isolation/regional_gate.py` | `isolate_region()`/`restore_region()` (변경 없음) |
| `services/blast_radius/service.py` | `set_policy()` (변경 없음) |
| `resilience/bulkhead/registry.py` | `get()` (변경 없음) |
| `scaling/traffic_gate.py` | `should_allow()` (변경 없음) |
