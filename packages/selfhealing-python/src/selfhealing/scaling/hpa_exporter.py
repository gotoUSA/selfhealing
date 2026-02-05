"""
HPA Metrics Exporter.

Kubernetes HPA용 커스텀 메트릭을 Prometheus 형식으로 노출합니다.
백그라운드 스레드에서 주기적으로 메트릭을 업데이트합니다.

주요 메트릭:
- selfhealing_queue_depth: 현재 큐 깊이
- selfhealing_processing_rate: 처리율 (항목/초)
- selfhealing_backpressure_level: Backpressure 레벨 (0-4)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from selfhealing.scaling.config import (
    BackpressureLevel,
    BackpressureSettings,
    get_backpressure_settings,
)
from selfhealing.scaling.metrics import BackpressureMetrics, get_backpressure_metrics
from selfhealing.scaling.rate_controller import RateController, get_rate_controller

logger = logging.getLogger(__name__)


# Backpressure 레벨을 정수로 변환 (Prometheus 메트릭용)
LEVEL_TO_INT: dict[BackpressureLevel, int] = {
    BackpressureLevel.NONE: 0,
    BackpressureLevel.LOW: 1,
    BackpressureLevel.MEDIUM: 2,
    BackpressureLevel.HIGH: 3,
    BackpressureLevel.CRITICAL: 4,
}


class HPAMetricsExporter:
    """
    HPA Metrics Exporter.

    백그라운드에서 주기적으로 Prometheus 메트릭을 업데이트합니다.
    Kubernetes HPA가 이 메트릭을 사용하여 Pod 수를 조절합니다.

    Usage:
        def get_queue_size() -> int:
            return redis.llen("my_queue")

        exporter = HPAMetricsExporter(queue_size_provider=get_queue_size)
        exporter.start()

        # 애플리케이션 종료 시
        exporter.stop()
    """

    DEFAULT_COMPONENT_NAME = "selfhealing"
    DEFAULT_QUEUE_NAME = "default"
    DEFAULT_UPDATE_INTERVAL = 5.0  # 초

    def __init__(
        self,
        queue_size_provider: Callable[[], int] | None = None,
        rate_controller: RateController | None = None,
        metrics: BackpressureMetrics | None = None,
        settings: BackpressureSettings | None = None,
        component_name: str | None = None,
        queue_name: str | None = None,
        update_interval: float | None = None,
    ):
        """
        Args:
            queue_size_provider: 큐 크기 조회 함수
            rate_controller: RateController 인스턴스
            metrics: BackpressureMetrics 인스턴스
            settings: Backpressure 설정
            component_name: 컴포넌트 이름 (메트릭 라벨)
            queue_name: 큐 이름 (메트릭 라벨)
            update_interval: 메트릭 업데이트 주기 (초)
        """
        self._settings = settings or get_backpressure_settings()
        self._queue_size_provider = queue_size_provider or (lambda: 0)
        self._rate_controller = rate_controller or get_rate_controller()
        self._metrics = metrics or get_backpressure_metrics()
        self._component_name = component_name or self.DEFAULT_COMPONENT_NAME
        self._queue_name = queue_name or self.DEFAULT_QUEUE_NAME
        self._update_interval = update_interval or self.DEFAULT_UPDATE_INTERVAL

        self._running = False
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def _update_metrics(self) -> None:
        """Prometheus 메트릭 업데이트."""
        try:
            # 큐 깊이
            queue_size = self._queue_size_provider()
            self._metrics.set_queue_depth(self._queue_name, queue_size)

            # 현재 상태 조회
            state = self._rate_controller.get_state()

            # 처리율
            self._metrics.set_processing_rate(self._component_name, state.current_rate)

            # Backpressure 레벨 (정수로 변환)
            level_int = LEVEL_TO_INT.get(state.level, 0)
            self._metrics.set_backpressure_level(self._component_name, level_int)

            logger.debug(
                f"[HPAMetricsExporter] Updated: queue_depth={queue_size}, "
                f"rate={state.current_rate:.1f}, level={state.level.value}"
            )

        except Exception as e:
            logger.error(f"[HPAMetricsExporter] Update error: {e}")

    def _run_loop(self) -> None:
        """메트릭 업데이트 루프."""
        while self._running:
            self._update_metrics()
            time.sleep(self._update_interval)

    def start(self) -> None:
        """Exporter 시작."""
        if not self._settings.hpa_enabled:
            logger.info("[HPAMetricsExporter] HPA disabled")
            return

        if not self._settings.metrics_enabled:
            logger.info("[HPAMetricsExporter] Metrics disabled")
            return

        with self._lock:
            if self._running:
                return

            self._running = True
            self._worker = threading.Thread(
                target=self._run_loop,
                name="HPAMetricsExporter",
                daemon=True,
            )
            self._worker.start()
            logger.info("[HPAMetricsExporter] Started")

    def stop(self) -> None:
        """Exporter 중지."""
        with self._lock:
            self._running = False

        if self._worker:
            self._worker.join(timeout=5.0)
            self._worker = None

        logger.info("[HPAMetricsExporter] Stopped")

    def is_running(self) -> bool:
        """실행 중 여부 반환."""
        return self._running

    def update_now(self) -> None:
        """즉시 메트릭 업데이트 (테스트/디버깅용)."""
        self._update_metrics()


# =============================================================================
# Singleton
# =============================================================================

_exporter: HPAMetricsExporter | None = None
_exporter_lock = threading.Lock()


def get_hpa_metrics_exporter() -> HPAMetricsExporter:
    """HPAMetricsExporter 싱글톤 반환."""
    global _exporter
    if _exporter is None:
        with _exporter_lock:
            if _exporter is None:
                _exporter = HPAMetricsExporter()
    return _exporter


def reset_hpa_metrics_exporter() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _exporter
    with _exporter_lock:
        if _exporter is not None:
            _exporter.stop()
            _exporter = None
