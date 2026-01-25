"""
Pre-computed Cache Service for L3 Observability Endpoints.

V3 최적화: L3 오버헤드 50ms 이하 달성을 위한 사전 계산 캐시 서비스.

Architecture:
- L1: In-process TTLCache (2초 TTL) - 0ms 오버헤드
- L2: Redis Pre-computed JSON (15초 TTL) - 1-5ms 오버헤드
- L3: Direct Compute (fallback) - 50-200ms 오버헤드

Background Worker:
- Threading.Timer 기반 (Celery 불필요, 0 dependency)
- 15초마다 health/error-budget/pool-status 사전 계산
- AppConfig.ready()에서 시작

L3 엔드포인트 성능 최적화를 위한 사전 계산 캠시 서비스.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    orjson = None
    HAS_ORJSON = False

try:
    from cachetools import TTLCache
    HAS_CACHETOOLS = True
except ImportError:
    TTLCache = None
    HAS_CACHETOOLS = False

# Drift Detection Metrics
try:
    from selfhealing.metrics.drift_metrics import (
        record_cache_drift,
        update_cache_consistency,
        update_cache_hit_rate,
        record_cache_refresh,
    )
    HAS_DRIFT_METRICS = True
except ImportError:
    HAS_DRIFT_METRICS = False
    record_cache_drift = lambda *args, **kwargs: None
    update_cache_consistency = lambda *args, **kwargs: None
    update_cache_hit_rate = lambda *args, **kwargs: None
    record_cache_refresh = lambda *args, **kwargs: None

logger = logging.getLogger(__name__)


# =============================================================================
# Constants (defaults, actual values loaded from Settings)
# =============================================================================

def _get_l1_ttl_seconds() -> float:
    """L1 TTL을 Settings에서 로드."""
    from selfhealing.settings.precomputed_cache import get_precomputed_cache_settings
    return get_precomputed_cache_settings().l1_ttl_seconds


def _get_l2_ttl_seconds() -> float:
    """L2 TTL을 Settings에서 로드."""
    from selfhealing.settings.precomputed_cache import get_precomputed_cache_settings
    return get_precomputed_cache_settings().l2_ttl_seconds


def _get_refresh_interval() -> float:
    """Refresh interval을 Settings에서 로드."""
    from selfhealing.settings.precomputed_cache import get_precomputed_cache_settings
    return get_precomputed_cache_settings().refresh_interval_seconds


def _get_l1_maxsize() -> int:
    """L1 maxsize를 Settings에서 로드."""
    from selfhealing.settings.precomputed_cache import get_precomputed_cache_settings
    return get_precomputed_cache_settings().l1_maxsize


# Cache Keys
CACHE_KEY_HEALTH = "selfhealing:cache:health"
CACHE_KEY_ERROR_BUDGET = "selfhealing:cache:error_budget"
CACHE_KEY_POOL_STATUS = "selfhealing:cache:pool_status"


# =============================================================================
# Fast JSON Serialization
# =============================================================================

def fast_json_dumps(data: Any) -> str:
    """
    Fast JSON serialization using orjson if available.
    
    orjson is ~10x faster than stdlib json for serialization.
    """
    if HAS_ORJSON:
        # orjson.dumps returns bytes, decode to str
        return orjson.dumps(data).decode("utf-8")
    return json.dumps(data)


def fast_json_loads(data: str) -> Any:
    """Fast JSON deserialization using orjson if available."""
    if HAS_ORJSON:
        return orjson.loads(data)
    return json.loads(data)


# =============================================================================
# L1 In-Process Cache (TTLCache)
# =============================================================================

class L1Cache:
    """
    L1 In-Process Cache using cachetools.TTLCache.
    
    Zero network overhead - immediate response.
    Falls back to simple dict if cachetools not installed.
    """
    
    def __init__(self, maxsize: int | None = None, ttl: float | None = None):
        if maxsize is None:
            maxsize = _get_l1_maxsize()
        if ttl is None:
            ttl = _get_l1_ttl_seconds()
        self._ttl = ttl
        if HAS_CACHETOOLS:
            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        else:
            # Fallback: simple dict with timestamp
            self._cache: Dict[str, tuple] = {}
            self._maxsize = maxsize
        self._lock = threading.Lock()
        
    def get(self, key: str) -> Optional[str]:
        """Get value from L1 cache."""
        with self._lock:
            if HAS_CACHETOOLS:
                return self._cache.get(key)
            else:
                # Manual TTL check for fallback
                entry = self._cache.get(key)
                if entry:
                    value, timestamp = entry
                    if time.time() - timestamp < self._ttl:
                        return value
                    else:
                        del self._cache[key]
                return None
                
    def set(self, key: str, value: str) -> None:
        """Set value in L1 cache."""
        with self._lock:
            if HAS_CACHETOOLS:
                self._cache[key] = value
            else:
                # Evict oldest if at capacity
                if len(self._cache) >= self._maxsize:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                self._cache[key] = (value, time.time())
                
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()


# Global L1 cache instance
_l1_cache = L1Cache()


def get_l1_cache() -> L1Cache:
    """Get the global L1 cache instance."""
    return _l1_cache


# =============================================================================
# L2 Redis Cache
# =============================================================================

class L2RedisCache:
    """
    L2 Redis Cache for pre-computed JSON.
    
    Stores pre-serialized JSON strings to eliminate serialization overhead
    on read path.
    """
    
    def __init__(self):
        self._redis = None
        self._initialized = False
        
    def _get_redis(self):
        """Lazy load Redis connection."""
        if self._redis is None:
            try:
                from django.core.cache import caches
                # Use 'default' cache which should be Redis in production
                # Note: Django caches uses dict-style access, not .get() method
                self._redis = caches["default"]
                self._initialized = True
            except Exception as e:
                logger.warning(f"[PrecomputedCache] Redis not available: {e}")
                self._redis = None
        return self._redis
        
    def get(self, key: str) -> Optional[str]:
        """Get pre-computed JSON string from Redis."""
        try:
            redis = self._get_redis()
            if redis:
                value = redis.get(key)
                if isinstance(value, bytes):
                    return value.decode("utf-8")
                return value
        except Exception as e:
            logger.debug(f"[PrecomputedCache] Redis get failed: {e}")
        return None
        
    def set(self, key: str, value: str, ttl: float | None = None) -> bool:
        """Set pre-computed JSON string in Redis."""
        if ttl is None:
            ttl = _get_l2_ttl_seconds()
        try:
            redis = self._get_redis()
            if redis:
                redis.set(key, value, timeout=int(ttl))
                return True
        except Exception as e:
            logger.debug(f"[PrecomputedCache] Redis set failed: {e}")
        return False
        
    def is_available(self) -> bool:
        """Check if Redis is available."""
        try:
            redis = self._get_redis()
            return redis is not None
        except Exception:
            return False


# Global L2 cache instance
_l2_cache = L2RedisCache()


def get_l2_cache() -> L2RedisCache:
    """Get the global L2 Redis cache instance."""
    return _l2_cache


# =============================================================================
# Multi-Tier Cache Access with Drift Detection
# =============================================================================

# Drift tracking stats per cache key
_drift_stats: Dict[str, Dict[str, int]] = {}
_drift_stats_lock = threading.Lock()


def _get_drift_stats(cache_key: str) -> Dict[str, int]:
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


def get_drift_stats_all() -> Dict[str, Dict[str, int]]:
    """Get drift stats for all cache keys."""
    with _drift_stats_lock:
        return {k: v.copy() for k, v in _drift_stats.items()}


def get_cached_response(
    cache_key: str,
    compute_fn: Callable[[], Dict[str, Any]],
    use_l1: bool = True,
    use_l2: bool = True,
) -> Dict[str, Any]:
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
            data["_cache"] = {"hit": "L1", "latency_ms": round((time.perf_counter() - start_time) * 1000, 2)}
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
            data["_cache"] = {"hit": "L2", "latency_ms": round((time.perf_counter() - start_time) * 1000, 2)}
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
        logger.error(f"[PrecomputedCache] Compute failed for {cache_key}: {e}")
        return {
            "status": "error",
            "error": str(e),
            "_cache": {"hit": "ERROR", "latency_ms": round((time.perf_counter() - start_time) * 1000, 2)},
        }


def _update_hit_rate_metrics(cache_key: str, stats: Dict[str, int]) -> None:
    """Update Prometheus hit rate metrics."""
    total = stats["total_accesses"]
    if total > 0:
        l1_rate = stats["l1_hits"] / total
        l2_rate = stats["l2_hits"] / total
        l3_rate = stats["l3_fallbacks"] / total
        update_cache_hit_rate(cache_key, "l1", l1_rate)
        update_cache_hit_rate(cache_key, "l2", l2_rate)
        update_cache_hit_rate(cache_key, "l3", l3_rate)


def check_l1_l2_drift(cache_key: str) -> Optional[Dict[str, Any]]:
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
                f"[PrecomputedCache] Drift detected for {cache_key}: "
                f"L1 != L2 (drift_count={drift_count})"
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
        logger.debug(f"[PrecomputedCache] Drift check failed for {cache_key}: {e}")
    
    return None


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
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._lock = threading.Lock()
        self._compute_functions: Dict[str, Callable[[], Dict[str, Any]]] = {}
        
    def register(self, cache_key: str, compute_fn: Callable[[], Dict[str, Any]]) -> None:
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
        
    def get_stats(self) -> Dict[str, Any]:
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


# =============================================================================
# Compute Functions for L3 Endpoints
# =============================================================================

def compute_health_status() -> Dict[str, Any]:
    """Compute health status for caching."""
    try:
        from selfhealing.services.health_check import get_health_check_service
        service = get_health_check_service()
        health = service.get_overall_health()
        return health.to_dict()
    except Exception as e:
        logger.error(f"[PrecomputedCache] Health compute failed: {e}")
        return {"status": "error", "error": str(e)}


def compute_error_budget_status() -> Dict[str, Any]:
    """Compute error budget status for caching."""
    try:
        from selfhealing.services.error_budget_service import (
            get_error_budget_service,
            get_failsafe_status_response,
        )
        from django.utils import timezone
        
        service = get_error_budget_service()
        budget_status = service.get_budget_status("availability")
        
        return {
            "status": "success",
            "data": budget_status.to_dict(),
            "timestamp": timezone.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"[PrecomputedCache] Error budget compute failed: {e}")
        return {"status": "error", "error": str(e)}


def compute_pool_status() -> Dict[str, Any]:
    """Compute connection pool status for caching."""
    try:
        import os
        from django.db import connections
        
        try:
            from selfhealing.adapters.sqlalchemy_pool import get_pool_info
        except ImportError:
            get_pool_info = lambda: {}
            
        pool_info = get_pool_info()
        
        conn = connections["default"]
        
        # Repository를 통해 연결 통계 조회
        from selfhealing.adapters.postgres.repository import get_postgres_repository
        repo = get_postgres_repository()
        stats = repo.get_connection_stats()
            
        is_exhausted = pool_info.get("pool_exhausted", False)
        
        return {
            "status": "exhausted" if is_exhausted else "healthy",
            "sqlalchemy_pool": pool_info,
            "pg_stats": {
                "total_connections": stats.total_connections,
                "active": stats.active,
                "idle": stats.idle,
                "idle_in_transaction": stats.idle_in_transaction,
            },
            "connection_usable": conn.is_usable(),
            "use_connection_pool": os.getenv("USE_CONNECTION_POOL", "FALSE") == "TRUE",
        }
    except Exception as e:
        logger.error(f"[PrecomputedCache] Pool status compute failed: {e}")
        return {"status": "error", "error": str(e)}


def register_default_compute_functions() -> None:
    """Register default compute functions for L3 endpoints."""
    _worker.register(CACHE_KEY_HEALTH, compute_health_status)
    _worker.register(CACHE_KEY_ERROR_BUDGET, compute_error_budget_status)
    _worker.register(CACHE_KEY_POOL_STATUS, compute_pool_status)
    logger.info("[PrecomputedCache] Registered default compute functions")


# =============================================================================
# Public API for Views
# =============================================================================

def get_cached_health() -> Dict[str, Any]:
    """Get cached health status."""
    return get_cached_response(CACHE_KEY_HEALTH, compute_health_status)


def get_cached_error_budget() -> Dict[str, Any]:
    """Get cached error budget status."""
    return get_cached_response(CACHE_KEY_ERROR_BUDGET, compute_error_budget_status)


def get_cached_pool_status() -> Dict[str, Any]:
    """Get cached pool status."""
    return get_cached_response(CACHE_KEY_POOL_STATUS, compute_pool_status)
