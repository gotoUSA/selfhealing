"""
Pre-computed Cache Service - Constants and Configuration.

Cache keys, config loader functions, feature flags, and fast JSON serialization.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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
        record_cache_refresh,
        update_cache_consistency,
        update_cache_hit_rate,
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
