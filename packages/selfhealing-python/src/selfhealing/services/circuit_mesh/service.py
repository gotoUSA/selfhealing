"""
Circuit Mesh Service

Adaptive Circuit Breaker Mesh 오케스트레이터.

MeshCoordinator를 생성하고 EventBus에 연결하며,
운영 API(상태 조회, 수동 오버라이드 해제, dry-run 시뮬레이션)를 제공한다.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

import structlog

from selfhealing.services.circuit_mesh.mesh_coordinator import (
    MeshCoordinator,
    register_mesh_handlers,
    set_mesh_coordinator,
    unregister_mesh_handlers,
)
from selfhealing.services.circuit_mesh.models import MeshStateSnapshot
from selfhealing.settings.circuit_mesh import get_circuit_mesh_settings

if TYPE_CHECKING:
    from selfhealing.services.circuit_mesh import ThresholdOverride

logger = structlog.get_logger()


class CircuitMeshService:
    """
    Adaptive Circuit Breaker Mesh 오케스트레이터.

    MeshCoordinator를 생성하고 EventBus에 연결하며,
    운영 API를 제공한다.
    """

    _instance: CircuitMeshService | None = None
    _lock = Lock()

    def __new__(cls) -> CircuitMeshService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._settings = get_circuit_mesh_settings()
        self._coordinator: MeshCoordinator | None = None
        self._active = False
        self._initialized = True

    def start(self) -> None:
        """
        메쉬 서비스 시작.

        이벤트 유실 방지를 위해 다음 순서를 엄격히 준수한다:
        1. 의존성 수집 및 MeshCoordinator 생성
        2. EventBus 핸들러 등록 (구독 시작, 단 Hydration 완료 전 이벤트는 큐잉)
        3. L2(Redis) → L1 Hydration (기존 글로벌 오버라이드 복원)
        4. 정상 이벤트 처리 시작
        """
        if not self._settings.enabled:
            logger.info("circuit_mesh_service.disabled")
            return

        if self._active:
            logger.debug("circuit_mesh_service.already_active")
            return

        from selfhealing.factory import ProviderRegistry
        from selfhealing.services.circuit_breaker.blast_radius_integration import (
            BlastRadiusIntegration,
        )
        from selfhealing.services.circuit_breaker.service import (
            CircuitBreakerService,
        )
        from selfhealing.services.event_bus import get_event_bus

        br_integration = BlastRadiusIntegration()
        cb_service = CircuitBreakerService()
        cache = ProviderRegistry.get_cache("redis")
        event_bus = get_event_bus()

        from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore

        override_store = TwoTierMeshOverrideStore(
            cache=cache,
            event_bus=event_bus,
        )
        self._coordinator = MeshCoordinator(
            dependency_graph=br_integration.dependency_graph,
            cb_service=cb_service,
            override_store=override_store,
            settings=self._settings,
        )

        self._coordinator.initialize()
        set_mesh_coordinator(self._coordinator)

        # EventBus 핸들러 등록 (Hydration 전에 구독하여 이벤트 유실 방지)
        self._coordinator.set_hydrating(True)
        register_mesh_handlers(self._coordinator)

        # L2 → L1 Hydration (기존 글로벌 오버라이드 복원)
        self._coordinator.hydrate_from_store()

        # 정상 처리 시작 (큐잉된 이벤트 flush)
        self._coordinator.set_hydrating(False)

        self._active = True
        logger.info("circuit_mesh_service.started")

    def stop(self) -> None:
        """
        메쉬 서비스 중지.

        분산 환경의 글로벌 정합성을 보장하기 위해:
        - EventBus 구독만 해제한다
        - L1(로컬 메모리) 캐시만 정리한다
        - L2(Redis)의 글로벌 오버라이드는 건드리지 않는다
        """
        if not self._active:
            return

        if self._coordinator:
            unregister_mesh_handlers(self._coordinator)
            self._coordinator.override_store.clear_l1()

        self._active = False
        logger.info("circuit_mesh_service.stopped")

    # === 조회 API ===

    def get_mesh_state(self) -> MeshStateSnapshot:
        """현재 메쉬 상태 스냅샷 반환."""
        if not self._coordinator:
            return MeshStateSnapshot.empty()

        from selfhealing.core.timezone import now as _now

        snapshot = self._coordinator.get_mesh_snapshot()
        overrides = self._coordinator.override_store.get_all()
        return MeshStateSnapshot(
            timestamp=_now(),
            active_overrides=list(overrides.values()),
            recovery_queue=snapshot.get("recovery_queue", []),
        )

    def get_active_overrides(self) -> list[ThresholdOverride]:
        """현재 활성 오버라이드 목록. TwoTierStore API를 통해 조회."""
        if not self._coordinator:
            return []
        return list(self._coordinator.override_store.get_all().values())

    def get_recovery_order(self) -> list[str]:
        """현재 순차 복구 순서."""
        if not self._coordinator:
            return []
        return self._coordinator.get_recovery_order()

    # === 수동 제어 API ===

    def force_release_override(self, service_name: str, reason: str = "") -> bool:
        """특정 서비스의 오버라이드 수동 해제."""
        if not self._coordinator:
            return False
        return self._coordinator.force_release(service_name, reason)

    def force_release_all(self, reason: str = "") -> int:
        """
        모든 오버라이드 일괄 해제 (L1 + L2 모두).
        운영자가 명시적으로 호출하는 수동 API.
        반환값: 해제된 오버라이드 수.
        """
        if not self._coordinator:
            return 0
        return self._coordinator.release_all_overrides(reason)

    # === Dry-Run API ===

    def simulate_downstream_failure(self, service_name: str) -> list[ThresholdOverride]:
        """
        특정 서비스가 OPEN되었을 때 어떤 오버라이드가 발생할지 시뮬레이션.

        실제 오버라이드를 적용하지 않고 결과만 반환한다.
        """
        if not self._coordinator:
            return []
        return self._coordinator.simulate_downstream_open(service_name)

    @classmethod
    def _reset(cls) -> None:
        """싱글톤 초기화 (테스트용)."""
        with cls._lock:
            if cls._instance and cls._instance._active:
                cls._instance.stop()
            cls._instance = None
