"""
Bulkhead Metrics - Prometheus 메트릭 정의 및 업데이터.

격벽 패턴의 상태와 성능을 모니터링하기 위한 Prometheus 메트릭을 정의합니다.

Metrics:
- selfhealing_bulkhead_active_count: 현재 활성 요청 수
- selfhealing_bulkhead_max_concurrent: 최대 동시 실행 수
- selfhealing_bulkhead_rejected_total: 거부된 요청 총 수
- selfhealing_bulkhead_utilization_percent: 사용률 (%)
- selfhealing_bulkhead_waiting_count: 대기 중인 요청 수
"""

from __future__ import annotations

import threading
import time

import structlog

logger = structlog.get_logger()


# =============================================================================
# Lazy Metric Loading (prometheus_client 선택적 의존성)
# =============================================================================

_metrics_initialized = False
_metrics_lock = threading.Lock()

# 메트릭 객체 (지연 초기화)
_bulkhead_active_count = None
_bulkhead_max_concurrent = None
_bulkhead_rejected_total = None
_bulkhead_utilization_percent = None
_bulkhead_waiting_count = None


def _initialize_metrics() -> bool:
    """Prometheus 메트릭 초기화 (선택적)."""
    global _metrics_initialized
    global _bulkhead_active_count, _bulkhead_max_concurrent
    global _bulkhead_rejected_total, _bulkhead_utilization_percent
    global _bulkhead_waiting_count

    if _metrics_initialized:
        return True

    with _metrics_lock:
        if _metrics_initialized:
            return True

        try:
            from selfhealing.services.metrics.registry import (
                get_or_create_counter,
                get_or_create_gauge,
            )

            _bulkhead_active_count = get_or_create_gauge(
                "selfhealing_bulkhead_active_count",
                "현재 활성 요청 수",
                ["bulkhead_name", "bulkhead_type"],
            )

            _bulkhead_max_concurrent = get_or_create_gauge(
                "selfhealing_bulkhead_max_concurrent",
                "최대 동시 실행 수",
                ["bulkhead_name"],
            )

            _bulkhead_rejected_total = get_or_create_counter(
                "selfhealing_bulkhead_rejected_total",
                "거부된 요청 총 수",
                ["bulkhead_name"],
            )

            _bulkhead_utilization_percent = get_or_create_gauge(
                "selfhealing_bulkhead_utilization_percent",
                "격벽 사용률 (%)",
                ["bulkhead_name"],
            )

            _bulkhead_waiting_count = get_or_create_gauge(
                "selfhealing_bulkhead_waiting_count",
                "대기 중인 요청 수",
                ["bulkhead_name"],
            )

            _metrics_initialized = True
            logger.debug("bulkhead_metrics.prometheus_metrics_initialized")
            return True

        except ImportError:
            logger.warning("bulkhead_metrics.available_metrics_disabled")
            return False
        except Exception as e:
            logger.warning(
                "bulkhead_metrics.failed_initialize_metrics",
                error=e,
            )
            return False


def update_bulkhead_metrics(
    bulkhead_name: str,
    bulkhead_type: str,
    active_count: int,
    max_concurrent: int,
    waiting_count: int,
    rejected_count: int,
) -> None:
    """
    격벽 메트릭 업데이트.

    Args:
        bulkhead_name: 격벽 이름
        bulkhead_type: 격벽 유형 (semaphore, thread_pool)
        active_count: 현재 활성 요청 수
        max_concurrent: 최대 동시 실행 수
        waiting_count: 대기 중인 요청 수
        rejected_count: 거부된 요청 총 수
    """
    if not _initialize_metrics():
        return

    try:
        _bulkhead_active_count.labels(
            bulkhead_name=bulkhead_name,
            bulkhead_type=bulkhead_type,
        ).set(active_count)

        _bulkhead_max_concurrent.labels(
            bulkhead_name=bulkhead_name,
        ).set(max_concurrent)

        _bulkhead_waiting_count.labels(
            bulkhead_name=bulkhead_name,
        ).set(waiting_count)

        # 사용률 계산
        utilization = (active_count / max_concurrent * 100) if max_concurrent > 0 else 0
        _bulkhead_utilization_percent.labels(
            bulkhead_name=bulkhead_name,
        ).set(utilization)

    except Exception as e:
        logger.debug(
            "bulkhead_metrics.failed_update_metrics",
            error=e,
        )


def increment_rejected_count(bulkhead_name: str) -> None:
    """
    거부 카운터 증가.

    Args:
        bulkhead_name: 격벽 이름
    """
    if not _initialize_metrics():
        return

    try:
        _bulkhead_rejected_total.labels(
            bulkhead_name=bulkhead_name,
        ).inc()
    except Exception as e:
        logger.debug(
            "bulkhead_metrics.failed_increment_rejected_count",
            error=e,
        )


class BulkheadMetricsUpdater:
    """
    주기적으로 모든 격벽 메트릭을 업데이트하는 백그라운드 스레드.

    레지스트리에 등록된 모든 격벽의 상태를 주기적으로 Prometheus 메트릭에 반영합니다.

    Usage:
        updater = BulkheadMetricsUpdater(interval=10.0)
        updater.start()
        # ... 애플리케이션 실행 ...
        updater.stop()
    """

    def __init__(self, interval: float = 10.0):
        """
        Args:
            interval: 메트릭 업데이트 주기 (초)
        """
        self._interval = interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """메트릭 업데이터 시작."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._update_loop,
            name="bulkhead_metrics_updater",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "bulkhead_metrics_updater.started",
            _self=self._interval,
        )

    def stop(self) -> None:
        """메트릭 업데이터 중지."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._interval + 1)
        logger.info("bulkhead_metrics_updater.stopped")

    def _update_loop(self) -> None:
        """메트릭 업데이트 루프."""
        while self._running:
            try:
                self._update_all_metrics()
            except Exception as e:
                logger.warning(
                    "bulkhead_metrics_updater.update_error",
                    error=e,
                )

            time.sleep(self._interval)

    def _update_all_metrics(self) -> None:
        """모든 격벽 메트릭 업데이트."""
        try:
            from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

            registry = get_bulkhead_registry()
            states = registry.get_all_states()

            for name, state in states.items():
                update_bulkhead_metrics(
                    bulkhead_name=name,
                    bulkhead_type=state.bulkhead_type.value,
                    active_count=state.active_count,
                    max_concurrent=state.max_concurrent,
                    waiting_count=state.waiting_count,
                    rejected_count=state.rejected_count,
                )

        except Exception as e:
            logger.debug(
                "bulkhead_metrics_updater.failed_update",
                error=e,
            )


# =============================================================================
# Singleton
# =============================================================================

_updater: BulkheadMetricsUpdater | None = None
_updater_lock = threading.Lock()


def get_metrics_updater(interval: float = 10.0) -> BulkheadMetricsUpdater:
    """BulkheadMetricsUpdater 싱글톤 반환."""
    global _updater
    if _updater is None:
        with _updater_lock:
            if _updater is None:
                _updater = BulkheadMetricsUpdater(interval=interval)
    return _updater


def start_metrics_updater(interval: float = 10.0) -> BulkheadMetricsUpdater:
    """메트릭 업데이터 시작 (편의 함수)."""
    updater = get_metrics_updater(interval)
    updater.start()
    return updater


def stop_metrics_updater() -> None:
    """메트릭 업데이터 중지 (편의 함수)."""
    global _updater
    if _updater is not None:
        _updater.stop()
