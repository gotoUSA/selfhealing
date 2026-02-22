"""
System Metrics Cache — psutil CPU/Memory 백그라운드 캐시.

threading.Timer 기반으로 1초마다 psutil CPU/Memory를 측정하여 캐시.
소비자(collect_system_snapshot, ResourceGuard, CircuitBreakerService)는
캐시에서 ~0ms에 값을 읽어 블로킹 없이 시스템 메트릭을 조회할 수 있다.

패턴: PrecomputedCacheWorker (services/precomputed_cache/worker.py)와 동일한
threading.Timer + daemon=True 패턴.
"""

from __future__ import annotations

import structlog
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger()


@dataclass(frozen=True)
class CachedMetrics:
    """
    캐시된 시스템 메트릭 스냅샷 (Immutable).

    frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지한다.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_available_mb: float = 0.0
    measured_at: str = ""  # ISO format timestamp
    source: str = "cache"  # "cache" | "direct" (fallback) | "stale"
    age_seconds: float = 0.0  # 마지막 갱신 후 경과 시간


class SystemMetricsCache:
    """
    시스템 메트릭 백그라운드 캐시.

    동작:
    1. start() 호출 시 동기 1회 초기 측정 후 백그라운드 데몬 스레드 시작
    2. _refresh_interval 초마다 psutil.cpu_percent(interval=_sample_interval) + virtual_memory() 호출
    3. CachedMetrics 인스턴스를 생성하여 _cached 참조를 atomic 교체
    4. 소비자는 get_metrics() / get_cpu_percent() / get_memory_percent()로 ~0ms에 읽기

    스레드 안전성:
    - _cached 참조 교체는 GIL 하에서 atomic (Lock 불필요)
    - start()/stop()은 _lock으로 보호
    """

    def __init__(
        self,
        refresh_interval: float = 1.0,
        sample_interval: float = 0.1,
        max_age_seconds: float = 5.0,
    ):
        self._refresh_interval = refresh_interval
        self._sample_interval = sample_interval
        self._max_age_seconds = max_age_seconds
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._cached = CachedMetrics()
        self._last_refresh: float = 0.0

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """
        백그라운드 갱신 시작.

        Cold Start 방지: 첫 _do_refresh()를 동기로 1회 호출하여
        캐시 초기값을 즉시 확보한 후 Timer 스케줄링을 시작한다.
        동기 호출 비용 ~100ms는 AppConfig.ready() 시작 시 1회만 발생.
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            logger.info(
                f"[SystemMetricsCache] Starting " f"(refresh={self._refresh_interval}s, sample={self._sample_interval}s)"
            )

            # Cold Start 방지: 첫 측정을 동기로 수행 (~100ms)
            self._do_refresh()

            # 이후 주기적 갱신 스케줄링
            self._schedule_refresh()

    def stop(self) -> None:
        """백그라운드 갱신 중지."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            logger.info("system_metrics_cache.stopped")

    def is_running(self) -> bool:
        """캐시 워커 실행 여부."""
        return self._running

    # =========================================================================
    # 소비자 API (Read — Lock-free, ~0ms)
    # =========================================================================

    def get_metrics(self) -> CachedMetrics:
        """
        캐시된 메트릭 전체 반환.

        캐시가 max_age_seconds를 초과하면 source="stale"로 표시.
        """
        cached = self._cached
        age = time.monotonic() - self._last_refresh if self._last_refresh > 0 else float("inf")

        if age > self._max_age_seconds:
            return CachedMetrics(
                cpu_percent=cached.cpu_percent,
                memory_percent=cached.memory_percent,
                memory_used_mb=cached.memory_used_mb,
                memory_available_mb=cached.memory_available_mb,
                measured_at=cached.measured_at,
                source="stale",
                age_seconds=round(age, 1),
            )
        return cached

    def get_cpu_percent(self) -> float:
        """캐시된 CPU 사용률."""
        return self._cached.cpu_percent

    def get_memory_percent(self) -> float:
        """캐시된 메모리 사용률."""
        return self._cached.memory_percent

    def get_snapshot_dict(self) -> dict[str, Any]:
        """
        collect_system_snapshot() 호환 딕셔너리 반환.

        cpu/memory 부분만 캐시에서 읽어 반환.
        DB 연결수, Error Rate 등은 블로킹이 아니므로 미포함.
        """
        m = self._cached
        return {
            "cpu_percent": m.cpu_percent,
            "memory_percent": m.memory_percent,
            "memory_used_mb": m.memory_used_mb,
            "memory_available_mb": m.memory_available_mb,
            "metrics_source": m.source,
            "metrics_measured_at": m.measured_at,
        }

    # =========================================================================
    # Internal — Background Refresh
    # =========================================================================

    def _schedule_refresh(self) -> None:
        """다음 갱신 스케줄링."""
        if not self._running:
            return
        self._timer = threading.Timer(self._refresh_interval, self._do_refresh)
        self._timer.daemon = True
        self._timer.start()

    def _do_refresh(self) -> None:
        """
        psutil로 CPU/Memory 측정 후 캐시 갱신.

        psutil.cpu_percent(interval=sample_interval)은 내부적으로 sleep 후 측정.
        데몬 스레드에서만 실행되므로 Web 스레드에 무영향.
        메트릭 정밀도: round(val, 1) — ResourceCheckResult.to_response_dict() 관행 준수.
        """
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=self._sample_interval)
            memory = psutil.virtual_memory()

            self._cached = CachedMetrics(
                cpu_percent=round(cpu, 1),
                memory_percent=round(memory.percent, 1),
                memory_used_mb=round(memory.used / (1024 * 1024), 1),
                memory_available_mb=round(memory.available / (1024 * 1024), 1),
                measured_at=datetime.now(timezone.utc).isoformat(),
                source="cache",
                age_seconds=0.0,
            )
            self._last_refresh = time.monotonic()

        except Exception as e:
            logger.warning(
                "system_metrics_cache.refresh_failed",
                error=e,
            )

        # 다음 갱신 스케줄링
        self._schedule_refresh()

    def get_stats(self) -> dict[str, Any]:
        """디버깅/모니터링용 통계."""
        age = time.monotonic() - self._last_refresh if self._last_refresh > 0 else -1
        return {
            "running": self._running,
            "refresh_interval": self._refresh_interval,
            "sample_interval": self._sample_interval,
            "max_age_seconds": self._max_age_seconds,
            "cache_age_seconds": round(age, 1) if age >= 0 else None,
            "current_cpu_percent": self._cached.cpu_percent,
            "current_memory_percent": self._cached.memory_percent,
            "source": self._cached.source,
        }


# =============================================================================
# Global Singleton + Module-level API
# =============================================================================

_cache = SystemMetricsCache()


def get_system_metrics_cache() -> SystemMetricsCache:
    """글로벌 SystemMetricsCache 인스턴스 반환."""
    return _cache


def start_system_metrics_cache() -> None:
    """캐시 워커 시작. AppConfig.ready()에서 호출."""
    _cache.start()


def stop_system_metrics_cache() -> None:
    """캐시 워커 중지."""
    _cache.stop()


def get_cached_cpu_percent() -> float:
    """캐시된 CPU 사용률 반환 (~0ms)."""
    return _cache.get_cpu_percent()


def get_cached_memory_percent() -> float:
    """캐시된 메모리 사용률 반환 (~0ms)."""
    return _cache.get_memory_percent()


def reset_system_metrics_cache() -> None:
    """테스트용: 글로벌 인스턴스 리셋."""
    global _cache
    _cache.stop()
    _cache = SystemMetricsCache()
