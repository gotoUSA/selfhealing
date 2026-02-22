"""
Prometheus 메트릭 노출.

Backpressure 관련 메트릭을 Prometheus 형식으로 노출합니다.
prometheus_client 라이브러리가 없으면 no-op으로 동작합니다.
"""

from __future__ import annotations

import structlog

from selfhealing.scaling.config import BackpressureSettings, get_backpressure_settings

logger = structlog.get_logger()

# prometheus_client가 있으면 사용
try:
    from prometheus_client import Counter, Gauge, Histogram

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


class BackpressureMetrics:
    """
    Backpressure Prometheus 메트릭.

    메트릭:
    - selfhealing_queue_depth: 현재 큐 깊이
    - selfhealing_processing_rate: 처리율 (항목/초)
    - selfhealing_backpressure_level: Backpressure 레벨 (0-4)
    - selfhealing_processed_total: 처리된 총 항목 수
    - selfhealing_dropped_total: 버려진 총 항목 수
    - selfhealing_processing_duration_seconds: 처리 시간 히스토그램
    """

    def __init__(
        self,
        settings: BackpressureSettings | None = None,
    ):
        """
        Args:
            settings: Backpressure 설정
        """
        self._settings = settings or get_backpressure_settings()
        self._prefix = self._settings.metrics_prefix

        if not HAS_PROMETHEUS:
            logger.warning("backpressure_metrics.installed")
            return

        if not self._settings.metrics_enabled:
            return

        # Gauge: 현재 큐 깊이
        self.queue_depth = Gauge(
            f"{self._prefix}queue_depth",
            "Current queue depth",
            ["queue_name"],
        )

        # Gauge: 처리율
        self.processing_rate = Gauge(
            f"{self._prefix}processing_rate",
            "Current processing rate (items/second)",
            ["component"],
        )

        # Gauge: Backpressure 레벨 (0=NONE, 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL)
        self.backpressure_level = Gauge(
            f"{self._prefix}backpressure_level",
            "Current backpressure level",
            ["component"],
        )

        # Counter: 처리된 항목
        self.processed_total = Counter(
            f"{self._prefix}processed_total",
            "Total processed items",
            ["component", "status"],
        )

        # Counter: 버려진 항목
        self.dropped_total = Counter(
            f"{self._prefix}dropped_total",
            "Total dropped items",
            ["component", "reason"],
        )

        # Counter: Tier별 거부 항목 (Starvation 감지용)
        self.dropped_by_tier_total = Counter(
            f"{self._prefix}rate_controller_dropped_total",
            "Total dropped items per tier for starvation monitoring",
            ["tier"],
        )

        # Counter: Tier별 처리 항목 (Starvation Alert 분모용)
        self.processed_by_tier_total = Counter(
            f"{self._prefix}rate_controller_processed_total",
            "Total processed items per tier for starvation monitoring",
            ["tier"],
        )

        # Histogram: 처리 시간
        self.processing_duration = Histogram(
            f"{self._prefix}processing_duration_seconds",
            "Processing duration in seconds",
            ["component", "operation"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

    def set_queue_depth(self, queue_name: str, depth: int) -> None:
        """큐 깊이 설정."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.queue_depth.labels(queue_name=queue_name).set(depth)

    def set_processing_rate(self, component: str, rate: float) -> None:
        """처리율 설정."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processing_rate.labels(component=component).set(rate)

    def set_backpressure_level(self, component: str, level: int) -> None:
        """
        Backpressure 레벨 설정.

        Args:
            component: 컴포넌트 이름
            level: 레벨 값 (0=NONE, 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL)
        """
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.backpressure_level.labels(component=component).set(level)

    def inc_processed(self, component: str, status: str = "success") -> None:
        """처리 카운터 증가."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processed_total.labels(component=component, status=status).inc()

    def inc_dropped(self, component: str, reason: str = "backpressure") -> None:
        """드롭 카운터 증가."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.dropped_total.labels(component=component, reason=reason).inc()

    def inc_dropped_by_tier(self, tier: str) -> None:
        """Tier별 거부 카운터 증가 (Starvation 감지용)."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.dropped_by_tier_total.labels(tier=tier).inc()

    def inc_processed_by_tier(self, tier: str) -> None:
        """Tier별 처리 카운터 증가 (Starvation Alert 분모용)."""
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processed_by_tier_total.labels(tier=tier).inc()

    def observe_duration(
        self,
        component: str,
        operation: str,
        duration: float,
    ) -> None:
        """
        처리 시간 기록.

        Args:
            component: 컴포넌트 이름
            operation: 작업 이름
            duration: 소요 시간 (초)
        """
        if HAS_PROMETHEUS and self._settings.metrics_enabled:
            self.processing_duration.labels(
                component=component,
                operation=operation,
            ).observe(duration)


# =============================================================================
# Singleton
# =============================================================================

_metrics: BackpressureMetrics | None = None


def get_backpressure_metrics() -> BackpressureMetrics:
    """BackpressureMetrics 싱글톤 반환."""
    global _metrics
    if _metrics is None:
        _metrics = BackpressureMetrics()
    return _metrics
