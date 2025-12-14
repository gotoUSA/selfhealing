# Stage 26: DB Connection Pool 고갈 + Leak 테스트

## 🎯 목표

DB Connection Pool 고갈 및 누수 상황에서의 복원력 확보

## 📋 실제 장애 사례

- **2025년 토스 40분 장애**: Connection leak로 DB 풀 터져서 결제 전부 timeout

---

## 🏗️ 구현 내용

### 1. Connection Pool Monitor

**파일**: `packages/selfhealing-python/src/selfhealing/core/pool_monitor.py`

```python
"""
Connection Pool Monitor

Monitors database connection pool health:
- Active connections count
- Available connections count
- Wait queue length
- Connection leak detection
- Pool exhaustion prediction

Framework-agnostic design - works with any pool implementation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Dict, List, Callable, Any
import threading
import time


class PoolHealthStatus(str, Enum):
    """Connection pool health status"""
    HEALTHY = "healthy"           # 정상
    WARNING = "warning"           # 주의 (70% 이상 사용)
    CRITICAL = "critical"         # 위험 (90% 이상 사용)
    EXHAUSTED = "exhausted"       # 고갈됨
    LEAK_SUSPECTED = "leak_suspected"  # 누수 의심


@dataclass
class PoolStats:
    """Connection pool statistics"""
    pool_name: str
    max_connections: int
    active_connections: int
    available_connections: int
    waiting_requests: int = 0
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def usage_percent(self) -> float:
        if self.max_connections == 0:
            return 0.0
        return (self.active_connections / self.max_connections) * 100

    @property
    def is_exhausted(self) -> bool:
        return self.available_connections == 0 and self.waiting_requests > 0


@dataclass
class ConnectionInfo:
    """Tracked connection information for leak detection"""
    connection_id: str
    acquired_at: datetime
    stack_trace: Optional[str] = None
    query_info: Optional[str] = None
    thread_id: Optional[int] = None


@dataclass
class LeakReport:
    """Connection leak detection report"""
    suspected_leaks: List[ConnectionInfo]
    leak_threshold_seconds: float
    report_time: datetime

    @property
    def leak_count(self) -> int:
        return len(self.suspected_leaks)


class PoolStatsProvider(ABC):
    """Abstract interface for pool statistics provider"""

    @abstractmethod
    def get_stats(self) -> PoolStats:
        """Get current pool statistics"""
        pass


class ConnectionPoolMonitor:
    """
    Monitors connection pool health and detects leaks.

    Usage:
        monitor = ConnectionPoolMonitor(
            stats_provider=MyPoolStatsProvider(),
            warning_threshold=70,
            critical_threshold=90,
        )

        # Check health
        status = monitor.check_health()

        # Track connections for leak detection
        monitor.on_connection_acquired(conn_id, stack_trace)
        monitor.on_connection_released(conn_id)

        # Get leak report
        leaks = monitor.detect_leaks(threshold_seconds=300)
    """

    def __init__(
        self,
        stats_provider: Optional[PoolStatsProvider] = None,
        warning_threshold: float = 70.0,
        critical_threshold: float = 90.0,
        leak_threshold_seconds: float = 300.0,  # 5분
    ):
        self._stats_provider = stats_provider
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._leak_threshold = leak_threshold_seconds

        # Connection tracking for leak detection
        self._active_connections: Dict[str, ConnectionInfo] = {}
        self._lock = threading.Lock()

        # History for trend analysis
        self._stats_history: List[PoolStats] = []
        self._max_history = 100

    def set_stats_provider(self, provider: PoolStatsProvider) -> None:
        """Set the pool statistics provider"""
        self._stats_provider = provider

    def check_health(self) -> tuple[PoolHealthStatus, PoolStats]:
        """
        Check pool health status.
        Returns (status, stats)
        """
        if not self._stats_provider:
            raise ValueError("Pool stats provider not configured")

        stats = self._stats_provider.get_stats()
        self._record_stats(stats)

        # Check for exhaustion first
        if stats.is_exhausted:
            return PoolHealthStatus.EXHAUSTED, stats

        # Check for potential leak
        leak_report = self.detect_leaks()
        if leak_report.leak_count > 0:
            return PoolHealthStatus.LEAK_SUSPECTED, stats

        # Check usage thresholds
        if stats.usage_percent >= self._critical_threshold:
            return PoolHealthStatus.CRITICAL, stats
        elif stats.usage_percent >= self._warning_threshold:
            return PoolHealthStatus.WARNING, stats

        return PoolHealthStatus.HEALTHY, stats

    def _record_stats(self, stats: PoolStats) -> None:
        """Record stats for history"""
        self._stats_history.append(stats)
        if len(self._stats_history) > self._max_history:
            self._stats_history.pop(0)

    def on_connection_acquired(
        self,
        connection_id: str,
        stack_trace: Optional[str] = None,
        query_info: Optional[str] = None,
    ) -> None:
        """Track when a connection is acquired"""
        with self._lock:
            self._active_connections[connection_id] = ConnectionInfo(
                connection_id=connection_id,
                acquired_at=datetime.now(timezone.utc),
                stack_trace=stack_trace,
                query_info=query_info,
                thread_id=threading.current_thread().ident,
            )

    def on_connection_released(self, connection_id: str) -> None:
        """Track when a connection is released"""
        with self._lock:
            self._active_connections.pop(connection_id, None)

    def detect_leaks(self, threshold_seconds: Optional[float] = None) -> LeakReport:
        """
        Detect potential connection leaks.
        Connections held longer than threshold are suspected leaks.
        """
        threshold = threshold_seconds or self._leak_threshold
        now = datetime.now(timezone.utc)
        threshold_time = now - timedelta(seconds=threshold)

        suspected = []
        with self._lock:
            for conn_id, info in self._active_connections.items():
                if info.acquired_at < threshold_time:
                    suspected.append(info)

        return LeakReport(
            suspected_leaks=suspected,
            leak_threshold_seconds=threshold,
            report_time=now,
        )

    def get_trend(self) -> Dict[str, Any]:
        """Analyze pool usage trend"""
        if len(self._stats_history) < 2:
            return {"trend": "insufficient_data"}

        recent = self._stats_history[-10:]
        avg_usage = sum(s.usage_percent for s in recent) / len(recent)

        # Compare with older data
        if len(self._stats_history) > 20:
            older = self._stats_history[-20:-10]
            older_avg = sum(s.usage_percent for s in older) / len(older)

            if avg_usage > older_avg + 10:
                return {"trend": "increasing", "avg_usage": avg_usage}
            elif avg_usage < older_avg - 10:
                return {"trend": "decreasing", "avg_usage": avg_usage}

        return {"trend": "stable", "avg_usage": avg_usage}
```

---

### 2. Pool Watchdog (자동 복구)

**파일**: `packages/selfhealing-python/src/selfhealing/core/pool_watchdog.py`

```python
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
```

---

### 3. 테스트 케이스

**파일**: `packages/selfhealing-python/tests/unit/test_connection_pool.py`

```python
"""
Stage 26: Connection Pool Tests

Scenarios:
1. Normal pool usage
2. High usage warning (70%+)
3. Critical usage (90%+)
4. Pool exhaustion
5. Connection leak detection
6. Automatic leak cleanup
7. Pool expansion on exhaustion
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock

from selfhealing.core.pool_monitor import (
    PoolHealthStatus,
    PoolStats,
    ConnectionPoolMonitor,
    PoolStatsProvider,
)
from selfhealing.core.pool_watchdog import (
    RecoveryAction,
    PoolWatchdog,
    PoolRecoveryHandler,
)


class MockPoolStatsProvider(PoolStatsProvider):
    """Mock pool stats provider for testing"""

    def __init__(self, stats: PoolStats):
        self._stats = stats

    def get_stats(self) -> PoolStats:
        return self._stats

    def set_stats(self, stats: PoolStats) -> None:
        self._stats = stats


class TestPoolHealthMonitoring:
    """Pool health monitoring tests"""

    def test_healthy_pool(self):
        """정상 풀 상태"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=30,
            available_connections=70,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(stats_provider=provider)

        status, _ = monitor.check_health()

        assert status == PoolHealthStatus.HEALTHY

    def test_warning_threshold(self):
        """70% 이상 사용 시 경고"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=75,
            available_connections=25,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(
            stats_provider=provider,
            warning_threshold=70.0,
        )

        status, _ = monitor.check_health()

        assert status == PoolHealthStatus.WARNING

    def test_critical_threshold(self):
        """90% 이상 사용 시 위험"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=95,
            available_connections=5,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(
            stats_provider=provider,
            critical_threshold=90.0,
        )

        status, _ = monitor.check_health()

        assert status == PoolHealthStatus.CRITICAL

    def test_pool_exhausted(self):
        """풀 고갈 감지"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=100,
            available_connections=0,
            waiting_requests=10,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(stats_provider=provider)

        status, _ = monitor.check_health()

        assert status == PoolHealthStatus.EXHAUSTED


class TestConnectionLeakDetection:
    """Connection leak detection tests"""

    def test_no_leak_normal_usage(self):
        """정상 사용 시 누수 없음"""
        monitor = ConnectionPoolMonitor()

        # 연결 획득
        monitor.on_connection_acquired("conn_1")

        # 즉시 반환
        monitor.on_connection_released("conn_1")

        leaks = monitor.detect_leaks(threshold_seconds=1)

        assert leaks.leak_count == 0

    def test_detect_long_held_connection(self):
        """오래 유지된 연결 감지"""
        monitor = ConnectionPoolMonitor()

        # 과거에 획득한 연결 시뮬레이션
        from selfhealing.core.pool_monitor import ConnectionInfo
        monitor._active_connections["conn_old"] = ConnectionInfo(
            connection_id="conn_old",
            acquired_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        leaks = monitor.detect_leaks(threshold_seconds=60)  # 1분 임계값

        assert leaks.leak_count == 1
        assert leaks.suspected_leaks[0].connection_id == "conn_old"


class TestPoolWatchdogRecovery:
    """Pool watchdog recovery tests"""

    def test_alert_on_warning(self):
        """경고 시 알림 발송"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=75,
            available_connections=25,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(stats_provider=provider)

        alert_callback = Mock()
        watchdog = PoolWatchdog(
            monitor=monitor,
            alert_callback=alert_callback,
        )

        result = watchdog.check_and_recover()

        assert result.action == RecoveryAction.ALERT_ONLY
        assert alert_callback.called

    def test_close_leaked_connections(self):
        """누수 연결 자동 종료"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=50,
            available_connections=50,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(stats_provider=provider)

        # 누수 연결 추가
        from selfhealing.core.pool_monitor import ConnectionInfo
        monitor._active_connections["leaked_conn"] = ConnectionInfo(
            connection_id="leaked_conn",
            acquired_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        recovery_handler = Mock(spec=PoolRecoveryHandler)
        recovery_handler.close_connection.return_value = True

        watchdog = PoolWatchdog(
            monitor=monitor,
            recovery_handler=recovery_handler,
            auto_close_leaked=True,
        )

        result = watchdog.check_and_recover()

        assert result.action == RecoveryAction.CLOSE_LEAKED
        assert result.connections_closed == 1

    def test_expand_pool_on_exhaustion(self):
        """고갈 시 풀 확장"""
        stats = PoolStats(
            pool_name="default",
            max_connections=100,
            active_connections=100,
            available_connections=0,
            waiting_requests=5,
        )
        provider = MockPoolStatsProvider(stats)
        monitor = ConnectionPoolMonitor(stats_provider=provider)

        recovery_handler = Mock(spec=PoolRecoveryHandler)
        recovery_handler.expand_pool.return_value = True

        watchdog = PoolWatchdog(
            monitor=monitor,
            recovery_handler=recovery_handler,
            auto_expand=True,
            max_expansion=10,
        )

        result = watchdog.check_and_recover()

        assert result.action == RecoveryAction.EXPAND_POOL
        assert result.success is True
```

---

## 📁 파일 생성 순서

1. `packages/selfhealing-python/src/selfhealing/core/pool_monitor.py`
2. `packages/selfhealing-python/src/selfhealing/core/pool_watchdog.py`
3. `packages/selfhealing-python/src/selfhealing/core/__init__.py` 수정
4. `packages/selfhealing-python/tests/unit/test_connection_pool.py`

---

## ✅ 완료 기준

- [ ] ConnectionPoolMonitor 구현
- [ ] PoolWatchdog 구현
- [ ] 누수 감지 테스트 통과
- [ ] 자동 복구 테스트 통과
- [ ] 풀 확장 테스트 통과

---

## 📝 새 세션 시작 프롬프트

```
STAGE_26_CONNECTION_POOL.md 문서대로 구현해줘.
ConnectionPoolMonitor와 PoolWatchdog 구현.
```
