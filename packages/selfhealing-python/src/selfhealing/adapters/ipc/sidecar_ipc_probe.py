"""
SidecarIPCProbe - 메타워치독 헬스 프로브.

사이드카 IPC 통신 상태를 모니터링하는 헬스 프로브입니다.
메타워치독 시스템과 통합되어 IPC 레이어의 상태를 추적합니다.

Health Check 항목:
- UDS 서버 상태
- gRPC 서버 상태
- 연결 수
- 에러율
- 지연 시간

Usage:
    from selfhealing.adapters.ipc.sidecar_ipc_probe import SidecarIPCProbe

    probe = SidecarIPCProbe()
    health = probe.check()
    print(health.status)  # "healthy", "degraded", "unhealthy"
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# HealthStatus: 단일 소스는 meta/health_probe.py (Item 9 중복 제거)
from selfhealing.meta.health_probe import HealthStatus

logger = logging.getLogger(__name__)


@dataclass
class SidecarProbeResult:
    """헬스 체크 결과."""

    status: HealthStatus
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
            "latency_ms": self.latency_ms,
        }


@dataclass
class IPCHealthMetrics:
    """IPC 헬스 메트릭."""

    uds_active_connections: int = 0
    grpc_active_connections: int = 0
    total_requests: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    cache_hit_ratio: float = 0.0
    event_stream_count: int = 0
    last_error: str | None = None
    last_error_time: datetime | None = None

    @property
    def error_rate(self) -> float:
        """에러율 계산."""
        if self.total_requests == 0:
            return 0.0
        return self.error_count / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "uds_active_connections": self.uds_active_connections,
            "grpc_active_connections": self.grpc_active_connections,
            "total_requests": self.total_requests,
            "error_count": self.error_count,
            "error_rate": self.error_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "cache_hit_ratio": self.cache_hit_ratio,
            "event_stream_count": self.event_stream_count,
            "last_error": self.last_error,
            "last_error_time": (self.last_error_time.isoformat() if self.last_error_time else None),
        }


class SidecarIPCProbe:
    """
    사이드카 IPC 헬스 프로브.

    메타워치독 시스템과 통합되어 IPC 레이어의 상태를 추적합니다.
    """

    # 헬스 판단 임계값
    ERROR_RATE_DEGRADED_THRESHOLD = 0.01  # 1%
    ERROR_RATE_UNHEALTHY_THRESHOLD = 0.05  # 5%
    LATENCY_DEGRADED_THRESHOLD_MS = 100.0
    LATENCY_UNHEALTHY_THRESHOLD_MS = 500.0

    def __init__(
        self,
        *,
        check_interval_seconds: float = 30.0,
        history_size: int = 100,
    ):
        """
        프로브 초기화.

        Args:
            check_interval_seconds: 헬스 체크 간격
            history_size: 히스토리 유지 개수
        """
        self.check_interval_seconds = check_interval_seconds
        self.history_size = history_size

        self._metrics = IPCHealthMetrics()
        self._history: list[SidecarProbeResult] = []
        self._last_check: datetime | None = None

        # 서버 참조 (lazy loading)
        self._uds_server: Any | None = None
        self._grpc_server: Any | None = None
        self._cb_state_cache: Any | None = None
        self._event_stream_proxy: Any | None = None

        logger.debug("[SidecarIPCProbe] Initialized")

    def _get_uds_server(self) -> Any | None:
        """UDS 서버 가져오기 (lazy loading)."""
        if self._uds_server is None:
            try:
                from selfhealing.adapters.ipc.uds_server import get_uds_server

                self._uds_server = get_uds_server()
            except ImportError:
                pass
        return self._uds_server

    def _get_grpc_server(self) -> Any | None:
        """gRPC 서버 가져오기 (lazy loading)."""
        if self._grpc_server is None:
            try:
                from selfhealing.adapters.ipc.grpc_server import get_grpc_server

                self._grpc_server = get_grpc_server()
            except ImportError:
                pass
        return self._grpc_server

    def _get_cb_state_cache(self) -> Any | None:
        """CB 상태 캐시 가져오기 (lazy loading)."""
        if self._cb_state_cache is None:
            try:
                from selfhealing.adapters.ipc.cb_state_cache import cb_state_cache

                self._cb_state_cache = cb_state_cache
            except ImportError:
                pass
        return self._cb_state_cache

    def _get_event_stream_proxy(self) -> Any | None:
        """이벤트 스트림 프록시 가져오기 (lazy loading)."""
        if self._event_stream_proxy is None:
            try:
                from selfhealing.adapters.ipc.event_stream_proxy import (
                    event_stream_proxy,
                )

                self._event_stream_proxy = event_stream_proxy
            except ImportError:
                pass
        return self._event_stream_proxy

    def collect_metrics(self) -> IPCHealthMetrics:
        """
        현재 IPC 메트릭 수집.

        Returns:
            수집된 메트릭
        """
        metrics = IPCHealthMetrics()

        # UDS 서버 메트릭
        uds_server = self._get_uds_server()
        if uds_server is not None:
            try:
                metrics.uds_active_connections = getattr(uds_server, "connection_count", 0)
                stats = getattr(uds_server, "get_stats", lambda: {})()
                metrics.total_requests += stats.get("total_requests", 0)
                metrics.error_count += stats.get("error_count", 0)
            except Exception as e:
                logger.warning(f"[SidecarIPCProbe] UDS server metrics error: {e}")

        # gRPC 서버 메트릭
        grpc_server = self._get_grpc_server()
        if grpc_server is not None:
            try:
                metrics.grpc_active_connections = getattr(grpc_server, "connection_count", 0)
            except Exception as e:
                logger.warning(f"[SidecarIPCProbe] gRPC server metrics error: {e}")

        # 캐시 메트릭
        cache = self._get_cb_state_cache()
        if cache is not None:
            try:
                cache_stats = getattr(cache, "get_stats", lambda: {})()
                hits = cache_stats.get("hits", 0)
                misses = cache_stats.get("misses", 0)
                total = hits + misses
                if total > 0:
                    metrics.cache_hit_ratio = hits / total
            except Exception as e:
                logger.warning(f"[SidecarIPCProbe] Cache metrics error: {e}")

        # 이벤트 스트림 메트릭
        proxy = self._get_event_stream_proxy()
        if proxy is not None:
            try:
                metrics.event_stream_count = getattr(proxy, "subscriber_count", 0)
            except Exception as e:
                logger.warning(f"[SidecarIPCProbe] Event stream metrics error: {e}")

        self._metrics = metrics
        return metrics

    def check(self) -> SidecarProbeResult:
        """
        헬스 체크 수행.

        Returns:
            헬스 체크 결과
        """
        start_time = time.time()

        try:
            metrics = self.collect_metrics()
            status, message = self._evaluate_health(metrics)

            latency_ms = (time.time() - start_time) * 1000

            result = SidecarProbeResult(
                status=status,
                message=message,
                details=metrics.to_dict(),
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"[SidecarIPCProbe] Health check error: {e}")

            result = SidecarProbeResult(
                status=HealthStatus.UNKNOWN,
                message=f"Health check failed: {e}",
                latency_ms=latency_ms,
            )

        # 히스토리 추가
        self._history.append(result)
        if len(self._history) > self.history_size:
            self._history = self._history[-self.history_size :]

        self._last_check = result.timestamp

        return result

    def _evaluate_health(
        self,
        metrics: IPCHealthMetrics,
    ) -> tuple[HealthStatus, str]:
        """
        메트릭 기반 헬스 상태 평가.

        Args:
            metrics: 수집된 메트릭

        Returns:
            (상태, 메시지) 튜플
        """
        issues: list[str] = []

        # 에러율 체크
        if metrics.error_rate >= self.ERROR_RATE_UNHEALTHY_THRESHOLD:
            return (
                HealthStatus.UNHEALTHY,
                f"High error rate: {metrics.error_rate:.2%}",
            )
        elif metrics.error_rate >= self.ERROR_RATE_DEGRADED_THRESHOLD:
            issues.append(f"Elevated error rate: {metrics.error_rate:.2%}")

        # 지연 시간 체크
        if metrics.avg_latency_ms >= self.LATENCY_UNHEALTHY_THRESHOLD_MS:
            return (
                HealthStatus.UNHEALTHY,
                f"High latency: {metrics.avg_latency_ms:.2f}ms",
            )
        elif metrics.avg_latency_ms >= self.LATENCY_DEGRADED_THRESHOLD_MS:
            issues.append(f"Elevated latency: {metrics.avg_latency_ms:.2f}ms")

        # 서버 상태 체크
        uds_server = self._get_uds_server()
        grpc_server = self._get_grpc_server()

        if uds_server is None and grpc_server is None:
            return (HealthStatus.UNHEALTHY, "No IPC servers running")

        if uds_server is not None and not getattr(uds_server, "running", False):
            issues.append("UDS server not running")

        if grpc_server is not None and not getattr(grpc_server, "running", False):
            issues.append("gRPC server not running")

        # 결과 반환
        if issues:
            return (HealthStatus.DEGRADED, "; ".join(issues))

        return (HealthStatus.HEALTHY, "All IPC components healthy")

    def get_history(self) -> list[dict[str, Any]]:
        """
        헬스 체크 히스토리 반환.

        Returns:
            히스토리 목록
        """
        return [result.to_dict() for result in self._history]

    def get_summary(self) -> dict[str, Any]:
        """
        헬스 요약 정보 반환.

        Returns:
            요약 딕셔너리
        """
        recent = self._history[-10:] if self._history else []

        healthy_count = sum(1 for r in recent if r.status == HealthStatus.HEALTHY)
        degraded_count = sum(1 for r in recent if r.status == HealthStatus.DEGRADED)
        unhealthy_count = sum(1 for r in recent if r.status == HealthStatus.UNHEALTHY)

        avg_latency = sum(r.latency_ms for r in recent) / len(recent) if recent else 0.0

        return {
            "current_status": self._history[-1].status.value if self._history else "unknown",
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "recent_checks": len(recent),
            "healthy_count": healthy_count,
            "degraded_count": degraded_count,
            "unhealthy_count": unhealthy_count,
            "avg_check_latency_ms": avg_latency,
            "metrics": self._metrics.to_dict(),
        }


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_probe_instance: SidecarIPCProbe | None = None


def get_sidecar_ipc_probe() -> SidecarIPCProbe:
    """싱글톤 프로브 인스턴스 반환."""
    global _probe_instance
    if _probe_instance is None:
        _probe_instance = SidecarIPCProbe()
    return _probe_instance


def reset_sidecar_ipc_probe() -> None:
    """테스트용 프로브 인스턴스 리셋."""
    global _probe_instance
    _probe_instance = None
