"""
SafeGauge - Thread-safe Prometheus Gauge Wrapper.

Prevents negative gauge values after server restarts.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md

Usage:
    >>> from selfhealing.metrics.safe_gauge import SafeGauge
    >>> from prometheus_client import Gauge
    >>>
    >>> raw = Gauge("dlq_pending", "Pending DLQ items", ["domain"])
    >>> safe = SafeGauge(raw)
    >>> safe.labels(domain="payment").inc()
    >>> safe.labels(domain="payment").dec()  # Won't go below 0

Module Structure:
    - core.py: SafeGauge, SafeGaugeChild (핵심 래퍼)
    - sync.py: SyncStatus, SyncInfo (동기화 상태 추적)
    - clamping.py: clamp_non_negative, clamp_percentage, safe_set_gauge (유틸리티)
    - noop.py: NoOpGaugeChild (No-op 구현)
"""

from .core import SafeGauge, SafeGaugeChild
from .sync import SyncStatus, SyncInfo
from .clamping import clamp_non_negative, clamp_percentage, safe_set_gauge
from .noop import NoOpGaugeChild

__all__ = [
    # Core
    "SafeGauge",
    "SafeGaugeChild",
    # Sync
    "SyncStatus",
    "SyncInfo",
    # Clamping
    "clamp_non_negative",
    "clamp_percentage",
    "safe_set_gauge",
    # NoOp
    "NoOpGaugeChild",
]
