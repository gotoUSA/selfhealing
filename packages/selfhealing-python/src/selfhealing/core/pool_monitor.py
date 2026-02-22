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

import threading
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = structlog.get_logger().bind(component="pool_monitor")


class PoolHealthStatus(str, Enum):
    """Connection pool health status"""

    HEALTHY = "healthy"  # 정상
    WARNING = "warning"  # 주의 (70% 이상 사용)
    CRITICAL = "critical"  # 위험 (90% 이상 사용)
    EXHAUSTED = "exhausted"  # 고갈됨
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
    stack_trace: str | None = None
    query_info: str | None = None
    thread_id: int | None = None


@dataclass
class LeakReport:
    """Connection leak detection report"""

    suspected_leaks: list[ConnectionInfo]
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

        # Simulation override for chaos testing
        monitor.set_simulation_override(
            health_status=PoolHealthStatus.EXHAUSTED,
            experiment_id="exp-123",
        )
    """

    def __init__(
        self,
        stats_provider: PoolStatsProvider | None = None,
        warning_threshold: float = 70.0,
        critical_threshold: float = 90.0,
        leak_threshold_seconds: float = 300.0,  # 5분
        max_history: int = 100,
    ):
        self._stats_provider = stats_provider
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._leak_threshold = leak_threshold_seconds

        # Connection tracking for leak detection
        self._active_connections: dict[str, ConnectionInfo] = {}
        self._lock = threading.Lock()

        # History for trend analysis
        self._stats_history: list[PoolStats] = []
        self._max_history = max_history

        # Simulation override for chaos testing
        self._simulation_override: PoolHealthStatus | None = None
        self._simulation_stats: PoolStats | None = None
        self._simulation_experiment_id: str | None = None

    @classmethod
    def from_settings(
        cls,
        stats_provider: PoolStatsProvider | None = None,
        settings=None,
        **overrides,
    ) -> "ConnectionPoolMonitor":
        """
        Settings 기반 인스턴스 생성.

        Args:
            stats_provider: Pool 통계 제공자
            settings: PoolMonitorSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            ConnectionPoolMonitor: Settings 기반 인스턴스
        """
        from selfhealing.settings.pool_monitor import get_pool_monitor_settings

        s = settings or get_pool_monitor_settings()
        return cls(
            stats_provider=stats_provider,
            warning_threshold=overrides.get("warning_threshold", s.warning_threshold),
            critical_threshold=overrides.get("critical_threshold", s.critical_threshold),
            leak_threshold_seconds=overrides.get("leak_threshold_seconds", s.leak_threshold_seconds),
            max_history=overrides.get("max_history", s.max_history),
        )

    def set_simulation_override(
        self,
        health_status: PoolHealthStatus | None = None,
        stats: PoolStats | None = None,
        experiment_id: str | None = None,
    ) -> None:
        """
        시뮬레이션 상태 오버라이드 설정.

        실제 인프라를 변경하지 않고 모니터가 특정 상태를 보고하도록 강제.
        카오스 실험에서 알림/복구 체인 검증에 사용.

        Args:
            health_status: 강제할 건강 상태 (None이면 해제)
            stats: 강제할 통계 (None이면 기본값 사용)
            experiment_id: 관련 카오스 실험 ID (감사 추적용)

        Example:
            monitor.set_simulation_override(
                health_status=PoolHealthStatus.EXHAUSTED,
                experiment_id="exp-123",
            )
        """
        with self._lock:
            self._simulation_override = health_status
            self._simulation_stats = stats
            self._simulation_experiment_id = experiment_id

            if health_status:
                logger.info(
                    "pool_monitor.simulation_override_set",
                    status=health_status.value,
                    experiment_id=experiment_id,
                )
            else:
                logger.info("pool_monitor.simulation_override_cleared")

    def clear_simulation_override(self) -> None:
        """시뮬레이션 오버라이드 해제."""
        self.set_simulation_override(None, None, None)

    def is_simulation_active(self) -> bool:
        """시뮬레이션 오버라이드가 활성화되어 있는지 확인."""
        return self._simulation_override is not None

    def get_simulation_experiment_id(self) -> str | None:
        """현재 시뮬레이션과 연관된 실험 ID 반환."""
        return self._simulation_experiment_id

    def _get_default_simulated_stats(self) -> PoolStats:
        """시뮬레이션용 기본 통계 생성."""
        # 오버라이드 상태에 맞는 기본 통계
        if self._simulation_override == PoolHealthStatus.EXHAUSTED:
            return PoolStats(
                pool_name="simulated_pool",
                max_connections=100,
                active_connections=100,
                available_connections=0,
                waiting_requests=50,
            )
        elif self._simulation_override == PoolHealthStatus.CRITICAL:
            return PoolStats(
                pool_name="simulated_pool",
                max_connections=100,
                active_connections=95,
                available_connections=5,
                waiting_requests=10,
            )
        elif self._simulation_override == PoolHealthStatus.WARNING:
            return PoolStats(
                pool_name="simulated_pool",
                max_connections=100,
                active_connections=75,
                available_connections=25,
                waiting_requests=0,
            )
        else:
            return PoolStats(
                pool_name="simulated_pool",
                max_connections=100,
                active_connections=30,
                available_connections=70,
                waiting_requests=0,
            )

    def set_stats_provider(self, provider: PoolStatsProvider) -> None:
        """Set the pool statistics provider"""
        self._stats_provider = provider

    def check_health(self) -> tuple[PoolHealthStatus, PoolStats]:
        """
        Check pool health status.
        Returns (status, stats)

        시뮬레이션 오버라이드 지원
        """
        # 시뮬레이션 모드 체크
        if self._simulation_override is not None:
            logger.debug(
                "pool_monitor.simulated_status_returned",
                status=self._simulation_override.value,
            )
            stats = self._simulation_stats or self._get_default_simulated_stats()
            return self._simulation_override, stats

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
        stack_trace: str | None = None,
        query_info: str | None = None,
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

    def detect_leaks(self, threshold_seconds: float | None = None) -> LeakReport:
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

    def get_trend(self) -> dict[str, Any]:
        """
        Analyze pool usage trend.

        Phase 2 (238_PREDICTIVE_ANOMALY_FORECASTER): 분석 윈도우 10→100으로 확대.
        Phase 3: HoltLinearForecaster 기반 풀 고갈 예측 추가.
        """
        if len(self._stats_history) < 2:
            return {"trend": "insufficient_data"}

        # Phase 2: 분석 윈도우 100개로 확대 (통계적 유의성 향상)
        window_size = min(100, len(self._stats_history))
        recent = self._stats_history[-window_size:]
        avg_usage = sum(s.usage_percent for s in recent) / len(recent)

        result: dict[str, Any] = {"trend": "stable", "avg_usage": avg_usage}

        # Compare with older data (Phase 2: 100 vs 100 window)
        if len(self._stats_history) > window_size * 2:
            older = self._stats_history[-(window_size * 2) : -window_size]
            older_avg = sum(s.usage_percent for s in older) / len(older)

            if avg_usage > older_avg + 10:
                result["trend"] = "increasing"
            elif avg_usage < older_avg - 10:
                result["trend"] = "decreasing"

        # Phase 3: HoltLinearForecaster 기반 풀 고갈 예측
        try:
            from selfhealing.services.predictive_forecaster.time_series import (
                HoltLinearForecaster,
            )

            forecaster = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=min(30, window_size))
            for s in recent:
                forecaster.update(s.usage_percent)

            if forecaster.is_warmed_up:
                predicted_5 = forecaster.predict(steps_ahead=5)
                predicted_15 = forecaster.predict(steps_ahead=15)
                trend_slope = forecaster.get_trend_slope()
                confidence = forecaster.get_confidence()

                result["predicted_usage_5min"] = predicted_5
                result["predicted_usage_15min"] = predicted_15
                result["trend_slope"] = trend_slope
                result["prediction_confidence"] = confidence

                # 풀 고갈 예측 시간 (분)
                if trend_slope > 0 and avg_usage < 100:
                    remaining = 100.0 - avg_usage
                    minutes_to_exhaustion = remaining / trend_slope
                    result["estimated_exhaustion_minutes"] = minutes_to_exhaustion
        except Exception:
            pass  # Forecaster 미설치 시 기존 로직만 사용

        return result
