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

Modules:
    - constants: Cache keys, config loaders, feature flags, JSON utils
    - l1_cache: L1 in-process TTLCache
    - l2_cache: L2 Redis pre-computed JSON cache
    - multi_tier: Multi-tier cache access with drift detection
    - worker: Background pre-computation worker
    - compute_functions: Compute functions for L3 endpoints

.. versionadded:: 6.4.0
    ``precomputed_cache.py`` 플랫 파일에서 ``precomputed_cache/`` 패키지로 전환.
"""

from selfhealing.services.precomputed_cache.constants import (
    CACHE_KEY_ERROR_BUDGET,
    CACHE_KEY_HEALTH,
    CACHE_KEY_POOL_STATUS,
    HAS_CACHETOOLS,
    HAS_DRIFT_METRICS,
    HAS_ORJSON,
    fast_json_dumps,
    fast_json_loads,
)
from selfhealing.services.precomputed_cache.l1_cache import (
    L1Cache,
    get_l1_cache,
)
from selfhealing.services.precomputed_cache.l2_cache import (
    L2RedisCache,
    get_l2_cache,
)
from selfhealing.services.precomputed_cache.multi_tier import (
    check_l1_l2_drift,
    get_cached_response,
    get_drift_stats_all,
)
from selfhealing.services.precomputed_cache.worker import (
    PrecomputedCacheWorker,
    get_precomputed_cache_worker,
    start_precomputed_cache,
    stop_precomputed_cache,
)
from selfhealing.services.precomputed_cache.compute_functions import (
    compute_error_budget_status,
    compute_health_status,
    compute_pool_status,
    get_cached_error_budget,
    get_cached_health,
    get_cached_pool_status,
    register_default_compute_functions,
)

# ---------------------------------------------------------------------------
# Dynamic attribute forwarding – expose ALL sub-module attributes at package
# level so that ``from selfhealing.services.precomputed_cache import _x``
# keeps working even for private helpers.
# ---------------------------------------------------------------------------
import sys as _sys

from selfhealing.services.precomputed_cache import (
    constants as _constants_mod,
    l1_cache as _l1_cache_mod,
    l2_cache as _l2_cache_mod,
    multi_tier as _multi_tier_mod,
    worker as _worker_mod,
    compute_functions as _compute_functions_mod,
)

_pkg = _sys.modules[__name__]
for _mod in (
    _constants_mod,
    _l1_cache_mod,
    _l2_cache_mod,
    _multi_tier_mod,
    _worker_mod,
    _compute_functions_mod,
):
    for _name in dir(_mod):
        if not _name.startswith("__") and not hasattr(_pkg, _name):
            setattr(_pkg, _name, getattr(_mod, _name))
del _name, _mod, _pkg

__all__ = [
    # Constants & Config
    "CACHE_KEY_HEALTH",
    "CACHE_KEY_ERROR_BUDGET",
    "CACHE_KEY_POOL_STATUS",
    "HAS_ORJSON",
    "HAS_CACHETOOLS",
    "HAS_DRIFT_METRICS",
    "fast_json_dumps",
    "fast_json_loads",
    # L1 Cache
    "L1Cache",
    "get_l1_cache",
    # L2 Cache
    "L2RedisCache",
    "get_l2_cache",
    # Multi-Tier
    "get_cached_response",
    "check_l1_l2_drift",
    "get_drift_stats_all",
    # Worker
    "PrecomputedCacheWorker",
    "get_precomputed_cache_worker",
    "start_precomputed_cache",
    "stop_precomputed_cache",
    # Compute Functions
    "compute_health_status",
    "compute_error_budget_status",
    "compute_pool_status",
    "register_default_compute_functions",
    # Public API
    "get_cached_health",
    "get_cached_error_budget",
    "get_cached_pool_status",
]
