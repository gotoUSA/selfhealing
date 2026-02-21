# 264. Cell Health Aggregator — 건강도 집계 및 Prometheus 메트릭

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/health.py`

---

## 0. 요약

`CellHealthAggregator`는 Cell별 건강도를 집계하여 `CellRegistry.update_health_score()`에 반영하고, Prometheus 메트릭을 노출한다. 건강도가 임계치 이하로 떨어지면 `CellEvacuationPolicy` (265)가 대피를 트리거한다.

---

## 1. 설계 근거 — 기존 건강 체크 패턴

### 1.1 `HealthProbeManager` — Meta-Watchdog 건강 프로브

**파일**: `packages/selfhealing-python/src/selfhealing/meta/health_probe.py`

| 프로브 | 대상 | 메트릭 |
|--------|------|--------|
| `RedisProbe` (L284–L369) | Redis 연결, 메모리, 키 수 | ping latency, memory usage |
| `CircuitBreakerProbe` | CB 상태 (stuck OPEN 감지) | stuck_count |
| `DLQProbe` | DLQ 크기 | queue_size |
| `RecoveryPipelineProbe` | 파이프라인 상태 | active_recoveries |

`CellHealthAggregator`는 이 프로브들의 결과를 **Cell 단위로 집계**한다.

### 1.2 기존 메트릭 패턴 — `MetricsRecorder`

**파일**: `packages/selfhealing-python/src/selfhealing/metrics/recorders.py`

기존 시스템은 `prometheus_client`를 사용하여 메트릭을 노출:
- `Counter`, `Gauge`, `Histogram` 사용
- label로 분류 (`service_name`, `region`, `status` 등)

`CellHealthAggregator`도 동일 패턴으로 `cell_id` label을 추가한다.

---

## 2. 건강도 산출 모델

### 2.1 입력 시그널

| 시그널 | 소스 | 가중치 |
|--------|------|--------|
| `error_rate` | Prometheus `request_errors_total` / `request_total` per cell | 0.35 |
| `latency_p99` | Prometheus `request_duration_seconds` p99 per cell | 0.25 |
| `bulkhead_utilization` | `BulkheadRegistry.get_all_states()` per cell | 0.20 |
| `circuit_breaker_open_ratio` | Cell 내 서비스 중 CB OPEN 비율 | 0.20 |

### 2.2 건강도 공식

```
health_score = 1.0
             - (error_rate_normalized × 0.35)
             - (latency_normalized × 0.25)
             - (bulkhead_utilization × 0.20)
             - (cb_open_ratio × 0.20)

# 범위: 0.0 (완전 장애) ~ 1.0 (완전 건강)
```

### 2.3 정규화

```python
error_rate_normalized = min(error_rate / 0.5, 1.0)      # 50% 에러율 = 1.0
latency_normalized = min(latency_p99 / 5.0, 1.0)        # 5초 p99 = 1.0
bulkhead_utilization = active_count / max_concurrent     # 직접 비율
cb_open_ratio = open_cb_count / total_cb_count           # 직접 비율
```

---

## 3. CellHealthAggregator 구현

```python
"""
Cell Health Aggregator — Cell별 건강도 집계.

BulkheadRegistry, CircuitBreaker 상태를 수집하여
Cell 단위 건강도를 산출합니다.

의존성:
- CellRegistry: 건강도 업데이트 대상
- BulkheadRegistry: Bulkhead utilization 조회
- prometheus_client: 메트릭 노출 (선택적)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CellHealthSnapshot:
    """Cell 건강 상태 스냅샷."""

    cell_id: str
    health_score: float
    error_rate: float = 0.0
    latency_p99: float = 0.0
    bulkhead_utilization: float = 0.0
    cb_open_ratio: float = 0.0
    timestamp: float = field(default_factory=time.time)


class CellHealthAggregator:
    """
    Cell별 건강도 집계.

    주기적으로 건강 시그널을 수집하여 CellRegistry에 반영합니다.

    사용:
        aggregator = CellHealthAggregator()
        aggregator.start()   # 백그라운드 루프 시작

        # 수동 업데이트
        aggregator.record_request(cell_id, success=True, latency=0.05)

        # 스냅샷 조회
        snapshot = aggregator.get_snapshot("cell-3")
    """

    # 가중치 설정
    WEIGHT_ERROR_RATE = 0.35
    WEIGHT_LATENCY = 0.25
    WEIGHT_BULKHEAD = 0.20
    WEIGHT_CB_OPEN = 0.20

    # 정규화 기준
    MAX_ERROR_RATE = 0.5    # 50% 에러율 = 완전 비정상
    MAX_LATENCY_P99 = 5.0   # 5초 = 완전 비정상

    def __init__(self, settings: Any = None):
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._lock = threading.RLock()
        self._snapshots: dict[str, CellHealthSnapshot] = {}

        # 요청 카운터 (window 기반)
        self._request_counts: dict[str, int] = {}
        self._error_counts: dict[str, int] = {}
        self._latency_samples: dict[str, list[float]] = {}

        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Prometheus 메트릭 (선택적)
        self._metrics = self._init_metrics()

    def _init_metrics(self) -> dict[str, Any] | None:
        """Prometheus 메트릭 초기화."""
        if not self._settings.metrics_enabled:
            return None
        try:
            from prometheus_client import Counter, Gauge, Histogram

            return {
                "health_score": Gauge(
                    "selfhealing_cell_health_score",
                    "Cell health score (0.0~1.0)",
                    ["cell_id"],
                ),
                "request_total": Counter(
                    "selfhealing_cell_requests_total",
                    "Total requests per cell",
                    ["cell_id", "status"],
                ),
                "request_duration": Histogram(
                    "selfhealing_cell_request_duration_seconds",
                    "Request duration per cell",
                    ["cell_id"],
                    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
                ),
                "bulkhead_utilization": Gauge(
                    "selfhealing_cell_bulkhead_utilization",
                    "Bulkhead utilization ratio per cell",
                    ["cell_id"],
                ),
            }
        except ImportError:
            logger.debug("prometheus_client not available, metrics disabled")
            return None

    def record_request(
        self, cell_id: str, success: bool, latency: float
    ) -> None:
        """
        요청 결과 기록.

        CellTaggingMiddleware 또는 뷰에서 호출합니다.

        Args:
            cell_id: Cell 식별자
            success: 요청 성공 여부
            latency: 응답 시간(초)
        """
        with self._lock:
            self._request_counts[cell_id] = (
                self._request_counts.get(cell_id, 0) + 1
            )
            if not success:
                self._error_counts[cell_id] = (
                    self._error_counts.get(cell_id, 0) + 1
                )

            if cell_id not in self._latency_samples:
                self._latency_samples[cell_id] = []
            samples = self._latency_samples[cell_id]
            samples.append(latency)
            # 최근 1000개 유지
            if len(samples) > 1000:
                self._latency_samples[cell_id] = samples[-1000:]

        # Prometheus 메트릭
        if self._metrics:
            status = "success" if success else "error"
            self._metrics["request_total"].labels(
                cell_id=cell_id, status=status
            ).inc()
            self._metrics["request_duration"].labels(
                cell_id=cell_id
            ).observe(latency)

    def compute_health(self, cell_id: str) -> float:
        """
        Cell 건강도 산출.

        Args:
            cell_id: Cell 식별자

        Returns:
            건강도 (0.0~1.0)
        """
        with self._lock:
            # Error Rate
            total = self._request_counts.get(cell_id, 0)
            errors = self._error_counts.get(cell_id, 0)
            error_rate = (errors / total) if total > 0 else 0.0

            # Latency P99
            samples = self._latency_samples.get(cell_id, [])
            if samples:
                sorted_samples = sorted(samples)
                p99_idx = int(len(sorted_samples) * 0.99)
                latency_p99 = sorted_samples[min(p99_idx, len(sorted_samples) - 1)]
            else:
                latency_p99 = 0.0

        # Bulkhead Utilization
        bulkhead_util = self._get_bulkhead_utilization(cell_id)

        # CB Open Ratio
        cb_open_ratio = self._get_cb_open_ratio(cell_id)

        # 정규화
        error_norm = min(error_rate / self.MAX_ERROR_RATE, 1.0)
        latency_norm = min(latency_p99 / self.MAX_LATENCY_P99, 1.0)

        # 가중 합산
        penalty = (
            error_norm * self.WEIGHT_ERROR_RATE
            + latency_norm * self.WEIGHT_LATENCY
            + bulkhead_util * self.WEIGHT_BULKHEAD
            + cb_open_ratio * self.WEIGHT_CB_OPEN
        )

        health = max(0.0, 1.0 - penalty)

        # 스냅샷 저장
        self._snapshots[cell_id] = CellHealthSnapshot(
            cell_id=cell_id,
            health_score=health,
            error_rate=error_rate,
            latency_p99=latency_p99,
            bulkhead_utilization=bulkhead_util,
            cb_open_ratio=cb_open_ratio,
        )

        # Prometheus 게이지 업데이트
        if self._metrics:
            self._metrics["health_score"].labels(cell_id=cell_id).set(health)
            self._metrics["bulkhead_utilization"].labels(
                cell_id=cell_id
            ).set(bulkhead_util)

        return health

    def _get_bulkhead_utilization(self, cell_id: str) -> float:
        """
        BulkheadRegistry에서 Cell Bulkhead 사용률 조회.

        BulkheadRegistry.get_all_states() 사용.
        """
        try:
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            all_states = registry.get_all_states()
            state = all_states.get(cell_id)
            if state:
                max_c = state.max_concurrent or 1
                active = state.active_count or 0
                return min(active / max_c, 1.0)
        except Exception:
            pass
        return 0.0

    def _get_cb_open_ratio(self, cell_id: str) -> float:
        """Cell 내 서비스의 CB OPEN 비율 조회."""
        try:
            from selfhealing.services.cell_topology import get_cell_registry
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
            )

            registry = get_cell_registry()
            cell = registry.get_cell_info(cell_id)
            if not cell or not cell.assigned_services:
                return 0.0

            cb_service = get_circuit_breaker_service()
            all_states = cb_service.get_all_states()

            open_count = 0
            total = 0
            for state in all_states:
                if state.get("service_name") in cell.assigned_services:
                    total += 1
                    if state.get("state") == "OPEN":
                        open_count += 1

            return (open_count / total) if total > 0 else 0.0
        except Exception:
            return 0.0

    def get_snapshot(self, cell_id: str) -> CellHealthSnapshot | None:
        """최근 건강 스냅샷 조회."""
        return self._snapshots.get(cell_id)

    def get_all_snapshots(self) -> dict[str, CellHealthSnapshot]:
        """모든 Cell 건강 스냅샷 조회."""
        return dict(self._snapshots)

    def aggregate_all(self) -> None:
        """
        모든 Cell 건강도 일괄 산출 및 CellRegistry 갱신.

        백그라운드 루프에서 주기적으로 호출됩니다.
        """
        try:
            from selfhealing.services.cell_topology import get_cell_registry

            registry = get_cell_registry()
            for cell_id in registry.get_all_cells():
                score = self.compute_health(cell_id)
                registry.update_health_score(cell_id, score)
        except Exception as e:
            logger.error(f"CellHealthAggregator aggregate_all failed: {e}")

    def start(self) -> None:
        """백그라운드 건강 체크 루프 시작."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._health_loop, daemon=True, name="cell-health-aggregator"
        )
        self._worker.start()
        logger.info(
            f"CellHealthAggregator started "
            f"(interval={self._settings.health_check_interval_seconds}s)"
        )

    def stop(self) -> None:
        """백그라운드 루프 중지."""
        self._running = False
        self._stop_event.set()

    def _health_loop(self) -> None:
        """주기적 건강도 집계 루프."""
        while self._running:
            try:
                self.aggregate_all()
            except Exception as e:
                logger.error(f"CellHealthAggregator loop error: {e}")
            self._stop_event.wait(timeout=self._settings.health_check_interval_seconds)
```

---

## 4. Prometheus 메트릭 사양

| 메트릭 | 유형 | Labels | 설명 |
|--------|------|--------|------|
| `selfhealing_cell_health_score` | Gauge | `cell_id` | Cell 건강도 (0.0~1.0) |
| `selfhealing_cell_requests_total` | Counter | `cell_id`, `status` | Cell별 요청 수 |
| `selfhealing_cell_request_duration_seconds` | Histogram | `cell_id` | Cell별 응답 시간 |
| `selfhealing_cell_bulkhead_utilization` | Gauge | `cell_id` | Cell Bulkhead 사용률 |

### 4.1 Grafana 대시보드 쿼리 예시

```promql
# Cell별 건강도 현황
selfhealing_cell_health_score

# 건강도 임계치 이하 Cell
selfhealing_cell_health_score < 0.3

# Cell별 에러율
rate(selfhealing_cell_requests_total{status="error"}[5m])
/ rate(selfhealing_cell_requests_total[5m])

# Cell별 P99 레이턴시
histogram_quantile(0.99, rate(selfhealing_cell_request_duration_seconds_bucket[5m]))
```

---

## 5. CellEvacuationPolicy 연동점

`CellHealthAggregator.aggregate_all()` 실행 후, 건강도가 `evacuation_health_threshold` (기본 0.3) 이하인 Cell을 `CellEvacuationPolicy` (265)에 통보하는 방식:

```python
# aggregate_all() 내부에서 추가 (265에서 상세 구현)
for cell_id in registry.get_all_cells():
    score = self.compute_health(cell_id)
    registry.update_health_score(cell_id, score)
    if score <= settings.evacuation_health_threshold:
        # CellEvacuationPolicy가 처리
        self._notify_evacuation_needed(cell_id, score)
```

---

## 6. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `services/cell_topology/health.py` | 신규 생성 | 필수 |

**기존 파일 변경 없음** — `BulkheadRegistry.get_all_states()`, `CircuitBreakerService.get_all_states()` 모두 기존 API 그대로 사용.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정) |
| `262_CELL_REGISTRY.md` | `update_health_score()` 갱신 대상 |
| `263_CELL_TAGGER.md` | `record_request()` 호출 시점 제공 |
| `265_CELL_EVACUATION_POLICY.md` | 건강도 임계치 기반 대피 트리거 |
| `meta/health_probe.py` | 참조 (건강 프로브 패턴) |
| `resilience/bulkhead/registry.py` | `get_all_states()` (Bulkhead utilization 소스) |
| `metrics/recorders.py` | 참조 (Prometheus 메트릭 패턴) |
