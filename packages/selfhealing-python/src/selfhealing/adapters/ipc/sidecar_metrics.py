"""
사이드카 전용 Prometheus 메트릭.

IPC 통신, 버퍼 상태, 연결 수, 지연 시간 등
사이드카 운영에 필요한 메트릭을 정의합니다.

메트릭 목록:
- selfhealing_sidecar_ipc_active_connections: 활성 IPC 연결 수
- selfhealing_sidecar_uds_queue_depth: UDS 대기열 깊이
- selfhealing_sidecar_request_latency_seconds: 요청 지연 시간
- selfhealing_sidecar_errors_total: 총 에러 수
- selfhealing_sidecar_buffer_entries: 버퍼 엔트리 수
- selfhealing_sidecar_cache_hit_ratio: 캐시 히트율

Usage:
    from selfhealing.adapters.ipc.sidecar_metrics import (
        sidecar_metrics,
        record_ipc_request,
    )

    # 요청 기록
    with record_ipc_request("circuit_breaker.should_allow", "uds"):
        result = handle_request()

    # 연결 수 업데이트
    sidecar_metrics.update_connections("uds", 5)
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Prometheus 클라이언트 가용성 확인
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore
    Gauge = None  # type: ignore
    Histogram = None  # type: ignore


class SidecarMetrics:
    """
    사이드카 전용 Prometheus 메트릭 컬렉션.

    prometheus_client가 없으면 no-op으로 동작합니다.
    """

    def __init__(self, prefix: str = "selfhealing_sidecar"):
        """
        메트릭 초기화.

        Args:
            prefix: 메트릭 이름 접두사
        """
        self.prefix = prefix
        self._initialized = False

        if not PROMETHEUS_AVAILABLE:
            logger.debug("[SidecarMetrics] prometheus_client not available, metrics disabled")
            return

        # =====================================================================
        # 연결 메트릭
        # =====================================================================

        self.ipc_active_connections = Gauge(
            f"{prefix}_ipc_active_connections",
            "Active IPC connections by transport type",
            ["transport"],  # "uds", "grpc"
        )

        self.ipc_total_connections = Counter(
            f"{prefix}_ipc_total_connections",
            "Total IPC connections established",
            ["transport"],
        )

        # =====================================================================
        # 큐/버퍼 메트릭
        # =====================================================================

        self.uds_queue_depth = Gauge(
            f"{prefix}_uds_queue_depth",
            "Pending requests in UDS queue",
        )

        self.buffer_entries = Gauge(
            f"{prefix}_buffer_entries",
            "Entries in persistent buffer",
            ["entry_type"],  # "dlq", "audit", "metric"
        )

        self.buffer_bytes_used = Gauge(
            f"{prefix}_buffer_bytes_used",
            "Bytes used in persistent buffer",
        )

        # =====================================================================
        # 지연 시간 메트릭
        # =====================================================================

        self.request_latency_seconds = Histogram(
            f"{prefix}_request_latency_seconds",
            "Request latency in seconds",
            ["method", "transport"],
            buckets=[
                0.0001,  # 0.1ms
                0.0005,  # 0.5ms
                0.001,  # 1ms
                0.005,  # 5ms
                0.01,  # 10ms
                0.05,  # 50ms
                0.1,  # 100ms
                0.5,  # 500ms
                1.0,  # 1s
            ],
        )

        # =====================================================================
        # 에러 메트릭
        # =====================================================================

        self.errors_total = Counter(
            f"{prefix}_errors_total",
            "Total sidecar errors",
            ["method", "error_type"],
        )

        self.auth_failures_total = Counter(
            f"{prefix}_auth_failures_total",
            "Total authentication failures",
            ["transport"],
        )

        # =====================================================================
        # 캐시 메트릭
        # =====================================================================

        self.cache_hits_total = Counter(
            f"{prefix}_cache_hits_total",
            "Total cache hits",
            ["cache_type"],  # "cb_state"
        )

        self.cache_misses_total = Counter(
            f"{prefix}_cache_misses_total",
            "Total cache misses",
            ["cache_type"],
        )

        self.cache_invalidations_total = Counter(
            f"{prefix}_cache_invalidations_total",
            "Total cache invalidations",
            ["cache_type"],
        )

        # =====================================================================
        # 이벤트 스트리밍 메트릭
        # =====================================================================

        self.event_streams_active = Gauge(
            f"{prefix}_event_streams_active",
            "Active event stream subscriptions",
        )

        self.events_proxied_total = Counter(
            f"{prefix}_events_proxied_total",
            "Total events proxied to clients",
            ["event_type"],
        )

        self.events_dropped_total = Counter(
            f"{prefix}_events_dropped_total",
            "Total events dropped due to queue overflow",
        )

        # =====================================================================
        # Shared Memory 메트릭
        # =====================================================================

        self.shm_snapshot_updates_total = Counter(
            f"{prefix}_shm_snapshot_updates_total",
            "Total shared memory snapshot updates",
        )

        self.shm_snapshot_age_seconds = Gauge(
            f"{prefix}_shm_snapshot_age_seconds",
            "Age of current shared memory snapshot in seconds",
        )

        self._initialized = True
        logger.debug("[SidecarMetrics] Initialized")

    # =========================================================================
    # 연결 메트릭 업데이트
    # =========================================================================

    def update_connections(self, transport: str, count: int) -> None:
        """활성 연결 수 업데이트."""
        if not self._initialized:
            return
        self.ipc_active_connections.labels(transport=transport).set(count)

    def record_connection(self, transport: str) -> None:
        """새 연결 기록."""
        if not self._initialized:
            return
        self.ipc_total_connections.labels(transport=transport).inc()

    # =========================================================================
    # 요청 메트릭 업데이트
    # =========================================================================

    def record_request_latency(
        self,
        method: str,
        transport: str,
        latency_seconds: float,
    ) -> None:
        """요청 지연 시간 기록."""
        if not self._initialized:
            return
        self.request_latency_seconds.labels(
            method=method,
            transport=transport,
        ).observe(latency_seconds)

    def record_error(self, method: str, error_type: str) -> None:
        """에러 기록."""
        if not self._initialized:
            return
        self.errors_total.labels(method=method, error_type=error_type).inc()

    def record_auth_failure(self, transport: str) -> None:
        """인증 실패 기록."""
        if not self._initialized:
            return
        self.auth_failures_total.labels(transport=transport).inc()

    # =========================================================================
    # 캐시 메트릭 업데이트
    # =========================================================================

    def record_cache_hit(self, cache_type: str = "cb_state") -> None:
        """캐시 히트 기록."""
        if not self._initialized:
            return
        self.cache_hits_total.labels(cache_type=cache_type).inc()

    def record_cache_miss(self, cache_type: str = "cb_state") -> None:
        """캐시 미스 기록."""
        if not self._initialized:
            return
        self.cache_misses_total.labels(cache_type=cache_type).inc()

    def record_cache_invalidation(self, cache_type: str = "cb_state") -> None:
        """캐시 무효화 기록."""
        if not self._initialized:
            return
        self.cache_invalidations_total.labels(cache_type=cache_type).inc()

    # =========================================================================
    # 버퍼 메트릭 업데이트
    # =========================================================================

    def update_buffer_entries(self, entry_type: str, count: int) -> None:
        """버퍼 엔트리 수 업데이트."""
        if not self._initialized:
            return
        self.buffer_entries.labels(entry_type=entry_type).set(count)

    def update_buffer_bytes(self, bytes_used: int) -> None:
        """버퍼 사용량 업데이트."""
        if not self._initialized:
            return
        self.buffer_bytes_used.set(bytes_used)

    def update_queue_depth(self, depth: int) -> None:
        """UDS 큐 깊이 업데이트."""
        if not self._initialized:
            return
        self.uds_queue_depth.set(depth)

    # =========================================================================
    # 이벤트 스트리밍 메트릭 업데이트
    # =========================================================================

    def update_event_streams(self, count: int) -> None:
        """활성 이벤트 스트림 수 업데이트."""
        if not self._initialized:
            return
        self.event_streams_active.set(count)

    def record_event_proxied(self, event_type: str) -> None:
        """이벤트 프록시 기록."""
        if not self._initialized:
            return
        self.events_proxied_total.labels(event_type=event_type).inc()

    def record_event_dropped(self) -> None:
        """이벤트 드롭 기록."""
        if not self._initialized:
            return
        self.events_dropped_total.inc()

    # =========================================================================
    # Shared Memory 메트릭 업데이트
    # =========================================================================

    def record_shm_update(self) -> None:
        """Shared Memory 스냅샷 업데이트 기록."""
        if not self._initialized:
            return
        self.shm_snapshot_updates_total.inc()

    def update_shm_age(self, age_seconds: float) -> None:
        """Shared Memory 스냅샷 나이 업데이트."""
        if not self._initialized:
            return
        self.shm_snapshot_age_seconds.set(age_seconds)


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

sidecar_metrics = SidecarMetrics()


# =============================================================================
# 컨텍스트 매니저 헬퍼
# =============================================================================


@contextmanager
def record_ipc_request(
    method: str,
    transport: str,
) -> Generator[None, None, None]:
    """
    IPC 요청 메트릭 기록 컨텍스트 매니저.

    Usage:
        with record_ipc_request("circuit_breaker.should_allow", "uds"):
            result = handle_request()
    """
    start_time = time.time()
    try:
        yield
    except Exception as e:
        sidecar_metrics.record_error(method, type(e).__name__)
        raise
    finally:
        latency = time.time() - start_time
        sidecar_metrics.record_request_latency(method, transport, latency)


def record_cache_access(cache_type: str, hit: bool) -> None:
    """
    캐시 접근 기록 헬퍼.

    Args:
        cache_type: 캐시 타입
        hit: 캐시 히트 여부
    """
    if hit:
        sidecar_metrics.record_cache_hit(cache_type)
    else:
        sidecar_metrics.record_cache_miss(cache_type)
