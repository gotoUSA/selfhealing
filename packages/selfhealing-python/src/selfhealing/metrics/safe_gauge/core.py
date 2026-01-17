"""
Core SafeGauge Implementation.

Thread-safe gauge wrapper that prevents negative values.

Design Philosophy:
- Counter Pair (Google SRE style) is technically superior but requires
  PromQL calculations on the dashboard side.
- SafeGauge provides "plug-and-play" experience for buyers while
  internally preventing the -1 dashboard embarrassment.

Enhanced Features (Metric Reliability):
- Sync Status Tracking: last_sync_time and is_synced for data freshness
- Staleness Detection: Auto-mark as stale after threshold
- Stabilization Period: Gradual recovery from strict mode
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, TYPE_CHECKING

from .sync import SyncInfo, SyncStatus
from .noop import NoOpGaugeChild
from .clamping import clamp_non_negative

if TYPE_CHECKING:
    from prometheus_client import Gauge

logger = logging.getLogger(__name__)


class SafeGaugeChild:
    """
    Safe wrapper for labeled Gauge child.

    Prevents the gauge from going negative by clamping at 0.
    This is critical for preventing "-1 pending items" on dashboards
    after server restarts when the in-memory counter starts at 0.

    Enhanced with sync status tracking:
    - Tracks last_sync_time for data freshness indication
    - Auto-detects staleness based on threshold
    - Supports stabilization period for gradual recovery

    Thread Safety:
        Uses a lock to ensure atomic read-check-update operations.
        This prevents race conditions in high-concurrency environments.

    Note:
        Prometheus client doesn't expose _value directly in a clean way,
        so we maintain our own shadow counter for clamping logic.
        The Lazy Sync (Reconciler) will correct any drift periodically.
    """

    def __init__(
        self,
        gauge_child: Any,
        label_values: Dict[str, str],
        staleness_threshold: float = 300.0,
        stabilization_duration: float = 60.0,
    ):
        """
        Initialize SafeGaugeChild.

        Args:
            gauge_child: The original Prometheus Gauge child (labeled)
            label_values: Label key-value pairs for logging
            staleness_threshold: Seconds before data is considered stale (default: 5분)
            stabilization_duration: Seconds for gradual recovery (default: 60초)
        """
        self._gauge_child = gauge_child
        self._label_values = label_values
        self._lock = threading.Lock()
        # Shadow counter for clamping logic
        # Starts at 0, may drift from actual Prometheus value
        # Reconciler will sync periodically
        self._shadow_value: float = 0.0
        self._initialized = False

        # Sync status tracking
        self._sync_info = SyncInfo(
            staleness_threshold=staleness_threshold,
            stabilization_duration=stabilization_duration,
        )

    @property
    def sync_info(self) -> SyncInfo:
        """동기화 정보 조회."""
        return self._sync_info

    @property
    def is_synced(self) -> bool:
        """데이터 신뢰 가능 여부."""
        self._sync_info.check_staleness()
        return self._sync_info.is_synced

    @property
    def is_recovering(self) -> bool:
        """복구 중 여부."""
        return self._sync_info.is_recovering

    @property
    def last_sync_time(self) -> Optional[float]:
        """마지막 동기화 시간."""
        return self._sync_info.last_sync_time

    @property
    def sync_age_seconds(self) -> Optional[float]:
        """마지막 동기화 이후 경과 시간."""
        return self._sync_info.age_seconds

    def inc(self, amount: float = 1) -> None:
        """
        Increment the gauge value.

        Args:
            amount: Amount to increment (default: 1)
        """
        with self._lock:
            self._shadow_value += amount
            self._gauge_child.inc(amount)
            self._initialized = True
            self._sync_info.mark_synced("push")

    def dec(self, amount: float = 1) -> None:
        """
        Decrement the gauge value, clamping at 0.

        This is the key safety feature: if the shadow value would go
        negative, we set to 0 instead. This prevents the embarrassing
        "-1 pending items" display after server restarts.

        Args:
            amount: Amount to decrement (default: 1)
        """
        with self._lock:
            if not self._initialized:
                # First operation after restart is a dec - likely stale event
                # Don't decrement, just log and return
                logger.debug(
                    f"[SafeGauge] Ignoring dec() before any inc() - "
                    f"likely stale event after restart. labels={self._label_values}"
                )
                return

            if self._shadow_value >= amount:
                # Normal case: sufficient value to decrement
                self._shadow_value -= amount
                self._gauge_child.dec(amount)
            else:
                # Edge case: would go negative, clamp to 0
                old_value = self._shadow_value
                self._shadow_value = 0.0
                # Set to 0 instead of decrementing
                self._gauge_child.set(0)
                logger.warning(
                    f"[SafeGauge] Clamped gauge to 0 (would be {old_value - amount}). "
                    f"labels={self._label_values}. "
                    f"This may indicate event ordering issues after restart. "
                    f"Reconciler will sync correct value on next cycle."
                )

            self._sync_info.mark_synced("push")

    def set(self, value: float, source: str = "manual") -> None:
        """
        Set the gauge to a specific value.

        Args:
            value: Value to set (clamped to 0 if negative)
            source: Sync source identifier (default: "manual")
        """
        with self._lock:
            if value < 0:
                logger.warning(
                    f"[SafeGauge] Attempted to set negative value {value}, "
                    f"clamping to 0. labels={self._label_values}"
                )
                value = 0.0
            self._shadow_value = value
            self._gauge_child.set(value)
            self._initialized = True
            self._sync_info.mark_synced(source)

    def get_shadow_value(self) -> float:
        """
        Get the current shadow value (for testing/debugging).

        Returns:
            Current shadow counter value
        """
        with self._lock:
            return self._shadow_value

    def sync_from_source(self, actual_value: float, source: str = "reconciler") -> None:
        """
        Sync shadow value from authoritative source (Reconciler callback).

        Called by MetricReconciler to correct drift between
        in-memory shadow and actual DB state.

        Args:
            actual_value: Actual value from DB or external source
            source: Sync source identifier (e.g., "hydration", "manual", "snapshot")
        """
        with self._lock:
            if actual_value < 0:
                actual_value = 0.0
            old_shadow = self._shadow_value
            self._shadow_value = actual_value
            self._gauge_child.set(actual_value)
            self._initialized = True
            self._sync_info.mark_synced(source)
            if old_shadow != actual_value:
                logger.info(
                    f"[SafeGauge] Synced from source: {old_shadow} -> {actual_value}. "
                    f"labels={self._label_values}"
                )

    def mark_stale(self, reason: str = "external") -> None:
        """
        수동으로 stale 상태 마킹.

        Args:
            reason: Stale 이유
        """
        with self._lock:
            self._sync_info.mark_stale(reason)

    def get_reliability_info(self) -> Dict[str, Any]:
        """
        메트릭 신뢰도 정보 반환.

        Returns:
            신뢰도 정보 딕셔너리
        """
        with self._lock:
            self._sync_info.check_staleness()
            return {
                "is_synced": self._sync_info.is_synced,
                "status": self._sync_info.status.value,
                "last_sync_time": self._sync_info.last_sync_time,
                "last_sync_source": self._sync_info.last_sync_source,
                "age_seconds": self._sync_info.age_seconds,
                "is_recovering": self._sync_info.is_recovering,
                "recovery_progress": self._sync_info.recovery_progress,
                "shadow_value": self._shadow_value,
                "labels": self._label_values,
            }


class SafeGauge:
    """
    Safe wrapper for Prometheus Gauge.

    Wraps a Prometheus Gauge and returns SafeGaugeChild instances
    for labeled gauge operations, preventing negative values.

    This pattern is inspired by Netflix's metric handling approach:
    - Internal safety mechanisms (clamping)
    - External simplicity (standard Gauge interface)
    - Eventual consistency (Reconciler syncs periodically)

    Example:
        >>> from prometheus_client import Gauge
        >>> raw = Gauge("dlq_pending", "Pending DLQ items", ["domain"])
        >>> safe = SafeGauge(raw)
        >>>
        >>> # Use like normal Gauge
        >>> safe.labels(domain="payment").inc()
        >>> safe.labels(domain="payment").dec()  # Won't go below 0
    """

    def __init__(self, gauge: Optional["Gauge"]):
        """
        Initialize SafeGauge.

        Args:
            gauge: Prometheus Gauge to wrap. If None, operations are no-ops.
        """
        self._gauge = gauge
        self._children: Dict[tuple, SafeGaugeChild] = {}
        self._lock = threading.Lock()

    def labels(self, **kwargs) -> SafeGaugeChild:
        """
        Get a SafeGaugeChild for the given labels.

        Args:
            **kwargs: Label key-value pairs

        Returns:
            SafeGaugeChild instance for thread-safe operations
        """
        if self._gauge is None:
            # Return a no-op child if gauge is not available
            return NoOpGaugeChild()

        # Create a hashable key from sorted kwargs
        key = tuple(sorted(kwargs.items()))

        with self._lock:
            if key not in self._children:
                gauge_child = self._gauge.labels(**kwargs)
                self._children[key] = SafeGaugeChild(gauge_child, kwargs)
            return self._children[key]

    def get_child(self, **kwargs) -> Optional[SafeGaugeChild]:
        """
        Get existing SafeGaugeChild without creating new one.

        Args:
            **kwargs: Label key-value pairs

        Returns:
            SafeGaugeChild if exists, None otherwise
        """
        key = tuple(sorted(kwargs.items()))
        with self._lock:
            return self._children.get(key)

    @property
    def is_available(self) -> bool:
        """Check if underlying gauge is available."""
        return self._gauge is not None


__all__ = [
    "SafeGauge",
    "SafeGaugeChild",
]
