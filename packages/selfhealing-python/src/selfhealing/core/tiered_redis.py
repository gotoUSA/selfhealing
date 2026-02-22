"""
Tiered Redis Provider - Multi-Cluster Redis Topology.

LOCAL과 GLOBAL 범위의 Redis 클라이언트를 제공합니다.

문제:
- 단일 Redis는 전 세계 클러스터가 하나의 가용 영역에 종속
- adapters/resilient/backend.py#L39: redis_url 단일 URL만 지원

설계:
- LOCAL: 각 클러스터 내부 Redis (CB, 메트릭, DLQ) - 고속
- GLOBAL: 리전 간 복제 Redis (설정, 앵커, Error Budget) - 일관성

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class RedisScope(str, Enum):
    """Redis 접근 범위."""

    LOCAL = "local"  # 클러스터 내부 (고속, 실시간)
    GLOBAL = "global"  # 리전 간 (설정, 앵커)


class TieredRedisProvider:
    """
    계층화된 Redis 제공자.

    LOCAL: 각 클러스터 내부 Redis (CB, 메트릭, DLQ)
    GLOBAL: 리전 간 복제 Redis (설정, 앵커, Error Budget)

    사용 시나리오:
    - 단일 Redis (개발/테스트): LOCAL = GLOBAL = 동일 URL
    - 다중 Redis (프로덕션): LOCAL ≠ GLOBAL

    환경변수:
    - REDIS_URL: 로컬 Redis (기본)
    - REDIS_GLOBAL_URL: 글로벌 Redis (없으면 REDIS_URL 사용)
    """

    def __init__(
        self,
        local_url: str | None = None,
        global_url: str | None = None,
    ):
        """
        Initialize Tiered Redis Provider.

        Args:
            local_url: 로컬 Redis URL (기본: REDIS_URL 환경변수)
            global_url: 글로벌 Redis URL (기본: REDIS_GLOBAL_URL 또는 local_url)
        """
        self._local_url = local_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._global_url = global_url or os.environ.get("REDIS_GLOBAL_URL", self._local_url)

        self._local_client: Any | None = None
        self._global_client: Any | None = None

        logger.debug(
            "tiered_redis_provider.initialized",
            self=self._local_url,
            self_1=self._global_url,
        )

    def get_redis(self, scope: RedisScope = RedisScope.LOCAL) -> Any:
        """
        범위에 맞는 Redis 클라이언트 반환.

        Args:
            scope: Redis 범위 (LOCAL 또는 GLOBAL)

        Returns:
            Redis 클라이언트 인스턴스
        """
        if scope == RedisScope.LOCAL:
            return self._get_local_client()
        else:
            return self._get_global_client()

    def _get_local_client(self) -> Any:
        """로컬 Redis 클라이언트 반환 (지연 초기화)."""
        if self._local_client is None:
            import redis

            self._local_client = redis.from_url(self._local_url)
            logger.info(
                "tiered_redis_provider.local_redis_connected",
                self=self._local_url,
            )
        return self._local_client

    def _get_global_client(self) -> Any:
        """글로벌 Redis 클라이언트 반환 (지연 초기화)."""
        if self._global_client is None:
            # 같은 URL이면 로컬 클라이언트 재사용
            if self._global_url == self._local_url:
                self._global_client = self._get_local_client()
                logger.debug("tiered_redis_provider.global_redis_reusing_local")
            else:
                import redis

                self._global_client = redis.from_url(self._global_url)
                logger.info(
                    "tiered_redis_provider.global_redis_connected",
                    self=self._global_url,
                )
        return self._global_client

    @property
    def local_url(self) -> str:
        """로컬 Redis URL."""
        return self._local_url

    @property
    def global_url(self) -> str:
        """글로벌 Redis URL."""
        return self._global_url

    @property
    def is_tiered(self) -> bool:
        """LOCAL과 GLOBAL이 다른 Redis인지 여부."""
        return self._local_url != self._global_url

    def close(self) -> None:
        """모든 Redis 연결 종료."""
        if self._local_client is not None:
            try:
                self._local_client.close()
            except Exception as e:
                logger.warning(
                    "tiered_redis_provider.error_closing_local_client",
                    error=e,
                )
            self._local_client = None

        if self._global_client is not None and self._global_url != self._local_url:
            try:
                self._global_client.close()
            except Exception as e:
                logger.warning(
                    "tiered_redis_provider.error_closing_global_client",
                    error=e,
                )
            self._global_client = None

    def health_check(self, scope: RedisScope | None = None) -> dict:
        """
        Redis 상태 확인.

        Args:
            scope: 확인할 범위 (None이면 모두 확인)

        Returns:
            상태 딕셔너리
        """
        result = {}

        if scope is None or scope == RedisScope.LOCAL:
            try:
                self._get_local_client().ping()
                result["local"] = {"status": "healthy", "url": self._local_url}
            except Exception as e:
                result["local"] = {"status": "unhealthy", "error": str(e)}

        if scope is None or scope == RedisScope.GLOBAL:
            try:
                self._get_global_client().ping()
                result["global"] = {"status": "healthy", "url": self._global_url}
            except Exception as e:
                result["global"] = {"status": "unhealthy", "error": str(e)}

        return result


# =============================================================================
# Singleton
# =============================================================================

_provider: TieredRedisProvider | None = None


def get_tiered_redis_provider() -> TieredRedisProvider:
    """TieredRedisProvider 싱글톤 반환."""
    global _provider
    if _provider is None:
        _provider = TieredRedisProvider()
    return _provider


def reset_tiered_redis_provider() -> None:
    """테스트용 싱글톤 리셋."""
    global _provider
    if _provider is not None:
        _provider.close()
        _provider = None
