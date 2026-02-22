"""
Cell Health Aggregator — Cell별 건강도 집계.

Prometheus API를 글로벌 SSOT로 사용하고,
BulkheadRegistry·CB 상태 콜백을 수집하여 Cell 단위 건강도를 산출합니다.
LeaderScheduler를 통해 클러스터 내 단일 리더만 집계를 수행합니다.

의존성:
- CellRegistry: 건강도 업데이트 대상 (registry.py update_health_score)
- BulkheadRegistry: Bulkhead utilization 조회 (bulkhead/registry.py get_all_states)
- LeaderScheduler: 단일 리더 실행 보장 (coordination/scheduler.py)
- EWMAForecaster: 폴백 에러율 + 스코어 평활화 (time_series.py)
- prometheus_client: 메트릭 노출 (선택적)
"""

from __future__ import annotations

import structlog
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.predictive_forecaster.time_series import (
        EWMAForecaster,
    )

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Prometheus API 설정
# ---------------------------------------------------------------------------
_PROMETHEUS_TIMEOUT = 3.0  # 초 — aggregate_all 블로킹 방지
_PROMETHEUS_MAX_CONSECUTIVE_FAILURES = 3  # 연속 실패 시 폴백 모드 전환
_PROMETHEUS_RETRY_AFTER_SECONDS = 60.0  # 폴백 모드 진입 후 half-open probe 재시도 대기


@dataclass
class CellHealthSnapshot:
    """Cell 건강 상태 스냅샷."""

    cell_id: str
    health_score: float  # EWMA 평활화된 최종 스코어
    raw_health_score: float = 0.0  # 평활화 이전 원시 스코어 (디버깅·감사용)
    error_rate: float = 0.0
    latency_p99: float = 0.0
    bulkhead_utilization: float = 0.0
    cb_open_ratio: float = 0.0
    source: str = "prometheus"  # "prometheus" | "ewma_fallback"
    timestamp: float = field(default_factory=time.time)


class CellHealthAggregator:
    """
    Cell별 건강도 집계.

    LeaderScheduler를 통해 클러스터 내 단일 리더 워커만 집계를 실행합니다.
    각 워커의 record_request()는 Prometheus Counter/Histogram에 기록하며,
    리더의 aggregate_all()이 Prometheus API에서 글로벌 합산 메트릭을 조회합니다.

    사용 (LeaderScheduler 데코레이터)::

        from selfhealing.coordination.scheduler import get_leader_scheduler

        scheduler = get_leader_scheduler("cell-health-aggregator")

        @scheduler.job(interval_seconds=10)
        def cell_health_aggregation():
            aggregator = get_cell_health_aggregator()
            aggregator.aggregate_all()

    사용 (수동)::

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
    MAX_ERROR_RATE = 0.5  # 50% 에러율 = 완전 비정상
    MAX_LATENCY_P99 = 5.0  # 5초 = 완전 비정상

    # 저트래픽 보호
    MIN_SAMPLES_FOR_PENALTY = 10  # 최소 요청 수 미달 시 에러율 페널티 면제

    def __init__(
        self,
        settings: Any = None,
        prometheus_url: str | None = None,
    ):
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        self._settings = settings or get_cell_topology_settings()
        self._lock = threading.RLock()
        self._snapshots: dict[str, CellHealthSnapshot] = {}

        # Prometheus API 엔드포인트 (설정 → 생성자 인자 → 기본값 순 우선)
        self._prometheus_url = prometheus_url or getattr(self._settings, "prometheus_url", None) or "http://localhost:9090"
        self._prometheus_consecutive_failures = 0
        self._last_prometheus_failure_time: float = 0.0

        # EWMA 폴백 — 에러율·레이턴시 추적 (워커 로컬, O(1) 메모리)
        self._error_rate_ewma: dict[str, EWMAForecaster] = {}
        self._latency_ewma: dict[str, EWMAForecaster] = {}
        self._local_request_counts: dict[str, int] = {}  # 폴백 총 요청 수 추적

        # Health Score 스무딩 — Raw Score에 EWMA 적용
        self._health_ewma: dict[str, EWMAForecaster] = {}

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
            logger.debug("available_metrics_disabled")
            return None

    # =========================================================================
    # record_request() — 모든 워커에서 호출
    # =========================================================================

    def record_request(self, cell_id: str, success: bool, latency: float) -> None:
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
            self._metrics["request_total"].labels(cell_id=cell_id, status=status).inc()
            self._metrics["request_duration"].labels(cell_id=cell_id).observe(latency)

        # 2) 로컬 EWMA — 폴백용 (O(1) 메모리)
        with self._lock:
            if cell_id not in self._error_rate_ewma:
                from selfhealing.services.predictive_forecaster.time_series import (
                    EWMAForecaster,
                )

                self._error_rate_ewma[cell_id] = EWMAForecaster(alpha=0.3)
                self._latency_ewma[cell_id] = EWMAForecaster(alpha=0.3)

            self._error_rate_ewma[cell_id].update(0.0 if success else 1.0)
            self._latency_ewma[cell_id].update(latency)
            self._local_request_counts[cell_id] = self._local_request_counts.get(cell_id, 0) + 1

    # =========================================================================
    # Prometheus API 쿼리 — 리더 워커만 사용
    # =========================================================================

    def _fetch_prometheus_metrics(self, cell_id: str) -> tuple[float, float, int] | None:
        """
        Prometheus HTTP API에서 글로벌 에러율과 P99 레이턴시를 조회.

        timeout=3초로 aggregate_all 블로킹을 방지합니다.
        연속 3회 실패 시 폴백 모드로 전환됩니다.

        Returns:
            (error_rate, latency_p99, total_requests) 또는 실패 시 None
        """
        # Mini Circuit Breaker (half-open 포함)
        # 연속 실패 임계값 도달 시 폴백 모드, 일정 시간 후 1회 probe 시도
        if self._prometheus_consecutive_failures >= _PROMETHEUS_MAX_CONSECUTIVE_FAILURES:
            elapsed_since_failure = time.monotonic() - self._last_prometheus_failure_time
            if elapsed_since_failure < _PROMETHEUS_RETRY_AFTER_SECONDS:
                return None
            logger.info(
                "[CellHealthAggregator] Prometheus half-open probe " "(%.1fs since last failure)",
                elapsed_since_failure,
            )

        try:
            import httpx

            # Error Rate 쿼리
            error_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={
                    "query": (f"sum(rate(selfhealing_cell_requests_total" f'{{cell_id="{cell_id}",status="error"}}[5m]))')
                },
                timeout=_PROMETHEUS_TIMEOUT,
            )
            total_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={"query": (f"sum(rate(selfhealing_cell_requests_total" f'{{cell_id="{cell_id}"}}[5m]))')},
                timeout=_PROMETHEUS_TIMEOUT,
            )
            p99_resp = httpx.get(
                f"{self._prometheus_url}/api/v1/query",
                params={
                    "query": (
                        f"histogram_quantile(0.99, sum(rate("
                        f"selfhealing_cell_request_duration_seconds_bucket"
                        f'{{cell_id="{cell_id}"}}[5m])) by (le))'
                    )
                },
                timeout=_PROMETHEUS_TIMEOUT,
            )

            error_rate_val = self._parse_prometheus_scalar(error_resp)
            total_rate_val = self._parse_prometheus_scalar(total_resp)
            p99_val = self._parse_prometheus_scalar(p99_resp)

            error_rate = (error_rate_val / total_rate_val) if total_rate_val > 0 else 0.0
            # total_requests 추정 (5분 rate × 300초)
            total_requests = int(total_rate_val * 300)

            self._prometheus_consecutive_failures = 0
            return error_rate, p99_val, total_requests

        except Exception as e:
            self._prometheus_consecutive_failures += 1
            self._last_prometheus_failure_time = time.monotonic()
            logger.warning(
                "Prometheus API failed for %s " "(consecutive=%d): %s",
                cell_id,
                self._prometheus_consecutive_failures,
                e,
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

        # get_smoothed()는 None을 반환할 수 있으므로 기본값 보장
        if error_rate is None:
            error_rate = 0.0
        if latency_p99 is None:
            latency_p99 = 0.0

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
            self._metrics["health_score"].labels(cell_id=cell_id).set(smoothed_health)
            self._metrics["health_score_raw"].labels(cell_id=cell_id).set(raw_health)
            self._metrics["bulkhead_utilization"].labels(cell_id=cell_id).set(bulkhead_util)
            self._metrics["health_data_source"].labels(cell_id=cell_id).set(1.0 if source == "prometheus" else 0.0)

        return smoothed_health

    def _get_bulkhead_utilization(self, cell_id: str) -> float:
        """
        BulkheadRegistry에서 Cell Bulkhead 사용률 조회.

        BulkheadRegistry.get_all_states()를 사용하여 로컬 메모리에서 조회합니다.
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
        except ImportError:
            pass
        except Exception as e:
            logger.warning(
                "[CellHealthAggregator] Bulkhead util query failed " "for %s: %s",
                cell_id,
                e,
            )
        return 0.0

    def _get_cb_open_ratio(self, cell_id: str) -> float:
        """
        Composite Key 기반 Cell CB OPEN 비율 조회.

        CB의 service_name에서 cell_id를 직접 파싱하므로:
        - assigned_services TTL에 의존하지 않음 (레이스 컨디션 해소)
        - 수동 제어(force_open/force_close)도 감지 가능
        - metadata 경합 없음 (각 Cell이 물리적으로 분리된 CB 보유)
        """
        try:
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
            )
            from selfhealing.services.cell_topology.cb_namespace import (
                parse_composite_cb_name,
            )

            cb_service = get_circuit_breaker_service()
            all_states = cb_service.get_all_states()

            open_count = 0
            total = 0
            for state in all_states:
                _, state_cell_id = parse_composite_cb_name(state.get("service_name", ""))
                if state_cell_id == cell_id:
                    total += 1
                    if state.get("state") == "open":
                        open_count += 1

            return (open_count / total) if total > 0 else 0.0
        except Exception as e:
            logger.warning(
                "[CellHealthAggregator] CB open ratio query failed for %s: %s",
                cell_id,
                e,
            )
            return 0.0

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
        if self._leader_since is not None:
            elapsed = time.monotonic() - self._leader_since
            warmup_window = self._settings.health_check_interval_seconds * 2
            if elapsed < warmup_window:
                logger.info(
                    "[CellHealthAggregator] Leader warmup period " "(%.1fs < %ss), scores may be volatile",
                    elapsed,
                    warmup_window,
                )

        try:
            from selfhealing.services.cell_topology import get_cell_registry

            registry = get_cell_registry()
            for cell_id in registry.get_all_cells():
                score = self.compute_health(cell_id)
                registry.update_health_score(cell_id, score)

            # 건강도 갱신 완료 후 대피 정책 평가
            self._evaluate_evacuation_policy(registry)
        except Exception as e:
            logger.error(
                "cellhealthaggregator_failed",
                error=e,
            )

    def _evaluate_evacuation_policy(self, registry: object) -> None:
        """갱신된 건강도를 기반으로 Cell 대피 정책을 평가.

        evacuation_enabled 토글이 꺼져 있으면 즉시 반환한다.
        대피 정책 평가 실패가 건강도 수집 루프에 영향을 주지 않도록
        독립적인 try/except로 보호한다.
        """
        if not self._settings.evacuation_enabled:
            return

        try:
            from selfhealing.services.cell_topology.policy import (
                get_cell_evacuation_policy,
            )

            policy = get_cell_evacuation_policy()
            all_cells = registry.get_all_cells()  # type: ignore[attr-defined]
            for cell_id, cell_info in all_cells.items():
                policy.evaluate(cell_id, cell_info.health_score)
        except Exception as e:
            logger.error(
                "CellHealthAggregator evacuation policy evaluation failed: %s",
                e,
            )

    def on_become_leader(self) -> None:
        """리더 전환 시 호출 — warmup 시작 시각 기록."""
        self._leader_since = time.monotonic()
        logger.info("cell_health_aggregator.became_leader_ewma_fallback")

    def on_lose_leader(self) -> None:
        """리더십 상실 시 호출."""
        self._leader_since = None
        logger.info("cell_health_aggregator.lost_leadership")


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
    - LeaderScheduler (coordination/scheduler.py)
    - DLQConsumerCoordinator (coordination/dlq_consumer.py)
    """
    from selfhealing.settings.cell_topology import get_cell_topology_settings

    settings = get_cell_topology_settings()
    if not settings.enabled:
        return

    from selfhealing.coordination.scheduler import get_leader_scheduler

    aggregator = get_cell_health_aggregator()
    scheduler = get_leader_scheduler("cell-health-aggregator")

    # 리더 전환 이벤트 감지 — warmup 컨텍스트 기록
    scheduler.register_leader_callbacks(
        on_become=aggregator.on_become_leader,
        on_lose=aggregator.on_lose_leader,
    )

    @scheduler.job(
        interval_seconds=settings.health_check_interval_seconds,
        name="cell-health-aggregation",
    )
    def _aggregate():
        aggregator.aggregate_all()

    scheduler.start()
    logger.info(
        "[CellHealthAggregator] LeaderScheduler registered " "(interval=%ds)",
        settings.health_check_interval_seconds,
    )


def reset_cell_health_aggregator() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _aggregator
    with _aggregator_lock:
        _aggregator = None
