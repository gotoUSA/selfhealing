"""
Pre-computed Cache Service - Background Pre-computation Worker.

Threading.Timer based lightweight scheduling without Celery dependency.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .constants import (
    HAS_CACHETOOLS,
    HAS_ORJSON,
    _get_l1_ttl_seconds,
    _get_l2_ttl_seconds,
    _get_refresh_interval,
    fast_json_dumps,
    record_cache_refresh,
)
from .l1_cache import _l1_cache
from .l2_cache import _l2_cache
from .multi_tier import check_l1_l2_drift

logger = logging.getLogger(__name__)


# =============================================================================
# Background Pre-computation Worker
# =============================================================================


class PrecomputedCacheWorker:
    """
    Background worker that pre-computes cache entries.

    Uses Threading.Timer for lightweight scheduling without Celery dependency.
    Starts automatically via AppConfig.ready().
    """

    def __init__(self):
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._compute_functions: dict[str, Callable[[], dict[str, Any]]] = {}

    def register(self, cache_key: str, compute_fn: Callable[[], dict[str, Any]]) -> None:
        """Register a compute function for a cache key."""
        self._compute_functions[cache_key] = compute_fn

    def start(self) -> None:
        """Start the background worker."""
        with self._lock:
            if self._running:
                return
            self._running = True
            logger.info("[PrecomputedCache] Starting background worker")
            self._schedule_refresh()

    def stop(self) -> None:
        """Stop the background worker."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            logger.info("[PrecomputedCache] Stopped background worker")

    def _schedule_refresh(self) -> None:
        """Schedule the next refresh."""
        if not self._running:
            return
        self._timer = threading.Timer(_get_refresh_interval(), self._do_refresh)
        self._timer.daemon = True  # Don't block process exit
        self._timer.start()

    def _do_refresh(self) -> None:
        """Execute pre-computation for all registered keys with drift detection."""
        start_time = time.perf_counter()

        for cache_key, compute_fn in self._compute_functions.items():
            try:
                # Drift 체크 (갱신 전)
                check_l1_l2_drift(cache_key)

                data = compute_fn()
                data["_precomputed_at"] = datetime.now(timezone.utc).isoformat()
                json_str = fast_json_dumps(data)

                # Update both L1 and L2
                _l1_cache.set(cache_key, json_str)
                _l2_cache.set(cache_key, json_str)

                # Prometheus 메트릭: refresh 성공
                record_cache_refresh(cache_key, success=True)

            except Exception as e:
                logger.warning(f"[PrecomputedCache] Refresh failed for {cache_key}: {e}")
                # Prometheus 메트릭: refresh 실패
                record_cache_refresh(cache_key, success=False)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(f"[PrecomputedCache] Refresh completed in {elapsed_ms:.1f}ms")

        # Schedule next refresh
        self._schedule_refresh()

    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running

    def get_stats(self) -> dict[str, Any]:
        """Get worker statistics."""
        return {
            "running": self._running,
            "registered_keys": list(self._compute_functions.keys()),
            "refresh_interval_seconds": _get_refresh_interval(),
            "l1_ttl_seconds": _get_l1_ttl_seconds(),
            "l2_ttl_seconds": _get_l2_ttl_seconds(),
            "has_orjson": HAS_ORJSON,
            "has_cachetools": HAS_CACHETOOLS,
        }


# Global worker instance
_worker = PrecomputedCacheWorker()


def get_precomputed_cache_worker() -> PrecomputedCacheWorker:
    """Get the global pre-computed cache worker."""
    return _worker


def start_precomputed_cache() -> None:
    """Start the pre-computed cache worker."""
    _worker.start()


def stop_precomputed_cache() -> None:
    """Stop the pre-computed cache worker."""
    _worker.stop()
