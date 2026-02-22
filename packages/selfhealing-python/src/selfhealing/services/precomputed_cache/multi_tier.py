"""
Pre-computed Cache Service - Multi-Tier Cache Access with Drift Detection.
"""

from __future__ import annotations

import structlog
import threading
import time
from collections.abc import Callable
from typing import Any

from .constants import (
    fast_json_dumps,
    fast_json_loads,
    record_cache_drift,
    update_cache_consistency,
    update_cache_hit_rate,
)
from .l1_cache import _l1_cache
from .l2_cache import _l2_cache

logger = structlog.get_logger()


# =============================================================================
# Multi-Tier Cache Access with Drift Detection
# =============================================================================

# Drift tracking stats per cache key
_drift_stats: dict[str, dict[str, int]] = {}
_drift_stats_lock = threading.Lock()


def _get_drift_stats(cache_key: str) -> dict[str, int]:
    """Get or create drift stats for a cache key."""
    with _drift_stats_lock:
        if cache_key not in _drift_stats:
            _drift_stats[cache_key] = {
                "l1_hits": 0,
                "l2_hits": 0,
                "l3_fallbacks": 0,
                "drift_count": 0,
                "total_accesses": 0,
            }
        return _drift_stats[cache_key]


def get_drift_stats_all() -> dict[str, dict[str, int]]:
    """Get drift stats for all cache keys."""
    with _drift_stats_lock:
        return {k: v.copy() for k, v in _drift_stats.items()}


def get_cached_response(
    cache_key: str,
    compute_fn: Callable[[], dict[str, Any]],
    use_l1: bool = True,
    use_l2: bool = True,
) -> dict[str, Any]:
    """
    Get response from multi-tier cache with fallback to compute.

    v6.3.0: Drift Detection 메트릭 추가

    Flow:
    1. Check L1 (in-process) → 0ms if hit
    2. Check L2 (Redis) → 1-5ms if hit
    3. Compute fresh → 50-200ms

    Args:
        cache_key: Cache key for this endpoint
        compute_fn: Function to compute fresh response
        use_l1: Whether to use L1 cache
        use_l2: Whether to use L2 cache

    Returns:
        Response data dict
    """
    start_time = time.perf_counter()
    cache_hit = None
    stats = _get_drift_stats(cache_key)
    stats["total_accesses"] += 1

    # L1: In-process cache
    if use_l1:
        l1_value = _l1_cache.get(cache_key)
        if l1_value:
            cache_hit = "L1"
            stats["l1_hits"] += 1
            data = fast_json_loads(l1_value)
            data["_cache"] = {
                "hit": "L1",
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
            }
            _update_hit_rate_metrics(cache_key, stats)
            return data

    # L2: Redis cache
    if use_l2:
        l2_value = _l2_cache.get(cache_key)
        if l2_value:
            cache_hit = "L2"
            stats["l2_hits"] += 1
            # Populate L1 from L2
            if use_l1:
                _l1_cache.set(cache_key, l2_value)
            data = fast_json_loads(l2_value)
            data["_cache"] = {
                "hit": "L2",
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
            }
            _update_hit_rate_metrics(cache_key, stats)
            return data

    # L3: Direct compute
    stats["l3_fallbacks"] += 1
    try:
        data = compute_fn()
        data["_cache"] = {"hit": "MISS", "computed": True}

        # Cache the result
        json_str = fast_json_dumps(data)
        if use_l1:
            _l1_cache.set(cache_key, json_str)
        if use_l2:
            _l2_cache.set(cache_key, json_str)

        data["_cache"]["latency_ms"] = round((time.perf_counter() - start_time) * 1000, 2)
        _update_hit_rate_metrics(cache_key, stats)
        return data

    except Exception as e:
        logger.error(
            "precomputed_cache.compute_failed",
            cache_key=cache_key,
            error=e,
        )
        return {
            "status": "error",
            "error": str(e),
            "_cache": {
                "hit": "ERROR",
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
            },
        }


def _update_hit_rate_metrics(cache_key: str, stats: dict[str, int]) -> None:
    """Update Prometheus hit rate metrics."""
    total = stats["total_accesses"]
    if total > 0:
        l1_rate = stats["l1_hits"] / total
        l2_rate = stats["l2_hits"] / total
        l3_rate = stats["l3_fallbacks"] / total
        update_cache_hit_rate(cache_key, "l1", l1_rate)
        update_cache_hit_rate(cache_key, "l2", l2_rate)
        update_cache_hit_rate(cache_key, "l3", l3_rate)


def check_l1_l2_drift(cache_key: str) -> dict[str, Any] | None:
    """
    L1과 L2 캐시 값을 비교하여 drift를 감지합니다.

    Returns:
        drift 정보 dict (drift 있을 경우), None (drift 없을 경우)
    """
    l1_value = _l1_cache.get(cache_key)
    l2_value = _l2_cache.get(cache_key)

    if l1_value is None or l2_value is None:
        # 둘 중 하나가 없으면 drift 확인 불가
        return None

    try:
        l1_data = fast_json_loads(l1_value)
        l2_data = fast_json_loads(l2_value)

        # _cache 메타데이터 제외하고 비교
        l1_compare = {k: v for k, v in l1_data.items() if not k.startswith("_")}
        l2_compare = {k: v for k, v in l2_data.items() if not k.startswith("_")}

        if l1_compare != l2_compare:
            stats = _get_drift_stats(cache_key)
            stats["drift_count"] += 1

            # Prometheus 메트릭 기록
            record_cache_drift(cache_key, severity="warning")

            # 일관성 비율 계산 (drift가 없으면 1.0)
            total = stats["total_accesses"]
            drift_count = stats["drift_count"]
            consistency = 1.0 - (drift_count / total) if total > 0 else 1.0
            update_cache_consistency(cache_key, consistency)

            logger.warning(
                "precomputed_cache.drift_detected",
                cache_key=cache_key,
                drift_count=drift_count,
            )

            return {
                "cache_key": cache_key,
                "drift_count": drift_count,
                "consistency_ratio": consistency,
                "l1_data": l1_compare,
                "l2_data": l2_compare,
            }
        else:
            # 일관성 유지
            stats = _get_drift_stats(cache_key)
            total = stats["total_accesses"]
            drift_count = stats["drift_count"]
            consistency = 1.0 - (drift_count / total) if total > 0 else 1.0
            update_cache_consistency(cache_key, consistency)

    except Exception as e:
        logger.debug(
            "precomputed_cache.drift_check_failed",
            cache_key=cache_key,
            error=e,
        )

    return None
