# 264. Cell Health Aggregator — 건강도 집계 및 Prometheus 메트릭

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Implemented
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/health.py`

---

## 0. 요약

`CellHealthAggregator`는 Cell별 건강도를 집계하여 `CellRegistry.update_health_score()`에 반영하고, Prometheus 메트릭을 노출한다. 건강도가 임계치 이하로 떨어지면 `CellEvacuationPolicy` (265)가 대피를 트리거한다.

### 0.1 v2.0.0 아키텍처 결정 사항

| # | 문제 | 결정 | 근거 |
|---|------|------|------|
| R1 | 멀티 워커 메트릭 파편화 | Prometheus API를 글로벌 SSOT로, 로컬 EWMA를 폴백으로 | `prometheus_client` Counter/Histogram은 워커별 독립이므로 Prometheus의 `rate()`로 합산 |
| R2 | 누적 카운터 과거 데이터 영속 | Prometheus `rate()`로 Sliding Window 자동 해결, 폴백은 EWMA 감쇠 | `EWMAForecaster` (L332, `time_series.py`) 재사용 |
| R3 | 백그라운드 스레드 중복 실행 | `LeaderScheduler`로 단일 리더만 집계 | `DLQConsumerCoordinator` (L42, `dlq_consumer.py`) 동일 패턴 |
| R4 | CB-Cell 간접 매핑 불안정 | Phase 1: 콜백 이벤트 기반, Phase 2: CB metadata 확장 | [268_CB_METADATA.md](268_CB_METADATA.md) 별도 문서 |
| R5 | Health Score 플래핑 | 264는 Raw+EWMA 스무딩, 265는 히스테리시스 전담 | 측정(264)과 판단(265) 책임 분리 |

---

## 1. 설계 근거 — 기존 코드 패턴 분석

### 1.1 `HealthProbeManager` — Meta-Watchdog 건강 프로브

**파일**: `packages/selfhealing-python/src/selfhealing/meta/health_probe.py`

| 프로브 | 대상 | 메트릭 |
|--------|------|--------|
| `RedisProbe` (L284–L369) | Redis 연결, 메모리, 키 수 | ping latency, memory usage |
| `CircuitBreakerProbe` | CB 상태 (stuck OPEN 감지) | stuck_count |
| `DLQProbe` | DLQ 크기 | queue_size |
| `RecoveryPipelineProbe` | 파이프라인 상태 | active_recoveries |

`CellHealthAggregator`는 이 프로브들의 결과를 **Cell 단위로 집계**한다.

### 1.2 기존 메트릭 패턴 — `SelfHealingMetrics`

**파일**: `packages/selfhealing-python/src/selfhealing/metrics/prometheus.py`

기존 시스템은 `prometheus_client`를 사용하여 메트릭을 노출 (L161–L197):

```python
self.circuit_breaker_state = Gauge(
    f"{prefix}_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["service_name"],
)
self.circuit_breaker_transitions = Counter(
    f"{prefix}_circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    ["service_name", "from_state", "to_state"],
)
```

`CellHealthAggregator`도 동일 패턴으로 `cell_id` label을 추가한다.

### 1.3 `LeaderScheduler` — 리더 선출 기반 스케줄러

**파일**: `packages/selfhealing-python/src/selfhealing/coordination/scheduler.py`

`LeaderScheduler`는 Redis 기반 리더 선출(`RedisLeaderElector`)과 연동하여, **클러스터 내 단일 노드만** 주기적 작업을 실행하도록 보장한다 (L292–L322):

```python
def _scheduler_loop(self) -> None:
    while self._running and not self._stop_event.is_set():
        if not self._elector.is_leader():
            self._stop_event.wait(timeout=self._tick_interval)
            continue
        for job in self._jobs.values():
            if job.should_run():
                self._execute_job(job)
        self._stop_event.wait(timeout=self._tick_interval)
```

`@scheduler.job()` 데코레이터로 선언적 등록 가능 (L148–L181):

```python
@scheduler.job(interval_seconds=60)
def my_job():
    print("Running...")
```

**프로젝트 내 선례**: `DLQConsumerCoordinator` (L42, `dlq_consumer.py`)가 동일한 `get_leader_elector()` + 콜백 패턴을 사용하여 단일 노드 DLQ 소비를 보장.

`CellHealthAggregator`는 이 패턴을 사용하여 건강도 집계 루프의 중복 실행을 방지한다.

### 1.4 `EWMAForecaster` — 지수 이동 평균

**파일**: `packages/selfhealing-python/src/selfhealing/services/predictive_forecaster/time_series.py`

EWMA는 O(1) 메모리로 시계열 데이터를 평활화한다 (L332–L375):

```python
class EWMAForecaster:
    def __init__(self, alpha: float = 0.3):
        if not (0 < alpha <= 1):
            raise ValueError(f"alpha는 (0, 1] 범위여야 합니다: {alpha}")
        self._alpha = alpha
        self._ewma: float | None = None

    def update(self, value: float) -> float:
        if self._ewma is None:
            self._ewma = value
        else:
            self._ewma = self._alpha * value + (1 - self._alpha) * self._ewma
        return self._ewma
```

`CellHealthAggregator`는 두 곳에서 EWMA를 사용한다:

1. **Health Score 스무딩** — `compute_health()` 출력에 EWMA 적용하여 일시적 스파이크 완화
2. **폴백 에러율 추적** — Prometheus 불가용 시 로컬 EWMA로 에러율 감쇠

---

## 2. 건강도 산출 모델

### 2.1 2-Tier 메트릭 소스 아키텍처

멀티 워커 환경에서 각 워커의 로컬 카운터는 파편화되므로, **Prometheus를 글로벌 SSOT(Single Source of Truth)로, 로컬 EWMA를 폴백으로** 사용한다:

```
┌─────────────────────────────────────────────────────┐
│              record_request() — 모든 워커            │
│  ┌─────────────────────┐  ┌──────────────────────┐  │
│  │ Prometheus Counter   │  │ 로컬 EWMA (폴백)     │  │
│  │ .inc() / .observe()  │  │ alpha=0.3, O(1)      │  │
│  └──────────┬──────────┘  └──────────┬───────────┘  │
└─────────────┼───────────────────────-┼──────────────┘
              │                        │
     Prometheus 스크래핑               │
              │                        │
┌─────────────▼────────────────────────▼──────────────┐
│        aggregate_all() — 리더 워커만 실행             │
│  ┌────────────────────┐  ┌─────────────────────────┐│
│  │ Primary: PromQL     │  │ Fallback: 로컬 EWMA     ││
│  │ rate()[5m], p99     │──│ Prom 3회 연속 실패 시    ││
│  │ timeout=3s          │  │ 자동 전환               ││
│  └────────────────────┘  └─────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

### 2.2 입력 시그널

| 시그널 | Primary 소스 (Prometheus API) | Fallback 소스 (Local EWMA) | 가중치 |
|--------|------|--------|--------|
| `error_rate` | `rate(selfhealing_cell_requests_total{status="error"}[5m])` | `EWMAForecaster(alpha=0.3)` | 0.35 |
| `latency_p99` | `histogram_quantile(0.99, rate(..._bucket[5m]))` | `EWMAForecaster(alpha=0.3)` | 0.25 |
| `bulkhead_utilization` | `BulkheadRegistry.get_all_states()` (로컬 메모리) | 동일 | 0.20 |
| `circuit_breaker_open_ratio` | CB 상태 변경 콜백 기반 카운터 | 동일 | 0.20 |

### 2.3 건강도 공식

```
raw_health_score = 1.0
                 - (error_rate_normalized × 0.35)
                 - (latency_normalized × 0.25)
                 - (bulkhead_utilization × 0.20)
                 - (cb_open_ratio × 0.20)

# EWMA 스무딩 적용 (alpha=0.3)
health_score = EWMA(raw_health_score)

# 범위: 0.0 (완전 장애) ~ 1.0 (완전 건강)
```

### 2.4 정규화

```python
error_rate_normalized = min(error_rate / 0.5, 1.0)      # 50% 에러율 = 1.0
latency_normalized = min(latency_p99 / 5.0, 1.0)        # 5초 p99 = 1.0
bulkhead_utilization = active_count / max_concurrent     # 직접 비율
cb_open_ratio = open_cb_count / total_cb_count           # 직접 비율
```

### 2.5 Minimum Sample Size 가드

저트래픽 Cell에서 1~2건의 에러로 건강도가 급락하는 것을 방지한다:

```python
MIN_SAMPLES_FOR_PENALTY = 10

# 최근 5분 요청이 10건 미만이면 에러율 페널티 면제
if total_requests < MIN_SAMPLES_FOR_PENALTY:
    error_norm = 0.0
```

---

## 3. CellHealthAggregator 구현

```python
"""
Cell Health Aggregator — Cell별 건강도 집계.

Prometheus API를 글로벌 SSOT로 사용하고,
BulkheadRegistry·CB 상태 콜백을 수집하여 Cell 단위 건강도를 산출합니다.
LeaderScheduler를 통해 클러스터 내 단일 리더만 집계를 수행합니다.

의존성:
- CellRegistry: 건강도 업데이트 대상 (registry.py L200)
- BulkheadRegistry: Bulkhead utilization 조회 (registry.py L316)
- LeaderScheduler: 단일 리더 실행 보장 (scheduler.py L83)
- EWMAForecaster: 폴백 에러율 + 스코어 평활화 (time_series.py L332)
- prometheus_client: 메트릭 노출 (선택적)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus API 설정
# ---------------------------------------------------------------------------
_PROMETHEUS_TIMEOUT = 3.0  # 초 — aggregate_all 블로킹 방지
_PROMETHEUS_MAX_CONSECUTIVE_FAILURES = 3  # 연속 실패 시 폴백 모드 전환


@dataclass
class CellHealthSnapshot:
    """Cell 건강 상태 스냅샷."""

    cell_id: str
    health_score: float            # EWMA 평활화된 최종 스코어
    raw_health_score: float = 0.0  # 평활화 이전 원시 스코어 (디버깅·감사용)
    error_rate: float = 0.0
    latency_p99: float = 0.0
    bulkhead_utilization: float = 0.0
    cb_open_ratio: float = 0.0
    source: str = "prometheus"     # "prometheus" | "ewma_fallback"
    timestamp: float = field(default_factory=time.time)


class CellHealthAggregator:
    """
    Cell별 건강도 집계.

    LeaderScheduler를 통해 클러스터 내 단일 리더 워커만 집계를 실행합니다.
    각 워커의 record_request()는 Prometheus Counter/Histogram에 기록하며,
    리더의 aggregate_all()이 Prometheus API에서 글로벌 합산 메트릭을 조회합니다.

    사용 (LeaderScheduler 데코레이터):
        from selfhealing.coordination.scheduler import get_leader_scheduler

        scheduler = get_leader_scheduler("cell-health-aggregator")

        @scheduler.job(interval_seconds=10)
        def cell_health_aggregation():
            aggregator = get_cell_health_aggregator()
            aggregator.aggregate_all()

    사용 (수동):
        aggregator = CellHealthAggregator()
        aggregator.record_request(cell_id, success=True, latency=0.05)
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

    # 저트래픽 보호
    MIN_SAMPLES_FOR_PENALTY = 10  # 최소 요청 수 미달 시 에러율 페널티 면제

    def __init__(self, settings: Any = None, prometheus_url: str | None = None):
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._lock = threading.RLock()
        self._snapshots: dict[str, CellHealthSnapshot] = {}

        # Prometheus API 엔드포인트
        self._prometheus_url = prometheus_url or "http://localhost:9090"
        self._prometheus_consecutive_failures = 0

        # EWMA 폴백 — 에러율·레이턴시 추적 (워커 로컬, O(1) 메모리)
        self._error_rate_ewma: dict[str, Any] = {}   # cell_id → EWMAForecaster
        self._latency_ewma: dict[str, Any] = {}      # cell_id → EWMAForecaster
        self._local_request_counts: dict[str, int] = {}  # 폴백 총 요청 수 추적

        # Health Score 스무딩 — Raw Score에 EWMA 적용
        self._health_ewma: dict[str, Any] = {}  # cell_id → EWMAForecaster

        # CB 상태 변경 콜백 기반 카운터
        self._cb_open_counts: dict[str, int] = {}
        self._cb_total_counts: dict[str, int] = {}

        # Leader Handoff 감지
        self._leader_since: float | None = None

        # Prometheus 메트릭 (선택적)
        self._metrics = self._init_metrics()

    def _init_metrics(self) -> dict[str, Any] | None:
        """Prometheus 메트릭 초기화 — Raw·Smoothed 이중 Gauge 포함."""
        if not self._settings.metrics_enabled:
            return None
        try:
            from prometheus_client import Counter, Gauge, Histogram

            return {
                "health_score": Gauge(
                    "selfhealing_cell_health_score",
                    "Cell health score after EWMA smoothing (0.0~1.0)",
                    ["cell_id"],
                ),
                "health_score_raw": Gauge(
                    "selfhealing_cell_health_score_raw",
                    "Cell health score before EWMA smoothing (0.0~1.0)",
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
                "health_data_source": Gauge(
                    "selfhealing_cell_health_data_source",
                    "Health data source (1=prometheus, 0=ewma_fallback)",
                    ["cell_id"],
                ),
            }
        except ImportError:
            logger.debug("prometheus_client not available, metrics disabled")
            return None

    # =========================================================================
    # record_request() — 모든 워커에서 호출
    # =========================================================================

    def record_request(
        self, cell_id: str, success: bool, latency: float
    ) -> None:
        """
        요청 결과 기록.

        CellTaggingMiddleware 또는 뷰에서 호출합니다.
        Prometheus Counter/Histogram에 기록(글로벌 집계용)하고,
        로컬 EWMA에도 업데이트(폴백용)합니다.

        Args:
            cell_id: Cell 식별자
            success: 요청 성공 여부
            latency: 응답 시간(초)
        """
        # 1) Prometheus 메트릭 — 글로벌 집계용 (항상 기록)
        if self._metrics:
            status = "success" if success else "error"
            self._metrics["request_total"].labels(
                cell_id=cell_id, status=status
            ).inc()
            self._metrics["request_duration"].labels(
                cell_id=cell_id
            ).observe(latency)

        # 2) 로컬 EWMA — 폴백용 (O(1) 메모리)
        with self._lock:
            # 에러율 EWMA
            if cell_id not in self._error_rate_ewma:
                from selfhealing.services.predictive_forecaster.time_series import (
                    EWMAForecaster,
                )
                self._error_rate_ewma[cell_id] = EWMAForecaster(alpha=0.3)
                self._latency_ewma[cell_id] = EWMAForecaster(alpha=0.3)

            self._error_rate_ewma[cell_id].update(0.0 if success else 1.0)
            self._latency_ewma[cell_id].update(latency)
            self._local_request_counts[cell_id] = (
                self._local_request_counts.get(cell_id, 0) + 1
            )

    # =========================================================================
    # Prometheus API 쿼리 — 리더 워커만 사용
    # =========================================================================

    def _fetch_prometheus_metrics(
        self, cell_id: str
    ) -> tuple[float, float, int] | None:
        """
        Prometheus HTTP API에서 글로벌 에러율과 P99 레이턴시를 조회.

        timeout=3초로 aggregate_all 블로킹을 방지합니다.
        연속 3회 실패 시 폴백 모드로 전환됩니다.

        Returns:
            (error_rate, latency_p99, total_requests) 또는 실패 시 None
        """
        # Mini Circuit Breaker — 연속 실패 시 API 호출 건너뜀
        if (self._prometheus_consecutive_failures
                >= _PROMETHEUS_MAX_CONSECUTIVE_FAILURES):
            return None

        try:
            import httpx

            # Error Rate 쿼리
            error_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={
                    "query": (
                        f'sum(rate(selfhealing_cell_requests_total'
                        f'{{cell_id="{cell_id}",status="error"}}[5m]))'
                    )
                },
                timeout=_PROMETHEUS_TIMEOUT,
            )
            total_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={
                    "query": (
                        f'sum(rate(selfhealing_cell_requests_total'
                        f'{{cell_id="{cell_id}"}}[5m]))'
                    )
                },
                timeout=_PROMETHEUS_TIMEOUT,
            )
            p99_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={
                    "query": (
                        f'histogram_quantile(0.99, sum(rate('
                        f'selfhealing_cell_request_duration_seconds_bucket'
                        f'{{cell_id="{cell_id}"}}[5m])) by (le))'
                    )
                },
                timeout=_PROMETHEUS_TIMEOUT,
            )

            error_rate_val = self._parse_prometheus_scalar(error_resp)
            total_rate_val = self._parse_prometheus_scalar(total_resp)
            p99_val = self._parse_prometheus_scalar(p99_resp)

            error_rate = (
                (error_rate_val / total_rate_val)
                if total_rate_val > 0 else 0.0
            )
            # total_requests 추정 (5분 rate × 300초)
            total_requests = int(total_rate_val * 300)

            self._prometheus_consecutive_failures = 0
            return error_rate, p99_val, total_requests

        except Exception as e:
            self._prometheus_consecutive_failures += 1
            logger.warning(
                f"Prometheus API failed for {cell_id} "
                f"(consecutive={self._prometheus_consecutive_failures}): {e}"
            )
            return None

    @staticmethod
    def _parse_prometheus_scalar(resp: Any) -> float:
        """Prometheus instant query 응답에서 스칼라 값 추출."""
        data = resp.json()
        if data.get("status") == "success":
            result = data.get("data", {}).get("result", [])
            if result:
                return float(result[0]["value"][1])
        return 0.0

    # =========================================================================
    # compute_health() — 건강도 산출
    # =========================================================================

    def compute_health(self, cell_id: str) -> float:
        """
        Cell 건강도 산출.

        Primary: Prometheus API에서 글로벌 메트릭 조회
        Fallback: 로컬 EWMA (Prometheus 불가용 시)

        Args:
            cell_id: Cell 식별자

        Returns:
            EWMA 평활화된 건강도 (0.0~1.0)
        """
        # 1) Error Rate & Latency P99 — 2-Tier 소스
        prom_result = self._fetch_prometheus_metrics(cell_id)
        if prom_result is not None:
            error_rate, latency_p99, total_requests = prom_result
            source = "prometheus"
        else:
            # 폴백: 로컬 EWMA
            with self._lock:
                ewma = self._error_rate_ewma.get(cell_id)
                error_rate = ewma.get_smoothed() if ewma else 0.0
                lat_ewma = self._latency_ewma.get(cell_id)
                latency_p99 = lat_ewma.get_smoothed() if lat_ewma else 0.0
                total_requests = self._local_request_counts.get(cell_id, 0)
            source = "ewma_fallback"

        # 2) Bulkhead Utilization (로컬 메모리)
        bulkhead_util = self._get_bulkhead_utilization(cell_id)

        # 3) CB Open Ratio (콜백 기반 카운터)
        cb_open_ratio = self._get_cb_open_ratio(cell_id)

        # 4) 정규화 + Minimum Sample Size 가드
        if total_requests < self.MIN_SAMPLES_FOR_PENALTY:
            error_norm = 0.0  # 표본 부족 → 에러율 페널티 면제
        else:
            error_norm = min(error_rate / self.MAX_ERROR_RATE, 1.0)
        latency_norm = min(latency_p99 / self.MAX_LATENCY_P99, 1.0)

        # 5) 가중 합산 → Raw Health Score
        penalty = (
            error_norm * self.WEIGHT_ERROR_RATE
            + latency_norm * self.WEIGHT_LATENCY
            + bulkhead_util * self.WEIGHT_BULKHEAD
            + cb_open_ratio * self.WEIGHT_CB_OPEN
        )
        raw_health = max(0.0, 1.0 - penalty)

        # 6) EWMA 스무딩
        if cell_id not in self._health_ewma:
            from selfhealing.services.predictive_forecaster.time_series import (
                EWMAForecaster,
            )
            self._health_ewma[cell_id] = EWMAForecaster(alpha=0.3)
        smoothed_health = self._health_ewma[cell_id].update(raw_health)

        # 7) 스냅샷 저장 — Raw + Smoothed 모두 기록
        self._snapshots[cell_id] = CellHealthSnapshot(
            cell_id=cell_id,
            health_score=smoothed_health,
            raw_health_score=raw_health,
            error_rate=error_rate,
            latency_p99=latency_p99,
            bulkhead_utilization=bulkhead_util,
            cb_open_ratio=cb_open_ratio,
            source=source,
        )

        # 8) Prometheus Gauge 업데이트 — Raw + Smoothed 이중 노출
        if self._metrics:
            self._metrics["health_score"].labels(
                cell_id=cell_id
            ).set(smoothed_health)
            self._metrics["health_score_raw"].labels(
                cell_id=cell_id
            ).set(raw_health)
            self._metrics["bulkhead_utilization"].labels(
                cell_id=cell_id
            ).set(bulkhead_util)
            self._metrics["health_data_source"].labels(
                cell_id=cell_id
            ).set(1.0 if source == "prometheus" else 0.0)

        return smoothed_health

    def _get_bulkhead_utilization(self, cell_id: str) -> float:
        """
        BulkheadRegistry에서 Cell Bulkhead 사용률 조회.

        BulkheadRegistry.get_all_states() 사용 (로컬 메모리, L316 registry.py).
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
        """
        CB 상태 변경 콜백 기반 Cell CB OPEN 비율 조회.

        Phase 1: _state_change_callbacks 기반 이벤트 카운터.
            - CB 자동 전환(record_failure→OPEN, record_success→CLOSED) 감지.
            - 수동 제어(force_open/force_close)는 콜백 미호출
              (manual_control.py L69–L189 확인).
              → Phase 2에서 CB metadata 확장으로 해결 (268_CB_METADATA.md).

        Phase 2: CircuitBreakerStateData.metadata["cell_id"] 기반 직접 조회.
            → 268_CB_METADATA.md 참조.
        """
        with self._lock:
            total = self._cb_total_counts.get(cell_id, 0)
            open_count = self._cb_open_counts.get(cell_id, 0)
            return (open_count / total) if total > 0 else 0.0

    def register_cb_callbacks(self) -> None:
        """
        CircuitBreakerService 상태 변경 콜백 등록.

        _state_change_callbacks (service.py L96–L100) 활용:
          self._state_change_callbacks = {
              "open": [],
              "closed": [],
              "half_open": [],
          }

        콜백은 동기 실행 (service.py L150–L172)이므로 내부 로직을
        최대한 가볍게(dict increment만) 유지합니다.
        """
        try:
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
            )
            from selfhealing.services.cell_topology import get_cell_registry

            cb_service = get_circuit_breaker_service()
            registry = get_cell_registry()

            def _on_cb_opened(
                service_name: str, old_state: str, new_state: str
            ) -> None:
                cell_id = registry.get_cell_for_key(service_name)
                with self._lock:
                    self._cb_open_counts[cell_id] = (
                        self._cb_open_counts.get(cell_id, 0) + 1
                    )
                    self._cb_total_counts[cell_id] = (
                        self._cb_total_counts.get(cell_id, 0) + 1
                    )

            def _on_cb_closed(
                service_name: str, old_state: str, new_state: str
            ) -> None:
                cell_id = registry.get_cell_for_key(service_name)
                with self._lock:
                    self._cb_open_counts[cell_id] = max(
                        0, self._cb_open_counts.get(cell_id, 0) - 1
                    )

            cb_service._state_change_callbacks["open"].append(_on_cb_opened)
            cb_service._state_change_callbacks["closed"].append(_on_cb_closed)

            logger.info(
                "[CellHealthAggregator] CB state change callbacks registered"
            )
        except Exception as e:
            logger.warning(
                f"[CellHealthAggregator] CB callback registration failed: {e}"
            )

    # =========================================================================
    # Snapshot 조회
    # =========================================================================

    def get_snapshot(self, cell_id: str) -> CellHealthSnapshot | None:
        """최근 건강 스냅샷 조회."""
        return self._snapshots.get(cell_id)

    def get_all_snapshots(self) -> dict[str, CellHealthSnapshot]:
        """모든 Cell 건강 스냅샷 조회."""
        return dict(self._snapshots)

    # =========================================================================
    # aggregate_all() — LeaderScheduler에서 호출
    # =========================================================================

    def aggregate_all(self) -> None:
        """
        모든 Cell 건강도 일괄 산출 및 CellRegistry 갱신.

        LeaderScheduler의 @scheduler.job()을 통해
        클러스터 내 단일 리더 워커만 주기적으로 호출합니다.
        """
        is_warming = False
        if self._leader_since is not None:
            elapsed = time.monotonic() - self._leader_since
            warmup_window = self._settings.health_check_interval_seconds * 2
            if elapsed < warmup_window:
                is_warming = True
                logger.info(
                    "[CellHealthAggregator] Leader warmup period "
                    f"({elapsed:.1f}s < {warmup_window}s), "
                    "scores may be volatile"
                )

        try:
            from selfhealing.services.cell_topology import get_cell_registry

            registry = get_cell_registry()
            for cell_id in registry.get_all_cells():
                score = self.compute_health(cell_id)
                registry.update_health_score(cell_id, score)
        except Exception as e:
            logger.error(f"CellHealthAggregator aggregate_all failed: {e}")

    def on_become_leader(self) -> None:
        """리더 전환 시 호출 — warmup 시작 시각 기록."""
        self._leader_since = time.monotonic()
        logger.info(
            "[CellHealthAggregator] Became leader, "
            "EWMA fallback data may be cold"
        )

    def on_lose_leader(self) -> None:
        """리더십 상실 시 호출."""
        self._leader_since = None
        logger.info("[CellHealthAggregator] Lost leadership")


# =============================================================================
# LeaderScheduler 통합 — 진입점
# =============================================================================

_aggregator: CellHealthAggregator | None = None
_aggregator_lock = threading.Lock()


def get_cell_health_aggregator() -> CellHealthAggregator:
    """CellHealthAggregator 싱글톤 반환."""
    global _aggregator
    if _aggregator is None:
        with _aggregator_lock:
            if _aggregator is None:
                _aggregator = CellHealthAggregator()
    return _aggregator


def setup_cell_health_scheduler() -> None:
    """
    LeaderScheduler 기반 건강도 집계 루프 등록.

    AppConfig.ready() 또는 startup에서 호출합니다.

    패턴 참조:
    - LeaderScheduler (scheduler.py L83–L390)
    - DLQConsumerCoordinator (dlq_consumer.py L42–L100)
    """
    from selfhealing.settings.cell_topology import get_cell_topology_settings

    settings = get_cell_topology_settings()
    if not settings.enabled:
        return

    from selfhealing.coordination.scheduler import get_leader_scheduler

    aggregator = get_cell_health_aggregator()
    aggregator.register_cb_callbacks()

    scheduler = get_leader_scheduler("cell-health-aggregator")

    # 리더 전환 이벤트 감지 — warmup 컨텍스트 기록
    scheduler._elector.on_become_leader(aggregator.on_become_leader)
    scheduler._elector.on_lose_leader(aggregator.on_lose_leader)

    @scheduler.job(
        interval_seconds=settings.health_check_interval_seconds,
        name="cell-health-aggregation",
    )
    def _aggregate():
        aggregator.aggregate_all()

    scheduler.start()
    logger.info(
        f"[CellHealthAggregator] LeaderScheduler registered "
        f"(interval={settings.health_check_interval_seconds}s)"
    )


def reset_cell_health_aggregator() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _aggregator
    with _aggregator_lock:
        _aggregator = None
```

---

## 4. Prometheus 메트릭 사양

| 메트릭 | 유형 | Labels | 설명 |
|--------|------|--------|------|
| `selfhealing_cell_health_score` | Gauge | `cell_id` | **EWMA 평활화된** Cell 건강도 (0.0~1.0) |
| `selfhealing_cell_health_score_raw` | Gauge | `cell_id` | **평활화 이전** Raw Cell 건강도 (0.0~1.0) |
| `selfhealing_cell_requests_total` | Counter | `cell_id`, `status` | Cell별 요청 수 |
| `selfhealing_cell_request_duration_seconds` | Histogram | `cell_id` | Cell별 응답 시간 |
| `selfhealing_cell_bulkhead_utilization` | Gauge | `cell_id` | Cell Bulkhead 사용률 |
| `selfhealing_cell_health_data_source` | Gauge | `cell_id` | 데이터 소스 (1=Prometheus, 0=EWMA 폴백) |

### 4.1 Grafana 대시보드 쿼리 예시

```promql
# Cell별 건강도 현황 (Smoothed vs Raw 오버레이)
selfhealing_cell_health_score
selfhealing_cell_health_score_raw

# 건강도 임계치 이하 Cell
selfhealing_cell_health_score < 0.3

# Cell별 에러율 (글로벌 합산)
sum(rate(selfhealing_cell_requests_total{status="error"}[5m])) by (cell_id)
/ sum(rate(selfhealing_cell_requests_total[5m])) by (cell_id)

# Cell별 P99 레이턴시
histogram_quantile(0.99,
  sum(rate(selfhealing_cell_request_duration_seconds_bucket[5m])) by (cell_id, le)
)

# 데이터 소스 폴백 감지 경보
selfhealing_cell_health_data_source == 0
```

### 4.2 SRE 대시보드 권장 패널 구성

```
┌─────────────────────────────────────────────────────────┐
│  Panel: Cell Health Overview                             │
│  ┌──────────────────────────────────────────────────┐   │
│  │  ── selfhealing_cell_health_score (파란 실선)     │   │
│  │  ── selfhealing_cell_health_score_raw (빨간 점선)  │   │
│  │  ── evacuation_threshold 0.3 (수평 경계선)        │   │
│  │  ── recovery_threshold 0.7 (수평 경계선)          │   │
│  └──────────────────────────────────────────────────┘   │
│  Raw 지표의 순간적 튐 vs Smoothed 지표의 안정적 추세를     │
│  비교하여 플래핑 현상 및 대피 트리거의 정당성을 분석합니다.  │
└─────────────────────────────────────────────────────────┘
```

---

## 5. CellEvacuationPolicy 연동점

### 5.1 책임 분리: 측정(264) vs 판단(265)

| 레이어 | 담당 | 비유 |
|--------|------|------|
| **264 (Health Aggregator)** | Raw Score 산출 + EWMA 스무딩 | 온도계 |
| **265 (Evacuation Policy)** | 히스테리시스 기반 대피/복구 결정 | 자동온도조절기 |

264는 **순수하게 Smoothed Score를 제공**하고, 대피 여부 판단은 265에 전적으로 위임한다.

### 5.2 265가 처리할 히스테리시스 패턴 (참조)

```python
# 265에서 구현 — 264는 score만 제공
EVACUATE_THRESHOLD = 0.3
RECOVER_THRESHOLD = 0.7
EVACUATE_CONSECUTIVE = 3   # 연속 3회 이하 시 대피
RECOVER_CONSECUTIVE = 5    # 연속 5회 이상 시 복구

# 데드존 (0.3~0.7): 상태 전환 없음
# 비대칭: 대피(빠르게) vs 복구(보수적)
```

### 5.3 aggregate_all()에서의 연동

```python
# aggregate_all() 내부에서 추가 (265에서 상세 구현)
for cell_id in registry.get_all_cells():
    score = self.compute_health(cell_id)
    registry.update_health_score(cell_id, score)
    if score <= settings.evacuation_health_threshold:
        # CellEvacuationPolicy가 히스테리시스 판단 후 처리
        self._notify_evacuation_needed(cell_id, score)
```

---

## 6. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `services/cell_topology/health.py` | 신규 생성 | 필수 |

**기존 파일 변경 없음** — `BulkheadRegistry.get_all_states()` (L316, registry.py), `CircuitBreakerService._state_change_callbacks` (L96, service.py) 모두 기존 API 그대로 사용.

---

## 7. 리뷰 반영 추적

### 7.1 R1 — 멀티 워커 메트릭 파편화 해결

| 항목 | v1.0.0 (기존) | v2.0.0 (개선) |
|------|---------------|---------------|
| 에러율 소스 | `_request_counts` / `_error_counts` (워커 로컬 누적) | Prometheus `rate()[5m]` (글로벌 합산) |
| 레이턴시 소스 | `_latency_samples` (최근 1000개 로컬 리스트) | Prometheus `histogram_quantile(0.99, ...)` |
| 폴백 | 없음 | `EWMAForecaster(alpha=0.3)` — O(1) 메모리 |
| Prom API 보호 | 없음 | timeout=3s + 연속 3회 실패 시 폴백 전환 (Mini CB) |

**근거**: `prometheus_client` Counter는 워커별 독립 타임시리즈를 생성하므로, Prometheus `sum(rate(...))` PromQL이 **자동으로 20개 워커의 메트릭을 합산**한다.

### 7.2 R2 — 시간 윈도우 문제 해결

| 항목 | v1.0.0 (기존) | v2.0.0 (개선) |
|------|---------------|---------------|
| 에러율 계산 | `errors / total` (구동 이후 전체 누적) | `rate()[5m]` (최근 5분 Sliding Window) |
| 과거 데이터 | 영원히 반영 | 5분 후 자동 소멸 |
| 폴백 감쇠 | 없음 | EWMA alpha=0.3 → `new×0.3 + old×0.7` 자연 감쇠 |
| 저트래픽 보호 | 없음 | `MIN_SAMPLES_FOR_PENALTY = 10` 가드 |

**근거**: `EWMAForecaster` (L332, `time_series.py`)는 프로젝트 내 `BudgetDepletionForecaster`와 `ZScoreDetector`에서 이미 검증된 패턴.

### 7.3 R3 — 백그라운드 스레드 중복 실행 제거

| 항목 | v1.0.0 (기존) | v2.0.0 (개선) |
|------|---------------|---------------|
| 실행 주체 | 모든 워커에서 `threading.Thread` 각자 실행 | `LeaderScheduler` — 단일 리더만 실행 |
| 리더 선출 | 없음 | `RedisLeaderElector` + Fencing Token (L51, redis_elector.py) |
| 중복 대피 트리거 | 20개 워커가 각자 트리거 | 리더 1개만 트리거 |
| Graceful Shutdown | `_stop_event.set()` 수동 | `register_for_graceful_shutdown()` 자동 (SIGTERM/SIGINT) |
| Leader Handoff 감지 | 없음 | `on_become_leader()` / `on_lose_leader()` + warmup 로깅 |

**근거**: `DLQConsumerCoordinator` (L42, `dlq_consumer.py`)가 동일한 `get_leader_elector()` + 콜백 패턴을 사용하여 단일 노드 DLQ 소비를 보장. `LeaderScheduler`의 `_scheduler_loop()` (L292)는 매 tick마다 `is_leader()` 확인 후 job 실행.

**Celery Beat를 선택하지 않은 이유**: `BulkheadRegistry.get_all_states()` (L316, `bulkhead/registry.py`)는 **`self._bulkheads: dict[str, Bulkhead]` 로컬 메모리**에만 존재한다. Celery Beat 태스크는 별도 프로세스에서 실행되므로 이 데이터에 접근할 수 없다. 반면 `LeaderScheduler`는 Django 워커 프로세스 내부에서 실행되므로 로컬 상태에 직접 접근 가능하다.

### 7.4 R4 — CB-Cell 간접 매핑 개선

| 항목 | v1.0.0 (기존) | v2.0.0 Phase 1 | Phase 2 (268) |
|------|---------------|-----------------|---------------|
| 매핑 방식 | `assigned_services` + `get_all_states()` 루프 | CB 상태 변경 콜백 이벤트 | CB `metadata["cell_id"]` 직접 조회 |
| 복잡도 | O(CB수 × 서비스수) | O(1) dict increment | O(N) with cell_id 필터 |
| TTL 레이스 | `assigned_services` evict 시 매핑 소실 | `get_cell_for_key()` 런타임 resolve | CB 자체에 cell_id 영구 기록 |
| 수동 제어 감지 | 감지 가능 (`get_all_states()` 전수 조회) | ❌ `force_open` 콜백 미호출 | ✅ metadata에서 직접 조회 |

**Phase 1 한계**: `ManualControlMixin.force_open()` / `force_close()` (L69–L267, `manual_control.py`)는 `_invoke_state_change_callbacks()`를 호출하지 않으므로 수동 CB 제어를 감지할 수 없다. **Phase 2** (268_CB_METADATA.md)에서 `CircuitBreakerStateData.metadata` 확장으로 해결한다.

### 7.5 R5 — 플래핑 방지 및 관측가능성

| 항목 | v1.0.0 (기존) | v2.0.0 (개선) |
|------|---------------|---------------|
| Health Score | Raw 단일 값 | Raw + EWMA Smoothed 이중 산출 |
| Prometheus 노출 | `selfhealing_cell_health_score` 1개 | `_score` (Smoothed) + `_score_raw` 2개 |
| 스무딩 | 없음 | `EWMAForecaster(alpha=0.3)` |
| 대피 판단 | 264에서 임계치 직접 비교 | 265에서 히스테리시스 전담 (데드존+비대칭 임계치) |
| SRE 가시성 | 단일 메트릭 | Raw vs Smoothed 오버레이 + 데이터 소스 Gauge |

---

## 8. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정) |
| `262_CELL_REGISTRY.md` | `update_health_score()` 갱신 대상 |
| `263_CELL_TAGGER.md` | `record_request()` 호출 시점 제공 |
| `265_CELL_EVACUATION_POLICY.md` | 건강도 임계치 기반 대피 트리거 (히스테리시스 전담) |
| `268_CB_METADATA.md` | Phase 2: CB metadata 확장 (R4 완전 해결) |
| `meta/health_probe.py` | 참조 (건강 프로브 패턴) |
| `coordination/scheduler.py` | `LeaderScheduler` (R3 단일 리더 보장) |
| `coordination/dlq_consumer.py` | 참조 (Leader Election 동일 패턴) |
| `predictive_forecaster/time_series.py` | `EWMAForecaster` (R2/R5 스무딩) |
| `resilience/bulkhead/registry.py` | `get_all_states()` (Bulkhead utilization 소스) |
| `metrics/prometheus.py` | 참조 (Prometheus 메트릭 패턴) |
