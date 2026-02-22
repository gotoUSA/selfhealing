"""
서킷 브레이커 상태 로컬 캐시.

매번 Redis 조회 대신 로컬 TTL 캐시 + 이벤트 기반 무효화로
사이드카 IPC 성능을 최적화합니다.

특징:
- TTL 기반 만료 (기본 5초)
- EventBus 상태 변경 이벤트로 즉시 무효화
- Thread-safe 구현

Usage:
    from selfhealing.adapters.ipc.cb_state_cache import IPCStateCache

    cache = IPCStateCache(ttl_seconds=5.0)

    # 캐시 조회
    cached, hit = cache.get("payment_gateway")
    if hit:
        return cached

    # 실제 서비스 호출 후 캐시 저장
    result = cb_service.should_allow("payment_gateway")
    cache.set("payment_gateway", result)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class IPCCacheEntry:
    """캐시 엔트리."""

    value: Any
    """캐시된 값."""

    expires_at: float
    """만료 시각 (Unix timestamp)."""

    created_at: float = field(default_factory=time.time)
    """생성 시각."""


@dataclass
class CacheStats:
    """캐시 통계."""

    hits: int = 0
    """캐시 히트 횟수."""

    misses: int = 0
    """캐시 미스 횟수."""

    invalidations: int = 0
    """무효화 횟수."""

    expirations: int = 0
    """TTL 만료 횟수."""

    @property
    def hit_rate(self) -> float:
        """캐시 히트율."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class IPCStateCache:
    """
    서킷 브레이커 상태 로컬 캐시 (IPC 어댑터 전용).

    전략:
    - TTL 기반 만료 (기본 5초)
    - EventBus 상태 변경 이벤트로 즉시 무효화
    - Thread-safe 구현
    """

    DEFAULT_TTL_SECONDS = 5.0
    MAX_ENTRIES = 10000  # 최대 캐시 엔트리 수

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        enable_event_invalidation: bool = True,
    ):
        """
        캐시 초기화.

        Args:
            ttl_seconds: 캐시 TTL (초)
            enable_event_invalidation: EventBus 무효화 활성화 여부
        """
        self._cache: dict[str, IPCCacheEntry] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._stats = CacheStats()

        if enable_event_invalidation:
            self._register_invalidation_handlers()

    def _register_invalidation_handlers(self) -> None:
        """EventBus 이벤트로 캐시 무효화."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # CB 상태 변경 이벤트 구독
            for event_type in [
                EventType.CIRCUIT_BREAKER_OPENED,
                EventType.CIRCUIT_BREAKER_CLOSED,
                EventType.CIRCUIT_BREAKER_HALF_OPENED,
            ]:
                bus.subscribe(event_type, self._on_state_change)

            logger.debug("cell_registry.bulkheads_registered")
        except ImportError:
            logger.debug("ipc_state_cache.eventbus_available")
        except Exception as e:
            logger.warning(
                "ipc_state_cache.eventbus_registration_failed",
                error=e,
            )

    def _on_state_change(self, event: Any) -> None:
        """이벤트 기반 즉시 무효화."""
        try:
            service_name = event.data.get("service_name")
            if service_name:
                self.invalidate(service_name)
                logger.debug(
                    "ipc_state_cache.invalidated",
                    service_name=service_name,
                    event_type=event.event_type.value,
                )
        except Exception as e:
            logger.warning(
                "ipc_state_cache.event_handler_error",
                error=e,
            )

    def get(self, service_name: str) -> tuple[Any, bool]:
        """
        캐시 조회.

        Args:
            service_name: 서비스 이름

        Returns:
            (값, 히트여부) 튜플
        """
        with self._lock:
            entry = self._cache.get(service_name)

            if entry is None:
                self._stats.misses += 1
                return None, False

            # TTL 만료 확인
            if entry.expires_at <= time.time():
                del self._cache[service_name]
                self._stats.misses += 1
                self._stats.expirations += 1
                return None, False

            self._stats.hits += 1
            return entry.value, True

    def set(self, service_name: str, value: Any) -> None:
        """
        캐시 저장.

        Args:
            service_name: 서비스 이름
            value: 저장할 값
        """
        with self._lock:
            # 최대 엔트리 수 초과 시 만료된 엔트리 정리
            if len(self._cache) >= self.MAX_ENTRIES:
                self._cleanup_expired()

            self._cache[service_name] = IPCCacheEntry(
                value=value,
                expires_at=time.time() + self._ttl,
            )

    def get_or_set(
        self,
        service_name: str,
        factory: Any,
    ) -> Any:
        """
        캐시 조회, 없으면 팩토리로 생성 후 저장.

        Args:
            service_name: 서비스 이름
            factory: 값 생성 callable 또는 값

        Returns:
            캐시된 값 또는 새로 생성된 값
        """
        cached, hit = self.get(service_name)
        if hit:
            return cached

        # 팩토리 실행
        value = factory() if callable(factory) else factory
        self.set(service_name, value)
        return value

    def invalidate(self, service_name: str) -> bool:
        """
        특정 서비스 캐시 무효화.

        Args:
            service_name: 서비스 이름

        Returns:
            무효화 성공 여부
        """
        with self._lock:
            if service_name in self._cache:
                del self._cache[service_name]
                self._stats.invalidations += 1
                return True
            return False

    def invalidate_all(self) -> int:
        """
        전체 캐시 무효화.

        Returns:
            무효화된 엔트리 수
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._stats.invalidations += count
            return count

    def _cleanup_expired(self) -> int:
        """
        만료된 엔트리 정리.

        Returns:
            정리된 엔트리 수
        """
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]

        for key in expired:
            del self._cache[key]

        self._stats.expirations += len(expired)
        return len(expired)

    @property
    def size(self) -> int:
        """현재 캐시 크기."""
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> CacheStats:
        """캐시 통계."""
        return self._stats

    def get_stats_dict(self) -> dict[str, Any]:
        """캐시 통계 딕셔너리."""
        return {
            "size": self.size,
            "ttl_seconds": self._ttl,
            "hits": self._stats.hits,
            "misses": self._stats.misses,
            "hit_rate": round(self._stats.hit_rate, 4),
            "invalidations": self._stats.invalidations,
            "expirations": self._stats.expirations,
        }

    def contains(self, service_name: str) -> bool:
        """서비스가 캐시에 있는지 확인 (만료 고려)."""
        _, hit = self.get(service_name)
        return hit

    def keys(self) -> list[str]:
        """캐시된 서비스 이름 목록."""
        with self._lock:
            now = time.time()
            return [key for key, entry in self._cache.items() if entry.expires_at > now]

    def close(self) -> None:
        """캐시 종료 및 리소스 정리."""
        self.invalidate_all()


# =============================================================================
# 배치 캐시 헬퍼
# =============================================================================


class CBStateBatchCache:
    """
    배치 조회를 위한 CB 상태 캐시 래퍼.

    여러 서비스의 상태를 한 번에 조회/저장할 수 있습니다.
    """

    def __init__(self, cache: IPCStateCache | None = None):
        """
        배치 캐시 초기화.

        Args:
            cache: 기존 캐시 인스턴스 (None이면 새로 생성)
        """
        self._cache = cache or IPCStateCache()

    def get_batch(
        self,
        service_names: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """
        배치 캐시 조회.

        Args:
            service_names: 서비스 이름 목록

        Returns:
            (캐시 결과, 캐시 미스 목록) 튜플
        """
        results: dict[str, Any] = {}
        misses: list[str] = []

        for name in service_names:
            value, hit = self._cache.get(name)
            if hit:
                results[name] = value
            else:
                misses.append(name)

        return results, misses

    def set_batch(self, results: dict[str, Any]) -> None:
        """
        배치 캐시 저장.

        Args:
            results: {서비스이름: 값} 딕셔너리
        """
        for name, value in results.items():
            self._cache.set(name, value)

    @property
    def cache(self) -> IPCStateCache:
        """기본 캐시 인스턴스."""
        return self._cache


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_cache: IPCStateCache | None = None


def get_cb_state_cache() -> IPCStateCache:
    """싱글톤 캐시 인스턴스 반환."""
    global _cache
    if _cache is None:
        _cache = IPCStateCache()
    return _cache


def reset_cb_state_cache() -> None:
    """캐시 인스턴스 리셋 (테스트용)."""
    global _cache
    if _cache is not None:
        _cache.close()
    _cache = None


# 하위 호환 alias (deprecated)
CBStateCache = IPCStateCache
