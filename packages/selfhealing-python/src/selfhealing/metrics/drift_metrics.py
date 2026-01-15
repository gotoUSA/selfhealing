"""
Drift Detection Metrics for Self-Healing System.

이 모듈은 캐시, 계층형 저장소, 분산 복제 등에서 발생하는
불일치(Drift)를 추적하기 위한 Prometheus 메트릭을 정의합니다.

Reference:
    - 08_DRIFT_DETECTION_IMPLEMENTATION_PLAN.md

Phase 1: PoolCircuitBreaker, PrecomputedCache
Phase 2: EmergencyMode Cache, RateLimiter
Phase 3: Config lru_cache
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import prometheus_client, but don't fail if not installed
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None
    Gauge = None
    Histogram = None


# =============================================================================
# Metric Prefix
# =============================================================================

METRIC_PREFIX = "selfhealing"


# =============================================================================
# Phase 1: PoolCircuitBreaker Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # PoolCircuitBreaker Stale Cache 메트릭
    pool_cb_cache_stale_total = Counter(
        f"{METRIC_PREFIX}_pool_cb_cache_stale_total",
        "Total stale cache events in PoolCircuitBreaker",
        ["severity"],  # warning, critical
    )

    pool_cb_cache_age_ms = Histogram(
        f"{METRIC_PREFIX}_pool_cb_cache_age_ms",
        "Age of PoolCircuitBreaker cache when accessed (ms)",
        buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000],
    )

    pool_cb_cache_hit_rate = Gauge(
        f"{METRIC_PREFIX}_pool_cb_cache_hit_rate",
        "PoolCircuitBreaker cache hit rate",
    )

    pool_cb_background_restarts_total = Counter(
        f"{METRIC_PREFIX}_pool_cb_background_restarts_total",
        "Number of background thread restarts in PoolCircuitBreaker",
    )
else:
    pool_cb_cache_stale_total = None
    pool_cb_cache_age_ms = None
    pool_cb_cache_hit_rate = None
    pool_cb_background_restarts_total = None


# =============================================================================
# Phase 1: PrecomputedCache Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # PrecomputedCache Drift 메트릭
    cache_drift_detected_total = Counter(
        f"{METRIC_PREFIX}_cache_drift_detected_total",
        "Total cache drift detections between L1 and L2",
        ["cache_key", "severity"],  # severity: warning, critical
    )

    cache_l1_l2_consistency = Gauge(
        f"{METRIC_PREFIX}_cache_l1_l2_consistency",
        "L1/L2 cache consistency ratio (1.0 = fully consistent)",
        ["cache_key"],
    )

    cache_hit_rate = Gauge(
        f"{METRIC_PREFIX}_cache_hit_rate",
        "Cache hit rate per layer",
        ["cache_key", "layer"],  # layer: l1, l2, l3
    )

    cache_refresh_total = Counter(
        f"{METRIC_PREFIX}_cache_refresh_total",
        "Total cache refresh operations",
        ["cache_key", "status"],  # status: success, failed
    )
else:
    cache_drift_detected_total = None
    cache_l1_l2_consistency = None
    cache_hit_rate = None
    cache_refresh_total = None


# =============================================================================
# Phase 2: EmergencyMode Cache Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # EmergencyMode Cache 메트릭
    emergency_cache_stale_total = Counter(
        f"{METRIC_PREFIX}_emergency_cache_stale_total",
        "Number of times emergency mode cache became stale",
    )

    emergency_cache_drift_total = Counter(
        f"{METRIC_PREFIX}_emergency_cache_drift_total",
        "Number of times cached state differed from backend",
    )

    emergency_cache_age_seconds = Gauge(
        f"{METRIC_PREFIX}_emergency_cache_age_seconds",
        "Current age of emergency mode cache in seconds",
    )

    emergency_cache_load_total = Counter(
        f"{METRIC_PREFIX}_emergency_cache_load_total",
        "Number of times state was loaded from backend",
        ["reason"],  # reason: expired, invalidated, startup
    )
else:
    emergency_cache_stale_total = None
    emergency_cache_drift_total = None
    emergency_cache_age_seconds = None
    emergency_cache_load_total = None


# =============================================================================
# Phase 2: RateLimiter Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # RateLimiter Redis Drift 메트릭
    ratelimit_redis_unavailable_total = Counter(
        f"{METRIC_PREFIX}_ratelimit_redis_unavailable_total",
        "Number of times Redis was unavailable for rate limiting",
    )

    ratelimit_state_drift_total = Counter(
        f"{METRIC_PREFIX}_ratelimit_state_drift_total",
        "Number of rate limit state drifts detected",
        ["key"],
    )

    ratelimit_fallback_active = Gauge(
        f"{METRIC_PREFIX}_ratelimit_fallback_active",
        "Whether rate limiter is in fallback mode (1=yes, 0=no)",
    )

    ratelimit_reconciliation_total = Counter(
        f"{METRIC_PREFIX}_ratelimit_reconciliation_total",
        "Number of rate limit state reconciliations after Redis recovery",
        ["result"],  # result: success, failed
    )
else:
    ratelimit_redis_unavailable_total = None
    ratelimit_state_drift_total = None
    ratelimit_fallback_active = None
    ratelimit_reconciliation_total = None


# =============================================================================
# Phase 3: Config Cache Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # Config lru_cache 메트릭
    config_env_changed_total = Counter(
        f"{METRIC_PREFIX}_config_env_changed_total",
        "Number of environment variable changes detected",
        ["config_type"],
    )

    config_cache_invalidated_total = Counter(
        f"{METRIC_PREFIX}_config_cache_invalidated_total",
        "Number of config cache invalidations",
        ["config_type"],
    )

    config_cache_hit_total = Counter(
        f"{METRIC_PREFIX}_config_cache_hit_total",
        "Number of config cache hits",
        ["config_type"],
    )

    config_cache_miss_total = Counter(
        f"{METRIC_PREFIX}_config_cache_miss_total",
        "Number of config cache misses (recomputed)",
        ["config_type"],
    )
else:
    config_env_changed_total = None
    config_cache_invalidated_total = None
    config_cache_hit_total = None
    config_cache_miss_total = None


# =============================================================================
# Helper Functions
# =============================================================================


def record_pool_cb_stale(severity: str) -> None:
    """Record a PoolCircuitBreaker stale cache event."""
    if pool_cb_cache_stale_total is not None:
        pool_cb_cache_stale_total.labels(severity=severity).inc()


def record_pool_cb_cache_age(age_ms: float) -> None:
    """Record PoolCircuitBreaker cache age."""
    if pool_cb_cache_age_ms is not None:
        pool_cb_cache_age_ms.observe(age_ms)


def update_pool_cb_hit_rate(hit_rate: float) -> None:
    """Update PoolCircuitBreaker cache hit rate."""
    if pool_cb_cache_hit_rate is not None:
        pool_cb_cache_hit_rate.set(hit_rate)


def record_pool_cb_background_restart() -> None:
    """Record a background thread restart."""
    if pool_cb_background_restarts_total is not None:
        pool_cb_background_restarts_total.inc()


def record_cache_drift(cache_key: str, severity: str = "warning") -> None:
    """Record a cache drift detection event."""
    if cache_drift_detected_total is not None:
        cache_drift_detected_total.labels(
            cache_key=cache_key,
            severity=severity,
        ).inc()


def update_cache_consistency(cache_key: str, ratio: float) -> None:
    """Update cache consistency ratio (0.0 to 1.0)."""
    if cache_l1_l2_consistency is not None:
        cache_l1_l2_consistency.labels(cache_key=cache_key).set(ratio)


def update_cache_hit_rate(cache_key: str, layer: str, rate: float) -> None:
    """Update cache hit rate for a specific layer."""
    if cache_hit_rate is not None:
        cache_hit_rate.labels(cache_key=cache_key, layer=layer).set(rate)


def record_cache_refresh(cache_key: str, success: bool) -> None:
    """Record a cache refresh operation."""
    if cache_refresh_total is not None:
        status = "success" if success else "failed"
        cache_refresh_total.labels(cache_key=cache_key, status=status).inc()


def record_emergency_cache_stale() -> None:
    """Record an emergency cache stale event."""
    if emergency_cache_stale_total is not None:
        emergency_cache_stale_total.inc()


def record_emergency_cache_drift() -> None:
    """Record an emergency cache drift event."""
    if emergency_cache_drift_total is not None:
        emergency_cache_drift_total.inc()


def update_emergency_cache_age(age_seconds: float) -> None:
    """Update emergency cache age gauge."""
    if emergency_cache_age_seconds is not None:
        emergency_cache_age_seconds.set(age_seconds)


def record_emergency_cache_load(reason: str) -> None:
    """Record a state load from backend."""
    if emergency_cache_load_total is not None:
        emergency_cache_load_total.labels(reason=reason).inc()


def record_ratelimit_redis_unavailable() -> None:
    """Record Redis unavailability for rate limiting."""
    if ratelimit_redis_unavailable_total is not None:
        ratelimit_redis_unavailable_total.inc()


def record_ratelimit_drift(key: str) -> None:
    """Record a rate limit state drift."""
    if ratelimit_state_drift_total is not None:
        ratelimit_state_drift_total.labels(key=key).inc()


def set_ratelimit_fallback_mode(active: bool) -> None:
    """Set rate limiter fallback mode status."""
    if ratelimit_fallback_active is not None:
        ratelimit_fallback_active.set(1 if active else 0)


def record_ratelimit_reconciliation(success: bool) -> None:
    """Record a rate limit reconciliation."""
    if ratelimit_reconciliation_total is not None:
        result = "success" if success else "failed"
        ratelimit_reconciliation_total.labels(result=result).inc()


def record_config_env_changed(config_type: str) -> None:
    """Record an environment variable change detection."""
    if config_env_changed_total is not None:
        config_env_changed_total.labels(config_type=config_type).inc()


def record_config_cache_invalidated(config_type: str) -> None:
    """Record a config cache invalidation."""
    if config_cache_invalidated_total is not None:
        config_cache_invalidated_total.labels(config_type=config_type).inc()


def record_config_cache_hit(config_type: str) -> None:
    """Record a config cache hit."""
    if config_cache_hit_total is not None:
        config_cache_hit_total.labels(config_type=config_type).inc()


def record_config_cache_miss(config_type: str) -> None:
    """Record a config cache miss."""
    if config_cache_miss_total is not None:
        config_cache_miss_total.labels(config_type=config_type).inc()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Phase 1: PoolCircuitBreaker
    "pool_cb_cache_stale_total",
    "pool_cb_cache_age_ms",
    "pool_cb_cache_hit_rate",
    "pool_cb_background_restarts_total",
    "record_pool_cb_stale",
    "record_pool_cb_cache_age",
    "update_pool_cb_hit_rate",
    "record_pool_cb_background_restart",
    # Phase 1: PrecomputedCache
    "cache_drift_detected_total",
    "cache_l1_l2_consistency",
    "cache_hit_rate",
    "cache_refresh_total",
    "record_cache_drift",
    "update_cache_consistency",
    "update_cache_hit_rate",
    "record_cache_refresh",
    # Phase 2: EmergencyMode
    "emergency_cache_stale_total",
    "emergency_cache_drift_total",
    "emergency_cache_age_seconds",
    "emergency_cache_load_total",
    "record_emergency_cache_stale",
    "record_emergency_cache_drift",
    "update_emergency_cache_age",
    "record_emergency_cache_load",
    # Phase 2: RateLimiter
    "ratelimit_redis_unavailable_total",
    "ratelimit_state_drift_total",
    "ratelimit_fallback_active",
    "ratelimit_reconciliation_total",
    "record_ratelimit_redis_unavailable",
    "record_ratelimit_drift",
    "set_ratelimit_fallback_mode",
    "record_ratelimit_reconciliation",
    # Phase 3: Config
    "config_env_changed_total",
    "config_cache_invalidated_total",
    "config_cache_hit_total",
    "config_cache_miss_total",
    "record_config_env_changed",
    "record_config_cache_invalidated",
    "record_config_cache_hit",
    "record_config_cache_miss",
    # Utils
    "PROMETHEUS_AVAILABLE",
]
