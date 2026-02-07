"""
Health Check Service

비즈니스 로직을 View에서 분리한 Health Check 서비스 레이어.
기본 DB 연결 확인, 커넥션 풀 상태 조회, Kubernetes 프로브 기능을 제공합니다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class DatabaseCheck:
    """데이터베이스 연결 상태."""

    alias: str
    vendor: str = ""
    is_connected: bool = False
    is_usable: bool = False
    error: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PoolInfo:
    """커넥션 풀 정보."""

    alias: str
    vendor: str = ""
    is_usable: bool = False
    status: str = "unknown"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemHealthSummary:
    """전체 헬스 상태.

    Renamed from HealthStatus to avoid conflict with
    meta.health_probe.HealthStatus Enum (Item 22).
    """

    status: str  # healthy, degraded, unhealthy
    checks: dict[str, str] = field(default_factory=dict)
    services_count: int = 0
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadinessStatus:
    """Kubernetes Readiness 상태."""

    status: str  # ready, not_ready
    checks: dict[str, str] = field(default_factory=dict)
    is_ready: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PoolHealthSummary:
    """커넥션 풀 헬스 상태 요약."""

    status: str  # healthy, degraded, error
    pool_info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# Health Check Service
# =============================================================================


class HealthCheckService:
    """
    Health Check 비즈니스 로직 서비스.

    Features:
    - 기본 DB 연결 확인
    - 모든 DB 연결 확인
    - 커넥션 풀 상태 조회
    - 전체 시스템 헬스 체크
    - Kubernetes Liveness/Readiness 프로브

    Uses ProviderRegistry for statistics to maintain framework independence.

    Usage:
        service = HealthCheckService()

        # 전체 헬스 체크
        health = service.get_overall_health()

        # 특정 DB 체크
        db_check = service.check_database("default")
    """

    def _get_circuit_breaker_count(self) -> int:
        """
        Get circuit breaker count using ProviderRegistry.

        Falls back to Redis repository if ORM not available.
        """
        try:
            from selfhealing.factory import ProviderRegistry

            stats_repo = ProviderRegistry.get_statistics_repo()
            summary = stats_repo.get_circuit_breaker_summary()
            return summary.total
        except Exception as e:
            logger.debug(f"[HealthCheck] CB count via stats failed, trying Redis: {e}")
            try:
                from selfhealing.factory import ProviderRegistry

                cb_repo = ProviderRegistry.get_circuit_breaker_repo()
                states = cb_repo.get_all_states()
                return len(states)
            except Exception as e2:
                logger.debug(f"[HealthCheck] CB count via Redis failed: {e2}")
                return 0

    def check_database(self, alias: str = "default") -> DatabaseCheck:
        """
        특정 데이터베이스 연결 확인.

        Args:
            alias: DB 별칭 (default, replica 등)

        Returns:
            DatabaseCheck: DB 연결 상태
        """
        from django.db import connections

        start_time = time.time()
        try:
            conn = connections[alias]

            # Repository를 통해 ping 실행
            from selfhealing.adapters.postgres.repository import PostgresRepository

            repo = PostgresRepository(db_alias=alias)
            repo.ping()

            latency_ms = (time.time() - start_time) * 1000

            return DatabaseCheck(
                alias=alias,
                vendor=conn.vendor,
                is_connected=True,
                is_usable=conn.is_usable(),
                latency_ms=round(latency_ms, 2),
            )
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"[HealthCheck] Database {alias} check failed: {e}")
            return DatabaseCheck(
                alias=alias,
                is_connected=False,
                is_usable=False,
                error=str(e),
                latency_ms=round(latency_ms, 2),
            )

    def check_all_databases(self) -> list[DatabaseCheck]:
        """
        모든 데이터베이스 연결 확인.

        Returns:
            List[DatabaseCheck]: 모든 DB 연결 상태 리스트
        """
        from django.db import connections

        results = []
        for alias in connections:
            results.append(self.check_database(alias))
        return results

    def check_connection_pool(self, alias: str = "default") -> PoolInfo:
        """
        커넥션 풀 상태 조회.

        Args:
            alias: DB 별칭

        Returns:
            PoolInfo: 커넥션 풀 정보
        """
        from django.db import connections

        try:
            conn = connections[alias]
            is_usable = conn.is_usable()

            return PoolInfo(
                alias=alias,
                vendor=conn.vendor,
                is_usable=is_usable,
                status="healthy" if is_usable else "degraded",
            )
        except Exception as e:
            logger.error(f"[HealthCheck] Connection pool {alias} check failed: {e}")
            return PoolInfo(
                alias=alias,
                is_usable=False,
                status="error",
                error=str(e),
            )

    def get_pool_health(self) -> PoolHealthSummary:
        """
        전체 커넥션 풀 헬스 상태.

        Returns:
            PoolHealthSummary: 풀 헬스 상태
        """
        pool_info = self.check_connection_pool("default")

        if pool_info.error:
            return PoolHealthSummary(
                status="error",
                pool_info=pool_info.to_dict(),
                error=pool_info.error,
            )

        return PoolHealthSummary(
            status=pool_info.status,
            pool_info=pool_info.to_dict(),
        )

    def get_readiness(self) -> ReadinessStatus:
        """
        Kubernetes Readiness 상태 확인.

        Returns:
            ReadinessStatus: 준비 상태
        """
        db_checks = self.check_all_databases()

        checks = {}
        ready = True

        for db_check in db_checks:
            key = f"database_{db_check.alias}"
            if db_check.is_connected:
                checks[key] = "ready"
            else:
                checks[key] = "not_ready"
                ready = False

        return ReadinessStatus(
            status="ready" if ready else "not_ready",
            checks=checks,
            is_ready=ready,
        )

    def get_overall_health(self) -> SystemHealthSummary:
        """
        전체 시스템 헬스 체크.

        Uses ProviderRegistry for statistics to maintain framework independence.
        Logs cluster_id for multi-cluster observability.

        Returns:
            HealthStatus: 전체 헬스 상태
        """
        from django.utils import timezone

        # Cluster Identity 로깅
        cluster_id, region, environment = self._get_cluster_info()

        try:
            db_check = self.check_database("default")

            if db_check.is_connected:
                services_count = self._get_circuit_breaker_count()
                health_status = "healthy"
                db_status = "healthy"
            else:
                services_count = 0
                health_status = "degraded"
                db_status = "unhealthy"
        except Exception as e:
            logger.error(f"[HealthCheck] Overall health check failed: {e}")
            services_count = 0
            health_status = "degraded"
            db_status = "unhealthy"

        # 클러스터 정보 포함 로깅
        logger.info(
            f"[HealthCheck] cluster_id={cluster_id} region={region} env={environment} "
            f"status={health_status} services={services_count}"
        )

        return SystemHealthSummary(
            status=health_status,
            checks={
                "database": db_status,
                "circuit_breaker": "enabled",
                "cluster_id": cluster_id,
                "region": region or "unknown",
            },
            services_count=services_count,
            timestamp=timezone.now().isoformat(),
        )

    def _get_cluster_info(self) -> tuple:
        """클러스터 정보 조회."""
        try:
            from selfhealing.core.cluster_identity import get_cluster_identity

            identity = get_cluster_identity()
            return identity.cluster_id, identity.region, identity.environment
        except Exception:
            import os

            return (
                os.environ.get("SELFHEALING_CLUSTER_ID", "unknown"),
                os.environ.get("SELFHEALING_REGION"),
                os.environ.get("SELFHEALING_ENV", "production"),
            )

    def is_alive(self) -> bool:
        """
        Liveness 체크 (애플리케이션 실행 여부).

        Returns:
            bool: 항상 True (앱이 실행 중이면)
        """
        return True

    def is_ready(self) -> bool:
        """
        Readiness 체크 (트래픽 처리 가능 여부).

        Returns:
            bool: 모든 DB 연결 가능하면 True
        """
        return self.get_readiness().is_ready


# =============================================================================
# Singleton & Factory
# =============================================================================


_health_check_service: HealthCheckService | None = None


def get_health_check_service() -> HealthCheckService:
    """Get the global health check service instance."""
    global _health_check_service
    if _health_check_service is None:
        _health_check_service = HealthCheckService()
    return _health_check_service
