# 261. Cell-Based Blast Radius Containment — 개요 및 설정

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `settings/cell_topology.py`, `services/cell_topology/__init__.py`

---

## 0. 요약

Cell-Based Architecture를 Self-Healing 시스템에 도입하여, 장애 영향 범위를 **물리적 Cell 단위**로 격리한다.

현재 시스템의 `BlastRadiusService`, `BulkheadRegistry`, `TrafficGate`, `RegionalIsolationGate`를 **확장**(수정 아님)하여, Cell 개념을 추가 레이어로 적용한다.

**핵심 원칙**: 기존 미들웨어 철학에 따라 **토글 기반 제어** — 모든 기능을 한번에 구현하되, 각 기능별 `enabled` 플래그로 점진적 활성화 가능.

---

## 1. 기존 시스템과의 관계

### 1.1 확장할 기존 컴포넌트

| 기존 컴포넌트 | 파일 | 역할 | Cell 확장 |
|---------------|------|------|-----------|
| `BlastRadiusService` | `services/blast_radius/service.py` (512줄) | 장애 영향 범위 DNA | `cell_id` 파라미터 추가, Cell 단위 격리 |
| `BulkheadRegistry` | `resilience/bulkhead/registry.py` (349줄) | 격벽 패턴 관리 | Cell 단위 Bulkhead 자동 생성 |
| `TrafficGate` | `scaling/traffic_gate.py` (435줄) | 4단계 트래픽 제어 | Cell 단위 트래픽 차단 |
| `RegionalIsolationGate` | `services/isolation/regional_gate.py` (440줄) | 리전 격리 | Cell 격리 (리전 내 세분화) |
| `TieringMiddleware` | `api/django/tiering/middleware.py` (224줄) | EmergencyMode + Backpressure | Cell별 EmergencyLevel 적용 |

### 1.2 새로 추가할 컴포넌트

| 컴포넌트 | 파일 | 문서 |
|----------|------|------|
| `CellTopologySettings` | `settings/cell_topology.py` | 본 문서 (261) |
| `CellRegistry` | `services/cell_topology/registry.py` | 262 |
| `CellTagger` | `services/cell_topology/tagger.py` | 263 |
| `CellHealthAggregator` | `services/cell_topology/health.py` | 264 |
| `CellEvacuationPolicy` | `services/cell_topology/policy.py` | 265 |

---

## 2. 토글 기반 설정 — `CellTopologySettings`

### 2.1 설계 근거

기존 미들웨어 패턴 참조:

- `TieringMiddleware`: `settings.SELFHEALING_TIERING_MIDDLEWARE_ENABLED` (tiering/middleware.py L20)
- `BackpressureMiddleware`: `settings.SELFHEALING_BACKPRESSURE_MIDDLEWARE_ENABLED` (middleware/backpressure.py)
- `BulkheadRegistry`: `BulkheadSettings` (settings/bulkhead.py)

### 2.2 Settings 클래스

```python
"""
Cell Topology Settings.

Cell-Based Blast Radius Containment 설정.
각 기능별 독립 토글로 점진적 활성화를 지원합니다.

환경변수:
    SELFHEALING_CELL_TOPOLOGY_ENABLED: 전체 활성화 (기본: False)
    SELFHEALING_CELL_TAGGING_ENABLED: 요청 태깅 (기본: False)
    SELFHEALING_CELL_BULKHEAD_ENABLED: Cell 격벽 격리 (기본: False)
    SELFHEALING_CELL_EVACUATION_ENABLED: Cell 대피 (기본: False)
    SELFHEALING_CELL_COUNT: Cell 수 (기본: 8)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CellTopologySettings:
    """
    Cell Topology 설정.

    토글 체계:
    - enabled: 전체 마스터 스위치
    - tagging_enabled: 요청/태스크에 cell_id 태깅
    - bulkhead_isolation_enabled: Cell 단위 Bulkhead 격벽
    - evacuation_enabled: Cell 대피 (트래픽 이전)
    """

    # === 마스터 토글 ===
    enabled: bool = False
    """전체 Cell Topology 활성화. False면 모든 Cell 기능 비활성."""

    # === 개별 기능 토글 ===
    tagging_enabled: bool = False
    """요청/태스크에 cell_id 태깅. enabled=True일 때만 동작."""

    bulkhead_isolation_enabled: bool = False
    """Cell 단위 Bulkhead 격벽. enabled=True일 때만 동작."""

    evacuation_enabled: bool = False
    """Cell 대피(트래픽 이전). enabled=True일 때만 동작."""

    # === Cell 구조 ===
    cell_count: int = 8
    """Cell 수. Consistent Hash Ring에서 사용."""

    cell_prefix: str = "cell"
    """Cell 이름 접두사. 예: cell-0, cell-1, ..."""

    # === Bulkhead 연동 ===
    bulkhead_max_concurrent_per_cell: int = 100
    """Cell별 Bulkhead 최대 동시 요청 수."""

    bulkhead_type: str = "semaphore"
    """Bulkhead 유형. BulkheadRegistry.get_or_create()에 전달."""

    # === 대피 정책 ===
    evacuation_health_threshold: float = 0.3
    """Cell 건강도가 이 값 이하면 대피 트리거. (0.0~1.0)"""

    evacuation_traffic_drain_seconds: int = 30
    """대피 시 트래픽 드레인 대기 시간(초)."""

    # === 모니터링 ===
    health_check_interval_seconds: int = 10
    """Cell 건강 체크 주기(초)."""

    metrics_enabled: bool = True
    """Prometheus 메트릭 수집 여부."""


def get_cell_topology_settings() -> CellTopologySettings:
    """
    환경변수에서 CellTopologySettings 생성.

    Returns:
        CellTopologySettings
    """
    def _bool(key: str, default: bool) -> bool:
        return os.environ.get(key, str(default)).lower() in ("true", "1", "yes")

    def _int(key: str, default: int) -> int:
        return int(os.environ.get(key, str(default)))

    return CellTopologySettings(
        enabled=_bool("SELFHEALING_CELL_TOPOLOGY_ENABLED", False),
        tagging_enabled=_bool("SELFHEALING_CELL_TAGGING_ENABLED", False),
        bulkhead_isolation_enabled=_bool("SELFHEALING_CELL_BULKHEAD_ENABLED", False),
        evacuation_enabled=_bool("SELFHEALING_CELL_EVACUATION_ENABLED", False),
        cell_count=_int("SELFHEALING_CELL_COUNT", 8),
        bulkhead_max_concurrent_per_cell=_int(
            "SELFHEALING_CELL_BULKHEAD_MAX_CONCURRENT", 100
        ),
        evacuation_health_threshold=float(
            os.environ.get("SELFHEALING_CELL_EVACUATION_THRESHOLD", "0.3")
        ),
        evacuation_traffic_drain_seconds=_int(
            "SELFHEALING_CELL_EVACUATION_DRAIN_SECONDS", 30
        ),
        health_check_interval_seconds=_int(
            "SELFHEALING_CELL_HEALTH_CHECK_INTERVAL", 10
        ),
    )
```

### 2.3 토글 활성화 순서 (권장)

```
Phase 1: enabled=True, tagging_enabled=True
  → 요청/태스크에 cell_id 태깅만 수행
  → 기존 동작에 영향 없음, 메트릭 수집 시작

Phase 2: + bulkhead_isolation_enabled=True
  → Cell 단위 Bulkhead 격벽 활성화
  → BulkheadRegistry에 Cell별 Bulkhead 자동 등록

Phase 3: + evacuation_enabled=True
  → Cell 건강도 기반 자동 대피 활성화
  → TrafficGate, RegionalIsolationGate 연동
```

**단, 모든 Phase의 코드는 동시에 배포됨** — 토글만 변경하여 기능 활성화.

---

## 3. 디렉토리 구조

```
packages/selfhealing-python/src/selfhealing/
├── settings/
│   └── cell_topology.py          # CellTopologySettings
└── services/
    └── cell_topology/
        ├── __init__.py            # 패키지 초기화, 싱글톤 팩토리
        ├── registry.py            # CellRegistry (262)
        ├── tagger.py              # CellTagger (263)
        ├── health.py              # CellHealthAggregator (264)
        └── policy.py              # CellEvacuationPolicy (265)
```

### 3.1 `__init__.py` — 싱글톤 팩토리

```python
"""
Cell-Based Blast Radius Containment.

기존 BlastRadiusService, BulkheadRegistry, TrafficGate,
RegionalIsolationGate를 Cell 단위로 확장합니다.

활성화:
    SELFHEALING_CELL_TOPOLOGY_ENABLED=true
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.cell_topology.registry import CellRegistry

_cell_registry: CellRegistry | None = None
_lock = threading.Lock()


def get_cell_registry() -> CellRegistry:
    """CellRegistry 싱글톤 반환."""
    global _cell_registry
    if _cell_registry is None:
        with _lock:
            if _cell_registry is None:
                from selfhealing.services.cell_topology.registry import CellRegistry
                from selfhealing.settings.cell_topology import (
                    get_cell_topology_settings,
                )

                _cell_registry = CellRegistry(settings=get_cell_topology_settings())
    return _cell_registry


def reset_cell_registry() -> None:
    """테스트용 리셋."""
    global _cell_registry
    _cell_registry = None
```

---

## 4. 기존 컴포넌트 참조 코드

### 4.1 `BulkheadRegistry.get_or_create()` — Cell Bulkhead 등록에 사용

**파일**: `resilience/bulkhead/registry.py` L196

```python
def get_or_create(
    self,
    name: str,
    max_concurrent: int | None = None,
    bulkhead_type: str = "semaphore",
) -> Bulkhead:
```

→ `CellRegistry`가 Cell별 Bulkhead를 `get_or_create(f"cell-{i}", max_concurrent=100)`으로 등록.

### 4.2 `TrafficGate.should_allow()` — Cell 트래픽 제어에 사용

**파일**: `scaling/traffic_gate.py` L193

```python
def should_allow(
    self,
    priority: int = 0,
    bulkhead_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    bulkhead_timeout: float | None = None,
) -> TrafficDecision:
```

→ `CellTagger`가 `should_allow(bulkhead_name=f"cell-{cell_id}")` 호출로 Cell 트래픽 제어.

### 4.3 `RegionalIsolationGate.isolate_region()` — Cell 격리에 사용

**파일**: `services/isolation/regional_gate.py` L147

```python
def isolate_region(
    self,
    region: str,
    reason: str,
    duration_seconds: int = 300,
) -> bool:
```

→ `CellEvacuationPolicy`가 `isolate_region(f"cell-{cell_id}", reason)` 호출로 Cell 격리.

### 4.4 `BlastRadiusService.set_policy()` — Cell 단위 정책에 사용

**파일**: `services/blast_radius/service.py` L65

```python
def set_policy(
    self,
    stage_name: str,
    level: BlastRadiusLevel = BlastRadiusLevel.MINIMAL,
    affected_services: list[str] | None = None,
    max_affected_percentage: float = 10.0,
    auto_isolate: bool = True,
) -> BlastRadiusPolicy:
```

→ Cell별 `set_policy(f"cell-{cell_id}", level, affected_services=[cell_services])`

---

## 5. 문서 구조

| 번호 | 제목 | 내용 |
|------|------|------|
| **261** (본 문서) | 개요 및 설정 | Settings, 디렉토리, 기존 연동점 |
| **262** | Cell Registry | Consistent Hash Ring, Cell 할당 |
| **263** | Cell Tagger | Django 미들웨어, Celery 태스크 태깅 |
| **264** | Cell Health Aggregator | 건강도 집계, Prometheus 메트릭 |
| **265** | Cell Evacuation Policy | 대피 정책, TrafficGate/Isolation 연동 |

---

## 6. 관련 문서

| 문서 | 관계 |
|------|------|
| `services/blast_radius/service.py` | 확장 대상 (cell_id 파라미터 추가) |
| `resilience/bulkhead/registry.py` | 확장 대상 (Cell Bulkhead 등록) |
| `scaling/traffic_gate.py` | 확장 대상 (Cell 트래픽 제어) |
| `services/isolation/regional_gate.py` | 확장 대상 (Cell 격리) |
| `api/django/tiering/middleware.py` | 참조 (토글 패턴) |
| `settings/bulkhead.py` | 참조 (Settings 패턴) |
