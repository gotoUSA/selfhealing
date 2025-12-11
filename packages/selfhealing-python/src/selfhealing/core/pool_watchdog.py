"""
Connection Pool Watchdog

Automatic recovery actions for pool issues:
- Force-close leaked connections
- Expand pool temporarily
- Alert operators
- Circuit breaker for new connections
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable, List
import logging

from .pool_monitor import (
    ConnectionPoolMonitor,
    PoolHealthStatus,
    PoolStats,
    LeakReport,
)


logger = logging.getLogger(__name__)


class RecoveryAction(str, Enum):
    """Types of recovery actions"""
    NONE = "none"
    ALERT_ONLY = "alert_only"
    CLOSE_LEAKED = "close_leaked"
    EXPAND_POOL = "expand_pool"
    CIRCUIT_BREAK = "circuit_break"


@dataclass
class RecoveryResult:
    """Result of a recovery action"""
    action: RecoveryAction
    success: bool
    message: str
    timestamp: datetime
    connections_closed: int = 0


class PoolRecoveryHandler(ABC):
    """Abstract handler for pool recovery actions"""

    @abstractmethod
    def close_connection(self, connection_id: str) -> bool:
        """Force close a connection"""
        pass

    @abstractmethod
    def expand_pool(self, additional_connections: int) -> bool:
        """Temporarily expand pool size"""
        pass

    @abstractmethod
    def shrink_pool(self, target_size: int) -> bool:
        """Shrink pool back to normal size"""
        pass


class PoolWatchdog:
    """
    Watches pool health and takes recovery actions.

    Usage:
        watchdog = PoolWatchdog(
            monitor=pool_monitor,
            recovery_handler=my_recovery_handler,
            alert_callback=send_alert,
        )

        # Run check (call periodically)
        result = watchdog.check_and_recover()
    """

    def __init__(
        self,
        monitor: ConnectionPoolMonitor,
        recovery_handler: Optional[PoolRecoveryHandler] = None,
        alert_callback: Optional[Callable[[str, PoolHealthStatus], None]] = None,
        auto_close_leaked: bool = True,
        auto_expand: bool = False,
        max_expansion: int = 10,
    ):
        self._monitor = monitor
        self._recovery_handler = recovery_handler
        self._alert_callback = alert_callback
        self._auto_close_leaked = auto_close_leaked
        self._auto_expand = auto_expand
        self._max_expansion = max_expansion
        self._expanded_by = 0

    def check_and_recover(self) -> RecoveryResult:
        """
        Check pool health and take recovery action if needed.
        Returns the action taken.
        """
        status, stats = self._monitor.check_health()

        if status == PoolHealthStatus.HEALTHY:
            # If we expanded before, consider shrinking
            if self._expanded_by > 0:
                return self._try_shrink(stats)
            return RecoveryResult(
                action=RecoveryAction.NONE,
                success=True,
                message="Pool is healthy",
                timestamp=datetime.now(timezone.utc),
            )

        if status == PoolHealthStatus.LEAK_SUSPECTED:
            return self._handle_leak()

        if status == PoolHealthStatus.EXHAUSTED:
            return self._handle_exhaustion(stats)

        if status in (PoolHealthStatus.WARNING, PoolHealthStatus.CRITICAL):
            return self._handle_high_usage(status, stats)

        return RecoveryResult(
            action=RecoveryAction.NONE,
            success=True,
            message=f"Status: {status.value}",
            timestamp=datetime.now(timezone.utc),
        )

    def _handle_leak(self) -> RecoveryResult:
        """Handle suspected connection leak"""
        leak_report = self._monitor.detect_leaks()

        self._send_alert(
            f"Connection leak detected: {leak_report.leak_count} connections",
            PoolHealthStatus.LEAK_SUSPECTED
        )

        if not self._auto_close_leaked or not self._recovery_handler:
            return RecoveryResult(
                action=RecoveryAction.ALERT_ONLY,
                success=True,
                message=f"Leak alert sent for {leak_report.leak_count} connections",
                timestamp=datetime.now(timezone.utc),
            )

        # Force close leaked connections
        closed = 0
        for conn_info in leak_report.suspected_leaks:
            try:
                if self._recovery_handler.close_connection(conn_info.connection_id):
                    self._monitor.on_connection_released(conn_info.connection_id)
                    closed += 1
            except Exception as e:
                logger.error(f"Failed to close connection {conn_info.connection_id}: {e}")

        return RecoveryResult(
            action=RecoveryAction.CLOSE_LEAKED,
            success=closed > 0,
            message=f"Closed {closed}/{leak_report.leak_count} leaked connections",
            timestamp=datetime.now(timezone.utc),
            connections_closed=closed,
        )

    def _handle_exhaustion(self, stats: PoolStats) -> RecoveryResult:
        """Handle pool exhaustion"""
        self._send_alert(
            f"Connection pool exhausted: {stats.waiting_requests} requests waiting",
            PoolHealthStatus.EXHAUSTED
        )

        if not self._auto_expand or not self._recovery_handler:
            return RecoveryResult(
                action=RecoveryAction.ALERT_ONLY,
                success=True,
                message="Pool exhausted, alert sent",
                timestamp=datetime.now(timezone.utc),
            )

        # Try to expand pool
        if self._expanded_by >= self._max_expansion:
            return RecoveryResult(
                action=RecoveryAction.CIRCUIT_BREAK,
                success=False,
                message="Max expansion reached, circuit breaking",
                timestamp=datetime.now(timezone.utc),
            )

        expand_by = min(5, self._max_expansion - self._expanded_by)
        success = self._recovery_handler.expand_pool(expand_by)

        if success:
            self._expanded_by += expand_by

        return RecoveryResult(
            action=RecoveryAction.EXPAND_POOL,
            success=success,
            message=f"Expanded pool by {expand_by}" if success else "Failed to expand",
            timestamp=datetime.now(timezone.utc),
        )

    def _handle_high_usage(self, status: PoolHealthStatus, stats: PoolStats) -> RecoveryResult:
        """Handle high usage warning/critical"""
        self._send_alert(
            f"Pool usage {status.value}: {stats.usage_percent:.1f}%",
            status
        )

        return RecoveryResult(
            action=RecoveryAction.ALERT_ONLY,
            success=True,
            message=f"Alert sent for {status.value} usage",
            timestamp=datetime.now(timezone.utc),
        )

    def _try_shrink(self, stats: PoolStats) -> RecoveryResult:
        """Try to shrink pool back to normal if healthy"""
        if stats.usage_percent < 50 and self._recovery_handler:
            target = stats.max_connections - self._expanded_by
            if self._recovery_handler.shrink_pool(target):
                self._expanded_by = 0
                return RecoveryResult(
                    action=RecoveryAction.NONE,
                    success=True,
                    message="Pool shrunk back to normal",
                    timestamp=datetime.now(timezone.utc),
                )

        return RecoveryResult(
            action=RecoveryAction.NONE,
            success=True,
            message="Pool healthy, monitoring",
            timestamp=datetime.now(timezone.utc),
        )

    def _send_alert(self, message: str, status: PoolHealthStatus) -> None:
        """Send alert via callback"""
        if self._alert_callback:
            try:
                self._alert_callback(message, status)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")
