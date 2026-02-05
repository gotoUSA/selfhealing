"""
Bulkhead Registry - 도메인별 격벽 관리.

도메인(ConnectionType 또는 커스텀)별로 격벽을 관리하는 레지스트리입니다.
EventBus를 통해 설정 변경 이벤트를 구독하여 런타임에 격벽 설정을 동적으로 반영합니다.

Usage:
    registry = get_bulkhead_registry()

    # ConnectionType으로 조회
    db_bulkhead = registry.get(ConnectionType.DATABASE)

    # 커스텀 도메인으로 조회
    custom_bulkhead = registry.get_or_create("my_custom_domain")

    # 사용
    with db_bulkhead.acquire():
        do_database_work()
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.async_semaphore import AsyncSemaphoreBulkhead
from selfhealing.resilience.bulkhead.base import Bulkhead, BulkheadState
from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead

if TYPE_CHECKING:
    from selfhealing.services.event_bus import SelfHealingEvent
    from selfhealing.settings.bulkhead import BulkheadSettings

logger = logging.getLogger(__name__)


class BulkheadRegistry:
    """
    도메인별 격벽 레지스트리.

    기존 ConnectionType과 통합되며, 커스텀 도메인도 지원합니다.
    CONFIG_UPDATED 이벤트 구독으로 런타임 설정 변경을 반영합니다.

    Features:
    - ConnectionType별 기본 격벽 자동 등록
    - 커스텀 도메인 지원
    - 비동기 격벽 자동 생성
    - DB alias/캐시 인스턴스별 세분화된 격벽
    """

    def __init__(self, settings: BulkheadSettings | None = None):
        """
        Args:
            settings: 격벽 설정. None이면 기본값 사용.
        """
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        self._settings = settings or get_bulkhead_settings()
        self._bulkheads: dict[str, Bulkhead] = {}
        self._async_bulkheads: dict[str, AsyncSemaphoreBulkhead] = {}
        self._lock = threading.Lock()

        # ConnectionType 기반 기본 격벽 등록
        self._register_default_bulkheads()

        # CONFIG_UPDATED 이벤트 구독
        self._subscribe_config_updates()

    def _register_default_bulkheads(self) -> None:
        """ConnectionType 기반 기본 격벽 등록."""
        defaults: dict[str, Bulkhead] = {
            ConnectionType.DATABASE.value: SemaphoreBulkhead(
                name=ConnectionType.DATABASE.value,
                max_concurrent=self._settings.database_max_concurrent,
            ),
            ConnectionType.CACHE.value: SemaphoreBulkhead(
                name=ConnectionType.CACHE.value,
                max_concurrent=self._settings.cache_max_concurrent,
            ),
            ConnectionType.EXTERNAL_API.value: ThreadPoolBulkhead(
                name=ConnectionType.EXTERNAL_API.value,
                max_workers=self._settings.external_api_max_workers,
                queue_size=self._settings.external_api_queue_size,
            ),
            ConnectionType.MESSAGE_QUEUE.value: SemaphoreBulkhead(
                name=ConnectionType.MESSAGE_QUEUE.value,
                max_concurrent=self._settings.message_queue_max_concurrent,
            ),
        }

        for name, bulkhead in defaults.items():
            self._bulkheads[name] = bulkhead
            logger.debug(f"[BulkheadRegistry] Registered default: {name}")

    def _subscribe_config_updates(self) -> None:
        """CONFIG_UPDATED 이벤트 구독."""
        try:
            from selfhealing.services.event_bus import (
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.subscribe(
                EventType.CONFIG_UPDATED,
                self._on_config_updated,
            )
            logger.info("[BulkheadRegistry] Subscribed to CONFIG_UPDATED events")
        except Exception as e:
            logger.warning(f"[BulkheadRegistry] Failed to subscribe to EventBus: {e}")

    def _on_config_updated(self, event: SelfHealingEvent) -> None:
        """
        설정 변경 이벤트 핸들러.

        bulkhead 관련 설정 변경 시 격벽을 재생성합니다.
        """
        config_type = event.data.get("config_type", "")

        # bulkhead 설정 변경만 처리
        if "bulkhead" not in config_type.lower():
            return

        logger.info(f"[BulkheadRegistry] Config updated: {config_type}, " f"reloading bulkheads...")

        # 설정 리로드
        from selfhealing.settings.bulkhead import (
            get_bulkhead_settings,
            reset_bulkhead_settings,
        )

        reset_bulkhead_settings()
        self._settings = get_bulkhead_settings()

        # 기본 격벽 재생성
        self._reload_default_bulkheads()

    def _reload_default_bulkheads(self) -> None:
        """기본 격벽 재생성 (점진적 교체)."""
        with self._lock:
            # 새 격벽 생성
            new_bulkheads: dict[str, Bulkhead] = {
                ConnectionType.DATABASE.value: SemaphoreBulkhead(
                    name=ConnectionType.DATABASE.value,
                    max_concurrent=self._settings.database_max_concurrent,
                ),
                ConnectionType.CACHE.value: SemaphoreBulkhead(
                    name=ConnectionType.CACHE.value,
                    max_concurrent=self._settings.cache_max_concurrent,
                ),
                ConnectionType.EXTERNAL_API.value: ThreadPoolBulkhead(
                    name=ConnectionType.EXTERNAL_API.value,
                    max_workers=self._settings.external_api_max_workers,
                    queue_size=self._settings.external_api_queue_size,
                ),
                ConnectionType.MESSAGE_QUEUE.value: SemaphoreBulkhead(
                    name=ConnectionType.MESSAGE_QUEUE.value,
                    max_concurrent=self._settings.message_queue_max_concurrent,
                ),
            }

            # 교체
            for name, bulkhead in new_bulkheads.items():
                self._bulkheads[name] = bulkhead
                logger.info(f"[BulkheadRegistry] Reloaded {name}: " f"max_concurrent={bulkhead.get_state().max_concurrent}")

            # 비동기 격벽도 초기화
            self._async_bulkheads.clear()

    def get(self, name: str | ConnectionType) -> Bulkhead:
        """
        격벽 조회.

        Args:
            name: 도메인 이름 또는 ConnectionType

        Returns:
            Bulkhead 인스턴스

        Raises:
            KeyError: 등록되지 않은 도메인
        """
        key = name.value if isinstance(name, ConnectionType) else name

        with self._lock:
            if key not in self._bulkheads:
                raise KeyError(f"Bulkhead not found: {key}")
            return self._bulkheads[key]

    def get_or_create(
        self,
        name: str,
        max_concurrent: int | None = None,
        bulkhead_type: str = "semaphore",
    ) -> Bulkhead:
        """
        격벽 조회 또는 생성.

        Args:
            name: 도메인 이름
            max_concurrent: 최대 동시 실행 수 (None이면 기본값)
            bulkhead_type: "semaphore" 또는 "thread_pool"

        Returns:
            Bulkhead 인스턴스
        """
        with self._lock:
            if name not in self._bulkheads:
                concurrent = max_concurrent or self._settings.default_max_concurrent
                if bulkhead_type == "thread_pool":
                    self._bulkheads[name] = ThreadPoolBulkhead(
                        name=name,
                        max_workers=concurrent,
                    )
                else:
                    self._bulkheads[name] = SemaphoreBulkhead(
                        name=name,
                        max_concurrent=concurrent,
                    )
                logger.info(f"[BulkheadRegistry] Created: {name} ({bulkhead_type})")

            return self._bulkheads[name]

    def get_async(self, name: str | ConnectionType) -> AsyncSemaphoreBulkhead:
        """
        비동기 격벽 조회.

        동기 격벽과 동일한 설정으로 비동기 버전을 생성/반환합니다.

        Args:
            name: 도메인 이름 또는 ConnectionType

        Returns:
            AsyncSemaphoreBulkhead 인스턴스
        """
        key = name.value if isinstance(name, ConnectionType) else name

        with self._lock:
            if key not in self._async_bulkheads:
                # 동기 버전의 설정을 기반으로 생성
                sync_bh = self._bulkheads.get(key)
                max_concurrent = sync_bh.get_state().max_concurrent if sync_bh else self._settings.default_max_concurrent
                self._async_bulkheads[key] = AsyncSemaphoreBulkhead(
                    name=key,
                    max_concurrent=max_concurrent,
                )
            return self._async_bulkheads[key]

    def get_for_database(self, alias: str = "default") -> Bulkhead:
        """
        DB alias별 격벽 반환.

        Args:
            alias: Django DB alias (default, replica, analytics 등)

        Returns:
            해당 alias의 격벽
        """
        key = f"database:{alias}"
        max_concurrent = self._settings.database_aliases.get(alias, self._settings.database_max_concurrent)
        return self.get_or_create(
            name=key,
            max_concurrent=max_concurrent,
            bulkhead_type="semaphore",
        )

    def get_for_cache(self, name: str = "default") -> Bulkhead:
        """
        캐시 인스턴스별 격벽 반환.

        Args:
            name: 캐시 이름 (default, session 등)

        Returns:
            해당 캐시의 격벽
        """
        key = f"cache:{name}"
        max_concurrent = self._settings.cache_instances.get(name, self._settings.cache_max_concurrent)
        return self.get_or_create(
            name=key,
            max_concurrent=max_concurrent,
            bulkhead_type="semaphore",
        )

    def register(self, bulkhead: Bulkhead) -> None:
        """
        커스텀 격벽 등록.

        Args:
            bulkhead: 등록할 격벽
        """
        with self._lock:
            self._bulkheads[bulkhead.name] = bulkhead
            logger.info(f"[BulkheadRegistry] Registered: {bulkhead.name}")

    def unregister(self, name: str) -> bool:
        """
        격벽 등록 해제.

        Args:
            name: 도메인 이름

        Returns:
            True이면 해제 성공
        """
        with self._lock:
            if name in self._bulkheads:
                del self._bulkheads[name]
                return True
            return False

    def get_all_states(self) -> dict[str, BulkheadState]:
        """모든 격벽 상태 반환."""
        with self._lock:
            return {name: bh.get_state() for name, bh in self._bulkheads.items()}

    def list_names(self) -> list[str]:
        """등록된 모든 도메인 이름 반환."""
        with self._lock:
            return list(self._bulkheads.keys())


# =============================================================================
# Singleton
# =============================================================================

_registry: BulkheadRegistry | None = None
_registry_lock = threading.Lock()


def get_bulkhead_registry() -> BulkheadRegistry:
    """BulkheadRegistry 싱글톤 반환."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = BulkheadRegistry()
    return _registry


def reset_bulkhead_registry() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _registry
    with _registry_lock:
        _registry = None
